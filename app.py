import sys
import os
import glob
import re
import threading
import time
import sqlite3
import json
import secrets
import logging
from logging.handlers import RotatingFileHandler
import concurrent.futures
from datetime import datetime, timedelta
import random
import subprocess
import socket
import atexit
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, redirect
import io

class SafeStream:
    def write(self, s):
        pass
    def flush(self):
        pass

if getattr(sys, 'stdout', None) is None:
    sys.stdout = SafeStream()
else:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

if getattr(sys, 'stderr', None) is None:
    sys.stderr = SafeStream()
else:
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ── Logging Configuration ───────────────────────────────────────────────────
handler = RotatingFileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.log'), maxBytes=5_000_000, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger('qr_compare')

SSL_VERIFY = os.environ.get("QR_SSL_VERIFY", "0") == "1"

# ── Dynamic Flask Secret Key ─────────────────────────────────────────────────
FLASK_SECRET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".flask_secret")

def _get_or_create_flask_secret():
    if os.path.exists(FLASK_SECRET_PATH):
        try:
            with open(FLASK_SECRET_PATH, 'r', encoding='utf-8') as f:
                s = f.read().strip()
                if s:
                    return s
        except Exception:
            pass
    secret = secrets.token_hex(32)
    try:
        with open(FLASK_SECRET_PATH, 'w', encoding='utf-8') as f:
            f.write(secret)
    except Exception:
        pass
    return secret

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or _get_or_create_flask_secret()
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ── Global Thread Lock & Memory State ─────────────────────────────────────────
_state_lock = threading.RLock()
_user_cache_map = {}
_cache_access_order = []
_compare_cache_map = {}
bkst_status = "closed"
bkst_message = ""
_app_bkst_synced = False

_fetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='bkst_fetch')
_fetch_future = None

# ── Template Vars TTL Cache ───────────────────────────────────────────────────
_template_vars_cache = {
    'expires_at': 0,
    'user_name': '', 'v_code': '', 'full_commit': '',
    'v_date': '', 'v_msg': ''
}
_template_vars_lock = threading.Lock()

# ── Session Token Authentication Helper ───────────────────────────────────────
SESSION_TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".session_token")

def get_or_create_session_token():
    if os.path.exists(SESSION_TOKEN_PATH):
        try:
            with open(SESSION_TOKEN_PATH, 'r', encoding='utf-8') as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception:
            pass
    token = secrets.token_hex(32)
    try:
        with open(SESSION_TOKEN_PATH, 'w', encoding='utf-8') as f:
            f.write(token)
    except Exception:
        pass
    return token

LOCAL_SESSION_TOKEN = get_or_create_session_token()

# ── Cache-Control & Auth Headers ─────────────────────────────────────────────
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    if request.cookies.get('local_session_token') != LOCAL_SESSION_TOKEN:
        response.set_cookie('local_session_token', LOCAL_SESSION_TOKEN, httponly=True, samesite='Strict')
    return response

@app.before_request
def check_authentication():
    path = request.path
    if (path.startswith('/static') or 
        path == '/login' or 
        path == '/api/system/login' or 
        path == '/api/system/check_update' or 
        path == '/api/system/apply_update' or 
        path == '/api/system/version' or 
        path == '/api/system/heartbeat' or 
        path == '/api/heartbeat'):
        return None

    username, password, _, _ = read_bkst_credentials()
    if not username or not password:
        if path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Kullanıcı oturum açmamış veya giriş bilgileri yok.', 'code': 401}), 401
        return redirect('/login')

    if path.startswith('/api/'):
        client_tokens = [
            request.cookies.get('local_session_token'),
            request.headers.get('X-Local-Token'),
            request.headers.get('X-Session-Token'),
            request.args.get('token')
        ]
        # Ignore empty, None, and literal strings 'undefined', 'null'
        valid_tokens = [t.strip() for t in client_tokens if t and str(t).strip().lower() not in ('undefined', 'null', 'none', '')]

        if not any(t == LOCAL_SESSION_TOKEN for t in valid_tokens):
            return jsonify({'success': False, 'error': 'Geçersiz veya eksik oturum anahtarı', 'code': 401}), 401

        # CSRF Koruması: Veri değiştiren isteklerde X-Requested-With veya Same-Origin zorunludur
        if request.method in ['POST', 'PUT', 'DELETE']:
            is_xhr = (request.headers.get('X-Requested-With') == 'XMLHttpRequest')
            sec_fetch = (request.headers.get('Sec-Fetch-Site') or '').lower()
            is_same_origin = (sec_fetch == 'same-origin')

            host = request.headers.get('Host', '')
            origin = request.headers.get('Origin', '')
            referer = request.headers.get('Referer', '')
            if host:
                if origin and (host in origin):
                    is_same_origin = True
                if referer and (host in referer):
                    is_same_origin = True

            if not (is_xhr or is_same_origin):
                return jsonify({'success': False,
                                'error': 'CSRF koruması: X-Requested-With başlığı eksik',
                                'code': 403}), 403

    return None

# ── Sürüm & Güncelleme Bilgisi ────────────────────────────────────────────────
@app.route('/api/system/version', methods=['GET'])
def get_version_info():
    v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
    v_code = "v1.0"
    v_commit = ""
    v_date = datetime.now().strftime("%d.%m.%Y")
    v_msg = "Sistem Güncel"
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = str(v_data.get("version", "v1.0")).strip()
                v_commit = str(v_data.get("commit", "")).strip()
                v_date = str(v_data.get("date", "")).strip()
                v_msg = str(v_data.get("message", "Sistem Güncel")).strip()
        except Exception as e:
            logger.error(f"Error reading version.json: {e}")

    full_hash = f"{v_code} ({v_commit})" if v_commit else v_code
    return jsonify({
        "success": True,
        "version": v_code,
        "commit_hash": full_hash,
        "commit_date": v_date,
        "commit_msg": v_msg
    })

@app.route('/api/system/heartbeat', methods=['POST', 'GET'])
def system_heartbeat():
    payload = _bkst_state_payload()
    payload["local_count"] = _local_item_count_cached()
    return jsonify(payload)

@app.route('/api/heartbeat', methods=['POST', 'GET'])
def api_heartbeat():
    return jsonify({'status': 'ok'})

# ── SQLite Veritabanı Katmanı & Staging Swap ──────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cikis_kayitlari.db')

