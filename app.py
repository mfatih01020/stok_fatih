import os
import glob
import re
import threading
import time
import sqlite3
import json
from datetime import datetime
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, redirect
import io

app = Flask(__name__)

# ── Cache-Control Header to prevent stale browser caching ───────────────────
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ── Sürüm & Güncelleme Bilgisi Endpoint'i ─────────────────────────────────
import subprocess
import json

NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
def get_version_info():
    try:
        from guncelleme_kontrol import get_unified_version_info
        ver_code, ver_date, ver_msg = get_unified_version_info()
        return {
            "success": True,
            "version": ver_code,
            "commit_hash": ver_code,
            "commit_date": ver_date,
            "commit_msg": ver_msg
        }
    except Exception:
        pass

    v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = v_data.get("version", "v3.1.0")
                return {
                    "success": True,
                    "version": f"{v_code} ({v_data.get('commit', '3.1.0')})",
                    "commit_hash": f"{v_code} ({v_data.get('commit', '3.1.0')})",
                    "commit_date": v_data.get("date", "08.10.2026"),
                    "commit_msg": v_data.get("message", f"{v_code} Sürümü")
                }
        except Exception:
            pass

    return {
        "success": True,
        "version": "v3.1.0",
        "commit_hash": "v3.1.0",
        "commit_date": "08.10.2026",
        "commit_msg": "v3.1.0 Sürümü"
    }

import time
import threading

@app.route('/api/system/heartbeat', methods=['POST', 'GET'])
def system_heartbeat():
    return jsonify({"status": "ok"})

def get_version_info():
    try:
        from guncelleme_kontrol import get_unified_version_info
        ver_code, ver_date, ver_msg = get_unified_version_info()
        return {
            'success': True,
            'version': ver_code,
            'commit_hash': ver_code,
            'commit_date': ver_date,
            'commit_msg': ver_msg
        }
    except Exception as e:
        return {
            'success': True,
            'version': 'v3.1.0',
            'commit_hash': 'v3.1.0 (3.1.0)',
            'commit_date': '08.10.2026',
            'commit_msg': 'v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu ve stabilite güncellemeleri'
        }

@app.route('/api/system/version', methods=['GET'])
def system_version_api():
    return jsonify(get_version_info())

# ── SQLite Veritabanı ────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cikis_kayitlari.db')