def save_bkst_data_to_db(df, username=""):
    if not username:
        logger.warning("save_bkst_data_to_db: username boş, işlem iptal.")
        return
    if df is None:
        return
    if isinstance(df, pd.DataFrame) and df.empty:
        return
    if isinstance(df, list) and len(df) == 0:
        return

    ensure_db_schema()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()

    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?", (username,))

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if isinstance(df, pd.DataFrame):
            koli_col = find_koli_column(df.columns)
            records = df.to_dict(orient="records")
        else:
            koli_col = None
            records = df

        rows_to_insert = []
        for r in records:
            qr_val = normalize_qr(str(r.get("Karekod", r.get("tam_karekod", r.get("QR", "")))))
            gtin_val = ""
            for k in ("Gtin Numarası", "Gtin / Barkod", "Gtin/Barkod", "gtin", "GTIN", "BARKOD", "Barkod", "BARCODE", "Barcode"):
                v = r.get(k)
                if v is not None and str(v).strip() and str(v).strip().upper() != "NAN":
                    gtin_val = str(v).strip()
                    break
            if not gtin_val and qr_val:
                parsed = parse_gs1_qr(qr_val)
                if parsed and parsed.get("gtin"):
                    gtin_val = str(parsed["gtin"]).strip()

            urun_val = str(r.get("Ürün Adı", r.get("urun_adi", r.get("URUNADI", "")))).strip()
            seri_val = str(r.get("Seri Numarası", r.get("seri_no", r.get("SERIALNUMBER", "")))).strip()
            parti_val = str(r.get("Parti Numarası", r.get("parti_no", r.get("SARJNO", "")))).strip()
            raw_koli = r.get(koli_col) if koli_col else (r.get("Koli Numarası") or r.get("koli_no") or r.get("PAKETNO") or r.get("KOLINO"))
            koli_val = str(raw_koli).strip().upper() if raw_koli is not None else ""
            if koli_val in ("NAN", "NONE", "NULL"):
                koli_val = ""
            palet_val = str(r.get("Palet Numarası", r.get("palet_no", ""))).strip()
            uretim_val = str(r.get("Üretim Tarihi", r.get("uretim_tarihi", ""))).strip()
            skt_val = str(r.get("Son Kullanma Tarihi", r.get("skt", r.get("SKT", "")))).strip()

            rows_to_insert.append((gtin_val, urun_val, seri_val, parti_val, koli_val, palet_val, uretim_val, skt_val, qr_val, username, now_str))

        c.executemany('''INSERT INTO bkst_depo_verileri_staging
            (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', rows_to_insert)

        c.execute("DELETE FROM bkst_depo_verileri WHERE kullanici_adi = ?", (username,))
        c.execute('''INSERT INTO bkst_depo_verileri
            (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
            SELECT gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi
            FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?''', (username,))
        c.execute("DELETE FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?", (username,))
        conn.commit()
    except Exception as e:
        logger.error(f"save_bkst_data_to_db transaction error: {e}", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        ensure_db_schema()
        raise e
    finally:
        try:
            conn.close()
        except Exception:
            pass

    user_key = username or "default_user"
    with _state_lock:
        _user_cache_map.pop(user_key, None)
        if user_key in _cache_access_order:
            _cache_access_order.remove(user_key)

def ensure_db_schema(conn=None):
    close_at_end = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        close_at_end = True
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")

    # 1. cikis_kayitlari tablosu
    c.execute('''CREATE TABLE IF NOT EXISTS cikis_kayitlari (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih        TEXT NOT NULL,
        urun_adi     TEXT,
        barkod       TEXT,
        koli_no      TEXT,
        seri_no      TEXT,
        parti_no     TEXT,
        palet_no     TEXT,
        uretim_tarihi TEXT,
        skt          TEXT,
        ham_karekod  TEXT,
        tekrar_uyari INTEGER DEFAULT 0,
        kullanici_adi TEXT
    )''')
    c.execute("PRAGMA table_info(cikis_kayitlari)")
    cikis_cols = {row[1].lower() for row in c.fetchall()}
    for col, col_type in [
        ("tarih", "TEXT NOT NULL DEFAULT ''"),
        ("urun_adi", "TEXT"),
        ("barkod", "TEXT"),
        ("koli_no", "TEXT"),
        ("seri_no", "TEXT"),
        ("parti_no", "TEXT"),
        ("palet_no", "TEXT"),
        ("uretim_tarihi", "TEXT"),
        ("skt", "TEXT"),
        ("ham_karekod", "TEXT"),
        ("tekrar_uyari", "INTEGER DEFAULT 0"),
        ("kullanici_adi", "TEXT")
    ]:
        if col.lower() not in cikis_cols:
            try:
                c.execute(f"ALTER TABLE cikis_kayitlari ADD COLUMN {col} {col_type}")
            except Exception as e:
                logger.warning(f"Could not add column {col} to cikis_kayitlari: {e}")

    # 2. bkst_depo_verileri tablosu
    c.execute('''CREATE TABLE IF NOT EXISTS bkst_depo_verileri (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        gtin             TEXT,
        urun_adi         TEXT,
        seri_no          TEXT,
        parti_no         TEXT,
        koli_no          TEXT,
        palet_no         TEXT,
        uretim_tarihi    TEXT,
        skt              TEXT,
        tam_karekod      TEXT,
        gln              TEXT,
        adres_id         TEXT,
        kullanici_adi    TEXT,
        guncelleme_tarihi TEXT
    )''')
    c.execute("PRAGMA table_info(bkst_depo_verileri)")
    bkst_cols = {row[1].lower() for row in c.fetchall()}
    for col in [
        "gtin", "urun_adi", "seri_no", "parti_no", "koli_no",
        "palet_no", "uretim_tarihi", "skt", "tam_karekod",
        "gln", "adres_id", "kullanici_adi", "guncelleme_tarihi"
    ]:
        if col.lower() not in bkst_cols:
            try:
                c.execute(f"ALTER TABLE bkst_depo_verileri ADD COLUMN {col} TEXT")
            except Exception as e:
                logger.warning(f"Could not add column {col} to bkst_depo_verileri: {e}")

    # 3. bkst_depo_verileri_staging tablosu
    c.execute('''CREATE TABLE IF NOT EXISTS bkst_depo_verileri_staging (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        gtin             TEXT,
        urun_adi         TEXT,
        seri_no          TEXT,
        parti_no         TEXT,
        koli_no          TEXT,
        palet_no         TEXT,
        uretim_tarihi    TEXT,
        skt              TEXT,
        tam_karekod      TEXT,
        gln              TEXT,
        adres_id         TEXT,
        kullanici_adi    TEXT,
        guncelleme_tarihi TEXT
    )''')
    c.execute("PRAGMA table_info(bkst_depo_verileri_staging)")
    staging_cols = {row[1].lower() for row in c.fetchall()}
    for col in [
        "gtin", "urun_adi", "seri_no", "parti_no", "koli_no",
        "palet_no", "uretim_tarihi", "skt", "tam_karekod",
        "gln", "adres_id", "kullanici_adi", "guncelleme_tarihi"
    ]:
        if col.lower() not in staging_cols:
            try:
                c.execute(f"ALTER TABLE bkst_depo_verileri_staging ADD COLUMN {col} TEXT")
            except Exception as e:
                logger.warning(f"Could not add column {col} to bkst_depo_verileri_staging: {e}")

    # 4. satis_arsivi tablosu (Kalıcı Satış & İstatistik Arşivi)
    c.execute('''CREATE TABLE IF NOT EXISTS satis_arsivi (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih         TEXT NOT NULL DEFAULT '',
        urun_adi      TEXT,
        barkod        TEXT,
        koli_no       TEXT,
        seri_no       TEXT,
        parti_no      TEXT,
        palet_no      TEXT,
        uretim_tarihi TEXT,
        skt           TEXT,
        ham_karekod   TEXT,
        tekrar_uyari  INTEGER DEFAULT 0,
        kullanici_adi TEXT,
        durum         TEXT DEFAULT 'CIKIS_YAPILDI'
    )''')

    # 5. ciftciler tablosu (Müşteri & Çiftçi Rehberi)
    c.execute('''CREATE TABLE IF NOT EXISTS ciftciler (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        tc_no             TEXT UNIQUE NOT NULL,
        ad_soyad          TEXT NOT NULL,
        telefon           TEXT DEFAULT '',
        il                TEXT DEFAULT '',
        ilce              TEXT DEFAULT '',
        koy               TEXT DEFAULT '',
        eklenme_tarihi    TEXT DEFAULT '',
        son_islem_tarihi  TEXT DEFAULT '',
        notlar            TEXT DEFAULT '',
        kullanici_adi     TEXT DEFAULT ''
    )''')
    c.execute("PRAGMA table_info(ciftciler)")
    ciftci_cols = {row[1].lower() for row in c.fetchall()}
    for col in ["tc_no", "ad_soyad", "telefon", "il", "ilce", "koy", "eklenme_tarihi", "son_islem_tarihi", "notlar", "kullanici_adi"]:
        if col.lower() not in ciftci_cols:
            try:
                c.execute(f"ALTER TABLE ciftciler ADD COLUMN {col} TEXT DEFAULT ''")
            except Exception as e:
                logger.warning(f"Could not add column {col} to ciftciler: {e}")

    # 6. sistem_ayarlari tablosu (Güvenli Giriş & Sistem Yapılandırması)
    c.execute('''CREATE TABLE IF NOT EXISTS sistem_ayarlari (
        anahtar           TEXT PRIMARY KEY,
        deger             TEXT DEFAULT '',
        guncelleme_tarihi TEXT DEFAULT ''
    )''')

    # İndeksler
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_ham_karekod ON cikis_kayitlari(ham_karekod)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_kullanici_adi ON cikis_kayitlari(kullanici_adi)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_karekod ON bkst_depo_verileri(tam_karekod)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_gtin ON bkst_depo_verileri(gtin)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_kullanici ON bkst_depo_verileri(kullanici_adi)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_arsivi_tarih ON satis_arsivi(tarih)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_arsivi_qr ON satis_arsivi(ham_karekod)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_arsivi_seri ON satis_arsivi(seri_no)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_arsivi_urun ON satis_arsivi(urun_adi)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ciftci_tc ON ciftciler(tc_no)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ciftci_ad ON ciftciler(ad_soyad)")
    except Exception:
        pass

    # Otomatik ilk aktarım: cikis_kayitlari'ndan satis_arsivi'ne tek seferlik ilk geçiş aktarımı
    try:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (key TEXT PRIMARY KEY, migrated_at TEXT)")
        c.execute("SELECT 1 FROM schema_migrations WHERE key = 'v315_initial_archive_backfill'")
        if not c.fetchone():
            c.execute('''
                INSERT INTO satis_arsivi (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, durum)
                SELECT tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, 'CIKIS_YAPILDI'
                FROM cikis_kayitlari
            ''')
            c.execute("INSERT OR REPLACE INTO schema_migrations (key, migrated_at) VALUES ('v315_initial_archive_backfill', ?)", 
                      (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
    except Exception as e_backfill:
        logger.warning(f"satis_arsivi backfill notice: {e_backfill}")

    conn.commit()
    if close_at_end:
        conn.close()

def init_db():
    ensure_db_schema()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    # ── Otomatik GTIN Onarımı / Geriye Dönük Veri Doldurma ────────────────────
    try:
        # bkst_depo_verileri boş gtin'leri karekoddan doldur
        c.execute("SELECT id, tam_karekod FROM bkst_depo_verileri WHERE gtin IS NULL OR gtin = ''")
        for row_id, qr_code in c.fetchall():
            if qr_code:
                parsed = parse_gs1_qr(qr_code)
                if parsed and parsed.get("gtin"):
                    c.execute("UPDATE bkst_depo_verileri SET gtin = ? WHERE id = ?", (str(parsed["gtin"]), row_id))

        # cikis_kayitlari boş barkod'ları karekoddan doldur
        c.execute("SELECT id, ham_karekod FROM cikis_kayitlari WHERE barkod IS NULL OR barkod = ''")
        for row_id, qr_code in c.fetchall():
            if qr_code:
                parsed = parse_gs1_qr(qr_code)
                if parsed and parsed.get("gtin"):
                    c.execute("UPDATE cikis_kayitlari SET barkod = ? WHERE id = ?", (str(parsed["gtin"]), row_id))

        # bkst_depo_verileri içindeki eşleşen tam_karekod'u cikis_kayitlari'na yaz (seri no ile okutulmuş kayıtları onar)
        c.execute('''
            SELECT ck.id, bv.tam_karekod
            FROM cikis_kayitlari ck
            JOIN bkst_depo_verileri bv ON LOWER(ck.seri_no) = LOWER(bv.seri_no)
            WHERE (ck.ham_karekod IS NULL OR length(ck.ham_karekod) < 20 OR ck.ham_karekod = ck.seri_no)
              AND bv.tam_karekod IS NOT NULL AND length(bv.tam_karekod) >= 20
        ''')
        for ck_id, full_qr in c.fetchall():
            c.execute("UPDATE cikis_kayitlari SET ham_karekod = ? WHERE id = ?", (full_qr, ck_id))

        # Yinelenen seri numarası veya karekod durumunda tekrar_uyari flag'lerini onar:
        # İlk çıkış (en küçük id) -> 0, sonraki çıkışlar (büyük id'ler) -> 1
        c.execute("SELECT id, seri_no, ham_karekod, barkod FROM cikis_kayitlari ORDER BY id ASC")
        rows = c.fetchall()
        seen_keys = set()
        for r_id, s_no, qr_val, b_val in rows:
            key_qr = f"qr:{qr_val.strip().casefold()}" if qr_val and len(qr_val.strip()) >= 16 else None
            key_seri = f"seri:{s_no.strip().casefold()}_{b_val or ''}" if s_no and s_no.strip() else None

            is_duplicate = False
            if key_qr and key_qr in seen_keys:
                is_duplicate = True
            if key_seri and key_seri in seen_keys:
                is_duplicate = True

            if key_qr: seen_keys.add(key_qr)
            if key_seri: seen_keys.add(key_seri)

            c.execute("UPDATE cikis_kayitlari SET tekrar_uyari = ? WHERE id = ?", (1 if is_duplicate else 0, r_id))
    except Exception as e:
        logger.error(f"GTIN and duplicate backfill migration error: {e}")

    conn.commit()
    conn.close()

    excel_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bkst_depo_verileri.xlsx")
    if os.path.exists(excel_file):
        try:
            username, _, _, _ = read_bkst_credentials()
            df_old = pd.read_excel(excel_file)
            if not df_old.empty:
                save_bkst_data_to_db(df_old, username)
            os.remove(excel_file)
            logger.info("bkst_depo_verileri.xlsx successfully migrated to SQLite DB and removed!")
        except Exception as e:
            logger.error(f"Migration error: {e}")

def get_system_setting(key, default=""):
    try:
        ensure_db_schema()
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = ?", (key,))
        row = c.fetchone()
        conn.close()
        if row and row[0] is not None:
            return str(row[0]).strip()
    except Exception as e:
        logger.warning(f"get_system_setting error ({key}): {e}")
    return default

def set_system_setting(key, value):
    try:
        ensure_db_schema()
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("""
            INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
            VALUES (?, ?, ?)
            ON CONFLICT(anahtar) DO UPDATE SET
            deger = excluded.deger,
            guncelleme_tarihi = excluded.guncelleme_tarihi
        """, (key, str(value or ''), now_str))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"set_system_setting error ({key}): {e}")
        return False

def delete_system_setting(key):
    try:
        ensure_db_schema()
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("DELETE FROM sistem_ayarlari WHERE anahtar = ?", (key,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"delete_system_setting error ({key}): {e}")
        return False

def read_bkst_credentials():
    # 1. Veritabanından (sistem_ayarlari) oku
    username = get_system_setting("bkst_kullanici_adi", "")
    password = get_system_setting("bkst_sifre", "")
    address_id = get_system_setting("bkst_adres_id", "")
    api_key = get_system_setting("bkst_api_key", "")
    user_name = get_system_setting("bkst_kullanici_isim", "")
    
    # 2. Geriye dönük uyumluluk ve otomatik veritabanına taşıma (Txt dosyasından DB'ye geçiş)
    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    if os.path.exists(cred_file):
        try:
            with open(cred_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("KULLANICI_ADI=") and not username:
                        username = line.split("=", 1)[1].strip()
                    elif line.startswith("SIFRE=") and not password:
                        password = line.split("=", 1)[1].strip()
                    elif line.startswith("ADRES_ID=") and not address_id:
                        raw_id = line.split("=", 1)[1].strip()
                        guid_match = re.search(r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', raw_id)
                        if guid_match:
                            address_id = guid_match.group(1).strip()
                        elif " - " in raw_id:
                            address_id = raw_id.split(" - ")[0].strip()
                        else:
                            address_id = raw_id
                    elif (line.startswith("KEY=") or line.startswith("API_KEY=")) and not api_key:
                        api_key = line.split("=", 1)[1].strip()
                    elif line.startswith("KULLANICI_ISIM=") and not user_name:
                        user_name = line.split("=", 1)[1].strip()

            # Txt dosyasında bilgi varsa veritabanına yaz ve güvenlik için txt'yi temizle/sil
            if username or password:
                if username: set_system_setting("bkst_kullanici_adi", username)
                if password: set_system_setting("bkst_sifre", password)
                if address_id: set_system_setting("bkst_adres_id", address_id)
                if api_key: set_system_setting("bkst_api_key", api_key)
                if user_name: set_system_setting("bkst_kullanici_isim", user_name)
                try:
                    os.remove(cred_file)
                    logger.info("bakanlik_giris_bilgileri.txt veritabanına aktarıldı ve güvenli şekilde silindi.")
                except Exception:
                    pass
        except Exception as e_txt:
            logger.warning(f"cred_file migration error: {e_txt}")

    return username, password, address_id, api_key

def clean_user_name(name):
    if not name:
        return ""
    name = str(name).strip()
    cleaned = re.sub(r'^\d+[\s\-]+', '', name)
    cleaned = cleaned.split(" (")[0].strip()
    return cleaned if cleaned else name

@app.context_processor
def inject_global_template_vars():
    now = time.time()
    with _template_vars_lock:
        if now > _template_vars_cache['expires_at']:
            username, password, address_id, api_key = read_bkst_credentials()
            user_name = "Giriş Yapılmadı"
            if username:
                db_name = get_system_setting("bkst_kullanici_isim", "")
                user_name = clean_user_name(db_name or username)

            v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
            v_code = "v1.0"
            v_commit = ""
            v_date = datetime.now().strftime("%d.%m.%Y")
            v_msg = "Sistem Güncel"
            if os.path.exists(v_path):
                try:
                    with open(v_path, "r", encoding="utf-8") as f:
                        v_data = json.load(f)
                        v_code = str(v_data.get("version", "v1.0")).strip()
                        v_commit = str(v_data.get("commit", "")).strip()
                        v_date = str(v_data.get("date", "")).strip()
                        v_msg = str(v_data.get("message", "Sistem Güncel")).strip()
                except Exception:
                    pass

            full_commit = f"{v_code} ({v_commit})" if v_commit else v_code
            _template_vars_cache.update({
                'expires_at': now + 30,
                'user_name': user_name,
                'v_code': v_code,
                'full_commit': full_commit,
                'v_date': v_date,
                'v_msg': v_msg
            })

        user_name = _template_vars_cache['user_name']
        v_code = _template_vars_cache['v_code']
        full_commit = _template_vars_cache['full_commit']
        v_date = _template_vars_cache['v_date']
        v_msg = _template_vars_cache['v_msg']

    global bkst_online, bkst_status
    is_offline = (bkst_online is False or bkst_status in ("offline", "error"))
    system_status_text = "Sistem Deaktif" if is_offline else "Sistem Aktif"
    system_status_cls = "offline" if is_offline else "online"

    return dict(
        current_user_name=user_name,
        current_app_version=v_code,
        current_app_commit=full_commit,
        current_app_date=v_date,
        current_app_msg=v_msg,
        bkst_online=bkst_online,
        is_system_active=not is_offline,
        system_status_text=system_status_text,
        system_status_cls=system_status_cls,
        local_session_token=LOCAL_SESSION_TOKEN
    )

def normalize_qr(qr):
    if pd.isna(qr):
        return ""
    qr_str = str(qr).strip().replace(" ", "")
    qr_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', qr_str)
    return qr_str

def parse_gs1_qr(qr_str):
    if not qr_str or pd.isna(qr_str):
        return {"gtin": None, "seri_no": None, "parti_no": None, "skt": None, "uretim_tarihi": None}

    raw = str(qr_str).strip()
    if raw.startswith("]d2") or raw.startswith("]Q3"):
        raw = raw[3:]

    raw_clean = re.sub(r'[\x00-\x1f\x7f-\x9f]', '\x1d', raw)
    tokens = [t for t in raw_clean.split('\x1d') if t]

    result = {
        "gtin": None,
        "seri_no": None,
        "parti_no": None,
        "skt": None,
        "uretim_tarihi": None
    }

    for token in tokens:
        idx = 0
        while idx < len(token):
            if token[idx:].startswith("01") and len(token[idx:]) >= 16 and token[idx+2:idx+16].isdigit():
                if not result["gtin"]:
                    result["gtin"] = token[idx+2:idx+16]
                idx += 16
                continue
            elif token[idx:].startswith("17") and len(token[idx:]) >= 8 and token[idx+2:idx+8].isdigit():
                if not result["skt"]:
                    yy, mm, dd = token[idx+2:idx+4], token[idx+4:idx+6], token[idx+6:idx+8]
                    yy_int = int(yy)
                    century = "19" if 50 <= yy_int <= 99 else "20"
                    result["skt"] = f"{dd}.{mm}.{century}{yy}"
                idx += 8
                continue
            elif token[idx:].startswith("11") and len(token[idx:]) >= 8 and token[idx+2:idx+8].isdigit():
                if not result["uretim_tarihi"]:
                    yy, mm, dd = token[idx+2:idx+4], token[idx+4:idx+6], token[idx+6:idx+8]
                    yy_int = int(yy)
                    century = "19" if 50 <= yy_int <= 99 else "20"
                    result["uretim_tarihi"] = f"{dd}.{mm}.{century}{yy}"
                idx += 8
                continue
            elif token[idx:].startswith("21") and len(token[idx:]) > 2:
                if not result["seri_no"]:
                    result["seri_no"] = token[idx+2:]
                break
            elif token[idx:].startswith("10") and len(token[idx:]) > 2:
                if not result["parti_no"]:
                    result["parti_no"] = token[idx+2:]
                break
            else:
                idx += 1

    if not result["gtin"]:
        digits14 = re.findall(r'\d{14}', raw)
        if digits14:
            result["gtin"] = digits14[0]

    return result

def extract_gtin(qr_str):
    parsed = parse_gs1_qr(qr_str)
    return parsed.get("gtin")

def find_koli_column(cols):
    cols_list = [str(c) for c in cols]
    for c in cols_list:
        if 'koli' in c.lower():
            return c
    for c in cols_list:
        if 'paket' in c.lower():
            return c
    for c in cols_list:
        if 'palet' in c.lower():
            return c
    return None

def get_bkst_cache():
    username, _, _, _ = read_bkst_credentials()
    if not username:
        # FIX-CACHE-ANON: username yoksa DB'den oku, bellek önbelleğine yazmadan döndür
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        try:
            df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri", conn)
        except Exception:
            df = pd.DataFrame()
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if df is None or df.empty:
            return None, {}, {}, {}

        df = df.rename(columns={
            'gtin': 'Gtin Numarası',
            'urun_adi': 'Ürün Adı',
            'seri_no': 'Seri Numarası',
            'parti_no': 'Parti Numarası',
            'koli_no': 'Koli Numarası',
            'palet_no': 'Palet Numarası',
            'uretim_tarihi': 'Üretim Tarihi',
            'skt': 'Son Kullanma Tarihi',
            'tam_karekod': 'Karekod'
        })
        if 'Karekod' in df.columns:
            df = df.drop_duplicates(subset=['Karekod'])

        koli_dict = {}
        qr_dict = {}
        gtin_dict = {}

        for r in df.to_dict(orient="records"):
            qr_val = normalize_qr(str(r.get("Karekod", "")))
            gtin_val = normalize_qr(str(r.get("Gtin Numarası", "")))
            koli_val = str(r.get("Koli Numarası", "")).strip().upper()

            if qr_val:
                qr_dict[qr_val] = r
                qr_dict[qr_val.casefold()] = r
            if gtin_val:
                if gtin_val not in gtin_dict:
                    gtin_dict[gtin_val] = r
                if gtin_val.casefold() not in gtin_dict:
                    gtin_dict[gtin_val.casefold()] = r

            if koli_val and koli_val != "NAN":
                if koli_val not in koli_dict:
                    koli_dict[koli_val] = []
                koli_dict[koli_val].append(r)

        return (df, qr_dict, gtin_dict, koli_dict)

    user_key = username
    with _state_lock:
        if user_key in _user_cache_map:
            if user_key in _cache_access_order:
                _cache_access_order.remove(user_key)
            _cache_access_order.append(user_key)
            return _user_cache_map[user_key]

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ''", conn, params=(username,))
    except Exception as e:
        logger.error(f"Error querying bkst_depo_verileri, repairing schema: {e}")
        try:
            ensure_db_schema()
            df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ''", conn, params=(username,))
        except Exception:
            df = pd.DataFrame()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if df is None or df.empty:
        return None, {}, {}, {}

    df = df.rename(columns={
        'gtin': 'Gtin Numarası',
        'urun_adi': 'Ürün Adı',
        'seri_no': 'Seri Numarası',
        'parti_no': 'Parti Numarası',
        'koli_no': 'Koli Numarası',
        'palet_no': 'Palet Numarası',
        'uretim_tarihi': 'Üretim Tarihi',
        'skt': 'Son Kullanma Tarihi',
        'tam_karekod': 'Karekod'
    })

    if 'Karekod' in df.columns:
        df = df.drop_duplicates(subset=['Karekod'])

    koli_dict = {}
    qr_dict = {}
    gtin_dict = {}

    for r in df.to_dict(orient="records"):
        qr_val = normalize_qr(str(r.get("Karekod", "")))
        gtin_val = normalize_qr(str(r.get("Gtin Numarası", "")))
        koli_val = str(r.get("Koli Numarası", "")).strip().upper()

        if qr_val:
            qr_dict[qr_val] = r
            qr_dict[qr_val.casefold()] = r
        if gtin_val:
            if gtin_val not in gtin_dict:
                gtin_dict[gtin_val] = r
            if gtin_val.casefold() not in gtin_dict:
                gtin_dict[gtin_val.casefold()] = r

        if koli_val and koli_val != "NAN":
            if koli_val not in koli_dict:
                koli_dict[koli_val] = []
            koli_dict[koli_val].append(r)

    res = (df, qr_dict, gtin_dict, koli_dict)

    with _state_lock:
        _user_cache_map[user_key] = res
        if user_key in _cache_access_order:
            _cache_access_order.remove(user_key)
        _cache_access_order.append(user_key)

        while len(_user_cache_map) > 5 and _cache_access_order:
            oldest_key = _cache_access_order.pop(0)
            _user_cache_map.pop(oldest_key, None)

    return res

init_db()

def check_is_parti_no(code, df):
    if not code or df is None or df.empty:
        return False, 0, ""
    code_clean = str(code).strip()
    candidates = [code_clean.casefold()]
    if code_clean.startswith("(10)") and len(code_clean) > 4:
        candidates.append(code_clean[4:].strip().casefold())
    elif code_clean.startswith("10") and len(code_clean) > 2:
        candidates.append(code_clean[2:].strip().casefold())

    for cand in candidates:
        if not cand:
            continue
        matches = [
            r for r in df.to_dict(orient="records")
            if str(r.get("Parti Numarası", "")).strip().casefold() == cand
        ]
        if matches:
            urun_adi = str(matches[0].get("Ürün Adı", "")).strip()
            return True, len(matches), urun_adi

    return False, 0, ""

def check_is_gtin_no(code, df, gtin_map):
    if not code or df is None or df.empty:
        return False, 0, ""
    code_str = str(code).strip()

    # Tam bir GS1 karekod ise (içinde seri numarası varsa), bu saf GTIN değildir!
    parsed = parse_gs1_qr(code_str)
    if parsed and parsed.get('seri_no'):
        return False, 0, ""

    norm = normalize_qr(code_str)
    candidates = [norm]
    if norm.isdigit():
        candidates.append(norm.lstrip('0'))
        if len(norm) == 13:
            candidates.append('0' + norm)
        elif len(norm) == 14 and norm.startswith('0'):
            candidates.append(norm[1:])

    matched_name = ""
    for cand in candidates:
        if cand and gtin_map and cand in gtin_map:
            matched_name = str(gtin_map[cand].get('Ürün Adı', '')).strip()
            break

    if not matched_name and norm.isdigit() and len(norm) in (8, 12, 13, 14):
        for r in df.to_dict(orient="records"):
            r_gtin = normalize_qr(str(r.get("Gtin Numarası") or r.get("Gtin / Barkod") or r.get("gtin") or ""))
            if r_gtin in candidates:
                matched_name = str(r.get("Ürün Adı", "")).strip()
                break

    if matched_name:
        count = sum(
            1 for r in df.to_dict(orient="records")
            if normalize_qr(str(r.get("Gtin Numarası") or r.get("Gtin / Barkod") or r.get("gtin") or "")) in candidates
        )
        return True, count, matched_name

    return False, 0, ""

def resolve_product_from_cache(code, df, qr_map, gtin_map):
    if not code:
        return None
    code_norm = normalize_qr(code)
    code_case = code_norm.casefold()

    # Parti Numarası tekil kutu olarak asla eşleşmemelidir
    is_parti, _, _ = check_is_parti_no(code_norm, df)
    if is_parti:
        return None

    # GTIN / Barkod tekil kutu olarak asla eşleşmemelidir
    is_gtin, _, _ = check_is_gtin_no(code_norm, df, gtin_map)
    if is_gtin:
        return None

    # 1. Tam karekod eşleşmesi
    if qr_map and code_norm in qr_map:
        return qr_map[code_norm]

    # 2. GS1 Karekod ayrıştırma denemesi (Karekod içindeki seri ve gtin)
    parsed = parse_gs1_qr(code_norm)
    if parsed and parsed.get('seri_no'):
        p_seri = str(parsed['seri_no']).strip().casefold()
        p_gtin = str(parsed.get('gtin', '')).strip().casefold()
        if df is not None and not df.empty:
            for r in df.to_dict(orient="records"):
                r_seri = str(r.get("Seri Numarası", "")).strip().casefold()
                if r_seri and r_seri == p_seri:
                    r_gtin = str(r.get("Gtin Numarası") or r.get("Gtin / Barkod") or "").strip().casefold()
                    if not p_gtin or not r_gtin or p_gtin == r_gtin:
                        return r

    # 3. Seri Numarası doğrudan eşleşmesi (Kullanıcı barkod yerine seri no okuttuysa)
    if df is not None and not df.empty:
        for r in df.to_dict(orient="records"):
            r_seri = str(r.get("Seri Numarası", "")).strip().casefold()
            if r_seri and r_seri == code_case:
                return r

    return None

def build_gtin_name_map():
    mapping = {}
    try:
        df, _, _, _ = get_bkst_cache()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                gtin = normalize_qr(str(row.get('gtin', row.get('Gtin Numarası', ''))))
                name = str(row.get('urun_adi', row.get('Ürün Adı', ''))).strip()
                if gtin and name:
                    mapping[gtin] = name
    except Exception:
        pass
    return mapping

def parse_system_file(file_or_path, gtin_map=None):
    if gtin_map is None:
        gtin_map = {}
    items = []
    
    if isinstance(file_or_path, str):
        file_path = file_or_path
        filename = os.path.basename(file_path)
        if filename.endswith(('.xls', '.xlsx')):
            try:
                xl = pd.ExcelFile(file_path)
                target_sheet = "Tüm Ürünler (QR)" if "Tüm Ürünler (QR)" in xl.sheet_names else xl.sheet_names[0]
                df = pd.read_excel(file_path, sheet_name=target_sheet)
                
                qr_col = None
                for col in df.columns:
                    c_str = str(col).lower()
                    if 'karekod' in c_str or 'qr' in c_str:
                        qr_col = col
                        break
                if qr_col is None:
                    qr_col = df.columns[0]
                    
                name_col = None
                for col in df.columns:
                    if 'ürün adı' in str(col).lower() or 'urun' in str(col).lower() or 'ad' in str(col).lower():
                        name_col = col
                        break
                        
                for _, row in df.iterrows():
                    q = normalize_qr(row[qr_col])
                    if q:
                        p_name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""
                        if not p_name:
                            gtin = extract_gtin(q)
                            p_name = gtin_map.get(gtin, "Bilinmeyen Ürün")
                            
                        metadata = {}
                        for col in df.columns:
                            if col not in [qr_col, name_col]:
                                val = row[col]
                                if pd.notna(val):
                                    metadata[str(col)] = str(val)
                                    
                        items.append({
                            "qr": q,
                            "product_name": p_name,
                            "metadata": metadata
                        })
            except Exception as e:
                logger.error(f"Error parsing system file: {e}")
    else:
        file = file_or_path
        filename = file.filename
        if filename.endswith(('.xls', '.xlsx')):
            try:
                xl = pd.ExcelFile(file)
                target_sheet = "Tüm Ürünler (QR)" if "Tüm Ürünler (QR)" in xl.sheet_names else xl.sheet_names[0]
                df = pd.read_excel(file, sheet_name=target_sheet)
                
                qr_col = None
                for col in df.columns:
                    c_str = str(col).lower()
                    if 'karekod' in c_str or 'qr' in c_str:
                        qr_col = col
                        break
                if qr_col is None:
                    qr_col = df.columns[0]
                    
                name_col = None
                for col in df.columns:
                    if 'ürün adı' in str(col).lower() or 'urun' in str(col).lower() or 'ad' in str(col).lower():
                        name_col = col
                        break
                        
                for _, row in df.iterrows():
                    q = normalize_qr(row[qr_col])
                    if q:
                        p_name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""
                        if not p_name:
                            gtin = extract_gtin(q)
                            p_name = gtin_map.get(gtin, "Bilinmeyen Ürün")
                            
                        metadata = {}
                        for col in df.columns:
                            if col not in [qr_col, name_col]:
                                val = row[col]
                                if pd.notna(val):
                                    metadata[str(col)] = str(val)
                                    
                        items.append({
                            "qr": q,
                            "product_name": p_name,
                            "metadata": metadata
                        })
            except Exception as e:
                logger.error(f"Error parsing system file: {e}")

    return items

def parse_sales_file(file):
    sales_qrs = []
    filename = file.filename
    
    if filename.endswith('.txt'):
        content = file.read().decode('utf-8', errors='ignore')
        for line in content.splitlines():
            q = normalize_qr(line)
            if q:
                sales_qrs.append(q)
    elif filename.endswith(('.xls', '.xlsx')):
        df = pd.read_excel(file)
        qr_col = None
        for col in df.columns:
            col_str = str(col).lower()
            if 'qr' in col_str or 'karekod' in col_str or 'barkod' in col_str:
                qr_col = col
                break
        if qr_col is None:
            qr_col = df.columns[0]
            
        for val in df[qr_col].dropna():
            q = normalize_qr(val)
            if q:
                sales_qrs.append(q)
                
    return sales_qrs

# ── MAIN HTML ROUTES ────────────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect('/cikis')

@app.route('/stok-esitleme')
def stok_esitleme_page():
    return render_template('index.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/cikis')
def cikis_page():
    return render_template('cikis.html')

@app.route('/cikis-listesi')
@app.route('/cikis_listesi')
def cikis_listesi_page():
    return render_template('cikis_listesi.html')

@app.route('/depo_stoklari')
@app.route('/depo-stoklari')
def depo_stoklari_page():
    return render_template('depo_stoklari.html')

@app.route('/depo_kabul')
def depo_kabul_page():
    return render_template('depo_kabul.html')

@app.route('/kullaniciya-satis')
@app.route('/kullaniciya_satis')
def kullaniciya_satis_page():
    return render_template('kullaniciya_satis.html')

@app.route('/istatistikler')
@app.route('/raporlar')
def istatistikler_page():
    return render_template('istatistikler.html')

# ── STOK KARŞILAŞTIRMA & RAPORLAMA API ──────────────────────────────────────
@app.route('/api/compare', methods=['POST'])
def api_compare():
    try:
        if 'system_file' not in request.files or 'sales_file' not in request.files:
            return jsonify({"success": False, "error": "Lütfen hem Sistem Depo hem de Satış dosyasını yükleyin."})
            
        system_file = request.files['system_file']
        sales_file = request.files['sales_file']
        
        gtin_map = build_gtin_name_map()
        inventory = parse_system_file(system_file, gtin_map)
        sales_qrs = parse_sales_file(sales_file)
        
        if not inventory:
            return jsonify({"success": False, "error": "Sistem Depo dosyasından geçerli karekod okunamadı."})
        if not sales_qrs:
            return jsonify({"success": False, "error": "Satış dosyasından geçerli karekod okunamadı."})
            
        inventory_dict = {item["qr"]: item for item in inventory}
        matched_sales = []
        unmatched_sales = []
        sold_qrs_set = set()
        sales_by_product = {}
        
        koli_stats = {}
        for item in inventory:
            meta = item.get("metadata", {})
            koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO")
            if koli_no:
                koli_no = str(koli_no).strip()
                if koli_no not in koli_stats:
                    koli_stats[koli_no] = {"total": 0, "sold": 0, "product_name": item["product_name"]}
                koli_stats[koli_no]["total"] += 1
        
        for sale_qr in sales_qrs:
            if sale_qr in inventory_dict:
                item = dict(inventory_dict[sale_qr])
                meta = item.get("metadata", {})
                koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO")
                if koli_no:
                    koli_no = str(koli_no).strip()
                    if koli_no in koli_stats:
                        koli_stats[koli_no]["sold"] += 1

                item["aciklama"] = "Bu ürün sistemde görünüyor, sistemden çık"
                matched_sales.append(item)
                sold_qrs_set.add(sale_qr)
                p_name = item["product_name"]
                sales_by_product[p_name] = sales_by_product.get(p_name, 0) + 1
            else:
                gtin = extract_gtin(sale_qr)
                p_name = gtin_map.get(gtin, "Sistem Dışı Ürün (GTIN: {})".format(gtin) if gtin else "Bilinmeyen Karekod formatı")
                unmatched_sales.append({
                    "qr": sale_qr,
                    "product_name": p_name,
                    "aciklama": "Depoda bulunamadı (sistem dışı satış)"
                })
                sales_by_product[p_name] = sales_by_product.get(p_name, 0) + 1
                
        for item in matched_sales:
            meta = item.get("metadata", {})
            koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO")
            if koli_no:
                koli_no = str(koli_no).strip()
                stats = koli_stats.get(koli_no)
                if stats:
                    sold_cnt = stats["sold"]
                    tot_cnt = stats["total"]
                    rem_cnt = max(0, tot_cnt - sold_cnt)
                    if sold_cnt >= tot_cnt:
                        item["Koli Durumu"] = f"Koli {koli_no}: Tamamı Satıldı ({sold_cnt}/{tot_cnt})"
                    else:
                        item["Koli Durumu"] = f"Koli {koli_no}: Kısmi Satıldı ({sold_cnt}/{tot_cnt} - Depoda {rem_cnt} Kaldı)"
            else:
                item["Koli Durumu"] = "Koli Bilgisi Yok"
                
        remaining_inventory = [item for item in inventory if item["qr"] not in sold_qrs_set]
        
        remaining_counts = {}
        for item in remaining_inventory:
            p_name = item["product_name"]
            remaining_counts[p_name] = remaining_counts.get(p_name, 0) + 1
            
        initial_counts = {}
        for item in inventory:
            p_name = item["product_name"]
            initial_counts[p_name] = initial_counts.get(p_name, 0) + 1

        username_cred, _, _, _ = read_bkst_credentials()
        user_key = username_cred or "_anon"

        with _state_lock:
            _compare_cache_map[user_key] = {
                "matched_sales": matched_sales,
                "unmatched_sales": unmatched_sales,
                "remaining_inventory": remaining_inventory,
                "sales_by_product": sales_by_product,
                "initial_counts": initial_counts,
                "remaining_counts": remaining_counts,
                "koli_stats": koli_stats
            }

        return jsonify({
            "success": True,
            "total_initial": len(inventory),
            "total_sold": len(sales_qrs),
            "total_matched": len(matched_sales),
            "total_unmatched": len(unmatched_sales),
            "total_remaining": len(remaining_inventory),
            "stats": {
                "total_system": len(inventory),
                "total_sales": len(sales_qrs),
                "matched": len(matched_sales),
                "remaining": len(remaining_inventory),
                "total_unmatched": len(unmatched_sales)
            },
            "matched_sales": matched_sales,
            "sales_by_product": sales_by_product,
            "remaining_counts": remaining_counts
        })
    except Exception as e:
        logger.error(f"api_compare error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/download/sales', methods=['GET'])
@app.route('/api/download/full_report', methods=['GET'])
def download_sales():
    username_cred, _, _, _ = read_bkst_credentials()
    user_key = username_cred or "_anon"
    with _state_lock:
        cached_data = _compare_cache_map.get(user_key)
        if not cached_data:
            return "No comparison run yet", 400
        res_copy = dict(cached_data)
        
    matched_rows = []
    for item in res_copy.get("matched_sales", []):
        meta = item.get("metadata", {})
        koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO") or ""
        gtin = extract_gtin(item["qr"]) or meta.get("Gtin Numarası") or meta.get("BARKOD") or ""
        seri_no = meta.get("Seri Numarası") or meta.get("SERINO") or ""
        parti_no = meta.get("Parti Numarası") or meta.get("SARJNO") or ""
        palet_no = meta.get("Palet Numarası") or meta.get("PALETNO") or ""
        uretim = meta.get("Üretim Tarihi") or meta.get("URETIMTARIHI") or ""
        skt = meta.get("Son Kullanma Tarihi") or meta.get("SKT") or ""

        row = {
            "Ürün Adı": item["product_name"],
            "Karekod": item["qr"],
            "Gtin / Barkod": gtin,
            "Koli Numarası": koli_no,
            "Seri Numarası": seri_no,
            "Parti Numarası": parti_no,
            "Palet Numarası": palet_no,
            "Üretim Tarihi": uretim,
            "Son Kullanma Tarihi": skt,
            "Koli / Depo Durumu": item.get("Koli Durumu", ""),
            "Açıklama / İşlem": "Bu ürün sistemde görünüyor, sistemden çık"
        }
        matched_rows.append(row)

    df_matched = pd.DataFrame(matched_rows)
    if not df_matched.empty:
        df_matched = df_matched.sort_values(by=["Ürün Adı"])
        
    unmatched_rows = []
    for item in res_copy.get("unmatched_sales", []):
        unmatched_rows.append({
            "Ürün Adı": item["product_name"],
            "Karekod": item["qr"],
            "Açıklama / Durum": "Depoda Bulunamadı (Sistem Dışı Hatalı Satış!)"
        })
    df_unmatched = pd.DataFrame(unmatched_rows)
    if not df_unmatched.empty:
        df_unmatched = df_unmatched.sort_values(by=["Ürün Adı"])
        
    summary_rows = []
    for p_name, qty in res_copy.get("sales_by_product", {}).items():
        summary_rows.append({
            "Ürün Adı": p_name,
            "Satılan Miktar (Adet)": qty
        })
    df_summary = pd.DataFrame(summary_rows)
    if not df_summary.empty:
        df_summary = df_summary.sort_values(by=["Ürün Adı"])
        
    koli_rows = []
    for koli_no, stats in res_copy.get("koli_stats", {}).items():
        if stats["sold"] > 0:
            tot = stats["total"]
            sold = stats["sold"]
            rem = max(0, tot - sold)
            status = f"Tamamı Satıldı ({sold}/{tot})" if sold >= tot else f"Kısmi Satıldı ({sold}/{tot} - Depoda {rem} Kaldı)"
            koli_rows.append({
                "Koli Numarası": koli_no,
                "Ürün Adı": stats["product_name"],
                "Toplam Ürün": tot,
                "Satılan Ürün": sold,
                "Depoda Kalan": rem,
                "Durum": status
            })
    df_koli = pd.DataFrame(koli_rows)
    if not df_koli.empty:
        df_koli = df_koli.sort_values(by=["Durum", "Koli Numarası"])
        
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if not df_matched.empty:
            df_matched.to_excel(writer, index=False, sheet_name="Düşülecek Satışlar")
        else:
            pd.DataFrame(columns=["Ürün Adı", "Karekod", "Koli Numarası", "Koli / Depo Durumu", "Açıklama / İşlem"]).to_excel(writer, index=False, sheet_name="Düşülecek Satışlar")
            
        if not df_unmatched.empty:
            df_unmatched.to_excel(writer, index=False, sheet_name="Depoda Olmayan Satışlar")
        if not df_koli.empty:
            df_koli.to_excel(writer, index=False, sheet_name="Koli Durum Özeti")
        if not df_summary.empty:
            df_summary.to_excel(writer, index=False, sheet_name="Satış Özet Tablosu")
            
    output.seek(0)
    
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="satis_raporu_detayli.xlsx"
    )

@app.route('/api/download/remaining', methods=['GET'])
def download_remaining():
    username_cred, _, _, _ = read_bkst_credentials()
    user_key = username_cred or "_anon"
    with _state_lock:
        cached_data = _compare_cache_map.get(user_key)
        if not cached_data:
            return "No comparison run yet", 400
        res_copy = dict(cached_data)
        
    rows = []
    for item in res_copy.get("remaining_inventory", []):
        meta = item.get("metadata", {})
        koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO") or ""
        gtin = extract_gtin(item["qr"]) or meta.get("Gtin Numarası") or meta.get("BARKOD") or ""
        seri_no = meta.get("Seri Numarası") or meta.get("SERINO") or ""
        parti_no = meta.get("Parti Numarası") or meta.get("SARJNO") or ""
        palet_no = meta.get("Palet Numarası") or meta.get("PALETNO") or ""
        uretim = meta.get("Üretim Tarihi") or meta.get("URETIMTARIHI") or ""
        skt = meta.get("Son Kullanma Tarihi") or meta.get("SKT") or ""

        row = {
            "Ürün Adı": item["product_name"],
            "Karekod": item["qr"],
            "Gtin / Barkod": gtin,
            "Koli Numarası": koli_no,
            "Seri Numarası": seri_no,
            "Parti Numarası": parti_no,
            "Palet Numarası": palet_no,
            "Üretim Tarihi": uretim,
            "Son Kullanma Tarihi": skt,
            "Durum": "Depoda Mevcut"
        }
        rows.append(row)
        
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(by=["Ürün Adı"])
        
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Güncel Kalan Envanter")
    output.seek(0)
    
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="kalan_sistem_envanteri_guncel.xlsx"
    )

def find_matching_koli(code, koli_dict):
    if not code or not koli_dict:
        return None, []

    raw_code = str(code).strip().upper()
    clean_code_all = re.sub(r'[^A-Z0-9]', '', raw_code.replace("İ", "I"))
    digits_only = re.sub(r'\D', '', raw_code)

    digits_stripped = digits_only
    if len(digits_only) == 20 and digits_only.startswith('00'):
        digits_stripped = digits_only[2:]

    if raw_code in koli_dict:
        return raw_code, koli_dict[raw_code]

    if code in koli_dict:
        return code, koli_dict[code]

    for k_key, k_rows in koli_dict.items():
        key_str = str(k_key).strip().upper()
        clean_key_all = re.sub(r'[^A-Z0-9]', '', key_str.replace("İ", "I"))
        key_digits = re.sub(r'\D', '', key_str)

        key_digits_stripped = key_digits
        if len(key_digits) == 20 and key_digits.startswith('00'):
            key_digits_stripped = key_digits[2:]

        if clean_code_all and clean_key_all and len(clean_code_all) >= 6 and len(clean_key_all) >= 6 and clean_code_all == clean_key_all:
            return k_key, k_rows

        if (digits_stripped and key_digits_stripped
            and len(digits_stripped) >= 6 and len(key_digits_stripped) >= 6
            and digits_stripped == key_digits_stripped):
            return k_key, k_rows

    return None, []

@app.route('/api/audit_box', methods=['GET'])
def audit_box():
    code = request.args.get('code', '').strip().upper()
    if not code:
        return jsonify({"success": False, "error": "Barkod veya koli no boş olamaz."})

    df, qr_dict, gtin_dict, koli_dict = get_bkst_cache()
    code_norm = normalize_qr(code)
    target_koli = None
    matched_rows = []
    is_koli_scan = False
    scanned_qr = None

    if df is not None and not df.empty:
        matched_koli_key, matched_koli_rows = find_matching_koli(code, koli_dict)
        if matched_koli_key and matched_koli_rows:
            target_koli = matched_koli_key
            matched_rows = matched_koli_rows
            is_koli_scan = True

        # Koli değilse ve bir GTIN / Çizgi Barkod veya Parti No ise REDDET (rastgele ürün seçilmesini engelle)
        if not is_koli_scan:
            is_gtin, g_count, g_urun = check_is_gtin_no(code_norm, df, gtin_dict)
            if is_gtin:
                return jsonify({
                    "success": False,
                    "is_gtin_no": True,
                    "error": f'"{code}" bir GTIN / Çizgi Barkod numarasıdır ({g_urun}). Bu barkod tekil bir ilaca ait karekod değildir. Lütfen kutu üzerindeki 2D Karekodu (DataMatrix) okutunuz.'
                })
            if code_norm.isdigit() and len(code_norm) in (8, 12, 13, 14):
                return jsonify({
                    "success": False,
                    "is_gtin_no": True,
                    "error": f'"{code}" bir ürün çizgi barkodudur (GTIN). Tekil ilaç sayımı için lütfen kutu üzerindeki 2D Karekodu (DataMatrix) okutunuz.'
                })
            is_parti, p_count, p_urun = check_is_parti_no(code_norm, df)
            if is_parti:
                return jsonify({
                    "success": False,
                    "is_parti_no": True,
                    "error": f'"{code}" bir Parti Numarasıdır ({p_urun}). Bu numara üretim grubunu temsil eder. Lütfen kutu üzerindeki 2D Karekodu (DataMatrix) okutunuz.'
                })

        if not target_koli and code_norm in qr_dict:
            item_row = qr_dict[code_norm]
            scanned_qr = str(item_row.get("Karekod", item_row.get("QR", ""))).strip()
            k_col = find_koli_column(item_row.keys())
            if k_col and item_row.get(k_col):
                val = str(item_row[k_col]).strip().upper()
                if val and val != "NAN":
                    target_koli = val
                    matched_rows = koli_dict.get(target_koli, [item_row])
            if not target_koli:
                target_koli = str(item_row.get("Ürün Adı", "Kolisiz Stok Ürün"))
                matched_rows = [item_row]

        if not target_koli and len(code_norm) >= 20:
            for q_key, r_dict in qr_dict.items():
                if len(q_key) >= 20 and (code_norm in q_key or q_key in code_norm):
                    item_row = r_dict
                    scanned_qr = str(item_row.get("Karekod", item_row.get("QR", ""))).strip()
                    k_col = find_koli_column(item_row.keys())
                    if k_col and item_row.get(k_col):
                        val = str(item_row[k_col]).strip().upper()
                        if val and val != "NAN":
                            target_koli = val
                            matched_rows = koli_dict.get(target_koli, [item_row])
                    if not target_koli:
                        target_koli = str(item_row.get("Ürün Adı", "Kolisiz Stok Ürün"))
                        matched_rows = [item_row]
                    break

    if not target_koli or not matched_rows:
        if len(code_norm) < 18:
            return jsonify({
                "success": False,
                "error": f'"{code}" geçerli bir 2D Karekod (DataMatrix) veya koli numarası değildir. Lütfen kutu üzerindeki karekodu okutunuz.'
            })
        target_koli = "Sistem Dışı Ürün"
        scanned_qr = code_norm if code_norm else code
        matched_rows = [{
            "Ürün Adı": "Sistem Dışı / Bilinmeyen Ürün",
            "Karekod": scanned_qr,
            "Gtin Numarası": extract_gtin(code_norm) or "—",
            "Seri Numarası": "—",
            "Parti Numarası": "—",
            "Palet Numarası": "—"
        }]

    results = []
    seen_qrs = set()
    for row in matched_rows:
        qr_val = str(row.get("Karekod", row.get("QR", ""))).strip()
        if not qr_val or qr_val in seen_qrs:
            continue
        seen_qrs.add(qr_val)

        results.append({
            "product_name": str(row.get("Ürün Adı", "Ürün")),
            "qr": qr_val,
            "koli_no": target_koli,
            "gtin": str(row.get("Gtin Numarası", row.get("BARKOD", ""))),
            "seri_no": str(row.get("Seri Numarası", row.get("SERINO", ""))),
            "parti_no": str(row.get("Parti Numarası", row.get("SARJNO", ""))),
            "palet_no": str(row.get("Palet Numarası", row.get("PALETNO", ""))),
            "uretim_tarihi": str(row.get("Üretim Tarihi", row.get("URETIMTARIHI", ""))),
            "skt": str(row.get("Son Kullanma Tarihi", row.get("SKT", "")))
        })

    return jsonify({
        "success": True,
        "is_koli_scan": is_koli_scan,
        "scanned_qr": scanned_qr,
        "koli_no": target_koli,
        "total_items": len(results),
        "items": results
    })

@app.route('/api/audit_all', methods=['POST', 'GET'])
def audit_all():
    df, qr_dict, gtin_dict, koli_dict = get_bkst_cache()
    if df is None or df.empty:
        return jsonify({"success": False, "error": "Bakanlık depo verisi bulunamadı."})

    gtins = []
    product_names = []

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        gtins = data.get('gtins', [])
        product_names = data.get('product_names', [])
    else:
        gtin_str = request.args.get('gtins', '')
        if gtin_str:
            gtins = [g.strip() for g in gtin_str.split(',') if g.strip()]

    scanned_gtin_norms = set()
    for g in gtins:
        if not g: continue
        digits = re.sub(r'\D', '', str(g)).lstrip('0')
        if digits:
            scanned_gtin_norms.add(digits)

    scanned_pnames = set(str(p).strip().upper() for p in product_names if p and str(p).strip() != '—')

    results = []
    seen_qrs = set()
    koli_col = find_koli_column(df.columns)

    for _, row in df.iterrows():
        r = row.to_dict()
        qr_val = str(r.get("Karekod", r.get("QR", ""))).strip()
        if not qr_val or qr_val in seen_qrs:
            continue

        raw_gtin = str(r.get("Gtin Numarası", r.get("BARKOD", ""))).strip()
        if not raw_gtin or raw_gtin == "—" or raw_gtin == "NAN":
            raw_gtin = extract_gtin(qr_val) or ""

        row_gtin_norm = re.sub(r'\D', '', raw_gtin).lstrip('0')
        row_pname = str(r.get("Ürün Adı", "")).strip().upper()

        gtin_matched = scanned_gtin_norms and row_gtin_norm in scanned_gtin_norms
        pname_matched = scanned_pnames and row_pname in scanned_pnames

        if (scanned_gtin_norms or scanned_pnames) and not (gtin_matched or pname_matched):
            continue

        seen_qrs.add(qr_val)
        koli_val = str(r.get(koli_col, "")).strip() if koli_col and pd.notna(r.get(koli_col)) else "Kolisiz Stok"

        results.append({
            "product_name": str(r.get("Ürün Adı", "Ürün")),
            "qr": qr_val,
            "koli_no": koli_val,
            "gtin": raw_gtin or "—",
            "seri_no": str(r.get("Seri Numarası", r.get("SERINO", ""))),
            "parti_no": str(r.get("Parti Numarası", r.get("SARJNO", ""))),
            "palet_no": str(r.get("Palet Numarası", r.get("PALETNO", ""))),
            "uretim_tarihi": str(r.get("Üretim Tarihi", r.get("URETIMTARIHI", ""))),
            "skt": str(r.get("Son Kullanma Tarihi", r.get("SKT", "")))
        })

    return jsonify({
        "success": True,
        "total_items": len(results),
        "gtin_count": len(scanned_gtin_norms),
        "items": results
    })

@app.route('/api/download/audit_excel', methods=['POST'])
def download_audit_excel():
    try:
        data = request.json
        if not data or not isinstance(data, list):
             return jsonify({"error": "Geçersiz veri"}), 400
             
        df = pd.DataFrame(data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="Eksik Ürünler")
            
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name='koli_sayim_eksikler.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"download_audit_excel error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

def check_internet_connection():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(("8.8.8.8", 53))
        s.close()
        return True
    except Exception:
        return False

# ── BKST ASYNC WORKER & API ROUTES ───────────────────────────────────────────
# bkst_online: None = henüz denenmedi, True = Bakanlık'tan veri alındı, False = bağlantı/veri sorunu
bkst_online = None
bkst_fetched_count = 0


_local_count_cache = {"val": 0, "ts": 0}
_local_count_lock = threading.Lock()

def _local_item_count():
    try:
        df_cache, _, _, _ = get_bkst_cache()
        return len(df_cache) if df_cache is not None else 0
    except Exception:
        return 0

def _local_item_count_cached():
    with _local_count_lock:
        now = time.monotonic()
        if now - _local_count_cache["ts"] < 30:
            return _local_count_cache["val"]
    v = _local_item_count()
    with _local_count_lock:
        _local_count_cache["val"] = v
        _local_count_cache["ts"] = time.monotonic()
    return v


def _set_bkst_state(status, message, online, fetched=0):
    global bkst_status, bkst_message, bkst_online, bkst_fetched_count
    with _state_lock:
        bkst_status = status
        bkst_message = message
        bkst_online = online
        bkst_fetched_count = fetched


def _mark_offline(reason):
    """Bakanlığa ulaşılamadı / veri gelmedi: yerel veriler korunur, sistem DEAKTİF işaretlenir."""
    local_cnt = _local_item_count()
    msg = (f"⚠️ SİSTEM DEAKTİF: {reason} Bakanlık verisi güncellenemedi. "
           f"Yerel veritabanındaki son kayıtlı {local_cnt} adet stok kullanılıyor.")
    logger.warning(f"BKST offline: {reason} (local={local_cnt})")
    _set_bkst_state("offline", msg, False, 0)


def _do_fetch_api_worker():
    global _app_bkst_synced
    logger.info("BKST fetch_api worker thread started")
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password:
        _set_bkst_state("error", "❌ HATA: Giriş bilgileri (KULLANICI_ADI / SIFRE) bulunamadı! Lütfen tekrar giriş yapın.", False)
        return

    if not check_internet_connection():
        _mark_offline("İnternet bağlantısı yok.")
        return

    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest"
        })
        if api_key:
            session.headers.update({"Authorization": f"Bearer {api_key}", "Key": api_key})

        # 1) Ana sayfa
        try:
            r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(5, 10))
        except Exception as e_home:
            _mark_offline(f"Bakanlık sunucusuna (bkst.tarbil.gov.tr) ulaşılamıyor ({type(e_home).__name__}).")
            return
        if r_home.status_code != 200:
            _mark_offline(f"Bakanlık sunucusu hata döndürdü (HTTP {r_home.status_code}).")
            return

        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        # 2) Giriş
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf",
                                 data={"tcNo": username, "sifre": password, "__RequestVerificationToken": token1},
                                 verify=SSL_VERIFY, timeout=(5, 15))
        if res_login.status_code != 200:
            _mark_offline(f"Bakanlık giriş servisi yanıt vermiyor (HTTP {res_login.status_code}).")
            return
        if "0" not in res_login.text:
            _set_bkst_state("error", "❌ HATA: Bakanlık kullanıcı adı veya şifreniz yanlış! Sistem deaktif.", False)
            return

        # 3) Stok sayfası + GLN
        r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(5, 10))
        token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
        token2 = token2_match.group(1) if token2_match else token1

        gln_guid = address_id
        if not gln_guid or len(gln_guid) < 32:
            for f_type in ["0", "1", "2"]:
                try:
                    r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN",
                                         data={"FirmType": f_type, "__RequestVerificationToken": token2},
                                         verify=SSL_VERIFY, timeout=(5, 10))
                    if r_gln.status_code == 200:
                        gln_data = r_gln.json()
                        if isinstance(gln_data, list) and len(gln_data) > 0:
                            val = str(gln_data[0].get("Value") or "").strip()
                            if val and len(val) >= 32:
                                gln_guid = val
                                break
                except Exception:
                    pass
                if gln_guid and len(gln_guid) >= 32:
                    break
        if not gln_guid or len(gln_guid) < 32:
            gln_guid = address_id or ""
            if not gln_guid or len(gln_guid) < 32:
                _mark_offline("Bakanlık şirket adres (GLN) bilgisi bulunamadı. Lütfen tekrar giriş yapınız.")
                return

        # 4) Stok listesi
        r_grid = session.post("https://bkst.tarbil.gov.tr/Main/GetStockList",
                              data={"CompanyAddressId": gln_guid, "Gtin": "", "__RequestVerificationToken": token2},
                              verify=SSL_VERIFY, timeout=(5, 15))
        if r_grid.status_code != 200:
            _mark_offline(f"Bakanlık stok listesi servisi yanıt vermiyor (HTTP {r_grid.status_code}).")
            return
        try:
            grid_json = r_grid.json()
        except Exception:
            _mark_offline("Bakanlık stok listesi geçersiz yanıt döndürdü (oturum düşmüş olabilir).")
            return
        gtin_list = grid_json.get("Data", []) if isinstance(grid_json, dict) else []

        # 5) Detaylar
        all_rows = []
        detail_errors = 0
        for g_item in gtin_list:
            gtin_code = g_item.get("BARKOD")
            prod_name = g_item.get("URUNADI")
            if not gtin_code:
                continue
            try:
                session.post("https://bkst.tarbil.gov.tr/Main/GetViewReport",
                             data={"gtin": gtin_code, "gln": gln_guid, "__RequestVerificationToken": token2},
                             verify=SSL_VERIFY, timeout=(5, 10))
                r_detail = session.post("https://bkst.tarbil.gov.tr/Main/GetStockDetailList",
                                        data={"CompanyAddressId": gln_guid, "Gtin": gtin_code, "__RequestVerificationToken": token2},
                                        verify=SSL_VERIFY, timeout=(5, 15))
            except Exception:
                detail_errors += 1
                continue
            if r_detail.status_code != 200:
                detail_errors += 1
                continue
            try:
                d_items = r_detail.json()
            except Exception:
                detail_errors += 1
                continue
            if isinstance(d_items, list):
                for item in d_items:
                    koli = item.get("PAKETNO") or item.get("KOLINO") or item.get("PALETNO") or ""
                    all_rows.append({
                        "Koli Numarası": koli,
                        "Ürün Adı": prod_name or item.get("URUNADI") or "",
                        "Karekod": item.get("KAREKOD") or item.get("HAMKAREKOD") or "",
                        "Gtin Numarası": item.get("BARKOD") or gtin_code or "",
                        "Gtin / Barkod": item.get("BARKOD") or gtin_code or "",
                        "gtin": item.get("BARKOD") or gtin_code or "",
                        "Seri Numarası": item.get("SERINO") or "",
                        "Parti Numarası": item.get("SARJNO") or "",
                        "Palet Numarası": item.get("PALETNO") or "",
                        "Üretim Tarihi": item.get("URETIMTARIHI") or "",
                        "Son Kullanma Tarihi": item.get("SKT") or ""
                    })

        fetched = len(all_rows)
        if fetched == 0:
            # 0 adet veri = Bakanlık tarafında sorun. Yerel veriyi SİLMEYİZ.
            if not gtin_list:
                _mark_offline("Bakanlık'tan 0 adet stok verisi geldi (stok listesi boş döndü).")
            else:
                _mark_offline(f"Bakanlık'tan 0 adet karekod detayı alınabildi ({detail_errors} istek başarısız).")
            return

        try:
            save_bkst_data_to_db(pd.DataFrame(all_rows), username)
        except Exception as db_err:
            logger.error(f"Error saving to db, attempting auto-repair: {db_err}", exc_info=True)
            try:
                ensure_db_schema()
                save_bkst_data_to_db(pd.DataFrame(all_rows), username)
            except Exception as retry_err:
                logger.error(f"Failed to save to db after repair: {retry_err}", exc_info=True)
                _mark_offline(f"Bakanlık verileri çekildi ancak veritabanı kayıt hatası oluştu ({type(retry_err).__name__}).")
                return

        with _state_lock:
            _app_bkst_synced = True
        msg = f"🟢 SİSTEM AKTİF: Bakanlık'tan {fetched} adet stok verisi başarıyla çekildi."
        if detail_errors:
            msg += f" ({detail_errors} ürün detayı alınamadı.)"
        _set_bkst_state("done", msg, True, fetched)
        logger.info(f"BKST fetch_api worker finished: fetched={fetched}, detail_errors={detail_errors}")

    except Exception as e:
        logger.error(f"BKST fetch_api worker error: {e}", exc_info=True)
        _mark_offline(f"Bakanlık bağlantı hatası ({type(e).__name__}).")


@app.route('/api/bkst/fetch_api', methods=['POST'])
def bkst_fetch_api_start():
    global _fetch_future, bkst_status, bkst_message
    with _state_lock:
        if _fetch_future and not _fetch_future.done():
            return jsonify({"success": True, "status": "already_running",
                            "message": "Zaten devam eden bir veri çekme işlemi var, sonucu bekleniyor."})
        bkst_status = "fetching"
        bkst_message = "⚡ Bakanlık verileri API üzerinden çekiliyor..."
        _fetch_future = _fetch_executor.submit(_do_fetch_api_worker)
    return jsonify({"success": True, "status": "started", "message": "Veri çekme işlemi başlatıldı."})


def _bkst_state_payload():
    with _state_lock:
        running = bool(_fetch_future and not _fetch_future.done())
        return {
            "running": running,
            "status": bkst_status,
            "message": bkst_message,
            "online": bkst_online,
            "fetched_count": bkst_fetched_count,
        }


@app.route('/api/bkst/fetch_status', methods=['GET'])
def bkst_fetch_status():
    payload = _bkst_state_payload()
    payload["local_count"] = _local_item_count_cached()
    return jsonify(payload)

@app.route('/api/bkst/download_api_data', methods=['GET'])
@app.route('/api/bkst/download', methods=['GET'])
def download_api_data():
    df, _, _, _ = get_bkst_cache()
    if df is None or df.empty:
        return jsonify({"error": "Henüz stok verisi çekilmedi."}), 404
        
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Tüm Ürünler (QR)")
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name="bkst_depo_verileri_guncel.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ── SİSTEMDEN ÇIKIŞ KABUL VE İŞLEMLERİ ────────────────────────────────────────
@app.route('/api/depo_stoklari', methods=['GET'])
def api_depo_stoklari():
    try:
        df, _, _, _ = get_bkst_cache()
        rows = []
        if df is not None and not df.empty:
            df_clean = df.fillna("")
            for col in df_clean.columns:
                df_clean[col] = df_clean[col].astype(str)
            raw_rows = df_clean.to_dict(orient="records")
            for r in raw_rows:
                gtin_val = r.get('Gtin Numarası') or r.get('Gtin / Barkod') or r.get('gtin') or r.get('GTIN') or r.get('BARCODE') or r.get('barkod') or ''
                karekod_str = r.get('Karekod') or r.get('tam_karekod') or r.get('ham_karekod') or r.get('KAREKOD') or ''
                if (not gtin_val or gtin_val in ('-', 'None', 'nan', 'null', 'BELİRSİZ')) and karekod_str:
                    parsed = parse_gs1_qr(karekod_str)
                    if parsed and parsed.get('gtin'):
                        gtin_val = str(parsed['gtin'])
                    elif karekod_str.startswith('01') and len(karekod_str) >= 16:
                        gtin_val = karekod_str[2:16]
                    else:
                        m14 = re.findall(r'\d{14}', karekod_str)
                        if m14:
                            gtin_val = m14[0]
                r['Gtin Numarası'] = gtin_val
                r['Gtin / Barkod'] = gtin_val
                r['gtin'] = gtin_val
                r['GTIN'] = gtin_val
                rows.append(r)

        total_count = len(rows)
        page_param = request.args.get('page')
        size_param = request.args.get('size')

        if page_param is not None or size_param is not None:
            try:
                page = max(1, int(page_param or 1))
            except Exception:
                page = 1
            try:
                size = min(2000, max(1, int(size_param or 500)))
            except Exception:
                size = 500
            start_idx = (page - 1) * size
            end_idx = start_idx + size
            paged_products = rows[start_idx:end_idx]
            has_more = end_idx < total_count
            return jsonify({
                "success": True,
                "products": paged_products,
                "total": total_count,
                "page": page,
                "size": size,
                "has_more": has_more
            })

        return jsonify({
            "success": True,
            "products": rows,
            "total": total_count,
            "page": 1,
            "size": total_count,
            "has_more": False
        })
    except Exception as e:
        logger.error(f"api_depo_stoklari error: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "products": [],
            "total": 0,
            "page": 1,
            "size": 0,
            "has_more": False,
            "error": str(e)
        })

@app.route('/api/cikis/okut', methods=['POST'])
def cikis_okut():
    try:
        data = request.json or {}
        barkod_raw = data.get('barkod', '').strip()
        if not barkod_raw:
            return jsonify({'success': False, 'error': 'Barkod boş olamaz.'})

        barkod_norm = normalize_qr(barkod_raw)
        username, _, _, _ = read_bkst_credentials()

        df, qr_map, gtin_map, koli_map = get_bkst_cache()
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'error': 'Bakanlık depo verisi bulunamadı. Lütfen önce verileri çekin.'
            })

        # Koli / Palet toplu okutma kontrolü
        koli_key = barkod_norm.upper()
        if koli_map and (koli_key in koli_map or barkod_norm in koli_map):
            koli_items = koli_map.get(koli_key) or koli_map.get(barkod_norm) or []
            if koli_items:
                conn = sqlite3.connect(DB_PATH, timeout=30.0)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                c = conn.cursor()
                if username:
                    c.execute('SELECT ham_karekod FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ""', (username,))
                else:
                    c.execute('SELECT ham_karekod FROM cikis_kayitlari')
                existing_set = set((row[0] or "").casefold() for row in c.fetchall() if row[0])

                tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                insert_rows = []
                inserted_records = []
                last_inserted = None
                has_any_tekrar = False

                for r_item in koli_items:
                    item_qr = normalize_qr(str(r_item.get("Karekod", "")))
                    if not item_qr:
                        continue
                    item_is_tekrar = item_qr.casefold() in existing_set
                    if item_is_tekrar:
                        has_any_tekrar = True
                    existing_set.add(item_qr.casefold())

                    u_adi = str(r_item.get('Ürün Adı', '')).strip()
                    b_col = str(r_item.get('Gtin Numarası') or r_item.get('Gtin / Barkod') or r_item.get('gtin') or '').strip()
                    k_no  = str(r_item.get('Koli Numarası', '')).strip()
                    s_no  = str(r_item.get('Seri Numarası', '')).strip()
                    p_no  = str(r_item.get('Parti Numarası', '')).strip()
                    pal_no = str(r_item.get('Palet Numarası', '')).strip()
                    ur_t  = str(r_item.get('Üretim Tarihi', '')).strip()
                    sk_t  = str(r_item.get('Son Kullanma Tarihi', '')).strip()

                    insert_rows.append((tarih, u_adi, b_col, k_no, s_no, p_no, pal_no, ur_t, sk_t, item_qr, 1 if item_is_tekrar else 0, username))
                    rec = {
                        'tarih': tarih, 'urun_adi': u_adi, 'barkod': b_col, 'koli_no': k_no,
                        'seri_no': s_no, 'parti_no': p_no, 'palet_no': pal_no, 'uretim_tarihi': ur_t,
                        'skt': sk_t, 'ham_karekod': item_qr, 'tekrar_uyari': 1 if item_is_tekrar else 0
                    }
                    inserted_records.append(rec)
                    last_inserted = rec

                if insert_rows:
                    c.executemany('''INSERT INTO cikis_kayitlari
                        (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                         uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', insert_rows)

                    c.execute("SELECT max(id) FROM cikis_kayitlari")
                    max_id = c.fetchone()[0] or len(insert_rows)
                    first_id = max_id - len(insert_rows) + 1
                    for idx, r_rec in enumerate(inserted_records):
                        r_rec['id'] = first_id + idx

                    # Kalıcı satış ve istatistik arşivine de ekle
                    c.executemany('''INSERT INTO satis_arsivi
                        (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                         uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, durum)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CIKIS_YAPILDI')''',
                        insert_rows)
                    conn.commit()
                conn.close()

                return jsonify({
                    'success': True,
                    'is_bulk': True,
                    'tekrar_uyari': has_any_tekrar,
                    'count': len(insert_rows),
                    'message': f'{koli_key} kolisindeki {len(insert_rows)} adet ürün başarıyla çıkış yapıldı.',
                    'kayit': last_inserted,
                    'kayitlar': inserted_records
                })

        # Parti Numarası kontrolü: Parti numarası tekil kutuyu değil tüm partiyi temsil eder
        is_parti, p_count, p_urun = check_is_parti_no(barkod_norm, df)
        if is_parti:
            return jsonify({
                'success': False,
                'is_parti_no': True,
                'error': f'"{barkod_raw}" bir Parti Numarasıdır ({p_urun} - Depoda bu partiye ait {p_count} adet ürün var). Parti numarası üretimdeki bir grubu temsil eder ve tekil bir kutuya ait değildir. Çıkış yapabilmek için lütfen kutu üzerindeki Karekodu veya Seri Numarasını okutunuz.'
            })

        # GTIN Barkod kontrolü: GTIN tekil kutuyu değil genel ürün tanımını temsil eder
        is_gtin, g_count, g_urun = check_is_gtin_no(barkod_norm, df, gtin_map)
        if is_gtin:
            return jsonify({
                'success': False,
                'is_gtin_no': True,
                'error': f'"{barkod_raw}" bir GTIN / Çizgi Barkod numarasıdır ({g_urun} - Depoda bu barkoda ait {g_count} adet ürün var). Bu numara tekil bir kutuya ait karekod değildir. Çıkış yapabilmek için lütfen kutu üzerindeki Karekodu (DataMatrix) veya Seri Numarasını okutunuz.'
            })

        # Tekil ürün kontrolü: Önce ürünü Bakanlık deposundan tam eşleştir
        match_row = resolve_product_from_cache(barkod_norm, df, qr_map, gtin_map)
        if match_row is None:
            return jsonify({
                'success': False,
                'error': f'"{barkod_raw}" barkoduna / seri numarasına ait ürün Bakanlık depo verisinde bulunamadı.'
            })

        tarih         = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        urun_adi      = str(match_row.get('Ürün Adı', '')).strip()
        barkod_col    = str(match_row.get('Gtin Numarası') or match_row.get('Gtin / Barkod') or match_row.get('gtin') or match_row.get('BARKOD') or match_row.get('barkod') or '').strip()
        real_karekod  = str(match_row.get('Karekod') or '').strip() or barkod_norm
        koli_no       = str(match_row.get('Koli Numarası', '')).strip()
        seri_no       = str(match_row.get('Seri Numarası', '')).strip()
        parti_no      = str(match_row.get('Parti Numarası', '')).strip()
        palet_no      = str(match_row.get('Palet Numarası', '')).strip()
        uretim_tarihi = str(match_row.get('Üretim Tarihi', '')).strip()
        skt           = str(match_row.get('Son Kullanma Tarihi', '')).strip()

        if not barkod_col and real_karekod:
            parsed = parse_gs1_qr(real_karekod)
            if parsed and parsed.get('gtin'):
                barkod_col = str(parsed['gtin']).strip()
        if not seri_no and real_karekod:
            parsed = parse_gs1_qr(real_karekod)
            if parsed and parsed.get('seri_no'):
                seri_no = str(parsed['seri_no']).strip()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()

        # Çıkış kayıtlarında bu ürünün daha önce çıkış yapılıp yapılmadığını denetle:
        # 1) Gerçek tam karekod ile eşleşme
        # 2) Okutulan ham değer ile eşleşme
        # 3) Aynı Seri Numarası + GTIN ile eşleşme (kullanıcı ister karekod ister seri no okutmuş olsun yakalar)
        if username:
            c.execute('''
                SELECT id, tarih, urun_adi, ham_karekod, seri_no
                FROM (
                    SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod FROM cikis_kayitlari
                    UNION ALL
                    SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod FROM satis_arsivi
                )
                WHERE (kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")
                  AND (
                      LOWER(ham_karekod) = LOWER(?)
                      OR LOWER(ham_karekod) = LOWER(?)
                      OR (
                          seri_no IS NOT NULL AND seri_no != ""
                          AND LOWER(seri_no) = LOWER(?)
                          AND (? = "" OR barkod = ? OR barkod IS NULL OR barkod = "")
                      )
                  )
                ORDER BY id ASC LIMIT 1
            ''', (username, real_karekod, barkod_norm, seri_no, barkod_col, barkod_col))
        else:
            c.execute('''
                SELECT id, tarih, urun_adi, ham_karekod, seri_no
                FROM (
                    SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod FROM cikis_kayitlari
                    UNION ALL
                    SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod FROM satis_arsivi
                )
                WHERE (
                    LOWER(ham_karekod) = LOWER(?)
                    OR LOWER(ham_karekod) = LOWER(?)
                    OR (
                        seri_no IS NOT NULL AND seri_no != ""
                        AND LOWER(seri_no) = LOWER(?)
                        AND (? = "" OR barkod = ? OR barkod IS NULL OR barkod = "")
                    )
                )
                ORDER BY id ASC LIMIT 1
            ''', (real_karekod, barkod_norm, seri_no, barkod_col, barkod_col))

        existing = c.fetchone()
        is_tekrar = False
        ex_tarih = ""
        if existing:
            is_tekrar = True
            ex_tarih = existing[1] or ""

        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        # Karekod sütununa kullanıcının girdiği seri no yerine ürünün GERÇEK TAM KAREKODU kaydedilir
        c.execute('''INSERT INTO cikis_kayitlari
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username))
        new_id = c.lastrowid

        # Kalıcı satış & istatistik arşivine de ekle
        c.execute('''INSERT INTO satis_arsivi
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, durum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CIKIS_YAPILDI')''',
            (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username))
        conn.commit()
        conn.close()

        res_obj = {
            'success': True,
            'tekrar_uyari': is_tekrar,
            'kayit': {
                'id': new_id,
                'tarih': tarih,
                'urun_adi': urun_adi,
                'barkod': barkod_col,
                'koli_no': koli_no,
                'seri_no': seri_no,
                'parti_no': parti_no,
                'palet_no': palet_no,
                'uretim_tarihi': uretim_tarihi,
                'skt': skt,
                'ham_karekod': real_karekod,
                'tekrar_uyari': 1 if is_tekrar else 0
            }
        }
        if is_tekrar:
            res_obj['warning'] = f'Bu ürün daha önce depodan çıkarılmış! (Önceki çıkış tarihi: {ex_tarih})'
        return jsonify(res_obj)

    except Exception as e:
        logger.error(f"cikis_okut error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'Kayıt sırasında hata: {str(e)}'})

@app.route('/api/cikis/toplu_ekle', methods=['POST'])
def cikis_toplu_ekle():
    try:
        data = request.json or {}
        items = data.get('items', [])
        if not items or not isinstance(items, list):
            return jsonify({'success': False, 'error': 'Aktarılacak karekod listesi bulunamadı.'})

        df, qr_map, gtin_map, koli_map = get_bkst_cache()
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        c = conn.cursor()

        if username:
            c.execute('SELECT ham_karekod, seri_no, barkod FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ""', (username,))
        else:
            c.execute('SELECT ham_karekod, seri_no, barkod FROM cikis_kayitlari')
        existing_rows = c.fetchall()
        existing_qr_set = set((row[0] or "").casefold() for row in existing_rows if row[0])
        existing_seri_set = set((row[1] or "").casefold() for row in existing_rows if row[1])

        insert_rows = []
        added_count = 0
        already_count = 0
        tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for barkod_raw in items:
            raw_str = str(barkod_raw).strip()
            if not raw_str:
                continue

            # Parti Numarası tekil ürün olarak listeye eklenmemelidir
            is_parti, _, _ = check_is_parti_no(raw_str, df)
            if is_parti:
                logger.warning(f"cikis_toplu_ekle: '{raw_str}' parti numarası olduğu için tekil çıkışa eklenmedi.")
                continue

            # GTIN / Barkod tekil ürün olarak listeye eklenmemelidir
            is_gtin, _, _ = check_is_gtin_no(raw_str, df, gtin_map)
            if is_gtin:
                logger.warning(f"cikis_toplu_ekle: '{raw_str}' GTIN numarası olduğu için tekil çıkışa eklenmedi.")
                continue

            match_row = resolve_product_from_cache(raw_str, df, qr_map, gtin_map)
            if match_row:
                real_karekod = str(match_row.get('Karekod') or '').strip() or normalize_qr(raw_str)
                urun_adi = str(match_row.get('Ürün Adı', '')).strip()
                barkod_col = str(match_row.get('Gtin Numarası') or match_row.get('Gtin / Barkod') or match_row.get('gtin') or '').strip()
                koli_no = str(match_row.get('Koli Numarası', '')).strip()
                seri_no = str(match_row.get('Seri Numarası', '')).strip()
                parti_no = str(match_row.get('Parti Numarası', '')).strip()
                palet_no = str(match_row.get('Palet Numarası', '')).strip()
                uretim_tarihi = str(match_row.get('Üretim Tarihi', '')).strip()
                skt = str(match_row.get('Son Kullanma Tarihi', '')).strip()
            else:
                real_karekod = normalize_qr(raw_str)
                parsed = parse_gs1_qr(real_karekod)
                urun_adi = "Tanımsız Ürün"
                barkod_col = str(parsed.get('gtin') or '').strip()
                koli_no = ""
                seri_no = str(parsed.get('seri_no') or '').strip()
                parti_no = str(parsed.get('parti_no') or '').strip()
                palet_no = ""
                uretim_tarihi = str(parsed.get('uretim_tarihi') or '').strip()
                skt = str(parsed.get('skt') or '').strip()

            is_tekrar = (real_karekod.casefold() in existing_qr_set) or (seri_no and seri_no.casefold() in existing_seri_set)
            if is_tekrar:
                already_count += 1

            existing_qr_set.add(real_karekod.casefold())
            if seri_no:
                existing_seri_set.add(seri_no.casefold())

            insert_rows.append((tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
                                uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username))
            added_count += 1

        if insert_rows:
            c.executemany('''INSERT INTO cikis_kayitlari
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', insert_rows)
            # Kalıcı satış & istatistik arşivine de ekle
            c.executemany('''INSERT INTO satis_arsivi
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                insert_rows)
            conn.commit()

        conn.close()

        return jsonify({
            'success': True,
            'added_count': added_count,
            'already_count': already_count,
            'message': f'{added_count} adet ürün Çıkış Listesine aktarıldı.'
        })
    except Exception as e:
        logger.error(f"cikis_toplu_ekle error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'added_count': 0,
            'already_count': 0,
            'error': f'Toplu ekleme hatası: {str(e)}'
        })

@app.route('/api/cikis/listesi', methods=['GET'])
def cikis_listesi_api():
    try:
        username, _, _, _ = read_bkst_credentials()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if username:
            c.execute('SELECT * FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "" ORDER BY id DESC', (username,))
        else:
            c.execute('SELECT * FROM cikis_kayitlari ORDER BY id DESC')
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        clean_rows = []
        for row in rows:
            clean_row = {}
            for k, v in row.items():
                if k == 'tekrar_uyari':
                    clean_row['tekrar_uyari'] = 1 if (v in (1, '1', True)) else 0
                else:
                    clean_row[k] = "" if v is None else str(v)
            if not clean_row.get('barkod') and clean_row.get('ham_karekod'):
                parsed = parse_gs1_qr(clean_row['ham_karekod'])
                if parsed and parsed.get('gtin'):
                    clean_row['barkod'] = str(parsed['gtin'])
            clean_row['gtin'] = clean_row.get('barkod', '')
            clean_rows.append(clean_row)

        return jsonify({'success': True, 'kayitlar': clean_rows, 'toplam': len(clean_rows)})
    except Exception as e:
        logger.error(f"cikis_listesi_api error: {e}", exc_info=True)
        return jsonify({'success': False, 'kayitlar': [], 'toplam': 0, 'error': str(e)})

@app.route('/api/cikis/gruplanmis_urunler', methods=['GET'])
def cikis_gruplanmis_urunler():
    """
    cikis_kayitlari tablosundaki kayıtları ürün adına göre gruplayarak döndürür.
    Kullanıcıya satış ekranındaki 'Çıkış Listesinden Çıkış Yap' modunda kullanılır.
    """
    try:
        username, _, _, _ = read_bkst_credentials()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if username:
            c.execute('SELECT * FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "" ORDER BY id DESC', (username,))
        else:
            c.execute('SELECT * FROM cikis_kayitlari ORDER BY id DESC')
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        groups = {}
        for r in rows:
            u_adi = (r.get('urun_adi') or 'Tanımsız Ürün').strip()
            if u_adi not in groups:
                groups[u_adi] = {
                    'urun_adi': u_adi,
                    'gtin': r.get('barkod') or '',
                    'toplam_adet': 0,
                    'items': []
                }
            if not groups[u_adi]['gtin'] and r.get('barkod'):
                groups[u_adi]['gtin'] = r.get('barkod')

            groups[u_adi]['toplam_adet'] += 1
            groups[u_adi]['items'].append({
                'id': r.get('id'),
                'seri_no': r.get('seri_no') or '',
                'parti_no': r.get('parti_no') or '',
                'koli_no': r.get('koli_no') or '',
                'skt': r.get('skt') or '',
                'barkod': r.get('barkod') or '',
                'ham_karekod': r.get('ham_karekod') or ''
            })

        grouped_list = list(groups.values())
        grouped_list.sort(key=lambda x: x['urun_adi'])

        return jsonify({
            'success': True,
            'toplam_kayit': len(rows),
            'toplam_urun_cesidi': len(grouped_list),
            'gruplar': grouped_list
        })
    except Exception as e:
        logger.error(f"cikis_gruplanmis_urunler error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'gruplar': []})

@app.route('/api/cikis/sil/<int:kayit_id>', methods=['DELETE'])
def cikis_sil(kayit_id):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute('DELETE FROM cikis_kayitlari WHERE id = ?', (kayit_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"cikis_sil error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cikis/temizle', methods=['POST'])
def cikis_temizle():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute('DELETE FROM cikis_kayitlari')
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'mesaj': 'Tüm çıkış kayıtları silindi.'})
    except Exception as e:
        logger.error(f"cikis_temizle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cikis/indir', methods=['GET'])
def cikis_indir():
    username, _, _, _ = read_bkst_credentials()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    if username:
        df = pd.read_sql_query('SELECT * FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "" ORDER BY id DESC', conn, params=(username,))
    else:
        df = pd.read_sql_query('SELECT * FROM cikis_kayitlari ORDER BY id DESC', conn)
    conn.close()

    if 'kullanici_adi' in df.columns:
        df = df.drop(columns=['kullanici_adi'])

    if 'barkod' in df.columns and 'ham_karekod' in df.columns:
        for idx, row in df.iterrows():
            b_val = str(row.get('barkod') or '').strip()
            if not b_val and pd.notna(row.get('ham_karekod')):
                parsed = parse_gs1_qr(str(row['ham_karekod']))
                if parsed and parsed.get('gtin'):
                    df.at[idx, 'barkod'] = str(parsed['gtin'])

    df = df.rename(columns={
        'id':            'ID',
        'tarih':         'Tarih/Saat',
        'urun_adi':      'Ürün Adı',
        'barkod':        'Gtin/Barkod',
        'koli_no':       'Koli Numarası',
        'seri_no':       'Seri Numarası',
        'parti_no':      'Parti Numarası',
        'palet_no':      'Palet Numarası',
        'uretim_tarihi': 'Üretim Tarihi',
        'skt':           'Son Kullanma Tarihi',
        'ham_karekod':   'Tam Karekod',
        'tekrar_uyari':  'Tekrar Okutuldu'
    })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sistemden Çıkışlar')
    output.seek(0)

    fname = f'cikis_listesi_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return send_file(output, as_attachment=True, download_name=fname,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ── İSTATİSTİKLER & SATIŞ RAPORLARI API ──────────────────────────────────────
@app.route('/api/istatistikler/ozet', methods=['GET'])
def api_istatistikler_ozet():
    try:
        yil = request.args.get('yil', 'tum').strip()
        baslangic = request.args.get('baslangic', '').strip()
        bitis = request.args.get('bitis', '').strip()
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Mevcut tüm yılları topla
        c.execute("""
            SELECT DISTINCT substr(tarih, 1, 4) as yr
            FROM satis_arsivi
            WHERE tarih IS NOT NULL AND length(tarih) >= 4 AND substr(tarih, 1, 4) GLOB '[1-2][0-9][0-9][0-9]'
            ORDER BY yr DESC
        """)
        db_years = [r['yr'] for r in c.fetchall() if r['yr']]
        default_years = ['2026', '2025', '2024', '2023']
        all_years = sorted(list(set(db_years + default_years)), reverse=True)

        where_parts = []
        params = []
        if username:
            where_parts.append('(kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")')
            params.append(username)
        if yil and yil != 'tum':
            where_parts.append("substr(tarih, 1, 4) = ?")
            params.append(yil)
        if baslangic:
            where_parts.append("substr(tarih, 1, 10) >= ?")
            params.append(baslangic)
        if bitis:
            where_parts.append("substr(tarih, 1, 10) <= ?")
            params.append(bitis)

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # KPI Özeti
        c.execute(f"""
            SELECT 
                COUNT(*) as toplam_adet,
                COUNT(DISTINCT urun_adi) as tekil_urun,
                COUNT(CASE WHEN tekrar_uyari = 1 THEN 1 END) as tekrar_adet,
                COUNT(CASE WHEN koli_no IS NOT NULL AND koli_no != '' THEN 1 END) as koli_adet,
                COUNT(CASE WHEN palet_no IS NOT NULL AND palet_no != '' THEN 1 END) as palet_adet,
                COUNT(DISTINCT substr(tarih, 1, 10)) as aktif_gun,
                MIN(tarih) as ilk_tarih,
                MAX(tarih) as son_tarih
            FROM satis_arsivi
            {where_sql}
        """, params)
        kpi_raw = dict(c.fetchone() or {})
        toplam_adet = kpi_raw.get('toplam_adet') or 0
        tekil_urun = kpi_raw.get('tekil_urun') or 0
        tekrar_adet = kpi_raw.get('tekrar_adet') or 0
        koli_adet = kpi_raw.get('koli_adet') or 0
        palet_adet = kpi_raw.get('palet_adet') or 0
        tekil_adet = max(0, toplam_adet - koli_adet - palet_adet)
        aktif_gun = kpi_raw.get('aktif_gun') or 0
        gunluk_ort = round(toplam_adet / max(1, aktif_gun), 1) if aktif_gun else 0
        tekrar_orani = round((tekrar_adet / max(1, toplam_adet)) * 100, 1)

        # Lider Ürün
        c.execute(f"""
            SELECT urun_adi, COUNT(*) as adet
            FROM satis_arsivi
            {where_sql}
            GROUP BY urun_adi
            ORDER BY adet DESC
            LIMIT 1
        """, params)
        lider_row = c.fetchone()
        lider_urun = lider_row['urun_adi'] if lider_row else "Veri Yok"
        lider_adet = lider_row['adet'] if lider_row else 0

        # Aylık Dağılım (12 Ay: Ocak - Aralık)
        ay_isimleri = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        aylik_sayilar = [0] * 12
        c.execute(f"""
            SELECT substr(tarih, 6, 2) as ay_no, COUNT(*) as adet
            FROM satis_arsivi
            {where_sql}
            GROUP BY ay_no
            ORDER BY ay_no ASC
        """, params)
        for r in c.fetchall():
            ay_str = r['ay_no']
            if ay_str and ay_str.isdigit():
                idx = int(ay_str) - 1
                if 0 <= idx < 12:
                    aylik_sayilar[idx] = r['adet']

        # Yıllık Karşılaştırma (Tüm Yıllar)
        u_filter = "WHERE (kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = '')" if username else ""
        u_p = [username] if username else []
        c.execute(f"""
            SELECT substr(tarih, 1, 4) as yr, COUNT(*) as adet
            FROM satis_arsivi
            {u_filter}
            GROUP BY yr
            HAVING yr IS NOT NULL AND length(yr) = 4 AND yr GLOB '[1-2][0-9][0-9][0-9]'
            ORDER BY yr ASC
        """, u_p)
        yillik_dict = {str(y): 0 for y in all_years}
        for r in c.fetchall():
            yillik_dict[str(r['yr'])] = r['adet']
        yillik_labels = sorted(yillik_dict.keys())
        yillik_values = [yillik_dict[y] for y in yillik_labels]

        # En Çok Satan Ürünler (Top 30)
        c.execute(f"""
            SELECT 
                urun_adi, 
                COALESCE(barkod, '') as barkod,
                COUNT(*) as adet,
                COUNT(CASE WHEN koli_no != '' THEN 1 END) as koli_sayisi,
                COUNT(CASE WHEN palet_no != '' THEN 1 END) as palet_sayisi,
                MAX(tarih) as son_cikis
            FROM satis_arsivi
            {where_sql}
            GROUP BY urun_adi, barkod
            ORDER BY adet DESC
            LIMIT 30
        """, params)
        top_urunler = []
        for r in c.fetchall():
            ad = r['adet']
            pct = round((ad / max(1, toplam_adet)) * 100, 1)
            top_urunler.append({
                'urun_adi': r['urun_adi'] or 'İsimsiz Ürün',
                'barkod': r['barkod'] or '-',
                'adet': ad,
                'koli_sayisi': r['koli_sayisi'],
                'palet_sayisi': r['palet_sayisi'],
                'yuzde': pct,
                'son_cikis': r['son_cikis'] or ''
            })

        # Haftanın Günleri
        gunluk_dagilim = [0] * 7
        c.execute(f"""
            SELECT strftime('%w', tarih) as gun_no, COUNT(*) as adet
            FROM satis_arsivi
            {where_sql}
            GROUP BY gun_no
        """, params)
        for r in c.fetchall():
            g = r['gun_no']
            if g is not None and str(g).isdigit():
                idx = int(g)
                if 0 <= idx < 7:
                    gunluk_dagilim[idx] = r['adet']
        hafta_gunleri = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
        hafta_degerleri = gunluk_dagilim[1:] + gunluk_dagilim[:1]

        # Parti Dağılımı
        parti_where = (where_sql + " AND parti_no IS NOT NULL AND parti_no != ''") if where_sql else "WHERE parti_no IS NOT NULL AND parti_no != ''"
        c.execute(f"""
            SELECT 
                COALESCE(parti_no, 'Belirtilmemiş') as parti,
                urun_adi,
                COUNT(*) as adet,
                MAX(skt) as skt
            FROM satis_arsivi
            {parti_where}
            GROUP BY parti, urun_adi
            ORDER BY adet DESC
            LIMIT 20
        """, params)
        top_partiler = [dict(r) for r in c.fetchall()]

        # Depo Stoğu Karşılaştırması
        c.execute("SELECT COUNT(*) as depo_toplam, COUNT(DISTINCT urun_adi) as depo_kalem FROM bkst_depo_verileri")
        depo_row = dict(c.fetchone() or {})

        conn.close()

        return jsonify({
            'success': True,
            'filtre': {
                'yil': yil,
                'baslangic': baslangic,
                'bitis': bitis
            },
            'mevcut_yillar': all_years,
            'kpi': {
                'toplam_cikis': toplam_adet,
                'tekil_urun_sayisi': tekil_urun,
                'lider_urun': lider_urun,
                'lider_adet': lider_adet,
                'koli_adet': koli_adet,
                'palet_adet': palet_adet,
                'tekil_adet': tekil_adet,
                'koli_orani': round((koli_adet / max(1, toplam_adet)) * 100, 1),
                'tekil_orani': round((tekil_adet / max(1, toplam_adet)) * 100, 1),
                'gunluk_ortalama': gunluk_ort,
                'aktif_gun': aktif_gun,
                'tekrar_adet': tekrar_adet,
                'tekrar_orani': tekrar_orani,
                'depo_mevcut_stok': depo_row.get('depo_toplam', 0),
                'depo_kalem_sayisi': depo_row.get('depo_kalem', 0)
            },
            'aylik_grafik': {
                'etiketler': ay_isimleri,
                'veriler': aylik_sayilar
            },
            'yillik_grafik': {
                'etiketler': yillik_labels,
                'veriler': yillik_values
            },
            'haftalik_grafik': {
                'etiketler': hafta_gunleri,
                'veriler': hafta_degerleri
            },
            'en_cok_satanlar': top_urunler,
            'parti_dagilimi': top_partiler
        })
    except Exception as e:
        logger.error(f"api_istatistikler_ozet error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/excel_indir', methods=['GET'])
def api_istatistikler_excel_indir():
    try:
        yil = request.args.get('yil', 'tum').strip()
        baslangic = request.args.get('baslangic', '').strip()
        bitis = request.args.get('bitis', '').strip()
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        where_parts = []
        params = []
        if username:
            where_parts.append('(kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")')
            params.append(username)
        if yil and yil != 'tum':
            where_parts.append("substr(tarih, 1, 4) = ?")
            params.append(yil)
        if baslangic:
            where_parts.append("substr(tarih, 1, 10) >= ?")
            params.append(baslangic)
        if bitis:
            where_parts.append("substr(tarih, 1, 10) <= ?")
            params.append(bitis)

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # Fetch detail rows
        c.execute(f"""
            SELECT id, tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari
            FROM satis_arsivi
            {where_sql}
            ORDER BY id DESC
        """, params)
        detail_rows = [dict(r) for r in c.fetchall()]

        # Fetch top products
        c.execute(f"""
            SELECT urun_adi, COALESCE(barkod, '') as barkod, COUNT(*) as adet,
                   COUNT(CASE WHEN koli_no != '' THEN 1 END) as koli_adet,
                   COUNT(CASE WHEN koli_no = '' AND palet_no = '' THEN 1 END) as tekil_adet,
                   MAX(tarih) as son_cikis
            FROM satis_arsivi
            {where_sql}
            GROUP BY urun_adi, barkod
            ORDER BY adet DESC
        """, params)
        top_products = [dict(r) for r in c.fetchall()]

        # Monthly counts
        c.execute(f"""
            SELECT substr(tarih, 6, 2) as ay_no, COUNT(*) as adet
            FROM satis_arsivi
            {where_sql}
            GROUP BY ay_no
            ORDER BY ay_no ASC
        """, params)
        monthly_map = {r['ay_no']: r['adet'] for r in c.fetchall()}

        conn.close()

        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb = openpyxl.Workbook()
        
        # Style helpers
        font_title = Font(name='Segoe UI', size=14, bold=True, color='0F172A')
        font_sub = Font(name='Segoe UI', size=10, italic=True, color='64748B')
        font_hdr = Font(name='Segoe UI', size=10, bold=True, color='FFFFFF')
        font_bold = Font(name='Segoe UI', size=10, bold=True, color='0F172A')
        font_norm = Font(name='Segoe UI', size=10, color='1E293B')
        fill_hdr = PatternFill(start_color='1E293B', end_color='1E293B', fill_type='solid')
        fill_zebra = PatternFill(start_color='F8FAFC', end_color='F8FAFC', fill_type='solid')
        border_thin = Side(border_style='thin', color='CBD5E1')
        cell_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

        # SHEET 1: ÖZET RAPOR
        ws1 = wb.active
        ws1.title = "Genel Özet"
        ws1.views.sheetView[0].showGridLines = True
        ws1['A1'] = "QR COMPARE - SATIŞ VE ÇIKIŞ İSTATİSTİK RAPORU"
        ws1['A1'].font = font_title
        period_str = f"Filtre Dönemi: Yıl: {yil if yil != 'tum' else 'Tüm Yıllar'}"
        if baslangic or bitis:
            period_str += f" | {baslangic or 'İlk'} - {bitis or 'Son'}"
        ws1['A2'] = f"{period_str} | Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        ws1['A2'].font = font_sub

        total_cnt = len(detail_rows)
        kpi_data = [
            ("Toplam Çıkış Adedi (Kutu)", total_cnt),
            ("Çıkış Yapılan Kalem Sayısı", len(top_products)),
            ("Koli İle Çıkış Yapılan Adet", sum(r.get('koli_adet', 0) for r in top_products)),
            ("Tekil Kutu Çıkış Adedi", sum(r.get('tekil_adet', 0) for r in top_products)),
            ("En Çok Satan Ürün", top_products[0]['urun_adi'] if top_products else "-"),
            ("En Çok Satan Ürün Satış Adedi", top_products[0]['adet'] if top_products else 0),
        ]
        ws1.cell(row=4, column=1, value="METRİK").fill = fill_hdr
        ws1.cell(row=4, column=1).font = font_hdr
        ws1.cell(row=4, column=2, value="DEĞER").fill = fill_hdr
        ws1.cell(row=4, column=2).font = font_hdr

        for idx, (m_label, m_val) in enumerate(kpi_data, start=5):
            c1 = ws1.cell(row=idx, column=1, value=m_label)
            c2 = ws1.cell(row=idx, column=2, value=m_val)
            c1.font = font_bold
            c2.font = font_norm
            c1.border = cell_border
            c2.border = cell_border
            if idx % 2 == 1:
                c1.fill = fill_zebra
                c2.fill = fill_zebra

        ws1.column_dimensions['A'].width = 35
        ws1.column_dimensions['B'].width = 25

        # SHEET 2: ÜRÜN BAZLI SATIŞLAR
        ws2 = wb.create_sheet(title="Ürün Bazlı Satışlar")
        ws2.views.sheetView[0].showGridLines = True
        hdrs2 = ["Sıra", "Ürün Adı", "GTIN / Barkod", "Toplam Adet", "Pazar Payı (%)", "Koli Çıkışı", "Tekil Çıkış", "Son Çıkış Tarihi"]
        for c_idx, h_text in enumerate(hdrs2, start=1):
            cell = ws2.cell(row=1, column=c_idx, value=h_text)
            cell.font = font_hdr
            cell.fill = fill_hdr
            cell.alignment = Alignment(horizontal='center' if c_idx in [1, 4, 5, 6, 7] else 'left')

        for r_idx, p in enumerate(top_products, start=2):
            pct = round((p['adet'] / max(1, total_cnt)) * 100, 1)
            vals = [
                r_idx - 1,
                p['urun_adi'],
                p['barkod'],
                p['adet'],
                f"%{pct}",
                p.get('koli_adet', 0),
                p.get('tekil_adet', 0),
                p.get('son_cikis', '')
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws2.cell(row=r_idx, column=col_idx, value=val)
                cell.font = font_norm
                cell.border = cell_border
                if r_idx % 2 == 1:
                    cell.fill = fill_zebra

        ws2.column_dimensions['A'].width = 8
        ws2.column_dimensions['B'].width = 35
        ws2.column_dimensions['C'].width = 20
        ws2.column_dimensions['D'].width = 15
        ws2.column_dimensions['E'].width = 15
        ws2.column_dimensions['F'].width = 15
        ws2.column_dimensions['G'].width = 15
        ws2.column_dimensions['H'].width = 22

        # SHEET 3: AYLIK SATIŞ DAĞILIMI
        ws3 = wb.create_sheet(title="Aylık Dağılım")
        ws3.views.sheetView[0].showGridLines = True
        ws3.cell(row=1, column=1, value="Ay Numarası").fill = fill_hdr
        ws3.cell(row=1, column=1).font = font_hdr
        ws3.cell(row=1, column=2, value="Ay Adı").fill = fill_hdr
        ws3.cell(row=1, column=2).font = font_hdr
        ws3.cell(row=1, column=3, value="Satış Adedi (Kutu)").fill = fill_hdr
        ws3.cell(row=1, column=3).font = font_hdr
        ws3.cell(row=1, column=4, value="Yüzde Pay (%)").fill = fill_hdr
        ws3.cell(row=1, column=4).font = font_hdr

        ay_adlari = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        for m_idx in range(1, 13):
            m_str = f"{m_idx:02d}"
            cnt = monthly_map.get(m_str, 0)
            pct = round((cnt / max(1, total_cnt)) * 100, 1)
            row_num = m_idx + 1
            ws3.cell(row=row_num, column=1, value=m_idx).border = cell_border
            ws3.cell(row=row_num, column=2, value=ay_adlari[m_idx - 1]).border = cell_border
            ws3.cell(row=row_num, column=3, value=cnt).border = cell_border
            ws3.cell(row=row_num, column=4, value=f"%{pct}").border = cell_border
            if row_num % 2 == 1:
                for c_i in range(1, 5):
                    ws3.cell(row=row_num, column=c_i).fill = fill_zebra

        ws3.column_dimensions['A'].width = 14
        ws3.column_dimensions['B'].width = 18
        ws3.column_dimensions['C'].width = 22
        ws3.column_dimensions['D'].width = 16

        # SHEET 4: TÜM ÇIKIŞ KAYITLARI
        ws4 = wb.create_sheet(title="Ham Çıkış Kayıtları")
        ws4.views.sheetView[0].showGridLines = True
        hdrs4 = ["ID", "Tarih/Saat", "Ürün Adı", "GTIN/Barkod", "Seri No", "Parti No", "Koli No", "Palet No", "Üretim Tarihi", "SKT", "Tam Karekod", "Tekrar"]
        for c_idx, h_text in enumerate(hdrs4, start=1):
            cell = ws4.cell(row=1, column=c_idx, value=h_text)
            cell.font = font_hdr
            cell.fill = fill_hdr

        for r_idx, row in enumerate(detail_rows, start=2):
            vals = [
                row.get('id', ''),
                row.get('tarih', ''),
                row.get('urun_adi', ''),
                row.get('barkod', ''),
                row.get('seri_no', ''),
                row.get('parti_no', ''),
                row.get('koli_no', ''),
                row.get('palet_no', ''),
                row.get('uretim_tarihi', ''),
                row.get('skt', ''),
                row.get('ham_karekod', ''),
                'EVET' if row.get('tekrar_uyari') == 1 else 'HAYIR'
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws4.cell(row=r_idx, column=col_idx, value=val)
                cell.font = font_norm
                cell.border = cell_border

        for col_l in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L']:
            ws4.column_dimensions[col_l].width = 18
        ws4.column_dimensions['C'].width = 30
        ws4.column_dimensions['K'].width = 40

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"satis_istatistik_raporu_{yil}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        logger.error(f"api_istatistikler_excel_indir error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/ornek_gecmis_ekle', methods=['POST'])
def api_istatistikler_ornek_gecmis_ekle():
    try:
        username, _, _, _ = read_bkst_credentials()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()

        # Depodaki gerçek ürünlerden örnek al
        c.execute("SELECT DISTINCT gtin, urun_adi, parti_no, uretim_tarihi, skt FROM bkst_depo_verileri LIMIT 15")
        warehouse_prods = c.fetchall()

        if not warehouse_prods:
            warehouse_prods = [
                ("08699258170119", "Agnoround 20x1 LT", "A6868148", "05.08.2024", "05.08.2028"),
                ("08693814003187", "KORTAC 100 EC 1 LT", "K992011", "12.02.2024", "12.02.2027"),
                ("08681128520308", "EMALDA 1000 ML.", "EM88291", "10.04.2024", "10.04.2028"),
                ("08699514012014", "DORADO 500 SC 1 LT", "D77124", "01.06.2024", "01.06.2028"),
                ("08680123456789", "TEBUCONAZOLE 250 EW", "TB5521", "15.01.2024", "15.01.2027")
            ]

        demo_rows = []
        serial_counter = 500000

        for year in [2024, 2025]:
            for month in range(1, 13):
                monthly_count = random.randint(12, 32)
                for _ in range(monthly_count):
                    serial_counter += 1
                    prod = random.choice(warehouse_prods)
                    gtin, u_name, p_no, ur_t, sk_t = prod[0], prod[1], prod[2], prod[3], prod[4]
                    day = random.randint(1, 28)
                    hour = random.randint(8, 18)
                    minute = random.randint(0, 59)
                    second = random.randint(0, 59)
                    tarih = f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

                    is_koli = random.random() < 0.65
                    koli_no = f"004869{random.randint(1000000000, 9999999999)}" if is_koli else ""
                    seri_no = f"{serial_counter}"
                    tam_qr = f"DEMO_HISTORICAL_{gtin}_{seri_no}_{p_no}"

                    demo_rows.append((
                        tarih, u_name, gtin, koli_no, seri_no, p_no, "",
                        ur_t, sk_t, tam_qr, 0, username or "demo_gecmis"
                    ))

        c.executemany("""
            INSERT INTO satis_arsivi 
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, demo_rows)
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'2024 ve 2025 yıllarına ait toplam {len(demo_rows)} adet gerçekçi geçmiş satış kaydı başarıyla eklendi.',
            'eklenen_adet': len(demo_rows)
        })
    except Exception as e:
        logger.error(f"api_istatistikler_ornek_gecmis_ekle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/ornek_gecmis_temizle', methods=['POST'])
def api_istatistikler_ornek_gecmis_temizle():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM satis_arsivi WHERE ham_karekod LIKE 'DEMO_HISTORICAL_%'")
        deleted_cnt = c.rowcount
        conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'message': f'Örnek geçmiş satış verileri temizlendi ({deleted_cnt} kayıt silindi).',
            'silinen_adet': deleted_cnt
        })
    except Exception as e:
        logger.error(f"api_istatistikler_ornek_gecmis_temizle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/sifirla', methods=['POST'])
def api_istatistikler_sifirla():
    try:
        data = request.get_json(silent=True) or {}
        yil = str(data.get('yil', 'tum')).strip()
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()

        if yil and yil != 'tum':
            c.execute("DELETE FROM satis_arsivi WHERE substr(tarih, 1, 4) = ?", (yil,))
            msg = f"{yil} yılına ait tüm satış ve istatistik kayıtları başarıyla sıfırlandı."
        else:
            c.execute("DELETE FROM satis_arsivi")
            msg = "Kalıcı satış arşivi ve tüm geçmiş istatistik verileri başarıyla sıfırlandı."

        deleted_cnt = c.rowcount
        conn.commit()
        conn.close()

        logger.info(f"api_istatistikler_sifirla: {deleted_cnt} kayıt silindi (yil={yil})")
        return jsonify({
            'success': True,
            'message': msg,
            'silinen_adet': deleted_cnt
        })
    except Exception as e:
        logger.error(f"api_istatistikler_sifirla error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/excel_yukle', methods=['POST'])
def api_istatistikler_excel_yukle():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Lütfen bir Excel dosyası seçin.'})
        f = request.files['file']
        if not f.filename:
            return jsonify({'success': False, 'error': 'Dosya adı boş olamaz.'})

        username, _, _, _ = read_bkst_credentials()
        df = pd.read_excel(f)
        if df.empty:
            return jsonify({'success': False, 'error': 'Yüklenen dosya boş.'})

        col_map = {}
        for c in df.columns:
            c_str = str(c).strip().lower()
            if 'tarih' in c_str or 'date' in c_str: col_map['tarih'] = c
            elif 'ürün' in c_str or 'urun' in c_str or 'name' in c_str: col_map['urun_adi'] = c
            elif 'barkod' in c_str or 'gtin' in c_str or 'barcode' in c_str: col_map['barkod'] = c
            elif 'seri' in c_str or 'serial' in c_str: col_map['seri_no'] = c
            elif 'parti' in c_str or 'lot' in c_str: col_map['parti_no'] = c
            elif 'koli' in c_str: col_map['koli_no'] = c
            elif 'palet' in c_str: col_map['palet_no'] = c
            elif 'karekod' in c_str or 'qr' in c_str: col_map['ham_karekod'] = c

        if 'urun_adi' not in col_map:
            return jsonify({'success': False, 'error': "Excel dosyasında 'Ürün Adı' sütunu bulunamadı."})

        insert_rows = []
        for _, r in df.iterrows():
            t_val = str(r.get(col_map.get('tarih', ''), '')).strip() if 'tarih' in col_map else ''
            if not t_val or t_val in ('NaT', 'nan', 'None'):
                t_val = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            u_val = str(r.get(col_map['urun_adi'], '')).strip()
            if not u_val or u_val in ('nan', 'None'): continue

            b_val = str(r.get(col_map.get('barkod', ''), '')).strip() if 'barkod' in col_map else ''
            s_val = str(r.get(col_map.get('seri_no', ''), '')).strip() if 'seri_no' in col_map else ''
            p_val = str(r.get(col_map.get('parti_no', ''), '')).strip() if 'parti_no' in col_map else ''
            k_val = str(r.get(col_map.get('koli_no', ''), '')).strip() if 'koli_no' in col_map else ''
            pal_val = str(r.get(col_map.get('palet_no', ''), '')).strip() if 'palet_no' in col_map else ''
            qr_val = str(r.get(col_map.get('ham_karekod', ''), '')).strip() if 'ham_karekod' in col_map else f"IMPORT_{b_val}_{s_val}"

            insert_rows.append((
                t_val, u_val, b_val, k_val, s_val, p_val, pal_val,
                "", "", qr_val, 0, username or ""
            ))

        if not insert_rows:
            return jsonify({'success': False, 'error': 'Excel dosyasında geçerli kayıt bulunamadı.'})

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.executemany("""
            INSERT INTO satis_arsivi
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, insert_rows)
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Excel dosyasından toplam {len(insert_rows)} adet geçmiş satış kaydı başarıyla sisteme aktarıldı.',
            'eklenen_adet': len(insert_rows)
        })
    except Exception as e:
        logger.error(f"api_istatistikler_excel_yukle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

# ── BKST AUTHENTICATED SESSION HELPER ────────────────────────────────────────
_bkst_session_cache = {"session": None, "gln": None, "token2": None, "ts": 0}
_bkst_session_lock = threading.Lock()

def get_bkst_authenticated_session():
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password:
        return None, None, None, "Kullanıcı adı veya şifre bulunamadı."

    now = time.monotonic()
    with _bkst_session_lock:
        if (_bkst_session_cache["session"] is not None 
            and _bkst_session_cache["gln"] 
            and (now - _bkst_session_cache["ts"] < 300)):
            return _bkst_session_cache["session"], _bkst_session_cache["gln"], _bkst_session_cache["token2"], None

    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest"
    })
    if api_key:
        session.headers.update({"Authorization": f"Bearer {api_key}", "Key": api_key})

    r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(5, 10))
    token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
    token1 = token_match.group(1) if token_match else ""

    login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
    res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=SSL_VERIFY, timeout=(5, 15))
    if "0" not in res_login.text:
        return None, None, None, "Bakanlık kullanıcı adı veya şifreniz hatalı."

    r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(5, 10))
    token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
    token2 = token2_match.group(1) if token2_match else token1

    r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=SSL_VERIFY, timeout=(5, 10))
    gln_guid = address_id
    if r_gln.status_code == 200:
        try:
            gln_data = r_gln.json()
            if isinstance(gln_data, list) and len(gln_data) > 0:
                gln_guid = str(gln_data[0].get("Value") or "").strip()
        except Exception:
            pass

    with _bkst_session_lock:
        _bkst_session_cache.update({
            "session": session,
            "gln": gln_guid,
            "token2": token2,
            "ts": time.monotonic()
        })

    return session, gln_guid, token2, None

# ── Çiftçi & Müşteri Rehberi API ──────────────────────────────────────────────
@app.route('/api/ciftciler/liste', methods=['GET'])
def api_ciftciler_liste():
    q = str(request.args.get('q', '')).strip().lower()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if q:
            c.execute("""
                SELECT * FROM ciftciler 
                WHERE LOWER(tc_no) LIKE ? OR LOWER(ad_soyad) LIKE ? OR LOWER(telefon) LIKE ? OR LOWER(koy) LIKE ?
                ORDER BY son_islem_tarihi DESC, id DESC LIMIT 50
            """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))
        else:
            c.execute("SELECT * FROM ciftciler ORDER BY son_islem_tarihi DESC, id DESC LIMIT 100")
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({'success': True, 'ciftciler': rows, 'toplam': len(rows)})
    except Exception as e:
        logger.error(f"api_ciftciler_liste error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'ciftciler': []})

@app.route('/api/ciftciler/kaydet', methods=['POST'])
def api_ciftciler_kaydet():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    ad_soyad = str(data.get('ad_soyad', '')).strip().upper()
    telefon = str(data.get('telefon', '')).strip()
    il = str(data.get('il', '')).strip()
    ilce = str(data.get('ilce', '')).strip()
    koy = str(data.get('koy', '')).strip()
    notlar = str(data.get('notlar', '')).strip()

    if not tc_no or len(tc_no) < 10:
        return jsonify({'success': False, 'error': 'Geçerli bir T.C. Kimlik / Vergi No giriniz (10 veya 11 hane).'})
    if not ad_soyad:
        return jsonify({'success': False, 'error': 'Çiftçinin Adı Soyadı boş bırakılamaz.'})

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    username, _, _, _ = read_bkst_credentials()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute("""
            INSERT INTO ciftciler (tc_no, ad_soyad, telefon, il, ilce, koy, notlar, eklenme_tarihi, son_islem_tarihi, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tc_no) DO UPDATE SET
            ad_soyad = excluded.ad_soyad,
            telefon = COALESCE(NULLIF(excluded.telefon, ''), ciftciler.telefon),
            il = COALESCE(NULLIF(excluded.il, ''), ciftciler.il),
            ilce = COALESCE(NULLIF(excluded.ilce, ''), ciftciler.ilce),
            koy = COALESCE(NULLIF(excluded.koy, ''), ciftciler.koy),
            notlar = COALESCE(NULLIF(excluded.notlar, ''), ciftciler.notlar),
            son_islem_tarihi = excluded.son_islem_tarihi
        """, (tc_no, ad_soyad, telefon, il, ilce, koy, notlar, now_str, now_str, username or ''))
        conn.commit()
        ciftci_id = c.lastrowid
        conn.close()
        return jsonify({'success': True, 'message': f'✅ {ad_soyad} ({tc_no}) başarıyla rehbere kaydedildi.', 'id': ciftci_id})
    except Exception as e:
        logger.error(f"api_ciftciler_kaydet error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ciftciler/sil/<int:ciftci_id>', methods=['DELETE', 'POST'])
def api_ciftciler_sil(ciftci_id):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM ciftciler WHERE id = ?", (ciftci_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Çiftçi rehberden silindi.'})
    except Exception as e:
        logger.error(f"api_ciftciler_sil error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ciftciler/detay/<tc_no>', methods=['GET'])
def api_ciftciler_detay(tc_no):
    tc_clean = str(tc_no).strip()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM ciftciler WHERE tc_no = ?", (tc_clean,))
        row = c.fetchone()
        conn.close()
        if row:
            return jsonify({'success': True, 'bulundu': True, 'ciftci': dict(row)})
        return jsonify({'success': True, 'bulundu': False, 'ciftci': None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bkst/recetesiz_satis/sms_gonder', methods=['POST'])
def bkst_sms_gonder():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    if not tc_no:
        return jsonify({'success': False, 'error': 'T.C. Kimlik veya Vergi No giriniz.'})

    farmer_name = ""
    farmer_phone = ""
    try:
        conn_f = sqlite3.connect(DB_PATH)
        c_f = conn_f.cursor()
        c_f.execute("SELECT ad_soyad, telefon FROM ciftciler WHERE tc_no = ?", (tc_no,))
        row_f = c_f.fetchone()
        if row_f:
            farmer_name = row_f[0] or ""
            farmer_phone = row_f[1] or ""
        conn_f.close()
    except Exception:
        pass

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'OperationType': 1,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/SendSmsVerificationCode', data=payload, verify=SSL_VERIFY, timeout=(5, 15))
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('IsSuccess') is True:
                phone_hidden = res_json.get('MobilePhoneHidden', '') or farmer_phone
                bkst_name = (res_json.get('ProducerName') or res_json.get('FullName') or res_json.get('NameSurname') or res_json.get('UreticiAdi') or "").strip()
                if bkst_name and not farmer_name:
                    farmer_name = bkst_name

                if farmer_name:
                    try:
                        conn_f = sqlite3.connect(DB_PATH)
                        c_f = conn_f.cursor()
                        c_f.execute("""INSERT INTO ciftciler (tc_no, ad_soyad, telefon, son_islem_tarihi) 
                                       VALUES (?, ?, ?, ?)
                                       ON CONFLICT(tc_no) DO UPDATE SET 
                                       ad_soyad=COALESCE(NULLIF(excluded.ad_soyad, ''), ciftciler.ad_soyad),
                                       telefon=COALESCE(NULLIF(excluded.telefon, ''), ciftciler.telefon),
                                       son_islem_tarihi=excluded.son_islem_tarihi""",
                                    (tc_no, farmer_name, phone_hidden, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                        conn_f.commit()
                        conn_f.close()
                    except Exception:
                        pass

                return jsonify({
                    'success': True,
                    'phone_hidden': phone_hidden,
                    'farmer_name': farmer_name,
                    'verification_token': res_json.get('VerificationCode', ''),
                    'message': f"📲 SMS doğrulama kodu {phone_hidden} numaralı telefona gönderildi." + (f" ({farmer_name})" if farmer_name else "")
                })
            else:
                return jsonify({'success': False, 'error': res_json.get('Message') or 'SMS gönderilemedi.'})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})
    except Exception as e:
        logger.error(f"bkst_sms_gonder error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f"SMS Gönderme Hatası: {str(e)}"})

@app.route('/api/bkst/recetesiz_satis/sms_dogrula', methods=['POST'])
def bkst_sms_dogrula():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    sms_code = str(data.get('sms_code', '')).strip()
    verification_token = str(data.get('verification_token', '')).strip()

    if not tc_no or not sms_code or not verification_token:
        return jsonify({'success': False, 'error': 'Eksik parametre. T.C. No ve SMS kodu gereklidir.'})

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'VerificationCode': verification_token,
            'Code': sms_code,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/CheckSmsVerificationCode', data=payload, verify=SSL_VERIFY, timeout=(5, 15))
        if res.status_code == 200:
            res_str = res.text.strip().replace('"', '')
            try:
                res_json = res.json()
                if isinstance(res_json, dict):
                    if res_json.get('IsSuccess') is False:
                        return jsonify({'success': False, 'error': res_json.get('Message') or 'SMS doğrulama kodu hatalı.'})
                    v_tok = res_json.get('VerificationCode') or res_json.get('Data') or verification_token
                    return jsonify({
                        'success': True,
                        'verified_token': v_tok,
                        'message': '🟢 SMS doğrulaması başarıyla onaylandı!'
                    })
            except Exception:
                pass

            if res_str == "00000000-0000-0000-0000-000000000001":
                return jsonify({'success': False, 'error': '❌ Girilen SMS doğrulama kodu geçersiz veya hatalı.'})
            elif res_str == "00000000-0000-0000-0000-000000000000":
                return jsonify({'success': False, 'error': '❌ SMS doğrulama başarısız veya kodun süresi dolmuş.'})
            elif res_str and "error" not in res_str.lower() and "false" not in res_str.lower():
                return jsonify({
                    'success': True,
                    'verified_token': res_str,
                    'message': '🟢 SMS doğrulaması başarıyla onaylandı!'
                })
            else:
                return jsonify({'success': False, 'error': '❌ Girilen SMS doğrulama kodu hatalı veya süresi dolmuş.'})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})
    except Exception as e:
        logger.error(f"bkst_sms_dogrula error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f"SMS Doğrulama Hatası: {str(e)}"})

@app.route('/api/bkst/parseller', methods=['POST'])
def bkst_parseller():
    """
    Çiftçinin ÇKS / TÜKAS / KOBUKS parsel kayıtlarını Bakanlık BKST sisteminden çeker.
    """
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    verification_token = str(data.get('verification_token', '')).strip()
    parsel_tipi = str(data.get('parsel_tipi', 'cks')).strip().lower()

    if not tc_no or not verification_token:
        return jsonify({'success': False, 'error': 'T.C. No ve SMS Doğrulama tokenı gereklidir.'})

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        action_urls = {
            'cks': 'https://bkst.tarbil.gov.tr/Main/GetParcelList',
            'tukas': 'https://bkst.tarbil.gov.tr/Main/GetParcelListTKS',
            'kobuks': 'https://bkst.tarbil.gov.tr/Main/GetParcelListKBS'
        }
        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2
        url = action_urls.get(parsel_tipi, action_urls['cks'])

        payload = {
            'IdTaxNo': tc_no,
            'VerificationCode': verification_token,
            '__RequestVerificationToken': token
        }
        logger.info(f"BKST Parsel Sorgulanıyor: url={url}, tc={tc_no}, tip={parsel_tipi}")
        res = session.post(url, data=payload, verify=SSL_VERIFY, timeout=(5, 15))
        logger.info(f"BKST Parsel Yanıtı: status={res.status_code}, content_preview={res.text[:300]}")
        if res.status_code == 200:
            try:
                parseller = res.json()
                p_list = []
                if isinstance(parseller, list):
                    p_list = parseller
                elif isinstance(parseller, dict):
                    p_list = parseller.get('Data') or parseller.get('data') or parseller.get('Items') or []
                
                # Parsellerden çiftçi adını otomatik yakala
                producer_from_parcels = ""
                for p in p_list:
                    if isinstance(p, dict):
                        p_name = (p.get('ProducerName') or p.get('FullName') or p.get('ProducerTitle') or p.get('OwnerName') or p.get('UreticiAdi') or "").strip()
                        if p_name:
                            producer_from_parcels = p_name
                            break

                if producer_from_parcels:
                    try:
                        conn_f = sqlite3.connect(DB_PATH)
                        c_f = conn_f.cursor()
                        c_f.execute("""INSERT INTO ciftciler (tc_no, ad_soyad, son_islem_tarihi) 
                                       VALUES (?, ?, ?)
                                       ON CONFLICT(tc_no) DO UPDATE SET 
                                       ad_soyad=COALESCE(NULLIF(excluded.ad_soyad, ''), ciftciler.ad_soyad),
                                       son_islem_tarihi=excluded.son_islem_tarihi""",
                                    (tc_no, producer_from_parcels, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                        conn_f.commit()
                        conn_f.close()
                    except Exception:
                        pass

                return jsonify({'success': True, 'parseller': p_list, 'toplam': len(p_list), 'ciftci_adi': producer_from_parcels})
            except Exception as json_err:
                logger.warning(f"BKST parsel JSON ayrıştırma uyarısı: {json_err}, ham veri: {res.text[:300]}")
                return jsonify({'success': True, 'parseller': [], 'toplam': 0})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})
    except Exception as e:
        logger.error(f"bkst_parseller error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f"Parsel sorgulama hatası: {str(e)}"})

@app.route('/api/bkst/server_filtering/bitki', methods=['POST'])
def bkst_filter_bitki():
    """
    Bakanlıktan seçili ürün grubu için (örn. 'BUĞDAY') bitki listesini çeker.
    """
    data = request.json or {}
    grup_adi = str(data.get('grup_adi', '')).strip()

    session, _, _, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        res = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetUrun', data={'grupAdi': grup_adi, 'text': ''}, verify=SSL_VERIFY, timeout=(5, 12))
        if res.status_code == 200:
            return jsonify({'success': True, 'data': res.json()})
        return jsonify({'success': False, 'error': f'BKST HTTP {res.status_code}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bkst/server_filtering/zararli', methods=['POST'])
def bkst_filter_zararli():
    """
    Bakanlıktan bitkiNo için hedef zararlıları çeker.
    """
    data = request.json or {}
    bitki_no = str(data.get('bitki_no', '')).strip()

    session, _, _, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        res = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetZararli', data={'bitkiNo': bitki_no, 'text': ''}, verify=SSL_VERIFY, timeout=(5, 12))
        if res.status_code == 200:
            return jsonify({'success': True, 'data': res.json()})
        return jsonify({'success': False, 'error': f'BKST HTTP {res.status_code}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bkst/server_filtering/bku', methods=['POST'])
def bkst_filter_bku():
    """
    Bakanlıktan bitkiNo ve zararliNo için ruhsatlı BKU ürünlerini çeker.
    """
    data = request.json or {}
    bitki_no = str(data.get('bitki_no', '')).strip()
    zararli_no = str(data.get('zararli_no', '')).strip()

    session, _, _, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        res = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetBitkiBKUYeni', data={'bitkiNo': bitki_no, 'zararliNo': zararli_no, 'text': ''}, verify=SSL_VERIFY, timeout=(5, 12))
        if res.status_code == 200:
            return jsonify({'success': True, 'data': res.json()})
        return jsonify({'success': False, 'error': f'BKST HTTP {res.status_code}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

_REAL_PEST_CACHE = {}

def _tr_norm(s):
    tbl = str.maketrans('İıŞşĞğÜüÖöÇç', 'IiSsGgUuOoCc')
    return str(s or '').translate(tbl).upper()

@app.route('/api/bkst/urun_gercek_zararlilar', methods=['POST'])
def bkst_urun_gercek_zararlilar():
    """
    Seçilen bitki + ilaç için Bakanlıkta (BKST) GERÇEKTEN ruhsatlı olan zararlıları
    ve her biri için tavsiye dozu çeker; her zararlıyı ayrı ayrı listeler ve dozu en yüksekten düşüğe sıralar.
    """
    from concurrent.futures import ThreadPoolExecutor
    data = request.json or {}
    bitki_no = str(data.get('bitki_no', '')).strip()
    bitki_adi = str(data.get('bitki_adi', '') or data.get('cks_urun_adi', '')).strip()
    urun_adi = str(data.get('urun_adi', '')).strip()
    if not urun_adi:
        return jsonify({'success': False, 'error': 'urun_adi gerekli.'})

    session, _, _, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Eğer bitki_no yoksa bitki_adi üzerinden Bakanlıktan bitki_no bul
        if not bitki_no or bitki_no == '0':
            plant_search = bitki_adi or 'FINDIK'
            r_u = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetUrun',
                               data={'grupAdi': plant_search, 'text': ''}, verify=SSL_VERIFY, timeout=(5, 10))
            if r_u.status_code == 200:
                u_list = r_u.json()
                if u_list:
                    bitki_no = str(u_list[0].get('Value', ''))

        if not bitki_no or bitki_no == '0':
            bitki_no = '71' # FINDIK varsayılan

        brand = _tr_norm(re.split(r'[\s]+', urun_adi)[0])
        words = [w for w in re.split(r'[\s\(\)\[\]\/\-\_]+', _tr_norm(urun_adi)) if len(w) >= 2 and not w.endswith('LT') and not w.endswith('KG') and not w.endswith('GR') and not w.endswith('ML')]
        
        cache_key = (bitki_no, brand, " ".join(words))
        if cache_key in _REAL_PEST_CACHE:
            return jsonify({'success': True, 'zararlilar': _REAL_PEST_CACHE[cache_key], 'cached': True, 'bitki_no': bitki_no})

        r = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetZararli',
                         data={'bitkiNo': bitki_no, 'text': ''}, verify=SSL_VERIFY, timeout=(5, 30))
        pests = [p for p in (r.json() if r.status_code == 200 else []) if str(p.get('Value')) not in ('-2', '')]

        def check(p):
            try:
                rb = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetBitkiBKUYeni',
                                  data={'bitkiNo': bitki_no, 'zararliNo': p['Value'], 'text': brand},
                                  verify=SSL_VERIFY, timeout=(5, 10))
                if rb.status_code != 200:
                    return None
                b_list = rb.json()
                if not b_list:
                    return None

                # Find best matching BKU for this specific product
                best_b = None
                best_score = -1
                for b in b_list:
                    b_text = _tr_norm(b.get('Text', ''))
                    if brand not in b_text:
                        continue
                    score = 10
                    for w in words[1:]:
                        if w in b_text:
                            score += 5
                    if score > best_score:
                        best_score = score
                        best_b = b

                if not best_b:
                    return None

                ruhsat = best_b.get('Value')
                ruhsat_adi = best_b.get('Text', '')
                if not ruhsat:
                    return None

                rd = session.post('https://bkst.tarbil.gov.tr/Main/GetTavsiyeDozBilgisi',
                                  data={'zararliNo': p['Value'], 'ruhsatNo': ruhsat, 'bitkiNo': bitki_no},
                                  verify=SSL_VERIFY, timeout=(5, 10))
                dj = rd.json() if rd.status_code == 200 and rd.text.strip() else {}
                miktar = float(dj.get('IlacDozMiktar') or 0)
                if miktar <= 0:
                    return None
                birim = dj.get('IlacDozMiktarBirimi') or 'ml'
                olcu = dj.get('IlacDozKullanimOlcusu') or '1 da'
                return {
                    'zararli': p['Text'], 
                    'zararlisi': p['Text'], 
                    'zararli_no': str(p['Value']),
                    'ruhsat_no': str(ruhsat), 
                    'bku_adi': ruhsat_adi,
                    'dozaj': f"{miktar:g} {birim} / {olcu}",
                    'doz_miktar': miktar, 
                    'birim': birim, 
                    'olcu': olcu, 
                    'is_max': False
                }
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=16) as ex:
            results = [x for x in ex.map(check, pests) if x]

        results.sort(key=lambda x: x['doz_miktar'], reverse=True)
        if results:
            results[0]['is_max'] = True
            _REAL_PEST_CACHE[cache_key] = results
        return jsonify({'success': True, 'zararlilar': results, 'cached': False, 'bitki_no': bitki_no})
    except Exception as e:
        logger.error(f"urun_gercek_zararlilar error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bkst/server_filtering/doz_ve_formulasyon', methods=['POST'])
def bkst_filter_doz_ve_formulasyon():
    """
    Bakanlıktan ruhsatNo, zararliNo, bitkiNo için tavsiye doz ve aktif madde / formülasyonu çeker.
    """
    data = request.json or {}
    bitki_no = str(data.get('bitki_no', '')).strip()
    zararli_no = str(data.get('zararli_no', '')).strip()
    ruhsat_no = str(data.get('ruhsat_no', '')).strip()

    session, _, _, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Doz
        r_doz = session.post('https://bkst.tarbil.gov.tr/Main/GetTavsiyeDozBilgisi', data={'zararliNo': zararli_no, 'ruhsatNo': ruhsat_no, 'bitkiNo': bitki_no}, verify=SSL_VERIFY, timeout=(5, 12))
        doz_data = r_doz.json() if r_doz.status_code == 200 and r_doz.text.strip() else {}

        # Formülasyon
        r_form = session.post('https://bkst.tarbil.gov.tr/Main/GetActiveMaterialAndFormulation', data={'ruhsatNo': ruhsat_no}, verify=SSL_VERIFY, timeout=(5, 12))
        form_data = r_form.json() if r_form.status_code == 200 and r_form.text.strip() else {}

        return jsonify({
            'success': True,
            'doz': doz_data,
            'formulasyon': form_data
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bkst/recetesiz_satis', methods=['POST'])
def bkst_recetesiz_satis():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    verification_token = str(data.get('verification_token', '')).strip()
    karekods = data.get('karekods', [])
    belge_no = str(data.get('belge_no', '')).strip()
    aciklama = str(data.get('aciklama', 'Reçetesiz Satış')).strip()
    parcel_list = str(data.get('parcel_list', '')).strip()
    harmful_choice = data.get('harmful_choice', '')
    province = str(data.get('province', '')).strip()

    if not tc_no:
        return jsonify({'success': False, 'error': 'T.C. Kimlik veya Vergi No boş olamaz.'})
    if not karekods or not isinstance(karekods, list):
        return jsonify({'success': False, 'error': 'Satış yapılacak ürün/karekod bulunamadı.'})

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        df_cache, qr_map, gtin_map, koli_map = get_bkst_cache()

        datasource_items = []
        for raw_qr in karekods:
            norm_qr = normalize_qr(str(raw_qr).strip())
            if not norm_qr:
                continue

            match_row = resolve_product_from_cache(norm_qr, df_cache, qr_map, gtin_map)

            gtin = str(match_row.get('Gtin Numarası', '')).strip() if match_row else ""
            seri = str(match_row.get('Seri Numarası', '')).strip() if match_row else ""
            parti = str(match_row.get('Parti Numarası', '')).strip() if match_row else ""
            koli = str(match_row.get('Koli Numarası', '')).strip() if match_row else ""
            urun = str(match_row.get('Ürün Adı', '')).strip() if match_row else ""
            skt = str(match_row.get('Son Kullanma Tarihi', '')).strip() if match_row else ""

            # Eğer önbellekte bulunamadıysa cikis_kayitlari veritabanından çek
            if not urun or urun == "Bitki Koruma Ürünü":
                try:
                    conn_tmp = sqlite3.connect(DB_PATH)
                    c_tmp = conn_tmp.cursor()
                    c_tmp.execute("SELECT urun_adi, barkod, seri_no, parti_no, koli_no, skt FROM cikis_kayitlari WHERE ham_karekod = ? OR seri_no = ? LIMIT 1", (norm_qr, norm_qr))
                    r_ck = c_tmp.fetchone()
                    if r_ck:
                        if r_ck[0]: urun = r_ck[0]
                        if not gtin and r_ck[1]: gtin = r_ck[1]
                        if not seri and r_ck[2]: seri = r_ck[2]
                        if not parti and r_ck[3]: parti = r_ck[3]
                        if not koli and r_ck[4]: koli = r_ck[4]
                        if not skt and r_ck[5]: skt = r_ck[5]
                    conn_tmp.close()
                except Exception:
                    pass

            if not urun:
                urun = str(data.get('aciklama', '').replace('Reçetesiz Satış:', '').strip() or "Bitki Koruma Ürünü")

            parsed_qr = parse_gs1_qr(norm_qr)
            if not gtin and parsed_qr.get('gtin'):
                gtin = str(parsed_qr['gtin'])
            if not seri and parsed_qr.get('seri_no'):
                seri = str(parsed_qr['seri_no'])
            if not parti and parsed_qr.get('parti_no'):
                parti = str(parsed_qr['parti_no'])
            if not skt and parsed_qr.get('skt'):
                skt = str(parsed_qr['skt'])

            skt_val = skt or parsed_qr.get('skt') or ""
            uretim_val = str(match_row.get('Üretim Tarihi', '')).strip() if match_row else ""
            if not uretim_val:
                uretim_val = parsed_qr.get('uretim_tarihi', '')
            if not uretim_val and skt_val:
                try:
                    p_dt = datetime.strptime(skt_val, "%d.%m.%Y")
                    uretim_val = (p_dt.replace(year=p_dt.year - 4)).strftime("%d.%m.%Y")
                except Exception:
                    uretim_val = "01.01.2025"
            if not uretim_val:
                uretim_val = "01.01.2025"

            skt_yy = ""
            if skt_val:
                try:
                    dt = datetime.strptime(skt_val, "%d.%m.%Y")
                    skt_yy = dt.strftime("%y%m%d")
                except Exception:
                    skt_yy = "290101"

            # Format data to strictly match BKST MVC Grid & Model Binder
            datasource_items.append({
                "HEADERSTATE": None,
                "OPERATION": None,
                "BARCODE": gtin,
                "LOTNUMBER": parti,
                "SERIALNUMBER": seri,
                "PRODUCTIONDATE": uretim_val,
                "SKT": skt_yy,
                "DETAILSTATE": "AKTIF",
                "OPERATIONDATE": datetime.now().strftime("%d.%m.%Y"),
                "EXPIRATIONDATE": skt_val or "01.01.2028",
                "KAREKOD": norm_qr,
                "OPERATIONATABLE": "FIRM",
                "CARRIERLABEL1": "",
                "CARRIERLABEL2": koli,
                "STOCKNAME": urun,
                "PACKAGESIZE": 1.0,
                "PACKAGEUNIT": "l",
                "QUANTITY": 1
            })

        # ── 1. ÇİFT SATIŞ KONTROLÜ (Aynı ürünün tekrar satılmasını engelle) ──────
        conn_chk = sqlite3.connect(DB_PATH, timeout=30.0)
        c_chk = conn_chk.cursor()
        already_sold = []
        for item in datasource_items:
            norm_qr = item.get('KAREKOD', '').strip()
            seri = item.get('SERIALNUMBER', '').strip()
            if norm_qr or seri:
                c_chk.execute("""
                    SELECT tarih, urun_adi, seri_no 
                    FROM satis_arsivi 
                    WHERE ((ham_karekod = ? AND ham_karekod != '') 
                       OR (seri_no = ? AND seri_no != ''))
                    AND durum = 'RECETESIZ_SATIS_BKST'
                    LIMIT 1
                """, (norm_qr, seri))
                row = c_chk.fetchone()
                if row:
                    already_sold.append(f"{row[1]} (Seri No: {row[2] or '-'}, Satış Tarihi: {row[0]})")
        conn_chk.close()

        if already_sold:
            err_details = "<br>• " + "<br>• ".join(already_sold[:5])
            if len(already_sold) > 5:
                err_details += f"<br>... ve {len(already_sold) - 5} adet daha."
            return jsonify({
                'success': False, 
                'error': f"⚠️ TEKRAR SATIŞ ENGELİ: Satışa eklenen ürün(ler) daha önce satılmış olarak kayıtlıdır! Aynı ürün tekrar satılamaz:{err_details}"
            })

        # ── HarmfulChoice Doğrulama ve Otomatik Tamamlama ─────────────────────
        harmful_obj = {}
        if isinstance(harmful_choice, dict):
            harmful_obj = dict(harmful_choice)
        elif isinstance(harmful_choice, str) and harmful_choice.strip():
            try:
                harmful_obj = json.loads(harmful_choice)
            except Exception:
                harmful_obj = {}

        # Parsel ve İl Doğrulama / Tamamlama
        if not province or not parcel_list:
            try:
                r_parcels = session.post('https://bkst.tarbil.gov.tr/Main/GetParcelList', 
                                         data={'IdTaxNo': tc_no, 'VerificationCode': verification_token, '__RequestVerificationToken': token}, 
                                         verify=SSL_VERIFY, timeout=(5, 10))
                if r_parcels.status_code == 200:
                    p_list = r_parcels.json()
                    if p_list and isinstance(p_list, list):
                        if not province:
                            province = str(p_list[0].get('City') or 'SAMSUN').strip()
                        if not parcel_list:
                            parcel_list = str(p_list[0].get('FieldProductId') or p_list[0].get('Id') or '').strip()
                        if not harmful_obj.get('cksProductName'):
                            harmful_obj['cksProductName'] = str(p_list[0].get('Product') or 'FINDIK').strip()
            except Exception as e_p:
                logger.warning(f"Parcel auto-match fallback notice: {e_p}")

        if not province:
            province = 'SAMSUN'

        plant_name = str(harmful_obj.get('cksProductName') or harmful_obj.get('productName') or 'FINDIK').strip()
        harmful_obj['cksProductName'] = plant_name
        harmful_obj['productName'] = plant_name

        if not harmful_obj.get('productNo') or str(harmful_obj.get('productNo')) == '0':
            try:
                r_u = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetUrun', 
                                   data={'grupAdi': plant_name, 'text': ''}, 
                                   verify=SSL_VERIFY, timeout=(5, 10))
                u_list = r_u.json() if r_u.status_code == 200 else []
                harmful_obj['productNo'] = str(u_list[0]['Value']) if u_list else '71'
            except Exception:
                harmful_obj['productNo'] = '71'

        sample_urun = datasource_items[0]['STOCKNAME'] if datasource_items else ''
        clean_sample = _tr_norm(re.split(r'[\s\(\[\d]', sample_urun)[0].strip()) if sample_urun else 'KORFOSAT'

        # KORFOSAT 48 SL veya benzeri ürünler için doğru ruhsatı belirle
        target_bku_no = str(harmful_obj.get('bkuNo') or '').strip()
        if 'KORFOSAT' in _tr_norm(sample_urun) and '48' in _tr_norm(sample_urun):
            target_bku_no = '3120'
        elif not target_bku_no or target_bku_no in ('0', 'None'):
            target_bku_no = '3120'

        harmful_obj['bkuNo'] = target_bku_no
        bitki_id = str(harmful_obj.get('productNo') or '71')
        harmful_obj['productNo'] = bitki_id

        # 1. Seçili zararlı ve ruhsat için Bakanlıktan Tavsiye Doz Bilgisini Doğrula
        valid_dose_info = None
        current_z_no = str(harmful_obj.get('harmfulOrganismNo') or '').strip()

        if current_z_no and current_z_no not in ('0', 'None'):
            try:
                r_doz_chk = session.post('https://bkst.tarbil.gov.tr/Main/GetTavsiyeDozBilgisi',
                                         data={'zararliNo': current_z_no, 'ruhsatNo': target_bku_no, 'bitkiNo': bitki_id},
                                         verify=SSL_VERIFY, timeout=(5, 10))
                if r_doz_chk.status_code == 200 and r_doz_chk.text.strip():
                    dj = r_doz_chk.json()
                    if float(dj.get('IlacDozMiktar') or 0) > 0:
                        valid_dose_info = dj
            except Exception as e_doz:
                logger.warning(f"GetTavsiyeDozBilgisi initial check error: {e_doz}")

        # 2. Eğer tavsiye dozu bulunamadıysa / geçersizse, bu BKU ve bitki için en yüksek dozu veren geçerli zararlıyı otomatik bul
        if not valid_dose_info:
            try:
                r_z = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetZararli',
                                   data={'bitkiNo': bitki_id, 'text': ''},
                                   verify=SSL_VERIFY, timeout=(5, 10))
                z_list = r_z.json() if r_z.status_code == 200 else []

                # Kanyaş (921), Büyük ısırgan (882), Köpekdişi (539) gibi yüksek dozlu otları önce dene
                priority_z_ids = ['921', '882', '539', '525', '818', '1052']
                sorted_z = sorted(z_list, key=lambda z: 0 if str(z.get('Value')) in priority_z_ids else 1)

                for z in sorted_z:
                    z_val = str(z.get('Value'))
                    if z_val in ('-2', '0', ''):
                        continue
                    r_chk = session.post('https://bkst.tarbil.gov.tr/Main/GetTavsiyeDozBilgisi',
                                         data={'zararliNo': z_val, 'ruhsatNo': target_bku_no, 'bitkiNo': bitki_id},
                                         verify=SSL_VERIFY, timeout=(5, 8))
                    if r_chk.status_code == 200 and r_chk.text.strip():
                        dj = r_chk.json()
                        m = float(dj.get('IlacDozMiktar') or 0)
                        if m > 0:
                            valid_dose_info = dj
                            harmful_obj['harmfulOrganismNo'] = z_val
                            harmful_obj['harmfulOrganismName'] = str(z.get('Text') or dj.get('ZararliAdi') or 'Kanyaş (Sorghum halepense)')
                            if m >= 600:
                                break
            except Exception as e_find:
                logger.warning(f"Auto-finding valid pest error: {e_find}")

        if not valid_dose_info:
            # Kesin fallback
            harmful_obj['harmfulOrganismNo'] = '921'
            harmful_obj['harmfulOrganismName'] = 'Kanyaş (Sorghum halepense)'
            valid_dose_info = {'IlacDozMiktar': 600, 'IlacDozMiktarBirimi': 'ml', 'IlacDozKullanimOlcusu': '1 da'}

        # İsim temizliği
        clean_harm_name = str(harmful_obj.get('harmfulOrganismName') or 'Kanyaş (Sorghum halepense)').strip()
        clean_harm_name = re.sub(r'^[^\w\(\)\=\.\s]+', '', clean_harm_name)
        clean_harm_name = re.sub(r'\[.*?\]', '', clean_harm_name).split('—')[0].split('-')[0].strip()
        harmful_obj['harmfulOrganismName'] = clean_harm_name

        # 3. Formülasyon ve Aktif Madde Bilgilerini GetActiveMaterialAndFormulation ile Çek
        try:
            r_fm = session.post('https://bkst.tarbil.gov.tr/Main/GetActiveMaterialAndFormulation',
                                data={'ruhsatNo': target_bku_no},
                                verify=SSL_VERIFY, timeout=(5, 10))
            if r_fm.status_code == 200 and r_fm.text.strip():
                fm_data = r_fm.json()
                if fm_data:
                    harmful_obj['activeMaterialGroupNo'] = str(fm_data.get('AktifMaddeGrupNumarasi') or '176')
                    harmful_obj['activeMaterialGroupName'] = str(fm_data.get('AktifMaddeGrupAdi') or 'Glyphosate isopropylamine tuzu')
                    harmful_obj['formulationNo'] = str(fm_data.get('FormulasyonNumarasi') or '37')
                    harmful_obj['formulationName'] = str(fm_data.get('FormulasyonAdi') or 'SL')
                    if fm_data.get('BKUUrunAdi'):
                        harmful_obj['bkuName'] = str(fm_data.get('BKUUrunAdi'))
        except Exception as e_fm:
            logger.warning(f"GetActiveMaterialAndFormulation notice: {e_fm}")

        harmful_obj.setdefault('activeMaterialGroupNo', '176')
        harmful_obj.setdefault('activeMaterialGroupName', 'Glyphosate isopropylamine tuzu')
        harmful_obj.setdefault('formulationNo', '37')
        harmful_obj.setdefault('formulationName', 'SL')
        harmful_obj.setdefault('bkuName', sample_urun or 'KORFOSAT 48 SL (İMAL)')

        # Kesin JSON Dizesi Oluştur
        final_harmful_choice_json = json.dumps({
            'cksProductName': str(harmful_obj.get('cksProductName') or 'FINDIK'),
            'productNo': str(harmful_obj.get('productNo') or '71'),
            'productName': str(harmful_obj.get('productName') or 'FINDIK'),
            'harmfulOrganismNo': str(harmful_obj.get('harmfulOrganismNo') or '921'),
            'harmfulOrganismName': str(harmful_obj.get('harmfulOrganismName') or 'Kanyaş (Sorghum halepense)'),
            'activeMaterialGroupNo': str(harmful_obj.get('activeMaterialGroupNo') or '176'),
            'activeMaterialGroupName': str(harmful_obj.get('activeMaterialGroupName') or 'Glyphosate isopropylamine tuzu'),
            'formulationNo': str(harmful_obj.get('formulationNo') or '37'),
            'formulationName': str(harmful_obj.get('formulationName') or 'SL'),
            'bkuNo': str(harmful_obj.get('bkuNo') or target_bku_no),
            'bkuName': str(harmful_obj.get('bkuName') or sample_urun)
        })

        # ── 3. BKST GetStockByQrcode İle Zenginleştirilmiş DataSource Hazırlama ────
        bkst_grid_items = []
        stk_dose_val = float(valid_dose_info.get('IlacDozMiktar') or 600)
        stk_dose_unit = str(valid_dose_info.get('IlacDozMiktarBirimi') or 'ml')
        stk_dose_measure = str(valid_dose_info.get('IlacDozKullanimOlcusu') or '1 da')

        for raw_qr in karekods:
            norm_qr = normalize_qr(str(raw_qr).strip())
            if not norm_qr:
                continue
            try:
                r_stk = session.post('https://bkst.tarbil.gov.tr/Main/GetStockByQrcode', data={
                    'DataSource': json.dumps(bkst_grid_items),
                    'AddressId': gln_guid,
                    'Karekod': norm_qr,
                    'CarrierType': '1',
                    'BkuNo': str(harmful_obj.get('bkuNo') or target_bku_no),
                    'cName': province,
                    'pList': parcel_list,
                    'hNo': str(harmful_obj.get('harmfulOrganismNo') or '921'),
                    'uMeasure': stk_dose_measure,
                    'dValue': stk_dose_val,
                    'dUnit': stk_dose_unit,
                    'pArea': 100,
                    'pNo': str(harmful_obj.get('productNo') or '71'),
                    'cksPName': str(harmful_obj.get('cksProductName') or 'FINDIK'),
                    '__RequestVerificationToken': token
                }, verify=SSL_VERIFY, timeout=(5, 10))
                stk_json = r_stk.json() if r_stk.status_code == 200 else {}
                g_data = stk_json.get('gridData', [])
                if g_data and isinstance(g_data, list) and len(g_data) > 0:
                    bkst_grid_items = g_data
            except Exception as e_stk:
                logger.warning(f"GetStockByQrcode notice: {e_stk}")

        final_datasource = bkst_grid_items if bkst_grid_items else datasource_items

        today_str = datetime.now().strftime("%Y.%m.%d")
        payload = {
            'SenderAddress': gln_guid,
            'IdTaxNo': tc_no,
            'DocumentNo': belge_no or f"SATIS-{datetime.now().strftime('%Y%m%d%H%M')}",
            'DocumentDate': today_str,
            'Desc': aciklama,
            'DataSource': json.dumps(final_datasource),
            'ParcelList': parcel_list,
            'HarmfulChoice': final_harmful_choice_json,
            'Province': province,
            'VerificationCode': verification_token,
            '__RequestVerificationToken': token
        }

        logger.info(f"BKST Reçetesiz Satış İsteği Gönderiliyor: DocumentNo={payload['DocumentNo']}, TC={tc_no}, ParcelList={parcel_list}, Province={province}, HarmfulChoice={final_harmful_choice_json}, ItemsCount={len(final_datasource)}")

        res = session.post('https://bkst.tarbil.gov.tr/Main/NewCheckOutNotificationForProducerNonPrescribed', data=payload, verify=SSL_VERIFY, timeout=(5, 20))
        logger.info(f"BKST Reçetesiz Satış Yanıtı: status={res.status_code}, content_preview={res.text[:400]}")

        if res.status_code == 200:
            try:
                res_data = res.json()
                if res_data.get('Result') is True or res_data == 1:
                    username_cur, _, _, _ = read_bkst_credentials()
                    conn = sqlite3.connect(DB_PATH, timeout=30.0)
                    c = conn.cursor()
                    tarih_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    for item in datasource_items:
                        c.execute('''INSERT INTO satis_arsivi
                            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, durum)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'RECETESIZ_SATIS_BKST')''',
                            (tarih_now, item['STOCKNAME'], item['BARCODE'], item['CARRIERLABEL2'], item['SERIALNUMBER'], item['LOTNUMBER'], '', '', item['EXPIRATIONDATE'], item['KAREKOD'], username_cur))

                    # ── 2. ÇIKIŞ LİSTESİNDEN (cikis_kayitlari) TEMİZLEME ─────────────
                    kayit_ids = data.get('kayit_ids', [])
                    if kayit_ids and isinstance(kayit_ids, list):
                        placeholders = ','.join('?' for _ in kayit_ids)
                        c.execute(f"DELETE FROM cikis_kayitlari WHERE id IN ({placeholders})", tuple(kayit_ids))

                    sold_karekods = [item['KAREKOD'] for item in datasource_items if item.get('KAREKOD')]
                    if sold_karekods:
                        for i in range(0, len(sold_karekods), 500):
                            chk = sold_karekods[i:i+500]
                            ph = ','.join('?' for _ in chk)
                            c.execute(f"DELETE FROM cikis_kayitlari WHERE ham_karekod IN ({ph})", tuple(chk))

                    sold_serials = [item['SERIALNUMBER'] for item in datasource_items if item.get('SERIALNUMBER')]
                    if sold_serials:
                        for i in range(0, len(sold_serials), 500):
                            chk = sold_serials[i:i+500]
                            ph = ','.join('?' for _ in chk)
                            c.execute(f"DELETE FROM cikis_kayitlari WHERE seri_no IN ({ph})", tuple(chk))

                    if karekods and isinstance(karekods, list):
                        for i in range(0, len(karekods), 500):
                            chk = [str(k).strip() for k in karekods[i:i+500] if str(k).strip()]
                            ph = ','.join('?' for _ in chk)
                            c.execute(f"DELETE FROM cikis_kayitlari WHERE ham_karekod IN ({ph})", tuple(chk))

                    # ── 3. ÇİFTÇİ REHBERİNİ GÜNCELLE / KAYDET ────────────────────────
                    ciftci_adi = str(data.get('ciftci_adi', '')).strip()
                    if tc_no:
                        try:
                            c.execute("""
                                INSERT INTO ciftciler (tc_no, ad_soyad, il, eklenme_tarihi, son_islem_tarihi, kullanici_adi)
                                VALUES (?, ?, ?, ?, ?, ?)
                                ON CONFLICT(tc_no) DO UPDATE SET
                                ad_soyad = COALESCE(NULLIF(excluded.ad_soyad, ''), ciftciler.ad_soyad),
                                il = COALESCE(NULLIF(excluded.il, ''), ciftciler.il),
                                son_islem_tarihi = excluded.son_islem_tarihi
                            """, (tc_no, ciftci_adi or 'Kayıtlı Çiftçi', province or '', tarih_now, tarih_now, username_cur or ''))
                        except Exception as e_ciftci:
                            logger.warning(f"ciftci kayit error: {e_ciftci}")

                    conn.commit()
                    conn.close()

                    return jsonify({
                        'success': True,
                        'message': f"🟢 BAŞARILI! Bakanlık BKST sistemine {len(datasource_items)} adet ürünün Reçetesiz Satış bildirimi API ile tamamlandı!"
                    })
                else:
                    err_msgs = []
                    data_errs = res_data.get('Data', [])
                    if isinstance(data_errs, list):
                        for e in data_errs:
                            if isinstance(e, dict) and e.get('Message'):
                                err_msgs.append(e.get('Message'))
                    err_text = " - ".join(err_msgs) if err_msgs else (res_data.get('Message') or str(res_data))
                    return jsonify({'success': False, 'error': f"BKST Bildirimi: {err_text}"})
            except Exception:
                return jsonify({'success': False, 'error': f"Bakanlık Yanıtı: {res.text[:300]}"})
        else:
            err_detail = ""
            if "<title>" in res.text:
                m_title = re.search(r'<title>(.*?)</title>', res.text, re.IGNORECASE | re.DOTALL)
                if m_title:
                    err_detail = m_title.group(1).strip()
            if not err_detail:
                err_detail = f"Sunucu Yanıt Kodu: {res.status_code}"
            return jsonify({
                'success': False, 
                'error': f"BKST Sunucu Hatası ({res.status_code}): {err_detail} — Lütfen çiftçinin geçerli bir parsel seçtiğinden ve SMS/Kimlik doğrulamasının yapıldığından emin olunuz."
            })

    except Exception as e:
        logger.error(f"bkst_recetesiz_satis error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f"Sistem hatası: {str(e)}"})

# ── Tarım ve Orman Bakanlığı BKU Bilgi & Dozaj Veri Tabanı ───────────────────
PESTICIDE_KNOWLEDGE_BASE = [
    {
        'keywords': ['korfasat', 'korfosat', 'agnoround', 'roundup', 'glifosat', 'glyphosate', 'knockdown', 'clinic', 'herkul', 'herkül', 'kortest', 'rodeo', 'tornado', 'cleaner', 'glycel', 'taifun', 'mamba'],
        'urun_grubu': 'Total Herbisit (Yabancı Ot İlacı)',
        'etken_madde': 'Glifosat İzopropilamin Tuzu (480 g/L)',
        'hedef_zararlilar': 'Kanyaş, Köpekdişi Ayrığı, Tarla Sarmaşığı, Topalak, Büyük Isırgan, Yabani Fiğ, Darıcan, Sirken, Semizotu vb.',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 600 ml / Dekar',
        'dozaj_detay': 'Derin köklü çok yıllık yabancı otlarda (Kanyaş, Köpekdişi ayrığı, Topalak, Tarla sarmaşığı, Isırgan): 600 ml/Dekar | Tek yıllık dar ve geniş yapraklı otlarda: 300 ml/Dekar',
        'uygulama_zamani': 'Yabancı otların genç ve aktif büyüme döneminde bol su ile uygulanmalıdır.',
        'hasat_arasi_sure': '7 Gün',
        'onemli_notlar': '⚠️ Total herbisittir! Sistemden azami kota alabilmek için ruhsatlı zararlılar arasından "Kanyaş", "Köpekdişi Ayrığı", "Tarla Sarmaşığı" veya "Büyük Isırgan" (600 ml/da) seçilmelidir.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Kanyaş (Sorghum halepense)', 'dozaj': '600 ml / Dekar', 'doz_miktar': 600, 'zararli_no': '921'},
            {'zararli': 'Köpekdişi ayrığı (Cynodon dactylon)', 'dozaj': '600 ml / Dekar', 'doz_miktar': 600, 'zararli_no': '539'},
            {'zararli': 'Tarla sarmaşığı (Convolvulus arvensis)', 'dozaj': '600 ml / Dekar', 'doz_miktar': 600, 'zararli_no': '525'},
            {'zararli': 'Topalak (Cyperus rotundus)', 'dozaj': '600 ml / Dekar', 'doz_miktar': 600, 'zararli_no': '818'},
            {'zararli': 'Büyük ısırgan (Urtica diocia)', 'dozaj': '600 ml / Dekar', 'doz_miktar': 600, 'zararli_no': '882'},
            {'zararli': 'Yabani pelin otu (Artemisia vulgaris)', 'dozaj': '600 ml / Dekar', 'doz_miktar': 600, 'zararli_no': '1052'},
            {'zararli': 'Yabani fiğ (Vicia spp)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '319'},
            {'zararli': 'Darıcan (Echinochloa crus-galli)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '559'},
            {'zararli': 'Sirken (Chenopodium album)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '1104'},
            {'zararli': 'Semizotu (Portulaca oleracea)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '562'},
            {'zararli': 'Horozibiği (Amaranthus albus)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '356'},
            {'zararli': 'Kuşotu = Serçe dili (Stellaria media)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '1458'},
            {'zararli': 'Ballıbaba (Lamium spp.)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '1504'},
            {'zararli': 'Yabani hardal (Sinapis arvensis)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '649'},
            {'zararli': 'Yabani havuç (Daucus carota)', 'dozaj': '300 ml / Dekar', 'doz_miktar': 300, 'zararli_no': '77'}
        ]
    },
    {
        'keywords': ['kung-fu', 'kungfu', 'karate', 'lambda', 'cyhalothrin', 'ninja', 'maestro'],
        'urun_grubu': 'Geniş Spektrumlu Piretroid İnsektisit (Kapsül Süspansiyon)',
        'etken_madde': 'Lambda-Cyhalothrin (50 g/L CS/EC)',
        'hedef_zararlilar': 'Fındık kurdu, Yeşilkurt, Süne, Kımıl, Elma içkurdu, Patates böceği, Bağ salkım güvesi, Mısır kurdu',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 50 ml / Dekar',
        'dozaj_detay': 'Fındık Kurdu, Pamuk/Domates Yeşilkurt: 50 ml / Dekar (AZAMİ DOZ) | Patates Böceği: 40 ml / Dekar | Hububatta Süne ve Kımıl: 30 - 50 ml / Dekar | Mısır Koçankurdu: 30 ml / Dekar | Sebzede Yaprakbiti/Trips: 25 - 30 ml / Dekar | Bağ Salkım Güvesi: 20 ml / 100 L su',
        'uygulama_zamani': 'Fındık kurdu ergin çıkışlarında, süne nimf döneminde veya kurt yumurtaları açılmadan sabah erken saatlerde uygulanır.',
        'hasat_arasi_sure': '3 - 7 Gün',
        'onemli_notlar': '💡 Sistemden azami kota alabilmek için "Fındık Kurdu / Pamuk ve Domates Yeşilkurt (50 ml/da)" seçilmelidir.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Fındık Kurdu (Curculio nucum) ve Kokarca Mücadelesi', 'dozaj': '50 ml / Dekar', 'doz_miktar': 50},
            {'zararli': 'Pamuk ve Domateste Yeşilkurt (Helicoverpa armigera)', 'dozaj': '50 ml / Dekar', 'doz_miktar': 50},
            {'zararli': 'Sebzelerde Bozkurt (Agrotis spp.) ve Tırtıllar', 'dozaj': '40 - 50 ml / Dekar', 'doz_miktar': 45},
            {'zararli': 'Patates Böceği (Leptinotarsa decemlineata)', 'dozaj': '40 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Hububatta Süne Nimf ve Kımıl Mücadelesi', 'dozaj': '30 - 50 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Mısır Koçankurdu ve Mısır Kurdu (Ostrinia)', 'dozaj': '30 ml / Dekar', 'doz_miktar': 30},
            {'zararli': 'Sebzelerde Yaprak Bitleri ve Tripsler', 'dozaj': '25 - 30 ml / Dekar', 'doz_miktar': 30},
            {'zararli': 'Elma İçkurdu ve Yaprak Bükücü (Cydia pomonella)', 'dozaj': '20 - 25 ml / 100 L su', 'doz_miktar': 25},
            {'zararli': 'Bağ Salkım Güvesi (Lobesia botrana)', 'dozaj': '20 ml / 100 L su', 'doz_miktar': 20}
        ]
    },
    {
        'keywords': ['korquid', 'korquıd', 'reglone', 'diquat'],
        'urun_grubu': 'Kontakt Etkili Desikant & Seçici Olmayan Herbisit',
        'etken_madde': 'Diquat Dibromür (200 g/L SL)',
        'hedef_zararlilar': 'Hasat öncesi kurutma (desikasyon), geniş ve dar yapraklı yabancı otlar',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 400 ml / Dekar',
        'dozaj_detay': 'Ayçiçeğinde Hasat Öncesi Baş Kurutma: 300 - 400 ml/Dekar (AZAMİ DOZ) | Sıra arası yabancı otlar: 300 - 400 ml/Dekar | Patateste Sap Kurutma: 250 - 300 ml/Dekar | Yonca tohumluğu kurutma: 250 - 300 ml/Dekar',
        'uygulama_zamani': 'Hasattan 7-10 gün önce ürün olgunlaşma aşamasında bol su ile uygulanır.',
        'hasat_arasi_sure': '7 Gün',
        'onemli_notlar': '⚠️ Çabuk kurutucu kontakt herbisittir. Güneşli havada etkisi daha hızlıdır.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Ayçiçeğinde Hasat Öncesi Baş Kurutma (Desikasyon)', 'dozaj': '350 - 400 ml / Dekar', 'doz_miktar': 400},
            {'zararli': 'Sıra Arası Geniş ve Dar Yapraklı Yabancı Otlar', 'dozaj': '300 - 400 ml / Dekar', 'doz_miktar': 350},
            {'zararli': 'Patateste Hasat Öncesi Sap Kurutma (Desikasyon)', 'dozaj': '250 - 300 ml / Dekar', 'doz_miktar': 300},
            {'zararli': 'Yonca ve Baklagil Tohumluklarında Kurutma', 'dozaj': '250 - 300 ml / Dekar', 'doz_miktar': 300},
            {'zararli': 'Pamukta Hasat Öncesi Yaprak Döktürme', 'dozaj': '200 - 250 ml / Dekar', 'doz_miktar': 250}
        ]
    },
    {
        'keywords': ['kortac', 'kortaç', 'fastac', 'alphacypermethrin', 'alpha-cypermethrin', 'super tak'],
        'urun_grubu': 'Hızlı Düşürücü (Knock-down) Sentetik Piretroid İnsektisit',
        'etken_madde': 'Alpha-Cypermethrin (100 g/L EC)',
        'hedef_zararlilar': 'Fındık kurdu, Süne, Kımıl, Yeşilkurt, Elma içkurdu, Koçankurdu',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 50 ml / Dekar',
        'dozaj_detay': 'Fındık Kurdu: 40 - 50 ml / Dekar (AZAMİ DOZ) | Hububatta Süne Nimf: 40 ml / Dekar | Pamukta Yeşilkurt: 30 - 40 ml / Dekar | Mısır Kurdu: 30 ml / Dekar | Sebzede Yeşilkurt: 25 - 30 ml / Dekar | Elmada İçkurt: 15 - 20 ml / 100 L su',
        'uygulama_zamani': 'Zararlı eşiğe ulaştığında serin havada homojen püskürtme ile uygulanır.',
        'hasat_arasi_sure': '7 - 14 Gün',
        'onemli_notlar': 'ℹ️ Fındıkta azami kota için "Fındık Kurdu (50 ml/da)" seçiniz.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Fındık Kurdu (Curculio nucum) ve Kokarca', 'dozaj': '40 - 50 ml / Dekar', 'doz_miktar': 50},
            {'zararli': 'Hububatta Süne Nimf ve Kımıl Mücadelesi', 'dozaj': '40 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Pamuk ve Domateste Yeşilkurt (Helicoverpa)', 'dozaj': '30 - 40 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Mısırda Koçankurdu ve Mısır Kurdu', 'dozaj': '30 ml / Dekar', 'doz_miktar': 30},
            {'zararli': 'Sebzelerde Bozkurt ve Yaprak Kurtları', 'dozaj': '25 - 30 ml / Dekar', 'doz_miktar': 30},
            {'zararli': 'Elma İçkurdu ve Ağaç Sarı Kurdu', 'dozaj': '15 - 20 ml / 100 L su', 'doz_miktar': 20}
        ]
    },
    {
        'keywords': ['alcine', 'amistar', 'ortiva', 'quadris', 'azoxystrobin', 'azoksistrobin'],
        'urun_grubu': 'Geniş Spektrumlu Sistemik Strobilurin Fungisit',
        'etken_madde': 'Azoxystrobin (250 g/L SC veya Kombinasyon)',
        'hedef_zararlilar': 'Çeltik yanıklığı, Hububat pası, Bağ mildiyö ve külleme, Sebze küllemesi',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 100 ml / Dekar',
        'dozaj_detay': 'Çeltik Yanıklığı: 100 ml / Dekar (AZAMİ DOZ) | Hububatta Pas ve Külleme: 75 - 100 ml / Dekar | Bağda Külleme ve Mildiyö: 75 - 100 ml / Dekar | Domates/Biber Külleme: 75 ml / Dekar | Mısır Yaprak Yanıklığı: 50 - 75 ml / Dekar',
        'uygulama_zamani': 'Hastalık belirtileri görülmeden veya ilk lezyonlar başladığında koruyucu olarak uygulanır.',
        'hasat_arasi_sure': '3 - 7 Gün',
        'onemli_notlar': 'ℹ️ Hem koruyucu hem tedavi edici etkiye sahiptir. Azami kota için "Çeltik Yanıklığı / Hububat Pası (100 ml/da)" seçilmelidir.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Çeltik Yanıklığı (Pyricularia oryzae)', 'dozaj': '100 ml / Dekar', 'doz_miktar': 100},
            {'zararli': 'Hububatta Sarı Pas, Kahverengi Pas ve Külleme', 'dozaj': '75 - 100 ml / Dekar', 'doz_miktar': 100},
            {'zararli': 'Bağda Külleme ve Mildiyö Hastalıkları', 'dozaj': '75 - 100 ml / Dekar', 'doz_miktar': 90},
            {'zararli': 'Domates, Biber ve Hıyarda Külleme ve Erken Yanıklık', 'dozaj': '75 ml / Dekar', 'doz_miktar': 75},
            {'zararli': 'Mısırda Yaprak Yanıklığı (Helminthosporium)', 'dozaj': '50 - 75 ml / Dekar', 'doz_miktar': 70},
            {'zararli': 'Şeker Pancarında Cercospora Yaprak Lekesi ve Külleme', 'dozaj': '50 - 60 ml / Dekar', 'doz_miktar': 60}
        ]
    },
    {
        'keywords': ['emalda', 'surrender', 'emamectin', 'affirm', 'proclaim'],
        'urun_grubu': 'Doğal Kökenli Modern Kurt & Güve İnsektisiti (Avermectin)',
        'etken_madde': 'Emamectin Benzoate (50 g/kg veya 9.5 g/L)',
        'hedef_zararlilar': 'Domates güvesi (Tuta absoluta), Yeşilkurt, Pamuk yaprakkurdu, Salkım güvesi, Elma içkurdu',
        'en_yuksek_doz_birim': 'gr/Dekar',
        'maksimum_dozaj': 'Maksimum 30 gr / Dekar',
        'dozaj_detay': 'Domates Güvesi (Tuta) ve Yeşilkurt: 25 - 30 gr / Dekar (AZAMİ DOZ) | Biber/Hıyar Pamuk Yaprakkurdu: 25 - 30 gr / Dekar | Bağ Salkım Güvesi: 25 gr / 100 L su | Elma İçkurdu: 20 - 25 gr / 100 L su | Pamukta Yeşilkurt: 20 - 25 gr / Dekar',
        'uygulama_zamani': 'Tuzaklarda ilk erginler yakalandığında ve yapraklarda ilk yumurtalar açıldığında uygulanır.',
        'hasat_arasi_sure': '3 - 7 Gün',
        'onemli_notlar': '🟢 Translaminar etkilidir; yaprak dokusu içine geçer ve larvayı beslenirken hemen felç eder.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Domates Güvesi (Tuta absoluta) ve Yeşilkurt', 'dozaj': '25 - 30 gr / Dekar', 'doz_miktar': 30},
            {'zararli': 'Biber ve Hıyarda Pamuk Yaprakkurdu (Spodoptera)', 'dozaj': '25 - 30 gr / Dekar', 'doz_miktar': 30},
            {'zararli': 'Bağ Salkım Güvesi (Lobesia botrana)', 'dozaj': '25 gr / 100 L su', 'doz_miktar': 25},
            {'zararli': 'Elmada Elma İçkurdu (Cydia pomonella)', 'dozaj': '20 - 25 gr / 100 L su', 'doz_miktar': 25},
            {'zararli': 'Pamukta Yeşilkurt ve Dikenlikurt', 'dozaj': '20 - 25 gr / Dekar', 'doz_miktar': 25},
            {'zararli': 'Mısırda Koçankurdu ve Mısır Kurdu', 'dozaj': '20 gr / Dekar', 'doz_miktar': 20}
        ]
    },
    {
        'keywords': ['taldex', 'metaldehyde', 'metaldehit', 'snail', 'limacid'],
        'urun_grubu': 'Molluskisit (Salyangoz ve Sümüklüböcek Yemi)',
        'etken_madde': 'Metaldehyde (%5 Hazır Pellet Yem)',
        'hedef_zararlilar': 'Salyangozlar (Helix aspersa) ve Sümüklüböcekler (Deroceras reticulatum)',
        'en_yuksek_doz_birim': 'gr/Dekar',
        'maksimum_dozaj': 'Maksimum 700 gr / Dekar',
        'dozaj_detay': 'Sebze, Çilek ve Bağda Ağır Bulaşmalarda: 500 - 700 gr / Dekar (AZAMİ DOZ) | Meyve Bahçelerinde Ağaç Çevresi: 400 - 500 gr / Dekar | Hububat ve Tarla Bitkileri: 300 - 400 gr / Dekar',
        'uygulama_zamani': 'Yağış sonrası veya sulama ardından toprak nemliyken akşam saatlerinde ocaklar halinde serpilir.',
        'hasat_arasi_sure': '7 Gün',
        'onemli_notlar': '⚠️ Pelletler suyla erimeye dayanıklıdır. Doğrudan bitki üzerine değil, sıra aralarına ve kök boğazı yakınına uygulanmalıdır.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Sebze, Meyve ve Bağda Salyangoz (Helix spp.) Yoğun İstilası', 'dozaj': '500 - 700 gr / Dekar', 'doz_miktar': 700},
            {'zararli': 'Çilek ve Serada Sümüklüböcek (Deroceras spp.)', 'dozaj': '400 - 500 gr / Dekar', 'doz_miktar': 500},
            {'zararli': 'Narenciye ve Fındıkta Salyangoz Mücadelesi', 'dozaj': '400 gr / Dekar', 'doz_miktar': 400},
            {'zararli': 'Hububat ve Tarla Bitkilerinde Salyangoz', 'dozaj': '300 - 400 gr / Dekar', 'doz_miktar': 350}
        ]
    },
    {
        'keywords': ['veliyette', 'aliette', 'fosetyl', 'fosetil'],
        'urun_grubu': 'İki Yönlü Sistemik Koruyucu ve Tedavi Edici Fungisit',
        'etken_madde': 'Fosetyl-Al (%80 WP/WG)',
        'hedef_zararlilar': 'Mildiyö, Uçkurutan, Kök boğazı çürüklüğü, Ateş yanıklığı',
        'en_yuksek_doz_birim': 'gr/Dekar',
        'maksimum_dozaj': 'Maksimum 250 gr / Dekar',
        'dozaj_detay': 'Hıyar ve Kavunda Mildiyö: 200 - 250 gr / Dekar (AZAMİ DOZ) | Turunçgilde Uçkurutan / Zamklanma: 200 - 250 gr / 100 L su | Domateste Mildiyö: 200 gr / Dekar | Elmada Ateş Yanıklığı: 150 - 200 gr / 100 L su | Soğan Mildiyösü: 150 - 200 gr / Dekar',
        'uygulama_zamani': 'Sürgün gelişimi döneminde yapraktan veya damlama sulamayla uygulanır.',
        'hasat_arasi_sure': '3 - 14 Gün',
        'onemli_notlar': 'ℹ️ Hem yukarı hem aşağı taşınabilen (çift yönlü) nadir sistemik ilaçlardandır.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Hıyar ve Sebzelerde Mildiyö (Pseudoperonospora cubensis)', 'dozaj': '200 - 250 gr / Dekar', 'doz_miktar': 250},
            {'zararli': 'Turunçgilde Uçkurutan ve Zamklanma (Phytophthora)', 'dozaj': '200 - 250 gr / 100 L su', 'doz_miktar': 250},
            {'zararli': 'Domateste Mildiyö (Phytophthora infestans)', 'dozaj': '200 gr / Dekar', 'doz_miktar': 200},
            {'zararli': 'Elma ve Armutta Ateş Yanıklığı (Erwinia amylovora)', 'dozaj': '150 - 200 gr / 100 L su', 'doz_miktar': 200},
            {'zararli': 'Soğan Mildiyösü (Peronospora destructor)', 'dozaj': '150 - 200 gr / Dekar', 'doz_miktar': 180}
        ]
    },
    {
        'keywords': ['ribasso', 'tagula', 'nicosulfuron', 'foramsulfuron', 'kelvin', 'accent'],
        'urun_grubu': 'Mısır Seçici Çıkış Sonrası (Post-em) Sistemik Herbisit',
        'etken_madde': 'Nicosulfuron (40 g/L OD/SC)',
        'hedef_zararlilar': 'Mısırda dar ve geniş yapraklı yabancı otlar (Kanyaş, Darıcan, Yapışkanot, Horozibiği)',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 150 ml / Dekar',
        'dozaj_detay': 'Gelişmiş Çok Yıllık Kanyaşta (Sorghum halepense): 125 - 150 ml / Dekar (AZAMİ DOZ) | Tek Yıllık Dar Yapraklılar (Darıcan, Yapışkanot): 100 - 125 ml / Dekar | Geniş Yapraklı Otlar (Sirken, Horozibiği): 100 ml / Dekar',
        'uygulama_zamani': 'Mısırın 4-8 yapraklı, kanyaşların 15-25 cm boyda olduğu evrede uygulanır.',
        'hasat_arasi_sure': '60 Gün',
        'onemli_notlar': '⚠️ Mısır bitkisine zarar vermez (selektiftir). Tatlı mısır ve patlatmalık mısırda kullanılmaz.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Mısırda Gelişmiş Çok Yıllık Kanyaş (Sorghum halepense)', 'dozaj': '125 - 150 ml / Dekar', 'doz_miktar': 150},
            {'zararli': 'Mısırda Tek Yıllık Dar Yapraklı Otlar (Darıcan, Yapışkanot)', 'dozaj': '100 - 125 ml / Dekar', 'doz_miktar': 125},
            {'zararli': 'Mısırda Geniş Yapraklı Yabancı Otlar (Sirken, Horozibiği, Pıtrak)', 'dozaj': '100 ml / Dekar', 'doz_miktar': 100},
            {'zararli': 'Mısırda Tohum Kanyaşı ve Yabani Darı', 'dozaj': '75 - 100 ml / Dekar', 'doz_miktar': 90}
        ]
    },
    {
        'keywords': ['masnamen', 'dithane', 'mancozeb', 'manzate', 'sancozeb', 'korzeb', 'penncozeb'],
        'urun_grubu': 'Geniş Spektrumlu Ditiyokarbamat Koruyucu Fungisit',
        'etken_madde': 'Mancozeb (%80 WP/WG)',
        'hedef_zararlilar': 'Mildiyö, Karaleke, Monilya, Pas, Alternaria yaprak yanıklığı',
        'en_yuksek_doz_birim': 'gr/Dekar',
        'maksimum_dozaj': 'Maksimum 350 gr / Dekar',
        'dozaj_detay': 'Patates ve Domateste Mildiyö: 300 - 350 gr / Dekar (AZAMİ DOZ) | Elmada Karaleke: 250 - 300 gr / 100 L su | Bağda Mildiyö ve Ölükol: 200 - 250 gr / 100 L su | Sert Çekirdeklilerde Yaprak Delen (Çil): 200 - 250 gr / 100 L su | Soğan Mildiyösü: 200 gr / Dekar',
        'uygulama_zamani': 'Hastalık koşulları oluştuğunda yağışlardan önce koruyucu olarak uygulanır.',
        'hasat_arasi_sure': '14 - 28 Gün',
        'onemli_notlar': '⚠️ Kontak etkilidir. Azami kota için "Patates ve Domates Mildiyösü (350 gr/da)" seçilmelidir.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Patates ve Domateste Mildiyö (Phytophthora infestans)', 'dozaj': '300 - 350 gr / Dekar', 'doz_miktar': 350},
            {'zararli': 'Elma Karalekesi (Venturia inaequalis)', 'dozaj': '250 - 300 gr / 100 L su', 'doz_miktar': 300},
            {'zararli': 'Bağda Mildiyö (Plasmopara) ve Ölükol (Phomopsis)', 'dozaj': '200 - 250 gr / 100 L su', 'doz_miktar': 250},
            {'zararli': 'Sert Çekirdekli Meyvelerde Yaprak Delen (Çil Hastalığı)', 'dozaj': '200 - 250 gr / 100 L su', 'doz_miktar': 250},
            {'zararli': 'Soğan Mildiyösü ve Sebzede Erken Yaprak Yanıklığı (Alternaria)', 'dozaj': '200 gr / Dekar', 'doz_miktar': 200}
        ]
    },
    {
        'keywords': ['decis', 'deltis', 'deltamethrin', 'deltharin', 'pole'],
        'urun_grubu': 'Geniş Spektrumlu Sentetik Piretroid İnsektisit',
        'etken_madde': 'Deltamethrin (25 g/L EC)',
        'hedef_zararlilar': 'Bağ salkım güvesi, Elma içkurdu, Yeşilkurt, Süne, Kımıl, Tırtıllar, Fındık kurdu',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 50 ml / Dekar',
        'dozaj_detay': 'Bağ Salkım Güvesi ve Elma İçkurdu: 50 ml / Dekar (AZAMİ DOZ) | Pamuk/Domates Yeşilkurt: 40 - 50 ml / Dekar | Hububatta Süne ve Kımıl: 30 - 50 ml / Dekar | Fındık Kurdu: 30 - 40 ml / Dekar | Mısır Çizgili Yaprakkurdu: 40 ml / Dekar | Yaprakbitleri/Trips: 25 - 30 ml / Dekar',
        'uygulama_zamani': 'Larva çıkış döneminde sabah veya akşam serinliğinde kaplama olarak uygulanır.',
        'hasat_arasi_sure': '3 - 7 Gün',
        'onemli_notlar': '⚠️ Çiçeklenme döneminde arılara dikkat edilmelidir. Azami kota için "Salkım Güvesi / Yeşilkurt (50 ml/da)" seçiniz.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Bağ Salkım Güvesi (Lobesia botrana) ve Elma İçkurdu', 'dozaj': '50 ml / Dekar', 'doz_miktar': 50},
            {'zararli': 'Pamuk ve Domateste Yeşilkurt ve Dikenlikurt', 'dozaj': '40 - 50 ml / Dekar', 'doz_miktar': 50},
            {'zararli': 'Mısırda Çizgili Yaprakkurdu ve Koçankurdu', 'dozaj': '40 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Hububatta Süne ve Kımıl Mücadelesi', 'dozaj': '30 - 50 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Fındık Kurdu (Curculio nucum) ve Kokarca', 'dozaj': '30 - 40 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Sebzelerde Yeşilkurt ve Bozkurt', 'dozaj': '30 - 40 ml / Dekar', 'doz_miktar': 35},
            {'zararli': 'Yaprak Bitleri, Tripsler ve Pireler', 'dozaj': '25 - 30 ml / Dekar', 'doz_miktar': 30}
        ]
    },
    {
        'keywords': ['mospilan', 'goldplan', 'acetamiprid', 'mosplan', 'profile', 'gallant'],
        'urun_grubu': 'Neonikotinoid Sistemik İnsektisit (Emici Böcek İlacı)',
        'etken_madde': 'Acetamiprid (%20 SP/WP)',
        'hedef_zararlilar': 'Beyazsinek, Unlubit, Kabuklubit, Yaprak biti, Patates böceği, Kiraz sineği',
        'en_yuksek_doz_birim': 'gr/Dekar',
        'maksimum_dozaj': 'Maksimum 40 gr / Dekar',
        'dozaj_detay': 'Domates ve Sebzede Beyazsinek ve Unlubit: 35 - 40 gr / Dekar (AZAMİ DOZ) | Meyvede Kabuklubit ve Virgül Kabuklubiti: 30 - 40 gr / 100 L su | Pamukta Yaprak Biti: 25 - 30 gr / Dekar | Kiraz Sineği: 20 - 25 gr / 100 L su | Patates Böceği: 15 - 20 gr / Dekar',
        'uygulama_zamani': 'Zararlı popülasyonu ilk görüldüğünde sistemik yaprak uygulaması yapılır.',
        'hasat_arasi_sure': '3 - 7 Gün',
        'onemli_notlar': 'ℹ️ Sistemik özelliktedir; bitki özsuyuna geçerek yeni sürgünleri de korur. Yüksek çıkış kotası için Beyazsinek/Unlubit seçilmelidir.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Domates ve Sebzede Beyazsinek (Bemisia tabaci)', 'dozaj': '35 - 40 gr / Dekar', 'doz_miktar': 40},
            {'zararli': 'Turunçgil ve Meyvede Unlubit (Pseudococcidae)', 'dozaj': '30 - 40 gr / 100 L su', 'doz_miktar': 40},
            {'zararli': 'Meyve Ağaçlarında Virgül Kabuklubiti ve Dut Kabuklubiti', 'dozaj': '25 - 30 gr / 100 L su', 'doz_miktar': 30},
            {'zararli': 'Pamukta Yaprak Biti ve Beyazsinek', 'dozaj': '25 - 30 gr / Dekar', 'doz_miktar': 30},
            {'zararli': 'Kiraz Sineği ve Zeytin Sineği', 'dozaj': '20 - 25 gr / 100 L su', 'doz_miktar': 25},
            {'zararli': 'Patates Böceği ve Yaprak Bitleri', 'dozaj': '15 - 20 gr / Dekar', 'doz_miktar': 20}
        ]
    },
    {
        'keywords': ['agrimec', 'apache', 'abamectin', 'beramec', 'vertimec', 'voliam targo'],
        'urun_grubu': 'Akarisit & İnsektisit (Kırmızı Örümcek & Gal Arısı İlacı)',
        'etken_madde': 'Abamectin (18 g/L EC)',
        'hedef_zararlilar': 'Kırmızı örümcekler, Pas akarı, Yaprak galeri sineği, Çilek akarı',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 50 ml / Dekar',
        'dozaj_detay': 'Meyve Ağaçlarında Pas Akarı ve Kırmızı Örümcek: 50 ml / Dekar (50 ml / 100 L su) (AZAMİ DOZ) | Sebzede Yaprak Galeri Sineği: 40 - 50 ml / Dekar | Sebzede İki Noktalı Kırmızı Örümcek: 30 - 40 ml / Dekar | Çilek Akarı: 30 - 35 ml / Dekar',
        'uygulama_zamani': 'Yaprak başına ortalama 3-5 adet hareketli akar görüldüğünde yaprak altlarını iyice ıslatarak uygulanır.',
        'hasat_arasi_sure': '3 Gün',
        'onemli_notlar': 'ℹ️ Translaminar etkilidir. İlaçlama sırasında yaprak altlarının tam ıslanması şarttır.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Meyve Ağaçlarında Pas Akarı ve Kırmızı Örümcekler', 'dozaj': '50 ml / Dekar', 'doz_miktar': 50},
            {'zararli': 'Sebzelerde Yaprak Galeri Sineği (Liriomyza spp.)', 'dozaj': '40 - 50 ml / Dekar', 'doz_miktar': 45},
            {'zararli': 'Sebzelerde İki Noktalı Kırmızı Örümcek (Tetranychus)', 'dozaj': '30 - 40 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Pamukta Kırmızı Örümcek Mücadelesi', 'dozaj': '35 - 40 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Çilekte Kırmızı Örümcek ve Çilek Akarı', 'dozaj': '30 - 35 ml / Dekar', 'doz_miktar': 35},
            {'zararli': 'Avrupa Kırmızı Örümceği (Panonychus ulmi)', 'dozaj': '25 - 30 ml / 100 L su', 'doz_miktar': 30}
        ]
    },
    {
        'keywords': ['coragen', 'altacor', 'chlorantraniliprole', 'voliam flexi'],
        'urun_grubu': 'Modern Kurt & Güve İnsektisiti (Ryanodine Reseptör Modülatörü)',
        'etken_madde': 'Chlorantraniliprole (200 g/L SC)',
        'hedef_zararlilar': 'Domates güvesi (Tuta absoluta), Mısır koçankurdu, Yeşilkurt, Elma içkurdu',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 20 ml / Dekar',
        'dozaj_detay': 'Mısırda Koçankurdu ve Mısır Kurdu: 20 ml / Dekar (AZAMİ DOZ) | Domateste Tuta absoluta ve Yeşilkurt: 17.5 - 20 ml / Dekar | Pamukta Yeşilkurt: 15 - 20 ml / Dekar | Elmada İçkurt: 15 ml / 100 L su',
        'uygulama_zamani': 'Tuzaklarda ergin çıkışları başladığında ve ilk yumurtalar açılmadan hemen önce uygulanır.',
        'hasat_arasi_sure': '1 Gün',
        'onemli_notlar': '🟢 Bombus arılarına ve faydalı böceklere karşı güvenlidir.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Mısır Koçankurdu ve Yeşilkurt (Helicoverpa / Ostrinia)', 'dozaj': '20 ml / Dekar', 'doz_miktar': 20},
            {'zararli': 'Domates Güvesi (Tuta absoluta) ve Yeşilkurt', 'dozaj': '17.5 - 20 ml / Dekar', 'doz_miktar': 20},
            {'zararli': 'Pamukta Yeşilkurt (Helicoverpa armigera)', 'dozaj': '15 - 20 ml / Dekar', 'doz_miktar': 18},
            {'zararli': 'Elma İçkurdu (Cydia pomonella)', 'dozaj': '15 ml / 100 L su', 'doz_miktar': 15},
            {'zararli': 'Bağ Salkım Güvesi (Lobesia botrana)', 'dozaj': '12 - 15 ml / 100 L su', 'doz_miktar': 15}
        ]
    },
    {
        'keywords': ['bakir', 'bakır', 'copper', 'cuprocaffaro', 'bordo', 'champion', 'kocide', 'funguran', 'vitra'],
        'urun_grubu': 'İnorganik Bakırlı Fungisit & Bakterisit',
        'etken_madde': 'Bakır Oksiklorür veya Bakır Hidroksit (%50)',
        'hedef_zararlilar': 'Mildiyö, Karaleke, Yaprak delen (Çil), Monilya, Dal yanıklığı, Halkalı leke',
        'en_yuksek_doz_birim': 'gr/Dekar',
        'maksimum_dozaj': 'Maksimum 2000 gr / Dekar',
        'dozaj_detay': 'Meyvelerde Kış İlaçlaması / Dal Yanıklığı: 1500 - 2000 gr / Dekar (AZAMİ DOZ) | Zeytinde Halkalı Leke: 1000 - 1500 gr / Dekar | Fındıkta Bakteriyel Yanıklık: 800 - 1000 gr / Dekar | Bağda Mildiyö: 400 - 500 gr / Dekar | Sebze Yaprak Delen / Karaleke: 300 - 400 gr / Dekar',
        'uygulama_zamani': 'Sonbaharda yaprak dökümünde (%75-80) ve ilkbaharda gözler patlamadan önce uygulanır.',
        'hasat_arasi_sure': '14 - 21 Gün',
        'onemli_notlar': '⚠️ Kış dönemi kürü seçildiğinde en yüksek çıkış miktarı onaylanır.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Meyve Ağaçlarında Kış Mücadelesi ve Dal Yanıklığı', 'dozaj': '1500 - 2000 gr / Dekar', 'doz_miktar': 2000},
            {'zararli': 'Zeytinde Halkalı Leke Hastalığı (Spilocaea oleagina)', 'dozaj': '1000 - 1500 gr / Dekar', 'doz_miktar': 1500},
            {'zararli': 'Fındıkta Bakteriyel Yanıklık (Xanthomonas arboricola)', 'dozaj': '800 - 1000 gr / Dekar', 'doz_miktar': 1000},
            {'zararli': 'Bağ Mildiyösü (Plasmopara viticola)', 'dozaj': '400 - 500 gr / Dekar', 'doz_miktar': 500},
            {'zararli': 'Sebze ve Meyvede Yaprak Delen (Çil) ve Karaleke', 'dozaj': '300 - 400 gr / Dekar', 'doz_miktar': 400}
        ]
    },
    {
        'keywords': ['score', 'difenoconazole', 'folicur', 'tebuconazole', 'topas', 'penconazole', 'tilt'],
        'urun_grubu': 'Sistemik ve Tedavi Edici Triazol Fungisit',
        'etken_madde': 'Difenoconazole / Tebuconazole (Triazol)',
        'hedef_zararlilar': 'Külleme, Karaleke, Monilya, Pas, Yaprak yanıklığı',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 50 ml / Dekar',
        'dozaj_detay': 'Hububatta Pas ve Külleme: 40 - 50 ml / Dekar (AZAMİ DOZ) | Şeftali ve Kirazda Monilya: 35 - 40 ml / Dekar | Sebzede Külleme ve Erken Yanıklık: 30 - 50 ml / Dekar | Elma Karalekesi: 25 - 30 ml / Dekar',
        'uygulama_zamani': 'İlk hastalık belirtilerinde koruyucu ve tedavi edici olarak uygulanır.',
        'hasat_arasi_sure': '14 Gün',
        'onemli_notlar': 'ℹ️ Sistemik etkilidir; bitki dokusuna 2 saat içinde nüfuz eder, yağıştan etkilenmez.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Hububat Sarı Pas, Kahverengi Pas ve Külleme', 'dozaj': '40 - 50 ml / Dekar', 'doz_miktar': 50},
            {'zararli': 'Şeftali ve Sert Çekirdeklilerde Monilya Hastalığı', 'dozaj': '35 - 40 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Sebzelerde Külleme ve Erken Yaprak Yanıklığı (Alternaria)', 'dozaj': '30 - 50 ml / Dekar', 'doz_miktar': 40},
            {'zararli': 'Elma Karalekesi (Venturia inaequalis)', 'dozaj': '25 - 30 ml / Dekar', 'doz_miktar': 30},
            {'zararli': 'Şeker Pancarında Cercospora Yaprak Lekesi', 'dozaj': '25 - 30 ml / Dekar', 'doz_miktar': 30}
        ]
    },
    {
        'keywords': ['2,4-d', 'aminofarm', 'esteron', 'weedkiller', 'ester', 'amin'],
        'urun_grubu': 'Selektif Hormon Terkibli Herbisit (Geniş Yapraklı Ot İlacı)',
        'etken_madde': '2,4-D Amin Tuzu (500 g/L)',
        'hedef_zararlilar': 'Hububat ve mısırda çok yıllık ve tek yıllık geniş yapraklı yabancı otlar (Köygöçüren, Sarmaşık, Sirken, Yabani Hardal)',
        'en_yuksek_doz_birim': 'ml/Dekar',
        'maksimum_dozaj': 'Maksimum 250 ml / Dekar',
        'dozaj_detay': 'Gelişmiş çok yıllık geniş yapraklılarda (Köygöçüren, Sarmaşık): 200 - 250 ml / Dekar (AZAMİ DOZ) | Mısırda İri Geniş Yapraklılar: 200 ml / Dekar | Genç tek yıllık otlarda: 150 - 200 ml / Dekar',
        'uygulama_zamani': 'Hububatın kardeşlenme sonu - sapa kalkma başlangıcında uygulanır.',
        'hasat_arasi_sure': '14 Gün',
        'onemli_notlar': '⚠️ Pamuk, domates ve bağ gibi geniş yapraklı kültür bitkilerine sürüklenmemelidir.',
        'ruhsatli_zararlilar': [
            {'zararli': 'Çok Yıllık Geniş Yapraklılar (Köygöçüren, Tarla Sarmaşığı)', 'dozaj': '200 - 250 ml / Dekar', 'doz_miktar': 250},
            {'zararli': 'Mısırda İri Yapılı Geniş Yapraklı Yabancı Otlar', 'dozaj': '175 - 200 ml / Dekar', 'doz_miktar': 200},
            {'zararli': 'Tek Yıllık Geniş Yapraklı Otlar (Sirken, Yabani Hardal)', 'dozaj': '150 - 200 ml / Dekar', 'doz_miktar': 180},
            {'zararli': 'Yabani Turp ve Papatya Türleri', 'dozaj': '150 ml / Dekar', 'doz_miktar': 150}
        ]
    }
]

@app.route('/api/urun/dozaj_bilgisi', methods=['GET', 'POST'])
def api_urun_dozaj_bilgisi():
    """
    Ürün adı, GTIN veya Karekod bazlı hedef zararlı organizma, azami dozaj ve
    sistemden en fazla ilaç çıkışı yapmayı sağlayan tavsiye edilen zararlıyı döndürür.
    Ruhsatlı zararlılar listesi DOZAJA GÖRE BÜYÜKTEN KÜÇÜĞE SIRALANIR, böylece
    en üstteki her zaman en yüksek kotayı veren zararlı olur.
    """
    try:
        if request.method == 'POST':
            data = request.json or {}
            urun_adi = str(data.get('urun_adi', '')).strip()
            gtin = str(data.get('gtin', '')).strip()
            karekod = str(data.get('karekod', '')).strip()
            dekar_val = data.get('dekar', 0)
        else:
            urun_adi = str(request.args.get('urun_adi', '')).strip()
            gtin = str(request.args.get('gtin', '')).strip()
            karekod = str(request.args.get('karekod', '')).strip()
            dekar_val = request.args.get('dekar', 0)

        try:
            dekar = float(dekar_val) if dekar_val else 0.0
        except Exception:
            dekar = 0.0

        # Eğer karekod verilmişse cache'ten ürün ve GTIN bul
        if karekod and not urun_adi:
            norm_qr = normalize_qr(karekod)
            df_cache, qr_map, gtin_map, _ = get_bkst_cache()
            match_row = resolve_product_from_cache(norm_qr, df_cache, qr_map, gtin_map)
            if match_row:
                urun_adi = str(match_row.get('Ürün Adı', '')).strip()
                if not gtin:
                    gtin = str(match_row.get('Gtin Numarası', '')).strip()

        # Karekodun daha önce satılıp satılmadığını satis_arsivi tablosundan kontrol et
        daha_once_satildi = False
        onceki_satis = None
        if karekod:
            try:
                norm_qr = normalize_qr(karekod)
                conn_chk = sqlite3.connect(DB_PATH, timeout=10.0)
                c_chk = conn_chk.cursor()
                c_chk.execute("""
                    SELECT tarih, urun_adi, seri_no, kullanici_adi 
                    FROM satis_arsivi 
                    WHERE ((ham_karekod = ? AND ham_karekod != '') 
                       OR (ham_karekod = ? AND ham_karekod != '')) 
                    AND durum = 'RECETESIZ_SATIS_BKST'
                    LIMIT 1
                """, (norm_qr, karekod))
                row_chk = c_chk.fetchone()
                if row_chk:
                    daha_once_satildi = True
                    onceki_satis = {
                        'tarih': row_chk[0],
                        'urun_adi': row_chk[1],
                        'seri_no': row_chk[2],
                        'kullanici': row_chk[3]
                    }
                conn_chk.close()
            except Exception as e:
                logger.warning(f"satis_arsivi duplicate check error: {e}")

        # Eğer sadece GTIN verilmişse cache'ten ürün adını bul
        if not urun_adi and gtin:
            df_cache, _, gtin_map, _ = get_bkst_cache()
            if gtin in gtin_map:
                urun_adi = str(gtin_map[gtin].get('Ürün Adı', ''))

        if not urun_adi and not gtin and not karekod:
            return jsonify({'success': False, 'error': 'Ürün adı, barkod veya karekod parametresi gereklidir.'})

        urun_adi_lower = urun_adi.lower()

        # 1. Bilgi tabanımızdan en iyi eşleşmeyi ara
        best_match = None
        for item in PESTICIDE_KNOWLEDGE_BASE:
            for kw in item['keywords']:
                if kw in urun_adi_lower:
                    best_match = item
                    break
            if best_match:
                break

        if best_match:
            ruhsatli_list = []
            for r in best_match.get('ruhsatli_zararlilar', []):
                doz_m = r.get('doz_miktar')
                if not doz_m:
                    m = re.search(r'(\d+)', str(r.get('dozaj', '')))
                    doz_m = int(m.group(1)) if m else 50
                ruhsatli_list.append({
                    'zararli': r.get('zararli', ''),
                    'zararlisi': r.get('zararli', ''),
                    'dozaj': r.get('dozaj', ''),
                    'doz_miktar': doz_m,
                    'birim_dekar_litre': doz_m / 1000.0,
                    'is_max': False
                })

            # KESİN VE GARANTİLİ: Dozaj miktarına göre azalan (büyükten küçüğe) sırala
            ruhsatli_list.sort(key=lambda x: x['doz_miktar'], reverse=True)
            if ruhsatli_list:
                ruhsatli_list[0]['is_max'] = True
                max_miktar = ruhsatli_list[0]['doz_miktar']
                en_cok_zararli = ruhsatli_list[0]['zararli']
                max_birim = best_match.get('en_yuksek_doz_birim', 'ml/Dekar')
                en_yuksek_doz_metin = ruhsatli_list[0]['dozaj']
            else:
                max_miktar = best_match.get('en_yuksek_doz_miktar', 50)
                en_cok_zararli = best_match.get('en_yuksek_doz_zararli', '')
                max_birim = best_match.get('en_yuksek_doz_birim', 'ml/Dekar')
                en_yuksek_doz_metin = f"{max_miktar} {max_birim}"

            # Dekar bazlı çıkış kapasitesi hesabı
            kapasite = {}
            if dekar > 0 and max_miktar > 0:
                toplam_doz = dekar * max_miktar
                toplam_hacim = toplam_doz / 1000.0
                tahmini_kutu = int(toplam_hacim + 0.99) if toplam_hacim > 0 else 0
                kapasite = {
                    'dekar': dekar,
                    'toplam_izin_verilen_doz': f"{toplam_doz:.1f} {max_birim.split('/')[0]}",
                    'toplam_hacim_litre_kg': f"{toplam_hacim:.2f} L / Kg",
                    'tahmini_azami_kutu': tahmini_kutu,
                    'bilgi_mesaji': f"🎯 Seçili {dekar:.1f} dekar tarla için '{en_cok_zararli}' seçildiğinde sistemden en fazla {toplam_hacim:.1f} Litre ({tahmini_kutu} Kutu) çıkış kotası alınabilir."
                }

            return jsonify({
                'success': True,
                'bulundu': True,
                'kaynak': 'BKU Akıllı Dozaj Kılavuzu (T.C. Tarım ve Orman Bakanlığı Standartları)',
                'urun_adi': urun_adi,
                'urun_grubu': best_match['urun_grubu'],
                'etken_madde': best_match['etken_madde'],
                'hedef_zararlilar': best_match['hedef_zararlilar'],
                'en_yuksek_doz_zararli': en_cok_zararli,
                'en_yuksek_doz_miktar': max_miktar,
                'en_yuksek_doz_birim': max_birim,
                'en_yuksek_doz_metin': en_yuksek_doz_metin,
                'maksimum_dozaj': best_match.get('maksimum_dozaj', f"Maksimum {max_miktar} {max_birim}"),
                'dozaj_detay': best_match.get('dozaj_detay', ''),
                'uygulama_zamani': best_match.get('uygulama_zamani', ''),
                'hasat_arasi_sure': best_match.get('hasat_arasi_sure', '7 Gün'),
                'onemli_notlar': best_match.get('onemli_notlar', ''),
                'ruhsatli_zararlilar': ruhsatli_list,
                'kapasite_hesabi': kapasite,
                'daha_once_satildi': daha_once_satildi,
                'onceki_satis': onceki_satis
            })

        # 2. Genel Kategori Tahmini (5-6 alternatif hedef zararlı ile zenginleştirilmiş)
        cat_name = "Bitki Koruma Ürünü"
        zararlilar = "Hedeflenen Kültür Bitkisi Zararlıları, Hastalıkları veya Yabancı Otlar"
        max_doz = "Tavsiye Edilen Etiket Dozajı Aşılmamalıdır"
        doz_detay = "Dekara 100-200 L su hesabı ile ambalaj etiketinde belirtilen dozda homojen uygulama yapılmalıdır."
        uyg_zaman = "Sabahın erken saatlerinde veya akşam serinliğinde, rüzgarsız havada uygulanmalıdır."
        max_birim = "ml/Dekar"
        fallback_pests = []

        if any(w in urun_adi_lower for w in ['ot', 'herbisit', 'weed']):
            cat_name = "Herbisit (Yabancı Ot İlacı)"
            zararlilar = "Tek ve çok yıllık dar ve geniş yapraklı yabancı otlar"
            fallback_pests = [
                {'zararli': 'Çok Yıllık Köklü Yabancı Otlar (Topalak, Kanyaş, Ayrık)', 'dozaj': '600 ml / Dekar', 'doz_miktar': 600},
                {'zararli': 'Çalımsı ve Odunsu Bitkiler (Ağaç altı / Hendek)', 'dozaj': '500 ml / Dekar', 'doz_miktar': 500},
                {'zararli': 'Geniş Yapraklı Yabancı Otlar (Gelişmiş Dönem)', 'dozaj': '400 ml / Dekar', 'doz_miktar': 400},
                {'zararli': 'Dar Yapraklı Yabancı Otlar (Yabani Yulaf, Kuşyemi)', 'dozaj': '350 ml / Dekar', 'doz_miktar': 350},
                {'zararli': 'Genç Dönem Tek Yıllık Yabancı Otlar', 'dozaj': '250 ml / Dekar', 'doz_miktar': 250}
            ]
        elif any(w in urun_adi_lower for w in ['kurt', 'böcek', 'insektisit', 'bit', 'sinek', 'pire']):
            cat_name = "İnsektisit (Zararlı Böcek İlacı)"
            zararlilar = "Emici ve çiğneyici böcekler, yaprak bitleri, yeşilkurt, tripsler"
            fallback_pests = [
                {'zararli': 'Fındık Kurdu ve Meyve İçkurtları', 'dozaj': '50 ml / Dekar', 'doz_miktar': 50},
                {'zararli': 'Yeşilkurt ve Güve Larvaları (İleri Dönem)', 'dozaj': '45 ml / Dekar', 'doz_miktar': 45},
                {'zararli': 'Patates Böceği ve Kabuklubitler', 'dozaj': '40 ml / Dekar', 'doz_miktar': 40},
                {'zararli': 'Hububatta Süne Nimf ve Kımıl', 'dozaj': '35 ml / Dekar', 'doz_miktar': 35},
                {'zararli': 'Yaprak Bitleri ve Tripsler', 'dozaj': '25 ml / Dekar', 'doz_miktar': 25}
            ]
        elif any(w in urun_adi_lower for w in ['mantar', 'fungisit', 'mildiyö', 'külleme', 'leke', 'pas']):
            cat_name = "Fungisit (Mantar Hastalıkları İlacı)"
            max_birim = "gr/Dekar"
            zararlilar = "Mildiyö, külleme, karaleke, pas ve yaprak yanıklığı hastalıkları"
            fallback_pests = [
                {'zararli': 'Patates ve Domates Mildiyösü (İleri Dönem)', 'dozaj': '350 gr / Dekar', 'doz_miktar': 350},
                {'zararli': 'Karaleke ve Pas Hastalıkları', 'dozaj': '300 gr / Dekar', 'doz_miktar': 300},
                {'zararli': 'Külleme ve Yaprak Yanıklığı', 'dozaj': '250 gr / Dekar', 'doz_miktar': 250},
                {'zararli': 'Monilya ve Çiçek Yanıklığı', 'dozaj': '200 gr / Dekar', 'doz_miktar': 200},
                {'zararli': 'Koruyucu İlk Dönem Uygulaması', 'dozaj': '150 gr / Dekar', 'doz_miktar': 150}
            ]
        elif any(w in urun_adi_lower for w in ['örümcek', 'akarisit', 'akar']):
            cat_name = "Akarisit (Kırmızı Örümcek İlacı)"
            zararlilar = "İki noktalı kırmızı örümcek, Avrupa kırmızı örümceği, pas akarı"
            fallback_pests = [
                {'zararli': 'Pas Akarı ve Kırmızı Örümcek (Yoğun Popülasyon)', 'dozaj': '50 ml / Dekar', 'doz_miktar': 50},
                {'zararli': 'Sebzede Yaprak Galeri Sineği', 'dozaj': '45 ml / Dekar', 'doz_miktar': 45},
                {'zararli': 'İki Noktalı Kırmızı Örümcek', 'dozaj': '35 ml / Dekar', 'doz_miktar': 35},
                {'zararli': 'Avrupa Kırmızı Örümceği Nimfleri', 'dozaj': '25 ml / Dekar', 'doz_miktar': 25}
            ]
        else:
            fallback_pests = [
                {'zararli': 'Azami Dozaj İsteyen İleri Seviye Zararlı / Hastalık', 'dozaj': '100 ml / Dekar', 'doz_miktar': 100},
                {'zararli': 'Yoğun Bulaşma Dönemi Zirai Mücadele', 'dozaj': '75 ml / Dekar', 'doz_miktar': 75},
                {'zararli': 'Orta Yoğunlukta Zararlı Popülasyonu', 'dozaj': '50 ml / Dekar', 'doz_miktar': 50},
                {'zararli': 'Standart Koruyucu Mücadele Seviyesi', 'dozaj': '35 ml / Dekar', 'doz_miktar': 35}
            ]

        fallback_pests.sort(key=lambda x: x['doz_miktar'], reverse=True)
        fallback_pests[0]['is_max'] = True
        max_miktar = fallback_pests[0]['doz_miktar']
        en_cok_zararli = fallback_pests[0]['zararli']

        ruhsatli_fallback = []
        for r in fallback_pests:
            ruhsatli_fallback.append({
                'zararli': r['zararli'],
                'zararlisi': r['zararli'],
                'dozaj': r['dozaj'],
                'doz_miktar': r['doz_miktar'],
                'birim_dekar_litre': r['doz_miktar'] / 1000.0,
                'is_max': r.get('is_max', False)
            })

        kapasite = {}
        if dekar > 0 and max_miktar > 0:
            toplam_doz = dekar * max_miktar
            toplam_hacim = toplam_doz / 1000.0
            tahmini_kutu = int(toplam_hacim + 0.99) if toplam_hacim > 0 else 0
            kapasite = {
                'dekar': dekar,
                'toplam_izin_verilen_doz': f"{toplam_doz:.1f} {max_birim.split('/')[0]}",
                'toplam_hacim_litre_kg': f"{toplam_hacim:.2f} L / Kg",
                'tahmini_azami_kutu': tahmini_kutu,
                'bilgi_mesaji': f"🎯 Seçili {dekar:.1f} dekar tarla için azami doz ({max_miktar} {max_birim}) baz alınarak hesaplanmıştır."
            }

        return jsonify({
            'success': True,
            'bulundu': False,
            'kaynak': 'BKU Genel Zirai Dozaj Rehberi',
            'urun_adi': urun_adi,
            'urun_grubu': cat_name,
            'etken_madde': 'BKU Ruhsatlı Formülasyon',
            'hedef_zararlilar': zararlilar,
            'en_yuksek_doz_zararli': en_cok_zararli,
            'en_yuksek_doz_miktar': max_miktar,
            'en_yuksek_doz_birim': max_birim,
            'en_yuksek_doz_metin': f"{max_miktar} {max_birim}",
            'maksimum_dozaj': max_doz,
            'dozaj_detay': doz_detay,
            'uygulama_zamani': uyg_zaman,
            'ruhsatli_zararlilar': ruhsatli_fallback,
            'kapasite_hesabi': kapasite,
            'daha_once_satildi': daha_once_satildi,
            'onceki_satis': onceki_satis
        })
    except Exception as e:
        logger.error(f"api_urun_dozaj_bilgisi error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

def format_date_val(val):
    if not val or str(val).strip() in ["", "None", "nan", "NaN", "null"]:
        return ""
    val_str = str(val).strip()
    if "/Date(" in val_str:
        try:
            m = re.search(r'\d+', val_str)
            if m:
                ts = int(m.group()) / 1000.0
                return datetime.fromtimestamp(ts).strftime("%d.%m.%Y")
        except Exception:
            pass
    if "T" in val_str:
        try:
            clean_iso = val_str.split("T")[0]
            parts = clean_iso.split("-")
            if len(parts) == 3:
                return f"{parts[2]}.{parts[1]}.{parts[0]}"
        except Exception:
            pass
    return val_str

@app.route('/api/depo_kabul/gelen_listesi', methods=['POST', 'GET'])
def api_depo_kabul_gelen_listesi():
    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err:
        return jsonify({"success": False, "error": err})

    # Token'ı ReceivedNotificationList sayfasından al
    try:
        r_page = session.get("https://bkst.tarbil.gov.tr/Main/ReceivedNotificationList", verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        if token_match:
            token2 = token_match.group(1)
    except Exception:
        pass

    req_data = request.get_json(silent=True) or {}
    period = req_data.get('period') or request.args.get('period') or 'son_180'
    start_date = req_data.get('start_date') or request.args.get('start_date')
    end_date = req_data.get('end_date') or request.args.get('end_date')

    now = datetime.now()
    range_label = ""
    if period == 'son_30':
        start_date = (now - timedelta(days=30)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 30 Gün ({start_date} - {end_date})"
    elif period == 'son_90':
        start_date = (now - timedelta(days=90)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 90 Gün ({start_date} - {end_date})"
    elif period == 'son_180':
        start_date = (now - timedelta(days=180)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 6 Ay / Sezon ({start_date} - {end_date})"
    elif period in ['son_365', 'son_1_yil', 'bu_yil']:
        start_date = (now - timedelta(days=365)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 1 Yıl ({start_date} - {end_date})"
    elif period == 'tum':
        start_date = ""
        end_date = ""
        range_label = "Tüm Zamanlar"
    elif not start_date:
        # Varsayılan: Son 180 Gün (6 Ay / Sezon) - Yıl geçişlerinde (örneğin 2027 başında 2026 sonu faturaları) KESİNLİKLE kesilmez!
        start_date = (now - timedelta(days=180)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 6 Ay / Sezon ({start_date} - {end_date})"
    else:
        range_label = f"Özel Tarih ({start_date} - {end_date or 'Bugün'})"

    notifications = []
    try:
        payload = {
            "CompanyAddressId": gln_guid,
            "SenderGln": "",
            "DocumentNo": "",
            "StartDate": start_date or "",
            "EndDate": end_date or "",
            "NotificationType": "",
            "page": 1,
            "pageSize": 100,
            "__RequestVerificationToken": token2
        }
        res = session.post("https://bkst.tarbil.gov.tr/Main/GetReceivedNotificationList", data=payload, verify=SSL_VERIFY, timeout=(5, 15))
        if res.status_code == 200:
            jdata = res.json()
            raw_list = jdata.get("Data") if isinstance(jdata, dict) else (jdata if isinstance(jdata, list) else [])
            for item in raw_list:
                state = str(item.get("HEADERSTATE") or item.get("StateDescription") or "").upper()
                if "IPTAL" in state or "İPTAL" in state:
                    continue

                op_raw = str(item.get("OPERATION") or "MALALIM").upper()
                op_display = "MAL ALIM" if op_raw in ["SATIS", "MALALIM"] else op_raw

                notifications.append({
                    "HEADERID": item.get("HEADERID") or item.get("Id") or item.get("ID") or str(item.get("WAYBILLNUMBER", "")),
                    "WAYBILLNUMBER": item.get("WAYBILLNUMBER") or item.get("WaybillNumber") or item.get("BELGENO") or "-",
                    "WAYBILLDATE": format_date_val(item.get("WAYBILLDATE") or item.get("WaybillDate") or item.get("TARIH")),
                    "SENDER": item.get("CompanyTitle") or item.get("SENDER") or item.get("GonderenFirma") or item.get("FIRMA") or "Tedarikçi / Üretici",
                    "PRODUCTCOUNT": item.get("PRODUCTCOUNT") or item.get("ProductCount") or item.get("ADET") or 0,
                    "HEADERSTATE": "Stoğa Alınmış",
                    "OPERATION": op_display,
                    "products": []
                })

            # Her bildirimin ürün detaylarını sorgulayarak 'Kabul Bekliyor' mu yoksa 'Stoğa Alınmış' mı olduğunu belirle
            _bkst_fetch_lock = threading.Lock()

            def resolve_header_state(notif):
                h_id = notif.get("HEADERID")
                if not h_id:
                    return h_id, "Stoğa Alınmış", 0
                try:
                    with _bkst_fetch_lock:
                        r = session.post(
                            "https://bkst.tarbil.gov.tr/Main/GetNotificationDetailList",
                            data={"CompanyAddressId": gln_guid, "HeaderId": h_id, "__RequestVerificationToken": token2},
                            verify=SSL_VERIFY,
                            timeout=(3, 8)
                        )
                    if r.status_code == 200:
                        d = r.json()
                        d_list = d if isinstance(d, list) else (d.get("Data", []) if isinstance(d, dict) else [])
                        waiting_cnt = sum(1 for x in d_list if "ALIMA UYGUN" in str(x.get("DETAILSTATE", "")).upper() and "DEĞİL" not in str(x.get("DETAILSTATE", "")).upper() and "DEGIL" not in str(x.get("DETAILSTATE", "")).upper())

                        # Eski/Arşiv Bildirim Koruması: 180 günden eski faturalar ticari/yasal olarak
                        # beklemede olamaz. Bakanlık sisteminden silinmiş veya zaman aşımına uğramış
                        # hayalet kayıtların yanlışlıkla 'Kabul Bekliyor' olarak öne çıkmasını engelle.
                        w_date_str = str(notif.get("WAYBILLDATE") or "")
                        is_ancient = False
                        try:
                            d_parts = w_date_str.split(".")
                            if len(d_parts) == 3:
                                notif_dt = datetime(int(d_parts[2]), int(d_parts[1]), int(d_parts[0]))
                                if (datetime.now() - notif_dt).days > 180:
                                    is_ancient = True
                        except Exception:
                            pass

                        if waiting_cnt > 0 and not is_ancient:
                            return h_id, "Kabul Bekliyor", waiting_cnt
                        return h_id, "Stoğa Alınmış", 0
                except Exception as e:
                    logger.warning(f"resolve_header_state hatası (h_id={h_id}): {e}")
                return h_id, "Stoğa Alınmış", 0

            # Bildirimleri kontrollü eşzamanlı sorgula (max_workers=5)
            state_map = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                for h_id, state_str, w_cnt in executor.map(resolve_header_state, notifications):
                    state_map[h_id] = (state_str, w_cnt)

            for n in notifications:
                h_id = n.get("HEADERID")
                if h_id in state_map:
                    st, w_cnt = state_map[h_id]
                    n["HEADERSTATE"] = st
                    n["WAITINGCOUNT"] = w_cnt
                else:
                    n["HEADERSTATE"] = "Stoğa Alınmış"
                    n["WAITINGCOUNT"] = 0

            # Kabul Bekleyen bildirimleri en başa getir (kullanıcı hemen görsün)
            notifications.sort(key=lambda x: 0 if x.get("HEADERSTATE") == "Kabul Bekliyor" else 1)

    except Exception as e:
        logger.error(f"BKST gelen bildirim hatası: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"BKST sunucusundan bildirimler çekilirken hata oluştu: {str(e)}"})

    kabul_bekleyen_sayisi = sum(1 for n in notifications if n.get("HEADERSTATE") == "Kabul Bekliyor")
    msg = f"Toplam {len(notifications)} bildirim incelendi [{range_label}]. ({kabul_bekleyen_sayisi} adet Kabul Bekliyor, {len(notifications)-kabul_bekleyen_sayisi} adet Stoğa Alınmış)" if notifications else f"Belirtilen dönemde [{range_label}] gelen bildirim bulunamadı."

    return jsonify({
        "success": True,
        "notifications": notifications,
        "kabul_bekleyen_sayisi": kabul_bekleyen_sayisi,
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "range_label": range_label,
        "message": msg
    })

@app.route('/api/depo_kabul/detay/<header_id>', methods=['GET'])
def api_depo_kabul_detay(header_id):
    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err:
        return jsonify({"success": False, "error": err, "products": []})

    try:
        r_page = session.get("https://bkst.tarbil.gov.tr/Main/ReceivedNotificationList", verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        if token_match:
            token2 = token_match.group(1)
    except Exception:
        pass

    products = []
    waiting_count = 0
    in_stock_count = 0
    try:
        res = session.post(
            "https://bkst.tarbil.gov.tr/Main/GetNotificationDetailList",
            data={"CompanyAddressId": gln_guid, "HeaderId": header_id, "__RequestVerificationToken": token2},
            verify=SSL_VERIFY,
            timeout=(5, 15)
        )
        if res.status_code == 200:
            jdata = res.json()
            raw_list = jdata if isinstance(jdata, list) else (jdata.get("Data", []) if isinstance(jdata, dict) else [])
            for item in raw_list:
                gtin = item.get("BARCODE") or item.get("GTIN") or item.get("Gtin") or ""
                qr = item.get("KAREKOD") or item.get("HAMKAREKOD") or item.get("Barcode") or ""
                seri = item.get("SERIALNUMBER") or item.get("SERINO") or item.get("SerialNumber") or ""
                parti = item.get("LOTNUMBER") or item.get("SARJNO") or item.get("LOT") or item.get("BatchNumber") or ""
                koli = item.get("CARRIERLABEL1") or item.get("PAKETNO") or item.get("KOLINO") or ""
                palet = item.get("CARRIERLABEL2") or item.get("PALETNO") or ""
                urun_adi = item.get("STOCKNAME") or item.get("URUNADI") or item.get("ProductName") or "Bitki Koruma Ürünü"
                ur_tarih = format_date_val(item.get("PRODUCTIONDATE") or item.get("URETIMTARIHI") or item.get("ProductionDate"))
                skt_val = format_date_val(item.get("SKT") or item.get("ExpirationDate"))

                d_state = str(item.get("DETAILSTATE") or "").upper()
                if "ALIMA UYGUN" in d_state and "DEĞİL" not in d_state and "DEGIL" not in d_state:
                    product_durum = "Kabul Bekliyor"
                    waiting_count += 1
                else:
                    product_durum = "Stoğa Alınmış"
                    in_stock_count += 1

                products.append({
                    "Koli Numarası": koli,
                    "Ürün Adı": urun_adi,
                    "Karekod": qr,
                    "Gtin / Barkod": gtin,
                    "gtin": gtin,
                    "Seri Numarası": seri,
                    "Parti Numarası": parti,
                    "Palet Numarası": palet,
                    "Üretim Tarihi": ur_tarih,
                    "Son Kullanma Tarihi": skt_val,
                    "durum": product_durum
                })
    except Exception as e:
        logger.error(f"Detail fetch error: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"Detay çekilirken hata oluştu: {str(e)}", "products": []})

    overall_status = "Kabul Bekliyor" if waiting_count > 0 else "Stoğa Alınmış"

    return jsonify({
        "success": True,
        "products": products,
        "overall_status": overall_status,
        "bekleyen_adet": waiting_count,
        "stoktaki_adet": in_stock_count
    })

@app.route('/api/depo_kabul/onayla', methods=['POST'])
def api_depo_kabul_onayla():
    req_data = request.get_json() or {}
    header_id = req_data.get('header_id')
    incoming_products = req_data.get('products') or []

    session, gln_guid, token2, err = get_bkst_authenticated_session()

    # Eğer ön yüzden ürün listesi boş geldiyse arka planda detay servisini çağır
    if not incoming_products and header_id and session and gln_guid:
        try:
            r_detail = session.post(
                "https://bkst.tarbil.gov.tr/Main/GetNotificationDetailList",
                data={"CompanyAddressId": gln_guid, "HeaderId": header_id, "__RequestVerificationToken": token2},
                verify=SSL_VERIFY,
                timeout=(5, 15)
            )
            if r_detail.status_code == 200:
                jd = r_detail.json()
                incoming_products = jd if isinstance(jd, list) else (jd.get("Data", []) if isinstance(jd, dict) else [])
        except Exception as e_fetch:
            logger.warning(f"api_depo_kabul_onayla: Otomatik detay çekme hatası: {e_fetch}")

    bkst_msg = ""
    if session and gln_guid and token2 and header_id:
        accept_endpoints = [
            "https://bkst.tarbil.gov.tr/Main/NotificationAccept",
            "https://bkst.tarbil.gov.tr/Main/SaveNotificationAccept",
            "https://bkst.tarbil.gov.tr/Main/ConfirmNotification",
            "https://bkst.tarbil.gov.tr/Main/SaveMalAlim"
        ]
        for ep in accept_endpoints:
            try:
                res = session.post(ep, data={"HeaderId": header_id, "CompanyAddressId": gln_guid, "__RequestVerificationToken": token2}, verify=SSL_VERIFY, timeout=(5, 12))
                if res.status_code == 200:
                    bkst_msg = "Bakanlık (BKST) bildirimi onaylandı."
                    break
            except Exception:
                pass

    username, _, _, _ = read_bkst_credentials()
    df_existing, _, _, _ = get_bkst_cache()
    
    existing_karekods = set()
    if df_existing is not None and not df_existing.empty and 'Karekod' in df_existing.columns:
        existing_karekods = set(str(k).strip().casefold() for k in df_existing['Karekod'].dropna() if str(k).strip())
    
    new_rows = []
    added_count = 0
    for p in incoming_products:
        qr = str(p.get("Karekod") or p.get("KAREKOD") or "").strip()
        if qr and qr.casefold() in existing_karekods:
            continue
        
        gtin_parsed = p.get("Gtin Numarası") or p.get("Gtin / Barkod") or p.get("BARCODE") or p.get("GTIN") or ""
        if not gtin_parsed and qr:
            parsed_qr = parse_gs1_qr(qr)
            if parsed_qr and parsed_qr.get("gtin"):
                gtin_parsed = parsed_qr["gtin"]

        row = {
            "Koli Numarası": p.get("Koli Numarası") or p.get("CARRIERLABEL1") or p.get("PAKETNO") or p.get("KOLINO") or "",
            "Ürün Adı": p.get("Ürün Adı") or p.get("STOCKNAME") or p.get("URUNADI") or "",
            "Karekod": qr,
            "Gtin Numarası": gtin_parsed,
            "Gtin / Barkod": gtin_parsed,
            "gtin": gtin_parsed,
            "Seri Numarası": p.get("Seri Numarası") or p.get("SERIALNUMBER") or p.get("SERINO") or "",
            "Parti Numarası": p.get("Parti Numarası") or p.get("LOTNUMBER") or p.get("SARJNO") or p.get("LOT") or "",
            "Palet Numarası": p.get("Palet Numarası") or p.get("CARRIERLABEL2") or p.get("PALETNO") or "",
            "Üretim Tarihi": format_date_val(p.get("Üretim Tarihi") or p.get("PRODUCTIONDATE") or p.get("URETIMTARIHI")),
            "Son Kullanma Tarihi": format_date_val(p.get("Son Kullanma Tarihi") or p.get("SKT"))
        }
        new_rows.append(row)
        if qr:
            existing_karekods.add(qr.casefold())
        added_count += 1

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        if df_existing is not None and not df_existing.empty:
            updated_df = pd.concat([df_existing, new_df], ignore_index=True)
        else:
            updated_df = new_df
        save_bkst_data_to_db(updated_df, username)
        # Önbelleği temizle ki yeni ürünler anında depomdaki stoklar ve çıkışta aktif olsun
        with _state_lock:
            _user_cache_map.pop(username or "_anon", None)

    msg = f"🟢 Mal Alım bildirimi kabul edildi ve {added_count} adet ürün yerel veritabanınıza eklendi."
    if bkst_msg:
        msg += f" ({bkst_msg})"

    return jsonify({
        "success": True,
        "message": msg,
        "added_count": added_count
    })

# ── SİSTEM AUTHENTICATION API ROUTES ─────────────────────────────────────────
@app.route('/api/system/login', methods=['POST'])
def api_system_login():
    with _state_lock:
        global _app_bkst_synced
        _app_bkst_synced = False

    data = request.json or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()
    address_id = str(data.get('address_id', '')).strip()

    if not username or not password:
        return jsonify({'success': False, 'error': 'Kullanıcı adı ve şifre giriniz.'})

    user_name = get_system_setting("bkst_kullanici_isim", username)

    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest"
        })

        r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(4, 8))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=SSL_VERIFY, timeout=(5, 10))
        
        if "0" not in res_login.text:
            return jsonify({'success': False, 'error': 'Bakanlık kullanıcı adı veya şifreniz hatalı.'})

        try:
            jdata = res_login.json()
            if isinstance(jdata, dict):
                fetched_name = jdata.get("NameSurname") or jdata.get("UserName") or jdata.get("Name")
                if fetched_name:
                    user_name = fetched_name
        except Exception:
            pass

        try:
            r_stock = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(4, 8))
            token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock.text)
            token2 = token2_match.group(1) if token2_match else token1

            r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=SSL_VERIFY, timeout=(4, 8))
            if r_gln.status_code == 200:
                gln_data = r_gln.json()
                if isinstance(gln_data, list) and len(gln_data) > 0:
                    if not address_id:
                        address_id = str(gln_data[0].get("Value") or "").strip()
                    raw_text = str(gln_data[0].get("Text") or "").strip()
                    if raw_text:
                        clean_name = clean_user_name(raw_text)
                        if clean_name and not clean_name.isdigit():
                            user_name = clean_name
        except Exception:
            pass

        user_name = clean_user_name(user_name)

        # 🔒 Güvenli Veritabanı Kaydı (sistem_ayarlari tablosu)
        set_system_setting("bkst_kullanici_adi", username)
        set_system_setting("bkst_sifre", password)
        set_system_setting("bkst_adres_id", address_id)
        set_system_setting("bkst_kullanici_isim", user_name)

        # Güvenlik amacıyla eski plain text txt dosyasını temizle/sil
        cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
        if os.path.exists(cred_file):
            try:
                os.remove(cred_file)
            except Exception:
                pass

        with _bkst_session_lock:
            _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0})
        with _template_vars_lock:
            _template_vars_cache['expires_at'] = 0

        return jsonify({'success': True, 'message': 'Giriş başarılı ve güvenli veritabanına kaydedildi.', 'user_name': user_name, 'token': LOCAL_SESSION_TOKEN})

    except Exception as e:
        logger.warning(f"api_system_login offline fallback check: {e}")
        is_net_error = isinstance(e, (requests.ConnectionError, requests.Timeout,
                                      requests.RequestException, socket.gaierror,
                                      urllib3.exceptions.HTTPError))
        if not is_net_error:
            return jsonify({'success': False, 'error': f'Giriş hatası: {str(e)}'})

        saved_u, saved_p, saved_a, _ = read_bkst_credentials()
        saved_name = get_system_setting("bkst_kullanici_isim", saved_u)
        if saved_u == username and saved_p == password:
            return jsonify({
                'success': True,
                'offline_mode': True,
                'message': 'İnternet bağlantısı yok. Kayıtlı bilgilerle çevrimdışı (offline) modda giriş yapıldı.',
                'user_name': clean_user_name(saved_name or username),
                'token': LOCAL_SESSION_TOKEN
            })
        return jsonify({'success': False, 'error': 'Bakanlık sunucusuna bağlanılamadı ve girilen bilgiler kayıtlı çevrimdışı bilgilerle eşleşmiyor.'})

@app.route('/api/system/user_info', methods=['GET'])
def api_system_user_info():
    username, password, address_id, api_key = read_bkst_credentials()
    if not username:
        return jsonify({'success': True, 'username': '', 'user_name': 'Giriş Yapılmadı'})

    db_name = get_system_setting("bkst_kullanici_isim", "")
    display_name = clean_user_name(db_name or username)
    return jsonify({
        'success': True,
        'username': username,
        'user_name': display_name
    })

@app.route('/api/system/sync_status', methods=['GET'])
def api_system_sync_status():
    payload = _bkst_state_payload()
    with _state_lock:
        payload["synced"] = _app_bkst_synced
    return jsonify(payload)

_last_update_check_time = 0
_cached_update_response = {'has_update': False}

@app.route('/api/system/check_update', methods=['GET'])
def api_system_check_update():
    global _last_update_check_time, _cached_update_response

    # Geliştirici modu kontrolü (.dev_mode dosyası veya DEV_MODE env)
    dev_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dev_mode")
    if os.path.exists(dev_path) or os.environ.get("DEV_MODE") == "1":
        return jsonify({'has_update': False})

    now = time.time()
    # Son 3 dakika içinde kontrol edildiyse önbellekten dön (gereksiz ağ gecikmesini önler)
    if now - _last_update_check_time < 180 and _cached_update_response is not None:
        return jsonify(_cached_update_response)

    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        local_vpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
        local_commit = ""
        cur_code = "v1.0"
        if os.path.exists(local_vpath):
            with open(local_vpath, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                local_commit = str(v_data.get("commit", "")).strip()
                cur_code = str(v_data.get("version", "v1.0")).strip()

        headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache, no-store, must-revalidate"}
        remote_vurl = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/main/version.json?t={time.time_ns()}"
        resp = requests.get(remote_vurl, verify=SSL_VERIFY, timeout=(4, 8), headers=headers)
        if resp.status_code == 200:
            rdata = resp.json()
            remote_commit = str(rdata.get("commit", "")).strip()
            remote_version = str(rdata.get("version", "v1.0")).strip()
            _last_update_check_time = now

            def _parse_version_tuple(v_str):
                try:
                    clean = re.sub(r'[^0-9.]', '', str(v_str))
                    parts = [int(p) for p in clean.split('.') if p.isdigit()]
                    return tuple(parts)
                except Exception:
                    return (0, 0, 0)

            remote_tup = _parse_version_tuple(remote_version)
            local_tup = _parse_version_tuple(cur_code)

            if remote_tup > local_tup:
                _cached_update_response = {
                    'has_update': True,
                    'current_version': cur_code,
                    'remote_version': remote_version,
                    'remote_commit': remote_commit,
                    'message': rdata.get("message", "Yeni sistem güncellemesi mevcut.")
                }
                return jsonify(_cached_update_response)
            else:
                _cached_update_response = {'has_update': False}
                return jsonify(_cached_update_response)
    except Exception as e:
        logger.error(f"Check update error: {e}")

    return jsonify({'has_update': False})

@app.route('/api/system/apply_update', methods=['POST'])
def api_system_apply_update():
    try:
        from guncelleme_kontrol import force_update
        updated = force_update()
        if updated:
            def _restart_process():
                time.sleep(1.2)
                try:
                    logger.info("Restarting QR-Compare server process after update...")
                    base_dir = os.path.dirname(os.path.abspath(__file__))
                    py_dir = os.path.dirname(sys.executable)
                    pythonw_cand = os.path.join(py_dir, "pythonw.exe")
                    target_py = pythonw_cand if os.path.exists(pythonw_cand) else sys.executable
                    flags = 0x08000000 if os.name == 'nt' else 0
                    if os.name == 'nt':
                        flags |= 0x00000008
                    subprocess.Popen([target_py, "app.py"], cwd=base_dir, creationflags=flags)
                except Exception as ex:
                    logger.error(f"Restart error: {ex}")
                finally:
                    os._exit(0)

            threading.Thread(target=_restart_process, daemon=True).start()
            return jsonify({'success': True, 'updated': True, 'message': 'Güncelleme başarıyla yüklendi! Program yeniden başlatılıyor...'})
        return jsonify({'success': True, 'updated': False, 'message': 'Sistem zaten güncel.'})
    except Exception as e:
        logger.error(f"Apply update error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'Güncelleme hatası: {str(e)}'})

@app.route('/api/system/logout', methods=['POST'])
def api_system_logout():
    with _state_lock:
        global _app_bkst_synced
        _app_bkst_synced = False

    # 🔒 Güvenli Veritabanı Oturum Kapatma (Giriş bilgilerini sıfırla)
    set_system_setting("bkst_kullanici_adi", "")
    set_system_setting("bkst_sifre", "")
    set_system_setting("bkst_adres_id", "")
    set_system_setting("bkst_kullanici_isim", "")

    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    if os.path.exists(cred_file):
        try:
            os.remove(cred_file)
        except Exception:
            pass

    with _bkst_session_lock:
        _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0})
    with _template_vars_lock:
        _template_vars_cache['expires_at'] = 0

    return jsonify({'success': True, 'message': 'Oturum kapatıldı.'})

if __name__ == '__main__':
    try:
        from waitress import serve
        logger.info("Starting QR-Compare server with Waitress (threads=32, port=5000)...")
        serve(
            app,
            host='127.0.0.1',
            port=5000,
            threads=32,
            connection_limit=200,
            channel_timeout=180,
            cleanup_interval=30,
            ident='QR-Compare'
        )
    except ImportError:
        logger.warning("Waitress bulunamadı, otomatik pip ile yüklenmeye çalışılıyor...")
        try:
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "waitress"], check=True)
            from waitress import serve
            serve(
                app,
                host='127.0.0.1',
                port=5000,
                threads=32,
                connection_limit=200,
                channel_timeout=180,
                cleanup_interval=30,
                ident='QR-Compare'
            )
        except Exception as e:
            logger.warning(f"Waitress kurulamadı ({e}), Flask development server ile başlatılıyor...")
            app.run(host='127.0.0.1', port=5000, threaded=True)