def save_bkst_data_to_db(df, username=""):
    if df is None or df.empty:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    
    if username:
        c.execute("DELETE FROM bkst_depo_verileri WHERE kullanici_adi = ?", (username,))
    else:
        c.execute("DELETE FROM bkst_depo_verileri")
        
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    koli_col = find_koli_column(df.columns)
    
    for _, row in df.iterrows():
        r = row.to_dict()
        qr_val = normalize_qr(str(r.get("Karekod", r.get("tam_karekod", r.get("QR", "")))))
        gtin_val = str(r.get("Gtin Numarası", r.get("gtin", r.get("BARKOD", "")))).strip()
        urun_val = str(r.get("Ürün Adı", r.get("urun_adi", r.get("URUNADI", "")))).strip()
        seri_val = str(r.get("Seri Numarası", r.get("seri_no", r.get("SERIALNUMBER", "")))).strip()
        parti_val = str(r.get("Parti Numarası", r.get("parti_no", r.get("SARJNO", "")))).strip()
        koli_val = str(r.get(koli_col, r.get("koli_no", r.get("KOLINO", "")))).strip().upper() if koli_col and pd.notna(r.get(koli_col)) else str(r.get("koli_no", "")).strip().upper()
        if koli_val == "NAN":
            koli_val = ""
        palet_val = str(r.get("Palet Numarası", r.get("palet_no", "")))
        uretim_val = str(r.get("Üretim Tarihi", r.get("uretim_tarihi", "")))
        skt_val = str(r.get("Son Kullanma Tarihi", r.get("skt", r.get("SKT", ""))))
        
        c.execute('''INSERT INTO bkst_depo_verileri
            (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (gtin_val, urun_val, seri_val, parti_val, koli_val, palet_val, uretim_val, skt_val, qr_val, username, now_str))
            
    conn.commit()
    conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
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
    cols = [row[1] for row in c.fetchall()]
    if "kullanici_adi" not in cols:
        c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN kullanici_adi TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ham_karekod ON cikis_kayitlari(ham_karekod)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_kullanici_adi ON cikis_kayitlari(kullanici_adi)")

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
    c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_karekod ON bkst_depo_verileri(tam_karekod)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_gtin ON bkst_depo_verileri(gtin)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_kullanici ON bkst_depo_verileri(kullanici_adi)")

    conn.commit()
    conn.close()

def read_bkst_credentials():
    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    
    username = ""
    password = ""
    address_id = ""
    api_key = ""
    
    if os.path.exists(cred_file):
        with open(cred_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("KULLANICI_ADI="):
                    username = line.split("=", 1)[1].strip()
                elif line.startswith("SIFRE="):
                    password = line.split("=", 1)[1].strip()
                elif line.startswith("ADRES_ID="):
                    raw_id = line.split("=", 1)[1].strip()
                    if "-" in raw_id:
                        address_id = raw_id.split("-")[0].strip()
                    else:
                        address_id = raw_id
                elif line.startswith("KEY=") or line.startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    
    return username, password, address_id, api_key

@app.route('/api/heartbeat', methods=['POST', 'GET'])
def api_heartbeat():
    return jsonify({'status': 'ok'})

@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

def clean_user_name(name):
    if not name:
        return ""
    name = str(name).strip()
    cleaned = re.sub(r'^\d+[\s\-]+', '', name)
    cleaned = cleaned.split(" (")[0].strip()
    return cleaned if cleaned else name


@app.context_processor
def inject_global_template_vars():
    username, password, address_id, api_key = read_bkst_credentials()
    user_name = "Giriş Yapılmadı"
    if username:
        user_name = username
        cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
        if os.path.exists(cred_file):
            try:
                with open(cred_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip().startswith("KULLANICI_ISIM="):
                            val = line.strip().split("=", 1)[1].strip()
                            if val:
                                user_name = val
                                break
            except Exception:
                pass
        user_name = clean_user_name(user_name or username)

    version_str = "v3.1.0"
    v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = v_data.get("version", "3.1.0")
                if not str(v_code).startswith("v"):
                    version_str = f"v{v_code}"
                else:
                    version_str = str(v_code)
        except Exception:
            pass

    return dict(current_user_name=user_name, current_app_version=version_str)

def normalize_qr(qr):
    if pd.isna(qr):
        return ""
    qr_str = str(qr).strip().replace(" ", "")
    qr_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', qr_str)
    return qr_str.upper()

def parse_gs1_qr(qr_str):
    """
    GS1 DataMatrix / Karekod standart ayrıştırıcısı.
    FNC1 (\x1d, \x1e, \x1f) ve AI (01, 10, 17, 21, 11) belirteçlerini doğru ayıklar.
    """
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
            # AI 01: GTIN (14 hane)
            if token[idx:].startswith("01") and len(token[idx:]) >= 16 and token[idx+2:idx+16].isdigit():
                if not result["gtin"]:
                    result["gtin"] = token[idx+2:idx+16]
                idx += 16
                continue

            # AI 17: SKT (6 hane: YYMMDD)
            elif token[idx:].startswith("17") and len(token[idx:]) >= 8 and token[idx+2:idx+8].isdigit():
                if not result["skt"]:
                    yy, mm, dd = token[idx+2:idx+4], token[idx+4:idx+6], token[idx+6:idx+8]
                    result["skt"] = f"{dd}.{mm}.20{yy}"
                idx += 8
                continue

            # AI 11: Üretim Tarihi (6 hane: YYMMDD)
            elif token[idx:].startswith("11") and len(token[idx:]) >= 8 and token[idx+2:idx+8].isdigit():
                if not result["uretim_tarihi"]:
                    yy, mm, dd = token[idx+2:idx+4], token[idx+4:idx+6], token[idx+6:idx+8]
                    result["uretim_tarihi"] = f"{dd}.{mm}.20{yy}"
                idx += 8
                continue

            # AI 21: Seri Numarası
            elif token[idx:].startswith("21") and len(token[idx:]) > 2:
                if not result["seri_no"]:
                    result["seri_no"] = token[idx+2:]
                break

            # AI 10: Parti Numarası
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

# ── HIZLI BELLEK ÖNBELLEĞİ (MEMORY CACHE FOR INSTANT SCANS <1ms) ─────────────
_bkst_df_cache = None
_bkst_mtime_cache = 0
_bkst_koli_dict = {}
_bkst_qr_dict = {}
_bkst_gtin_dict = {}

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
    global _bkst_df_cache, _bkst_koli_dict, _bkst_qr_dict, _bkst_gtin_dict

    username, _, _, _ = read_bkst_credentials()
    conn = sqlite3.connect(DB_PATH)
    if username:
        df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ''", conn, params=(username,))
    else:
        df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri", conn)
    conn.close()

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

    for _, row in df.iterrows():
        r = row.to_dict()
        qr_val = normalize_qr(str(r.get("Karekod", "")))
        gtin_val = normalize_qr(str(r.get("Gtin Numarası", "")))
        koli_val = str(r.get("Koli Numarası", "")).strip().upper()

        if qr_val:
            qr_dict[qr_val] = r
        if gtin_val and gtin_val not in gtin_dict:
            gtin_dict[gtin_val] = r

        if koli_val and koli_val != "NAN":
            if koli_val not in koli_dict:
                koli_dict[koli_val] = []
            koli_dict[koli_val].append(r)

    _bkst_df_cache = df
    _bkst_qr_dict = qr_dict
    _bkst_gtin_dict = gtin_dict
    _bkst_koli_dict = koli_dict

    _bkst_df_cache = df
    _bkst_qr_dict = qr_dict
    _bkst_gtin_dict = gtin_dict
    _bkst_koli_dict = koli_dict

    return _bkst_df_cache, _bkst_qr_dict, _bkst_gtin_dict, _bkst_koli_dict

init_db()

# Migrate legacy bkst_depo_verileri.xlsx if present
excel_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bkst_depo_verileri.xlsx")
if os.path.exists(excel_file):
    try:
        username, _, _, _ = read_bkst_credentials()
        df_old = pd.read_excel(excel_file)
        if not df_old.empty:
            save_bkst_data_to_db(df_old, username)
        os.remove(excel_file)
        print("bkst_depo_verileri.xlsx successfully migrated to SQLite DB and removed!")
    except Exception as e:
        print(f"Migration error: {e}")

# ── Global browser session ──────────────────────────────────────────────────
bkst_driver = None
bkst_status = "closed"
bkst_message = ""
cached_results = {}
_app_bkst_synced = False

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
                print(f"Error parsing system file: {e}")
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
                print(f"Error parsing system file: {e}")

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

@app.before_request
def check_authentication():
    if request.path.startswith('/static') or request.path.startswith('/api/') or request.path == '/login':
        return None

    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password:
        return redirect('/login')


# ── MAIN ROUTES ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect('/cikis')


@app.route('/stok-esitleme')
def stok_esitleme_page():
    return render_template('index.html')


@app.route('/login')
def login_page():
    return render_template('login.html')


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

        global cached_results
        cached_results = {
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
            "matched_sales": matched_sales,
            "sales_by_product": sales_by_product,
            "remaining_counts": remaining_counts
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/download/sales', methods=['GET'])
@app.route('/api/download/full_report', methods=['GET'])
def download_sales():
    global cached_results
    if 'cached_results' not in globals() or not cached_results:
        return "No comparison run yet", 400
        
    matched_rows = []
    for item in cached_results["matched_sales"]:
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
    for item in cached_results["unmatched_sales"]:
        unmatched_rows.append({
            "Ürün Adı": item["product_name"],
            "Karekod": item["qr"],
            "Açıklama / Durum": "Depoda Bulunamadı (Sistem Dışı Hatalı Satış!)"
        })
    df_unmatched = pd.DataFrame(unmatched_rows)
    if not df_unmatched.empty:
        df_unmatched = df_unmatched.sort_values(by=["Ürün Adı"])
        
    summary_rows = []
    for p_name, qty in cached_results["sales_by_product"].items():
        summary_rows.append({
            "Ürün Adı": p_name,
            "Satılan Miktar (Adet)": qty
        })
    df_summary = pd.DataFrame(summary_rows)
    if not df_summary.empty:
        df_summary = df_summary.sort_values(by=["Ürün Adı"])
        
    koli_rows = []
    for koli_no, stats in cached_results.get("koli_stats", {}).items():
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
    global cached_results
    if 'cached_results' not in globals() or not cached_results:
        return "No comparison run yet", 400
        
    rows = []
    for item in cached_results["remaining_inventory"]:
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

        if clean_code_all and clean_key_all and (clean_code_all == clean_key_all or clean_code_all.endswith(clean_key_all) or clean_key_all.endswith(clean_code_all)):
            return k_key, k_rows

        if digits_stripped and key_digits_stripped:
            if digits_stripped == key_digits_stripped or digits_stripped.lstrip('0') == key_digits_stripped.lstrip('0'):
                return k_key, k_rows

        if digits_only and key_digits and digits_only.lstrip('0') == key_digits.lstrip('0'):
            if len(raw_code) <= 8 or "KOLI" in clean_code_all or "PAKET" in clean_code_all or "PALET" in clean_code_all:
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
        # A. Koli Numarası / SSCC Barkodu Eşleşmesi
        matched_koli_key, matched_koli_rows = find_matching_koli(code, koli_dict)
        if matched_koli_key and matched_koli_rows:
            target_koli = matched_koli_key
            matched_rows = matched_koli_rows
            is_koli_scan = True

        # B. Tekil Ürün Karekod Eşleşmesi (Eğer Koli olarak eşleşmediyse)
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

        # C. Alt Dize Karekod Eşleşmesi
        if not target_koli:
            for q_key, r_dict in qr_dict.items():
                if code_norm in q_key or q_key in code_norm:
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

        # D. GTIN Eşleşmesi
        if not target_koli and code in gtin_dict:
            item_row = gtin_dict[code]
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

    if not target_koli or not matched_rows:
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
        return jsonify({"success": False, "error": "Bakanlık depo verisi bulunamadı. Lütfen önce 1. bölümden verileri çekin."})

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

        # Match by GTIN OR Product Name
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
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _find_chrome_path():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def _find_edge_path():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _auto_update_driver():
    try:
        import urllib.request
        import zipfile
        import tempfile

        edge_ver = "120.0.2210.133"
        url = f"https://msedgedriver.microsoft.com/{edge_ver}/edgedriver_win64.zip"
        temp_dir = tempfile.gettempdir()
        zip_path = os.path.join(temp_dir, "edgedriver.zip")

        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extract("msedgedriver.exe", os.path.dirname(os.path.abspath(__file__)))
        print(f"msedgedriver.exe ({edge_ver}) updated via pure Python!")
    except Exception as e:
        print(f"Auto driver update error: {e}")


def _do_open():
    global bkst_driver, bkst_status, bkst_message
    bkst_status = "opening"
    bkst_message = "Bakanlık tarayıcısı açılıyor, lütfen bekleyin..."

    if bkst_driver is not None:
        try:
            bkst_driver.title
            bkst_status = "login_page"
            bkst_message = "Bakanlık sayfası açık."
            return
        except Exception:
            bkst_driver = None

    driver = None
    from selenium import webdriver
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.chrome.service import Service as ChromeService

    local_driver = os.path.join(os.path.dirname(os.path.abspath(__file__)), "msedgedriver.exe")
    edge_path = _find_edge_path()

    def launch_edge(drv_path):
        if os.path.exists(drv_path):
            try:
                service = EdgeService(executable_path=drv_path)
                service.creation_flags = NO_WINDOW
                options = webdriver.EdgeOptions()
                if edge_path:
                    options.binary_location = edge_path
                options.add_argument("--start-maximized")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_experimental_option("detach", True)
                d = webdriver.Edge(service=service, options=options)
                print("Successfully launched Edge using msedgedriver.exe!")
                return d
            except Exception as e_loc:
                print(f"Edge launch with {drv_path} failed: {e_loc}")
        return None

    # 1. Try local msedgedriver.exe
    driver = launch_edge(local_driver)

    # 2. If local driver failed due to version mismatch, auto-update driver and retry
    if driver is None:
        print("Local Edge driver missing or version mismatched. Updating driver automatically...")
        _auto_update_driver()
        driver = launch_edge(local_driver)

    # 3. Standard Edge launch fallback (Selenium Manager auto-driver)
    if driver is None:
        try:
            service = EdgeService()
            service.creation_flags = NO_WINDOW
            options = webdriver.EdgeOptions()
            if edge_path:
                options.binary_location = edge_path
            options.add_argument("--start-maximized")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_experimental_option("detach", True)
            driver = webdriver.Edge(service=service, options=options)
            print("Successfully launched Edge via Selenium Manager!")
        except Exception as e1:
            print(f"Edge standard launch error: {e1}")

    # 4. Chrome fallback
    if driver is None:
        chrome_path = _find_chrome_path()
        try:
            service = ChromeService()
            service.creation_flags = NO_WINDOW
            options = webdriver.ChromeOptions()
            if chrome_path:
                options.binary_location = chrome_path
            options.add_argument("--start-maximized")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_experimental_option("detach", True)
            driver = webdriver.Chrome(service=service, options=options)
            print("Successfully launched Chrome!")
        except Exception as e2:
            print(f"Chrome launch error: {e2}")

    if driver is not None:
        try:
            driver.set_page_load_timeout(15)
        except Exception:
            pass
        bkst_driver = driver
        try:
            bkst_driver.get("https://bkst.tarbil.gov.tr")
        except Exception as e_get:
            print(f"Error navigating to ministry page: {e_get}")
        bkst_status = "login_page"
        bkst_message = "Bakanlık giriş sayfası açıldı. Lütfen şifrenizle giriş yapıp sol menüden 'Stok Takibi' sayfasına gidin."
    else:
        bkst_status = "error"
        bkst_message = "Tarayıcı (Edge veya Chrome) başlatılamadı. Lütfen bilgisayarınızda Edge veya Chrome tarayıcısının yüklü olduğundan emin olun." 


def read_bkst_credentials():
    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    
    username = ""
    password = ""
    address_id = ""
    api_key = ""
    
    if os.path.exists(cred_file):
        with open(cred_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("KULLANICI_ADI="):
                    username = line.split("=", 1)[1].strip()
                elif line.startswith("SIFRE="):
                    password = line.split("=", 1)[1].strip()
                elif line.startswith("ADRES_ID="):
                    raw_id = line.split("=", 1)[1].strip()
                    if "-" in raw_id:
                        address_id = raw_id.split("-")[0].strip()
                    else:
                        address_id = raw_id
                elif line.startswith("KEY=") or line.startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    
    return username, password, address_id, api_key


@app.route('/api/bkst/fetch_api', methods=['POST'])
def bkst_fetch_api():
    global bkst_cache_df, bkst_cache_qr, bkst_cache_gtin, bkst_cache_koli, bkst_status, bkst_message
    
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password or username == "" or password == "":
        bkst_status = "error"
        bkst_message = "❌ HATA: 'bakanlik_giris_bilgileri.txt' dosyasında KULLANICI_ADI veya SIFRE bulunamadı! Lütfen bilgilerinizi doldurup kaydedin."
        return jsonify({"success": False, "unauthenticated": True, "error": bkst_message})
        
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
        
        # 1. Get token from homepage
        r_home = session.get("https://bkst.tarbil.gov.tr/", verify=False, timeout=10)
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        # 2. Login via UserOperation/GetUserInf
        login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=False, timeout=15)

        if "0" not in res_login.text:
            bkst_status = "error"
            bkst_message = "❌ HATA: Bakanlık kullanıcı adı veya şifreniz yanlış! Lütfen 'bakanlik_giris_bilgileri.txt' dosyasındaki bilgileri kontrol edin."
            return jsonify({"success": False, "error": bkst_message})

        # 3. Get StockList page token
        r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=False, timeout=10)
        token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
        token2 = token2_match.group(1) if token2_match else token1

        # 4. Fetch GLN GUID
        r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=False, timeout=10)
        gln_guid = address_id
        if r_gln.status_code == 200:
            try:
                gln_data = r_gln.json()
                if isinstance(gln_data, list) and len(gln_data) > 0:
                    gln_guid = str(gln_data[0].get("Value") or "").strip()
            except Exception:
                pass

        if not gln_guid:
            gln_guid = "8aaf058e-7444-48bb-bd74-4077173fa6a8"

        # 5. Fetch GTIN product list
        r_grid = session.post("https://bkst.tarbil.gov.tr/Main/GetStockList", data={"CompanyAddressId": gln_guid, "Gtin": "", "__RequestVerificationToken": token2}, verify=False, timeout=15)
        gtin_list = r_grid.json().get("Data", []) if r_grid.status_code == 200 else []

        all_rows = []
        for g_item in gtin_list:
            gtin_code = g_item.get("BARKOD")
            prod_name = g_item.get("URUNADI")
            if not gtin_code:
                continue

            session.post("https://bkst.tarbil.gov.tr/Main/GetViewReport", data={"gtin": gtin_code, "gln": gln_guid, "__RequestVerificationToken": token2}, verify=False, timeout=10)
            r_detail = session.post("https://bkst.tarbil.gov.tr/Main/GetStockDetailList", data={"CompanyAddressId": gln_guid, "Gtin": gtin_code, "__RequestVerificationToken": token2}, verify=False, timeout=15)

            if r_detail.status_code == 200:
                try:
                    d_items = r_detail.json()
                    if isinstance(d_items, list):
                        for item in d_items:
                            koli = item.get("PAKETNO") or item.get("KOLINO") or item.get("PALETNO") or ""
                            all_rows.append({
                                "Koli Numarası": koli,
                                "Ürün Adı": prod_name or item.get("URUNADI") or "",
                                "Karekod": item.get("KAREKOD") or item.get("HAMKAREKOD") or "",
                                "Gtin / Barkod": item.get("BARKOD") or gtin_code,
                                "Seri Numarası": item.get("SERINO") or "",
                                "Parti Numarası": item.get("SARJNO") or "",
                                "Palet Numarası": item.get("PALETNO") or "",
                                "Üretim Tarihi": item.get("URETIMTARIHI") or "",
                                "Son Kullanma Tarihi": item.get("SKT") or ""
                            })
                except Exception:
                    pass

        df = pd.DataFrame(all_rows)
        save_bkst_data_to_db(df, username)

        bkst_cache_df = None
        bkst_cache_qr = None
        bkst_cache_gtin = None
        bkst_cache_koli = None

        df_cache, _, _, _ = get_bkst_cache()
        item_count = len(df_cache) if df_cache is not None else len(all_rows)

        global _app_bkst_synced
        _app_bkst_synced = True

        if item_count == 0:
            bkst_status = "done"
            bkst_message = "⚠️ UYARI: Bakanlık sisteminde kayıtlı stok bulunamadı (0 adet)."
        else:
            bkst_status = "done"
            bkst_message = f"🟢 TEBRİKLER! Bakanlık stok verileri API ile 2 saniyede başarıyla çekildi! Toplam {item_count} adet ürün hazır."

        return jsonify({
            "success": True,
            "message": bkst_message,
            "item_count": item_count
        })

    except Exception as e:
        bkst_status = "error"
        bkst_message = "❌ HATA: Bakanlık API bağlantı hatası: " + str(e)
        return jsonify({
            "success": False,
            "error": bkst_message
        })


@app.route('/api/bkst/download_api_data', methods=['GET'])
def download_api_data():
    out_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bkst_depo_verileri.xlsx")
    if not os.path.exists(out_file):
        return jsonify({"error": "Henüz stok verisi çekilmedi."}), 404
        
    return send_file(
        out_file,
        as_attachment=True,
        download_name="bkst_depo_verileri_guncel.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route('/api/bkst/open', methods=['POST'])
def bkst_open():
    global bkst_driver, bkst_status

    if bkst_status in ["opening", "fetching"]:
        return jsonify({"success": True, "message": "Tarayıcı zaten başlatılıyor...", "status": bkst_status})

    if bkst_driver is not None:
        try:
            bkst_driver.title
            bkst_status = "open"
            return jsonify({"success": True, "message": "Bakanlık sayfası zaten açık.", "status": "open"})
        except Exception:
            bkst_driver = None

    t = threading.Thread(target=_do_open, daemon=True)
    t.start()
    return jsonify({"success": True, "message": "Bakanlık sayfası açılıyor...", "status": "opening"})


@app.route('/api/bkst/status', methods=['GET'])
def bkst_status_route():
    global bkst_driver, bkst_status, bkst_message
    if bkst_driver is not None:
        try:
            curr_url = str(bkst_driver.current_url or "")
            if "StockList" in curr_url or "Stok Takibi" in str(bkst_driver.title or ""):
                if bkst_status not in ["fetching", "done"]:
                    bkst_status = "ready"
                    bkst_message = "Bakanlık Stok Takibi sayfasındasınız. Lütfen 'Ara' butonuna basıp tablo yüklendikten sonra 2. buton ile verileri çekin."
            else:
                if bkst_status not in ["fetching", "done"]:
                    bkst_status = "login_page"
                    bkst_message = "Lütfen açılan tarayıcıda kullanıcı şifrenizle giriş yapıp sol menüden 'Stok Takibi' sayfasına gidin."
        except Exception:
            pass

    return jsonify({"status": bkst_status, "message": bkst_message})


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


def _do_fetch():
    global bkst_driver, bkst_status, bkst_message
    bkst_status = "fetching"
    bkst_message = "Bakanlık sistemindeki TÜM stok, SKT ve QR detay verileri çekiliyor..."

    try:
        if bkst_driver is None:
            bkst_status = "error"
            bkst_message = "Tarayıcı kapalı. Lütfen önce 1. buton ile tarayıcıyı açın."
            return

        # 1. PageSize 100.000 yaparak TÜM stok özet verisini (GetStockList) çekme
        trigger_js = """
        try {
            window._allBkstDetails = null;
            var companyAddressId = $("#GLNList").data("kendoComboBox") ? $("#GLNList").data("kendoComboBox").value() : "";
            var gridSummary = $("#stockListGrid").data("kendoGrid");
            if (gridSummary && gridSummary.dataSource) {
                gridSummary.dataSource.pageSize(100000);
                gridSummary.dataSource.page(1);
                gridSummary.dataSource.read();
            } else {
                var btn = document.getElementById("btnSearchStock");
                if (btn) btn.click();
            }
        } catch(e) {}
        """
        try:
            bkst_driver.execute_script(trigger_js)
        except Exception:
            pass

        time.sleep(3)

        # 2. Çekilen TÜM GTIN'lerin QR Detaylarını (GetStockDetailList) AJAX ile Toplu Çekme
        fetch_details_js = """
        try {
            var companyAddressId = $("#GLNList").data("kendoComboBox") ? $("#GLNList").data("kendoComboBox").value() : "";
            var summaryGrid = $("#stockListGrid").data("kendoGrid");

            if (summaryGrid && summaryGrid.dataSource) {
                var summaryItems = summaryGrid.dataSource.data();
                if (summaryItems && summaryItems.length > 0) {
                    var allDetails = [];
                    var promises = [];

                    summaryItems.forEach(function(item) {
                        var gtin = item.BARKOD;
                        if (gtin) {
                            promises.push(
                                $.ajax({
                                    url: '/Main/GetStockDetailList',
                                    data: { 'CompanyAddressId': companyAddressId, 'Gtin': gtin },
                                    type: 'POST',
                                    cache: false
                                }).done(function(detailData) {
                                    if (detailData && detailData.length > 0) {
                                        detailData.forEach(function(d) {
                                            d.URUNADI = item.URUNADI || d.URUNADI || "";
                                            allDetails.push(d);
                                        });
                                    } else {
                                        allDetails.push({
                                            BARKOD: item.BARKOD,
                                            URUNADI: item.URUNADI,
                                            STOKMIKTARI: item.STOKMIKTARI
                                        });
                                    }
                                }).fail(function() {
                                    allDetails.push({
                                        BARKOD: item.BARKOD,
                                        URUNADI: item.URUNADI,
                                        STOKMIKTARI: item.STOKMIKTARI
                                    });
                                })
                            );
                        }
                    });

                    $.when.apply($, promises).always(function() {
                        window._allBkstDetails = allDetails;
                    });
                } else {
                    window._allBkstDetails = [];
                }
            } else {
                window._allBkstDetails = [];
            }
        } catch(e) {
            window._allBkstDetails = [];
        }
        """
        bkst_driver.execute_script(fetch_details_js)

        # Wait up to 15 seconds for parallel AJAX detail queries to return
        data = None
        for attempt in range(15):
            res = bkst_driver.execute_script("return window._allBkstDetails;")
            if res is not None:
                data = res
                if len(data) > 0:
                    break
            time.sleep(1.0)

        # Fallback: Eğer GetStockDetailList boş kalırsa mevcut Grid verisini al
        if not data or len(data) == 0:
            extract_js = """
            var data = null;
            try {
                var gridSummary = $("#stockListGrid").data("kendoGrid");
                if (gridSummary && gridSummary.dataSource) {
                    var items = gridSummary.dataSource.data();
                    if (items && items.length > 0) data = JSON.parse(JSON.stringify(items));
                }
            } catch(e) {}
            return data;
            """
            data = bkst_driver.execute_script(extract_js)

        if not data:
            bkst_status = "error"
            bkst_message = "Bakanlık sayfasında ürün verisi bulunamadı! Lütfen açılan tarayıcıda 'Stok Takibi' sayfasında 'Ara' butonuna bastığınızdan emin olun."
            return

        formatted = []
        for row in data:
            if not isinstance(row, dict):
                continue
            urun_adi = row.get("URUNADI") or row.get("UrunAdi") or row.get("urunAdi") or row.get("Ürün Adı") or row.get("col_2") or row.get("col_1") or ""
            gtin_no = row.get("BARKOD") or row.get("GtinNo") or row.get("gtinNo") or row.get("Gtin Numarası") or row.get("col_1") or row.get("col_0") or ""
            seri_no = row.get("SERINO") or row.get("SeriNo") or row.get("seriNo") or row.get("Seri Numarası") or ""
            parti_no = row.get("SARJNO") or row.get("PartiNo") or row.get("partiNo") or row.get("Parti Numarası") or ""
            qr_code = row.get("KAREKOD") or row.get("QrCode") or row.get("qrCode") or row.get("Karekod") or ""
            palet_no = row.get("PALETNO") or row.get("PaletNo") or row.get("paletNo") or row.get("Palet Numarası") or ""
            koli_no = row.get("PAKETNO") or row.get("KoliNo") or row.get("koliNo") or row.get("Koli Numarası") or ""

            uretim_raw = row.get("URETIMTARIHI") or row.get("UretimTarihi") or row.get("Üretim Tarihi") or ""
            skt_raw = row.get("SKTDate") or row.get("SKT") or row.get("SonKullanmaTarihi") or row.get("Son Kullanma Tarihi") or ""

            uretim_tarihi = format_date_val(uretim_raw)
            skt = format_date_val(skt_raw)
            stok_miktari = str(row.get("STOKMIKTARI") or row.get("StokMiktari") or row.get("Stok Miktarı") or row.get("col_3") or "1")

            formatted.append({
                "Ürün Adı": urun_adi,
                "Gtin Numarası": gtin_no,
                "Seri Numarası": seri_no,
                "Parti Numarası": parti_no,
                "Karekod": qr_code,
                "Palet Numarası": palet_no,
                "Koli Numarası": koli_no,
                "Üretim Tarihi": uretim_tarihi,
                "Son Kullanma Tarihi": skt,
                "Stok Miktarı (Toplam)": stok_miktari
            })

        if not formatted:
            bkst_status = "error"
            bkst_message = "Çekilen tabloda geçerli ürün satırı bulunamadı."
            return

        df_all = pd.DataFrame(formatted)

        out_path = os.path.join(os.getcwd(), "bkst_depo_verileri.xlsx")
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            df_all.to_excel(writer, sheet_name="Tüm Ürünler (QR)", index=False)

        global _bkst_df_cache, _bkst_mtime_cache
        _bkst_df_cache = None
        _bkst_mtime_cache = 0
        get_bkst_cache()

        bkst_status = "done"
        bkst_message = f"TÜM DEPO GÜNCELLENDİ! Toplam {len(formatted)} adet ürün/karekod (Üretim & Son Kullanma Tarihleri dahil) Bakanlıktan çekildi!"

    except Exception as e:
        bkst_status = "error"
        bkst_message = f"Veri çekme hatası: {str(e)}"


@app.route('/api/bkst/fetch', methods=['POST'])
def bkst_fetch():
    global bkst_driver, bkst_status

    if bkst_driver is None:
        return jsonify({"success": False, "message": "Önce tarayıcıyı açın."})

    if bkst_status == "fetching":
        return jsonify({"success": False, "message": "Zaten veri çekme işlemi devam ediyor."})

    t = threading.Thread(target=_do_fetch, daemon=True)
    t.start()
    return jsonify({"success": True, "message": "Veri çekme işlemi başlatıldı..."})


@app.route('/api/bkst/download', methods=['GET'])
def bkst_download():
    out_path = os.path.join(os.getcwd(), "bkst_depo_verileri.xlsx")
    if not os.path.exists(out_path):
        return "Henüz bir dosya oluşturulmadı.", 404
    return send_file(out_path, as_attachment=True, download_name="bkst_depo_verileri.xlsx")


@app.route('/api/bkst/close', methods=['POST'])
def bkst_close():
    global bkst_driver, bkst_status, bkst_message
    if bkst_driver:
        try:
            bkst_driver.quit()
        except Exception:
            pass
        bkst_driver = None
    bkst_status = "closed"
    bkst_message = ""
    return jsonify({"success": True})


# ── Sistemden Çıkış Modülü ──────────────────────────────────────────────────

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

@app.route('/api/depo_stoklari', methods=['GET'])
def api_depo_stoklari():
    df, _, _, _ = get_bkst_cache()
    if df is None or df.empty:
        excel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bkst_depo_verileri.xlsx")
        if os.path.exists(excel_path):
            try:
                df = pd.read_excel(excel_path)
            except Exception:
                df = pd.DataFrame()
        else:
            df = pd.DataFrame()

    rows = []
    if df is not None and not df.empty:
        df_clean = df.fillna("")
        rows = df_clean.to_dict(orient="records")

    return jsonify({
        "success": True,
        "products": rows,
        "total": len(rows)
    })


@app.route('/api/cikis/okut', methods=['POST'])
def cikis_okut():
    data = request.json or {}
    barkod_raw = data.get('barkod', '').strip()
    if not barkod_raw:
        return jsonify({'success': False, 'error': 'Barkod boş olamaz.'})

    barkod_norm = normalize_qr(barkod_raw)
    username, _, _, _ = read_bkst_credentials()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if username:
        c.execute('SELECT id, tarih, urun_adi FROM cikis_kayitlari WHERE ham_karekod = ? AND (kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")', (barkod_norm, username))
    else:
        c.execute('SELECT id, tarih, urun_adi FROM cikis_kayitlari WHERE ham_karekod = ?', (barkod_norm,))
    existing = c.fetchone()
    conn.close()

    if existing:
        ex_id, ex_tarih, ex_urun = existing
        return jsonify({
            'success': False,
            'already_exited': True,
            'error': f'Bu ürün zaten depodan çıkarılmış! Ürün: {ex_urun} (Tarih: {ex_tarih})'
        })

    df, qr_map, gtin_map, koli_map = get_bkst_cache()
    if df is None or df.empty:
        return jsonify({
            'success': False,
            'error': 'Bakanlık depo verisi bulunamadı. Lütfen önce "Bakanlık Ekranı Aç" ile verileri çekin.'
        })

    match_row = qr_map.get(barkod_norm)
    if match_row is None:
        match_row = gtin_map.get(barkod_norm)

    if match_row is None:
        for k_qr, r_dict in qr_map.items():
            if barkod_norm in k_qr or k_qr in barkod_norm:
                match_row = r_dict
                break

    if match_row is None:
        return jsonify({
            'success': False,
            'error': f'"{barkod_raw}" barkoduna ait ürün Bakanlık depo verisinde bulunamadı.'
        })

    tarih         = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    urun_adi      = str(match_row.get('Ürün Adı', '')).strip()
    barkod_col    = str(match_row.get('Gtin Numarası', '')).strip()
    koli_no       = str(match_row.get('Koli Numarası', '')).strip()
    seri_no       = str(match_row.get('Seri Numarası', '')).strip()
    parti_no      = str(match_row.get('Parti Numarası', '')).strip()
    palet_no      = str(match_row.get('Palet Numarası', '')).strip()
    uretim_tarihi = str(match_row.get('Üretim Tarihi', '')).strip()
    skt           = str(match_row.get('Son Kullanma Tarihi', '')).strip()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute('''INSERT INTO cikis_kayitlari
        (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
         uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)''',
        (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
         uretim_tarihi, skt, barkod_norm, username))
    new_id = c.lastrowid
    conn.commit()
    conn.close()

    return jsonify({
        'success': True,
        'tekrar_uyari': False,
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
            'ham_karekod': barkod_norm
        }
    })


@app.route('/api/cikis/toplu_ekle', methods=['POST'])
def cikis_toplu_ekle():
    data = request.json or {}
    items = data.get('items', [])
    if not items or not isinstance(items, list):
        return jsonify({'success': False, 'error': 'Aktarılacak karekod listesi bulunamadı.'})

    df, qr_map, gtin_map, koli_map = get_bkst_cache()
    if df is None or df.empty:
        return jsonify({
            'success': False,
            'error': 'Bakanlık depo verisi bulunamadı. Lütfen önce güncel stok verilerini çekin.'
        })

    username, _, _, _ = read_bkst_credentials()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")

    added_count = 0
    already_count = 0
    tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for barkod_raw in items:
        barkod_norm = normalize_qr(str(barkod_raw).strip())
        if not barkod_norm:
            continue

        if username:
            c.execute('SELECT id FROM cikis_kayitlari WHERE ham_karekod = ? AND (kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")', (barkod_norm, username))
        else:
            c.execute('SELECT id FROM cikis_kayitlari WHERE ham_karekod = ?', (barkod_norm,))
        if c.fetchone():
            already_count += 1
            continue

        match_row = qr_map.get(barkod_norm) or gtin_map.get(barkod_norm)
        if match_row is None:
            for k_qr, r_dict in qr_map.items():
                if barkod_norm in k_qr or k_qr in barkod_norm:
                    match_row = r_dict
                    break

        if match_row is None:
            urun_adi = "Tanımsız Ürün"
            barkod_col = ""
            koli_no = ""
            seri_no = ""
            parti_no = ""
            palet_no = ""
            uretim_tarihi = ""
            skt = ""
        else:
            urun_adi      = str(match_row.get('Ürün Adı', '')).strip()
            barkod_col    = str(match_row.get('Gtin Numarası', '')).strip()
            koli_no       = str(match_row.get('Koli Numarası', '')).strip()
            seri_no       = str(match_row.get('Seri Numarası', '')).strip()
            parti_no      = str(match_row.get('Parti Numarası', '')).strip()
            palet_no      = str(match_row.get('Palet Numarası', '')).strip()
            uretim_tarihi = str(match_row.get('Üretim Tarihi', '')).strip()
            skt           = str(match_row.get('Son Kullanma Tarihi', '')).strip()

        c.execute('''INSERT INTO cikis_kayitlari
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)''',
            (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, barkod_norm, username))
        added_count += 1

    conn.commit()
    conn.close()

    return jsonify({
        'success': True,
        'added_count': added_count,
        'already_count': already_count,
        'message': f'{added_count} adet ürün Çıkış Listesine aktarıldı.'
    })


@app.route('/api/cikis/listesi', methods=['GET'])
def cikis_listesi_api():
    username, _, _, _ = read_bkst_credentials()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if username:
        c.execute('SELECT * FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "" ORDER BY id DESC', (username,))
    else:
        c.execute('SELECT * FROM cikis_kayitlari ORDER BY id DESC')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'kayitlar': rows, 'toplam': len(rows)})


@app.route('/api/cikis/sil/<int:kayit_id>', methods=['DELETE'])
def cikis_sil(kayit_id):
    username, _, _, _ = read_bkst_credentials()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if username:
        c.execute('DELETE FROM cikis_kayitlari WHERE id = ? AND (kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")', (kayit_id, username))
    else:
        c.execute('DELETE FROM cikis_kayitlari WHERE id = ?', (kayit_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/cikis/temizle', methods=['POST'])
def cikis_temizle():
    username, _, _, _ = read_bkst_credentials()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if username:
        c.execute('DELETE FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ""', (username,))
    else:
        c.execute('DELETE FROM cikis_kayitlari')
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'mesaj': 'Tüm çıkış kayıtları silindi.'})


@app.route('/api/cikis/indir', methods=['GET'])
def cikis_indir():
    username, _, _, _ = read_bkst_credentials()
    conn = sqlite3.connect(DB_PATH)
    if username:
        df = pd.read_sql_query('SELECT * FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "" ORDER BY id DESC', conn, params=(username,))
    else:
        df = pd.read_sql_query('SELECT * FROM cikis_kayitlari ORDER BY id DESC', conn)
    conn.close()

    if 'kullanici_adi' in df.columns:
        df = df.drop(columns=['kullanici_adi'])

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


# ── Depoya Kabul Et & Gelen Bildirimler API ────────────────────────────────
def get_bkst_authenticated_session():
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password:
        return None, None, None, "Kullanıcı adı veya şifre bulunamadı."
    
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

    r_home = session.get("https://bkst.tarbil.gov.tr/", verify=False, timeout=10)
    token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
    token1 = token_match.group(1) if token_match else ""

    login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
    res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=False, timeout=15)
    if "0" not in res_login.text:
        return None, None, None, "Bakanlık kullanıcı adı veya şifreniz hatalı."

    r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=False, timeout=10)
    token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
    token2 = token2_match.group(1) if token2_match else token1

    r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=False, timeout=10)
    gln_guid = address_id
    if r_gln.status_code == 200:
        try:
            gln_data = r_gln.json()
            if isinstance(gln_data, list) and len(gln_data) > 0:
                gln_guid = str(gln_data[0].get("Value") or "").strip()
        except Exception:
            pass

    return session, gln_guid, token2, None


@app.route('/depo_kabul')
def depo_kabul_page():
    return render_template('depo_kabul.html')


@app.route('/kullaniciya-satis')
@app.route('/kullaniciya_satis')
def kullaniciya_satis_page():
    return render_template('kullaniciya_satis.html')


@app.route('/api/bkst/recetesiz_satis/sms_gonder', methods=['POST'])
def bkst_sms_gonder():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    if not tc_no:
        return jsonify({'success': False, 'error': 'T.C. Kimlik veya Vergi No giriniz.'})

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=False, timeout=10)
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'OperationType': 1,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/SendSmsVerificationCode', data=payload, verify=False, timeout=15)
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('IsSuccess') is True:
                return jsonify({
                    'success': True,
                    'phone_hidden': res_json.get('MobilePhoneHidden', ''),
                    'verification_token': res_json.get('VerificationCode', ''),
                    'message': f"📲 SMS doğrulama kodu {res_json.get('MobilePhoneHidden', '')} numaralı telefona gönderildi."
                })
            else:
                return jsonify({'success': False, 'error': res_json.get('Message') or 'SMS gönderilemedi.'})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})
    except Exception as e:
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

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=False, timeout=10)
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'VerificationCode': verification_token,
            'Code': sms_code,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/CheckSmsVerificationCode', data=payload, verify=False, timeout=15)
        if res.status_code == 200:
            res_str = res.text.strip().replace('"', '')
            if res_str and res_str != "00000000-0000-0000-0000-000000000000":
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
        return jsonify({'success': False, 'error': f"SMS Doğrulama Hatası: {str(e)}"})


@app.route('/api/bkst/recetesiz_satis', methods=['POST'])
def bkst_recetesiz_satis():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    verification_token = str(data.get('verification_token', '')).strip()
    karekods = data.get('karekods', [])
    belge_no = str(data.get('belge_no', '')).strip()
    aciklama = str(data.get('aciklama', 'Reçetesiz Satış')).strip()

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

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=False, timeout=10)
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        df_cache, qr_map, gtin_map, koli_map = get_bkst_cache()

        datasource_items = []
        for raw_qr in karekods:
            norm_qr = normalize_qr(str(raw_qr).strip())
            if not norm_qr:
                continue

            match_row = qr_map.get(norm_qr) or gtin_map.get(norm_qr) if qr_map else None
            if match_row is None and qr_map:
                for k_qr, r_dict in qr_map.items():
                    if norm_qr in k_qr or k_qr in norm_qr:
                        match_row = r_dict
                        break

            gtin = str(match_row.get('Gtin Numarası', '')).strip() if match_row else ""
            seri = str(match_row.get('Seri Numarası', '')).strip() if match_row else ""
            parti = str(match_row.get('Parti Numarası', '')).strip() if match_row else ""
            koli = str(match_row.get('Koli Numarası', '')).strip() if match_row else ""
            urun = str(match_row.get('Ürün Adı', '')).strip() if match_row else "Bitki Koruma Ürünü"
            skt = str(match_row.get('Son Kullanma Tarihi', '')).strip() if match_row else ""

            datasource_items.append({
                "BARKOD": gtin,
                "KAREKOD": norm_qr,
                "SERIALNUMBER": seri,
                "SARJNO": parti,
                "KOLINO": koli,
                "URUNADI": urun,
                "SKT": skt,
                "QUANTITY": 1
            })

        today_str = datetime.now().strftime("%Y.%m.%d")
        payload = {
            'SenderAddress': gln_guid,
            'IdTaxNo': tc_no,
            'DocumentNo': belge_no or f"SATIS-{datetime.now().strftime('%Y%m%d%H%M')}",
            'DocumentDate': today_str,
            'Desc': aciklama,
            'DataSource': json.dumps(datasource_items),
            'ParcelList': '',
            'HarmfulChoice': '',
            'Province': '',
            'VerificationCode': verification_token,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/NewCheckOutNotificationForProducerNonPrescribed', data=payload, verify=False, timeout=20)

        if res.status_code == 200:
            try:
                res_data = res.json()
                if res_data.get('Result') is True or res_data == 1:
                    username_cur, _, _, _ = read_bkst_credentials()
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    tarih_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    for item in datasource_items:
                        c.execute('''INSERT INTO cikis_kayitlari
                            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)''',
                            (tarih_now, item['URUNADI'], item['BARKOD'], item['KOLINO'], item['SERIALNUMBER'], item['SARJNO'], '', '', item['SKT'], item['KAREKOD'], username_cur))
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
                    err_text = " - ".join(err_msgs) if err_msgs else str(res_data)
                    return jsonify({'success': False, 'error': f"BKST Hata Bildirimi: {err_text}"})
            except Exception:
                return jsonify({'success': False, 'error': f"Bakanlık Yanıtı: {res.text[:300]}"})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})

    except Exception as e:
        return jsonify({'success': False, 'error': f"API Bağlantı Hatası: {str(e)}"})


@app.route('/api/depo_kabul/gelen_listesi', methods=['POST', 'GET'])
def api_depo_kabul_gelen_listesi():
    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err:
        return jsonify({"success": False, "error": err})

    notifications = []
    try:
        endpoints = [
            "https://bkst.tarbil.gov.tr/Main/GetReceivedNotificationList",
            "https://bkst.tarbil.gov.tr/Main/GetNotificationList",
            "https://bkst.tarbil.gov.tr/Main/ReceivedNotificationList"
        ]
        
        for ep in endpoints:
            try:
                res = session.post(ep, data={
                    "CompanyAddressId": gln_guid,
                    "NotificationType": "1",
                    "NotificationDirection": "1",
                    "HeaderState": "0",
                    "__RequestVerificationToken": token2
                }, verify=False, timeout=12)
                
                if res.status_code == 200:
                    try:
                        jdata = res.json()
                        raw_list = jdata.get("Data") if isinstance(jdata, dict) else (jdata if isinstance(jdata, list) else [])
                        if isinstance(raw_list, list) and len(raw_list) > 0:
                            for item in raw_list:
                                op = str(item.get("OPERATION") or item.get("OperationName") or item.get("ISLEMTIPI") or item.get("NotificationType") or item.get("DESCR") or item.get("Operation") or "").upper()
                                direction = str(item.get("NotificationDirection") or item.get("DIRECTION") or item.get("YON") or "").upper()

                                # Satış, Çıkış veya Giden bildirimleri KESİNLİKLE FİLTRELE!
                                if any(x in op for x in ["SATIS", "SATIŞ", "CIKIS", "ÇIKIŞ", "DEVR", "GÖNDER", "SATIŞA"]):
                                    continue
                                if direction in ["2", "OUT", "OUTGOING", "GİDEN", "GIDEN"]:
                                    continue

                                notifications.append({
                                    "HEADERID": item.get("HEADERID") or item.get("Id") or item.get("ID") or str(item.get("WAYBILLNUMBER", "")),
                                    "WAYBILLNUMBER": item.get("WAYBILLNUMBER") or item.get("WaybillNumber") or item.get("BELGENO") or "-",
                                    "WAYBILLDATE": format_date_val(item.get("WAYBILLDATE") or item.get("WaybillDate") or item.get("TARIH")),
                                    "SENDER": item.get("CompanyTitle") or item.get("SENDER") or item.get("GonderenFirma") or item.get("FIRMA") or "Tedarikçi / Üretici",
                                    "PRODUCTCOUNT": item.get("PRODUCTCOUNT") or item.get("ProductCount") or item.get("ADET") or 0,
                                    "HEADERSTATE": item.get("HEADERSTATE") or item.get("StateDescription") or "Bekliyor",
                                    "OPERATION": "MALALIM",
                                    "products": item.get("products") or []
                                })
                            break
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception as e:
        print(f"BKST gelen bildirim hatası: {e}")

    return jsonify({
        "success": True,
        "notifications": notifications,
        "message": f"Sadece Tipi 'MAL ALIM' (Gelen) olan {len(notifications)} adet bildirim filtreler ile listelendi." if notifications else "Gelen/bekleyen MAL ALIM bildirimi bulunamadı."
    })


@app.route('/api/depo_kabul/detay/<header_id>', methods=['GET'])
def api_depo_kabul_detay(header_id):
    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err:
        return jsonify({"success": False, "error": err, "products": []})

    products = []
    try:
        endpoints = [
            "https://bkst.tarbil.gov.tr/Main/GetReceivedNotificationDetailList",
            "https://bkst.tarbil.gov.tr/Main/GetNotificationDetailList",
            "https://bkst.tarbil.gov.tr/Main/GetNotificationDetail"
        ]
        for ep in endpoints:
            try:
                res = session.post(ep, data={"HeaderId": header_id, "CompanyAddressId": gln_guid, "__RequestVerificationToken": token2}, verify=False, timeout=12)
                if res.status_code == 200:
                    jdata = res.json()
                    raw_list = jdata.get("Data") if isinstance(jdata, dict) else (jdata if isinstance(jdata, list) else [])
                    if isinstance(raw_list, list) and len(raw_list) > 0:
                        for item in raw_list:
                            products.append({
                                "Koli Numarası": item.get("PAKETNO") or item.get("KOLINO") or item.get("PALETNO") or "",
                                "Ürün Adı": item.get("STOCKNAME") or item.get("URUNADI") or item.get("ProductName") or "",
                                "Karekod": item.get("KAREKOD") or item.get("HAMKAREKOD") or item.get("Barcode") or "",
                                "Gtin / Barkod": item.get("BARCODE") or item.get("GTIN") or item.get("Gtin") or "",
                                "Seri Numarası": item.get("SERIALNUMBER") or item.get("SERINO") or item.get("SerialNumber") or "",
                                "Parti Numarası": item.get("SARJNO") or item.get("LOT") or item.get("BatchNumber") or "",
                                "Palet Numarası": item.get("PALETNO") or "",
                                "Üretim Tarihi": format_date_val(item.get("URETIMTARIHI") or item.get("ProductionDate")),
                                "Son Kullanma Tarihi": format_date_val(item.get("SKT") or item.get("ExpirationDate"))
                            })
                        break
            except Exception:
                pass
    except Exception as e:
        print(f"Detail fetch error: {e}")

    return jsonify({
        "success": True,
        "products": products
    })


@app.route('/api/depo_kabul/onayla', methods=['POST'])
def api_depo_kabul_onayla():
    req_data = request.get_json() or {}
    header_id = req_data.get('header_id')
    waybill_number = req_data.get('waybill_number')
    incoming_products = req_data.get('products') or []

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    bkst_accepted = False
    bkst_msg = ""
    if session and gln_guid and token2:
        accept_endpoints = [
            "https://bkst.tarbil.gov.tr/Main/NotificationAccept",
            "https://bkst.tarbil.gov.tr/Main/SaveNotificationAccept",
            "https://bkst.tarbil.gov.tr/Main/ConfirmNotification",
            "https://bkst.tarbil.gov.tr/Main/SaveMalAlim"
        ]
        for ep in accept_endpoints:
            try:
                res = session.post(ep, data={"HeaderId": header_id, "CompanyAddressId": gln_guid, "__RequestVerificationToken": token2}, verify=False, timeout=12)
                if res.status_code == 200:
                    bkst_accepted = True
                    bkst_msg = "Bakanlık (BKST) mal alım bildirimi onaylandı."
                    break
            except Exception:
                pass

    excel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bkst_depo_verileri.xlsx")
    cols = ["Koli Numarası", "Ürün Adı", "Karekod", "Gtin / Barkod", "Seri Numarası", "Parti Numarası", "Palet Numarası", "Üretim Tarihi", "Son Kullanma Tarihi"]
    
    existing_df = pd.DataFrame(columns=cols)
    if os.path.exists(excel_path):
        try:
            existing_df = pd.read_excel(excel_path)
            for c in cols:
                if c not in existing_df.columns:
                    existing_df[c] = ""
        except Exception:
            existing_df = pd.DataFrame(columns=cols)

    existing_karekods = set(existing_df['Karekod'].dropna().astype(str).str.strip())
    
    new_rows = []
    added_count = 0
    for p in incoming_products:
        qr = str(p.get("Karekod") or p.get("KAREKOD") or "").strip()
        if qr and qr in existing_karekods:
            continue
        
        row = {
            "Koli Numarası": p.get("Koli Numarası") or p.get("PAKETNO") or p.get("KOLINO") or "",
            "Ürün Adı": p.get("Ürün Adı") or p.get("STOCKNAME") or p.get("URUNADI") or "",
            "Karekod": qr,
            "Gtin / Barkod": p.get("Gtin / Barkod") or p.get("BARCODE") or p.get("GTIN") or "",
            "Seri Numarası": p.get("Seri Numarası") or p.get("SERIALNUMBER") or p.get("SERINO") or "",
            "Parti Numarası": p.get("Parti Numarası") or p.get("SARJNO") or p.get("LOT") or "",
            "Palet Numarası": p.get("Palet Numarası") or p.get("PALETNO") or "",
            "Üretim Tarihi": format_date_val(p.get("Üretim Tarihi") or p.get("URETIMTARIHI")),
            "Son Kullanma Tarihi": format_date_val(p.get("Son Kullanma Tarihi") or p.get("SKT"))
        }
        new_rows.append(row)
        if qr:
            existing_karekods.add(qr)
        added_count += 1

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        updated_df = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        updated_df = existing_df

    updated_df.to_excel(excel_path, index=False)

    global bkst_cache_df, bkst_cache_qr, bkst_cache_gtin, bkst_cache_koli
    bkst_cache_df = None
    bkst_cache_qr = None
    bkst_cache_gtin = None
    bkst_cache_koli = None

    msg = f"🟢 Mal Alım bildirimi kabul edildi ve {added_count} adet ürün yerel deponuza (Excel) eklendi."
    if bkst_msg:
        msg += f" ({bkst_msg})"

    return jsonify({
        "success": True,
        "message": msg,
        "added_count": added_count
    })


@app.route('/api/system/login', methods=['POST'])
def api_system_login():
    global _app_bkst_synced
    _app_bkst_synced = False

    data = request.json or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()
    address_id = str(data.get('address_id', '')).strip()

    if not username or not password:
        return jsonify({'success': False, 'error': 'Kullanıcı adı ve şifre giriniz.'})

    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    user_name = username

    if os.path.exists(cred_file):
        try:
            with open(cred_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith("KULLANICI_ISIM="):
                        val = line.strip().split("=", 1)[1].strip()
                        if val:
                            user_name = val
                            break
        except Exception:
            pass

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

        r_home = session.get("https://bkst.tarbil.gov.tr/", verify=False, timeout=5)
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=False, timeout=8)
        
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
            r_stock = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=False, timeout=5)
            token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock.text)
            token2 = token2_match.group(1) if token2_match else token1

            r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=False, timeout=5)
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

        lines = [
            "# ==============================================================================",
            "# BAKANLIK BKST GİRİŞ BİLGİLERİ",
            "# ==============================================================================",
            f"KULLANICI_ADI={username}",
            f"SIFRE={password}",
            f"ADRES_ID={address_id}",
            f"KULLANICI_ISIM={user_name}",
            ""
        ]
        with open(cred_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        return jsonify({'success': True, 'message': 'Giriş başarılı ve kaydedildi.', 'user_name': user_name})

    except Exception as e:
        # Offline Mode: Save credentials locally and allow offline login
        lines = [
            "# ==============================================================================",
            "# BAKANLIK BKST GİRİŞ BİLGİLERİ",
            "# ==============================================================================",
            f"KULLANICI_ADI={username}",
            f"SIFRE={password}",
            f"ADRES_ID={address_id}",
            f"KULLANICI_ISIM={user_name}",
            ""
        ]
        with open(cred_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        return jsonify({
            'success': True,
            'offline_mode': True,
            'message': 'İnternet bağlantısı yok veya Bakanlık sunucusu erişilemiyor. Yerel Çevrimdışı (Offline) Modda Giriş Yapıldı.',
            'user_name': clean_user_name(user_name)
        })


@app.route('/api/system/user_info', methods=['GET'])
def api_system_user_info():
    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    username, password, address_id, api_key = read_bkst_credentials()
    if not username:
        return jsonify({'success': True, 'username': '', 'user_name': 'Giriş Yapılmadı'})

    user_name = username
    if os.path.exists(cred_file):
        try:
            with open(cred_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith("KULLANICI_ISIM="):
                        val = line.strip().split("=", 1)[1].strip()
                        if val:
                            user_name = val
                            break
        except Exception:
            pass

    display_name = clean_user_name(user_name or username)
    return jsonify({
        'success': True,
        'username': username,
        'user_name': display_name
    })


@app.route('/api/system/sync_status', methods=['GET'])
def api_system_sync_status():
    return jsonify({"synced": _app_bkst_synced})


@app.route('/api/system/check_update', methods=['GET'])
def api_system_check_update():
    try:
        from guncelleme_kontrol import get_unified_version_info, get_latest_remote_commit_sha
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        cur_code, cur_date, cur_msg = get_unified_version_info()
        local_vpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
        local_commit = ""
        if os.path.exists(local_vpath):
            with open(local_vpath, "r", encoding="utf-8") as f:
                local_commit = str(json.load(f).get("commit", "")).strip()

        latest_sha = get_latest_remote_commit_sha(requests)
        if not latest_sha or latest_sha == "main":
            return jsonify({'has_update': False, 'current_version': cur_code})

        headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
        remote_vurl = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/{latest_sha}/version.json?t={time.time_ns()}"
        resp = requests.get(remote_vurl, verify=False, timeout=5, headers=headers)
        if resp.status_code == 200:
            rdata = resp.json()
            remote_commit = str(rdata.get("commit", "")).strip()
            remote_version = str(rdata.get("version", "v1.0")).strip()
            if remote_commit and remote_commit != local_commit:
                return jsonify({
                    'has_update': True,
                    'current_version': cur_code,
                    'remote_version': remote_version,
                    'remote_commit': remote_commit,
                    'message': rdata.get("message", "Yeni sistem güncellemesi mevcut.")
                })
    except Exception as e:
        print(f"Check update error: {e}")

    return jsonify({'has_update': False})


@app.route('/api/system/apply_update', methods=['POST'])
def api_system_apply_update():
    try:
        from guncelleme_kontrol import force_update
        updated = force_update()
        if updated:
            def _restart():
                time.sleep(1)
                os.execv(sys.executable, [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")])
            threading.Thread(target=_restart, daemon=True).start()
            return jsonify({'success': True, 'updated': True, 'message': 'Güncelleme başarıyla yüklendi! Sistem otomatik yeniden başlatılıyor...'})
        return jsonify({'success': True, 'updated': False, 'message': 'Sistem zaten güncel.'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Güncelleme hatası: {str(e)}'})


@app.route('/api/system/logout', methods=['POST'])
def api_system_logout():
    global _app_bkst_synced
    _app_bkst_synced = False

    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    lines = [
        "# ==============================================================================",
        "# BAKANLIK BKST GİRİŞ BİLGİLERİ",
        "# ==============================================================================",
        "KULLANICI_ADI=",
        "SIFRE=",
        "ADRES_ID=",
        ""
    ]
    with open(cred_file, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))

    return jsonify({'success': True, 'message': 'Oturum kapatıldı.'})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
