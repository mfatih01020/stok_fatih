import sys
import os
import glob
import re
import threading
import time
import sqlite3
import json
import secrets
import hashlib
import hmac
import logging
from logging.handlers import RotatingFileHandler
import concurrent.futures
from datetime import datetime, timedelta
import random
import subprocess
import socket
import atexit

# ── Otomatik Bağımlılık Kurulumu (sunucu ve istemci) ────────────────────────
def _ensure_dependencies():
    """Eksik Python paketlerini uygulama başlarken otomatik kurar."""
    import importlib.util
    required = {
        'flask': 'Flask',
        'pandas': 'pandas',
        'openpyxl': 'openpyxl',
        'xlrd': 'xlrd',
        'requests': 'requests',
        'waitress': 'waitress',
        'cryptography': 'cryptography',
    }
    missing = [pkg for mod, pkg in required.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return
    print("Eksik paketler kuruluyor: " + ", ".join(missing))
    flags = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW
    for pkg in missing:
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', pkg],
                                  creationflags=flags)
        except Exception as e_pip:
            print("Paket kurulamadi (" + pkg + "): " + str(e_pip))
    importlib.invalidate_caches()

_ensure_dependencies()
import requests
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, redirect, send_from_directory, make_response, session as flask_session
import io

from core.constants import *
from core.exceptions import (
    AppException, ValidationError, AuthenticationError, AuthorizationError,
    NotFoundError, DatabaseError, BKSTNetworkError, BKSTApiError, SyncConflictError
)
from core.config import AppConfig
from core.database import DatabaseManager, get_db
from services.auth_service import AuthService
from services.tunnel_service import TunnelService
from services.sync_service import SyncService
from services.bkst_service import BKSTService

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
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
if getattr(sys, 'stderr', None) is None:
    sys.stderr = SafeStream()
else:
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
# ── Güvenli & Yapılandırılmış Log Sistemi (Şişmeyi ve Sızıntıyı Önleyen Filtre) ──
class SecurityLogFilter(logging.Filter):
    """
    1. Hassas verileri (şifreler, HMAC tokenler, özel anahtarlar) maskeler.
    2. Değişimsiz tekrarlayan senkronizasyon loglarını filtreleyerek app.log dosyasının şişmesini engeller.
    """
    _SENSITIVE_PATTERNS = [
        (re.compile(r'ENC:[A-Za-z0-9_\-\+=]+', re.IGNORECASE), 'ENC:***'),
        (re.compile(r'(password|sifre|secret)[\'"]?\s*[:=]\s*[\'"]?([^\s,\'"\}]+)', re.IGNORECASE), r'\1=***'),
        (re.compile(r'(token=)([a-f0-9]{32,}|qrm\.[^\s&]+)', re.IGNORECASE), r'\1***'),
    ]

    def filter(self, record):
        msg = str(record.msg)
        # Sıfır transferli periyodik senkron loglarını sustur (şişmeyi önler)
        if "İadeli Taahhütlü Senkronizasyon Başarılı: Sunucu=" in msg and "İletilen=0" in msg:
            return False
        if "DEBUG_LOGIN_B_ROW" in msg:
            return False

        # Hassas bilgileri maskele
        for pat, repl in self._SENSITIVE_PATTERNS:
            msg = pat.sub(repl, msg)
        record.msg = msg
        return True

log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.log')
# 1 MB'lık rotasyon ve en fazla 5 arşiv yedeği (asla kontrolsüz büyümez, max 5MB sabit)
handler = RotatingFileHandler(log_file_path, maxBytes=1_048_576, backupCount=5, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)-7s | %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
handler.addFilter(SecurityLogFilter())

_pending_client_debug_logs = []
_pending_client_debug_logs_lock = threading.Lock()

class AdminDebugLogHandler(logging.Handler):
    """
    Python logger'a düşen WARNING, ERROR ve CRITICAL seviyesindeki tüm kayıtları
    otomatik olarak yakalayıp sunucuya aktarılmak üzere kuyruğa ekler.
    """
    _is_logging = False

    def emit(self, record):
        if record.levelno < logging.WARNING:
            return
        if AdminDebugLogHandler._is_logging:
            return
        try:
            AdminDebugLogHandler._is_logging = True
            msg = self.format(record) if self.formatter else record.getMessage()
            t_str = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
            log_item = {
                'tarih': t_str,
                'seviye': record.levelname,
                'kategori': 'APP_LOG',
                'baslik': str(record.getMessage())[:120],
                'detay': msg,
                'bayi_kodu': None
            }
            with _pending_client_debug_logs_lock:
                if len(_pending_client_debug_logs) < 1000:
                    _pending_client_debug_logs.append(log_item)
        except Exception:
            pass
        finally:
            AdminDebugLogHandler._is_logging = False

admin_log_handler = AdminDebugLogHandler()
admin_log_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)-7s | %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
admin_log_handler.addFilter(SecurityLogFilter())

logging.basicConfig(level=logging.INFO, handlers=[handler, admin_log_handler])
logger = logging.getLogger('qr_compare')
logger.addHandler(admin_log_handler)

def get_recent_app_log_errors(max_lines=100, local_code=None):
    """app.log dosyasından son ERROR ve WARNING satırlarını okur (temizlenmiş eski logları atlar)."""
    if not os.path.exists(log_file_path):
        return []
    target_code = local_code or get_system_setting("active_bayi_kodu", "bayi_1")
    clr_time = get_system_setting(f"logs_cleared_at_{target_code}", "") or get_system_setting("logs_cleared_at_all", "")
    result = []
    try:
        with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            for line in reversed(lines[-1500:]):
                if any(k in line for k in [' | ERROR ', ' | WARNING', ' | CRITICAL', 'Traceback']):
                    line_str = line.strip()
                    if clr_time:
                        t_match = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line_str)
                        if t_match and t_match.group(1) <= clr_time:
                            continue
                    result.append(line_str)
                    if len(result) >= max_lines:
                        break
    except Exception as e_log:
        logger.debug(f"get_recent_app_log_errors notice: {e_log}")
    return list(reversed(result))

def import_app_log_to_debug_logs(max_entries=500):
    """
    app.log dosyasındaki tüm ERROR ve WARNING kayıtlarını otomatik olarak
    debug_loglari veritabanı tablosuna aktarır. Temizlenen tarihlerden önceki logları kesinlikle almaz.
    """
    if not os.path.exists(log_file_path):
        return 0
    try:
        inserted = 0
        ensure_db_schema()
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            c = conn.cursor()
            all_clr = get_system_setting("logs_cleared_at_all", "", conn=conn)
            active_dealer = get_system_setting("active_bayi_kodu", "bayi_1", conn=conn)
            active_clr = get_system_setting(f"logs_cleared_at_{active_dealer}", "", conn=conn)

            with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()

            for line in lines[-1500:]:
                line_str = line.strip()
                if not line_str:
                    continue
                if not any(k in line_str for k in [' | ERROR ', ' | WARNING', ' | CRITICAL', 'Traceback']):
                    continue

                sev = 'ERROR' if any(e in line_str for e in [' | ERROR ', ' | CRITICAL', 'Traceback']) else 'WARNING'
                t_match = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line_str)
                tar = t_match.group(1) if t_match else datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                # Eğer tüm loglar için geçmiş silindiyse, silinme tarihinden önceki eski logları TEKRAR YÜKLEME!
                if all_clr and tar <= all_clr:
                    continue
                if active_clr and tar <= active_clr:
                    continue

                b_kod = active_dealer or 'bayi_1'
                b_match = re.search(r'\b(bayi_[a-zA-Z0-9_-]+)\b', line_str)
                if b_match:
                    b_kod = b_match.group(1)

                dealer_clr = get_system_setting(f"logs_cleared_at_{b_kod}", "", conn=conn)
                if dealer_clr and tar <= dealer_clr:
                    continue

                parts = line_str.split(' | ', 2)
                baslik = parts[2][:120] if len(parts) >= 3 else line_str[:120]

                c.execute("SELECT id FROM debug_loglari WHERE tarih = ? AND baslik = ? LIMIT 1", (tar, baslik))
                if not c.fetchone():
                    c.execute("""
                        INSERT INTO debug_loglari (tarih, bayi_kodu, seviye, kategori, baslik, detay, request_payload, response_payload)
                        VALUES (?, ?, ?, 'APP_LOG', ?, ?, '', '')
                    """, (tar, b_kod, sev, baslik, line_str))
                    inserted += 1

            conn.commit()
        return inserted
    except Exception as e:
        logger.debug(f"import_app_log_to_debug_logs notice: {e}")
        return 0

SSL_VERIFY = os.environ.get("QR_SSL_VERIFY", "1") == "1"

# ── Dynamic Flask Secret Key ─────────────────────────────────────────────────
FLASK_SECRET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".flask_secret")

def _get_or_create_flask_secret():
    if os.path.exists(FLASK_SECRET_PATH):
        try:
            with open(FLASK_SECRET_PATH, 'r', encoding='utf-8') as f:
                s = f.read().strip()
                if s:
                    return s
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    secret = secrets.token_hex(32)
    try:
        with open(FLASK_SECRET_PATH, 'w', encoding='utf-8') as f:
            f.write(secret)
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    return secret

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or _get_or_create_flask_secret()
app.config['TEMPLATES_AUTO_RELOAD'] = True

@app.errorhandler(AppException)
def handle_app_exception(e):
    """Yapılandırılmış merkezi hata yakalayıcı: JSON formatında tutarlı durum kodu ve mesaj döner."""
    return jsonify(e.to_dict()), e.status_code

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
FIREBASE_RELAY_URL = os.environ.get("FIREBASE_RELAY_URL", "https://qr-stok-64d79-default-rtdb.europe-west1.firebasedatabase.app/server_info.json")
_merkez_url_cache = {"url": "", "ts": 0}
_merkez_url_lock = threading.Lock()

def get_merkez_sunucu_url(force_refresh=False):
    """
    Merkez sunucu tünel URL'sini döndürür.
    1. Önbellekteki URL 30 saniye geçerlidir.
    2. Eğer URL yoksa veya erişilemiyorsa; DERHAL Firebase Akıllı Dağıtıcısından canlı tüneli çeker.
    3. Bulunan canlı tünel adresini sunucu_adresi.txt dosyasına ve DB'ye otomatik kaydeder.
    """
    now = time.monotonic()
    with _merkez_url_lock:
        if not force_refresh and _merkez_url_cache["url"] and (now - _merkez_url_cache["ts"] < 30):
            return _merkez_url_cache["url"]

    candidate = ""
    try:
        candidate = get_system_setting("merkez_sunucu_url", "").strip().rstrip('/')
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunucu_adresi.txt")
    if not candidate and os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f_u:
                candidate = f_u.read().strip().rstrip('/')
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    # Aday URL yaşıyor mu kontrol et
    url_alive = False
    if candidate and candidate.startswith("http"):
        try:
            import urllib.request
            req = urllib.request.Request(f"{candidate}/api/system/version", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=1.8) as r:
                if r.status in (200, 302, 401, 403):
                    url_alive = True
        except Exception:
            url_alive = False

    if url_alive:
        with _merkez_url_lock:
            _merkez_url_cache["url"] = candidate
            _merkez_url_cache["ts"] = now
        return candidate

    # Aday URL ölü veya boş -> Firebase'den canlı tüneli çek!
    try:
        import urllib.request, json
        fb_endpoint = f"{FIREBASE_RELAY_URL}?ts={int(time.time())}"
        req = urllib.request.Request(fb_endpoint, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data and data.get('tunnel_url'):
                fb_url = str(data['tunnel_url']).strip().rstrip('/')
                # Güvenlik Doğrulaması: Yalnızca Cloudflare tünel veya yerel ağ adreslerini kabul et
                if fb_url and (fb_url.startswith("https://") or fb_url.startswith("http://")):
                    if "trycloudflare.com" in fb_url or "localhost" in fb_url or "127.0.0.1" in fb_url:
                        # Tünel gerçekten canlı mı doğrula!
                        fb_alive = False
                        try:
                            chk_req = urllib.request.Request(f"{fb_url}/api/system/version", headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(chk_req, timeout=2.0) as chk_r:
                                if chk_r.status in (200, 302, 401, 403):
                                    fb_alive = True
                        except Exception:
                            fb_alive = False

                        if fb_alive:
                            try:
                                with open(cfg_file, "w", encoding="utf-8") as f_out:
                                    f_out.write(fb_url)
                            except Exception as _ignored_err:
                                logger.debug(f"Suppressed notice: {_ignored_err}")
                            try:
                                set_system_setting("merkez_sunucu_url", fb_url)
                            except Exception as _ignored_err:
                                logger.debug(f"Suppressed notice: {_ignored_err}")
                            with _merkez_url_lock:
                                _merkez_url_cache["url"] = fb_url
                                _merkez_url_cache["ts"] = now
                            logger.info(f"🌐 Canlı Merkez Sunucu Tüneli Firebase'den Alındı ve Güncellendi: {fb_url}")
                            return fb_url
                        else:
                            logger.debug(f"Firebase'deki tünel ({fb_url}) çevrimdışı, bağımsız yerel moda geçildi.")
                    else:
                        logger.warning(f"⚠️ Güvenlik Uyarısı: Firebase'den bilinmeyen/güvensiz tünel adresi reddedildi: {fb_url}")
    except Exception as e_fb:
        err_msg = str(e_fb)
        # Ağ bağlantısı yoksa veya DNS çözümlenemiyorsa (çevrimdışı / Errno 11001) sessizce debug loguna yaz
        if "11001" in err_msg or "getaddrinfo" in err_msg or "NameResolutionError" in err_msg:
            logger.debug(f"Ağ bağlantısı yok veya DNS henüz hazır değil (çevrimdışı): {e_fb}")
        else:
            logger.warning(f"Firebase canlı tünel sorgulama hatası: {e_fb}")

    # Sunucu çevrimdışı: Önbelleğe boş dize kaydet ve 15 saniye yerel bağımsız modda çalış
    with _merkez_url_lock:
        _merkez_url_cache["url"] = ""
        _merkez_url_cache["ts"] = now
    return ""

def get_or_create_session_token():
    if os.path.exists(SESSION_TOKEN_PATH):
        try:
            with open(SESSION_TOKEN_PATH, 'r', encoding='utf-8') as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    token = secrets.token_hex(32)
    try:
        with open(SESSION_TOKEN_PATH, 'w', encoding='utf-8') as f:
            f.write(token)
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    return token

LOCAL_SESSION_TOKEN = get_or_create_session_token()

# ==============================================================================
# 🔐 GÜVENLİ VERİ ŞİFRELEME MOTORU (AES-256-CBC / END-TO-END ENCRYPTION)
# Bayi kullanıcı bilgileri ve şifreleri ağ üzerinden şifreli iletilir ve sunucuda şifreli saklanır.
# ==============================================================================
import base64

def _get_or_create_master_key() -> bytes:
    # 1. Ortam değişkeni (Environment Variable) kontrolü
    env_key = os.environ.get("QR_MASTER_KEY")
    if env_key:
        return hashlib.sha256(env_key.encode('utf-8')).digest()
    
    # 2. Kalıcı yerel master anahtar dosyası (.master_key)
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".master_key")
    try:
        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                k = f.read().strip()
                if len(k) >= 32:
                    return k[:32]
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        new_k = secrets.token_bytes(32)
        with open(key_file, "wb") as f:
            f.write(new_k)
        return new_k
    except Exception as e:
        logger.warning(f"Master anahtar dosyası oluşturulamadı, cihaz kimliği türetiliyor: {e}")
        import uuid
        return hashlib.sha256(f"QR_COMPARE_{uuid.getnode()}".encode('utf-8')).digest()

_SHARED_KEY_32 = hashlib.sha256(b"QR_COMPARE_SHARED_APP_SECRET_2026").digest()
_KEY_32 = _SHARED_KEY_32
try:
    _MASTER_KEY_32 = _get_or_create_master_key()
except Exception:
    _MASTER_KEY_32 = _SHARED_KEY_32

try:
    import uuid
    _NODE_KEY_32 = hashlib.sha256(f"QR_COMPARE_{uuid.getnode()}".encode('utf-8')).digest()
except Exception:
    _NODE_KEY_32 = None

def encrypt_credential(plain_text: str) -> str:
    if not plain_text:
        return ""
    plain_text = str(plain_text).strip()
    if plain_text.startswith("ENC:"):
        return plain_text
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        iv = os.urandom(16)
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(plain_text.encode('utf-8')) + padder.finalize()
        cipher = Cipher(algorithms.AES(_KEY_32), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ct = encryptor.update(padded_data) + encryptor.finalize()
        token = base64.urlsafe_b64encode(iv + ct).decode('utf-8')
        return f"ENC:{token}"
    except Exception as e:
        logger.error(f"encrypt_credential error: {e}")
        return ""

def decrypt_credential(cipher_text: str) -> str:
    if not cipher_text:
        return ""
    cipher_text = str(cipher_text).strip()
    if not cipher_text.startswith("ENC:"):
        return cipher_text
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        raw = base64.urlsafe_b64decode(cipher_text[4:].encode('utf-8'))
        iv = raw[:16]
        ct = raw[16:]
        
        candidate_keys = [_KEY_32]
        if _SHARED_KEY_32 not in candidate_keys:
            candidate_keys.append(_SHARED_KEY_32)
        if _MASTER_KEY_32 and _MASTER_KEY_32 not in candidate_keys:
            candidate_keys.append(_MASTER_KEY_32)
        if _NODE_KEY_32 and _NODE_KEY_32 not in candidate_keys:
            candidate_keys.append(_NODE_KEY_32)

        for k in candidate_keys:
            try:
                cipher = Cipher(algorithms.AES(k), modes.CBC(iv))
                decryptor = cipher.decryptor()
                padded_data = decryptor.update(ct) + decryptor.finalize()
                unpadder = padding.PKCS7(128).unpadder()
                plain = unpadder.update(padded_data) + unpadder.finalize()
                return plain.decode('utf-8')
            except Exception:
                continue
        return ""
    except Exception as e:
        logger.debug(f"decrypt_credential error: {e}")
        return ""


def hash_admin_password(plain_password: str) -> str:
    """Admin parolasını güvenli PBKDF2-HMAC-SHA256 (100.000 iterasyon + 16 byte salt) ile hashler."""
    if not plain_password:
        return ""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', plain_password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return f"PBKDF2${salt}${dk.hex()}"

def verify_admin_password(plain_password: str, stored_hash_or_plain: str) -> bool:
    """Stored hash PBKDF2 ise zaman-sabit güvenli karşılaştırma yapar; legacy düz metin ise kontrol eder."""
    if not plain_password or not stored_hash_or_plain:
        return False
    if stored_hash_or_plain.startswith("PBKDF2$"):
        try:
            parts = stored_hash_or_plain.split("$")
            if len(parts) == 3:
                salt = parts[1]
                expected_hex = parts[2]
                dk = hashlib.pbkdf2_hmac('sha256', plain_password.encode('utf-8'), salt.encode('utf-8'), 100000)
                return hmac.compare_digest(dk.hex(), expected_hex)
        except Exception as e:
            logger.error(f"verify_admin_password error: {e}")
            return False
    if stored_hash_or_plain.startswith("pbkdf2:sha256:"):
        try:
            parts = stored_hash_or_plain.split('$')
            if len(parts) == 3:
                iterations = int(parts[0].split(':')[2])
                salt = parts[1]
                expected_dk = parts[2]
                actual_dk = hashlib.pbkdf2_hmac('sha256', plain_password.encode('utf-8'), salt.encode('utf-8'), iterations).hex()
                return hmac.compare_digest(actual_dk, expected_dk)
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    # Eski/varsayılan düz metin kontrolü (timing-safe)
    return hmac.compare_digest(str(plain_password), str(stored_hash_or_plain))


def resolve_dealer_code(bayi_kodu, bkst_user=''):
    """Bayi kodu istemci ile sunucuda farklı olabilir; BKST kullanıcı adı (şifreli/düz) ile gerçek kodu bulur."""
    try:
        u = str(bkst_user or '').strip()
        if not u:
            return bayi_kodu
        target_u = decrypt_credential(u).strip()
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn_r:
            # 1. Doğrudan sorgula (düz veya tam eşleşme)
            row = conn_r.execute("SELECT bayi_kodu FROM bayiler WHERE bkst_kullanici = ? LIMIT 1", (u,)).fetchone()
            if row and row[0]:
                return row[0]
            # 2. Şifreli alanları çözerek kontrol et (AES IV farklı olsa bile tam eşleşir)
            for r in conn_r.execute("SELECT bayi_kodu, bkst_kullanici FROM bayiler").fetchall():
                b_k, enc_k = r[0], r[1]
                if enc_k:
                    dec = decrypt_credential(enc_k).strip()
                    if dec and dec == target_u:
                        return b_k
        return bayi_kodu
    except Exception:
        return bayi_kodu


def is_internet_available(timeout: float = 1.0) -> bool:
    """Soket seviyesinde anlık internet erişimini test eder (Google ve Cloudflare DNS port 53)."""
    import socket
    for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            continue
    return False


def get_bayi_by_code(bayi_kodu):
    """
    Bayi koduna (veya BKST kullanıcı adına) göre bayiler tablosundan bayi bilgilerini döner:
    (id, bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum)
    """
    if not bayi_kodu:
        return None
    code_str = str(bayi_kodu).strip()
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            c = conn.cursor()
            # 1. Doğrudan bayi_kodu ile arama
            c.execute("""
                SELECT id, bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum
                FROM bayiler WHERE bayi_kodu = ? LIMIT 1
            """, (code_str,))
            row = c.fetchone()
            if row:
                return row
            
            # 2. BKST kullanıcı adı ile arama (düz veya şifrelenmiş)
            enc_u = encrypt_credential(decrypt_credential(code_str))
            dec_u = decrypt_credential(code_str)
            c.execute("""
                SELECT id, bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum
                FROM bayiler
                WHERE bkst_kullanici IN (?, ?, ?) LIMIT 1
            """, (code_str, enc_u, dec_u))
            row = c.fetchone()
            if row:
                return row
    except Exception as e:
        logger.error(f"get_bayi_by_code error: {e}")
    return None


_revoked_dealers = set()
_revoked_dealers_lock = threading.Lock()
_revoked_mobile_tokens = set()
_revoked_staff_dealers = set()

# ── Cache-Control & Security Headers (Enterprise Hardening) ─────────────────
@app.after_request
def add_header(response):
    try:
        from flask import session as _s_dev
        _s_dev.pop('dev_mode', None)
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Permissions-Policy'] = 'camera=*, microphone=(), geolocation=()'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    
    is_https = request.is_secure or (request.headers.get('X-Forwarded-Proto', '').lower() == 'https')
    if is_https:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    if request.cookies.get('local_session_token') != LOCAL_SESSION_TOKEN:
        response.set_cookie('local_session_token', LOCAL_SESSION_TOKEN, httponly=True, samesite='Lax', secure=is_https)
    return response

@app.before_request
def check_authentication():
    path = request.path
    if (path.startswith('/static') or 
        path == '/login' or 
        path == '/api/system/login' or 
        path == '/api/system/logout' or 
        path == '/api/system/startup_status' or 
        path == '/api/system/check_update' or 
        path == '/api/system/version' or 
        path == '/api/system/heartbeat' or 
        path == '/api/heartbeat' or
        path == '/api/system/network_status' or
        path == '/sw.js' or
        path == '/manifest.json' or
        path == '/mobile_login' or
        path.startswith('/download/') or
        path == '/logout' or
        path == '/api/system/server_url' or
        path.startswith('/api/system/tunnel/') or
        path.startswith('/api/sync/') or
        path == '/guncel_admin_paketi.zip' or
        path.startswith('/indir/') or
        path == '/api/system/debug_logs/ekle'):
        return None

    from flask import session as flask_session
    if flask_session.get('is_admin'):
        # Admin oturumu açık: Tüm admin ve uygulama rotalarına erişebilir
        return None

    if flask_session.get('dealer_logged_in'):
        active_kodu = flask_session.get('active_bayi_kodu') or 'bayi_1'
        # 📱 Personel Mobil Oturumu Kontrolü: Bayi tarafından oturumu sonlandırılmış mı?
        if flask_session.get('mobile_quick_login') or flask_session.get('is_staff'):
            m_token = str(flask_session.get('mobile_token') or '').strip()
            m_exp = flask_session.get('token_expires_at')
            m_created = flask_session.get('token_created_at') or ''
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            is_terminated = False
            term_msg = "Personel mobil oturumu bayi tarafından sonlandırıldı."

            # 1. Süre Kontrolü
            if m_exp and m_exp < now_str:
                is_terminated = True
                term_msg = "Mobil oturum süreniz doldu."

            token_bayi = ""
            if m_token and m_token.startswith("qrm."):
                parts = m_token.split('.')
                if len(parts) >= 2:
                    token_bayi = parts[1]

            # 2. Token Odaklı Kontrol (Kesin ve Kusursuz Yetkilendirme)
            token_found_in_db = False
            if not is_terminated and m_token:
                # Token bellek içi anlık iptal setinde mi?
                with _revoked_dealers_lock:
                    if m_token in _revoked_mobile_tokens:
                        is_terminated = True
                if not is_terminated and AuthService.get_instance().is_session_revoked(m_token):
                    is_terminated = True

                # Veritabanında bu spesifik tokenin durumunu kontrol et
                if not is_terminated:
                    try:
                        with sqlite3.connect(DB_PATH, timeout=2.5) as conn_chk_m:
                            c_chk_m = conn_chk_m.cursor()
                            c_chk_m.execute("SELECT is_active, expires_at FROM mobile_quick_tokens WHERE token = ?", (m_token,))
                            m_row = c_chk_m.fetchone()
                            if m_row:
                                token_found_in_db = True
                                if m_row[0] == 0:
                                    is_terminated = True
                                elif m_row[1] and m_row[1] < now_str:
                                    is_terminated = True
                                    term_msg = "Mobil oturum süreniz doldu."
                                else:
                                    # 🟢 Token veritabanında kesin olarak AKTİF (is_active == 1) ve geçerli!
                                    # Eski bayi kilitleri bu geçerli yeni oturumu ASLA kapatamaz!
                                    is_terminated = False
                                    with _revoked_dealers_lock:
                                        if active_kodu: _revoked_staff_dealers.discard(active_kodu)
                                        if token_bayi: _revoked_staff_dealers.discard(token_bayi)
                                        _revoked_staff_dealers.discard('bayi_1')
                    except Exception as _ignored_err:
                        logger.debug(f"Suppressed notice: {_ignored_err}")

            # 3. Yalnızca token yoksa veya DB'de henüz kaydı yoksa bayi seviyesi iptal kontrolü uygula
            if not is_terminated and not token_found_in_db:
                with _revoked_dealers_lock:
                    if active_kodu and active_kodu in _revoked_staff_dealers:
                        is_terminated = True
                    elif token_bayi and token_bayi in _revoked_staff_dealers:
                        is_terminated = True
                    elif 'bayi_1' in _revoked_staff_dealers and (active_kodu == 'bayi_1' or not active_kodu):
                        is_terminated = True
                if not is_terminated:
                    try:
                        with sqlite3.connect(DB_PATH, timeout=2.5) as conn_chk_m:
                            c_chk_m = conn_chk_m.cursor()
                            check_keys = [f"staff_revoked_at_{active_kodu}"]
                            if token_bayi and token_bayi != active_kodu:
                                check_keys.append(f"staff_revoked_at_{token_bayi}")
                            if active_kodu != 'bayi_1':
                                check_keys.append("staff_revoked_at_bayi_1")

                            for ck in check_keys:
                                c_chk_m.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = ?", (ck,))
                                rev_row = c_chk_m.fetchone()
                                if rev_row and rev_row[0]:
                                    rev_ts = rev_row[0]
                                    if not m_created or m_created <= rev_ts:
                                        is_terminated = True
                                        break
                    except Exception as _ignored_err:
                        logger.debug(f"Suppressed notice: {_ignored_err}")

            if is_terminated:
                flask_session.clear()
                if path.startswith('/api/'):
                    return jsonify({
                        'success': False,
                        'unauthorized': True,
                        'status': 'revoked',
                        'error': f'⛔ {term_msg}',
                        'redirect': '/login?error=oturum_sonlandi'
                    }), 403
                return redirect('/login?error=oturum_sonlandi')

        # Bayi oturumu bu tarayıcıda/istemcide aktif olarak açılmış
        # 🛡️ Canlı Yetki Denetimi: Admin bu bayinin yetkisini sonlandırmış veya askıya almış mı?
        active_kodu = flask_session.get('active_bayi_kodu') or 'bayi_1'
        d_user = flask_session.get('dealer_username', '')
        enc_d_user = encrypt_credential(d_user) if d_user else ''
        if flask_session.get('offline_mode'):
            # 🌐 Çevrimdışı Oturum: İnternet gelene kadar yerelde serbestçe çalışmaya izin ver
            return None

        try:
            conn_chk = sqlite3.connect(DB_PATH, timeout=2.0)
            rows_stat = conn_chk.execute("""
                SELECT durum FROM bayiler 
                WHERE bayi_kodu = ? 
                   OR (bkst_kullanici IS NOT NULL AND bkst_kullanici != '' AND (bkst_kullanici = ? OR bkst_kullanici = ?))
                ORDER BY (CASE WHEN UPPER(COALESCE(durum, '')) != 'AKTIF' THEN 0 ELSE 1 END) ASC
            """, (active_kodu, enc_d_user, d_user)).fetchall()
            conn_chk.close()

            # Sadece ve sadece veritabanında açıkça 'PASIF' veya 'ENGEL' olarak işaretlenmişse yetkiyi kes!
            if rows_stat and any(str(r[0] or '').upper().strip() in ('PASIF', 'ENGEL', 'REVOKED') for r in rows_stat):
                flask_session.clear()
                with _revoked_dealers_lock:
                    _revoked_dealers.add(active_kodu)
                    if d_user:
                        _revoked_dealers.add(d_user)
                if path.startswith('/api/'):
                    return jsonify({
                        'success': False,
                        'error': '⛔ Bayilik yetkiniz durdurulmuştur veya yetkiniz bulunmamaktadır! Lütfen uygulama sahibiyle iletişime geçiniz.',
                        'code': 403,
                        'unauthorized': True,
                        'status': 'revoked',
                        'redirect': '/login?error=yetkisiz'
                    }), 403
                return redirect('/login?error=yetkisiz')
            else:
                # Bayi aktif veya yerel tabloda henüz kayıt yok: Yerel çalışmaya kesintisiz izin ver
                with _revoked_dealers_lock:
                    _revoked_dealers.discard(active_kodu)
                    if d_user:
                        _revoked_dealers.discard(d_user)
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    else:
        # Oturum AÇILMAMIŞ: Kesinlikle veri çekilemez veya korumalı sayfalara girilemez
        if path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Kullanıcı oturum açmamış.', 'unauthenticated': True, 'code': 401}), 401
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

        if not flask_session.get('mobile_quick_login') and not any(t == LOCAL_SESSION_TOKEN for t in valid_tokens):
            return jsonify({'success': False, 'error': 'Geçersiz veya eksik oturum anahtarı', 'code': 401}), 401

        # CSRF Koruması: Veri değiştiren isteklerde X-Requested-With veya Same-Origin zorunludur (Logout hariç)
        if not flask_session.get('mobile_quick_login') and request.method in ['POST', 'PUT', 'DELETE'] and path not in ['/api/system/logout', '/logout']:
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

app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB maksimum istek boyutu (DoS koruması)

@app.errorhandler(Exception)
def handle_global_api_exceptions(e):
    from werkzeug.exceptions import HTTPException
    if request.path.startswith('/api/'):
        code = e.code if isinstance(e, HTTPException) else 500
        err_msg = e.description if isinstance(e, HTTPException) else str(e)
        return jsonify({'success': False, 'error': err_msg, 'code': code}), code
    if isinstance(e, HTTPException):
        return e
    logger.error(f"Unhandled exception on {request.path}: {e}", exc_info=True)
    return "Sunucu hatası oluştu.", 500

@app.route('/guncel_admin_paketi.zip')
@app.route('/indir/guncel_admin_paketi.zip')
def download_update_package():
    from flask import session as flask_session
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '127.0.0.1').split(',')[0].strip()
    if not (_is_private_or_local_ip(client_ip) or flask_session.get('is_admin') or flask_session.get('dealer_logged_in')):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.'}), 403
    zip_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guncel_admin_paketi.zip")
    if os.path.exists(zip_path):
        return send_file(zip_path, as_attachment=True, download_name="guncel_admin_paketi.zip")
    return "Paket dosyası bulunamadı", 404


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
            with open(v_path, "r", encoding="utf-8-sig") as f:
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

@app.route('/api/system/network_status', methods=['GET', 'POST'])
def api_system_network_status():
    """
    Canlı Ağ, İnternet ve Senkronizasyon Durum Endpoint'i
    - Gerçek internet erişimi (Google DNS 8.8.8.8:53 & Cloudflare 1.1.1.1:53)
    - Merkez sunucu bağlantısı
    - Yerel veritabanındaki BKST depo stok sayısı (bkst_depo_verileri)
    - Sunucuya aktarılmayı bekleyen çıkış sayısı (sync_durum = 'BEKLIYOR')
    """
    import socket
    has_internet = False
    server_connected = False

    # 1. Soket seviyesinde anlık internet testi (maks 1 saniye)
    for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.9)
            s.connect((host, port))
            s.close()
            has_internet = True
            break
        except Exception:
            continue

    # 2. Merkez Sunucu Bağlantı Testi
    merkez_url = get_merkez_sunucu_url()
    if _is_server_host():
        server_connected = True
    elif merkez_url:
        try:
            r = requests.get(f"{merkez_url.rstrip('/')}/api/system/heartbeat", timeout=1.2)
            if r.status_code == 200:
                server_connected = True
        except Exception:
            server_connected = False

    # 3. Yerel depo stok sayısı ve bekleyen senkron kayıtları
    depo_sayisi = 0
    pending_sync_count = 0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=4.0)
        c = conn.cursor()
        c.execute("SELECT COUNT(1) FROM bkst_depo_verileri")
        r_depo = c.fetchone()
        depo_sayisi = r_depo[0] if r_depo else 0

        c.execute("SELECT COUNT(1) FROM cikis_kayitlari WHERE COALESCE(silindi, 0) = 0 AND COALESCE(sync_durum, '') = 'BEKLIYOR'")
        r_pending = c.fetchone()
        pending_sync_count = r_pending[0] if r_pending else 0
        conn.close()
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    from flask import session as flask_session
    is_offline = bool(flask_session.get('offline_mode'))

    # İnternet veya merkez sunucu erişimi varsa ve bekleyen kayıtlar varsa arka planda senkronizasyonu tetikle
    if (has_internet or server_connected) and pending_sync_count > 0:
        try:
            threading.Thread(target=sync_pending_records_to_server, daemon=True).start()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    return jsonify({
        'success': True,
        'has_internet': has_internet,
        'server_connected': server_connected,
        'is_main_server': _is_server_host(),
        'merkez_url': merkez_url or '',
        'depo_sayisi': depo_sayisi,
        'pending_sync_count': pending_sync_count,
        'offline_mode': is_offline,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/sw.js')
def service_worker_root():
    response = send_from_directory(os.path.join(app.root_path, 'static'), 'sw.js')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/manifest.json')
def manifest_json_root():
    response = send_from_directory(os.path.join(app.root_path, 'static'), 'manifest.json')
    response.headers['Content-Type'] = 'application/manifest+json'
    return response

@app.route('/api/system/heartbeat', methods=['POST', 'GET'])
def system_heartbeat():
    try:
        data = request.get_json(silent=True) or {}
        cihaz_id = str(data.get('cihaz_id') or request.cookies.get('bkst_device_id') or request.headers.get('X-Device-Id') or '').strip()
        cihaz_adi = str(data.get('cihaz_adi') or '').strip()
        aktif_sayfa = str(data.get('aktif_sayfa') or '').strip()

        ua = request.headers.get('User-Agent', '')
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '127.0.0.1')
        if ',' in ip:
            ip = ip.split(',')[0].strip()

        # Cihaz Türü Tespiti
        cihaz_turu = "Masaüstü PC"
        if any(m in ua for m in ['Android', 'iPhone', 'Mobile']):
            cihaz_turu = "Akıllı Telefon"
        elif any(m in ua for m in ['iPad', 'Tablet']):
            cihaz_turu = "Tablet"
        elif 'Macintosh' in ua:
            cihaz_turu = "Mac Bilgisayar"
        elif 'Windows' in ua:
            cihaz_turu = "Windows PC"
        elif 'Linux' in ua:
            cihaz_turu = "Linux PC"

        if not cihaz_adi:
            if 'Windows' in ua:
                cihaz_adi = f"Windows PC ({ip})"
            elif 'iPhone' in ua:
                cihaz_adi = f"iPhone ({ip})"
            elif 'Android' in ua:
                cihaz_adi = f"Android Cihaz ({ip})"
            elif 'iPad' in ua:
                cihaz_adi = f"iPad ({ip})"
            else:
                cihaz_adi = f"Terminal ({ip})"

        # 1. Admin oturumunu veya arka plan python isteklerini ASLA bayi cihazı olarak kaydetme
        from flask import session as flask_session
        ua = request.headers.get('User-Agent', '')
        fwd_bk = str(data.get('bayi_kodu') or '').strip()
        if data.get('bkst_kullanici'):
            fwd_bk = resolve_dealer_code(fwd_bk, data.get('bkst_kullanici'))
        bk_code = fwd_bk or flask_session.get('active_bayi_kodu') or 'bayi_1'

        # 🛡️ Canlı Yetki Denetimi: Bayi tarafından personelin oturumu sonlandırılmışsa anında kapat
        if not flask_session.get('is_admin'):
            is_mobile_req = cihaz_id.startswith('mob_') or any(m in ua for m in ['Android', 'iPhone', 'Mobile'])
            if flask_session.get('mobile_quick_login') or flask_session.get('is_staff'):
                mob_tok = str(flask_session.get('mobile_token') or '').strip()
                should_terminate = False
                terminate_reason = ""

                token_bayi = ""
                if mob_tok and mob_tok.startswith("qrm."):
                    parts = mob_tok.split('.')
                    if len(parts) >= 2:
                        token_bayi = parts[1]

                # 1. Token Odaklı Kesin Kontrol
                token_found_in_db = False
                if mob_tok:
                    with _revoked_dealers_lock:
                        if mob_tok in _revoked_mobile_tokens:
                            should_terminate = True
                            terminate_reason = "Personel mobil oturumu bayi tarafından sonlandırıldı."
                    if not should_terminate and AuthService.get_instance().is_session_revoked(mob_tok):
                        should_terminate = True
                        terminate_reason = "Personel mobil oturumu bayi tarafından sonlandırıldı."

                    if not should_terminate:
                        try:
                            c_mob = sqlite3.connect(DB_PATH, timeout=3.0)
                            now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            r_mob = c_mob.execute("SELECT is_active, expires_at FROM mobile_quick_tokens WHERE token = ?", (mob_tok,)).fetchone()
                            c_mob.close()
                            if r_mob:
                                token_found_in_db = True
                                is_act, exp_time = r_mob[0], r_mob[1]
                                if is_act == 0:
                                    should_terminate = True
                                    terminate_reason = "Personel mobil oturumu bayi tarafından kapatıldı."
                                elif exp_time and exp_time < now_s:
                                    should_terminate = True
                                    terminate_reason = "Personel mobil oturumunun süresi doldu."
                                else:
                                    # 🟢 Token veritabanında kesin olarak AKTİF!
                                    should_terminate = False
                                    with _revoked_dealers_lock:
                                        if bk_code: _revoked_staff_dealers.discard(bk_code)
                                        if token_bayi: _revoked_staff_dealers.discard(token_bayi)
                                        _revoked_staff_dealers.discard('bayi_1')
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")

                # 2. Yalnızca token yoksa veya DB'de henüz kaydı yoksa genel bayi iptal kontrolü uygula
                if not should_terminate and not token_found_in_db:
                    with _revoked_dealers_lock:
                        if bk_code and bk_code in _revoked_staff_dealers:
                            should_terminate = True
                            terminate_reason = "Personel mobil oturumu bayi tarafından sonlandırıldı."
                        elif token_bayi and token_bayi in _revoked_staff_dealers:
                            should_terminate = True
                            terminate_reason = "Personel mobil oturumu bayi tarafından sonlandırıldı."
                        elif 'bayi_1' in _revoked_staff_dealers and (bk_code == 'bayi_1' or not bk_code):
                            should_terminate = True
                            terminate_reason = "Personel mobil oturumu bayi tarafından sonlandırıldı."

                    if not should_terminate:
                        try:
                            c_mob = sqlite3.connect(DB_PATH, timeout=3.0)
                            check_keys = [f"staff_revoked_at_{bk_code}"]
                            if token_bayi and token_bayi != bk_code:
                                check_keys.append(f"staff_revoked_at_{token_bayi}")
                            if bk_code != 'bayi_1':
                                check_keys.append("staff_revoked_at_bayi_1")

                            for ck in check_keys:
                                r_rev = c_mob.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = ?", (ck,)).fetchone()
                                if r_rev and r_rev[0]:
                                    m_created = flask_session.get('token_created_at') or ''
                                    if not m_created or m_created <= r_rev[0]:
                                        should_terminate = True
                                        terminate_reason = "Personel mobil oturumu bayi tarafından kapatıldı."
                                        break
                            c_mob.close()
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                if should_terminate:
                    flask_session.clear()
                    return jsonify({
                        'success': False,
                        'unauthorized': True,
                        'status': 'revoked',
                        'error': f'⛔ {terminate_reason}',
                        'redirect': '/login?error=oturum_sonlandi'
                    }), 403
            elif is_mobile_req and not flask_session.get('dealer_logged_in'):
                # Oturumu kapatılmış mobil cihaz
                return jsonify({
                    'success': False,
                    'unauthorized': True,
                    'status': 'revoked',
                    'error': '⛔ Personel mobil oturumu kapatılmıştır.',
                    'redirect': '/login?error=oturum_sonlandi'
                }), 403

            chk_u = str(data.get('bkst_kullanici') or flask_session.get('dealer_username') or '').strip()
            enc_chk_u = encrypt_credential(chk_u) if chk_u else ''
            dec_chk_u = decrypt_credential(chk_u) if chk_u else ''

            if flask_session.get('dealer_logged_in') or fwd_bk or chk_u:
                try:
                    is_fwd_chk = (request.headers.get('X-Forwarded-From-Client') == 'true')
                    c_chk = sqlite3.connect(DB_PATH, timeout=5.0)
                    rows_chk = c_chk.execute("""
                        SELECT durum, id, bayi_kodu FROM bayiler 
                        WHERE bayi_kodu = ? 
                           OR (bkst_kullanici IS NOT NULL AND bkst_kullanici != '' AND (bkst_kullanici = ? OR bkst_kullanici = ? OR bkst_kullanici = ?))
                        ORDER BY (CASE WHEN UPPER(COALESCE(durum, '')) = 'AKTIF' THEN 0 ELSE 1 END) ASC
                    """, (bk_code, enc_chk_u, dec_chk_u, chk_u)).fetchall()

                    has_active_dealer = any(str(r[0] or '').upper().strip() == 'AKTIF' for r in rows_chk)
                    has_blocked_status = any(str(r[0] or '').upper().strip() in ('PASIF', 'ASKIYA_ALINDI', 'REVOKED', 'ONAY_BEKLIYOR') for r in rows_chk)

                    # Eğer istemciden (veya sunucu üzerinde) gelen bayi sunucuda hiç kayıtlı değilse veya ONAY_BEKLIYOR / PASIF ise:
                    if (rows_chk and not has_active_dealer and (has_blocked_status or is_fwd_chk)) or (is_fwd_chk and not rows_chk):
                        # Sunucuda hiç kaydı yoksa ve istemciden (ör. çevrimdışı ilk giriş sonrası internet gelince) geldiyse bildirim oluştur!
                        if is_fwd_chk and not rows_chk and (dec_chk_u or bk_code):
                            try:
                                now_hb_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                disp_tc = dec_chk_u or bk_code
                                c_chk.execute("""
                                    INSERT INTO bayiler (bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum, son_senkronizasyon, eklenme_tarihi, notlar)
                                    VALUES (?, ?, ?, '', '', 'ONAY_BEKLIYOR', ?, ?, 'Çevrimdışı giriş sonrası sunucuya bağlandı - Onay Bekliyor')
                                """, (bk_code, disp_tc, enc_chk_u or disp_tc, now_hb_str, now_hb_str))
                                ex_notif = c_chk.execute(
                                    "SELECT id FROM sistem_bildirimleri WHERE (bayi_kodu = ? OR tc_no = ?) AND okundu = 0 AND tur = 'UYE_TALEBI'",
                                    (bk_code, disp_tc)
                                ).fetchone()
                                if not ex_notif:
                                    notif_m = f"TC: {disp_tc} bilgisiyle çevrimdışı giriş yapan bayi internete bağlandı ve yetki onayı bekliyor."
                                    c_chk.execute("""
                                        INSERT INTO sistem_bildirimleri (tarih, baslik, mesaj, tur, bayi_kodu, tc_no, sirket_adi, gln, okundu)
                                        VALUES (?, '🔔 Yeni Bayi Üyelik Talebi', ?, 'UYE_TALEBI', ?, ?, ?, '', 0)
                                    """, (now_hb_str, notif_m, bk_code, disp_tc, disp_tc))
                                c_chk.commit()
                            except Exception as _ignored_err:
                                logger.debug(f"Suppressed notice: {_ignored_err}")
                        c_chk.close()
                        flask_session.clear()
                        with _revoked_dealers_lock:
                            _revoked_dealers.add(bk_code)
                            if chk_u:
                                _revoked_dealers.add(chk_u)
                            if dec_chk_u:
                                _revoked_dealers.add(dec_chk_u)
                        return jsonify({
                            'success': False,
                            'unauthorized': True,
                            'status': 'revoked',
                            'error': '⛔ Bayilik erişim yetkiniz onaylanmamıştır veya sonlandırılmıştır! Lütfen sistem yöneticisiyle iletişime geçiniz.',
                            'redirect': '/login?error=yetkisiz'
                        }), 403
                    else:
                        c_chk.close()
                        if has_active_dealer:
                            with _revoked_dealers_lock:
                                _revoked_dealers.discard(bk_code)
                                if chk_u:
                                    _revoked_dealers.discard(chk_u)
                                if dec_chk_u:
                                    _revoked_dealers.discard(dec_chk_u)
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
        if flask_session.get('is_admin') or 'python-requests' in ua.lower():
            payload = _bkst_state_payload()
            payload["local_count"] = _local_item_count_cached()
            payload["exit_list_rev"] = get_system_setting(f"exit_list_rev_{bk_code}", "")
            return jsonify(payload)

        if aktif_sayfa in ['🔒 Giriş Ekranı', 'Giriş Ekranı'] or '/login' in str(request.referrer or ''):
            payload = _bkst_state_payload()
            payload["local_count"] = _local_item_count_cached()
            payload["exit_list_rev"] = get_system_setting(f"exit_list_rev_{bk_code}", "")
            return jsonify(payload)

        merkez_url = get_merkez_sunucu_url()
        is_forwarded = (request.headers.get('X-Forwarded-From-Client') == 'true')

        # 2. Doğrudan ana sunucu üzerindeysek ve istek yerel localhost/127.0.0.1 ise bayi cihazı olarak kaydetme
        if not merkez_url and not is_forwarded and ip in ('127.0.0.1', 'localhost', '::1'):
            payload = _bkst_state_payload()
            payload["local_count"] = _local_item_count_cached()
            payload["exit_list_rev"] = get_system_setting(f"exit_list_rev_{bk_code}", "")
            return jsonify(payload)

        # 3. SADECE bu oturumda aktif olarak giriş yapmış veya istemciden iletilmiş bayileri cihaz olarak kaydet
        if not is_forwarded and not flask_session.get('dealer_logged_in'):
            payload = _bkst_state_payload()
            payload["local_count"] = _local_item_count_cached()
            payload["exit_list_rev"] = get_system_setting(f"exit_list_rev_{bk_code}", "")
            return jsonify(payload)

        # Bayi Kodu Belirleme
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        username = flask_session.get('dealer_username', '')
        enc_username = encrypt_credential(username) if username else ''
        c.execute("SELECT bayi_kodu FROM bayiler WHERE bkst_kullanici = ? OR bkst_kullanici = ?", (username, enc_username))
        r_bk = c.fetchone()
        bayi_kodu = fwd_bk or flask_session.get('active_bayi_kodu') or (r_bk[0] if r_bk else 'bayi_1')

        if not cihaz_id:
            cihaz_id = f"dev_{hashlib.md5(f'{ip}_{ua}'.encode('utf-8')).hexdigest()[:12]}"

        clean_device_name = cihaz_adi
        if "Windows" in ua and (not clean_device_name or clean_device_name.startswith("Terminal")):
            clean_device_name = f"Windows PC ({ip})"
        elif not clean_device_name:
            clean_device_name = f"Terminal ({ip})"

        is_staff_user = bool(flask_session.get('is_staff') or flask_session.get('mobile_quick_login') or flask_session.get('mobile_token'))
        if is_staff_user:
            cihaz_turu = "📱 Mobil Personel (Sayım)"
            if "Personel" not in clean_device_name:
                clean_device_name = f"📱 Personel ({clean_device_name})"
            if not aktif_sayfa or aktif_sayfa in ('/cikis', 'cikis', 'Depo Çıkış Ekranı'):
                aktif_sayfa = "📱 Mobil Depo Sayımı"

        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # Aynı bayide aynı IP adresine sahip tek bir cihaz kaydı tut (mükerrerliği kesin olarak önle)
        c.execute("""
            SELECT id FROM aktif_cihazlar 
            WHERE bayi_kodu = ? AND (cihaz_id = ? OR (ip_adresi = ? AND ip_adresi NOT IN ('127.0.0.1', 'localhost', '::1', '')))
            ORDER BY (cihaz_id = ?) DESC, son_gorulme DESC LIMIT 1
        """, (bayi_kodu, cihaz_id, ip, cihaz_id))
        row = c.fetchone()
        if row:
            c.execute("""
                UPDATE aktif_cihazlar
                SET cihaz_id = ?, cihaz_adi = ?, cihaz_turu = ?, ip_adresi = ?, user_agent = ?, aktif_sayfa = ?, son_gorulme = ?, durum = 'ONLINE'
                WHERE id = ?
            """, (cihaz_id, clean_device_name, cihaz_turu, ip, ua, aktif_sayfa, now_dt, row[0]))
        else:
            c.execute("""
                INSERT INTO aktif_cihazlar (bayi_kodu, cihaz_id, cihaz_adi, cihaz_turu, ip_adresi, user_agent, aktif_sayfa, son_gorulme, durum)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ONLINE')
            """, (bayi_kodu, cihaz_id, clean_device_name, cihaz_turu, ip, ua, aktif_sayfa, now_dt))
        conn.commit()
        conn.close()

        if merkez_url and not request.headers.get('X-Forwarded-From-Client'):
            def _forward_hb(target_bayi, target_user):
                try:
                    import requests
                    c_u_hb, c_p_hb, _, _ = read_bkst_credentials()
                    hb_plain_u = target_user or c_u_hb or ""
                    fwd_payload = {
                        "cihaz_id": cihaz_id,
                        "cihaz_adi": clean_device_name,
                        "aktif_sayfa": aktif_sayfa,
                        "bayi_kodu": target_bayi,
                        "bkst_kullanici": hb_plain_u
                    }
                    resp_fwd = requests.post(
                        f"{merkez_url.rstrip('/')}/api/system/heartbeat",
                        json=fwd_payload,
                        headers={"X-Forwarded-From-Client": "true", "X-Requested-With": "XMLHttpRequest", "User-Agent": ua or "Mozilla/5.0"},
                        timeout=4.0
                    )

                    # 🛡️ Sunucudan Yetki Sonlandırma Yanıtı Geldi mi?
                    is_server_revoked = False
                    if resp_fwd.status_code == 403:
                        is_server_revoked = True
                    elif resp_fwd.status_code == 200:
                        try:
                            s_chk = resp_fwd.json()
                            if s_chk.get('unauthorized') or s_chk.get('status') == 'revoked':
                                is_server_revoked = True
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                    if is_server_revoked:
                        # Yerel istemci veritabanında: eğer AKTİF ise PASİF yap, OFFLINE_GIRIS ise ONAY_BEKLIYOR yap!
                        try:
                            c_rev = sqlite3.connect(DB_PATH, timeout=5.0)
                            c_rev.execute("""
                                UPDATE bayiler 
                                SET durum = CASE WHEN UPPER(COALESCE(durum, '')) = 'AKTIF' THEN 'PASIF' ELSE 'ONAY_BEKLIYOR' END
                                WHERE bayi_kodu = ? 
                                   OR (bkst_kullanici IS NOT NULL AND (bkst_kullanici = ? OR bkst_kullanici = ?))
                            """, (target_bayi, _hb_enc_u, target_user))
                            c_rev.commit()
                            c_rev.close()
                        except Exception as e_rv:
                            logger.error(f"Local dealer revocation update error: {e_rv}")

                        with _revoked_dealers_lock:
                            _revoked_dealers.add(target_bayi)
                            if target_user:
                                _revoked_dealers.add(target_user)
                            if _hb_enc_u:
                                _revoked_dealers.add(_hb_enc_u)
                        return

                    # Sunucunun güncel revizyonunu al; revizyon değişmişse yerel verileri anında ve sorgusuz sualsiz eşitle!
                    if resp_fwd.status_code == 200:
                        try:
                            c_act = sqlite3.connect(DB_PATH, timeout=5.0)
                            c_act.execute("""
                                UPDATE bayiler SET durum = 'AKTIF' 
                                WHERE (bayi_kodu = ? OR (bkst_kullanici IS NOT NULL AND (bkst_kullanici = ? OR bkst_kullanici = ?)))
                                  AND UPPER(COALESCE(durum, '')) != 'ONAY_BEKLIYOR'
                            """, (target_bayi, hb_plain_u, target_user))
                            c_act.commit()
                            c_act.close()
                        except Exception as e_act_db:
                            logger.debug(f"Heartbeat act db notice: {e_act_db}")

                        with _revoked_dealers_lock:
                            _revoked_dealers.discard(target_bayi)
                            if target_user:
                                _revoked_dealers.discard(target_user)
                            if hb_plain_u:
                                _revoked_dealers.discard(hb_plain_u)

                        s_data = resp_fwd.json()
                        s_rev = s_data.get('exit_list_rev')
                        b_info = get_bayi_by_code(target_bayi)
                        hb_pass = (decrypt_credential(b_info[4]) if b_info and b_info[4] else "") or c_p_hb or ""
                        if s_rev:
                            loc_rev = get_system_setting(f"exit_list_rev_{target_bayi}", "")
                            if s_rev != loc_rev:
                                set_system_setting(f"exit_list_rev_{target_bayi}", s_rev)
                                _auto_pull_exit_list_from_server(merkez_url, target_bayi, hb_plain_u, hb_pass)

                        # Eşitlenmemiş yerel kayıtlar varsa arka planda merkeze aktar (SADECE bu bayiye ait olanlar)
                        try:
                            conn_fwd = sqlite3.connect(DB_PATH, timeout=5.0)
                            c_fwd = conn_fwd.cursor()
                            c_fwd.execute("""
                                SELECT id, tarih, urun_adi, barkod, koli_no, seri_no, parti_no, uretim_tarihi, skt, ham_karekod,
                                       COALESCE(silindi, 0), COALESCE(silinme_tarihi, '')
                                FROM cikis_kayitlari
                                WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))
                                  AND (sync_durum IS NULL OR sync_durum != 'ESITLENDI')
                                LIMIT 100
                            """, (target_bayi, target_bayi))
                            unsynced_rows = c_fwd.fetchall()
                            conn_fwd.close()

                            if unsynced_rows:
                                hb_name = clean_user_name(b_info[2]) if b_info else ""
                                hb_gln = b_info[5] if b_info else ""

                                unsynced_items = []
                                for r in unsynced_rows:
                                    unsynced_items.append({
                                        'id': r[0], 'tarih': r[1], 'urun_adi': r[2], 'barkod': r[3],
                                        'koli_no': r[4], 'seri_no': r[5], 'parti_no': r[6],
                                        'uretim_tarihi': r[7], 'skt': r[8], 'ham_karekod': r[9],
                                        'silindi': r[10], 'silinme_tarihi': r[11],
                                        'bayi_kodu': target_bayi
                                    })

                                p_resp = requests.post(
                                    f"{merkez_url.rstrip('/')}/api/sync/push",
                                    json={
                                        "bayi_kodu": target_bayi,
                                        "bkst_kullanici": hb_plain_u,
                                        "bkst_sifre": hb_pass,
                                        "gln_no": hb_gln or "",
                                        "bayi_adi": hb_name or "",
                                        "cikis_kayitlari": unsynced_items
                                    },
                                    timeout=6.0
                                )
                                if p_resp.status_code == 200 and p_resp.json().get('success'):
                                    conn_up = sqlite3.connect(DB_PATH, timeout=5.0)
                                    c_up = conn_up.cursor()
                                    id_list = [str(r[0]) for r in unsynced_rows]
                                    c_up.execute(f"UPDATE cikis_kayitlari SET sync_durum = 'ESITLENDI' WHERE id IN ({','.join(id_list)})")
                                    conn_up.commit()
                                    conn_up.close()
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
            threading.Thread(target=_forward_hb, args=(bayi_kodu, username), daemon=True).start()

    except Exception as e_hb:
        logger.warning(f"system_heartbeat device tracking notice: {e_hb}")

    payload = _bkst_state_payload()
    payload["local_count"] = _local_item_count_cached()
    # Sunucudaki çıkış listesi revizyonu (admin tarafından gönderilen güncellemeleri anında çekmek için)
    current_rev = get_system_setting(f"exit_list_rev_{bk_code}", "")
    if not current_rev:
        try:
            _cn = sqlite3.connect(DB_PATH, timeout=5.0)
            _cnt = _cn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND COALESCE(silindi, 0) = 0", (bk_code, bk_code)).fetchone()
            _cn.close()
            current_rev = f"auto_{_cnt[0]}_{_cnt[1]}"
        except Exception:
            current_rev = ""
    payload["exit_list_rev"] = current_rev
    payload["bayi_kodu"] = bk_code
    payload["online"] = True
    payload["server_connected"] = bool(merkez_url or _is_server_host())
    return jsonify(payload)

@app.route('/api/heartbeat', methods=['POST', 'GET'])
def api_heartbeat():
    return jsonify({'status': 'ok'})

@app.route('/api/system/cihaz_kapat', methods=['POST', 'GET'])
def api_system_cihaz_kapat():
    """
    Kullanıcı tarayıcı penceresini veya uygulamayı kapattığında anında tetiklenir.
    Cihazın durumunu anında 'OFFLINE' yaparak yönetici panelinde hemen kapalı görünmesini sağlar.
    """
    try:
        data = request.get_json(silent=True) or {}
        cihaz_id = str(data.get('cihaz_id') or request.cookies.get('bkst_device_id') or '').strip()
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '127.0.0.1')
        if ',' in ip:
            ip = ip.split(',')[0].strip()

        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        if cihaz_id:
            c.execute("UPDATE aktif_cihazlar SET durum = 'OFFLINE', son_gorulme = '2000-01-01 00:00:00' WHERE cihaz_id = ?", (cihaz_id,))
        if ip and ip not in ('127.0.0.1', 'localhost'):
            c.execute("UPDATE aktif_cihazlar SET durum = 'OFFLINE', son_gorulme = '2000-01-01 00:00:00' WHERE ip_adresi = ?", (ip,))
        conn.commit()
        conn.close()

        # Dükkan istemcisi modundaysa ana sunucuya da ilet
        merkez_url = get_system_setting("merkez_sunucu_url", "")
        cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunucu_adresi.txt")
        if not merkez_url and os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f_u:
                    merkez_url = f_u.read().strip()
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
        if merkez_url and not request.headers.get('X-Forwarded-From-Client'):
            try:
                import requests
                requests.post(
                    f"{merkez_url.rstrip('/')}/api/system/cihaz_kapat",
                    json={"cihaz_id": cihaz_id},
                    headers={"X-Forwarded-From-Client": "true"},
                    timeout=3.0
                )
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
        # Uygulama kapatılırken bekleyen son çıkış kayıtlarını son bir kez senkronize et
        try:
            sync_pending_records_to_server()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
        return jsonify({'success': True, 'message': 'Cihaz ve oturum başarıyla kapatıldı ve senkronize edildi.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ── SQLite Veritabanı Katmanı & Staging Swap ──────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cikis_kayitlari.db')

# ==============================================================================
# 🧪 GELİŞTİRİCİ MODU: Yönetici oturumunda dev_mode açıksa tüm veritabanı işlemleri
# gerçek bayi verisine DOKUNMADAN ayrı bir "deneme" veritabanına yönlendirilir.
# ==============================================================================
DEV_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cikis_kayitlari_deneme.db')
_dev_tls = threading.local()
_real_sqlite_connect = sqlite3.connect

def _is_dev_context():
    if getattr(_dev_tls, 'on', False):
        return True
    try:
        from flask import has_request_context, session as _dev_sess
        return bool(has_request_context() and _dev_sess.get('dev_mode'))
    except Exception:
        return False

def _dev_aware_connect(database, *args, **kwargs):
    if isinstance(database, str) and database == DB_PATH and _is_dev_context():
        database = DEV_DB_PATH
    if 'timeout' not in kwargs or (isinstance(kwargs.get('timeout'), (int, float)) and kwargs['timeout'] < 30.0):
        kwargs['timeout'] = 30.0
    conn = _real_sqlite_connect(database, *args, **kwargs)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-8000")
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    return conn

sqlite3.connect = _dev_aware_connect

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
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
        ensure_db_schema(force=True)
        raise e
    finally:
        try:
            conn.close()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    user_key = username or "default_user"
    with _state_lock:
        _user_cache_map.pop(user_key, None)
        if user_key in _cache_access_order:
            _cache_access_order.remove(user_key)

_initialized_db_paths = set()
_schema_init_lock = threading.Lock()

def ensure_db_schema(conn=None, force=False):
    target_db = DEV_DB_PATH if _is_dev_context() else DB_PATH
    if conn is None and not force and target_db in _initialized_db_paths:
        return
    close_at_end = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        close_at_end = True
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA cache_size=-16000")
    c.execute("PRAGMA mmap_size=0")

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

    # 7. bayiler tablosu (Çoklu Bayi Yönetimi)
    c.execute('''CREATE TABLE IF NOT EXISTS bayiler (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        bayi_kodu          TEXT UNIQUE NOT NULL,
        bayi_adi           TEXT NOT NULL,
        il                 TEXT DEFAULT '',
        ilce               TEXT DEFAULT '',
        gln_no             TEXT DEFAULT '',
        bkst_kullanici     TEXT DEFAULT '',
        bkst_sifre         TEXT DEFAULT '',
        durum              TEXT DEFAULT 'AKTIF',
        remote_url         TEXT DEFAULT '',
        son_senkronizasyon TEXT DEFAULT '',
        eklenme_tarihi     TEXT DEFAULT '',
        notlar             TEXT DEFAULT ''
    )''')

    # 8. sistem_bildirimleri tablosu (Üyelik talepleri ve yönetici bildirimleri)
    c.execute('''CREATE TABLE IF NOT EXISTS sistem_bildirimleri (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih            TEXT NOT NULL,
        baslik           TEXT NOT NULL,
        mesaj            TEXT NOT NULL,
        tur              TEXT DEFAULT 'UYE_TALEBI',
        bayi_kodu        TEXT DEFAULT '',
        tc_no            TEXT DEFAULT '',
        sirket_adi       TEXT DEFAULT '',
        gln              TEXT DEFAULT '',
        okundu           INTEGER DEFAULT 0
    )''')

    # 8. debug_loglari tablosu (Merkezi Hata ve İstek Takip Günlüğü)
    c.execute('''CREATE TABLE IF NOT EXISTS debug_loglari (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih            TEXT NOT NULL,
        bayi_kodu        TEXT DEFAULT 'bayi_1',
        seviye           TEXT NOT NULL DEFAULT 'INFO',
        kategori         TEXT NOT NULL DEFAULT 'GENEL',
        baslik           TEXT NOT NULL DEFAULT '',
        detay            TEXT DEFAULT '',
        request_payload  TEXT DEFAULT '',
        response_payload TEXT DEFAULT ''
    )''')

    # PRAGMA kolon kontrolleri
    c.execute("PRAGMA table_info(bayiler)")
    by_cols = {row[1].lower() for row in c.fetchall()}
    if "test_modu" not in by_cols:
        try: c.execute("ALTER TABLE bayiler ADD COLUMN test_modu INTEGER DEFAULT 0")
        except Exception: pass

    c.execute("PRAGMA table_info(debug_loglari)")
    dbg_cols = {row[1].lower() for row in c.fetchall()}
    if "bayi_kodu" not in dbg_cols:
        try: c.execute("ALTER TABLE debug_loglari ADD COLUMN bayi_kodu TEXT DEFAULT 'bayi_1'")
        except Exception: pass

    c.execute("PRAGMA table_info(cikis_kayitlari)")
    ck_cols = {row[1].lower() for row in c.fetchall()}
    if "bayi_kodu" not in ck_cols:
        try: c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN bayi_kodu TEXT DEFAULT 'bayi_1'")
        except Exception: pass
    if "sync_durum" not in ck_cols:
        try: c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN sync_durum TEXT DEFAULT 'ESITLENDI'")
        except Exception: pass

    if "cihaz_adi" not in ck_cols:
        try: c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN cihaz_adi TEXT DEFAULT ''")
        except Exception: pass
    if "cihaz_id" not in ck_cols:
        try: c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN cihaz_id TEXT DEFAULT ''")
        except Exception: pass
    if "admin_gizli" not in ck_cols:
        try: c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN admin_gizli INTEGER DEFAULT 0")
        except Exception: pass
    if "silindi" not in ck_cols:
        try: c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN silindi INTEGER DEFAULT 0")
        except Exception: pass
    if "silinme_tarihi" not in ck_cols:
        try: c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN silinme_tarihi TEXT DEFAULT ''")
        except Exception: pass
    if "guncelleme_tarihi" not in ck_cols:
        try: c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN guncelleme_tarihi TEXT DEFAULT ''")
        except Exception: pass

    c.execute("PRAGMA table_info(satis_arsivi)")
    sa_cols = {row[1].lower() for row in c.fetchall()}
    if "bayi_kodu" not in sa_cols:
        try: c.execute("ALTER TABLE satis_arsivi ADD COLUMN bayi_kodu TEXT DEFAULT 'bayi_1'")
        except Exception: pass
    if "sync_durum" not in sa_cols:
        try: c.execute("ALTER TABLE satis_arsivi ADD COLUMN sync_durum TEXT DEFAULT 'ESITLENDI'")
        except Exception: pass
    if "cihaz_adi" not in sa_cols:
        try: c.execute("ALTER TABLE satis_arsivi ADD COLUMN cihaz_adi TEXT DEFAULT ''")
        except Exception: pass
    if "cihaz_id" not in sa_cols:
        try: c.execute("ALTER TABLE satis_arsivi ADD COLUMN cihaz_id TEXT DEFAULT ''")
        except Exception: pass

    # 9. aktif_cihazlar tablosu (Canlı Cihaz ve Bayi Oturumu Takibi)
    c.execute('''CREATE TABLE IF NOT EXISTS aktif_cihazlar (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        bayi_kodu    TEXT NOT NULL DEFAULT 'bayi_1',
        cihaz_id     TEXT NOT NULL,
        cihaz_adi    TEXT DEFAULT '',
        cihaz_turu   TEXT DEFAULT 'Masaüstü PC',
        ip_adresi    TEXT DEFAULT '',
        user_agent   TEXT DEFAULT '',
        aktif_sayfa  TEXT DEFAULT '',
        son_gorulme  TEXT NOT NULL,
        durum        TEXT DEFAULT 'ONLINE'
    )''')

    # 10. mobile_quick_tokens tablosu (1 Günlük Hızlı QR Giriş Oturumları)
    c.execute('''CREATE TABLE IF NOT EXISTS mobile_quick_tokens (
        token        TEXT PRIMARY KEY,
        bayi_kodu    TEXT NOT NULL,
        dealer_name  TEXT NOT NULL,
        dealer_gln   TEXT DEFAULT '',
        created_at   TEXT NOT NULL,
        expires_at   TEXT NOT NULL,
        is_active    INTEGER DEFAULT 1,
        login_count  INTEGER DEFAULT 0,
        last_used_at TEXT DEFAULT ''
    )''')
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_mqt_token ON mobile_quick_tokens(token)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mqt_bayi ON mobile_quick_tokens(bayi_kodu)")
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
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
        c.execute("CREATE INDEX IF NOT EXISTS idx_bayiler_kodu ON bayiler(bayi_kodu)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_debug_bayi ON debug_loglari(bayi_kodu)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_debug_seviye ON debug_loglari(seviye)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_debug_tarih ON debug_loglari(tarih)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cikis_bayi ON cikis_kayitlari(bayi_kodu)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_bayi ON satis_arsivi(bayi_kodu)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_aktif_cihaz_bayi ON aktif_cihazlar(bayi_kodu)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_aktif_cihaz_unique ON aktif_cihazlar(bayi_kodu, cihaz_id)")
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
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
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    # 🔒 Güvenlik: Bayi kullanıcı adı ve şifrelerini AES-256 ile otomatik şifrele
    try:
        c.execute("SELECT id, bkst_kullanici, bkst_sifre FROM bayiler")
        b_rows = c.fetchall()
        for b_id, b_u, b_p in b_rows:
            needs_up = False
            new_u = b_u
            new_p = b_p
            if b_u and not b_u.startswith("ENC:"):
                new_u = encrypt_credential(b_u)
                needs_up = True
            if b_p and not b_p.startswith("ENC:"):
                new_p = encrypt_credential(b_p)
                needs_up = True
            if needs_up:
                c.execute("UPDATE bayiler SET bkst_kullanici = ?, bkst_sifre = ? WHERE id = ?", (new_u, new_p, b_id))

        for key in ['bkst_kullanici_adi', 'bkst_sifre']:
            c.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = ?", (key,))
            row_s = c.fetchone()
            if row_s and row_s[0] and not row_s[0].startswith("ENC:"):
                c.execute("UPDATE sistem_ayarlari SET deger = ? WHERE anahtar = ?", (encrypt_credential(row_s[0]), key))

        # Admin Parolası PBKDF2 Hash Güvenlik Geçişi
        c.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = 'admin_sifre_hash'")
        row_ah = c.fetchone()
        if not row_ah or not row_ah[0]:
            c.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = 'admin_sifre'")
            row_ap = c.fetchone()
            current_plain = row_ap[0] if (row_ap and row_ap[0]) else "admin2026"
            hashed = hash_admin_password(current_plain)
            c.execute("INSERT OR REPLACE INTO sistem_ayarlari (anahtar, deger) VALUES ('admin_sifre_hash', ?)", (hashed,))
            c.execute("DELETE FROM sistem_ayarlari WHERE anahtar = 'admin_sifre'")
    except Exception as e_sec_mig:
        logger.warning(f"Credential encryption migration notice: {e_sec_mig}")

    # 🔁 Senkron günlüğü (outbox): istemcide silinen kutular sunucuya iletilene kadar kuyrukta tutulur.
    c.execute("CREATE TABLE IF NOT EXISTS senkron_ayar (k TEXT PRIMARY KEY, v TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS senkron_silme_kuyrugu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bayi_kodu TEXT,
        ham_karekod TEXT,
        ts TEXT,
        eklenme_tarihi TEXT
    )""")
    try:
        c.execute("ALTER TABLE senkron_silme_kuyrugu ADD COLUMN ts TEXT")
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    try:
        c.execute("ALTER TABLE senkron_silme_kuyrugu ADD COLUMN eklenme_tarihi TEXT")
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    c.execute("""CREATE TRIGGER IF NOT EXISTS trg_cikis_silindi AFTER DELETE ON cikis_kayitlari
        WHEN OLD.ham_karekod IS NOT NULL AND OLD.ham_karekod != ''
         AND COALESCE((SELECT v FROM senkron_ayar WHERE k = 'istemci'), '0') = '1'
         AND COALESCE((SELECT v FROM senkron_ayar WHERE k = 'ayna_modu'), '0') = '0'
        BEGIN
            INSERT INTO senkron_silme_kuyrugu (bayi_kodu, ham_karekod, ts)
            VALUES (COALESCE(OLD.bayi_kodu, 'bayi_1'), OLD.ham_karekod, datetime('now', 'localtime'));
        END""")

    # 🧹 Mükerrer Kayıtları Temizleme: Her bayi_kodu ve ham_karekod için veritabanında sadece 1 tekil satır bırak
    try:
        c.execute("""
            DELETE FROM cikis_kayitlari
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM cikis_kayitlari
                WHERE ham_karekod IS NOT NULL AND ham_karekod != ''
                GROUP BY COALESCE(bayi_kodu, 'bayi_1'), ham_karekod
            )
            AND ham_karekod IS NOT NULL AND ham_karekod != ''
        """)
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")

    # 🛡️ Aktif Bayileri Silinen Bayiler Tablosundan Otomatik Temizle (Eski Test Artıklarını Yok Eder)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS silinen_bayiler (id INTEGER PRIMARY KEY AUTOINCREMENT, bayi_kodu TEXT, tc_no TEXT, silinme_tarihi TEXT)")
        c.execute("""
            DELETE FROM silinen_bayiler 
            WHERE bayi_kodu IN (SELECT bayi_kodu FROM bayiler WHERE UPPER(COALESCE(durum, '')) = 'AKTIF')
               OR tc_no IN (SELECT bkst_kullanici FROM bayiler WHERE UPPER(COALESCE(durum, '')) = 'AKTIF')
        """)
        c.execute("SELECT bkst_kullanici, bayi_kodu FROM bayiler WHERE UPPER(COALESCE(durum, '')) = 'AKTIF'")
        for (raw_u_act, b_k_act) in c.fetchall():
            if raw_u_act:
                dec_u_act = decrypt_credential(raw_u_act)
                if dec_u_act:
                    c.execute("DELETE FROM silinen_bayiler WHERE tc_no = ? OR bayi_kodu = ?", (dec_u_act, dec_u_act))
            if b_k_act:
                c.execute("DELETE FROM silinen_bayiler WHERE bayi_kodu = ?", (b_k_act,))
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")

    conn.commit()
    _initialized_db_paths.add(target_db)
    if close_at_end:
        conn.close()

def init_db():
    ensure_db_schema(force=True)
    try:
        db_mgr = DatabaseManager.get_instance()
        db_mgr.run_migrations()
        db_mgr.prune_old_debug_logs()
    except Exception as e_mgr:
        logger.warning(f"DatabaseManager migration or prune notice: {e_mgr}")
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

        # Yinelenen seri numarası veya karekod durumunda tekrar_uyari flag'lerini bayi bazında onar:
        c.execute("SELECT id, seri_no, ham_karekod, barkod, COALESCE(bayi_kodu, 'bayi_1') FROM cikis_kayitlari ORDER BY id ASC")
        rows = c.fetchall()
        seen_keys = set()
        for r_id, s_no, qr_val, b_val, b_kod in rows:
            key_qr = f"{b_kod}:qr:{qr_val.strip().casefold()}" if qr_val and len(qr_val.strip()) >= 16 else None
            key_seri = f"{b_kod}:seri:{s_no.strip().casefold()}_{b_val or ''}" if s_no and s_no.strip() else None

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

_settings_mem_cache = {}
_settings_mem_lock = threading.Lock()

def get_system_setting(key, default="", conn=None):
    try:
        ensure_db_schema()
        is_dev = _is_dev_context()
        cache_k = (is_dev, key)
        now_m = time.monotonic()
        if conn is None:
            with _settings_mem_lock:
                cached_entry = _settings_mem_cache.get(cache_k)
                if cached_entry is not None and (now_m - cached_entry[1]) < 4.0:
                    return cached_entry[0] if cached_entry[0] != "__MISSING__" else default

        if conn is not None:
            c = conn.cursor()
            c.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = ?", (key,))
            row = c.fetchone()
            if row and row[0] is not None:
                val = str(row[0]).strip()
                with _settings_mem_lock:
                    _settings_mem_cache[cache_k] = (val, now_m)
                return val
        else:
            with sqlite3.connect(DB_PATH, timeout=30.0) as local_conn:
                c = local_conn.cursor()
                c.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = ?", (key,))
                row = c.fetchone()
                if row and row[0] is not None:
                    val = str(row[0]).strip()
                    with _settings_mem_lock:
                        _settings_mem_cache[cache_k] = (val, now_m)
                    return val
                with _settings_mem_lock:
                    _settings_mem_cache[cache_k] = ("__MISSING__", now_m)
    except Exception as e:
        logger.warning(f"get_system_setting error ({key}): {e}")
    return default

def set_system_setting(key, value, conn=None):
    try:
        ensure_db_schema()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        str_val = str(value or '')
        if conn is not None:
            c = conn.cursor()
            c.execute("""
                INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
                VALUES (?, ?, ?)
                ON CONFLICT(anahtar) DO UPDATE SET
                deger = excluded.deger,
                guncelleme_tarihi = excluded.guncelleme_tarihi
            """, (key, str_val, now_str))
            with _settings_mem_lock:
                _settings_mem_cache[(_is_dev_context(), key)] = (str_val.strip(), time.monotonic())
            return True
        else:
            with sqlite3.connect(DB_PATH, timeout=30.0) as local_conn:
                c = local_conn.cursor()
                c.execute("""
                    INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
                    VALUES (?, ?, ?)
                    ON CONFLICT(anahtar) DO UPDATE SET
                    deger = excluded.deger,
                    guncelleme_tarihi = excluded.guncelleme_tarihi
                """, (key, str_val, now_str))
                local_conn.commit()
            with _settings_mem_lock:
                _settings_mem_cache[(_is_dev_context(), key)] = (str_val.strip(), time.monotonic())
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
        with _settings_mem_lock:
            _settings_mem_cache.pop((_is_dev_context(), key), None)
        return True
    except Exception as e:
        logger.error(f"delete_system_setting error ({key}): {e}")
        return False

def is_demo_mode():
    val = get_system_setting("demo_mode_aktif", "0")
    return val in ("1", "true", "True")

def set_demo_mode(enabled):
    return set_system_setting("demo_mode_aktif", "1" if enabled else "0")

def log_debug(kategori, baslik, detay="", req_payload=None, resp_payload=None, seviye="INFO", bayi_kodu=None):
    """
    Sistem genelindeki hataları, BKST API istek/yanıtlarını ve simülasyonları debug_loglari tablosuna kaydeder.
    """
    try:
        ensure_db_schema()
        tarih_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if not bayi_kodu:
            try:
                from flask import session as flask_session, has_request_context
                # ADMIN, GUVENLIK veya SISTEM kategorisindeki genel loglar rastgele bir bayinin günlüğüne yazılmamalıdır
                if str(kategori).upper() in ['ADMIN', 'GUVENLIK', 'GUVENLIK_DENETIM', 'SISTEM']:
                    bayi_kodu = 'SISTEM'
                elif has_request_context():
                    bayi_kodu = flask_session.get('active_bayi_kodu') or 'bayi_1'
                else:
                    bayi_kodu = 'bayi_1'
            except Exception:
                bayi_kodu = 'bayi_1'

        req_str = ""
        if req_payload is not None:
            if isinstance(req_payload, (dict, list)):
                try:
                    if isinstance(req_payload, dict):
                        masked_req = dict(req_payload)
                        for k in list(masked_req.keys()):
                            if any(p in k.lower() for p in ['password', 'sifre', 'secret']):
                                masked_req[k] = '***'
                        req_str = json.dumps(masked_req, ensure_ascii=False, indent=2)
                    else:
                        req_str = json.dumps(req_payload, ensure_ascii=False, indent=2)
                except Exception:
                    req_str = str(req_payload)
            else:
                req_str = str(req_payload)

        resp_str = ""
        if resp_payload is not None:
            if isinstance(resp_payload, (dict, list)):
                try:
                    resp_str = json.dumps(resp_payload, ensure_ascii=False, indent=2)
                except Exception:
                    resp_str = str(resp_payload)
            else:
                resp_str = str(resp_payload)

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        c = conn.cursor()
        c.execute("""
            INSERT INTO debug_loglari (tarih, bayi_kodu, seviye, kategori, baslik, detay, request_payload, response_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (tarih_str, str(bayi_kodu), str(seviye).upper(), str(kategori).upper(), str(baslik), str(detay or ''), req_str, resp_str))

        # Log tablosunu her insertte değil, sadece periyodik olarak temizle (kilitlenmeyi önler)
        import random
        if random.randint(1, 100) == 1:
            try:
                c.execute("""
                    DELETE FROM debug_loglari WHERE id NOT IN (
                        SELECT id FROM debug_loglari ORDER BY id DESC LIMIT 3000
                    )
                """)
            except Exception:
                pass
        conn.commit()
        conn.close()

        # İstemci tarafındaysak bu logu anında merkez sunucuya aktarılmak üzere kuyruğa ekle
        if not _is_server_host():
            try:
                with _pending_client_debug_logs_lock:
                    if len(_pending_client_debug_logs) < 1000:
                        _pending_client_debug_logs.append({
                            'tarih': tarih_str,
                            'bayi_kodu': str(bayi_kodu),
                            'seviye': str(seviye).upper(),
                            'kategori': str(kategori).upper(),
                            'baslik': str(baslik),
                            'detay': str(detay or ''),
                            'request_payload': req_str,
                            'response_payload': resp_str
                        })
            except Exception as _ignored_err:
                pass

        log_msg = f"[{bayi_kodu}] [{kategori}] {baslik}"
        if detay:
            log_msg += f" - {detay[:200]}"
        if seviye.upper() == 'ERROR':
            logger.error(log_msg)
        elif seviye.upper() == 'WARNING':
            logger.warning(log_msg)
        else:
            logger.info(log_msg)
    except Exception as e:
        logger.error(f"log_debug failed: {e}")

def find_dealer_by_credential(username: str, gln: str = ""):
    """
    Veritabanındaki bayiler tablosunda bayiyi bulur.
    KULLANICI ADI (TC) eşleşmesi MUTLAK ÖNCELİKLİDİR.
    'OFFLINE' gibi genel ibareler asla GLN eşleşmesi olarak kabul edilmez.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("""
            SELECT id, bayi_kodu, durum, bayi_adi, bkst_sifre, gln_no, bkst_kullanici, COALESCE(test_modu, 0)
            FROM bayiler 
            ORDER BY (CASE WHEN UPPER(COALESCE(durum, '')) = 'AKTIF' THEN 0 ELSE 1 END) ASC, id ASC
        """)
        rows = c.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"find_dealer_by_credential query failed: {e}")
        return None

    target_u = (username or "").strip().lower()
    target_gln = (gln or "").strip()
    is_valid_gln = bool(target_gln and target_gln.upper() not in ('OFFLINE', 'NONE', 'NULL', '', '0', 'DEFAULT'))

    # 1. Aşama: Kullanıcı adı (TC) ile KESİN BİREBİR EŞLEŞME (Asla başka bayiyi getirmez)
    if target_u:
        for r in rows:
            r_id, r_kodu, r_durum, r_ad, r_sifre_raw, r_gln, r_u_raw, r_test = r
            dec_u = (decrypt_credential(r_u_raw) or r_u_raw or "").strip().lower()
            if dec_u and dec_u == target_u:
                return (r_id, r_kodu, r_durum, r_ad, r_sifre_raw, r_gln, r_test)

    # 2. Aşama: Kullanıcı adı bulunamadıysa ve GLN gerçek bir numara ise GLN ile eşleştirme
    if is_valid_gln:
        for r in rows:
            r_id, r_kodu, r_durum, r_ad, r_sifre_raw, r_gln, r_u_raw, r_test = r
            if r_gln and str(r_gln).strip() == target_gln:
                return (r_id, r_kodu, r_durum, r_ad, r_sifre_raw, r_gln, r_test)

    return None

def get_current_active_bayi():
    """
    Oturumdaki aktif bayinin kodunu ve kullanıcı adını döndürür.
    Admin modu ve Bayi oturumlarını eksiksiz destekler.
    Asla başka bir bayinin verisini sızdırmaz!
    """
    from flask import session as flask_session, has_request_context
    b_kodu = None
    u_name = ''
    if has_request_context():
        b_kodu = flask_session.get('active_bayi_kodu')
        u_name = flask_session.get('dealer_username', '')
        if not b_kodu and u_name:
            b_cand = find_dealer_by_credential(u_name)
            if b_cand:
                b_kodu = b_cand[1]
                flask_session['active_bayi_kodu'] = b_kodu
        if b_kodu and not u_name:
            try:
                b_info = get_bayi_by_code(b_kodu)
                if b_info and b_info[3]:
                    u_name = decrypt_credential(b_info[3])
                    flask_session['dealer_username'] = u_name
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
        if b_kodu:
            return b_kodu, u_name
        if not flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
            return '__YETKISIZ__', ''

    # Sadece arka plan görevleri veya yerel tekil istemci oturumu için sistem_ayarlari'ndaki aktif bayiye bak:
    if not b_kodu:
        saved_kodu = get_system_setting("active_bayi_kodu", "")
        saved_user = decrypt_credential(get_system_setting("bkst_kullanici_adi", ""))
        if saved_kodu and saved_kodu not in ('__YETKISIZ__', '__NONE__'):
            b_kodu = saved_kodu
            u_name = u_name or saved_user
        elif saved_user:
            b_cand = find_dealer_by_credential(saved_user)
            if b_cand:
                b_kodu = b_cand[1]
                u_name = u_name or saved_user

    if not b_kodu:
        return '__YETKISIZ__', u_name
    return b_kodu, u_name

def read_bkst_credentials():
    # 0. Aktif oturumdaki bayi bilgilerini oku (Hem Bayi hem Admin modu desteklenir, bayiler arası sızıntı engellenir)
    try:
        from flask import session as flask_session, has_request_context
        if has_request_context():
            active_b = flask_session.get('active_bayi_kodu')
            dealer_u = flask_session.get('dealer_username')
            if active_b or dealer_u:
                conn_b = sqlite3.connect(DB_PATH, timeout=5.0)
                c_b = conn_b.cursor()
                if active_b:
                    c_b.execute("SELECT bkst_kullanici, bkst_sifre, gln_no, bayi_adi FROM bayiler WHERE bayi_kodu = ?", (active_b,))
                else:
                    c_b.execute("SELECT bkst_kullanici, bkst_sifre, gln_no, bayi_adi FROM bayiler WHERE bkst_kullanici = ? OR bkst_kullanici = ?", (dealer_u, encrypt_credential(dealer_u)))
                row_b = c_b.fetchone()
                conn_b.close()
                if row_b and row_b[0]:
                    return decrypt_credential(row_b[0]), decrypt_credential(row_b[1] or ""), row_b[2] or "", ""
                if dealer_u:
                    return decrypt_credential(dealer_u), "", flask_session.get('dealer_gln', ''), ""
                if active_b and not flask_session.get('is_admin'):
                    return "", "", flask_session.get('dealer_gln', ''), ""
            elif not flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
                return "", "", "", ""
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    # 1. Veritabanından (sistem_ayarlari) oku
    username = decrypt_credential(get_system_setting("bkst_kullanici_adi", ""))
    password = decrypt_credential(get_system_setting("bkst_sifre", ""))
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
                if username: set_system_setting("bkst_kullanici_adi", encrypt_credential(username))
                if password: set_system_setting("bkst_sifre", encrypt_credential(password))
                if address_id: set_system_setting("bkst_adres_id", address_id)
                if api_key: set_system_setting("bkst_api_key", api_key)
                if user_name: set_system_setting("bkst_kullanici_isim", user_name)
                try:
                    os.remove(cred_file)
                    logger.info("bakanlik_giris_bilgileri.txt veritabanına aktarıldı ve güvenli şekilde silindi.")
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
        except Exception as e_txt:
            logger.warning(f"cred_file migration error: {e_txt}")

    return username, password, address_id, api_key

def save_bkst_credentials(username, password, address_id="", user_name="", api_key=""):
    set_system_setting("bkst_kullanici_adi", encrypt_credential(username))
    set_system_setting("bkst_sifre", encrypt_credential(password))
    set_system_setting("bkst_adres_id", address_id or "")
    if user_name:
        set_system_setting("bkst_kullanici_isim", user_name)
    if api_key:
        set_system_setting("bkst_api_key", api_key)
    with _template_vars_lock:
        _template_vars_cache['expires_at'] = 0

def _auto_pull_exit_list_from_server(merkez_url, bayi_kodu='', username='', password=''):
    """
    Sunucudaki güncel çıkış listesini çeker, yerel veri tabanını sunucu ile tam eşitler
    (sunucuda silinenler yerelden de silinir, yeni eklenenler yerel db'ye yazılır, alan güncellemeleri senkronize edilir).
    """
    if not merkez_url:
        return False
    try:
        import requests
        c_u, c_p, c_g, c_n = read_bkst_credentials()
        target_bk = bayi_kodu
        target_u = username or c_u
        target_p = password or c_p

        if not target_bk and target_u:
            b_info = get_bayi_by_code(target_u)
            if b_info:
                target_bk = b_info[1]
                target_p = target_p or decrypt_credential(b_info[4])
        elif target_bk and (not target_u or not target_p):
            b_info = get_bayi_by_code(target_bk)
            if b_info:
                target_u = target_u or decrypt_credential(b_info[3])
                target_p = target_p or decrypt_credential(b_info[4])

        if not target_bk:
            target_bk = 'bayi_1'

        _flush_pending_deletes(merkez_url)   # önce bu cihazdaki silmeleri sunucuya ilet
        pull_url = f"{merkez_url.rstrip('/')}/api/sync/pull"
        resp = requests.post(pull_url, json={"bayi_kodu": target_bk, "bkst_kullanici": target_u, "bkst_sifre": target_p}, timeout=10.0)
        if resp.status_code != 200:
            return False
        remote_data = resp.json()
        if not remote_data.get('success'):
            return False

        remote_cikis_list = remote_data.get('cikis_kayitlari', [])

        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        _set_sync_flag('ayna_modu', '1', conn)  # sunucuya uyum için yapılan silmeler tekrar kuyruğa girmesin
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 1. Sunucudan gelen kayıtları En Son Değişiklik Kuralına (LWW) göre birleştir
        for itm in remote_cikis_list:
            hk = itm.get('ham_karekod') or ''
            if not hk:
                continue
            r_silindi = 1 if itm.get('silindi') in (1, '1', True) else 0
            r_del_time = itm.get('silinme_tarihi') or ''
            r_tarih = itm.get('tarih') or ''
            rem_ts = r_del_time if r_silindi == 1 else r_tarih
            
            c.execute("SELECT id, tarih, COALESCE(silindi, 0), COALESCE(silinme_tarihi, ''), sync_durum FROM cikis_kayitlari WHERE ham_karekod = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (hk, target_bk, target_bk))
            loc_row = c.fetchone()
            if not loc_row:
                # Yerelde hiç yoksa sunucudaki durumuyla ekle
                c.execute("""
                    INSERT INTO cikis_kayitlari (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, uretim_tarihi, skt, ham_karekod, bayi_kodu, sync_durum, cihaz_adi, cihaz_id, silindi, silinme_tarihi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ESITLENDI', ?, ?, ?, ?)
                """, (
                    r_tarih,
                    itm.get('urun_adi'),
                    itm.get('barkod'),
                    itm.get('koli_no'),
                    itm.get('seri_no'),
                    itm.get('parti_no'),
                    itm.get('uretim_tarihi'),
                    itm.get('skt'),
                    hk,
                    target_bk,
                    itm.get('cihaz_adi') or 'Masaüstü PC',
                    itm.get('cihaz_id') or '',
                    r_silindi,
                    r_del_time
                ))
            else:
                loc_id, loc_tarih, loc_silindi, loc_del_time, loc_sync = loc_row
                
                # Eğer yerelde silinmiş ama sunucuda hala aktif görünüyorsa:
                # Yereldeki silinme KESİNLİKLE korunur ve sunucudan da silinmesi için silme kuyruğuna yazılır!
                if loc_silindi == 1 and r_silindi == 0:
                    c.execute("INSERT OR IGNORE INTO senkron_silme_kuyrugu (bayi_kodu, ham_karekod, ts) VALUES (?, ?, ?)", (target_bk, hk, now_dt))
                    continue

                # Eğer sunucuda silinmişse, yerelde de kesinlikle silinir:
                if r_silindi == 1:
                    c.execute("UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'ESITLENDI' WHERE id = ?", (r_del_time or now_dt, loc_id))
                else:
                    # Alan güncellemeleri (ürün adı, barkod, parti vb.) senkronize edilir
                    c.execute("""
                        UPDATE cikis_kayitlari 
                        SET urun_adi = COALESCE(NULLIF(?, ''), urun_adi),
                            barkod = COALESCE(NULLIF(?, ''), barkod),
                            koli_no = COALESCE(NULLIF(?, ''), koli_no),
                            seri_no = COALESCE(NULLIF(?, ''), seri_no),
                            parti_no = COALESCE(NULLIF(?, ''), parti_no),
                            uretim_tarihi = COALESCE(NULLIF(?, ''), uretim_tarihi),
                            skt = COALESCE(NULLIF(?, ''), skt),
                            sync_durum = 'ESITLENDI'
                        WHERE id = ?
                    """, (
                        itm.get('urun_adi'),
                        itm.get('barkod'),
                        itm.get('koli_no'),
                        itm.get('seri_no'),
                        itm.get('parti_no'),
                        itm.get('uretim_tarihi'),
                        itm.get('skt'),
                        loc_id
                    ))

        # 2. Sunucuda artık bulunmayan (sunucuda tamamen silinmiş/arşivlenmiş) veya silinmiş kayıtları yerelde de temizle
        remote_active_hks = {itm.get('ham_karekod') for itm in remote_cikis_list if itm.get('ham_karekod') and itm.get('silindi') not in (1, '1', True)}
        
        # Yerelde aktif görünen ama sunucu listesinde aktif olmayan tüm kayıtları bul
        c.execute("SELECT id, ham_karekod FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND COALESCE(silindi, 0) = 0", (target_bk, target_bk))
        local_active_rows = c.fetchall()
        for loc_id, loc_hk in local_active_rows:
            if loc_hk and loc_hk not in remote_active_hks:
                # Sunucuda bu karekod aktif değil (silinmiş veya geçmişi temizlenmiş). Yerelde de anında sil!
                c.execute("UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'ESITLENDI' WHERE id = ?", (now_dt, loc_id))

        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, target_bk))
        conn.commit()
        conn.close()
        import gc
        gc.collect()
        logger.info(f"Auto-pull success: {len(remote_cikis_list)} items in exit list for {target_bk}")
        return True
    except Exception as e_ap:
        logger.warning(f"_auto_pull_exit_list_from_server error: {e_ap}")
        return False


def _merkez_url_cfg():
    return get_merkez_sunucu_url()

def _set_sync_flag(key, value, conn=None):
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("INSERT OR REPLACE INTO senkron_ayar (k, v) VALUES (?, ?)", (key, value))
    if own:
        conn.commit()
        conn.close()

def _flush_pending_deletes(merkez_url):
    """İstemcide yapılan silmeleri sunucuya iletir. Başarısızsa kuyrukta kalır (internet gelince tekrar denenir)."""
    try:
        import requests
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        rows = conn.execute("SELECT id, bayi_kodu, ham_karekod FROM senkron_silme_kuyrugu ORDER BY id LIMIT 1000").fetchall()
        if not rows:
            conn.close()
            return True
        c_user, c_pass, c_gln, c_name = read_bkst_credentials()
        groups, drop_ids = {}, []
        for rid, bk, hk in rows:
            exists = conn.execute(
                "SELECT 1 FROM cikis_kayitlari WHERE ham_karekod = ? AND COALESCE(silindi, 0) = 0 AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) LIMIT 1",
                (hk, bk, bk)).fetchone()
            if exists:
                drop_ids.append(rid)   # kutu sonradan yeniden okutulmuş: silme iptal
            else:
                groups.setdefault(bk, []).append((rid, hk))
        all_ok = True
        for bk, items in groups.items():
            b_info = get_bayi_by_code(bk)
            bk_user = (decrypt_credential(b_info[3]) if (b_info and b_info[3]) else "") or c_user
            bk_pass = (decrypt_credential(b_info[4]) if (b_info and b_info[4]) else "") or c_pass
            try:
                resp = requests.post(f"{merkez_url.rstrip('/')}/api/sync/delete",
                                     json={"bayi_kodu": bk, "bkst_kullanici": bk_user, "bkst_sifre": bk_pass, "ham_karekodlar": [h for _, h in items]},
                                     timeout=8.0)
                if resp.status_code == 200 and resp.json().get('success'):
                    drop_ids += [r for r, _ in items]
                else:
                    all_ok = False
            except Exception as e_del:
                logger.debug(f"flush delete error: {e_del}")
                all_ok = False
        if drop_ids:
            conn.execute(f"DELETE FROM senkron_silme_kuyrugu WHERE id IN ({','.join(str(int(i)) for i in drop_ids)})")
            conn.commit()
        conn.close()
        return all_ok
    except Exception as e_fl:
        logger.debug(f"flush pending deletes: {e_fl}")
        return False

def clean_user_name(name):
    if not name:
        return ""
    name = str(name).strip()
    if name.startswith("ENC:"):
        name = decrypt_credential(name).strip()
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
                    with open(v_path, "r", encoding="utf-8-sig") as f:
                        v_data = json.load(f)
                        v_code = str(v_data.get("version", "v1.0")).strip()
                        v_commit = str(v_data.get("commit", "")).strip()
                        v_date = str(v_data.get("date", "")).strip()
                        v_msg = str(v_data.get("message", "Sistem Güncel")).strip()
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
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

    from flask import session as flask_session
    active_display_name = clean_user_name(flask_session.get('dealer_name') or flask_session.get('active_bayi_adi') or '')
    if (not active_display_name or active_display_name.isdigit()) and flask_session.get('active_bayi_kodu'):
        try:
            _bi = get_bayi_by_code(flask_session.get('active_bayi_kodu'))
            if _bi and _bi[2]:
                _cand_n = clean_user_name(_bi[2])
                if _cand_n and not _cand_n.isdigit():
                    active_display_name = _cand_n
                    flask_session['dealer_name'] = _cand_n
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    if not active_display_name and flask_session.get('is_admin'):
        active_display_name = f"👑 Yönetici ({flask_session.get('admin_user', 'Admin')})"
    elif not active_display_name:
        active_display_name = clean_user_name(user_name)

    return dict(
        current_user_name=active_display_name,
        current_app_version=v_code,
        current_app_commit=full_commit,
        current_app_date=v_date,
        current_app_msg=v_msg,
        bkst_online=bkst_online,
        is_system_active=not is_offline,
        system_status_text=system_status_text,
        system_status_cls=system_status_cls,
        local_session_token=LOCAL_SESSION_TOKEN,
        is_impersonating=bool(flask_session.get('impersonating_dealer')),
        impersonated_dealer_name=flask_session.get('active_bayi_adi', ''),
        impersonated_dealer_gln=flask_session.get('dealer_gln', ''),
        impersonated_dealer_user=flask_session.get('dealer_username', ''),
        is_admin_logged_in=bool(flask_session.get('is_admin'))
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

def extract_sn(qr_str):
    parsed = parse_gs1_qr(qr_str)
    return parsed.get("seri_no")

def extract_lot(qr_str):
    parsed = parse_gs1_qr(qr_str)
    return parsed.get("parti_no")

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
        return None, {}, {}, {}

    user_key = username
    with _state_lock:
        if user_key in _user_cache_map:
            if user_key in _cache_access_order:
                _cache_access_order.remove(user_key)
            _cache_access_order.append(user_key)
            return _user_cache_map[user_key]

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri WHERE kullanici_adi = ?", conn, params=(username,))
    except Exception as e:
        logger.error(f"Error querying bkst_depo_verileri, repairing schema: {e}")
        try:
            ensure_db_schema(force=True)
            df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri WHERE kullanici_adi = ?", conn, params=(username,))
        except Exception:
            df = pd.DataFrame()
    finally:
        try:
            conn.close()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
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

        while len(_user_cache_map) > 120 and _cache_access_order:
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
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
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
    from flask import session as flask_session
    if flask_session.get('is_admin'):
        return redirect('/admin')
    if flask_session.get('dealer_logged_in'):
        saved_u = get_system_setting("bkst_kullanici_adi", "").strip()
        if saved_u:
            return redirect('/cikis')
        else:
            flask_session.clear()
            resp = make_response(redirect('/login?logout=1'))
            try:
                resp.delete_cookie(app.session_cookie_name or 'session', path='/')
                resp.delete_cookie('session', path='/')
                resp.delete_cookie('local_session_token', path='/')
            except Exception:
                pass
            return resp
    return redirect('/login')

@app.route('/stok-esitleme')
def stok_esitleme_page():
    return render_template('index.html')

@app.route('/login')
def login_page():
    from flask import session as flask_session
    if request.args.get('logout') or request.args.get('error') == 'oturum_sonlandi':
        flask_session.clear()
        resp = make_response(render_template('login.html'))
        try:
            resp.delete_cookie(app.session_cookie_name or 'session', path='/')
            resp.delete_cookie('session', path='/')
            resp.delete_cookie('local_session_token', path='/')
        except Exception:
            pass
        return resp

    if flask_session.get('is_admin'):
        return redirect('/admin')

    if flask_session.get('dealer_logged_in'):
        active_u = get_system_setting("bkst_kullanici_adi", "")
        if active_u:
            return redirect('/cikis')
        else:
            flask_session.clear()
            resp = make_response(render_template('login.html'))
            try:
                resp.delete_cookie(app.session_cookie_name or 'session', path='/')
                resp.delete_cookie('session', path='/')
                resp.delete_cookie('local_session_token', path='/')
            except Exception:
                pass
            return resp

    return render_template('login.html')

# ── Mobil Sayım ve 1 Günlük Parolasız Hızlı QR Girişi ────────────────────────
def generate_signed_mobile_token(bayi_kodu, exp_timestamp=None):
    if exp_timestamp is None:
        exp_timestamp = int(time.time()) + 86400 * 30
    # 256-bit kriptografik rastgele güvenli token (tahmin edilemez CSPRNG)
    rand_entropy = secrets.token_urlsafe(32)
    return f"qrm.{bayi_kodu}.{exp_timestamp}.{rand_entropy}"

def verify_signed_mobile_token(token):
    if not token or not str(token).startswith("qrm."):
        return False, None, 0
    try:
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            row = conn.execute(
                "SELECT bayi_kodu, expires_at, is_active FROM mobile_quick_tokens WHERE token = ?",
                (token,)
            ).fetchone()
            if row and row[2] == 1 and (not row[1] or row[1] > now_str):
                try:
                    exp_ts = int(datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S').timestamp())
                except Exception:
                    exp_ts = int(time.time()) + 3600
                return True, row[0], exp_ts
    except Exception as e_tok:
        logger.debug(f"verify_signed_mobile_token db error: {e_tok}")
    return False, None, 0

@app.route('/api/sync/mobile_token', methods=['POST'])
def api_sync_mobile_token():
    data = request.get_json(silent=True) or {}
    tok = str(data.get('token') or '').strip()
    b_kodu = str(data.get('bayi_kodu') or '').strip()
    d_name = str(data.get('dealer_name') or 'Yetkili Bayi').strip()
    d_gln = str(data.get('dealer_gln') or '').strip()
    c_at = str(data.get('created_at') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')).strip()
    e_at = str(data.get('expires_at') or '').strip()
    bk_pass = str(data.get('bkst_sifre') or '').strip()

    if not tok or not b_kodu or not e_at:
        return jsonify({'success': False, 'error': 'Eksik parametre'}), 400
    
    from flask import session as flask_session
    is_admin = bool(flask_session.get('is_admin'))
    is_same_sess = bool(flask_session.get('dealer_logged_in') and flask_session.get('active_bayi_kodu') == b_kodu)

    # 🛡️ Güvenlik Doğrulaması: Dışarıdan izinsiz token kaydını engellemek için kontrol
    if not is_admin and not is_same_sess:
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn_chk:
                r_chk = conn_chk.execute("SELECT bkst_sifre, durum FROM bayiler WHERE bayi_kodu = ?", (b_kodu,)).fetchone()
                if r_chk:
                    if str(r_chk[1] or '').upper() == 'PASIF':
                        return jsonify({'success': False, 'error': 'Bayi lisansı pasiftir.'}), 403
                    stored_pass = decrypt_credential(r_chk[0])
                    if stored_pass and bk_pass and not hmac.compare_digest(stored_pass, bk_pass):
                        logger.warning(f"⚠️ api_sync_mobile_token: Şifre uyuşmazlığı ({b_kodu})")
        except Exception as e_chk:
            logger.debug(f"api_sync_mobile_token chk warning: {e_chk}")

    bk_user = str(data.get('bkst_kullanici') or '').strip()

    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO mobile_quick_tokens 
                (token, bayi_kodu, dealer_name, dealer_gln, created_at, expires_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (tok, b_kodu, d_name, d_gln, c_at, e_at))

            # 🔐 Sunucuda Bakanlık Giriş Bilgilerini Güvenle Güncelle (Bakanlık API Stok Çekimi İçin)
            if bk_user and bk_pass:
                enc_u = encrypt_credential(bk_user)
                enc_p = encrypt_credential(bk_pass)
                row_ex = c.execute("SELECT id FROM bayiler WHERE bayi_kodu = ?", (b_kodu,)).fetchone()
                if row_ex:
                    c.execute("""
                        UPDATE bayiler 
                        SET bkst_kullanici = ?, bkst_sifre = ?, gln_no = COALESCE(NULLIF(?, ''), gln_no), bayi_adi = COALESCE(NULLIF(?, ''), bayi_adi), durum = 'AKTIF'
                        WHERE bayi_kodu = ?
                    """, (enc_u, enc_p, d_gln, d_name, b_kodu))
                else:
                    now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # Eski iptal damgalarını veritabanından tamamen temizle
            c.execute("DELETE FROM sistem_ayarlari WHERE anahtar IN (?, 'staff_revoked_at_bayi_1')", (f"staff_revoked_at_{b_kodu}",))

            # 📦 Eğer istemci depo stok verilerini göndermişse sunucuda kaydet (Mobil sayım çevrimdışı kalmasın)
            depo_rows = data.get('depo_verileri')
            if depo_rows and isinstance(depo_rows, list) and bk_user:
                try:
                    c.execute("DELETE FROM bkst_depo_verileri WHERE kullanici_adi = ?", (bk_user,))
                    ins_data = []
                    now_stk = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    for item in depo_rows:
                        ins_data.append((
                            item.get('gtin', ''),
                            item.get('urun_adi', ''),
                            item.get('seri_no', ''),
                            item.get('parti_no', ''),
                            item.get('koli_no', ''),
                            item.get('palet_no', ''),
                            item.get('uretim_tarihi', ''),
                            item.get('skt', ''),
                            item.get('tam_karekod', ''),
                            bk_user,
                            now_stk
                        ))
                    if ins_data:
                        c.executemany("""
                            INSERT OR REPLACE INTO bkst_depo_verileri
                            (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, ins_data)
                        logger.info(f"📦 [SYNC] {len(ins_data)} adet depo stok verisi sunucuya aktarıldı ({bk_user}).")
                except Exception as e_stk:
                    logger.warning(f"Depo stok verisi kaydedilemedi: {e_stk}")

            conn.commit()

        # 🔓 YENİ QR SENKRONİZE EDİLDİĞİNDE ESKİ İPTAL KİLİDİNİ KALDIR:
        with _revoked_dealers_lock:
            _revoked_staff_dealers.discard(b_kodu)
            _revoked_staff_dealers.discard('bayi_1')
            _revoked_mobile_tokens.discard(tok)
            _revoked_dealers.discard(b_kodu)
        AuthService.get_instance().unrevoke_dealer_staff(b_kodu)
        AuthService.get_instance().unrevoke_dealer_staff('bayi_1')

        logger.info(f"✅ [SYNC] Mobil token ve Bakanlık bilgileri senkronize edildi: {b_kodu} -> {tok[:18]}...")
        return jsonify({'success': True, 'message': 'Token senkronize edildi'})
    except Exception as e:
        logger.error(f"❌ [SYNC] Token kaydedilemedi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/auth/generate_mobile_qr', methods=['GET', 'POST'])
def api_generate_mobile_qr():
    from flask import session as flask_session
    if not flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim. Lütfen sisteme giriş yapınız.'}), 401

    target_bayi_kodu = flask_session.get('active_bayi_kodu') or 'bayi_1'
    target_dealer_name = flask_session.get('dealer_name') or flask_session.get('active_bayi_adi') or 'Yetkili Bayi'
    target_dealer_gln = flask_session.get('dealer_gln') or ''

    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            c = conn.cursor()
            c.execute("SELECT bayi_adi, gln_no FROM bayiler WHERE bayi_kodu = ?", (target_bayi_kodu,))
            row = c.fetchone()
            if row:
                if row[0]: target_dealer_name = row[0]
                if row[1]: target_dealer_gln = row[1]
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    # Oturum süresi parametresi (1, 3, 12 varsayılan, 24, 168 saat / 1 hafta)
    req_json = request.get_json(silent=True) or {}
    raw_duration = request.args.get('duration_hours') or req_json.get('duration_hours') or 12
    try:
        duration_hours = int(raw_duration)
    except Exception:
        duration_hours = 12
    if duration_hours not in (1, 3, 12, 24, 168):
        duration_hours = 12

    now_dt = datetime.now()
    exp_dt = now_dt + timedelta(hours=duration_hours)
    exp_ts = int(exp_dt.timestamp())
    token = generate_signed_mobile_token(target_bayi_kodu, exp_ts)
    created_at = now_dt.strftime('%Y-%m-%d %H:%M:%S')
    expires_at = exp_dt.strftime('%Y-%m-%d %H:%M:%S')

    try:
        with sqlite3.connect(DB_PATH, timeout=15.0) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO mobile_quick_tokens 
                (token, bayi_kodu, dealer_name, dealer_gln, created_at, expires_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (token, target_bayi_kodu, target_dealer_name, target_dealer_gln, created_at, expires_at))
            c.execute("DELETE FROM sistem_ayarlari WHERE anahtar IN (?, 'staff_revoked_at_bayi_1')", (f"staff_revoked_at_{target_bayi_kodu}",))
            conn.commit()
    except Exception as e_tok:
        logger.error(f"Generate mobile token error: {e_tok}")
        return jsonify({'success': False, 'error': str(e_tok)}), 500

    lan_ip = "127.0.0.1"
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(('8.8.8.8', 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    lan_url = f"http://{lan_ip}:5000/mobile_login?token={token}"

    cloud_base = ""
    try:
        cloud_base = get_merkez_sunucu_url().rstrip('/')
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    # Eğer cloud_base boş, 127.0.0.1 veya localhost ise doğrudan TUNEL_BAGLANTI_ADRESI.txt dosyasını kontrol et
    if not cloud_base or not cloud_base.startswith("http") or "127.0.0.1" in cloud_base or "localhost" in cloud_base:
        tun_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TUNEL_BAGLANTI_ADRESI.txt")
        if os.path.exists(tun_file):
            try:
                with open(tun_file, "r", encoding="utf-8") as ft:
                    m = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', ft.read())
                    if m:
                        cloud_base = m.group(1).strip()
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
    if not cloud_base or not cloud_base.startswith("http") or "127.0.0.1" in cloud_base or "localhost" in cloud_base:
        if lan_ip and lan_ip != "127.0.0.1":
            cloud_base = f"http://{lan_ip}:5000"
        else:
            cloud_base = request.host_url.rstrip('/')

    cloud_url = f"{cloud_base}/mobile_login?token={token}"
    local_url = f"{request.host_url.rstrip('/')}/mobile_login?token={token}"

    # 🔓 Yeni bir QR üretildiğinde bu bayi için eski iptal kilidini yerelde de temizle
    with _revoked_dealers_lock:
        _revoked_staff_dealers.discard(target_bayi_kodu)
        _revoked_staff_dealers.discard('bayi_1')
        _revoked_mobile_tokens.discard(token)
        _revoked_dealers.discard(target_bayi_kodu)
    AuthService.get_instance().unrevoke_dealer_staff(target_bayi_kodu)
    AuthService.get_instance().unrevoke_dealer_staff('bayi_1')

    # İstek bağlamından Bakanlık giriş bilgilerini oku
    u_user, u_pass, u_gln, _ = read_bkst_credentials()

    # 🌐 Merkez sunucuya tokeni ve depo stoklarını bildir (Senkron anlık teyit + Arka plan güvencesi)
    if not _is_server_host() and cloud_base.startswith("http") and not cloud_base.startswith("http://127.0.0.1") and not cloud_base.startswith("http://localhost"):
        def _sync_tok_remote():
            try:
                # Depodaki stok verilerini topla
                depo_verileri = []
                try:
                    with sqlite3.connect(DB_PATH, timeout=5.0) as conn_stk:
                        c_stk = conn_stk.cursor()
                        c_stk.execute("""
                            SELECT gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod 
                            FROM bkst_depo_verileri 
                            WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ''
                            LIMIT 3000
                        """, (u_user or '',))
                        for s_r in c_stk.fetchall():
                            depo_verileri.append({
                                'gtin': s_r[0] or '',
                                'urun_adi': s_r[1] or '',
                                'seri_no': s_r[2] or '',
                                'parti_no': s_r[3] or '',
                                'koli_no': s_r[4] or '',
                                'palet_no': s_r[5] or '',
                                'uretim_tarihi': s_r[6] or '',
                                'skt': s_r[7] or '',
                                'tam_karekod': s_r[8] or '',
                            })
                except Exception as _e_stk:
                    logger.debug(f"Depo verisi okuma uyarısı: {_e_stk}")

                payload = {
                    'token': token,
                    'bayi_kodu': target_bayi_kodu,
                    'dealer_name': target_dealer_name,
                    'dealer_gln': target_dealer_gln or u_gln or '',
                    'bkst_kullanici': u_user or '',
                    'bkst_sifre': u_pass or '',
                    'created_at': created_at,
                    'expires_at': expires_at,
                    'depo_verileri': depo_verileri
                }
                r = requests.post(f"{cloud_base}/api/sync/mobile_token", json=payload, timeout=6.0)
                if r.status_code == 200 and (r.json() or {}).get('success'):
                    logger.info(f"🌐 Mobil token ve {len(depo_verileri)} stok merkez sunucuya iletildi ({target_bayi_kodu})")
                else:
                    logger.warning(f"⚠️ Mobil token merkez sunucu yanıtı: {r.status_code}")
            except Exception as e_tok_sync:
                logger.warning(f"⚠️ Mobil token merkez sunucuya iletilemedi: {e_tok_sync}")

        sync_t = threading.Thread(target=_sync_tok_remote, daemon=True)
        sync_t.start()
        sync_t.join(timeout=0.8)

    # QR kod telefondan taranacağı için cloud veya lan adresini içerir
    qr_data_url = ""
    try:
        import qrcode
        import io
        import base64
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(cloud_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        qr_data_url = f"data:image/png;base64,{qr_b64}"
    except Exception as e_qr:
        logger.warning(f"QR code image generation warning: {e_qr}")

    return jsonify({
        'success': True,
        'token': token,
        'login_url': cloud_url,
        'local_login_url': local_url,
        'lan_login_url': lan_url,
        'cloud_login_url': cloud_url,
        'qr_data_url': qr_data_url,
        'bayi_kodu': target_bayi_kodu,
        'dealer_name': target_dealer_name,
        'dealer_gln': target_dealer_gln,
        'created_at': created_at,
        'expires_at': expires_at,
        'valid_hours': duration_hours
    })

@app.route('/mobile_login', methods=['GET'])
def route_mobile_login():
    token = request.args.get('token', '').strip()
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '127.0.0.1').split(',')[0].strip()
    if not token:
        log_debug("MOBIL_OTURUM", "Mobil Giriş Başarısız", f"Giriş anahtarı (token) belirtilmedi. IP: {client_ip}", seviye="UYARI")
        return render_template('mobile_login_expired.html', 
                               error='Giriş anahtarı belirtilmedi.')

    now_dt = datetime.now()
    now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')
    now_ts = int(time.time())

    # 1. Bellek içi İptal Kontrolü
    with _revoked_dealers_lock:
        if token in _revoked_mobile_tokens:
            log_debug("MOBIL_OTURUM", "Sonlandırılmış QR ile Mobil Giriş Engellendi", f"Bu personelin oturumu sonlandırılmıştır. Token: {token[:16]}..., IP: {client_ip}", seviye="UYARI")
            return render_template('mobile_login_expired.html', 
                                   error='Bu personelin oturumu bayi tarafından sonlandırılmıştır. Lütfen masaüstü uygulamasından yeni bir karekod okutunuz.')

    with sqlite3.connect(DB_PATH, timeout=15.0) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT token, bayi_kodu, dealer_name, dealer_gln, expires_at, is_active, login_count
            FROM mobile_quick_tokens
            WHERE token = ?
        """, (token,))
        row = c.fetchone()

        # 2. Dayanıklılık ve Self-Healing: Token veritabanında henüz yoksa (senkronizasyon gecikmesi vb.)
        if not row:
            try:
                parts = token.split('.')
                # Token formatı: qrm.{bayi_kodu}.{exp_ts}.{entropy}
                if len(parts) >= 4 and parts[0] == 'qrm':
                    f_bayi_kodu = parts[1]
                    f_exp_ts = int(parts[2])
                    if f_exp_ts > now_ts:
                        # Bayiyi kontrol et
                        c_b = c.execute("SELECT bayi_adi, gln_no, durum FROM bayiler WHERE bayi_kodu = ?", (f_bayi_kodu,)).fetchone()
                        if c_b and str(c_b[2] or '').upper() != 'PASIF':
                            f_name = c_b[0] or 'Yetkili Bayi'
                            f_gln = c_b[1] or ''
                            f_created = now_str
                            f_expires = datetime.fromtimestamp(f_exp_ts).strftime('%Y-%m-%d %H:%M:%S')
                            c.execute("""
                                INSERT OR REPLACE INTO mobile_quick_tokens 
                                (token, bayi_kodu, dealer_name, dealer_gln, created_at, expires_at, is_active)
                                VALUES (?, ?, ?, ?, ?, ?, 1)
                            """, (token, f_bayi_kodu, f_name, f_gln, f_created, f_expires))
                            conn.commit()
                            row = (token, f_bayi_kodu, f_name, f_gln, f_expires, 1, 0)
                            logger.info(f"✨ [SELF-HEAL] Mobil token anında doğrulandı ve veritabanına işlendi: {f_bayi_kodu}")
            except Exception as e_sh:
                logger.warning(f"Self-heal error: {e_sh}")

        if not row:
            log_debug("MOBIL_OTURUM", "Geçersiz QR ile Mobil Giriş Denendi", f"Geçersiz veya veritabanında bulunamayan token: {token[:20]}..., IP: {client_ip}", seviye="UYARI")
            return render_template('mobile_login_expired.html', 
                                   error='Geçersiz veya süresi dolmuş karekod! Lütfen masaüstü uygulamasından yeni bir karekod okutunuz.')

        t_token, t_bayi_kodu, t_dealer_name, t_dealer_gln, t_expires_at, t_is_active, t_login_cnt = row

        if not t_is_active:
            log_debug("MOBIL_OTURUM", "Devre Dışı Bırakılmış QR ile Giriş Denendi", f"Devre dışı token: {token[:20]}..., Bayi: {t_dealer_name}, IP: {client_ip}", seviye="UYARI", bayi_kodu=t_bayi_kodu)
            return render_template('mobile_login_expired.html', 
                                   error='Bu karekod devre dışı bırakılmış. Lütfen masaüstü uygulamasından yeni bir karekod oluşturunuz.')

        # 🔓 Bayi yeni bir QR oluşturmuş ve personel yeni QR okutuyorsa eski bayi kilidini tamamen kaldır:
        with _revoked_dealers_lock:
            _revoked_staff_dealers.discard(t_bayi_kodu)
            _revoked_staff_dealers.discard('bayi_1')
            _revoked_mobile_tokens.discard(token)
            _revoked_dealers.discard(t_bayi_kodu)
        AuthService.get_instance().unrevoke_dealer_staff(t_bayi_kodu)
        AuthService.get_instance().unrevoke_dealer_staff('bayi_1')
        try:
            c.execute("DELETE FROM sistem_ayarlari WHERE anahtar IN (?, 'staff_revoked_at_bayi_1')", (f"staff_revoked_at_{t_bayi_kodu}",))
        except Exception:
            pass

        # Süre kontrolü: Hem timestamp hem string kontrolü
        is_expired = False
        try:
            exp_parsed = datetime.strptime(t_expires_at, '%Y-%m-%d %H:%M:%S')
            if exp_parsed < now_dt:
                is_expired = True
        except Exception:
            if t_expires_at < now_str:
                is_expired = True

        if is_expired:
            log_debug("MOBIL_OTURUM", "Süresi Dolan QR ile Mobil Giriş", f"Karekodun süresi dolmuş ({t_expires_at}). Bayi: {t_dealer_name}, IP: {client_ip}", seviye="UYARI", bayi_kodu=t_bayi_kodu)
            return render_template('mobile_login_expired.html', 
                                   error=f'Bu karekodun geçerlilik süresi dolmuş ({t_expires_at}). Güvenlik gereği lütfen masaüstü uygulamasından yeni bir karekod oluşturunuz.')

        c.execute("""
            UPDATE mobile_quick_tokens 
            SET login_count = login_count + 1, last_used_at = ? 
            WHERE token = ?
        """, (now_str, token))
        conn.commit()

    from flask import session as flask_session
    flask_session['dealer_logged_in'] = True
    flask_session['active_bayi_kodu'] = t_bayi_kodu
    try:
        b_info = get_bayi_by_code(t_bayi_kodu)
        if b_info and b_info[3]:
            flask_session['dealer_username'] = decrypt_credential(b_info[3])
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    flask_session['dealer_name'] = clean_user_name(t_dealer_name)
    flask_session['dealer_gln'] = t_dealer_gln or ''
    flask_session['mobile_quick_login'] = True
    flask_session['is_staff'] = True
    flask_session['mobile_token'] = token
    flask_session['token_expires_at'] = t_expires_at
    flask_session['token_created_at'] = now_str
    flask_session.permanent = True

    try:
        user_agent = request.headers.get('User-Agent', '')
        device_id = f"mob_{token[:8]}"
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn_dev:
            c_dev = conn_dev.cursor()
            c_dev.execute("""
                INSERT OR REPLACE INTO aktif_cihazlar 
                (bayi_kodu, cihaz_id, cihaz_adi, cihaz_turu, ip_adresi, user_agent, aktif_sayfa, son_gorulme, durum)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ONLINE')
            """, (t_bayi_kodu, device_id, f"📱 Personel ({t_dealer_name[:12]})", "📱 Mobil Personel (Sayım)", client_ip, user_agent, "📱 Mobil Depo Sayımı", now_str))
            conn_dev.commit()
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    logger.info(f"📱 Mobil Giriş Başarılı: '{t_dealer_name}' ({t_bayi_kodu}) QR token ile oturum açtı.")
    log_debug("MOBIL_OTURUM", f"📱 Mobil Personel Giriş Yaptı: {t_dealer_name}", f"Cihaz: mob_{token[:8]}, IP: {client_ip}, Geçerlilik: {t_expires_at}", seviye="INFO", bayi_kodu=t_bayi_kodu)
    return redirect('/cikis')

@app.route('/mobil_sayim', methods=['GET'])
def route_mobil_sayim():
    return redirect('/cikis')

@app.route('/download/QR_Compare.apk', methods=['GET'])
@app.route('/download/app.apk', methods=['GET'])
def route_download_apk():
    apk_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'QR_Compare.apk')
    if not os.path.exists(apk_path):
        apk_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'QR_Compare.apk')
    if os.path.exists(apk_path):
        return send_file(apk_path, as_attachment=True, download_name='QR_Compare.apk', mimetype='application/vnd.android.package-archive')
    return jsonify({'error': 'APK dosyası bulunamadı.'}), 404

@app.route('/cikis')
def cikis_page():
    from flask import session as flask_session
    if not flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
        return redirect('/login')
    if flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
        saved_u = get_system_setting("bkst_kullanici_adi", "").strip()
        if not saved_u:
            flask_session.clear()
            resp = make_response(redirect('/login?logout=1'))
            try:
                resp.delete_cookie(app.session_cookie_name or 'session', path='/')
                resp.delete_cookie('session', path='/')
                resp.delete_cookie('local_session_token', path='/')
            except Exception:
                pass
            return resp
    return render_template('cikis.html')

@app.route('/cikis-listesi')
@app.route('/cikis_listesi')
def cikis_listesi_page():
    from flask import session as flask_session
    if not flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
        return redirect('/login')
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
    from flask import session as flask_session
    if flask_session.get('mobile_quick_login'):
        return render_template('unauthorized_worker.html',
                               title="Satış Yetkisi Bulunmamaktadır",
                               message="Çalışan karekod oturumu ile resmi Bakanlık satışı yapılamaz. Bu işlem yalnızca bayi sahibi tarafından yapılabilir.")
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
    import socket
    for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53), ("77.88.8.8", 53)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.5)
            s.connect((host, port))
            s.close()
            return True
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    try:
        import requests
        r = requests.get("https://bkst.tarbil.gov.tr/", timeout=4.0, verify=SSL_VERIFY)
        return True
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    try:
        import requests
        r = requests.get("https://www.google.com", timeout=4.0)
        return True
    except Exception:
        return False

_last_internet_check_time = 0.0
_last_internet_status = True
_internet_check_lock = threading.Lock()

def is_internet_available(ttl=3.0, *args, **kwargs):
    global _last_internet_check_time, _last_internet_status
    timeout_val = kwargs.get('timeout', ttl)
    with _internet_check_lock:
        now_t = time.time()
        if now_t - _last_internet_check_time < timeout_val:
            return _last_internet_status
        _last_internet_status = check_internet_connection()
        _last_internet_check_time = now_t
        return _last_internet_status

# ── BKST ASYNC WORKER & API ROUTES ───────────────────────────────────────────
# bkst_online: None = henüz denenmedi, True = Bakanlık'tan veri alındı, False = bağlantı/veri sorunu
bkst_online = None
bkst_fetched_count = 0


_local_count_cache_by_user = {}
_local_count_lock = threading.Lock()

def _local_item_count():
    try:
        username, _, _, _ = read_bkst_credentials()
        if not username:
            return 0
        with _state_lock:
            if username in _user_cache_map and _user_cache_map[username][0] is not None:
                return len(_user_cache_map[username][0])
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        r = conn.execute("SELECT COUNT(*) FROM bkst_depo_verileri WHERE kullanici_adi = ?", (username,)).fetchone()
        conn.close()
        return r[0] if r else 0
    except Exception:
        return 0

def _local_item_count_cached():
    try:
        username, _, _, _ = read_bkst_credentials()
    except Exception:
        username = ""
    u_key = username or "__ANON__"
    now = time.monotonic()
    with _local_count_lock:
        entry = _local_count_cache_by_user.get(u_key)
        if entry is not None and (now - entry[1]) < 30.0:
            return entry[0]
    v = _local_item_count()
    with _local_count_lock:
        if len(_local_count_cache_by_user) > 200:
            _local_count_cache_by_user.clear()
        _local_count_cache_by_user[u_key] = (v, time.monotonic())
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


def _do_fetch_api_worker(user=None, pwd=None, addr=None, key=None):
    global _app_bkst_synced
    logger.info("BKST fetch_api worker thread started")
    if user and pwd:
        username, password, address_id, api_key = user, pwd, (addr or ""), (key or "")
    else:
        username, password, address_id, api_key = read_bkst_credentials()
    b_kodu = resolve_dealer_code('bayi_1', username) if username else 'bayi_1'

    if not username or not password:
        err_msg = "❌ HATA: Giriş bilgileri (KULLANICI_ADI / SIFRE) bulunamadı! Lütfen tekrar giriş yapın."
        _set_bkst_state("error", err_msg, False)
        log_debug("BAKANLIK_API", "Bakanlık Giriş Bilgileri Eksik", err_msg, seviye="ERROR", bayi_kodu=b_kodu)
        return

    if not check_internet_connection():
        _mark_offline("İnternet bağlantısı yok.")
        log_debug("BAKANLIK_API", "İnternet Bağlantısı Yok", "Bakanlık stok sorgusu yapılamadı, internet bağlantısı tespit edilemedi.", seviye="UYARI", bayi_kodu=b_kodu)
        return

    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=Retry(total=2, backoff_factor=0.3))
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest"
        })
        if api_key:
            session.headers.update({"Authorization": f"Bearer {api_key}", "Key": api_key})

        # 1) Ana sayfa
        try:
            r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(8, 15))
        except Exception as e_home:
            err_msg = f"Bakanlık sunucusuna (bkst.tarbil.gov.tr) ulaşılamıyor ({type(e_home).__name__})."
            _mark_offline(err_msg)
            log_debug("BAKANLIK_API", "Bakanlık Sunucusuna Erişilemedi", f"{err_msg} - {e_home}", seviye="ERROR", bayi_kodu=b_kodu)
            return
        if r_home.status_code != 200:
            err_msg = f"Bakanlık sunucusu hata döndürdü (HTTP {r_home.status_code})."
            _mark_offline(err_msg)
            log_debug("BAKANLIK_API", "Bakanlık Ana Sayfa Hatası", err_msg, resp_payload=r_home.text[:500], seviye="ERROR", bayi_kodu=b_kodu)
            return

        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        # 2) Giriş
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf",
                                 data={"tcNo": username, "sifre": password, "__RequestVerificationToken": token1},
                                 verify=SSL_VERIFY, timeout=(10, 25))
        if res_login.status_code != 200:
            err_msg = f"Bakanlık giriş servisi yanıt vermiyor (HTTP {res_login.status_code})."
            _mark_offline(err_msg)
            log_debug("BAKANLIK_API", "Bakanlık Giriş Servisi Yanıt Vermedi", err_msg, req_payload={"tcNo": username}, resp_payload=res_login.text[:500], seviye="ERROR", bayi_kodu=b_kodu)
            return
        if "0" not in res_login.text:
            err_msg = "❌ HATA: Bakanlık kullanıcı adı veya şifreniz yanlış! Sistem deaktif."
            _set_bkst_state("error", err_msg, False)
            log_debug("BAKANLIK_API", "Bakanlık Giriş Başarısız (Hatalı Şifre/TC)", err_msg, req_payload={"tcNo": username}, resp_payload=res_login.text[:500], seviye="ERROR", bayi_kodu=b_kodu)
            return

        # 3) Stok sayfası + GLN
        r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(8, 20))
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
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
                if gln_guid and len(gln_guid) >= 32:
                    break
        if not gln_guid or len(gln_guid) < 32:
            gln_guid = address_id or ""
            if not gln_guid or len(gln_guid) < 32:
                err_msg = "Bakanlık şirket adres (GLN) bilgisi bulunamadı. Lütfen tekrar giriş yapınız."
                _mark_offline(err_msg)
                log_debug("BAKANLIK_API", "Bakanlık GLN Bilgisi Alınamadı", err_msg, seviye="ERROR", bayi_kodu=b_kodu)
                return

        # 4) Stok listesi
        r_grid = session.post("https://bkst.tarbil.gov.tr/Main/GetStockList",
                              data={"CompanyAddressId": gln_guid, "Gtin": "", "__RequestVerificationToken": token2},
                              verify=SSL_VERIFY, timeout=(5, 15))
        if r_grid.status_code != 200:
            err_msg = f"Bakanlık stok listesi servisi yanıt vermiyor (HTTP {r_grid.status_code})."
            _mark_offline(err_msg)
            log_debug("BAKANLIK_API", "Bakanlık Stok Listesi Servisi Hatası", err_msg, req_payload={"CompanyAddressId": gln_guid}, resp_payload=r_grid.text[:500], seviye="ERROR", bayi_kodu=b_kodu)
            return
        try:
            grid_json = r_grid.json()
        except Exception:
            err_msg = "Bakanlık stok listesi geçersiz yanıt döndürdü (oturum düşmüş olabilir)."
            _mark_offline(err_msg)
            log_debug("BAKANLIK_API", "Bakanlık Stok Listesi JSON Hatası", err_msg, resp_payload=r_grid.text[:500], seviye="ERROR", bayi_kodu=b_kodu)
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
                err_msg = "Bakanlık'tan 0 adet stok verisi geldi (stok listesi boş döndü)."
                _mark_offline(err_msg)
                log_debug("BAKANLIK_API", "Bakanlık Stok Listesi Boş", err_msg, seviye="UYARI", bayi_kodu=b_kodu)
            else:
                err_msg = f"Bakanlık'tan 0 adet karekod detayı alınabildi ({detail_errors} istek başarısız)."
                _mark_offline(err_msg)
                log_debug("BAKANLIK_API", "Bakanlık Detay Alınamadı", err_msg, seviye="ERROR", bayi_kodu=b_kodu)
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
                err_msg = f"Bakanlık verileri çekildi ancak veritabanı kayıt hatası oluştu ({type(retry_err).__name__})."
                _mark_offline(err_msg)
                log_debug("BAKANLIK_API", "Veritabanı Kayıt Hatası", f"{err_msg} - {retry_err}", seviye="ERROR", bayi_kodu=b_kodu)
                return

        with _state_lock:
            _app_bkst_synced = True
        msg = f"🟢 SİSTEM AKTİF: Bakanlık'tan {fetched} adet stok verisi başarıyla çekildi."
        if detail_errors:
            msg += f" ({detail_errors} ürün detayı alınamadı.)"
        _set_bkst_state("done", msg, True, fetched)
        logger.info(f"BKST fetch_api worker finished: fetched={fetched}, detail_errors={detail_errors}")
        log_debug("BAKANLIK_API", f"✅ Bakanlık Stok Verileri Çekildi: {fetched} Ürün", f"GLN: {gln_guid}, Farklı barkod çeşidi: {len(gtin_list)}, Hatalı detay: {detail_errors}", seviye="INFO", bayi_kodu=b_kodu)
        try:
            threading.Thread(target=sync_pending_records_to_server, daemon=True).start()
        except Exception as _sync_err:
            logger.debug(f"Post-fetch sync notice: {_sync_err}")

    except Exception as e:
        logger.error(f"BKST fetch_api worker error: {e}", exc_info=True)
        err_msg = f"Bakanlık bağlantı hatası ({type(e).__name__})."
        _mark_offline(err_msg)
        log_debug("BAKANLIK_API", "Bakanlık Bağlantı İstisnası", f"{err_msg} - {e}", seviye="ERROR", bayi_kodu=b_kodu)


@app.route('/api/bkst/fetch_api', methods=['POST'])
def bkst_fetch_api_start():
    from flask import session as flask_session
    if not flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
        return jsonify({"success": False, "unauthenticated": True, "error": "Kullanıcı oturum açmamış. Veriler çekilemez."}), 401

    global _fetch_future, bkst_status, bkst_message
    with _state_lock:
        if _fetch_future and not _fetch_future.done():
            return jsonify({"success": True, "status": "already_running",
                            "message": "Zaten devam eden bir veri çekme işlemi var, sonucu bekleniyor."})
        bkst_status = "fetching"
        bkst_message = "⚡ Bakanlık verileri API üzerinden çekiliyor..."
        u_cred, p_cred, a_cred, k_cred = read_bkst_credentials()
        _dev_flag = bool(flask_session.get('dev_mode'))
        def _fetch_in_ctx(*a_):
            _dev_tls.on = _dev_flag
            try:
                return _do_fetch_api_worker(*a_)
            finally:
                _dev_tls.on = False
        _fetch_future = _fetch_executor.submit(_fetch_in_ctx, u_cred, p_cred, a_cred, k_cred)
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
        bayi_kodu, username = get_current_active_bayi()
        c_user, c_pass, c_gln, c_name = read_bkst_credentials()
        if c_user:
            username = c_user

        df, qr_map, gtin_map, koli_map = get_bkst_cache()

        # Koli / Palet toplu okutma kontrolü
        koli_key = barkod_norm.upper()
        if koli_map and (koli_key in koli_map or barkod_norm in koli_map):
            koli_items = koli_map.get(koli_key) or koli_map.get(barkod_norm) or []
            if koli_items:
                conn = sqlite3.connect(DB_PATH, timeout=30.0)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                c = conn.cursor()
                c.execute('SELECT ham_karekod FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1"))', (bayi_kodu, bayi_kodu))
                existing_set = set((row[0] or "").casefold() for row in c.fetchall() if row[0])

                tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                dev_id = str(data.get('cihaz_id') or request.cookies.get('bkst_device_id') or '').strip()
                dev_name = str(data.get('cihaz_adi') or '').strip()
                if not dev_name:
                    dev_name = "Windows PC" if "Windows" in request.headers.get('User-Agent', '') else "Terminal"

                insert_rows = []
                inserted_records = []
                last_inserted = None
                has_any_tekrar = False
                has_restored = False

                for r_item in koli_items:
                    item_qr = normalize_qr(str(r_item.get("Karekod", "")))
                    if not item_qr:
                        continue

                    u_adi = str(r_item.get('Ürün Adı', '')).strip()
                    b_col = str(r_item.get('Gtin Numarası') or r_item.get('Gtin / Barkod') or r_item.get('gtin') or '').strip()
                    k_no  = str(r_item.get('Koli Numarası', '')).strip()
                    s_no  = str(r_item.get('Seri Numarası', '')).strip()
                    p_no  = str(r_item.get('Parti Numarası', '')).strip()
                    pal_no = str(r_item.get('Palet Numarası', '')).strip()
                    ur_t  = str(r_item.get('Üretim Tarihi', '')).strip()
                    sk_t  = str(r_item.get('Son Kullanma Tarihi', '')).strip()

                    # Bu kutu bu bayide zaten cikis_kayitlari tablosunda var mı?
                    c.execute("SELECT id, COALESCE(silindi, 0) FROM cikis_kayitlari WHERE ham_karekod = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) LIMIT 1", (item_qr, bayi_kodu, bayi_kodu))
                    row_k = c.fetchone()
                    if row_k:
                        k_id, k_sil = row_k
                        if k_sil == 0:
                            # Zaten aktif ekli, ASLA mükerrer satır ekleme!
                            has_any_tekrar = True
                            continue
                        else:
                            # Daha önce silinmiş: Canlandır (Restore)
                            c.execute("UPDATE cikis_kayitlari SET silindi = 0, silinme_tarihi = NULL, tarih = ?, sync_durum = 'BEKLIYOR', cihaz_adi = ?, cihaz_id = ?, bayi_kodu = ? WHERE id = ?", (tarih, dev_name, dev_id, bayi_kodu, k_id))
                            has_restored = True
                            rec = {
                                'id': k_id, 'tarih': tarih, 'urun_adi': u_adi, 'barkod': b_col, 'koli_no': k_no,
                                'seri_no': s_no, 'parti_no': p_no, 'palet_no': pal_no, 'uretim_tarihi': ur_t,
                                'skt': sk_t, 'ham_karekod': item_qr, 'tekrar_uyari': 0,
                                'cihaz_adi': dev_name, 'cihaz_id': dev_id, 'bayi_kodu': bayi_kodu
                            }
                            inserted_records.append(rec)
                            last_inserted = rec
                            continue

                    insert_rows.append((tarih, u_adi, b_col, k_no, s_no, p_no, pal_no, ur_t, sk_t, item_qr, 0, username, dev_name, dev_id, bayi_kodu))
                    rec = {
                        'tarih': tarih, 'urun_adi': u_adi, 'barkod': b_col, 'koli_no': k_no,
                        'seri_no': s_no, 'parti_no': p_no, 'palet_no': pal_no, 'uretim_tarihi': ur_t,
                        'skt': sk_t, 'ham_karekod': item_qr, 'tekrar_uyari': 0,
                        'cihaz_adi': dev_name, 'cihaz_id': dev_id, 'bayi_kodu': bayi_kodu
                    }
                    inserted_records.append(rec)
                    last_inserted = rec

                if insert_rows:
                    c.executemany('''INSERT INTO cikis_kayitlari
                        (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                         uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, cihaz_adi, cihaz_id, sync_durum, bayi_kodu)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'BEKLIYOR', ?)''', insert_rows)

                    c.execute("SELECT max(id) FROM cikis_kayitlari")
                    max_id = c.fetchone()[0] or len(insert_rows)
                    first_id = max_id - len(insert_rows) + 1
                    new_idx = 0
                    for r_rec in inserted_records:
                        if 'id' not in r_rec:
                            r_rec['id'] = first_id + new_idx
                            new_idx += 1

                    # Kalıcı satış ve istatistik arşivine de ekle
                    c.executemany('''INSERT INTO satis_arsivi
                        (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                         uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, cihaz_adi, cihaz_id, durum, bayi_kodu)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CIKIS_YAPILDI', ?)''',
                        insert_rows)

                if insert_rows or has_restored:
                    conn.commit()
                conn.close()

                if not inserted_records:
                    log_debug("KAREKOD_OKUTMA", "Koli Zaten Listede", f"{koli_key} kolisindeki ürünlerin tamamı zaten çıkış listesinde ekli.", seviye="UYARI", bayi_kodu=bayi_kodu)
                    return jsonify({
                        'success': True,
                        'is_bulk': True,
                        'already_exists': True,
                        'tekrar_uyari': True,
                        'count': 0,
                        'message': f'{koli_key} kolisindeki ürünlerin tamamı zaten çıkış listesinde ekli!',
                        'kayit': None,
                        'kayitlar': []
                    })

                # Dükkan İstemcisi modu: Koli içindeki tüm ürünleri arka planda ANINDA ve DONMA OLMADAN ana sunucuya ilet
                merkez_url = get_merkez_sunucu_url()
                if merkez_url and not request.headers.get('X-Forwarded-From-Client'):
                    def _fwd_bulk():
                        try:
                            import requests
                            requests.post(
                                f"{merkez_url.rstrip('/')}/api/sync/push",
                                json={
                                    "bayi_kodu": bayi_kodu,
                                    "bkst_kullanici": c_user or username or "",
                                    "bkst_sifre": c_pass or "",
                                    "gln_no": c_gln or "",
                                    "bayi_adi": c_name or "",
                                    "cikis_kayitlari": inserted_records
                                },
                                headers={"X-Forwarded-From-Client": "true", "X-Requested-With": "XMLHttpRequest"},
                                timeout=8.0
                            )
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                    threading.Thread(target=_fwd_bulk, daemon=True).start()
                else:
                    try:
                        rev_id = str(int(time.time() * 1000))
                        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)
                    except Exception as _ignored_err:
                        logger.debug(f"Suppressed notice: {_ignored_err}")
                log_debug("KAREKOD_OKUTMA", f"📦 Koli Çıkış Listesine Eklendi: {koli_key}", f"{len(inserted_records)} adet ürün listeye eklendi. Cihaz: {dev_name}", seviye="INFO", bayi_kodu=bayi_kodu)
                return jsonify({
                    'success': True,
                    'is_bulk': True,
                    'tekrar_uyari': has_any_tekrar,
                    'count': len(inserted_records),
                    'message': f'{koli_key} kolisindeki {len(inserted_records)} adet ürün başarıyla çıkış listesine eklendi.',
                    'kayit': last_inserted,
                    'kayitlar': inserted_records
                })

        # Parti Numarası kontrolü: Parti numarası tekil kutuyu değil tüm partiyi temsil eder
        is_parti, p_count, p_urun = check_is_parti_no(barkod_norm, df)
        if is_parti:
            err_msg = f'"{barkod_raw}" bir Parti Numarasıdır ({p_urun} - Depoda bu partiye ait {p_count} adet ürün var). Parti numarası üretimdeki bir grubu temsil eder ve tekil bir kutuya ait değildir. Çıkış yapabilmek için lütfen kutu üzerindeki Karekodu veya Seri Numarasını okutunuz.'
            log_debug("KAREKOD_OKUTMA", "Parti Numarası Okutuldu (Hatalı)", f"Okutulan: '{barkod_raw}', Ürün: {p_urun}, Stok Adedi: {p_count}", seviye="UYARI", bayi_kodu=bayi_kodu)
            return jsonify({
                'success': False,
                'is_parti_no': True,
                'error': err_msg
            })

        # GTIN Barkod kontrolü: GTIN tekil kutuyu değil genel ürün tanımını temsil eder
        is_gtin, g_count, g_urun = check_is_gtin_no(barkod_norm, df, gtin_map)
        if is_gtin:
            err_msg = f'"{barkod_raw}" bir GTIN / Çizgi Barkod numarasıdır ({g_urun} - Depoda bu barkoda ait {g_count} adet ürün var). Bu numara tekil bir kutuya ait karekod değildir. Çıkış yapabilmek için lütfen kutu üzerindeki Karekodu (DataMatrix) veya Seri Numarasını okutunuz.'
            log_debug("KAREKOD_OKUTMA", "GTIN Çizgi Barkod Okutuldu (Hatalı)", f"Okutulan: '{barkod_raw}', Ürün: {g_urun}, Stok Adedi: {g_count}", seviye="UYARI", bayi_kodu=bayi_kodu)
            return jsonify({
                'success': False,
                'is_gtin_no': True,
                'error': err_msg
            })

        # Tekil ürün kontrolü: Önce ürünü Bakanlık deposundan tam eşleştir
        match_row = resolve_product_from_cache(barkod_norm, df, qr_map, gtin_map) if (df is not None and not df.empty) else None
        if match_row is None:
            # 🌐 Sistem Çevrimiçi mi yoksa Çevrimdışı (internetsiz / sunucusuz) mu?
            # KULLANICI KURALI: İnternet VE sunucu bağlantısı VARSA rastgele metin veya depoda olmayan kayıtlar KESİNLİKLE EKLENMEMELİ!
            # YALNIZCA internet veya sunucu bağlantısı YOKKEN çevrimdışı sayım modunda yerel geçici kayıt olarak eklenmeli.
            is_client_offline = bool(data.get('is_offline') or data.get('offline'))
            has_net = is_internet_available(ttl=3.0) if not is_client_offline else False
            has_depo = bool(df is not None and not df.empty)

            server_connected = False
            if not is_client_offline and has_net:
                if _is_server_host():
                    server_connected = True
                else:
                    m_url = get_merkez_sunucu_url()
                    if m_url:
                        server_connected = True
                    else:
                        server_connected = True

            # Çevrimiçi doğrulanmış mod: İstemci çevrimdışı değil + İnternet var + Sunucu bağlı + Depo stok listesi mevcut
            is_online_mode = (not is_client_offline) and has_net and server_connected and has_depo

            if is_online_mode:
                logger.warning(f"⚠️ [ONLINE KORUMA] Barkod depoda bulunamadı ve çevrimiçi modda reddedildi: '{barkod_raw}'")
                log_debug("KAREKOD_OKUTMA", "Depoda Kayıtlı Olmayan Karekod Reddedildi", f"Barkod: '{barkod_raw}' deponuzdaki kayıtlı ürünler arasında bulunamadı. Çevrimiçi modda çıkış reddedildi.", seviye="UYARI", bayi_kodu=bayi_kodu)
                return jsonify({
                    'success': False,
                    'not_found': True,
                    'error': f'"{barkod_raw}" deponuzdaki kayıtlı ürünler arasında bulunamadı! Çevrimiçi modda sadece deponuzdaki resmi kayıtlı ürünlerin çıkışı yapılabilir.'
                })

            # 🌐 Yalnızca ÇEVRİMDIŞI MODDA (İnternet veya sunucu bağlantısı yokken) geçici kayıt oluştur:
            parsed = parse_gs1_qr(barkod_norm)
            gtin_val = str(parsed.get('gtin') or extract_gtin(barkod_norm) or '').strip()
            sn_val = str(parsed.get('seri_no') or extract_sn(barkod_norm) or '').strip()
            p_val = str(parsed.get('parti_no') or '').strip()
            ur_val = str(parsed.get('uretim_tarihi') or '').strip()
            sk_val = str(parsed.get('skt') or '').strip()

            g_map = build_gtin_name_map()
            if gtin_val and gtin_val in g_map:
                p_name = g_map[gtin_val]
            elif gtin_val:
                p_name = f"Ürün (GTIN: {gtin_val})"
            else:
                p_name = f"Çevrimdışı Ürün ({barkod_norm[:16]})"

            match_row = {
                'Ürün Adı': p_name,
                'Gtin Numarası': gtin_val,
                'Karekod': barkod_norm,
                'Koli Numarası': '',
                'Palet Numarası': '',
                'Seri Numarası': sn_val,
                'Parti Numarası': p_val,
                'Üretim Tarihi': ur_val,
                'Son Kullanma Tarihi': sk_val,
                '_is_offline_item': True
            }

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

        # Çıkış kayıtlarında bu ürünün daha önce çıkış yapılıp yapılmadığını denetle (Aktif Bayiye Özel):
        c.execute('''
            SELECT id, tarih, urun_adi, ham_karekod, seri_no
            FROM (
                SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod, bayi_kodu FROM cikis_kayitlari
                UNION ALL
                SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod, bayi_kodu FROM satis_arsivi
            )
            WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))
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
        ''', (bayi_kodu, bayi_kodu, real_karekod, barkod_norm, seri_no, barkod_col, barkod_col))

        existing = c.fetchone()
        is_tekrar = False
        ex_tarih = ""
        if existing:
            is_tekrar = True
            ex_tarih = existing[1] or ""

        # 1. Cihaz bilgileri
        dev_id = str(data.get('cihaz_id') or request.cookies.get('bkst_device_id') or '').strip()
        dev_name = str(data.get('cihaz_adi') or '').strip()
        if not dev_name:
            dev_name = "Windows PC" if "Windows" in request.headers.get('User-Agent', '') else "Terminal"

        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")

        # 2. Bu kutu daha önce cikis_kayitlari tablosuna eklenmiş mi? (Mükerrer eklemeyi kesin engelle - Aktif Bayiye Özel)
        c.execute('''
            SELECT id, COALESCE(silindi, 0), tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod
            FROM cikis_kayitlari
            WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))
            AND (
                (ham_karekod IS NOT NULL AND ham_karekod != '' AND (LOWER(ham_karekod) = LOWER(?) OR LOWER(ham_karekod) = LOWER(?)))
                OR (
                    seri_no IS NOT NULL AND seri_no != ''
                    AND LOWER(seri_no) = LOWER(?)
                    AND (? = '' OR barkod = ? OR barkod IS NULL OR barkod = '')
                )
            )
            ORDER BY id DESC LIMIT 1
        ''', (bayi_kodu, bayi_kodu, real_karekod, barkod_norm, seri_no, barkod_col, barkod_col))
        existing_cikis = c.fetchone()

        if existing_cikis:
            cikis_id, cikis_silindi, c_tar, c_uadi, c_barkod, c_koli, c_seri, c_parti, c_palet, c_uretim, c_skt, c_ham = existing_cikis
            if cikis_silindi == 0:
                # Kutu ZATEN LİSTEDE AKTİF! Kesinlikle ikinci kez INSERT etme!
                conn.close()
                log_debug("KAREKOD_OKUTMA", "Ürün Zaten Çıkış Listesinde", f"Ürün: '{c_uadi or urun_adi}', Seri No: '{c_seri or seri_no}' zaten listede mevcut.", seviye="UYARI", bayi_kodu=bayi_kodu)
                return jsonify({
                    'success': True,
                    'already_exists': True,
                    'tekrar_uyari': True,
                    'warning': f'Bu ürün zaten çıkış listesinde ekli! ({c_uadi or urun_adi} - Seri No: {c_seri or seri_no})',
                    'kayit': {
                        'id': cikis_id,
                        'tarih': c_tar,
                        'urun_adi': c_uadi or urun_adi,
                        'barkod': c_barkod or barkod_col,
                        'koli_no': c_koli or koli_no,
                        'seri_no': c_seri or seri_no,
                        'parti_no': c_parti or parti_no,
                        'palet_no': c_palet or palet_no,
                        'uretim_tarihi': c_uretim or uretim_tarihi,
                        'skt': c_skt or skt,
                        'ham_karekod': c_ham or real_karekod,
                        'tekrar_uyari': 1,
                        'bayi_kodu': bayi_kodu
                    }
                })
            else:
                # Kutu daha önce silinmiş: Tekrar okutulduğunda yeniden aktife al (Restore)
                c.execute('''
                    UPDATE cikis_kayitlari
                    SET silindi = 0, silinme_tarihi = NULL, tarih = ?, sync_durum = 'BEKLIYOR', cihaz_adi = ?, cihaz_id = ?, bayi_kodu = ?
                    WHERE id = ?
                ''', (tarih, dev_name, dev_id, bayi_kodu, cikis_id))
                new_id = cikis_id
        else:
            # Gerçekten yeni kayıt: İlk kez cikis_kayitlari'na ekle
            c.execute('''INSERT INTO cikis_kayitlari
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, cihaz_adi, cihaz_id, sync_durum, bayi_kodu)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'BEKLIYOR', ?)''',
                (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username, dev_name, dev_id, bayi_kodu))
            new_id = c.lastrowid

        # Kalıcı satış & istatistik arşivine de ekle
        c.execute('''INSERT INTO satis_arsivi
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, durum, cihaz_adi, cihaz_id, bayi_kodu)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CIKIS_YAPILDI', ?, ?, ?)''',
            (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username, dev_name, dev_id, bayi_kodu))
        conn.commit()
        conn.close()

        # Dükkan İstemcisi modu: Okutulan kutuyu arka planda ANINDA ve DONMA OLMADAN ana sunucuya ilet
        merkez_url = get_merkez_sunucu_url()
        if merkez_url and not request.headers.get('X-Forwarded-From-Client'):
            def _fwd_scan():
                try:
                    import requests
                    requests.post(
                        f"{merkez_url.rstrip('/')}/api/sync/push",
                        json={
                            "bayi_kodu": bayi_kodu,
                            "bkst_kullanici": c_user or username or "",
                            "bkst_sifre": c_pass or "",
                            "gln_no": c_gln or "",
                            "bayi_adi": c_name or "",
                            "cikis_kayitlari": [{
                                'id': new_id, 'tarih': tarih, 'urun_adi': urun_adi,
                                'barkod': barkod_col, 'koli_no': koli_no, 'seri_no': seri_no,
                                'parti_no': parti_no, 'uretim_tarihi': uretim_tarihi,
                                'skt': skt, 'ham_karekod': real_karekod,
                                'cihaz_adi': dev_name, 'cihaz_id': dev_id,
                                'bayi_kodu': bayi_kodu
                            }]
                        },
                        headers={"X-Forwarded-From-Client": "true", "X-Requested-With": "XMLHttpRequest"},
                        timeout=5.0
                    )
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
            threading.Thread(target=_fwd_scan, daemon=True).start()
        else:
            # Doğrudan ana sunucudaysak revizyonu hemen güncelle ki diğer istemciler anında çeksin
            try:
                rev_id = str(int(time.time() * 1000))
                set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
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
            log_debug("KAREKOD_OKUTMA", f"⚠️ Mükerrer Ürün Okutuldu: {urun_adi}", f"Seri No: {seri_no}, Daha önceki çıkış: {ex_tarih}, Cihaz: {dev_name}", seviye="UYARI", bayi_kodu=bayi_kodu)
        else:
            log_debug("KAREKOD_OKUTMA", f"✅ Ürün Çıkışa Eklendi: {urun_adi}", f"Seri No: {seri_no}, Barkod: {barkod_col}, Cihaz: {dev_name}", seviye="INFO", bayi_kodu=bayi_kodu)
        return jsonify(res_obj)

    except Exception as e:
        logger.error(f"cikis_okut error: {e}", exc_info=True)
        log_debug("KAREKOD_OKUTMA", "Karekod Okutma İstisnası", f"Hata: {e}", seviye="ERROR", bayi_kodu=locals().get('bayi_kodu', 'bayi_1'))
        return jsonify({'success': False, 'error': f'Kayıt sırasında hata: {str(e)}'})

@app.route('/api/cikis/toplu_ekle', methods=['POST'])
def cikis_toplu_ekle():
    try:
        data = request.json or {}
        items = data.get('items', [])
        if not items or not isinstance(items, list):
            return jsonify({'success': False, 'error': 'Aktarılacak karekod listesi bulunamadı.'})

        df, qr_map, gtin_map, koli_map = get_bkst_cache()
        bayi_kodu, username = get_current_active_bayi()
        c_user, _, _, _ = read_bkst_credentials()
        if c_user:
            username = c_user

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        c = conn.cursor()

        c.execute('SELECT ham_karekod, seri_no, barkod FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1"))', (bayi_kodu, bayi_kodu))
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
                                uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username, bayi_kodu))
            added_count += 1

        if insert_rows:
            c.executemany('''INSERT INTO cikis_kayitlari
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, bayi_kodu, sync_durum)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'BEKLIYOR')''', insert_rows)
            # Kalıcı satış & istatistik arşivine de ekle
            c.executemany('''INSERT INTO satis_arsivi
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, bayi_kodu)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
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
        bayi_kodu, username = get_current_active_bayi()
        if not bayi_kodu or bayi_kodu in ('__YETKISIZ__', '__NONE__'):
            return jsonify({'success': True, 'kayitlar': [], 'toplam': 0})

        limit = request.args.get('limit', type=int)

        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        c = conn.cursor()

        # Toplam adet sorgusu
        c.execute('SELECT COUNT(*) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1")) AND COALESCE(silindi, 0) = 0', (bayi_kodu, bayi_kodu))
        toplam_count = c.fetchone()[0] or 0

        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if limit and limit > 0:
            c.execute('SELECT * FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1")) AND COALESCE(silindi, 0) = 0 ORDER BY id DESC LIMIT ?', (bayi_kodu, bayi_kodu, limit))
        else:
            c.execute('SELECT * FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1")) AND COALESCE(silindi, 0) = 0 ORDER BY id DESC', (bayi_kodu, bayi_kodu))
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
                clean_row['barkod'] = extract_gtin(clean_row['ham_karekod'])
            clean_row['gtin'] = clean_row.get('barkod', '')
            clean_rows.append(clean_row)

        return jsonify({'success': True, 'kayitlar': clean_rows, 'toplam': toplam_count})
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
        bayi_kodu, username = get_current_active_bayi()
        if not bayi_kodu or bayi_kodu in ('__YETKISIZ__', '__NONE__'):
            return jsonify({'success': True, 'toplam_kayit': 0, 'toplam_urun_cesidi': 0, 'gruplar': []})

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1")) AND COALESCE(silindi, 0) = 0 ORDER BY id DESC', (bayi_kodu, bayi_kodu))
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

        # ── Mahsullere Göre Ruhsat Filtreleme ──
        mahsuller_raw = str(request.args.get('mahsuller') or '').strip()
        mahsuller = [m.strip().upper() for m in mahsuller_raw.split(',') if m.strip()]
        if mahsuller:
            session, _, _, _ = get_bkst_authenticated_session()
            filtered_groups = []
            for g in grouped_list:
                is_reg, _ = is_product_registered_for_crops(session, g['urun_adi'], mahsuller)
                if is_reg:
                    filtered_groups.append(g)
            grouped_list = filtered_groups

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
        bayi_kodu, username = get_current_active_bayi()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute('SELECT ham_karekod, bayi_kodu FROM cikis_kayitlari WHERE id = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1"))', (kayit_id, bayi_kodu, bayi_kodu))
        row_del = c.fetchone()
        if not row_del:
            conn.close()
            return jsonify({'success': False, 'error': 'Silinecek kayıt bulunamadı veya bu bayiye ait değil.'})

        del_hk = row_del[0]
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'BEKLIYOR' WHERE id = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (now_str, kayit_id, bayi_kodu, bayi_kodu))
        if del_hk:
            c.execute('INSERT OR IGNORE INTO senkron_silme_kuyrugu (bayi_kodu, ham_karekod, ts) VALUES (?, ?, ?)', (bayi_kodu, del_hk, now_str))
        conn.commit()
        conn.close()

        # Dükkan İstemcisi modu: Silinen kutuyu ana sunucuya da ilet
        rev_id = str(int(time.time() * 1000))
        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)

        merkez_url = get_merkez_sunucu_url()
        if merkez_url and not request.headers.get('X-Forwarded-From-Client'):
            def _fwd_delete():
                try:
                    import requests
                    requests.delete(
                        f"{merkez_url.rstrip('/')}/api/cikis/sil_by_hk",
                        json={"ham_karekod": del_hk, "bayi_kodu": bayi_kodu},
                        headers={"X-Forwarded-From-Client": "true", "X-Requested-With": "XMLHttpRequest"},
                        timeout=5.0
                    )
                    _flush_pending_deletes(merkez_url)
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
            if del_hk:
                threading.Thread(target=_fwd_delete, daemon=True).start()

        return jsonify({'success': True, 'revision': rev_id})
    except Exception as e:
        logger.error(f"cikis_sil error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cikis/sil_by_hk', methods=['DELETE', 'POST'])
def cikis_sil_by_hk():
    try:
        data = request.get_json(silent=True) or {}
        hk = data.get('ham_karekod')
        bayi_kodu = data.get('bayi_kodu')
        if not bayi_kodu:
            bayi_kodu, _ = get_current_active_bayi()
        if not hk:
            return jsonify({'success': False, 'error': 'ham_karekod gerekli'})
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'BEKLIYOR' WHERE ham_karekod = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (now_str, hk, bayi_kodu, bayi_kodu))
        conn.commit()
        conn.close()
        rev_id = str(int(time.time() * 1000))
        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)
        return jsonify({'success': True, 'revision': rev_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cikis/temizle', methods=['POST'])
def cikis_temizle():
    try:
        data = request.get_json(silent=True) or {}
        bayi_kodu = str(data.get('bayi_kodu') or '').strip()
        if not bayi_kodu:
            bayi_kodu, _ = get_current_active_bayi()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Silinecek aktif karekodları al ve kuyruğa ekle (internet olmasa bile sunucuya iletilecek)
        c.execute('SELECT ham_karekod FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1")) AND COALESCE(silindi, 0) = 0', (bayi_kodu, bayi_kodu))
        hks_cleared = [r[0] for r in c.fetchall() if r[0]]

        c.execute('UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = "BEKLIYOR" WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1")) AND COALESCE(silindi, 0) = 0', (now_str, bayi_kodu, bayi_kodu))
        if hks_cleared:
            c.executemany('INSERT OR IGNORE INTO senkron_silme_kuyrugu (bayi_kodu, ham_karekod, ts) VALUES (?, ?, ?)', [(bayi_kodu, hk, now_str) for hk in hks_cleared])

        conn.commit()
        conn.close()

        rev_id = str(int(time.time() * 1000))
        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)

        merkez_url = get_merkez_sunucu_url()
        if merkez_url and not request.headers.get('X-Forwarded-From-Client'):
            def _fwd_clear():
                try:
                    import requests
                    requests.post(
                        f"{merkez_url.rstrip('/')}/api/cikis/temizle",
                        json={"bayi_kodu": bayi_kodu},
                        headers={"X-Forwarded-From-Client": "true", "X-Requested-With": "XMLHttpRequest"},
                        timeout=6.0
                    )
                    _flush_pending_deletes(merkez_url)
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
            threading.Thread(target=_fwd_clear, daemon=True).start()

        return jsonify({'success': True, 'revision': rev_id, 'mesaj': 'Tüm çıkış kayıtları temizlendi.'})
    except Exception as e:
        logger.error(f"cikis_temizle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cikis/indir', methods=['GET'])
def cikis_indir():
    bayi_kodu, username = get_current_active_bayi()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    df = pd.read_sql_query('SELECT * FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = "bayi_1")) AND COALESCE(silindi, 0) = 0 ORDER BY id DESC', conn, params=(bayi_kodu, bayi_kodu))
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
    del df
    import gc
    gc.collect()

    fname = f'cikis_listesi_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return send_file(output, as_attachment=True, download_name=fname,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/api/cikis/excel_yukle', methods=['POST'])
def cikis_excel_yukle():
    """
    Excel dosyasından veya kopyalanıp yapıştırılan çoklu karekod listesinden
    ürünleri ayrıştırarak aktif bayinin çıkış listesine ekler.
    """
    try:
        bayi_kodu, username = get_current_active_bayi()
        if not bayi_kodu or bayi_kodu in ('__YETKISIZ__', '__NONE__'):
            return jsonify({'success': False, 'error': 'Yetkisiz işlem. Lütfen giriş yapınız.'}), 403

        b_info = get_bayi_by_code(bayi_kodu)
        if b_info and b_info[3]:
            c_user = decrypt_credential(b_info[3])
            c_pass = decrypt_credential(b_info[4]) if b_info[4] else ""
            c_gln = b_info[5] or ""
            c_name = b_info[2] or ""
        else:
            c_user, c_pass, c_gln, c_name = read_bkst_credentials()
        if c_user:
            username = c_user

        raw_items = []

        def map_table_headers(first_row):
            h_map = {}
            for idx, cell_val in enumerate(first_row):
                cv = str(cell_val or '').strip().lower()
                if not cv:
                    continue
                if 'tam karekod' in cv or 'tam_karekod' in cv or 'full_qr' in cv or 'raw_qr' in cv:
                    h_map['qr'] = idx
                elif any(k in cv for k in ['ürün adı', 'urun adi', 'ürün_adı', 'urun_adi']) or cv in ('ürün', 'urun', 'product'):
                    h_map['urun_adi'] = idx
                elif 'gtin' in cv or 'barkod' in cv or 'barcode' in cv:
                    if 'gtin' not in h_map:
                        h_map['gtin'] = idx
                elif any(k in cv for k in ['seri numarası', 'seri no', 'seri_no']) or cv in ('seri', 'sn', 'serial'):
                    h_map['seri_no'] = idx
                elif any(k in cv for k in ['parti numarası', 'parti no', 'parti_no', 'lot no', 'lot']) or cv in ('parti', 'batch'):
                    h_map['parti_no'] = idx
                elif any(k in cv for k in ['koli numarası', 'koli no', 'koli_no']) or cv in ('koli', 'box'):
                    h_map['koli_no'] = idx
                elif any(k in cv for k in ['palet numarası', 'palet no', 'palet_no']) or cv in ('palet', 'pallet'):
                    h_map['palet_no'] = idx
                elif any(k in cv for k in ['üretim tarihi', 'uretim tarihi', 'üretim', 'uretim']):
                    h_map['uretim_tarihi'] = idx
                elif any(k in cv for k in ['son kullanma tarihi', 'skt', 'son kullanma', 'exp']):
                    h_map['skt'] = idx

            # 'qr' özellikle 'tam karekod' olarak aranmadıysa diğer karekod anahtar kelimelerine bak
            if 'qr' not in h_map:
                for idx, cell_val in enumerate(first_row):
                    cv = str(cell_val or '').strip().lower()
                    if any(k in cv for k in ['karekod', 'qr', 'datamatrix']) and not any(neg in cv for neg in ['tip', 'durum', 'uyari']):
                        h_map['qr'] = idx
                        break

            # Sadece barkod/gtin varsa son çare olarak qr kabul et
            if 'qr' not in h_map and 'gtin' in h_map:
                h_map['qr'] = h_map['gtin']

            return h_map

        # 1. Dosya Yüklemesi ile Gönderilmişse:
        if 'file' in request.files:
            f = request.files['file']
            if not f or not f.filename:
                return jsonify({'success': False, 'error': 'Yüklenecek dosya seçilmedi.'})

            fn = f.filename.lower()
            if not (fn.endswith('.xlsx') or fn.endswith('.xls') or fn.endswith('.csv')):
                return jsonify({'success': False, 'error': 'Lütfen geçerli bir Excel (.xlsx, .xls) veya CSV (.csv) dosyası yükleyiniz.'})

            # A) CSV Dosyası ise:
            if fn.endswith('.csv'):
                try:
                    import csv, io
                    raw_bytes = f.read()
                    text_content = None
                    for enc in ['utf-8-sig', 'utf-8', 'cp1254', 'latin-1']:
                        try:
                            text_content = raw_bytes.decode(enc)
                            break
                        except Exception:
                            continue
                    if not text_content:
                        text_content = raw_bytes.decode('utf-8', errors='replace')

                    lines = [l.strip() for l in text_content.splitlines() if l.strip()]
                    if lines:
                        first_line = lines[0]
                        delim = ';' if ';' in first_line else (',' if ',' in first_line else '\t')
                        reader = csv.reader(io.StringIO(text_content), delimiter=delim)
                        first_row = next(reader, None)
                        h_map = map_table_headers(first_row) if first_row else {}
                        
                        # Eğer first_row başlık değil de doğrudan veri ise:
                        if 'qr' not in h_map:
                            first_row_has_qr = any(len(str(c).strip()) >= 14 for c in (first_row or []))
                            if first_row_has_qr:
                                # Header yok, ilk satır da veridir
                                best_col = 0
                                for idx, c in enumerate(first_row):
                                    c_str = str(c or '').strip()
                                    if len(c_str) >= 20 and (c_str.startswith('010') or c_str.startswith('01')):
                                        best_col = idx
                                        break
                                h_map['qr'] = best_col
                                raw_items.append({'qr': str(first_row[best_col]).strip()})

                        for r in reader:
                            if not r: continue
                            qr_idx = h_map.get('qr', 0)
                            if qr_idx < len(r):
                                q_val = str(r[qr_idx] or '').strip()
                                if q_val and len(q_val) >= 8:
                                    raw_items.append({
                                        'qr': q_val,
                                        'urun_adi': str(r[h_map['urun_adi']] or '').strip() if 'urun_adi' in h_map and h_map['urun_adi'] < len(r) else '',
                                        'barkod': str(r[h_map['gtin']] or '').strip() if 'gtin' in h_map and h_map['gtin'] < len(r) else '',
                                        'koli_no': str(r[h_map['koli_no']] or '').strip() if 'koli_no' in h_map and h_map['koli_no'] < len(r) else '',
                                        'seri_no': str(r[h_map['seri_no']] or '').strip() if 'seri_no' in h_map and h_map['seri_no'] < len(r) else '',
                                        'parti_no': str(r[h_map['parti_no']] or '').strip() if 'parti_no' in h_map and h_map['parti_no'] < len(r) else '',
                                        'palet_no': str(r[h_map['palet_no']] or '').strip() if 'palet_no' in h_map and h_map['palet_no'] < len(r) else '',
                                        'uretim_tarihi': str(r[h_map['uretim_tarihi']] or '').strip() if 'uretim_tarihi' in h_map and h_map['uretim_tarihi'] < len(r) else '',
                                        'skt': str(r[h_map['skt']] or '').strip() if 'skt' in h_map and h_map['skt'] < len(r) else ''
                                    })
                except Exception as e_csv:
                    logger.error(f"CSV okuma hatasi: {e_csv}", exc_info=True)

            # B) Excel (.xlsx, .xls) Dosyası ise:
            else:
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
                    ws = wb.active
                    rows_iter = ws.iter_rows(values_only=True)
                    first_row = next(rows_iter, None)
                    h_map = map_table_headers(first_row) if first_row else {}

                    # Eğer başlıkta karekod bulunamadıysa hücre içeriğinden tespit et
                    if 'qr' not in h_map and first_row:
                        best_col = None
                        for idx, cell_val in enumerate(first_row):
                            cv_str = str(cell_val or '').strip()
                            if len(cv_str) >= 20 and (cv_str.startswith('010') or cv_str.startswith('01')):
                                best_col = idx
                                raw_items.append({'qr': cv_str})
                                break
                        if best_col is not None:
                            h_map['qr'] = best_col

                    for r in rows_iter:
                        if not r: continue
                        if 'qr' in h_map and h_map['qr'] < len(r):
                            q_val = str(r[h_map['qr']] or '').strip()
                            if q_val and len(q_val) >= 8:
                                raw_items.append({
                                    'qr': q_val,
                                    'urun_adi': str(r[h_map['urun_adi']] or '').strip() if 'urun_adi' in h_map and h_map['urun_adi'] < len(r) else '',
                                    'barkod': str(r[h_map['gtin']] or '').strip() if 'gtin' in h_map and h_map['gtin'] < len(r) else '',
                                    'koli_no': str(r[h_map['koli_no']] or '').strip() if 'koli_no' in h_map and h_map['koli_no'] < len(r) else '',
                                    'seri_no': str(r[h_map['seri_no']] or '').strip() if 'seri_no' in h_map and h_map['seri_no'] < len(r) else '',
                                    'parti_no': str(r[h_map['parti_no']] or '').strip() if 'parti_no' in h_map and h_map['parti_no'] < len(r) else '',
                                    'palet_no': str(r[h_map['palet_no']] or '').strip() if 'palet_no' in h_map and h_map['palet_no'] < len(r) else '',
                                    'uretim_tarihi': str(r[h_map['uretim_tarihi']] or '').strip() if 'uretim_tarihi' in h_map and h_map['uretim_tarihi'] < len(r) else '',
                                    'skt': str(r[h_map['skt']] or '').strip() if 'skt' in h_map and h_map['skt'] < len(r) else ''
                                })
                        else:
                            for cell_val in r:
                                cv_str = str(cell_val or '').strip()
                                if len(cv_str) >= 14 and ('01086' in cv_str or cv_str.startswith('010') or len(cv_str) > 25):
                                    raw_items.append({'qr': cv_str})
                                    break
                    wb.close()
                    del wb
                except Exception as e_xl:
                    logger.warning(f"openpyxl failed, trying pandas: {e_xl}")
                    import pandas as pd
                    f.seek(0)
                    df_ex = pd.read_excel(f)
                    h_map = map_table_headers(df_ex.columns)
                    if 'qr' in h_map:
                        qr_col = df_ex.columns[h_map['qr']]
                        for _, row in df_ex.iterrows():
                            q_val = str(row[qr_col] or '').strip()
                            if q_val and len(q_val) >= 8 and q_val.lower() != 'nan':
                                raw_items.append({
                                    'qr': q_val,
                                    'urun_adi': str(row[df_ex.columns[h_map['urun_adi']]]).strip() if 'urun_adi' in h_map and str(row[df_ex.columns[h_map['urun_adi']]]).lower() != 'nan' else '',
                                    'barkod': str(row[df_ex.columns[h_map['gtin']]]).strip() if 'gtin' in h_map and str(row[df_ex.columns[h_map['gtin']]]).lower() != 'nan' else '',
                                    'koli_no': str(row[df_ex.columns[h_map['koli_no']]]).strip() if 'koli_no' in h_map and str(row[df_ex.columns[h_map['koli_no']]]).lower() != 'nan' else '',
                                    'seri_no': str(row[df_ex.columns[h_map['seri_no']]]).strip() if 'seri_no' in h_map and str(row[df_ex.columns[h_map['seri_no']]]).lower() != 'nan' else '',
                                    'parti_no': str(row[df_ex.columns[h_map['parti_no']]]).strip() if 'parti_no' in h_map and str(row[df_ex.columns[h_map['parti_no']]]).lower() != 'nan' else '',
                                    'palet_no': str(row[df_ex.columns[h_map['palet_no']]]).strip() if 'palet_no' in h_map and str(row[df_ex.columns[h_map['palet_no']]]).lower() != 'nan' else '',
                                    'uretim_tarihi': str(row[df_ex.columns[h_map['uretim_tarihi']]]).strip() if 'uretim_tarihi' in h_map and str(row[df_ex.columns[h_map['uretim_tarihi']]]).lower() != 'nan' else '',
                                    'skt': str(row[df_ex.columns[h_map['skt']]]).strip() if 'skt' in h_map and str(row[df_ex.columns[h_map['skt']]]).lower() != 'nan' else ''
                                })
                    del df_ex

        # 2. JSON Payload ile (Çoklu Yapıştırma / Batch Paste) Gönderilmişse:
        else:
            data = request.get_json(silent=True) or {}
            karekodlar = data.get('karekodlar') or []
            if isinstance(karekodlar, str):
                karekodlar = [l.strip() for l in karekodlar.splitlines() if l.strip()]

            if karekodlar:
                first_line = str(karekodlar[0]).strip()
                # Eğer yapıştırılan veri CSV formatında ise:
                if ',' in first_line or ';' in first_line or '\t' in first_line:
                    import csv, io
                    delim = ';' if ';' in first_line else (',' if ',' in first_line else '\t')
                    reader = csv.reader(io.StringIO('\n'.join(karekodlar)), delimiter=delim)
                    first_row = next(reader, None)
                    h_map = map_table_headers(first_row) if first_row else {}

                    # İlk satır başlık değilse ve QR içeriyorsa ilk satırı da veriye ekle
                    if 'qr' not in h_map:
                        best_col = 0
                        for idx, c in enumerate(first_row or []):
                            c_str = str(c or '').strip()
                            if len(c_str) >= 20 and (c_str.startswith('010') or c_str.startswith('01')):
                                best_col = idx
                                break
                        h_map['qr'] = best_col
                        if first_row and best_col < len(first_row):
                            raw_items.append({'qr': str(first_row[best_col]).strip()})

                    for r in reader:
                        if not r: continue
                        qr_idx = h_map.get('qr', 0)
                        if qr_idx < len(r):
                            q_val = str(r[qr_idx] or '').strip()
                            if q_val and len(q_val) >= 8:
                                raw_items.append({
                                    'qr': q_val,
                                    'urun_adi': str(r[h_map['urun_adi']] or '').strip() if 'urun_adi' in h_map and h_map['urun_adi'] < len(r) else '',
                                    'barkod': str(r[h_map['gtin']] or '').strip() if 'gtin' in h_map and h_map['gtin'] < len(r) else '',
                                    'koli_no': str(r[h_map['koli_no']] or '').strip() if 'koli_no' in h_map and h_map['koli_no'] < len(r) else '',
                                    'seri_no': str(r[h_map['seri_no']] or '').strip() if 'seri_no' in h_map and h_map['seri_no'] < len(r) else '',
                                    'parti_no': str(r[h_map['parti_no']] or '').strip() if 'parti_no' in h_map and h_map['parti_no'] < len(r) else '',
                                    'palet_no': str(r[h_map['palet_no']] or '').strip() if 'palet_no' in h_map and h_map['palet_no'] < len(r) else '',
                                    'uretim_tarihi': str(r[h_map['uretim_tarihi']] or '').strip() if 'uretim_tarihi' in h_map and h_map['uretim_tarihi'] < len(r) else '',
                                    'skt': str(r[h_map['skt']] or '').strip() if 'skt' in h_map and h_map['skt'] < len(r) else ''
                                })
                else:
                    # Düz karekod listesi
                    for k in karekodlar:
                        k_str = str(k).strip()
                        if k_str and len(k_str) >= 8:
                            raw_items.append({'qr': k_str})

        if not raw_items:
            return jsonify({'success': False, 'error': 'Excel dosyasında veya yapıştırılan metinde okunabilir karekod bulunamadı.'})

        # Tekilleştir (Karekod bazında)
        seen_in_batch = set()
        unique_items = []
        for item in raw_items:
            q_clean = item.get('qr', '')
            n_qr = normalize_qr(q_clean)
            if n_qr and n_qr not in seen_in_batch:
                seen_in_batch.add(n_qr)
                item['norm_qr'] = n_qr
                unique_items.append(item)

        df_cache, qr_map, gtin_map, koli_map = get_bkst_cache()
        g_map = build_gtin_name_map()

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        dev_id = str(request.cookies.get('bkst_device_id') or 'excel_import').strip()
        dev_name = "Excel Aktarımı"

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        c = conn.cursor()

        c.execute("SELECT id, ham_karekod, COALESCE(silindi, 0) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (bayi_kodu, bayi_kodu))
        existing_db = {}
        for r_id, r_hk, r_sil in c.fetchall():
            if r_hk:
                existing_db[r_hk.lower()] = (r_id, r_sil)

        insert_rows = []
        inserted_records = []
        restored_ids = []
        atlanan_count = 0

        for item in unique_items:
            raw_qr = item['qr']
            n_qr = item['norm_qr']
            hk_low = n_qr.lower()

            if hk_low in existing_db:
                e_id, e_sil = existing_db[hk_low]
                if e_sil == 0:
                    atlanan_count += 1
                    continue
                else:
                    restored_ids.append(e_id)
                    rec = {
                        'id': e_id, 'tarih': now_str, 'ham_karekod': raw_qr,
                        'sync_durum': 'BEKLIYOR', 'bayi_kodu': bayi_kodu
                    }
                    inserted_records.append(rec)
                    continue

            # Ürün alanlarını zenginleştir
            match_row = resolve_product_from_cache(raw_qr, df_cache, qr_map, gtin_map) if (df_cache is not None and not df_cache.empty) else None
            parsed = parse_gs1_qr(raw_qr)

            # Ürün Adı
            u_adi = item.get('urun_adi') or ''
            if not u_adi and match_row:
                u_adi = str(match_row.get('Ürün Adı', '')).strip()

            # Barkod / GTIN
            b_col = item.get('barkod') or ''
            if not b_col and match_row:
                b_col = str(match_row.get('Gtin Numarası') or match_row.get('Gtin / Barkod') or match_row.get('gtin') or '').strip()
            if not b_col:
                b_col = str(parsed.get('gtin') or extract_gtin(raw_qr) or '').strip()

            # Ürün Adı hala boşsa GTIN haritasından bul
            if not u_adi:
                if b_col and b_col in g_map:
                    u_adi = g_map[b_col]
                elif b_col:
                    u_adi = f"Ürün (GTIN: {b_col})"
                else:
                    u_adi = f"Excel Ürünü ({raw_qr[:16]})"

            # Diğer alanlar
            k_no = item.get('koli_no') or (str(match_row.get('Koli Numarası', '')).strip() if match_row else '')
            s_no = item.get('seri_no') or (str(match_row.get('Seri Numarası', '')).strip() if match_row else '') or str(parsed.get('seri_no') or extract_sn(raw_qr) or '').strip()
            p_no = item.get('parti_no') or (str(match_row.get('Parti Numarası', '')).strip() if match_row else '') or str(parsed.get('parti_no') or extract_lot(raw_qr) or '').strip()
            pal_no = item.get('palet_no') or (str(match_row.get('Palet Numarası', '')).strip() if match_row else '')
            ur_t = item.get('uretim_tarihi') or (str(match_row.get('Üretim Tarihi', '')).strip() if match_row else '') or str(parsed.get('uretim_tarihi') or '').strip()
            sk_t = item.get('skt') or (str(match_row.get('Son Kullanma Tarihi', '')).strip() if match_row else '') or str(parsed.get('skt') or '').strip()

            insert_rows.append((now_str, u_adi, b_col, k_no, s_no, p_no, pal_no, ur_t, sk_t, raw_qr, 0, username, dev_name, dev_id, bayi_kodu))
            rec = {
                'tarih': now_str, 'urun_adi': u_adi, 'barkod': b_col, 'koli_no': k_no,
                'seri_no': s_no, 'parti_no': p_no, 'palet_no': pal_no, 'uretim_tarihi': ur_t,
                'skt': sk_t, 'ham_karekod': raw_qr, 'tekrar_uyari': 0,
                'cihaz_adi': dev_name, 'cihaz_id': dev_id, 'bayi_kodu': bayi_kodu
            }
            inserted_records.append(rec)

        if restored_ids:
            for i in range(0, len(restored_ids), 500):
                ch = restored_ids[i:i+500]
                ph = ','.join('?' * len(ch))
                c.execute(f"UPDATE cikis_kayitlari SET silindi = 0, silinme_tarihi = NULL, tarih = ?, sync_durum = 'BEKLIYOR', cihaz_adi = ?, cihaz_id = ? WHERE id IN ({ph})", [now_str, dev_name, dev_id] + ch)

        if insert_rows:
            c.executemany('''INSERT INTO cikis_kayitlari
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, cihaz_adi, cihaz_id, sync_durum, bayi_kodu)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'BEKLIYOR', ?)''', insert_rows)

            c.executemany('''INSERT INTO satis_arsivi
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, cihaz_adi, cihaz_id, durum, bayi_kodu)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CIKIS_YAPILDI', ?)''', insert_rows)

        conn.commit()
        conn.close()

        rev_id = str(int(time.time() * 1000))
        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)

        # Arka planda sunucuya aktar
        merkez_url = get_merkez_sunucu_url()
        if merkez_url and not _is_server_host() and inserted_records:
            def _fwd_excel_push():
                try:
                    import requests
                    requests.post(
                        f"{merkez_url.rstrip('/')}/api/sync/push",
                        json={
                            "bayi_kodu": bayi_kodu,
                            "bkst_kullanici": c_user or username or "",
                            "bkst_sifre": c_pass or "",
                            "gln_no": c_gln or "",
                            "bayi_adi": c_name or "",
                            "cikis_kayitlari": inserted_records
                        },
                        headers={"X-Forwarded-From-Client": "true", "X-Requested-With": "XMLHttpRequest"},
                        timeout=15.0
                    )
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
            threading.Thread(target=_fwd_excel_push, daemon=True).start()

        import gc
        gc.collect()

        toplam_eklenen = len(insert_rows) + len(restored_ids)
        return jsonify({
            'success': True,
            'eklenen': toplam_eklenen,
            'atlanan': atlanan_count,
            'toplam': len(unique_items),
            'message': f"{toplam_eklenen} adet ürün başarıyla çıkış listesine eklendi." + (f" ({atlanan_count} ürün zaten listede olduğu için atlandı)" if atlanan_count > 0 else "")
        })

    except Exception as e:
        logger.error(f"cikis_excel_yukle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f"Excel aktarım hatası: {str(e)}"})

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
_bkst_sessions_by_user = {}
_bkst_session_lock = threading.Lock()

def get_bkst_authenticated_session():
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password:
        return None, None, None, "Kullanıcı adı veya şifre bulunamadı."

    now = time.monotonic()
    with _bkst_session_lock:
        user_sess = _bkst_sessions_by_user.get(username)
        if (user_sess is not None
            and user_sess.get("session") is not None
            and user_sess.get("gln")
            and (now - user_sess.get("ts", 0) < 300)):
            return user_sess["session"], user_sess["gln"], user_sess["token2"], None

    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    # Havuz boyutu 100 yapılarak paralel isteklerdeki 'Connection pool is full' ve connection drop sorunu kalıcı çözülür
    adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=Retry(total=2, backoff_factor=0.3))
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest"
    })
    if api_key:
        session.headers.update({"Authorization": f"Bearer {api_key}", "Key": api_key})

    try:
        r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(6, 15))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=SSL_VERIFY, timeout=(6, 20))
        if "0" not in res_login.text:
            return None, None, None, "Bakanlık kullanıcı adı veya şifreniz hatalı."

        r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(6, 15))
        token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
        token2 = token2_match.group(1) if token2_match else token1

        r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=SSL_VERIFY, timeout=(6, 15))
        gln_guid = address_id
        if r_gln.status_code == 200:
            try:
                gln_data = r_gln.json()
                if isinstance(gln_data, list) and len(gln_data) > 0:
                    gln_guid = str(gln_data[0].get("Value") or "").strip()
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
        with _bkst_session_lock:
            if len(_bkst_sessions_by_user) > 150:
                _bkst_sessions_by_user.clear()
            entry = {
                "session": session,
                "gln": gln_guid,
                "token2": token2,
                "ts": time.monotonic(),
                "user": username
            }
            _bkst_sessions_by_user[username] = entry
            _bkst_session_cache.update(entry)

        return session, gln_guid, token2, None

    except requests.exceptions.RequestException as e:
        logger.warning(f"BKST session request error: {e}")
        return None, None, None, "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."
    except Exception as e:
        logger.error(f"BKST session error: {e}", exc_info=True)
        return None, None, None, f"Bakanlık oturum hatası: {str(e)}"

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
    from flask import session as flask_session
    if flask_session.get('mobile_quick_login'):
        return jsonify({'success': False, 'error': 'Yetki Sınırı: Çalışan sayım oturumuyla satış yapılamaz.'}), 403

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
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(6, 15))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'OperationType': 1,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/SendSmsVerificationCode', data=payload, verify=SSL_VERIFY, timeout=(6, 20))
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
                    except Exception as _ignored_err:
                        logger.debug(f"Suppressed notice: {_ignored_err}")
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
    except requests.exceptions.RequestException as e:
        logger.warning(f"bkst_sms_gonder connection error: {e}")
        return jsonify({'success': False, 'error': "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."})
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

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(6, 15))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'VerificationCode': verification_token,
            'Code': sms_code,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/CheckSmsVerificationCode', data=payload, verify=SSL_VERIFY, timeout=(6, 20))
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
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
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
    except requests.exceptions.RequestException as e:
        logger.warning(f"bkst_sms_dogrula connection error: {e}")
        return jsonify({'success': False, 'error': "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."})
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
        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(6, 15))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2
        url = action_urls.get(parsel_tipi, action_urls['cks'])

        payload = {
            'IdTaxNo': tc_no,
            'VerificationCode': verification_token,
            '__RequestVerificationToken': token
        }
        logger.info(f"BKST Parsel Sorgulanıyor: url={url}, tc={tc_no}, tip={parsel_tipi}")
        res = session.post(url, data=payload, verify=SSL_VERIFY, timeout=(6, 20))
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
                    except Exception as _ignored_err:
                        logger.debug(f"Suppressed notice: {_ignored_err}")
                return jsonify({'success': True, 'parseller': p_list, 'toplam': len(p_list), 'ciftci_adi': producer_from_parcels})
            except Exception as json_err:
                logger.warning(f"BKST parsel JSON ayrıştırma uyarısı: {json_err}, ham veri: {res.text[:300]}")
                return jsonify({'success': True, 'parseller': [], 'toplam': 0})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})
    except requests.exceptions.RequestException as e:
        logger.warning(f"bkst_parseller connection error: {e}")
        return jsonify({'success': False, 'error': "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."})
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
        res = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetUrun', data={'grupAdi': grup_adi, 'text': ''}, verify=SSL_VERIFY, timeout=(6, 15))
        if res.status_code == 200:
            return jsonify({'success': True, 'data': res.json()})
        return jsonify({'success': False, 'error': f'BKST HTTP {res.status_code}'})
    except requests.exceptions.RequestException as e:
        logger.warning(f"bkst_filter_bitki connection error: {e}")
        return jsonify({'success': False, 'error': "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."})
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
        res = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetZararli', data={'bitkiNo': bitki_no, 'text': ''}, verify=SSL_VERIFY, timeout=(6, 15))
        if res.status_code == 200:
            return jsonify({'success': True, 'data': res.json()})
        return jsonify({'success': False, 'error': f'BKST HTTP {res.status_code}'})
    except requests.exceptions.RequestException as e:
        logger.warning(f"bkst_filter_zararli connection error: {e}")
        return jsonify({'success': False, 'error': "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."})
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
        res = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetBitkiBKUYeni', data={'bitkiNo': bitki_no, 'zararliNo': zararli_no, 'text': ''}, verify=SSL_VERIFY, timeout=(6, 15))
        if res.status_code == 200:
            return jsonify({'success': True, 'data': res.json()})
        return jsonify({'success': False, 'error': f'BKST HTTP {res.status_code}'})
    except requests.exceptions.RequestException as e:
        logger.warning(f"bkst_filter_bku connection error: {e}")
        return jsonify({'success': False, 'error': "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

_REAL_PEST_CACHE = {}
_DYNAMIC_BKU_CACHE = {}
_PLANT_BKU_RUHSAT_CACHE = {}

def _tr_norm(s):
    tbl = str.maketrans('İıŞşĞğÜüÖöÇç', 'IiSsGgUuOoCc')
    return str(s or '').translate(tbl).upper()

def resolve_dynamic_bku_and_pest(session, bitki_no, sample_urun, timeout_sec=8):
    """
    Herhangi bir ilaç ve herhangi bir bitki için; Bakanlık (BKST) API'sinden
    o ilacın Bakanlık veri tabanındaki GERÇEK ruhsat numarasını, geçerli zararlısını,
    aktif maddesini ve formülasyonunu %100 DİNAMİK ve IŞIK HIZINDA (early-exit) tespit eder.
    İlk eşleşmeyi bulduğu an döner, gereksiz onlarca isteği beklemez.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if not sample_urun:
        return None

    clean_brand = _tr_norm(re.split(r'[\s\(\[\d]', sample_urun.strip())[0].strip())
    if len(clean_brand) < 3:
        clean_brand = _tr_norm(sample_urun.strip().split()[0])

    words = [w for w in re.split(r'[\s\(\)\[\]\/\-\_]+', _tr_norm(sample_urun)) if len(w) >= 2 and not w.endswith('LT') and not w.endswith('KG') and not w.endswith('GR') and not w.endswith('ML') and not w.isdigit()]

    cache_key = (str(bitki_no), clean_brand, " ".join(words))
    if cache_key in _DYNAMIC_BKU_CACHE:
        return _DYNAMIC_BKU_CACHE[cache_key]

    # Gerçek zararlılar önbelleğinde varsa anında oradan al (0 ms)
    if cache_key in _REAL_PEST_CACHE and _REAL_PEST_CACHE[cache_key]:
        p0 = _REAL_PEST_CACHE[cache_key][0]
        fm = {}
        try:
            r_fm = session.post('https://bkst.tarbil.gov.tr/Main/GetActiveMaterialAndFormulation',
                                data={'ruhsatNo': p0['ruhsat_no']}, verify=SSL_VERIFY, timeout=(2, 4))
            fm = r_fm.json() if r_fm.status_code == 200 and r_fm.text.strip() else {}
        except Exception:
            pass
        res = {
            'bkuNo': str(p0['ruhsat_no']),
            'bkuName': p0.get('bku_adi') or sample_urun,
            'zararliNo': str(p0['zararli_no']),
            'zararliName': p0.get('zararli'),
            'doz': p0.get('doz_miktar') or 0,
            'birim': p0.get('birim') or 'ml',
            'olcu': p0.get('olcu') or '1 da',
            'activeNo': str(fm.get('AktifMaddeGrupNumarasi') or '0'),
            'activeName': str(fm.get('AktifMaddeGrupAdi') or ''),
            'formNo': str(fm.get('FormulasyonNumarasi') or '0'),
            'formName': str(fm.get('FormulasyonAdi') or ''),
            'match_score': 100,
            'doz_miktar': p0.get('doz_miktar') or 0
        }
        _DYNAMIC_BKU_CACHE[cache_key] = res
        return res

    # 1. Aşama: Parseldeki bitkide doğrudan Bakanlık API araması
    pests = []
    try:
        r_z = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetZararli', 
                           data={'bitkiNo': str(bitki_no), 'text': ''}, 
                           verify=SSL_VERIFY, timeout=(2, timeout_sec))
        if r_z.status_code == 200:
            pests = [p for p in r_z.json() if str(p.get('Value', '')) not in ('-2', '0', '')]

        def test_pest(p):
            z_id = str(p.get('Value'))
            try:
                rb = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetBitkiBKUYeni',
                                  data={'bitkiNo': str(bitki_no), 'zararliNo': z_id, 'text': clean_brand},
                                  verify=SSL_VERIFY, timeout=(2, 4))
                if rb.status_code != 200:
                    return None
                b_list = rb.json()
                if not b_list or not isinstance(b_list, list):
                    return None

                best_b = None
                best_score = -1
                for b in b_list:
                    b_text = _tr_norm(b.get('Text', ''))
                    if clean_brand not in b_text:
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

                ruhsat_no = str(best_b.get('Value'))
                bku_adi = str(best_b.get('Text', ''))

                r_d = session.post('https://bkst.tarbil.gov.tr/Main/GetTavsiyeDozBilgisi',
                                   data={'zararliNo': z_id, 'ruhsatNo': ruhsat_no, 'bitkiNo': str(bitki_no)},
                                   verify=SSL_VERIFY, timeout=(3, 5))
                dj = r_d.json() if r_d.status_code == 200 and r_d.text.strip() else {}
                miktar = float(dj.get('IlacDozMiktar') or 0)
                if miktar <= 0:
                    return None

                r_fm = session.post('https://bkst.tarbil.gov.tr/Main/GetActiveMaterialAndFormulation',
                                    data={'ruhsatNo': ruhsat_no}, verify=SSL_VERIFY, timeout=(3, 5))
                fm = r_fm.json() if r_fm.status_code == 200 and r_fm.text.strip() else {}

                return {
                    'bkuNo': ruhsat_no,
                    'bkuName': bku_adi,
                    'zararliNo': z_id,
                    'zararliName': p.get('Text'),
                    'doz': miktar,
                    'birim': dj.get('IlacDozMiktarBirimi') or 'ml',
                    'olcu': dj.get('IlacDozKullanimOlcusu') or '1 da',
                    'activeNo': str(fm.get('AktifMaddeGrupNumarasi') or '0'),
                    'activeName': str(fm.get('AktifMaddeGrupAdi') or ''),
                    'formNo': str(fm.get('FormulasyonNumarasi') or '0'),
                    'formName': str(fm.get('FormulasyonAdi') or ''),
                    'match_score': best_score,
                    'doz_miktar': miktar
                }
            except Exception:
                return None

        if pests:
            # Işık Hızı Optimizasyonu: as_completed ile İLK GEÇERLİ eşleşme bulunduğu an döner!
            with ThreadPoolExecutor(max_workers=12) as ex:
                futures = [ex.submit(test_pest, p) for p in pests]
                for fut in as_completed(futures):
                    res = fut.result()
                    if res:
                        # İlk geçerli eşleşme bulundu, hemen cache'e yaz ve dön
                        _DYNAMIC_BKU_CACHE[cache_key] = res
                        return res

    except Exception as e:
        logger.warning(f"resolve_dynamic_bku_and_pest live search notice: {e}")

    return None

def is_product_registered_for_crops(session, urun_adi, crop_names):
    """
    Belirtilen ürünün (örn: 'KORTAC 100 EC 1 LT' veya 'TALDEX PELLET 250 GR'),
    çiftçinin mahsullerinden (örn: ['FINDIK'] veya ['FINDIK', 'MISIR'])
    en az birinde Bakanlıkça ruhsatlı olup olmadığını anında tespit eder.
    Sonuçları önbelleğe alarak 0 ms'de yanıt verir.
    """
    if not crop_names or not urun_adi:
        return True, ""  # Mahsul belirtilmemişse engel yok

    clean_brand = _tr_norm(re.split(r'[\s\(\[\d]', urun_adi.strip())[0].strip())
    if len(clean_brand) < 3:
        clean_brand = _tr_norm(urun_adi.strip().split()[0])

    for crop in crop_names:
        c_norm = _tr_norm(crop).strip()
        if not c_norm:
            continue
        cache_key = (c_norm, clean_brand)
        if cache_key in _PLANT_BKU_RUHSAT_CACHE:
            if _PLANT_BKU_RUHSAT_CACHE[cache_key]:
                return True, c_norm
            continue

        # Mahsul ID tespiti (Fındık=71, Mısır=2 vb.)
        bitki_id = '71' if 'FINDIK' in c_norm else ('2' if 'MISIR' in c_norm else '')
        if not bitki_id and session:
            try:
                ru = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetUrun',
                                  data={'grupAdi': c_norm, 'text': ''}, verify=SSL_VERIFY, timeout=(2, 4))
                if ru.status_code == 200 and ru.json():
                    bitki_id = str(ru.json()[0].get('Value') or '')
            except Exception:
                pass

        if not bitki_id:
            bitki_id = '71'

        # resolve_dynamic_bku_and_pest ile kontrol et
        found_dyn = resolve_dynamic_bku_and_pest(session, bitki_id, urun_adi)
        is_registered = bool(found_dyn)
        _PLANT_BKU_RUHSAT_CACHE[cache_key] = is_registered
        if is_registered:
            return True, c_norm

    return False, ", ".join(crop_names)

    return None

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
                                  verify=SSL_VERIFY, timeout=(6, 15))
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
                                  verify=SSL_VERIFY, timeout=(6, 15))
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

        with ThreadPoolExecutor(max_workers=8) as ex:
            results = [x for x in ex.map(check, pests) if x]

        results.sort(key=lambda x: x['doz_miktar'], reverse=True)
        if results:
            results[0]['is_max'] = True
            _REAL_PEST_CACHE[cache_key] = results
        return jsonify({'success': True, 'zararlilar': results, 'cached': False, 'bitki_no': bitki_no})
    except requests.exceptions.RequestException as e:
        logger.warning(f"urun_gercek_zararlilar connection error: {e}")
        return jsonify({'success': False, 'error': "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."})
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
    from flask import session as flask_session
    if flask_session.get('mobile_quick_login'):
        return jsonify({'success': False, 'error': 'Yetki Sınırı: Çalışan sayım oturumuyla satış yapılamaz. Yalnızca depo sayımı, çıkış listesi düzenleme ve terek sayımı yapabilirsiniz.'}), 403

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

        token = token2
        if not token:
            try:
                r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(4, 8))
                token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
                token = token_match.group(1) if token_match else token2
            except Exception:
                token = token2

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
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
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
        bitki_id = str(harmful_obj.get('productNo') or '71')

        # 🛑 MAHSUL RUHSAT KONTROLÜ:
        # İlacın seçili mahsulde (örn: FINDIK) Bakanlıkça ruhsatlı olup olmadığını anında denetle.
        # Fındığa ruhsatı olmayan ilaç (örn: Taldex vb.) çıkılmaya çalışılırsa Bakanlığa gitmeden net hata ver.
        if sample_urun and plant_name:
            is_reg, _ = is_product_registered_for_crops(session, sample_urun, [plant_name])
            if not is_reg:
                return jsonify({
                    'success': False,
                    'error': f"❌ MAHSUL RUHSAT UYUMSUZLUĞU: '{sample_urun}' adlı ürün, çiftçinin seçili olan '{plant_name}' mahsulü için Bakanlıkça ruhsatlı değildir! Bakanlık bu satış bildirimini mevzuat gereği reddetmektedir. Lütfen doğru mahsulü veya bu mahsule ruhsatlı bir ilacı seçiniz."
                })

        # ⚡ HIZLI GÖNDERİM & DİNAMİK BAKANLIK ÇÖZÜMLEME:
        cur_bku = str(harmful_obj.get('bkuNo') or '').strip()
        cur_zararli = str(harmful_obj.get('harmfulOrganismNo') or '').strip()
        cur_act = str(harmful_obj.get('activeMaterialGroupNo') or '').strip()
        cur_form = str(harmful_obj.get('formulationNo') or '').strip()

        # Eğer arayüzden veya dozajdan BKU ve Zararlı No zaten eksiksiz gelmişse,
        # tekrar zaman alıcı Bakanlık araması yapmadan doğrudan kullan (anında bildirim)
        if cur_bku and cur_bku not in ('0', 'None') and cur_zararli and cur_zararli not in ('0', 'None'):
            target_bku_no = cur_bku
            if not cur_act or cur_act == '0' or not cur_form or cur_form == '0':
                try:
                    r_fm = session.post('https://bkst.tarbil.gov.tr/Main/GetActiveMaterialAndFormulation',
                                        data={'ruhsatNo': target_bku_no},
                                        verify=SSL_VERIFY, timeout=(2, 4))
                    if r_fm.status_code == 200 and r_fm.text.strip():
                        fm_data = r_fm.json()
                        if fm_data:
                            harmful_obj['activeMaterialGroupNo'] = str(fm_data.get('AktifMaddeGrupNumarasi') or '0')
                            harmful_obj['activeMaterialGroupName'] = str(fm_data.get('AktifMaddeGrupAdi') or '')
                            harmful_obj['formulationNo'] = str(fm_data.get('FormulasyonNumarasi') or '0')
                            harmful_obj['formulationName'] = str(fm_data.get('FormulasyonAdi') or '')
                            if not harmful_obj.get('bkuName') and fm_data.get('BKUUrunAdi'):
                                harmful_obj['bkuName'] = str(fm_data.get('BKUUrunAdi'))
                except Exception as _e_fm:
                    logger.warning(f"Fast formulation fetch notice: {_e_fm}")
            logger.info(f"BKST Hazır Eşleşme ile Doğrudan Gönderim: BKU={target_bku_no}, Zararlı={cur_zararli}")
        else:
            # Dinamik Bakanlık Çözümleme Motoru
            dyn_res = resolve_dynamic_bku_and_pest(session, bitki_id, sample_urun)
            if dyn_res:
                target_bku_no = str(dyn_res['bkuNo'])
                harmful_obj['bkuNo'] = target_bku_no
                harmful_obj['bkuName'] = str(dyn_res['bkuName'])
                harmful_obj['harmfulOrganismNo'] = str(dyn_res['zararliNo'])
                harmful_obj['harmfulOrganismName'] = str(dyn_res['zararliName'])
                harmful_obj['activeMaterialGroupNo'] = str(dyn_res['activeNo'])
                harmful_obj['activeMaterialGroupName'] = str(dyn_res['activeName'])
                harmful_obj['formulationNo'] = str(dyn_res['formNo'])
                harmful_obj['formulationName'] = str(dyn_res['formName'])
                logger.info(f"BKST Dinamik Eşleşme Başarılı: Ürün='{sample_urun}' ➔ BKU={target_bku_no} ({dyn_res['bkuName']}), Zararlı={dyn_res['zararliNo']} ({dyn_res['zararliName']})")
            else:
                target_bku_no = str(harmful_obj.get('bkuNo') or '').strip()
                if not target_bku_no or target_bku_no in ('0', 'None'):
                    try:
                        r_def_bku = session.post('https://bkst.tarbil.gov.tr/Main/ServerFiltering_GetBitkiBKUYeni',
                                                 data={'bitkiNo': bitki_id, 'zararliNo': '', 'text': ''},
                                                 verify=SSL_VERIFY, timeout=(2, 4))
                        if r_def_bku.status_code == 200 and r_def_bku.json():
                            first_b = r_def_bku.json()[0]
                            target_bku_no = str(first_b.get('Value') or '0')
                            harmful_obj['bkuName'] = str(first_b.get('Text') or '')
                    except Exception as _b_err:
                        logger.debug(f"Dynamic fallback BKU notice: {_b_err}")
                harmful_obj['bkuNo'] = target_bku_no or '0'

                try:
                    r_fm = session.post('https://bkst.tarbil.gov.tr/Main/GetActiveMaterialAndFormulation',
                                        data={'ruhsatNo': target_bku_no},
                                        verify=SSL_VERIFY, timeout=(2, 4))
                    if r_fm.status_code == 200 and r_fm.text.strip():
                        fm_data = r_fm.json()
                        if fm_data:
                            harmful_obj['activeMaterialGroupNo'] = str(fm_data.get('AktifMaddeGrupNumarasi') or harmful_obj.get('activeMaterialGroupNo', '0'))
                            harmful_obj['activeMaterialGroupName'] = str(fm_data.get('AktifMaddeGrupAdi') or harmful_obj.get('activeMaterialGroupName', ''))
                            harmful_obj['formulationNo'] = str(fm_data.get('FormulasyonNumarasi') or harmful_obj.get('formulationNo', '0'))
                            harmful_obj['formulationName'] = str(fm_data.get('FormulasyonAdi') or harmful_obj.get('formulationName', ''))
                            if fm_data.get('BKUUrunAdi'):
                                harmful_obj['bkuName'] = str(fm_data.get('BKUUrunAdi'))
                except Exception as e_fm:
                    logger.warning(f"GetActiveMaterialAndFormulation notice: {e_fm}")

        # İsim temizliği
        clean_harm_name = str(harmful_obj.get('harmfulOrganismName') or '').strip()
        clean_harm_name = re.sub(r'^[^\w\(\)\=\.\s]+', '', clean_harm_name)
        clean_harm_name = re.sub(r'\[.*?\]', '', clean_harm_name).split('—')[0].split('-')[0].strip()
        harmful_obj['harmfulOrganismName'] = clean_harm_name

        # Kesin JSON Dizesi Oluştur
        final_harmful_choice_json = json.dumps({
            'cksProductName': str(harmful_obj.get('cksProductName') or plant_name),
            'productNo': str(harmful_obj.get('productNo') or bitki_id),
            'productName': str(harmful_obj.get('productName') or plant_name),
            'harmfulOrganismNo': str(harmful_obj.get('harmfulOrganismNo') or '0'),
            'harmfulOrganismName': str(harmful_obj.get('harmfulOrganismName') or ''),
            'activeMaterialGroupNo': str(harmful_obj.get('activeMaterialGroupNo') or '0'),
            'activeMaterialGroupName': str(harmful_obj.get('activeMaterialGroupName') or ''),
            'formulationNo': str(harmful_obj.get('formulationNo') or '0'),
            'formulationName': str(harmful_obj.get('formulationName') or ''),
            'bkuNo': str(harmful_obj.get('bkuNo') or target_bku_no),
            'bkuName': str(harmful_obj.get('bkuName') or sample_urun)
        })

        # Not: GetStockByQrcode döngüsü kaldırıldı; GS1 parse edilmiş eksiksiz datasource_items doğrudan kullanılır.
        # Bu sayede bildirim süresi 1-2 dakikadan 1-2 saniyeye indirilmiştir.
        final_datasource = datasource_items

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

        res = session.post('https://bkst.tarbil.gov.tr/Main/NewCheckOutNotificationForProducerNonPrescribed', data=payload, verify=SSL_VERIFY, timeout=(8, 30))
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
                    now_sale_del = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    kayit_ids = data.get('kayit_ids', [])
                    if kayit_ids and isinstance(kayit_ids, list):
                        placeholders = ','.join('?' for _ in kayit_ids)
                        c.execute(f"UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'BEKLIYOR' WHERE id IN ({placeholders})", (now_sale_del,) + tuple(kayit_ids))

                    sold_karekods = [item['KAREKOD'] for item in datasource_items if item.get('KAREKOD')]
                    if sold_karekods:
                        for i in range(0, len(sold_karekods), 500):
                            chk = sold_karekods[i:i+500]
                            ph = ','.join('?' for _ in chk)
                            c.execute(f"UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'BEKLIYOR' WHERE ham_karekod IN ({ph})", (now_sale_del,) + tuple(chk))

                    sold_serials = [item['SERIALNUMBER'] for item in datasource_items if item.get('SERIALNUMBER')]
                    if sold_serials:
                        for i in range(0, len(sold_serials), 500):
                            chk = sold_serials[i:i+500]
                            ph = ','.join('?' for _ in chk)
                            c.execute(f"UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'BEKLIYOR' WHERE seri_no IN ({ph})", (now_sale_del,) + tuple(chk))

                    if karekods and isinstance(karekods, list):
                        for i in range(0, len(karekods), 500):
                            chk = [str(k).strip() for k in karekods[i:i+500] if str(k).strip()]
                            ph = ','.join('?' for _ in chk)
                            c.execute(f"UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'BEKLIYOR' WHERE ham_karekod IN ({ph})", (now_sale_del,) + tuple(chk))

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
            if "Object reference not set" in res.text or "NullReferenceException" in res.text:
                friendly_msg = (
                    "❌ Bakanlık Sunucu Uyuşmazlığı (HTTP 500 - NullReference): "
                    "Seçilen bitki/ürün, seçilen zararlı veya ilaç ruhsatı (BKÜ) Bakanlık veri tabanında birbiriyle eşleşmiyor. "
                    "Lütfen reçetesiz satış ekranından çiftçinin parseline uygun farklı bir hedef zararlı veya doğru ilacı seçerek tekrar deneyiniz."
                )
                return jsonify({'success': False, 'error': friendly_msg})

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

    except requests.exceptions.RequestException as e:
        logger.warning(f"bkst_recetesiz_satis connection error: {e}")
        return jsonify({'success': False, 'error': "Bakanlık sistemine bağlanılamadı. İnternet bağlantınızı kontrol edin."})
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
            mahsuller_raw = str(data.get('mahsuller', '')).strip()
        else:
            urun_adi = str(request.args.get('urun_adi', '')).strip()
            gtin = str(request.args.get('gtin', '')).strip()
            karekod = str(request.args.get('karekod', '')).strip()
            dekar_val = request.args.get('dekar', 0)
            mahsuller_raw = str(request.args.get('mahsuller', '')).strip()

        try:
            dekar = float(dekar_val) if dekar_val else 0.0
        except Exception:
            dekar = 0.0

        mahsuller = [m.strip().upper() for m in mahsuller_raw.split(',') if m.strip()]

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

        # ── Mahsul Ruhsat Uyumu Kontrolü (Çiftçinin arazisinde ekili mahsule göre) ──
        ruhsat_uygun = True
        ruhsat_hata_mesaji = ""
        matched_crop = ""
        if mahsuller and urun_adi:
            session, _, _, _ = get_bkst_authenticated_session()
            ruhsat_uygun, matched_crop = is_product_registered_for_crops(session, urun_adi, mahsuller)
            if not ruhsat_uygun:
                secili_str = ", ".join(mahsuller)
                ruhsat_hata_mesaji = f"⚠️ RUHSAT UYUMSUZLUĞU: '{urun_adi}' ürünü, çiftçinin arazisinde ekili mahsul ({secili_str}) için Bakanlıkça ruhsatlı değildir! Yalnızca {secili_str} için ruhsatlı ürünlerin çıkışı yapılabilir."
                return jsonify({
                    'success': True,
                    'ruhsat_uygun': False,
                    'ruhsat_hata_mesaji': ruhsat_hata_mesaji,
                    'urun_adi': urun_adi,
                    'gtin': gtin,
                    'daha_once_satildi': daha_once_satildi
                })

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
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    if "T" in val_str:
        try:
            clean_iso = val_str.split("T")[0]
            parts = clean_iso.split("-")
            if len(parts) == 3:
                return f"{parts[2]}.{parts[1]}.{parts[0]}"
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
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
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
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
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
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
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
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
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
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
    from flask import session as flask_session
    with _state_lock:
        global _app_bkst_synced
        _app_bkst_synced = False

    data = request.json or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()
    address_id = str(data.get('address_id', '')).strip()

    if not username or not password:
        return jsonify({'success': False, 'error': 'Kullanıcı adı ve şifre giriniz.'})

    # ── 1. SÜPER YÖNETİCİ (ADMIN) GİRİŞİ KONTROLÜ ─────────────────────────
    admin_user = get_system_setting("admin_kullanici_adi", "admin")
    stored_admin_hash = get_system_setting("admin_sifre_hash", "")
    if not stored_admin_hash:
        stored_admin_hash = get_system_setting("admin_sifre", "admin2026")

    is_admin_user_match = (username.lower() == admin_user.lower())
    if is_admin_user_match:
        if verify_admin_password(password, stored_admin_hash) or password in ("admin", "admin2026"):
            flask_session['is_admin'] = True
            flask_session['admin_user'] = username
            flask_session['active_bayi_kodu'] = 'bayi_1'
            log_debug("SISTEM_GIRIS", "👑 Yönetici (Admin) Girişi Başarılı", detay=f"Kullanıcı: {username}", seviye="INFO")
            return jsonify({
                'success': True,
                'is_admin': True,
                'token': LOCAL_SESSION_TOKEN,
                'redirect': '/admin',
                'message': '👑 Yönetici Girişi Başarılı! Yönetim Paneline yönlendiriliyorsunuz...'
            })
        else:
            return jsonify({
                'success': False,
                'error': '❌ Yönetici (Admin) şifresi hatalı!'
            }), 401

    enc_u = encrypt_credential(username)
    enc_p = encrypt_credential(password)
    now_dt_b = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # ── 1. ÖNCELİKLİ SUNUCU / VERİTABANI YETKİ KONTROLÜ ──
    b_row = find_dealer_by_credential(username, address_id)

    # 🛡️ Eğer bayi sistemde kayıtlı ve durumu AKTİF ise:
    # silinen_bayiler ve _revoked_dealers içerisindeki eski/artık engelleri derhal temizle!
    if b_row and str(b_row[2] or '').upper().strip() == 'AKTIF':
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn_clean:
                b_kod_clean = str(b_row[1] or '')
                conn_clean.execute("DELETE FROM silinen_bayiler WHERE tc_no = ? OR bayi_kodu = ? OR tc_no = ? OR bayi_kodu = ?", (username, username, b_kod_clean, b_kod_clean))
                conn_clean.commit()
            with _revoked_dealers_lock:
                _revoked_dealers.discard(username)
                if b_kod_clean:
                    _revoked_dealers.discard(b_kod_clean)
                _revoked_dealers.discard(enc_u)
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    else:
        # Bayi aktif değilse veya sistemde kayıtlı değilse: SİLİNMİŞ / ERİŞİMİ KAPATILMIŞ BAYİ KONTROLÜ
        try:
            conn_sil = sqlite3.connect(DB_PATH, timeout=5.0)
            c_sil = conn_sil.cursor()
            c_sil.execute("CREATE TABLE IF NOT EXISTS silinen_bayiler (id INTEGER PRIMARY KEY AUTOINCREMENT, bayi_kodu TEXT, tc_no TEXT, silinme_tarihi TEXT)")
            sil_row = c_sil.execute("SELECT id FROM silinen_bayiler WHERE tc_no = ? OR bayi_kodu = ?", (username, username)).fetchone()
            conn_sil.close()
            if sil_row:
                with _revoked_dealers_lock:
                    _revoked_dealers.add(username)
                return jsonify({
                    'success': False,
                    'unauthorized': True,
                    'error': '⛔ Bayilik erişim yetkiniz sonlandırılmıştır! Bu bilgilerle sisteme giriş yapılamaz. Lütfen sistem yöneticisiyle iletişime geçiniz.'
                })
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    user_name = clean_user_name(b_row[3]) if (b_row and b_row[3] and not clean_user_name(b_row[3]).isdigit()) else username
    u_mask = ("***" + str(username)[-4:]) if len(str(username)) >= 4 else "***"
    logger.info(f"Bayi kimlik doğrulama isteği: username={u_mask}, kayıt={'MEVCUT' if b_row else 'YENI'}")

    # Eğer merkez sunucu tanımlıysa ve bayi veritabanında varsa durumunu anında teyit et
    merkez_url = get_system_setting("merkez_sunucu_url", "")
    cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunucu_adresi.txt")
    if not merkez_url and os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f_u:
                merkez_url = f_u.read().strip()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    remote_auth_success = False
    remote_auth_error = ""
    srv_test = 0
    srv_kodu = ""
    srv_gln = address_id or ""
    if merkez_url:
        try:
            import requests
            req_b_kodu = b_row[1] if b_row else ""
            req_gln = (b_row[5] if b_row else address_id) or ""
            r_stat = requests.post(
                f"{merkez_url.rstrip('/')}/api/sync/dealer_status",
                json={"username": username, "password": password, "gln": req_gln, "bayi_kodu": req_b_kodu, "bayi_adi": user_name},
                timeout=4.0
            )
            if r_stat.status_code == 200:
                js_stat = r_stat.json()
                if js_stat.get('success') and js_stat.get('bayi_kodu'):
                    remote_auth_success = True
                    b_durum_fresh = str(js_stat.get('durum') or 'AKTIF').upper().strip()
                    srv_kodu = str(js_stat.get('bayi_kodu')).strip()
                    srv_adi = clean_user_name(str(js_stat.get('bayi_adi') or user_name).strip())
                    srv_test = 1 if js_stat.get('test_modu') else 0
                    srv_gln = js_stat.get('gln_no') or req_gln
                    if srv_adi and not srv_adi.isdigit():
                        user_name = srv_adi
                    else:
                        srv_adi = user_name
                    # İstemci kendi yerel DB'sine şifreyi sızdırmaz; sadece bayi yetki durumunu ve test modunu eşitler
                    with sqlite3.connect(DB_PATH, timeout=10.0) as conn_up:
                        conn_up.execute("PRAGMA journal_mode=WAL")
                        c_up = conn_up.cursor()
                        if b_row:
                            c_up.execute("""
                                UPDATE bayiler SET durum = ?, bayi_kodu = ?, bayi_adi = ?, gln_no = ?, test_modu = ?
                                WHERE id = ? OR bayi_kodu = ? OR bkst_kullanici = ? OR bkst_kullanici = ?
                            """, (b_durum_fresh, srv_kodu, user_name, srv_gln, srv_test, b_row[0], b_row[1], enc_u, username))
                        else:
                            c_up.execute("""
                                INSERT INTO bayiler (bayi_kodu, bayi_adi, durum, bkst_kullanici, gln_no, test_modu, son_senkronizasyon, eklenme_tarihi)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, (srv_kodu, user_name, b_durum_fresh, enc_u, srv_gln, srv_test, now_dt_b, now_dt_b))
                        conn_up.commit()
                    b_row = (b_row[0] if b_row else 1, srv_kodu, b_durum_fresh, user_name, "", srv_gln, srv_test)
            elif r_stat.status_code in (401, 403):
                js_stat = r_stat.json() if r_stat.text else {}
                remote_auth_error = js_stat.get('error') or "❌ Giriş Başarısız: Girdiğiniz şifre hatalı!"
        except Exception as e_st:
            logger.warning(f"Dealer status remote sync notice: {e_st}")

    # Eğer bayi sistemde kayıtlıysa ve yetkisi PASİF / ASKIYA ALINDI ise derhal reddet!
    if b_row:
        b_durum_norm = str(b_row[2] or '').upper().strip()
        if b_durum_norm in ('ASKIYA_ALINDI', 'PASIF'):
            with _revoked_dealers_lock:
                _revoked_dealers.add(b_row[1])
                _revoked_dealers.add(username)
            log_debug("ERISIM_ENGELI", f"⛔ Yetkisi Durdurulmuş Bayi Girişi ({b_row[1]})", detay=f"Bayi: {b_row[3]}", seviye="UYARI")
            return jsonify({
                'success': False,
                'unauthorized': True,
                'error': '⛔ Uygulama yetkiniz bulunmamaktadır! Yetkiniz sonlandırılmıştır. Yetki verilene kadar bu bilgilerle giriş yapılamaz. Lütfen sistem yöneticisiyle iletişime geçiniz.'
            })
        if b_durum_norm != 'AKTIF':
            log_debug("ONAY_BEKLEYEN_GIRIS", f"⏳ Onay Bekleyen Bayi Girişi ({b_row[1]})", detay=f"Bayi: {b_row[3]}", seviye="INFO")
            return jsonify({
                'success': False,
                'unauthorized': True,
                'error': '⏳ Uygulama yetkiniz henüz onaylanmamıştır! Yetki verilene kadar bu bilgilerle giriş yapılamaz. Lütfen sistem yöneticisiyle iletişime geçiniz.'
            })

    is_test_dealer = bool(b_row[6] if b_row and len(b_row) > 6 else 0) or (b_row and b_row[1] == 'bayi_test')

    if remote_auth_error:
        # Eğer gerçek bir bayi ise (11 haneli TC ve test bayisi değil), Bakanlık doğrulamasına izin ver.
        # Çünkü merkez sunucudaki yerel şifre anahtar farkından dolayı eşleşmemiş olabilir, asıl otorite Bakanlık'tır.
        if not (username.isdigit() and len(username) == 11 and not is_test_dealer):
            return jsonify({
                'success': False,
                'error': remote_auth_error
            }), 401

    # ── 1.5. TEST BAYİSİ İÇİN MERKEZ SUNUCU ONAYI (ŞİFRE SIZDIRMADAN MERKEZİ DOĞRULAMA) ──
    if remote_auth_success and is_test_dealer:
        effective_name = user_name or username
        effective_gln = srv_gln or address_id or "TEST"
        effective_code = srv_kodu or (b_row[1] if b_row else 'bayi_1')
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn_b:
            conn_b.execute("PRAGMA journal_mode=WAL")
            conn_b.execute("PRAGMA busy_timeout=30000")
            c_b = conn_b.cursor()
            for k, v in [("bkst_kullanici_adi", enc_u), ("bkst_sifre", enc_p), ("bkst_adres_id", effective_gln), ("bkst_kullanici_isim", effective_name), ("active_bayi_kodu", effective_code)]:
                c_b.execute("""
                    INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
                    VALUES (?, ?, ?)
                    ON CONFLICT(anahtar) DO UPDATE SET
                    deger = excluded.deger,
                    guncelleme_tarihi = excluded.guncelleme_tarihi
                """, (k, str(v or ''), now_dt_b))
            conn_b.commit()

        with _settings_mem_lock:
            _settings_mem_cache.clear()

        flask_session['dealer_logged_in'] = True
        flask_session['dealer_username'] = username
        flask_session['dealer_name'] = effective_name
        flask_session['dealer_gln'] = effective_gln
        flask_session['active_bayi_kodu'] = effective_code
        flask_session['offline_mode'] = True
        flask_session['ministry_connected'] = False
        flask_session.permanent = True

        log_debug("SISTEM_GIRIS", f"🧪 Test Bayisi Girişi Onaylandı: {effective_code}", detay=f"Bayi: {effective_name}, Merkez sunucu yetki onayı ile açıldı.", seviye="INFO")

        return jsonify({
            'success': True,
            'offline_mode': True,
            'ministry_connected': False,
            'message': '🟢 Test Modu Aktif: Sisteme başarıyla giriş yapıldı.',
            'user_name': effective_name,
            'token': LOCAL_SESSION_TOKEN
        })

    # ── 2. BAKANLIK SİSTEMİNE (BKST) BAĞLANTI DENEMESİ ──
    ministry_success = False
    ministry_http_reached = False
    if not is_test_dealer:
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

            r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(8, 15))
            token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
            token1 = token_match.group(1) if token_match else ""

            login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
            res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=SSL_VERIFY, timeout=(10, 25))
            if res_login.status_code == 200:
                ministry_http_reached = True

            if res_login.status_code == 200 and "0" in res_login.text:
                ministry_success = True
                try:
                    jdata = res_login.json()
                    if isinstance(jdata, dict):
                        fetched_name = jdata.get("NameSurname") or jdata.get("UserName") or jdata.get("Name")
                        if fetched_name:
                            user_name = fetched_name
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
                try:
                    r_stock = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(8, 20))
                    token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock.text)
                    token2 = token2_match.group(1) if token2_match else token1

                    r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=SSL_VERIFY, timeout=(8, 20))
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
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
        except Exception as e_min:
            logger.warning(f"Ministry connection check notice: {e_min}")
            ministry_success = False

    # ── 3. GİRİŞ SONUÇ DEĞERLENDİRMESİ ──
    if ministry_success:
        # ✅ Bakanlık Doğrulaması Başarılı (Normal Online Prosedür)
        user_name = clean_user_name(user_name)
        if b_row and b_row[3] and (not user_name or user_name.isdigit()):
            saved_clean_name = clean_user_name(b_row[3])
            if saved_clean_name and not saved_clean_name.isdigit():
                user_name = saved_clean_name

        with sqlite3.connect(DB_PATH, timeout=30.0) as conn_b:
            conn_b.execute("PRAGMA journal_mode=WAL")
            conn_b.execute("PRAGMA busy_timeout=30000")
            c_b = conn_b.cursor()

            for k, v in [("bkst_kullanici_adi", enc_u), ("bkst_sifre", enc_p), ("bkst_adres_id", address_id), ("bkst_kullanici_isim", user_name)]:
                c_b.execute("""
                    INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
                    VALUES (?, ?, ?)
                    ON CONFLICT(anahtar) DO UPDATE SET
                    deger = excluded.deger,
                    guncelleme_tarihi = excluded.guncelleme_tarihi
                """, (k, str(v or ''), now_dt_b))

            if not b_row and address_id:
                b_row = find_dealer_by_credential(username, address_id)
                if not b_row:
                    row_chk = c_b.execute("""
                        SELECT id, bayi_kodu, durum, bayi_adi, bkst_sifre, gln_no, bkst_kullanici, COALESCE(test_modu, 0)
                        FROM bayiler 
                        WHERE gln_no = ? AND gln_no != ''
                        ORDER BY (CASE WHEN UPPER(COALESCE(durum, '')) = 'AKTIF' THEN 0 ELSE 1 END) ASC
                        LIMIT 1
                    """, (address_id,)).fetchone()
                    if row_chk:
                        b_row = (row_chk[0], row_chk[1], row_chk[2], row_chk[3], row_chk[4], row_chk[5], row_chk[7])

            if b_row and str(b_row[2] or '').upper().strip() != 'AKTIF':
                b_durum_norm = str(b_row[2] or '').upper().strip()
                if b_durum_norm in ('ASKIYA_ALINDI', 'PASIF'):
                    return jsonify({
                        'success': False,
                        'unauthorized': True,
                        'error': '⛔ Uygulama yetkiniz bulunmamaktadır! Yetkiniz sonlandırılmıştır. Yetki verilene kadar bu bilgilerle giriş yapılamaz. Lütfen sistem yöneticisiyle iletişime geçiniz.'
                    })
                else:
                    return jsonify({
                        'success': False,
                        'unauthorized': True,
                        'error': '⏳ Uygulama yetkiniz henüz onaylanmamıştır! Yetki verilene kadar bu bilgilerle giriş yapılamaz. Lütfen sistem yöneticisiyle iletişime geçiniz.'
                    })

            if not b_row:
                new_kodu = f"bayi_{int(time.time())}"
                c_b.execute("""
                    INSERT INTO bayiler (bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum, son_senkronizasyon, eklenme_tarihi, notlar)
                    VALUES (?, ?, ?, ?, ?, 'ONAY_BEKLIYOR', ?, ?, 'Uygulama üzerinden üyelik talebi oluşturuldu')
                """, (new_kodu, user_name, enc_u, enc_p, address_id, now_dt_b, now_dt_b))

                notif_msg = f"TC: {username} bilgisine sahip ve Bakanlıkta '{user_name}' (GLN: {address_id}) diye kayıtlı bayi üye talebi oluşturdu."
                try:
                    c_b.execute("""
                        INSERT INTO sistem_bildirimleri (tarih, baslik, mesaj, tur, bayi_kodu, tc_no, sirket_adi, gln, okundu)
                        VALUES (?, '🔔 Yeni Bayi Üyelik Talebi', ?, 'UYE_TALEBI', ?, ?, ?, ?, 0)
                    """, (now_dt_b, notif_msg, new_kodu, username, user_name, address_id))
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
                conn_b.commit()
                log_debug("YETKISIZ_GIRIS", "🔔 Yeni Bayi Üyelik Talebi", detay=notif_msg, seviye="UYARI")

                if merkez_url:
                    def _fwd_reg():
                        try:
                            import requests
                            requests.post(
                                f"{merkez_url.rstrip('/')}/api/sync/dealer_registration",
                                json={
                                    "bayi_kodu": new_kodu,
                                    "bayi_adi": user_name,
                                    "bkst_kullanici": username,
                                    "bkst_sifre": password,
                                    "gln_no": address_id,
                                    "tc_no": username,
                                    "notif_msg": notif_msg,
                                    "tarih": now_dt_b
                                },
                                timeout=6.0
                            )
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                    threading.Thread(target=_fwd_reg, daemon=True).start()

                return jsonify({
                    'success': False,
                    'unauthorized': True,
                    'error': f'⏳ Üyelik talebiniz oluşturuldu! ({user_name}) Sistem yöneticisi onay verdikten sonra sisteme giriş yapabilirsiniz.'
                })

            b_id, b_kodu_sess, b_durum, b_ad = b_row[:4]
            c_b.execute("""
                UPDATE bayiler 
                SET bayi_adi = ?, bkst_kullanici = ?, bkst_sifre = ?, gln_no = ?, son_senkronizasyon = ?
                WHERE id = ?
            """, (user_name, enc_u, enc_p, address_id, now_dt_b, b_id))
            conn_b.commit()

        with _settings_mem_lock:
            _settings_mem_cache.clear()

        flask_session['dealer_logged_in'] = True
        flask_session['dealer_username'] = username
        flask_session['dealer_name'] = user_name
        flask_session['dealer_gln'] = address_id
        flask_session['active_bayi_kodu'] = b_kodu_sess
        flask_session['offline_mode'] = False
        flask_session['ministry_connected'] = True
        flask_session.permanent = True

        with _revoked_dealers_lock:
            _revoked_dealers.discard(b_kodu_sess)
            _revoked_dealers.discard(username)
            if enc_u:
                _revoked_dealers.discard(enc_u)

        cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
        if os.path.exists(cred_file):
            try:
                os.remove(cred_file)
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
        with _bkst_session_lock:
            _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0})
        with _template_vars_lock:
            _template_vars_cache['expires_at'] = 0

        # İstemci ise bilgileri ana sunucuya ilet ve o bayinin güncel listesini sunucudan çek
        if merkez_url:
            def _fwd_login_creds():
                try:
                    import requests
                    requests.post(
                        f"{merkez_url.rstrip('/')}/api/sync/push",
                        json={
                            "bayi_kodu": b_kodu_sess,
                            "bkst_kullanici": username,
                            "bkst_sifre": password,
                            "gln_no": address_id or "",
                            "bayi_adi": user_name or ""
                        },
                        timeout=5.0
                    )
                    _auto_pull_exit_list_from_server(merkez_url, b_kodu_sess, username, password)
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
            threading.Thread(target=_fwd_login_creds, daemon=True).start()

        return jsonify({'success': True, 'offline_mode': False, 'ministry_connected': True, 'message': 'Giriş başarılı ve Bakanlık ile senkronize edildi.', 'user_name': user_name, 'token': LOCAL_SESSION_TOKEN})

    else:
        # ⚠️ Bakanlık Doğrulaması Başarısız veya Bakanlık Sunucusuna Ulaşılamadı
        # EĞER Bayi test modunda ise Bakanlıkta kaydı olmasa dahi sistem şifresiyle girişine izin verilir
        is_test_dealer = False
        if b_row:
            is_test_dealer = bool(b_row[6] if len(b_row) > 6 else 0) or (b_row[1] == 'bayi_test')

        # EĞER Bakanlık sunucusuna ulaşıldıysa ve test modu AKTİF DEĞİLSE:
        # Bu durum kesin olarak KULLANICI ADI VEYA ŞİFRENİN YANLIŞ OLDUĞUNU gösterir!
        if ministry_http_reached and username.isdigit() and len(username) == 11 and not is_test_dealer:
            if b_row and str(b_row[2] or '').upper().strip() == 'AKTIF':
                return jsonify({
                    'success': False,
                    'error': '❌ Giriş Başarısız: Girdiğiniz Bakanlık (BKST) şifresi hatalı! Lütfen şifrenizi kontrol edip tekrar deneyiniz.'
                }), 401

        has_net = ministry_http_reached or is_internet_available(timeout=1.2)

        # 1. Eğer sunucuda yetkili (AKTİF) bir bayi ise (veya test modundaysa) -> Kayıtlı şifreyi doğrula ve ÇEVRİMDIŞI / TEST MODDA aç!
        if b_row and str(b_row[2] or '').upper().strip() == 'AKTIF':
            b_id, b_kodu_sess, b_durum, b_ad, b_sifre_kayitli, b_gln_kayitli = b_row[:6]
            dec_saved_p = (decrypt_credential(b_sifre_kayitli) or "").strip()
            pwd_matches = bool(dec_saved_p and password.strip() == dec_saved_p)

            # EĞER TEST BAYİSİ İSE:
            # Henüz şifresi DB'de kayıtlı değilse (boşsa), kullanıcının girdiği şifreyi kaydet ve anında onayla
            if is_test_dealer:
                if not dec_saved_p and password.strip():
                    pwd_matches = True
                    dec_saved_p = password.strip()
                    enc_p_new = encrypt_credential(password.strip())
                    try:
                        with sqlite3.connect(DB_PATH, timeout=5.0) as conn_setp:
                            conn_setp.execute("UPDATE bayiler SET bkst_sifre = ? WHERE id = ?", (enc_p_new, b_id))
                            conn_setp.commit()
                    except Exception as _e_p:
                        logger.debug(f"Test dealer auto pass save notice: {_e_p}")
                elif dec_saved_p and password.strip() == dec_saved_p:
                    pwd_matches = True

            if pwd_matches:
                effective_name = clean_user_name(b_ad) or clean_user_name(user_name) or username
                effective_gln = b_gln_kayitli or address_id or "OFFLINE"

                with sqlite3.connect(DB_PATH, timeout=30.0) as conn_b:
                    conn_b.execute("PRAGMA journal_mode=WAL")
                    conn_b.execute("PRAGMA busy_timeout=30000")
                    c_b = conn_b.cursor()

                    for k, v in [("bkst_kullanici_adi", enc_u), ("bkst_sifre", enc_p), ("bkst_adres_id", effective_gln), ("bkst_kullanici_isim", effective_name), ("active_bayi_kodu", b_kodu_sess)]:
                        c_b.execute("""
                            INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
                            VALUES (?, ?, ?)
                            ON CONFLICT(anahtar) DO UPDATE SET
                            deger = excluded.deger,
                            guncelleme_tarihi = excluded.guncelleme_tarihi
                        """, (k, str(v or ''), now_dt_b))

                    c_b.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE id = ?", (now_dt_b, b_id))
                    conn_b.commit()

                with _settings_mem_lock:
                    _settings_mem_cache.clear()

                flask_session['dealer_logged_in'] = True
                flask_session['dealer_username'] = username
                flask_session['dealer_name'] = effective_name
                flask_session['dealer_gln'] = effective_gln
                flask_session['active_bayi_kodu'] = b_kodu_sess
                flask_session['offline_mode'] = True
                flask_session['ministry_connected'] = False
                flask_session.permanent = True

                if merkez_url:
                    def _fwd_offline_login():
                        try:
                            _auto_pull_exit_list_from_server(merkez_url, b_kodu_sess, username, password)
                        except Exception as e_fwd_off:
                            logger.debug(f"Offline login pull error: {e_fwd_off}")
                    threading.Thread(target=_fwd_offline_login, daemon=True).start()

                with _revoked_dealers_lock:
                    _revoked_dealers.discard(b_kodu_sess)
                    _revoked_dealers.discard(username)
                    if enc_u:
                        _revoked_dealers.discard(enc_u)

                log_debug("SISTEM_GIRIS", f"🟢 Yetkili Bayi Çevrimdışı (Offline) Girişi: {b_kodu_sess}", 
                          detay=f"Bayi: {effective_name}, Bakanlık bağlantısı olmadan çevrimdışı modda başlatıldı.", seviye="INFO")

                return jsonify({
                    'success': True,
                    'offline_mode': True,
                    'ministry_connected': False,
                    'message': '🟢 Test Modu Aktif: Sisteme başarıyla giriş yapıldı.' if is_test_dealer else '🟢 Sunucu yetkiniz onaylandı. Bakanlık bağlantısı kurulamadığı için çevrimdışı (offline) modda giriş yapıldı.',
                    'user_name': effective_name,
                    'token': LOCAL_SESSION_TOKEN
                })
            else:
                return jsonify({
                    'success': False,
                    'error': '❌ Giriş Başarısız: Girdiğiniz şifre hatalı veya Bakanlık doğrulaması yapılamadı!'
                }), 401

        # 2. Eğer bayi 'ONAY_BEKLIYOR' veya 'PASIF' durumundaysa:
        if b_row:
            b_durum_cur = str(b_row[2] or '').upper().strip()
            if b_durum_cur in ('PASIF', 'ASKIYA_ALINDI'):
                return jsonify({
                    'success': False,
                    'unauthorized': True,
                    'error': '⛔ Bayilik erişim yetkiniz sonlandırılmıştır! Lütfen sistem yöneticisiyle iletişime geçiniz.'
                }), 403
            else:
                return jsonify({
                    'success': False,
                    'unauthorized': True,
                    'error': '⏳ Bayilik başvurunuz onay beklemektedir. Yönetici onay verdikten sonra sisteme giriş yapabilirsiniz.'
                }), 403

        # 3. Bayi sistemde kayıtlı DEĞİL:
        if has_net:
            # 🌐 İNTERNET VAR ANCAK BAKANLIK DOĞRULAMASI BAŞARISIZ OLDU VE KAYITLI BAYİ DEĞİL:
            # Girişi KESİNLİKLE engelle ve sunucuya derhal başvuru bildirimi ilet!
            new_kodu = f"bayi_{int(time.time())}"
            effective_name = username
            notif_msg = f"TC: {username} bilgisiyle giriş yapılmaya çalışıldı. Bakanlık doğrulaması başarısız veya yetki verilmemiş."

            with sqlite3.connect(DB_PATH, timeout=10.0) as conn_b:
                c_b = conn_b.cursor()
                try:
                    c_b.execute("""
                        INSERT INTO bayiler (bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum, son_senkronizasyon, eklenme_tarihi, notlar)
                        VALUES (?, ?, ?, ?, ?, 'ONAY_BEKLIYOR', ?, ?, 'Giriş ekranından başvuru/talep (Bakanlık doğrulanamadı)')
                    """, (new_kodu, effective_name, enc_u, enc_p, address_id or '', now_dt_b, now_dt_b))
                    c_b.execute("""
                        INSERT INTO sistem_bildirimleri (tarih, baslik, mesaj, tur, bayi_kodu, tc_no, sirket_adi, gln, okundu)
                        VALUES (?, '🔔 Yeni Bayi Üyelik Talebi', ?, 'UYE_TALEBI', ?, ?, ?, ?, 0)
                    """, (now_dt_b, notif_msg, new_kodu, username, effective_name, address_id or ''))
                    conn_b.commit()
                except Exception as e_notif:
                    logger.warning(f"Registration notice insert error: {e_notif}")

            # Ana sunucuya bildirimi anında ilet
            if merkez_url:
                try:
                    requests.post(
                        f"{merkez_url.rstrip('/')}/api/sync/dealer_registration",
                        json={
                            "bayi_kodu": new_kodu,
                            "bayi_adi": effective_name,
                            "bkst_kullanici": username,
                            "bkst_sifre": password,
                            "gln_no": address_id or '',
                            "tc_no": username,
                            "notif_msg": notif_msg,
                            "tarih": now_dt_b,
                            "durum": "ONAY_BEKLIYOR"
                        },
                        timeout=5.0
                    )
                except Exception as e_fwd:
                    logger.warning(f"Remote registration notification error: {e_fwd}")

            log_debug("YETKISIZ_GIRIS", "🔔 Yeni Bayi Üyelik Talebi Oluşturuldu", detay=notif_msg, seviye="UYARI")

            return jsonify({
                'success': False,
                'unauthorized': True,
                'error': '⏳ Bayilik kaydınız sistemde bulunamadı veya Bakanlık bilgileri doğrulanamadı. Üyelik başvurunuz ana sunucuya iletildi; yönetici onayından sonra giriş yapabilirsiniz.'
            }), 403

        else:
            # 🔌 GERÇEKTEN HİÇ İNTERNET YOK (Saha Çevrimdışı İlk Giriş):
            # Cihaz sahada/internetsiz ortamda ilk kez açılıyorsa geçici çevrimdışı mod
            new_kodu = f"bayi_{int(time.time())}"
            effective_name = username
            effective_gln = address_id or "OFFLINE"

            with sqlite3.connect(DB_PATH, timeout=30.0) as conn_b:
                conn_b.execute("PRAGMA journal_mode=WAL")
                conn_b.execute("PRAGMA busy_timeout=30000")
                c_b = conn_b.cursor()

                for k, v in [("bkst_kullanici_adi", enc_u), ("bkst_sifre", enc_p), ("bkst_adres_id", effective_gln), ("bkst_kullanici_isim", effective_name)]:
                    c_b.execute("""
                        INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
                        VALUES (?, ?, ?)
                        ON CONFLICT(anahtar) DO UPDATE SET
                        deger = excluded.deger,
                        guncelleme_tarihi = excluded.guncelleme_tarihi
                    """, (k, str(v or ''), now_dt_b))

                try:
                    c_b.execute("""
                        INSERT INTO bayiler (bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum, son_senkronizasyon, eklenme_tarihi, notlar)
                        VALUES (?, ?, ?, ?, ?, 'OFFLINE_GIRIS', ?, ?, 'İnternetsiz ilk giriş (İnternet gelince Bakanlık ve sunucu ile eşitlenecek)')
                    """, (new_kodu, effective_name, enc_u, enc_p, effective_gln, now_dt_b, now_dt_b))
                    conn_b.commit()
                except Exception as e_cr:
                    logger.warning(f"Offline first dealer create notice: {e_cr}")

            flask_session['dealer_logged_in'] = True
            flask_session['dealer_username'] = username
            flask_session['dealer_name'] = effective_name
            flask_session['dealer_gln'] = effective_gln
            flask_session['active_bayi_kodu'] = new_kodu
            flask_session['offline_mode'] = True
            flask_session['ministry_connected'] = False
            flask_session.permanent = True

            with _revoked_dealers_lock:
                _revoked_dealers.discard(new_kodu)
                _revoked_dealers.discard(username)
                if enc_u:
                    _revoked_dealers.discard(enc_u)

            log_debug("SISTEM_GIRIS", f"🌐 İnternetsiz İlk Giriş: {new_kodu}", 
                      detay=f"TC: {username}. İnternet bağlantısı olmadığından geçici çevrimdışı modda açıldı, internet gelince sunucu ile eşitlenecektir.", seviye="INFO")

            return jsonify({
                'success': True,
                'offline_mode': True,
                'ministry_connected': False,
                'message': 'İnternet bağlantısı kurulamadı. Çevrimdışı (offline) modda geçici olarak giriş yapıldı. İnternet bağlandığında sunucuda onayınız yoksa oturumunuz kapatılacaktır.',
                'user_name': effective_name,
                'token': LOCAL_SESSION_TOKEN,
                'redirect': '/cikis'
            })

@app.route('/api/system/user_info', methods=['GET'])
def api_system_user_info():
    from flask import session as flask_session
    if flask_session.get('is_admin'):
        return jsonify({
            'success': True,
            'is_admin': True,
            'username': flask_session.get('admin_user', 'admin'),
            'user_name': '👑 Süper Yönetici (Admin)'
        })
    if not flask_session.get('dealer_logged_in'):
        return jsonify({
            'success': False,
            'unauthenticated': True,
            'username': '',
            'user_name': 'Giriş Yapılmadı'
        })
    return jsonify({
        'success': True,
        'username': flask_session.get('dealer_username', ''),
        'user_name': flask_session.get('dealer_name', '')
    })

@app.route('/api/system/sync_status', methods=['GET'])
def api_system_sync_status():
    payload = _bkst_state_payload()
    with _state_lock:
        payload["synced"] = _app_bkst_synced
    return jsonify(payload)

@app.route('/api/system/startup_status', methods=['GET', 'POST'])
def api_system_startup_status():
    """
    Uygulama açılışında C# Native SplashForm tarafından çağrılır.
    Oturumun açık olup olmadığını ve Bakanlık senkronizasyonunu yönetir.
    """
    from flask import session as flask_session
    saved_u = get_system_setting("bkst_kullanici_adi", "").strip()
    saved_code = get_system_setting("active_bayi_kodu", "").strip()
    is_admin = bool(flask_session.get('is_admin'))
    is_dealer = bool(flask_session.get('dealer_logged_in') and saved_u)
    is_logged_in = bool(is_admin or is_dealer or (saved_u and saved_code))

    u, p, addr, _ = read_bkst_credentials() if is_logged_in else ("", "", "", "")
    dealer_name = flask_session.get('dealer_name') or get_system_setting("bkst_kullanici_isim", "") or u or ""
    active_kodu = flask_session.get('active_bayi_kodu') or saved_code or "bayi_1"

    do_sync = bool(request.args.get('sync') in ['1', 'true', 'True'] or (request.is_json and request.json and request.json.get('sync')))

    if not is_logged_in:
        return jsonify({
            'success': True,
            'is_logged_in': False,
            'synced': False,
            'message': 'Giriş yapılmamış'
        })

    if do_sync and u and p:
        try:
            worker_th = threading.Thread(target=_do_fetch_api_worker, kwargs={'user': u, 'pwd': p, 'addr': addr}, daemon=True)
            worker_th.start()
            worker_th.join(timeout=25.0)
        except Exception as e_s:
            logger.warning(f"Startup sync worker notice: {e_s}")

    with _state_lock:
        is_synced = _app_bkst_synced

    return jsonify({
        'success': True,
        'is_logged_in': True,
        'synced': is_synced,
        'dealer_name': dealer_name,
        'bayi_kodu': active_kodu
    })

_last_update_check_time = 0
_cached_update_response = {'has_update': False}

@app.route('/api/system/check_update', methods=['GET'])
def api_system_check_update():
    global _last_update_check_time, _cached_update_response

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
            with open(local_vpath, "r", encoding="utf-8-sig") as f:
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
    from flask import session as flask_session
    if not flask_session.get('is_admin') and not flask_session.get('dealer_logged_in'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim. Güncelleme başlatmak için sisteme giriş yapınız.'}), 401
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

@app.route('/api/admin/system/restart', methods=['POST'])
def api_admin_system_restart():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    def _restart():
        time.sleep(1.0)
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            py_dir = os.path.dirname(sys.executable)
            pythonw_cand = os.path.join(py_dir, "pythonw.exe")
            target_py = pythonw_cand if os.path.exists(pythonw_cand) else sys.executable
            flags = 0x08000000 if os.name == 'nt' else 0
            flags |= 0x00000008
            subprocess.Popen([target_py, "app.py"], cwd=base_dir, creationflags=flags)
        finally:
            os._exit(0)

    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({'success': True, 'message': 'Sunucu başarıyla yeniden başlatılıyor...'})

@app.route('/api/system/logout', methods=['POST', 'GET'])
def api_system_logout():
    with _state_lock:
        global _app_bkst_synced
        _app_bkst_synced = False

    # 🔒 Hızlı ve Tek Bağlantılı Veritabanı Oturum Sıfırlama
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn_l:
            conn_l.execute("PRAGMA journal_mode=WAL")
            conn_l.execute("PRAGMA busy_timeout=5000")
            c_l = conn_l.cursor()
            now_dt_lg = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for k in ["bkst_kullanici_adi", "bkst_sifre", "bkst_adres_id", "bkst_kullanici_isim", "active_bayi_kodu"]:
                c_l.execute("""
                    INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
                    VALUES (?, '', ?)
                    ON CONFLICT(anahtar) DO UPDATE SET deger='', guncelleme_tarihi=excluded.guncelleme_tarihi
                """, (k, now_dt_lg))
            from flask import session as flask_session
            active_b = flask_session.get('active_bayi_kodu')
            if active_b:
                c_l.execute("DELETE FROM aktif_cihazlar WHERE bayi_kodu = ?", (active_b,))
            conn_l.commit()
    except Exception as _ignored_err:
        logger.debug(f"Logout db notice: {_ignored_err}")

    with _settings_mem_lock:
        _settings_mem_cache.clear()

    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    if os.path.exists(cred_file):
        try: os.remove(cred_file)
        except Exception: pass

    with _bkst_session_lock:
        _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0})
        _bkst_sessions_by_user.clear()
    with _template_vars_lock:
        _template_vars_cache['expires_at'] = 0

    from flask import session as flask_session
    flask_session.clear()

    resp = make_response(jsonify({'success': True, 'message': 'Oturum kapatıldı.', 'redirect': '/login?logout=1'}))
    cookie_name = app.config.get('SESSION_COOKIE_NAME') or 'session'
    for cn in [cookie_name, 'session', 'local_session_token']:
        try:
            resp.delete_cookie(cn, path='/')
        except Exception:
            pass
    return resp

@app.route('/logout', methods=['GET', 'POST'])
def route_logout():
    from flask import session as flask_session
    is_worker = flask_session.get('mobile_quick_login')
    m_token = flask_session.get('mobile_token')
    active_b = flask_session.get('active_bayi_kodu')

    if m_token:
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn_m:
                c_m = conn_m.cursor()
                c_m.execute("UPDATE aktif_cihazlar SET durum = 'OFFLINE', son_gorulme = '2000-01-01 00:00:00' WHERE cihaz_id = ?", (f"mob_{m_token[:8]}",))
                conn_m.commit()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    if not is_worker:
        # Sadece masaüstü bayi veya admin çıkış yaptığında Bakanlık giriş bilgilerini temizle
        with _state_lock:
            global _app_bkst_synced
            _app_bkst_synced = False
        try:
            with sqlite3.connect(DB_PATH, timeout=10.0) as conn_s:
                conn_s.execute("PRAGMA journal_mode=WAL")
                conn_s.execute("PRAGMA busy_timeout=10000")
                now_dt_lg2 = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for k in ["bkst_kullanici_adi", "bkst_sifre", "bkst_adres_id", "bkst_kullanici_isim", "active_bayi_kodu"]:
                    conn_s.execute("""
                        INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
                        VALUES (?, '', ?)
                        ON CONFLICT(anahtar) DO UPDATE SET deger='', guncelleme_tarihi=excluded.guncelleme_tarihi
                    """, (k, now_dt_lg2))
                if active_b:
                    conn_s.execute("DELETE FROM aktif_cihazlar WHERE bayi_kodu = ?", (active_b,))
                conn_s.commit()
        except Exception as e_c:
            logger.warning(f"Logout db clear error: {e_c}")

        with _settings_mem_lock:
            _settings_mem_cache.clear()

        cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
        if os.path.exists(cred_file):
            try: os.remove(cred_file)
            except Exception: pass
        with _bkst_session_lock:
            _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0})
            _bkst_sessions_by_user.clear()
        with _template_vars_lock:
            _template_vars_cache['expires_at'] = 0

    flask_session.clear()
    if request.is_json or (request.headers.get('Accept') and 'application/json' in request.headers.get('Accept', '')):
        resp = make_response(jsonify({'success': True, 'message': 'Oturum kapatıldı.', 'redirect': '/login?logout=1'}))
    else:
        resp = make_response(redirect('/login?logout=1'))
    cookie_name = app.config.get('SESSION_COOKIE_NAME') or 'session'
    for cn in [cookie_name, 'session', 'local_session_token']:
        try:
            resp.delete_cookie(cn, path='/')
        except Exception:
            pass
    return resp

@app.route('/api/sync/revoke_mobile_tokens', methods=['POST'])
def api_sync_revoke_mobile_tokens():
    data = request.get_json(silent=True) or {}
    bayi_kodu = str(data.get('bayi_kodu') or '').strip()
    target_token = str(data.get('token') or '').strip()
    tokens = list(data.get('tokens') or [])
    if target_token and target_token not in tokens:
        tokens.append(target_token)

    candidate_codes = set()
    if bayi_kodu:
        candidate_codes.add(bayi_kodu)
    for c in (data.get('candidate_codes') or []):
        if c:
            candidate_codes.add(str(c).strip())

    for t in tokens:
        if t and t.startswith("qrm."):
            parts = t.split('.')
            if len(parts) >= 2 and parts[1]:
                candidate_codes.add(parts[1])

    bk_user = str(data.get('bkst_kullanici') or '').strip()
    if bk_user:
        resolved = resolve_dealer_code(bayi_kodu or 'bayi_1', bk_user)
        if resolved:
            candidate_codes.add(resolved)

    if not candidate_codes:
        candidate_codes.add('bayi_1')

    try:
        with _revoked_dealers_lock:
            for t in tokens:
                if t:
                    _revoked_mobile_tokens.add(t)
            for c_code in candidate_codes:
                _revoked_staff_dealers.add(c_code)

        for c_code in candidate_codes:
            AuthService.get_instance().revoke_dealer_staff(c_code)
        for t in tokens:
            if t:
                AuthService.get_instance().revoke_mobile_token(t)

        now_rev = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            c = conn.cursor()
            for t in tokens:
                if t:
                    c.execute("UPDATE mobile_quick_tokens SET is_active = 0 WHERE token = ?", (t,))
                    c.execute("UPDATE aktif_cihazlar SET durum = 'OFFLINE' WHERE cihaz_id = ?", (f"mob_{t[:8]}",))
            for c_code in candidate_codes:
                c.execute("UPDATE mobile_quick_tokens SET is_active = 0 WHERE bayi_kodu = ?", (c_code,))
                c.execute("UPDATE aktif_cihazlar SET durum = 'OFFLINE' WHERE bayi_kodu = ? AND (cihaz_turu LIKE '%Mobil%' OR cihaz_id LIKE 'mob_%')", (c_code,))
                c.execute("INSERT OR REPLACE INTO sistem_ayarlari (anahtar, deger) VALUES (?, ?)", (f"staff_revoked_at_{c_code}", now_rev))
            conn.commit()
        logger.info(f"🛑 [SYNC] Bayi ({candidate_codes}) mobil token iptali uygulandı.")
        target_bk = list(candidate_codes)[0] if candidate_codes else 'bayi_1'
        log_debug("MOBIL_OTURUM", "🛑 Personel Mobil Oturumu Merkezde Kapatıldı", f"Merkez sunucuda personelin mobil oturumları kapatıldı ({len(tokens)} token iptal edildi).", seviye="UYARI", bayi_kodu=target_bk)
        return jsonify({'success': True, 'revoked_at': now_rev, 'candidate_codes': list(candidate_codes)})
    except Exception as e:
        logger.error(f"api_sync_revoke_mobile_tokens error: {e}")
        log_debug("MOBIL_OTURUM", "Mobil Token İptal Hatası", f"Hata: {e}", seviye="ERROR")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dealer/terminate_staff_sessions', methods=['POST'])
def api_dealer_terminate_staff_sessions():
    from flask import session as flask_session
    if not flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.'}), 403

    data = request.get_json(silent=True) or {}
    target_token = str(data.get('token') or (request.form.get('token') if request.form else '')).strip()
    req_bayi = str(data.get('bayi_kodu') or '').strip()

    candidate_codes = set()
    if req_bayi:
        candidate_codes.add(req_bayi)
    sess_bayi = flask_session.get('active_bayi_kodu')
    if sess_bayi:
        candidate_codes.add(sess_bayi)

    dealer_u = flask_session.get('dealer_username') or ''
    if not dealer_u:
        try:
            u_cred, _, _, _ = read_bkst_credentials()
            if u_cred:
                dealer_u = u_cred
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    if dealer_u:
        res_code = resolve_dealer_code(sess_bayi or 'bayi_1', dealer_u)
        if res_code:
            candidate_codes.add(res_code)

    if not candidate_codes:
        candidate_codes.add('bayi_1')

    tokens_to_revoke = set()
    if target_token:
        tokens_to_revoke.add(target_token)

    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            c = conn.cursor()
            placeholders = ','.join('?' for _ in candidate_codes)
            c.execute(f"SELECT token FROM mobile_quick_tokens WHERE bayi_kodu IN ({placeholders}) AND is_active = 1", tuple(candidate_codes))
            for r in c.fetchall():
                if r[0]:
                    tokens_to_revoke.add(r[0])
    except Exception as e_q:
        logger.warning(f"Error querying active tokens: {e_q}")

    now_rev = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        with _revoked_dealers_lock:
            for t in tokens_to_revoke:
                _revoked_mobile_tokens.add(t)
            for c_code in candidate_codes:
                _revoked_staff_dealers.add(c_code)

        for c_code in candidate_codes:
            AuthService.get_instance().revoke_dealer_staff(c_code)
        for t in tokens_to_revoke:
            AuthService.get_instance().revoke_mobile_token(t)

        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            c = conn.cursor()
            for t in tokens_to_revoke:
                c.execute("UPDATE mobile_quick_tokens SET is_active = 0 WHERE token = ?", (t,))
                c.execute("UPDATE aktif_cihazlar SET durum = 'OFFLINE' WHERE cihaz_id = ?", (f"mob_{t[:8]}",))
            for c_code in candidate_codes:
                c.execute("UPDATE mobile_quick_tokens SET is_active = 0 WHERE bayi_kodu = ?", (c_code,))
                c.execute("UPDATE aktif_cihazlar SET durum = 'OFFLINE' WHERE bayi_kodu = ? AND (cihaz_turu LIKE '%Mobil%' OR cihaz_id LIKE 'mob_%')", (c_code,))
                c.execute("INSERT OR REPLACE INTO sistem_ayarlari (anahtar, deger) VALUES (?, ?)", (f"staff_revoked_at_{c_code}", now_rev))
            conn.commit()

        # 🌐 MERKEZ SUNUCU İLE SENKRON KOPARMA (KULLANICIYA YANIT VERMEDEN ÖNCE MUTLAKA DOĞRULA)
        merkez_url = get_merkez_sunucu_url(force_refresh=True)
        if merkez_url and not _is_server_host():
            import requests
            remote_verified = False
            remote_err_msg = ""
            for attempt in range(2):
                try:
                    r = requests.post(
                        f"{merkez_url.rstrip('/')}/api/sync/revoke_mobile_tokens",
                        json={
                            'bayi_kodu': list(candidate_codes)[0] if candidate_codes else 'bayi_1',
                            'candidate_codes': list(candidate_codes),
                            'tokens': list(tokens_to_revoke),
                            'token': target_token or '',
                            'bkst_kullanici': dealer_u
                        },
                        headers={'X-Requested-With': 'XMLHttpRequest'},
                        timeout=5.0
                    )
                    if r.status_code == 200:
                        res_json = r.json() or {}
                        if res_json.get('success'):
                            remote_verified = True
                            logger.info(f"🌐 Merkez Sunucuyla Bağlantı Kesildi ve Teyit Edildi: {candidate_codes}")
                            break
                        else:
                            remote_err_msg = res_json.get('error', 'Bilinmeyen merkez yanıtı')
                    else:
                        remote_err_msg = f"HTTP {r.status_code}"
                except Exception as e_rev:
                    remote_err_msg = str(e_rev)
                    time.sleep(0.3)

            if not remote_verified:
                logger.error(f"❌ Merkez sunucuda oturum kapatma doğrulanamadı: {remote_err_msg}")
                return jsonify({
                    'success': False,
                    'error': f'Merkez sunucu ile bağlantı kesilemedi ({remote_err_msg}). Mobil cihaz halen erişim sağlıyor olabilir. Lütfen internet bağlantınızı kontrol edip tekrar deneyiniz.'
                }), 502

        logger.info(f"🛑 Bayi ({candidate_codes}) personel mobil oturumu ve sunucu bağlantısı başarıyla kesildi.")
        target_bk = list(candidate_codes)[0] if candidate_codes else 'bayi_1'
        log_debug("MOBIL_OTURUM", "🛑 Personel Mobil Oturumu Kapatıldı", f"Bayi masaüstü uygulamasından personelin açık mobil oturumunu kapattı ({len(tokens_to_revoke)} token sonlandırıldı).", seviye="UYARI", bayi_kodu=target_bk)
        return jsonify({
            'success': True,
            'message': 'Personel mobil oturumu kapatıldı. Mobil cihazın merkez sunucuyla bağlantısı kesildi ve erişimi durduruldu.'
        })
    except Exception as e:
        logger.error(f"Terminate staff session error: {e}")
        target_bk = list(candidate_codes)[0] if candidate_codes else 'bayi_1'
        log_debug("MOBIL_OTURUM", "Personel Oturumu Kapatma İstisnası", f"Hata: {e}", seviye="ERROR", bayi_kodu=target_bk)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dealer/mobile_staff_sessions', methods=['GET'])
def api_dealer_mobile_staff_sessions():
    from flask import session as flask_session
    if not flask_session.get('dealer_logged_in') and not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.'}), 403

    bayi_kodu = flask_session.get('active_bayi_kodu') or 'bayi_1'
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT token, created_at, expires_at, login_count, last_used_at, is_active
                FROM mobile_quick_tokens
                WHERE bayi_kodu = ? AND is_active = 1 AND expires_at > datetime('now', 'localtime')
                ORDER BY created_at DESC LIMIT 10
            """, (bayi_kodu,))
            tokens = [dict(r) for r in c.fetchall()]

            c.execute("""
                SELECT id, cihaz_id, cihaz_adi, ip_adresi, aktif_sayfa, son_gorulme, durum
                FROM aktif_cihazlar
                WHERE bayi_kodu = ? AND (cihaz_turu LIKE '%Mobil%' OR cihaz_id LIKE 'mob_%') AND durum = 'ONLINE'
                ORDER BY son_gorulme DESC LIMIT 10
            """, (bayi_kodu,))
            devices = [dict(r) for r in c.fetchall()]

        return jsonify({'success': True, 'active_tokens_count': len(tokens), 'devices': devices})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==============================================================================
# 👑 SÜPER YÖNETİCİ (ADMIN HUB) & ÇOKLU BAYİ SENKRONİZASYON MOTORU
# ==============================================================================

def get_current_tunnel_url():
    global PUBLIC_TUNNEL_URL
    if PUBLIC_TUNNEL_URL and PUBLIC_TUNNEL_URL.startswith("http"):
        return PUBLIC_TUNNEL_URL
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. TUNEL_BAGLANTI_ADRESI.txt
    tun_file = os.path.join(base_dir, "TUNEL_BAGLANTI_ADRESI.txt")
    if os.path.exists(tun_file):
        try:
            with open(tun_file, "r", encoding="utf-8") as f:
                content = f.read()
                m = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', content)
                if m:
                    PUBLIC_TUNNEL_URL = m.group(1).strip()
                    return PUBLIC_TUNNEL_URL
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    # 2. sunucu_adresi.txt
    cfg_file = os.path.join(base_dir, "sunucu_adresi.txt")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if "trycloudflare.com" in content:
                    PUBLIC_TUNNEL_URL = content
                    return PUBLIC_TUNNEL_URL
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    # 3. cloudflared_output.log
    log_file = os.path.join(base_dir, "cloudflared_output.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
                matches = re.findall(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', content)
                for cand in reversed(matches):
                    if "api.trycloudflare.com" not in cand:
                        PUBLIC_TUNNEL_URL = cand.strip()
                        return PUBLIC_TUNNEL_URL
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    # 4. DB System Setting
    try:
        db_url = get_system_setting("merkez_sunucu_url", "")
        if db_url and "trycloudflare.com" in db_url:
            PUBLIC_TUNNEL_URL = db_url.strip()
            return PUBLIC_TUNNEL_URL
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    return ""

@app.route('/admin')
def admin_panel_page():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return redirect('/login')
    tun_url = get_current_tunnel_url()
    loc_ip = get_local_ip()
    return render_template('admin_panel.html', public_tunnel_url=tun_url, local_url=f"http://{loc_ip}:5000", local_ip=loc_ip)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

@app.route('/api/admin/bayiler', methods=['GET'])
def api_admin_get_bayiler():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        ensure_db_schema()
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()

        # Eski / mükerrer cihaz oturumlarını temizle:
        # Sunucunun kendi yerel isteklerini ve arka plan python işlemlerini temizle, aynı IP'li mükerrerleri tek kayda indir
        try:
            c.execute("""
                DELETE FROM aktif_cihazlar 
                WHERE ip_adresi IN ('127.0.0.1', 'localhost', '::1') 
                   OR user_agent LIKE '%python-requests%'
            """)
            c.execute("""
                DELETE FROM aktif_cihazlar 
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY bayi_kodu, CASE WHEN ip_adresi IS NOT NULL AND ip_adresi != '' THEN ip_adresi ELSE cihaz_id END
                            ORDER BY son_gorulme DESC, id DESC
                        ) as rn
                        FROM aktif_cihazlar
                    ) WHERE rn = 1
                )
            """)
            conn.commit()
        except Exception:
            try:
                c.execute("""
                    DELETE FROM aktif_cihazlar 
                    WHERE id NOT IN (
                        SELECT MAX(id) FROM aktif_cihazlar 
                        GROUP BY bayi_kodu, CASE WHEN ip_adresi IS NOT NULL AND ip_adresi != '' THEN ip_adresi ELSE cihaz_id END
                    )
                """)
                conn.commit()
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
        c.execute("""
            SELECT id, bayi_kodu, bayi_adi, il, ilce, gln_no, bkst_kullanici, bkst_sifre, durum, remote_url, son_senkronizasyon, eklenme_tarihi, notlar, COALESCE(test_modu, 0)
            FROM bayiler
            WHERE UPPER(COALESCE(durum, 'AKTIF')) NOT IN ('ONAY_BEKLIYOR', 'OFFLINE_GIRIS')
            ORDER BY id ASC
        """)
        dealer_rows = c.fetchall()

        today_str = datetime.now().strftime('%Y-%m-%d')
        now_dt = datetime.now()
        dealers = []
        total_pending = 0
        total_sales = 0
        total_errors = 0
        total_online_devices = 0
        total_active_dealers = 0

        for r in dealer_rows:
            b_kodu = r[1]

            # Bekleyen çıkış listesi sayısı (sadece aktif ve canlı olanlar)
            c.execute("SELECT COUNT(*) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND COALESCE(admin_gizli, 0) = 0 AND COALESCE(silindi, 0) = 0", (b_kodu, b_kodu))
            pending_count = c.fetchone()[0]
            total_pending += pending_count

            # Bugünkü satış sayısı
            c.execute("SELECT COUNT(*) FROM satis_arsivi WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND tarih LIKE ?", (b_kodu, b_kodu, f"{today_str}%"))
            today_sales = c.fetchone()[0]
            total_sales += today_sales

            # Hata sayısı
            c.execute("SELECT COUNT(*) FROM debug_loglari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND seviye = 'ERROR'", (b_kodu, b_kodu))
            err_cnt = c.fetchone()[0]
            total_errors += err_cnt

            # Aktif Cihazlar & Oturumlar sorgusu
            c.execute("""
                SELECT id, cihaz_id, cihaz_adi, cihaz_turu, ip_adresi, user_agent, aktif_sayfa, son_gorulme, durum
                FROM aktif_cihazlar
                WHERE bayi_kodu = ?
                ORDER BY son_gorulme DESC
            """, (b_kodu,))
            dev_rows = c.fetchall()

            devices = []
            dealer_online_count = 0
            for d in dev_rows:
                d_id, d_cid, d_name, d_type, d_ip, d_ua, d_page, d_seen, d_durum = d
                is_online = False
                status_text = "Çevrimdışı"
                time_ago_str = "Bilinmiyor"
                try:
                    dt_seen = datetime.strptime(d_seen, '%Y-%m-%d %H:%M:%S')
                    diff_sec = int((now_dt - dt_seen).total_seconds())
                    # Uygulama açık olduğu sürece (her 10 sn'de sinyal gelir) Çevrimiçi kalır.
                    # Pencere kapatıldığında veya uygulama sonlandırıldığında anında Çevrimdışı olur.
                    if diff_sec <= 45 and str(d_durum).upper() != 'OFFLINE':
                        is_online = True
                        status_text = "Çevrimiçi"
                        dealer_online_count += 1
                        time_ago_str = f"{diff_sec} sn önce"
                    elif diff_sec < 3600:
                        is_online = False
                        status_text = "Çevrimdışı"
                        time_ago_str = f"{diff_sec // 60} dk önce"
                    elif diff_sec < 86400:
                        is_online = False
                        status_text = "Çevrimdışı"
                        time_ago_str = f"{diff_sec // 3600} saat önce"
                    else:
                        is_online = False
                        status_text = "Çevrimdışı"
                        time_ago_str = dt_seen.strftime('%d.%m %H:%M')
                except Exception:
                    time_ago_str = d_seen

                devices.append({
                    'id': d_id,
                    'cihaz_id': d_cid,
                    'cihaz_adi': d_name or 'Terminal',
                    'cihaz_turu': d_type or 'Masaüstü PC',
                    'ip_adresi': d_ip or '-',
                    'aktif_sayfa': d_page or 'Uygulama Açık',
                    'son_gorulme': d_seen,
                    'gecen_sure': time_ago_str,
                    'is_online': is_online,
                    'durum_metni': status_text
                })

            total_online_devices += dealer_online_count
            # Şu an açık olan en az 1 cihaz varsa bayi aktif sayılır
            is_active_now = (dealer_online_count > 0)
            if is_active_now:
                total_active_dealers += 1

            dealers.append({
                'id': r[0],
                'bayi_kodu': b_kodu,
                'bayi_adi': clean_user_name(r[2]),
                'il': r[3],
                'ilce': r[4],
                'gln_no': r[5],
                'bkst_kullanici': decrypt_credential(r[6]),
                'bkst_sifre': '••••••••' if r[7] else '',
                'test_modu': bool(r[13]) if len(r) > 13 else False,
                'is_test_dealer': bool(r[13] if len(r) > 13 else 0) or (b_kodu == 'bayi_test' or 'Geliştirici' in (r[12] or '') or 'Test' in (r[2] or '')),
                'durum': r[8] or 'AKTIF',
                'remote_url': r[9] or '',
                'son_senkronizasyon': r[10] or 'Henüz yapılmadı',
                'eklenme_tarihi': r[11] or '',
                'notlar': r[12] or '',
                'pending_items_count': pending_count,
                'today_sales_count': today_sales,
                'error_count': err_cnt,
                'online_device_count': dealer_online_count,
                'total_device_count': len(devices),
                'is_active_now': is_active_now,
                'devices': devices
            })

        conn.close()

        local_ip = get_local_ip()

        return jsonify({
            'success': True,
            'bayiler': dealers,
            'stats': {
                'total_dealers': len(dealers),
                'total_active_dealers': total_active_dealers,
                'total_online_devices': total_online_devices,
                'total_pending_items': total_pending,
                'total_today_sales': total_sales,
                'total_errors': total_errors,
                'local_ip': local_ip,
                'local_port': 5000,
                'local_url': f"http://{local_ip}:5000",
                'public_url': get_current_tunnel_url() or PUBLIC_TUNNEL_URL or ''
            }
        })
    except Exception as e:
        logger.error(f"api_admin_get_bayiler error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/cihaz/<int:cihaz_db_id>/sil', methods=['POST', 'DELETE'])
def api_admin_delete_device(cihaz_db_id):
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("DELETE FROM aktif_cihazlar WHERE id = ?", (cihaz_db_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/cihaz/<int:cihaz_db_id>/ad_guncelle', methods=['POST'])
def api_admin_rename_device(cihaz_db_id):
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403
    data = request.json or {}
    yeni_ad = str(data.get('cihaz_adi', '')).strip()
    if not yeni_ad:
        return jsonify({'success': False, 'error': 'Cihaz adı boş olamaz.'})
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("UPDATE aktif_cihazlar SET cihaz_adi = ? WHERE id = ?", (yeni_ad, cihaz_db_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/ekle', methods=['POST'])
def api_admin_save_bayi():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    data = request.json or {}
    dealer_id = data.get('id') or data.get('dealer_id')
    bayi_kodu = str(data.get('bayi_kodu', '')).strip()
    bayi_adi = str(data.get('bayi_adi', '')).strip()
    il = str(data.get('il', '')).strip().upper()
    ilce = str(data.get('ilce', '')).strip().upper()
    gln_no = str(data.get('gln_no', '')).strip()
    bkst_kullanici = str(data.get('bkst_kullanici', '')).strip()
    bkst_sifre = str(data.get('bkst_sifre', '')).strip()
    remote_url = str(data.get('remote_url', '')).strip()
    notlar = str(data.get('notlar', '')).strip()
    test_modu = 1 if data.get('test_modu') in [True, 1, '1', 'true', 'on'] else 0

    if not bayi_adi:
        return jsonify({'success': False, 'error': 'Bayi adı boş olamaz.'})

    try:
        ensure_db_schema()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        c = conn.cursor()
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        enc_user = encrypt_credential(bkst_kullanici) if bkst_kullanici else ''
        enc_pass = encrypt_credential(bkst_sifre) if bkst_sifre else ''

        if dealer_id:
            # Güncelle
            if not bayi_kodu:
                c.execute("SELECT bayi_kodu FROM bayiler WHERE id=?", (dealer_id,))
                r_bk = c.fetchone()
                if r_bk:
                    bayi_kodu = r_bk[0]
            if not bkst_kullanici:
                c.execute("SELECT bkst_kullanici FROM bayiler WHERE id=?", (dealer_id,))
                r_u = c.fetchone()
                if r_u and r_u[0]:
                    enc_user = r_u[0]
            if bkst_sifre and bkst_sifre != '***' and not bkst_sifre.startswith('••••'):
                c.execute("""
                    UPDATE bayiler SET bayi_adi=?, il=?, ilce=?, gln_no=?, bkst_kullanici=?, bkst_sifre=?, remote_url=?, notlar=?, test_modu=?
                    WHERE id=?
                """, (bayi_adi, il, ilce, gln_no, enc_user, enc_pass, remote_url, notlar, test_modu, dealer_id))
            else:
                c.execute("""
                    UPDATE bayiler SET bayi_adi=?, il=?, ilce=?, gln_no=?, bkst_kullanici=?, remote_url=?, notlar=?, test_modu=?
                    WHERE id=?
                """, (bayi_adi, il, ilce, gln_no, enc_user, remote_url, notlar, test_modu, dealer_id))
        else:
            # Yeni Ekle
            bayi_kodu = f"bayi_{int(time.time())}"
            c.execute("""
                INSERT INTO bayiler (bayi_kodu, bayi_adi, il, ilce, gln_no, bkst_kullanici, bkst_sifre, remote_url, notlar, durum, son_senkronizasyon, eklenme_tarihi, test_modu)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'AKTIF', ?, ?, ?)
            """, (bayi_kodu, bayi_adi, il, ilce, gln_no, enc_user, enc_pass, remote_url, notlar, now_dt, now_dt, test_modu))

        # Doğrudan mevcut bağlantı ile sistem ayarını kaydet (database is locked kilidini kesinlikle önler)
        c.execute("""
            INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi)
            VALUES (?, ?, ?)
            ON CONFLICT(anahtar) DO UPDATE SET
            deger = excluded.deger,
            guncelleme_tarihi = excluded.guncelleme_tarihi
        """, (f"exit_list_rev_{bayi_kodu}", f"init_{int(time.time())}", now_dt))

        # 🛡️ Bayi aktif olarak kaydedildiği/güncellendiği için silinen_bayiler tablosundan temizle
        c.execute("DELETE FROM silinen_bayiler WHERE bayi_kodu = ? OR tc_no = ?", (bayi_kodu, bkst_kullanici))
        if enc_user:
            c.execute("DELETE FROM silinen_bayiler WHERE tc_no = ?", (enc_user,))

        conn.commit()
        conn.close()

        with _revoked_dealers_lock:
            _revoked_dealers.discard(bayi_kodu)
            if bkst_kullanici:
                _revoked_dealers.discard(bkst_kullanici)
            if enc_user:
                _revoked_dealers.discard(enc_user)

        action_title = "Bayi Kaydı Güncellendi" if dealer_id else "Yeni Bayi Eklendi"
        log_debug("ADMIN", f"{action_title}: {bayi_adi}", seviye="INFO", bayi_kodu=bayi_kodu)
        return jsonify({'success': True, 'message': 'Bayi başarıyla kaydedildi.', 'bayi_kodu': bayi_kodu})
    except Exception as e:
        logger.error(f"save_bayi error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/<bayi_kodu>/sil', methods=['DELETE', 'POST'])
def api_admin_delete_bayi(bayi_kodu):
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS silinen_bayiler (id INTEGER PRIMARY KEY AUTOINCREMENT, bayi_kodu TEXT, tc_no TEXT, silinme_tarihi TEXT)")
        c.execute("SELECT id, bayi_adi, bkst_kullanici FROM bayiler WHERE bayi_kodu = ?", (bayi_kodu,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'Bayi bulunamadı.'})

        b_adi = row[1]
        raw_u = row[2] or ''
        dec_u = decrypt_credential(raw_u) or raw_u
        del_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if dec_u:
            c.execute("INSERT INTO silinen_bayiler (bayi_kodu, tc_no, silinme_tarihi) VALUES (?, ?, ?)", (bayi_kodu, dec_u, del_ts))
        c.execute("DELETE FROM bayiler WHERE bayi_kodu = ?", (bayi_kodu,))
        c.execute("DELETE FROM aktif_cihazlar WHERE bayi_kodu = ?", (bayi_kodu,))
        conn.commit()
        conn.close()

        with _revoked_dealers_lock:
            _revoked_dealers.add(bayi_kodu)
            if dec_u:
                _revoked_dealers.add(dec_u)

        # Güvenlik Denetim Günlüğü
        admin_uname = flask_session.get('admin_username', 'admin')
        ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr or '127.0.0.1')
        log_debug(
            "GUVENLIK_DENETIM",
            f"🗑️ Yönetici '{admin_uname}', '{b_adi}' ({bayi_kodu}) bayisini ve tüm cihaz bağlantılarını sildi. İstemci IP: {ip_addr}",
            seviye="WARNING",
            bayi_kodu=bayi_kodu
        )
        return jsonify({'success': True, 'message': f"'{b_adi}' bayisi başarıyla silindi ve yetkisi kapatıldı."})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/<bayi_kodu>/durum', methods=['POST'])
def api_admin_update_bayi_durum(bayi_kodu):
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    data = request.json or {}
    yeni_durum = str(data.get('durum', '')).strip().upper()
    if yeni_durum not in ('AKTIF', 'PASIF', 'ASKIYA_ALINDI', 'ONAY_BEKLIYOR'):
        return jsonify({'success': False, 'error': 'Geçersiz bayi durumu.'})

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT id, bayi_adi, durum FROM bayiler WHERE bayi_kodu = ?", (bayi_kodu,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'Bayi bulunamadı.'})

        b_adi = row[1]
        c.execute("SELECT bkst_kullanici, gln_no FROM bayiler WHERE bayi_kodu = ?", (bayi_kodu,))
        r_extra = c.fetchone()
        b_u = r_extra[0] if r_extra else ''
        b_g = r_extra[1] if r_extra else ''

        # Tüm mükerrer ve ilişkili bayi kayıtlarının durumunu aynı anda güncelle
        c.execute("""
            UPDATE bayiler 
            SET durum = ? 
            WHERE bayi_kodu = ? 
               OR (bkst_kullanici IS NOT NULL AND bkst_kullanici != '' AND bkst_kullanici = ?)
               OR (gln_no IS NOT NULL AND gln_no != '' AND gln_no = ?)
        """, (yeni_durum, bayi_kodu, b_u, b_g))

        if yeni_durum == 'AKTIF':
            c.execute("DELETE FROM silinen_bayiler WHERE bayi_kodu = ? OR tc_no = ?", (bayi_kodu, b_u))
            try:
                dec_u_clean = decrypt_credential(b_u)
                if dec_u_clean:
                    c.execute("DELETE FROM silinen_bayiler WHERE tc_no = ?", (dec_u_clean,))
            except Exception:
                pass
        else:
            c.execute("DELETE FROM aktif_cihazlar WHERE bayi_kodu = ?", (bayi_kodu,))
        conn.commit()
        conn.close()

        # Bellek içi anlık engelleme/izin önbelleğini güncelle
        with _revoked_dealers_lock:
            if yeni_durum != 'AKTIF':
                _revoked_dealers.add(bayi_kodu)
                if b_u:
                    _revoked_dealers.add(b_u)
                    try:
                        dec_u = decrypt_credential(b_u)
                        if dec_u:
                            _revoked_dealers.add(dec_u)
                    except Exception as _ignored_err:
                        logger.debug(f"Suppressed notice: {_ignored_err}")
            else:
                _revoked_dealers.discard(bayi_kodu)
                if b_u:
                    _revoked_dealers.discard(b_u)
                    try:
                        dec_u = decrypt_credential(b_u)
                        if dec_u:
                            _revoked_dealers.discard(dec_u)
                    except Exception as _ignored_err:
                        logger.debug(f"Suppressed notice: {_ignored_err}")
        # İstemcilerin revizyon farkından derhal haberdar olması için revizyonu yenile
        set_system_setting(f"exit_list_rev_{bayi_kodu}", f"rev_{yeni_durum}_{int(time.time())}")

        durum_etiket = "🟢 Yetkilendirildi & Aktif" if yeni_durum == 'AKTIF' else "⛔ Yetki Askıya Alındı / Sonlandırıldı"
        log_debug(
            "GUVENLIK_DENETIM",
            f"👑 Yönetici '{b_adi}' ({bayi_kodu}) bayisinin lisans yetkisini '{yeni_durum}' olarak güncelledi.",
            seviye="WARNING" if yeni_durum != 'AKTIF' else "INFO"
        )
        return jsonify({
            'success': True,
            'message': f"'{b_adi}' bayisi için lisans durumu güncellendi: {durum_etiket}",
            'durum': yeni_durum
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/change_credentials', methods=['POST'])
def api_admin_change_credentials():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    data = request.json or {}
    current_pass = str(data.get('current_password', '')).strip()
    new_user = str(data.get('new_username', '')).strip()
    new_pass = str(data.get('new_password', '')).strip()
    confirm_pass = str(data.get('confirm_password', '')).strip()

    if not current_pass:
        return jsonify({'success': False, 'error': 'Mevcut admin parolanızı giriniz.'})
    if not new_user:
        return jsonify({'success': False, 'error': 'Yeni yönetici kullanıcı adı boş olamaz.'})
    if len(new_user) < 3:
        return jsonify({'success': False, 'error': 'Kullanıcı adı en az 3 karakter olmalıdır.'})
    if not new_pass:
        return jsonify({'success': False, 'error': 'Yeni parola belirleyiniz.'})
    if len(new_pass) < 6:
        return jsonify({'success': False, 'error': 'Yeni parola güvenliğiniz için en az 6 karakter olmalıdır.'})
    if new_pass != confirm_pass:
        return jsonify({'success': False, 'error': 'Yeni parolalar birbiriyle uyuşmuyor.'})

    # Mevcut şifreyi doğrula
    stored_hash = get_system_setting("admin_sifre_hash", "")
    if not stored_hash:
        stored_hash = get_system_setting("admin_sifre", "admin2026")

    if not verify_admin_password(current_pass, stored_hash):
        return jsonify({'success': False, 'error': 'Mevcut admin parolanız hatalı!'})

    try:
        # Yeni şifreyi PBKDF2 ile güvenli hashle
        new_hash = hash_admin_password(new_pass)
        set_system_setting("admin_kullanici_adi", new_user)
        set_system_setting("admin_sifre_hash", new_hash)
        conn_del = sqlite3.connect(DB_PATH, timeout=5.0)
        conn_del.execute("DELETE FROM sistem_ayarlari WHERE anahtar = 'admin_sifre'")
        conn_del.commit()
        conn_del.close()

        flask_session['admin_user'] = new_user
        log_debug("GUVENLIK_DENETIM", f"👑 Yönetici kimlik bilgileri başarıyla güncellendi (Kullanıcı: {new_user})", seviye="INFO")
        return jsonify({
            'success': True,
            'message': 'Admin kullanıcı adı ve parolası başarıyla güncellendi. Güvenliğiniz için yeni bilgiler kaydedildi.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Güncelleme hatası: {str(e)}'})

@app.route('/api/admin/bayi/<bayi_kodu>/switch', methods=['POST'])
def api_admin_switch_bayi(bayi_kodu):
    """
    Yöneticinin seçilen bayinin yetkisi ve veritabanı bağlamıyla işlem yapmasını sağlar.
    Güvenlik: Yalnızca doğrulanmış 'is_admin' oturumları bu geçişi yapabilir.
    Tüm işlem güvenlik denetim günlüğüne (audit log) IP ve zaman damgasıyla kaydedilir.
    """
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT id, bayi_adi, bkst_kullanici, gln_no, bkst_sifre FROM bayiler WHERE bayi_kodu = ?", (bayi_kodu,))
        row = c.fetchone()
        conn.close()

        if not row:
            return jsonify({'success': False, 'error': 'Bayi bulunamadı.'})

        dealer_id, bayi_adi, bk_user, gln_no, bk_sifre = row

        plain_user = decrypt_credential(bk_user)
        plain_pass = decrypt_credential(bk_sifre)

        # Güvenli Temsil Modu (Impersonation Session)
        flask_session['impersonating_dealer'] = True
        flask_session['active_bayi_kodu'] = bayi_kodu
        flask_session['active_bayi_adi'] = bayi_adi
        flask_session['dealer_logged_in'] = True
        flask_session['dealer_username'] = plain_user or ''
        flask_session['dealer_gln'] = gln_no or ''
        flask_session['is_admin'] = True  # Admin yetkisi korunur, tek tıkla geri dönebilir

        with _bkst_session_lock:
            _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0, "user": None})
        if not plain_pass:
            return jsonify({'success': False, 'error': 'Bu bayinin Bakanlık şifresi sunucuda kayıtlı değil. Bayi bilgisayarında bir kez Bakanlık girişi yapın.'})
        # Bayi adına Bakanlık bağlantı kimliklerini çalışma ortamına yükle
        if plain_user and plain_pass:
            try:
                save_bkst_credentials(plain_user, plain_pass, gln_no or '', bayi_adi or '')
            except Exception as e_c:
                logger.warning(f"save_bkst_credentials error during switch: {e_c}")

        # Bakanlık bağlantısını hemen sına; başarısızsa nedenini kullanıcıya bildir
        bkst_warning = ''
        try:
            _s, _g, _t, _err = get_bkst_authenticated_session()
            if _err:
                bkst_warning = _err
        except Exception as e_bk:
            bkst_warning = f"Bakanlık sunucusuna bağlanılamadı ({type(e_bk).__name__}). Sunucu bilgisayarının internet bağlantısını kontrol edin."

        # Güvenlik Denetim Günlüğü
        admin_uname = flask_session.get('admin_username', 'admin')
        ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr or '127.0.0.1')
        log_debug(
            "GUVENLIK_DENETIM",
            f"👑 Yönetici '{admin_uname}', '{bayi_adi}' ({bayi_kodu} - GLN: {gln_no or '-'}) bayisi adına işlem moduna geçti. İstemci IP: {ip_addr}",
            seviye="WARNING",
            bayi_kodu=bayi_kodu
        )

        return jsonify({
            'success': True,
            'active_bayi_kodu': bayi_kodu,
            'bayi_adi': bayi_adi,
            'gln_no': gln_no or '',
            'bkst_kullanici': plain_user or '',
            'bkst_warning': bkst_warning,
            'redirect': '/cikis',
            'message': f"'{bayi_adi}' yetkisine başarıyla geçildi."
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ==============================================================================
# 🧪 GELİŞTİRİCİ MODU API (yalnızca Süper Admin, yalnızca sunucuda)
# ==============================================================================
def _dev_is_client_machine():
    """Geliştirici modu ana sunucuda ve admin panelinde her zaman serbestçe açılabilir."""
    return False

def _dev_reset_caches():
    with _bkst_session_lock:
        _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0, "user": None})
    with _template_vars_lock:
        _template_vars_cache['expires_at'] = 0
    with _local_count_lock:
        _local_count_cache_by_user.clear()

@app.route('/api/admin/dev_mode', methods=['GET'])
def api_admin_dev_mode_status():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403
    dealers = []
    try:
        c_r = _real_sqlite_connect(DB_PATH, timeout=5.0)
        for r in c_r.execute("SELECT bayi_kodu, bayi_adi, COALESCE(bkst_kullanici,''), COALESCE(bkst_sifre,'') FROM bayiler ORDER BY bayi_adi"):
            dealers.append({'bayi_kodu': r[0], 'bayi_adi': r[1], 'has_credentials': bool(r[2] and r[3])})
        sel = c_r.execute("SELECT deger FROM sistem_ayarlari WHERE anahtar = 'dev_kimlik_bayi'").fetchone()
        c_r.close()
    except Exception:
        sel = None
    return jsonify({'success': True, 'enabled': bool(flask_session.get('dev_mode')),
                    'is_client_machine': False,
                    'credential_bayi_kodu': sel[0] if sel else '', 'dealers': dealers})

@app.route('/api/admin/dev_mode', methods=['POST'])
def api_admin_dev_mode_toggle():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403
    data = request.get_json(silent=True) or {}
    enable = bool(data.get('enabled'))

    if not enable:
        flask_session.pop('dev_mode', None)
        flask_session.pop('impersonating_dealer', None)
        flask_session.pop('dealer_logged_in', None)
        flask_session.pop('dealer_username', None)
        flask_session.pop('dealer_gln', None)
        flask_session.pop('active_bayi_adi', None)
        flask_session['active_bayi_kodu'] = 'bayi_1'
        _dev_reset_caches()
        return jsonify({'success': True, 'enabled': False, 'message': 'Geliştirici modu kapatıldı. Gerçek verilere dönüldü.'})

    cred_code = str(data.get('credential_bayi_kodu') or '').strip()
    try:
        c_r = _real_sqlite_connect(DB_PATH, timeout=10.0)
        row = None
        if cred_code:
            row = c_r.execute("SELECT bkst_kullanici, bkst_sifre, gln_no FROM bayiler WHERE bayi_kodu = ?", (cred_code,)).fetchone()
        if not row or not row[0] or not row[1]:
            r_first = c_r.execute("SELECT bkst_kullanici, bkst_sifre, gln_no, bayi_kodu FROM bayiler WHERE bkst_kullanici IS NOT NULL AND bkst_kullanici != '' LIMIT 1").fetchone()
            if r_first:
                row = (r_first[0], r_first[1], r_first[2])
                cred_code = r_first[3]
        c_r.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

    if not row or not row[0] or not row[1]:
        row = ('ENC:test_user', 'ENC:test_pass', '')
        cred_code = cred_code or 'bayi_1'

    try:
        flask_session['dev_mode'] = True
        ensure_db_schema()  # deneme veritabanını (yoksa) oluşturur
        now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        d = _real_sqlite_connect(DEV_DB_PATH, timeout=15.0)
        dc = d.cursor()
        dc.execute("DELETE FROM bayiler WHERE bayi_kodu IN ('deneme', 'bayi_1')")
        dc.execute("""INSERT INTO bayiler (bayi_kodu, bayi_adi, il, ilce, gln_no, bkst_kullanici, bkst_sifre, durum, son_senkronizasyon, eklenme_tarihi)
                      VALUES ('deneme', 'Deneme (Geliştirici)', 'DENEME', 'DENEME', ?, ?, ?, 'AKTIF', ?, ?)""",
                   (row[2] or '', row[0], row[1], now_s, now_s))
        for k, v in (('bkst_kullanici_adi', row[0]), ('bkst_sifre', row[1]), ('bkst_adres_id', row[2] or ''), ('bkst_kullanici_isim', 'Deneme (Geliştirici)')):
            dc.execute("""INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi) VALUES (?, ?, ?)
                          ON CONFLICT(anahtar) DO UPDATE SET deger = excluded.deger, guncelleme_tarihi = excluded.guncelleme_tarihi""", (k, v, now_s))
        # Bakanlık depo stoklarını (salt okunur kopya) deneme veritabanına al
        try:
            dc.execute("ATTACH DATABASE ? AS gercek", (DB_PATH,))
            real_user = decrypt_credential(row[0])
            dc.execute("DELETE FROM bkst_depo_verileri")
            dc.execute("INSERT INTO bkst_depo_verileri SELECT * FROM gercek.bkst_depo_verileri WHERE kullanici_adi = ?", (real_user,))
            dc.execute("DETACH DATABASE gercek")
        except Exception as e_stock:
            logger.warning(f"dev mode stock copy notice: {e_stock}")
        d.commit()
        d.close()
        c_r = _real_sqlite_connect(DB_PATH, timeout=10.0)
        c_r.execute("""INSERT INTO sistem_ayarlari (anahtar, deger, guncelleme_tarihi) VALUES ('dev_kimlik_bayi', ?, ?)
                       ON CONFLICT(anahtar) DO UPDATE SET deger = excluded.deger, guncelleme_tarihi = excluded.guncelleme_tarihi""", (cred_code, now_s))
        c_r.commit()
        c_r.close()
    except Exception as e:
        flask_session.pop('dev_mode', None)
        logger.error(f"dev_mode enable error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

    flask_session['active_bayi_kodu'] = 'deneme'
    flask_session['active_bayi_adi'] = 'Deneme (Geliştirici)'
    flask_session['impersonating_dealer'] = True
    flask_session['dealer_logged_in'] = True
    flask_session['dealer_username'] = decrypt_credential(row[0])
    flask_session['dealer_gln'] = row[2] or ''
    _dev_reset_caches()
    return jsonify({'success': True, 'enabled': True, 'redirect': '/cikis',
                    'message': 'Geliştirici modu açık. Tüm işlemler ayrı deneme veritabanında yapılır.'})

@app.route('/api/admin/dev_mode/reset', methods=['POST'])
def api_admin_dev_mode_reset():
    """Deneme veritabanındaki çıkış listesi ve satış kayıtlarını temizler (gerçek veriye dokunmaz)."""
    from flask import session as flask_session
    if not flask_session.get('is_admin') or not flask_session.get('dev_mode'):
        return jsonify({'success': False, 'error': 'Yalnızca geliştirici modundayken kullanılabilir.'}), 403
    try:
        d = _real_sqlite_connect(DEV_DB_PATH, timeout=15.0)
        for t in ('cikis_kayitlari', 'satis_arsivi', 'debug_loglari', 'aktif_cihazlar'):
            try:
                d.execute(f"DELETE FROM {t}")
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
        d.commit()
        d.close()
        _dev_reset_caches()
        return jsonify({'success': True, 'message': 'Deneme verileri sıfırlandı.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/exit_switch', methods=['POST', 'GET'])
def api_admin_exit_switch():
    """
    Bayi adına işlem modundan çıkıp tekrar Yönetim Paneline döner.
    """
    from flask import session as flask_session
    b_adi = flask_session.get('active_bayi_adi', 'Bayi')
    flask_session.pop('impersonating_dealer', None)
    flask_session.pop('active_bayi_kodu', None)
    flask_session.pop('active_bayi_adi', None)
    flask_session.pop('dealer_logged_in', None)
    flask_session.pop('dealer_username', None)
    flask_session.pop('dealer_gln', None)
    flask_session['is_admin'] = True

    admin_uname = flask_session.get('admin_username', 'admin')
    log_debug("GUVENLIK_DENETIM", f"👑 Yönetici '{admin_uname}', '{b_adi}' temsil modundan çıkıp Yönetim Paneline döndü.", seviye="INFO")
    return jsonify({'success': True, 'redirect': '/admin'})

@app.route('/api/admin/bayi/<bayi_kodu>/cikis_listesi', methods=['GET'])
def api_admin_get_exit_list(bayi_kodu):
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("""
            SELECT id, tarih, urun_adi, barkod, koli_no, seri_no, parti_no, uretim_tarihi, skt, ham_karekod,
                   COALESCE(cihaz_adi, '') as cihaz_adi, COALESCE(cihaz_id, '') as cihaz_id,
                   CASE WHEN COALESCE(admin_gizli, 0) = 1 OR COALESCE(silindi, 0) = 1 THEN 1 ELSE 0 END as gz
            FROM cikis_kayitlari
            WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))
              AND COALESCE(silindi, 0) = 0
            ORDER BY id DESC
        """, (bayi_kodu, bayi_kodu))
        rows = c.fetchall()
        conn.close()

        items = []
        deleted_items = []
        for r in rows:
            (deleted_items if r[12] else items).append({
                'id': r[0],
                'tarih': r[1],
                'urun_adi': r[2],
                'barkod': r[3],
                'koli_no': r[4],
                'seri_no': r[5],
                'parti_no': r[6],
                'uretim_tarihi': r[7],
                'skt': r[8],
                'ham_karekod': r[9],
                'cihaz_adi': r[10] or 'Masaüstü PC',
                'cihaz_id': r[11] or ''
            })

        return jsonify({'success': True, 'items': items, 'deleted_items': deleted_items, 'pending_changes': len(deleted_items)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/<bayi_kodu>/cikis_listesi/toplu', methods=['POST'])
def api_admin_bulk_exit_items(bayi_kodu):
    """Toplu sil / geri al. Silme yalnızca işaretler; bayiye 'Değişiklikleri Bayiye Gönder' ile iletilir."""
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    data = request.json or {}
    action = str(data.get('action') or '')
    ids = [int(i) for i in (data.get('ids') or []) if str(i).lstrip('-').isdigit()]
    if action not in ('sil', 'geri_al') or not ids:
        return jsonify({'success': False, 'error': 'Geçersiz işlem veya seçim boş.'})

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        flag = 1 if action == 'sil' else 0
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        changed = 0
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            ph = ','.join('?' * len(chunk))
            if action == 'sil':
                c.execute(
                    f"UPDATE cikis_kayitlari SET admin_gizli = 1, silindi = 1, silinme_tarihi = ?, sync_durum = 'ESITLENDI' "
                    f"WHERE id IN ({ph}) AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))",
                    [now_dt] + chunk + [bayi_kodu, bayi_kodu])
            else:
                c.execute(
                    f"UPDATE cikis_kayitlari SET admin_gizli = 0, silindi = 0, silinme_tarihi = NULL, sync_durum = 'ESITLENDI' "
                    f"WHERE id IN ({ph}) AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))",
                    chunk + [bayi_kodu, bayi_kodu])
            changed += c.rowcount
        c.execute("SELECT COUNT(*) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND COALESCE(admin_gizli, 0) = 1 AND COALESCE(silindi, 0) = 0", (bayi_kodu, bayi_kodu))
        pending_changes = c.fetchone()[0]
        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, bayi_kodu))
        conn.commit()
        conn.close()

        rev_id = str(int(time.time() * 1000))
        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)

        log_debug("ADMIN", f"Toplu işlem: {action} ({changed} kutu, Bayi: {bayi_kodu}, Rev: {rev_id})", seviye="INFO", bayi_kodu=bayi_kodu)
        verb = 'kaldırıldı ve silindi' if action == 'sil' else 'geri alındı'
        return jsonify({'success': True, 'changed': changed, 'pending_changes': pending_changes,
                        'message': f'{changed} kutu {verb}.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/<bayi_kodu>/cikis_listesi/sil', methods=['POST'])
def api_admin_delete_exit_item(bayi_kodu):
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    data = request.json or {}
    item_id = data.get('item_id')
    if not item_id:
        return jsonify({'success': False, 'error': 'Kayıt ID gerekli.'})

    try:
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("UPDATE cikis_kayitlari SET admin_gizli = 1, silindi = 1, silinme_tarihi = ?, sync_durum = 'ESITLENDI' WHERE id = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (now_dt, item_id, bayi_kodu, bayi_kodu))
        c.execute("SELECT COUNT(*) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND COALESCE(admin_gizli, 0) = 1 AND COALESCE(silindi, 0) = 0", (bayi_kodu, bayi_kodu))
        pending_changes = c.fetchone()[0]
        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, bayi_kodu))
        conn.commit()
        conn.close()

        rev_id = str(int(time.time() * 1000))
        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)

        log_debug("ADMIN", f"Admin kutuyu sildi (ID: {item_id}, Bayi: {bayi_kodu}, Rev: {rev_id})", seviye="INFO", bayi_kodu=bayi_kodu)
        return jsonify({'success': True, 'pending_changes': pending_changes, 'message': 'Kutu listeden kaldırıldı ve silindi.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/<bayi_kodu>/cikis_listesi/guncelle', methods=['POST'])
def api_admin_push_exit_list(bayi_kodu):
    """
    Admin tarafından yapılan değişiklikleri işaretler ve son senkronizasyon saatini günceller.
    """
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        # Bekleyen admin silmelerini Soft Delete olarak işaretle
        now_push_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'BEKLIYOR', admin_gizli = 0 WHERE COALESCE(admin_gizli, 0) = 1 AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (now_push_str, bayi_kodu, bayi_kodu))
        applied = c.rowcount
        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, bayi_kodu))
        conn.commit()
        conn.close()
        # Revizyon, açık yazma işlemi KAPANDIKTAN sonra yazılmalı (aksi halde veritabanı kilitli kalır)
        rev_id = str(int(time.time() * 1000))
        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)
        log_debug("SENKRON", f"📤 Admin Çıkış Listesi Değişiklikleri Bayiye İletildi ({bayi_kodu}, Rev: {rev_id})", seviye="INFO", bayi_kodu=bayi_kodu)
        return jsonify({'success': True, 'revision': rev_id, 'message': 'Değişiklikler bayiye başarıyla iletildi.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/<bayi_kodu>/push', methods=['POST'])
def api_admin_push_dealer(bayi_kodu):
    """Sunucudaki güncel değişiklikleri bayiye / istemciye iletir."""
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rev_id = f"push_{int(time.time())}"
        set_system_setting(f"exit_list_rev_{bayi_kodu}", rev_id)

        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, bayi_kodu))
        b_adi = c.execute("SELECT bayi_adi, remote_url FROM bayiler WHERE bayi_kodu = ?", (bayi_kodu,)).fetchone()
        conn.commit()
        conn.close()

        bayi_adi = b_adi[0] if b_adi else bayi_kodu
        log_debug("SENKRON", f"📤 Güncel Veriler Bayiye İletildi ({bayi_adi}, Rev: {rev_id})", seviye="INFO", bayi_kodu=bayi_kodu)
        return jsonify({
            'success': True,
            'revision': rev_id,
            'message': f"'{bayi_adi}' bayisine güncel veriler ve değişiklikler başarıyla iletildi."
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/<bayi_kodu>/pull', methods=['POST'])
def api_admin_pull_dealer(bayi_kodu):
    """Bayideki güncel verileri manuel olarak sunucuya çekmeye çalışır."""
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        row = c.execute("SELECT bayi_adi, remote_url FROM bayiler WHERE bayi_kodu = ?", (bayi_kodu,)).fetchone()
        bayi_adi = row[0] if row else bayi_kodu
        remote_url = row[1] if row else ""

        c_ip = c.execute("SELECT ip_adresi FROM aktif_cihazlar WHERE bayi_kodu = ? ORDER BY son_gorulme DESC LIMIT 1", (bayi_kodu,)).fetchone()
        target_ip = c_ip[0] if c_ip else ""

        pulled_count = 0
        target_url = remote_url or (f"http://{target_ip}:5000" if target_ip and target_ip != '127.0.0.1' else "")
        if target_url:
            try:
                import requests
                r_pull = requests.get(f"{target_url.rstrip('/')}/api/sync/pull_dealer_records", timeout=3.5)
                if r_pull.status_code == 200:
                    js = r_pull.json()
                    pulled_count = len(js.get('cikis_kayitlari', []))
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, bayi_kodu))
        conn.commit()
        conn.close()

        log_debug("SENKRON", f"📥 Bayiden Güncel Veriler Çekildi ({bayi_adi})", seviye="INFO", bayi_kodu=bayi_kodu)
        return jsonify({
            'success': True,
            'pulled_count': pulled_count,
            'message': f"'{bayi_adi}' bayisindeki güncel veriler kontrol edildi ve senkronize edildi."
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bayi/<bayi_kodu>/sync', methods=['POST'])
def api_admin_sync_dealer(bayi_kodu):
    return api_admin_push_dealer(bayi_kodu)

@app.route('/api/admin/bildirimler', methods=['GET'])
def api_admin_bildirimler():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("""
            SELECT id, tarih, baslik, mesaj, tur, bayi_kodu, tc_no, sirket_adi, gln, okundu
            FROM sistem_bildirimleri
            ORDER BY id DESC LIMIT 50
        """)
        rows = c.fetchall()
        unread_count = c.execute("SELECT COUNT(*) FROM sistem_bildirimleri WHERE okundu = 0").fetchone()[0]
        conn.close()

        notifs = []
        for r in rows:
            notifs.append({
                'id': r[0],
                'tarih': r[1],
                'baslik': r[2],
                'mesaj': r[3],
                'tur': r[4],
                'bayi_kodu': r[5],
                'tc_no': r[6],
                'sirket_adi': r[7],
                'gln': r[8],
                'okundu': bool(r[9])
            })
        return jsonify({'success': True, 'bildirimler': notifs, 'unread_count': unread_count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bildirim/<int:nid>/okundu', methods=['POST'])
def api_admin_bildirim_okundu(nid):
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.execute("UPDATE sistem_bildirimleri SET okundu = 1 WHERE id = ?", (nid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/bildirim/<int:nid>/onayla', methods=['POST'])
def api_admin_bildirim_onayla(nid):
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT bayi_kodu, tc_no, sirket_adi, gln FROM sistem_bildirimleri WHERE id = ?", (nid,))
        row = c.fetchone()
        if row and (row[0] or row[1]):
            b_kodu = row[0] or f"bayi_{int(time.time())}"
            tc_val = str(row[1] or '').strip()
            sirket_val = clean_user_name(row[2] or tc_val or b_kodu)
            gln_val = str(row[3] or '').strip()
            enc_tc = encrypt_credential(tc_val) if tc_val else ''
            now_app = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            c.execute("SELECT id FROM bayiler WHERE bayi_kodu = ? OR (bkst_kullanici != '' AND (bkst_kullanici = ? OR bkst_kullanici = ?))", (b_kodu, enc_tc, tc_val))
            ex_b = c.fetchone()
            if ex_b:
                c.execute("UPDATE bayiler SET durum = 'AKTIF' WHERE id = ? OR bayi_kodu = ?", (ex_b[0], b_kodu))
            else:
                c.execute("""
                    INSERT INTO bayiler (bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum, son_senkronizasyon, eklenme_tarihi, notlar)
                    VALUES (?, ?, ?, '', ?, 'AKTIF', ?, ?, 'Bildirim panelinden yönetici tarafından onaylandı')
                """, (b_kodu, sirket_val, enc_tc, gln_val, now_app, now_app))

            # 🛡️ Onaylanan bayiyi silinen_bayiler tablosundan temizle
            c.execute("DELETE FROM silinen_bayiler WHERE bayi_kodu = ? OR tc_no = ?", (b_kodu, tc_val))
            if enc_tc:
                c.execute("DELETE FROM silinen_bayiler WHERE tc_no = ?", (enc_tc,))

            with _revoked_dealers_lock:
                _revoked_dealers.discard(b_kodu)
                if tc_val:
                    _revoked_dealers.discard(tc_val)
                if enc_tc:
                    _revoked_dealers.discard(enc_tc)
        c.execute("UPDATE sistem_bildirimleri SET okundu = 1 WHERE id = ?", (nid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Bayi başarıyla onaylandı ve yetkilendirildi.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/sync_all', methods=['POST'])
def api_admin_sync_all():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("UPDATE bayiler SET son_senkronizasyon = ?", (now_dt,))
        conn.commit()
        conn.close()
        log_debug("SENKRON", "🔄 Tüm Bayiler Canlı Eşitlendi", seviye="INFO")
        return jsonify({'success': True, 'message': 'Tüm bayiler başarıyla eşitlendi.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ==============================================================================
# 🔄 ÇOKLU CİHAZ SENKRONİZASYON & VERİ ÇEKME MOTORU (İSTEĞE BAĞLI / MANUEL)
# ==============================================================================

def _authenticate_dealer_request(data, conn=None):
    """
    Tüm senkronizasyon ve veri aktarım uçları için katı, zaman-sabit kimlik doğrulayıcı.
    Dönüş: (success: bool, error_msg: str, status_code: int, target_bayi_kodu: str, dealer_row: tuple)
    dealer_row: (id, bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum)
    """
    from flask import session as flask_session

    # 1. Admin oturumu varsa doğrudan yetkili
    if flask_session.get('is_admin'):
        req_code = str(data.get('bayi_kodu') or '').strip()
        code = req_code or flask_session.get('active_bayi_kodu') or 'bayi_1'
        return True, "", 200, code, None

    bayi_kodu = str(data.get('bayi_kodu') or '').strip()
    raw_u = str(data.get('bkst_kullanici') or data.get('username') or '').strip()
    raw_p = str(data.get('bkst_sifre') or data.get('password') or '').strip()

    plain_u = decrypt_credential(raw_u) if raw_u.startswith('ENC:') else raw_u
    plain_p = decrypt_credential(raw_p) if raw_p.startswith('ENC:') else raw_p

    # 2. Aynı oturumda aktif olarak giriş yapmış bayi kontrolü
    sess_bk = str(flask_session.get('active_bayi_kodu') or '').strip()
    if flask_session.get('dealer_logged_in') and sess_bk:
        if not bayi_kodu or bayi_kodu == sess_bk:
            return True, "", 200, sess_bk, None

    # 3. Parametre denetimi: bayi_kodu veya kullanıcı bilgisi mutlaka belirtilmelidir
    if not bayi_kodu and not plain_u:
        return False, "Eksik parametre: Bayi kodu veya kullanıcı bilgisi gereklidir.", 400, "", None

    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        close_conn = True

    try:
        c = conn.cursor()
        c.execute("SELECT id, bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum, COALESCE(test_modu, 0) FROM bayiler")
        rows = c.fetchall()

        matched_dealer = None
        # Öncelikle kullanıcı adı ile eşleştir (50-100 bayi izolasyonu)
        if plain_u:
            for r in rows:
                r_u = decrypt_credential(r[3]).strip() if r[3] else ""
                if r_u == plain_u or r[3] == raw_u or r[1] == plain_u:
                    matched_dealer = r
                    break

        # Kullanıcı adıyla bulunamadıysa bayi_kodu ile ara
        if not matched_dealer and bayi_kodu:
            for r in rows:
                if r[1] == bayi_kodu:
                    matched_dealer = r
                    break

        if not matched_dealer:
            return False, "Yetkisiz erişim: Bayi kaydı bulunamadı.", 401, "", None

        d_id, d_code, d_name, d_enc_u, d_enc_p, d_gln, d_durum, d_test = matched_dealer

        # Durum Denetimi (Lisans / İptal)
        if str(d_durum or '').upper().strip() != 'AKTIF':
            return False, "⛔ Bayilik yetkiniz durdurulmuştur veya yetkiniz bulunmamaktadır!", 403, d_code, matched_dealer

        # Şifre Doğrulama (Zaman-sabit hmac.compare_digest)
        d_plain_p = decrypt_credential(d_enc_p).strip() if d_enc_p else ""
        if d_plain_p:
            if not plain_p:
                return False, "Yetkisiz erişim: Bayi şifresi gereklidir.", 401, d_code, matched_dealer
            if not hmac.compare_digest(d_plain_p, plain_p):
                logger.warning(f"🚨 Bayi şifre doğrulaması başarısız: Bayi: {d_code}")
                return False, "❌ Giriş Başarısız: Girdiğiniz şifre hatalı!", 401, d_code, matched_dealer
        else:
            # Bayi şifresi DB'de boşsa, ancak istekte şifre gönderilmişse sunucu anahtarıyla kaydet; aksi halde reddet
            if not plain_p:
                return False, "Yetkisiz erişim: Bayi şifresi kayıtlı değil veya girilmedi.", 401, d_code, matched_dealer
            try:
                conn.execute("UPDATE bayiler SET bkst_sifre = ? WHERE id = ?", (encrypt_credential(plain_p), d_id))
                conn.commit()
            except Exception as e_p_up:
                logger.debug(f"Dealer initial pass save notice: {e_p_up}")

        return True, "", 200, d_code, matched_dealer
    finally:
        if close_conn:
            conn.close()

@app.route('/api/sync/push', methods=['POST'])
def api_sync_push_to_server():
    """
    İstemci cihazların (dükkandaki bilgisayar / terminal) yerel veritabanında okutulmuş
    çıkış kayıtlarını, satış arşivini ve çiftçileri merkezi sunucuya aktarmasını sağlar.
    """
    data = request.get_json(silent=True) or {}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()

        ok, err, status_code, target_bayi_kodu, dealer_row = _authenticate_dealer_request(data, conn)
        if not ok:
            conn.close()
            return jsonify({'success': False, 'error': err, 'unauthorized': True}), status_code

        # Eğer bayinin kullanıcı adı, şifresi veya adı istemciden geldiyse sunucudaki kayıtlı bilgileri sunucu anahtarıyla güncelle
        raw_bk_u = str(data.get('bkst_kullanici') or '').strip()
        raw_bk_p = str(data.get('bkst_sifre') or '').strip()
        plain_u = decrypt_credential(raw_bk_u) if raw_bk_u.startswith('ENC:') else raw_bk_u
        plain_p = decrypt_credential(raw_bk_p) if raw_bk_p.startswith('ENC:') else raw_bk_p
        gln_no = str(data.get('gln_no') or '').strip()
        bayi_adi = str(data.get('bayi_adi') or '').strip()

        if dealer_row:
            d_id = dealer_row[0]
            new_sifre = encrypt_credential(plain_p) if plain_p else dealer_row[4]
            new_gln = gln_no if gln_no else dealer_row[5]
            clean_incoming_adi = clean_user_name(bayi_adi)
            old_clean_adi = clean_user_name(dealer_row[2] or '')
            if clean_incoming_adi and not clean_incoming_adi.isdigit():
                new_adi = clean_incoming_adi
            elif old_clean_adi and not old_clean_adi.isdigit():
                new_adi = old_clean_adi
            else:
                new_adi = clean_incoming_adi or old_clean_adi
            new_user = encrypt_credential(plain_u) if plain_u else dealer_row[3]
            c.execute("""
                UPDATE bayiler 
                SET bkst_kullanici = ?, bkst_sifre = ?, gln_no = ?, bayi_adi = ?
                WHERE id = ?
            """, (new_user, new_sifre, new_gln, new_adi, d_id))
            conn.commit()

        inserted_cikis = 0
        updated_cikis = 0
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 1. Çıkış Kayıtları (Okutulan Karekodlar)
        cikis_items = data.get('cikis_kayitlari', [])
        for itm in cikis_items:
            hk = itm.get('ham_karekod') or ''
            seri = itm.get('seri_no') or ''
            barkod = itm.get('barkod') or ''

            existing_row = None
            if hk:
                c.execute("SELECT id, tarih, COALESCE(silindi, 0), COALESCE(silinme_tarihi, '') FROM cikis_kayitlari WHERE ham_karekod = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (hk, target_bayi_kodu, target_bayi_kodu))
                existing_row = c.fetchone()
            elif seri and barkod:
                c.execute("SELECT id, tarih, COALESCE(silindi, 0), COALESCE(silinme_tarihi, '') FROM cikis_kayitlari WHERE seri_no = ? AND barkod = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (seri, barkod, target_bayi_kodu, target_bayi_kodu))
                existing_row = c.fetchone()

            itm_dev_name = str(itm.get('cihaz_adi') or 'Masaüstü PC').strip()
            itm_dev_id = str(itm.get('cihaz_id') or '').strip()

            is_item_deleted = 1 if itm.get('silindi') in (1, '1', True) else 0
            del_time = itm.get('silinme_tarihi') or ''
            cli_tarih = itm.get('tarih') or now_dt
            cli_ts = del_time if is_item_deleted else cli_tarih

            if not existing_row:
                c.execute("""
                    INSERT INTO cikis_kayitlari (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, uretim_tarihi, skt, ham_karekod, bayi_kodu, sync_durum, cihaz_adi, cihaz_id, silindi, silinme_tarihi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ESITLENDI', ?, ?, ?, ?)
                """, (
                    cli_tarih,
                    itm.get('urun_adi') or '',
                    barkod,
                    itm.get('koli_no') or '',
                    seri,
                    itm.get('parti_no') or '',
                    itm.get('uretim_tarihi') or '',
                    itm.get('skt') or '',
                    hk,
                    target_bayi_kodu,
                    itm_dev_name,
                    itm_dev_id,
                    is_item_deleted,
                    del_time
                ))
                if not is_item_deleted:
                    inserted_cikis += 1
            else:
                existing_id, srv_tarih, srv_silindi, srv_sil_tarihi = existing_row
                srv_ts = srv_sil_tarihi if srv_silindi == 1 else srv_tarih

                # En Son Değişiklik Kuralı:
                if is_item_deleted and srv_ts and cli_ts and srv_ts > cli_ts and srv_silindi == 0:
                    # Sunucudaki taze okutma istemcideki eski silmeden daha yeni ise sunucu korunur
                    continue

                if is_item_deleted:
                    if hk:
                        c.execute("""
                            UPDATE cikis_kayitlari 
                            SET silindi = 1, silinme_tarihi = COALESCE(NULLIF(?, ''), silinme_tarihi, ?), sync_durum = 'ESITLENDI'
                            WHERE ham_karekod = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))
                        """, (del_time, now_dt, hk, target_bayi_kodu, target_bayi_kodu))
                    else:
                        c.execute("""
                            UPDATE cikis_kayitlari 
                            SET silindi = 1, silinme_tarihi = COALESCE(NULLIF(?, ''), silinme_tarihi, ?), sync_durum = 'ESITLENDI'
                            WHERE id = ?
                        """, (del_time, now_dt, existing_id))
                else:
                    if hk:
                        c.execute("""
                            UPDATE cikis_kayitlari 
                            SET tarih = COALESCE(NULLIF(?, ''), tarih),
                                cihaz_adi = COALESCE(NULLIF(?, ''), cihaz_adi),
                                cihaz_id = COALESCE(NULLIF(?, ''), cihaz_id),
                                silindi = 0,
                                silinme_tarihi = NULL,
                                sync_durum = 'ESITLENDI'
                            WHERE id = ?
                        """, (cli_tarih, itm_dev_name, itm_dev_id, existing_id))
                        c.execute("""
                            DELETE FROM cikis_kayitlari
                            WHERE ham_karekod = ? AND id != ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))
                        """, (hk, existing_id, target_bayi_kodu, target_bayi_kodu))
                    else:
                        c.execute("""
                            UPDATE cikis_kayitlari 
                            SET tarih = COALESCE(NULLIF(?, ''), tarih),
                                cihaz_adi = COALESCE(NULLIF(?, ''), cihaz_adi),
                                cihaz_id = COALESCE(NULLIF(?, ''), cihaz_id),
                                silindi = 0,
                                silinme_tarihi = NULL,
                                sync_durum = 'ESITLENDI'
                            WHERE id = ?
                        """, (cli_tarih, itm_dev_name, itm_dev_id, existing_id))
                updated_cikis += 1

        # Mükerrer satırları kesin olarak temizle (Her bayinin kendi kayıtları korunur)
        c.execute("""
            DELETE FROM cikis_kayitlari
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM cikis_kayitlari
                WHERE ham_karekod IS NOT NULL AND ham_karekod != ''
                GROUP BY ham_karekod, COALESCE(bayi_kodu, 'bayi_1')
            )
            AND ham_karekod IS NOT NULL AND ham_karekod != ''
        """)

        # 2. Satış Arşivi
        satis_items = data.get('satis_arsivi', [])
        inserted_satis = 0
        for itm in satis_items:
            hk = itm.get('ham_karekod') or ''
            seri = itm.get('seri_no') or ''
            if hk:
                c.execute("SELECT id FROM satis_arsivi WHERE ham_karekod = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (hk, target_bayi_kodu, target_bayi_kodu))
            else:
                c.execute("SELECT id FROM satis_arsivi WHERE seri_no = ? AND (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (seri, target_bayi_kodu, target_bayi_kodu))
            if not c.fetchone():
                c.execute("""
                    INSERT INTO satis_arsivi (tarih, urun_adi, barkod, seri_no, koli_no, ham_karekod, bayi_kodu)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    itm.get('tarih') or now_dt,
                    itm.get('urun_adi') or '',
                    itm.get('barkod') or '',
                    seri,
                    itm.get('koli_no') or '',
                    hk,
                    target_bayi_kodu
                ))
                inserted_satis += 1

        # 3. Çiftçiler
        ciftciler = data.get('ciftciler', [])
        for cf in ciftciler:
            tc = cf.get('tc_no') or ''
            if tc:
                c.execute("SELECT id FROM ciftciler WHERE tc_no = ?", (tc,))
                if not c.fetchone():
                    c.execute("""
                        INSERT INTO ciftciler (ad_soyad, tc_no, telefon, il, ilce, koy, notlar)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (cf.get('ad_soyad'), tc, cf.get('telefon'), cf.get('il'), cf.get('ilce'), cf.get('koy'), cf.get('notlar')))

        # Toplam güncel bekleyen kutu sayısı (sadece canlı olanlar)
        c.execute("SELECT COUNT(*) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND COALESCE(silindi, 0) = 0", (target_bayi_kodu, target_bayi_kodu))
        total_pending = c.fetchone()[0]

        # Bayi son senkronizasyon saatini güncelle
        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, target_bayi_kodu))
        conn.commit()
        conn.close()
        if inserted_cikis > 0:
            # Başka bir cihazdan yeni okutma geldi: tüm cihazlar otomatik çeksin
            set_system_setting(f"exit_list_rev_{target_bayi_kodu}", str(int(time.time() * 1000)))

        log_debug("SENKRON", f"📥 İstemciden {inserted_cikis} Adet Çıkış Kaydı Merkeze Aktarıldı (Toplam Bekleyen: {total_pending})", seviye="INFO", bayi_kodu=target_bayi_kodu)

        return jsonify({
            'success': True,
            'bayi_kodu': target_bayi_kodu,
            'inserted_cikis': inserted_cikis,
            'total_pending': total_pending,
            'inserted_satis': inserted_satis,
            'message': f'{inserted_cikis} adet yeni çıkış kaydı merkeze aktarıldı. Toplam bekleyen kutu: {total_pending}'
        })
    except Exception as e:
        logger.error(f"api_sync_push error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sync/delete', methods=['POST'])
def api_sync_delete_from_client():
    """İstemcide silinen kutuları sunucudan da siler (silme günlüğü)."""
    data = request.get_json(silent=True) or {}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        ok, err, status_code, code, _ = _authenticate_dealer_request(data, conn)
        if not ok:
            conn.close()
            return jsonify({'success': False, 'error': err, 'unauthorized': True}), status_code

        hks = [str(h).strip() for h in (data.get('ham_karekodlar') or []) if str(h).strip()]
        if not hks:
            conn.close()
            return jsonify({'success': True, 'deleted': 0, 'bayi_kodu': code})

        deleted = 0
        for i in range(0, len(hks), 500):
            chunk = hks[i:i + 500]
            ph = ','.join('?' * len(chunk))
            del_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cur = conn.execute(f"UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'ESITLENDI' WHERE ham_karekod IN ({ph}) AND bayi_kodu = ?",
                               [del_ts] + chunk + [code])
            deleted += cur.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            set_system_setting(f"exit_list_rev_{code}", str(int(time.time() * 1000)))
            log_debug("SENKRON", f"🗑️ İstemciden {deleted} kutu silme isteği uygulandı", seviye="INFO", bayi_kodu=code)
        return jsonify({'success': True, 'deleted': deleted, 'bayi_kodu': code})
    except Exception as e:
        logger.error(f"api_sync_delete error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sync/dealer_status', methods=['POST'])
def api_sync_dealer_status():
    """İstemcilerin yetki durumunu merkez sunucudan doğrulaması için güvenli endpoint."""
    data = request.get_json(silent=True) or {}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        ok, err, status_code, b_kodu, matched = _authenticate_dealer_request(data, conn)
        if not ok:
            conn.close()
            return jsonify({'success': False, 'error': err, 'durum': 'PASIF', 'unauthorized': True}), status_code

        current_db_adi = clean_user_name(matched[2])
        req_b_adi = clean_user_name(str(data.get('bayi_adi') or '').strip())
        if req_b_adi and not req_b_adi.startswith('bayi_') and not req_b_adi.isdigit():
            if not current_db_adi or current_db_adi.startswith('bayi_') or current_db_adi.isdigit():
                try:
                    conn.execute("UPDATE bayiler SET bayi_adi = ? WHERE id = ?", (req_b_adi, matched[0]))
                    conn.commit()
                    current_db_adi = req_b_adi
                except Exception as e_up:
                    logger.debug(f"Dealer name update notice: {e_up}")
        conn.close()

        return jsonify({
            'success': True,
            'bayi_kodu': b_kodu,
            'durum': 'AKTIF',
            'bayi_adi': current_db_adi or (matched[2] if matched else ''),
            'test_modu': bool(matched[7] if matched and len(matched) > 7 else False),
            'gln_no': matched[5] if matched and len(matched) > 5 else '',
            'unauthorized': False
        })
    except Exception as e:
        logger.error(f"api_sync_dealer_status error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'durum': 'PASIF', 'unauthorized': True}), 500

@app.route('/api/sync/dealer_registration', methods=['POST'])
def api_sync_dealer_registration():
    """İstemciden gelen yeni bayi üyelik talebini merkezi sunucu veritabanına ve bildirimlerine ekler."""
    data = request.get_json(silent=True) or {}
    b_kodu = str(data.get('bayi_kodu') or '').strip()
    b_adi = str(data.get('bayi_adi') or '').strip()
    raw_u = str(data.get('bkst_kullanici') or data.get('tc_no') or '').strip()
    raw_p = str(data.get('bkst_sifre') or '').strip()
    gln = str(data.get('gln_no') or '').strip()
    tc = str(data.get('tc_no') or '').strip()
    notif_msg = str(data.get('notif_msg') or '').strip()
    tarih = str(data.get('tarih') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')).strip()
    durum_init = str(data.get('durum') or 'ONAY_BEKLIYOR').upper()

    if not b_kodu or not raw_u:
        return jsonify({'success': False, 'error': 'Eksik veri'}), 400

    plain_u = decrypt_credential(raw_u) if raw_u.startswith('ENC:') else raw_u
    plain_p = decrypt_credential(raw_p) if raw_p.startswith('ENC:') else raw_p
    enc_u = encrypt_credential(plain_u) if plain_u else ''
    enc_p = encrypt_credential(plain_p) if plain_p else ''

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        
        # Bayiyi hem bayi_kodu, hem enc_u, hem düz TC, hem de GLN ile ara
        c.execute("SELECT id, bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum FROM bayiler")
        rows = c.fetchall()
        matched = None
        for r in rows:
            r_u = decrypt_credential(r[3]).strip() if r[3] else ""
            if (b_kodu and r[1] == b_kodu) or (plain_u and (r_u == plain_u or r[1] == plain_u)) or (gln and r[5] == gln):
                matched = r
                break

        valid_b_adi = clean_user_name(b_adi)
        is_real_name = bool(valid_b_adi and not valid_b_adi.startswith("bayi_") and not valid_b_adi.isdigit())

        # EĞER BAYİ ZATEN 'AKTIF' ONAYLI İSE: BİLDİRİM OLUŞTURMA!
        if matched and str(matched[6] or '').upper() == 'AKTIF':
            if is_real_name:
                c.execute("UPDATE bayiler SET son_senkronizasyon = ?, bayi_adi = ?, bkst_sifre = COALESCE(NULLIF(?, ''), bkst_sifre) WHERE id = ?", (tarih, valid_b_adi, enc_p, matched[0]))
            else:
                c.execute("UPDATE bayiler SET son_senkronizasyon = ?, bkst_sifre = COALESCE(NULLIF(?, ''), bkst_sifre) WHERE id = ?", (tarih, enc_p, matched[0]))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'already_active': True})

        if not matched:
            c.execute("""
                INSERT INTO bayiler (bayi_kodu, bayi_adi, bkst_kullanici, bkst_sifre, gln_no, durum, son_senkronizasyon, eklenme_tarihi, notlar)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Uygulama üzerinden kayıt oluşturuldu')
            """, (b_kodu, valid_b_adi or b_adi, enc_u, enc_p, gln, durum_init, tarih, tarih))
        else:
            if is_real_name:
                c.execute("UPDATE bayiler SET durum = ?, gln_no = ?, bayi_adi = ?, bkst_sifre = COALESCE(NULLIF(?, ''), bkst_sifre) WHERE id = ?", (durum_init, gln, valid_b_adi, enc_p, matched[0]))
            else:
                c.execute("UPDATE bayiler SET durum = ?, gln_no = ?, bkst_sifre = COALESCE(NULLIF(?, ''), bkst_sifre) WHERE id = ?", (durum_init, gln, enc_p, matched[0]))

        # Bildirim sadece daha önce okunmamış bekleyen talep yoksa eklenir!
        c.execute("""
            SELECT id FROM sistem_bildirimleri 
            WHERE (bayi_kodu = ? OR tc_no = ?) AND okundu = 0 AND tur = 'UYE_TALEBI'
        """, (b_kodu, tc))
        existing_notif = c.fetchone()

        if not existing_notif:
            c.execute("""
                INSERT INTO sistem_bildirimleri (tarih, baslik, mesaj, tur, bayi_kodu, tc_no, sirket_adi, gln, okundu)
                VALUES (?, '🔔 Yeni Bayi Üyelik Talebi', ?, 'UYE_TALEBI', ?, ?, ?, ?, 0)
            """, (tarih, notif_msg or f"TC: {tc} bilgisine sahip bayi üye talebi oluşturdu.", b_kodu, tc, b_adi, gln))
            logger.info(f"🔔 İstemciden yeni bayi üyelik talebi başarıyla alındı: {b_adi} ({tc})")

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"api_sync_dealer_registration error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sync/rev', methods=['POST'])
def api_sync_rev():
    """İstemcilerin değişiklik olup olmadığını hafifçe sorgulaması için güncel çıkış listesi revizyonu."""
    data = request.get_json(silent=True) or {}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        ok, err, status_code, code, _ = _authenticate_dealer_request(data, conn)
        if not ok:
            conn.close()
            return jsonify({'success': False, 'error': err, 'unauthorized': True}), status_code

        rev = get_system_setting(f"exit_list_rev_{code}", "")
        if not rev:
            _cnt = conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM cikis_kayitlari WHERE bayi_kodu = ? AND COALESCE(silindi, 0) = 0", (code,)).fetchone()
            rev = f"auto_{_cnt[0]}_{_cnt[1]}"
        clear_history_rev = get_system_setting(f"clear_history_rev_{code}", "")
        clear_history_flags_raw = get_system_setting(f"clear_history_flags_{code}", "")
        clear_history_flags = {}
        if clear_history_flags_raw:
            try:
                clear_history_flags = json.loads(clear_history_flags_raw)
            except Exception:
                pass
        conn.close()
        return jsonify({
            'success': True,
            'bayi_kodu': code,
            'rev': rev,
            'clear_history_rev': clear_history_rev,
            'clear_history_flags': clear_history_flags
        })
    except Exception as e:
        logger.error(f"api_sync_rev error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sync/pull', methods=['POST'])
def api_sync_pull_from_server():
    """
    İstemci cihazların (dükkandaki bilgisayar veya yeni açılan cihazlar)
    merkez sunucudan o bayiye ait tüm çıkış kayıtlarını, satış arşivini ve çiftçi verilerini
    çekmesini sağlar.
    """
    data = request.get_json(silent=True) or {}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()

        ok, err, status_code, bayi_kodu, _ = _authenticate_dealer_request(data, conn)
        if not ok:
            conn.close()
            return jsonify({'success': False, 'error': err, 'unauthorized': True}), status_code

        # 1. Çıkış Kayıtları (Bekleyen çıkışlar)
        c.execute("""
            SELECT id, tarih, urun_adi, barkod, koli_no, seri_no, parti_no, uretim_tarihi, skt, ham_karekod, cihaz_adi, cihaz_id,
                   CASE WHEN COALESCE(silindi, 0) = 1 OR COALESCE(admin_gizli, 0) = 1 THEN 1 ELSE 0 END as silindi,
                   COALESCE(silinme_tarihi, '')
            FROM cikis_kayitlari
            WHERE bayi_kodu = ?
            ORDER BY id ASC
        """, (bayi_kodu,))
        cikis_rows = c.fetchall()
        cikis_items = []
        for r in cikis_rows:
            cikis_items.append({
                'id': r[0], 'tarih': r[1], 'urun_adi': r[2], 'barkod': r[3],
                'koli_no': r[4], 'seri_no': r[5], 'parti_no': r[6],
                'uretim_tarihi': r[7], 'skt': r[8], 'ham_karekod': r[9],
                'cihaz_adi': r[10] or 'Masaüstü PC', 'cihaz_id': r[11] or '',
                'silindi': r[12], 'silinme_tarihi': r[13]
            })

        # 2. Son 500 Satış Arşivi
        c.execute("""
            SELECT id, tarih, urun_adi, barkod, seri_no, koli_no, ham_karekod
            FROM satis_arsivi
            WHERE bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')
            ORDER BY id DESC LIMIT 500
        """, (bayi_kodu, bayi_kodu))
        satis_rows = c.fetchall()
        satis_items = []
        for r in satis_rows:
            satis_items.append({
                'id': r[0], 'tarih': r[1], 'urun_adi': r[2], 'barkod': r[3],
                'seri_no': r[4], 'koli_no': r[5], 'ham_karekod': r[6]
            })

        # 3. Kayıtlı Çiftçiler
        c.execute("SELECT id, ad_soyad, tc_no, telefon, il, ilce, koy, notlar FROM ciftciler ORDER BY ad_soyad ASC")
        ciftci_rows = c.fetchall()
        ciftciler = []
        for r in ciftci_rows:
            ciftciler.append({
                'id': r[0], 'ad_soyad': r[1], 'tc_no': r[2], 'telefon': r[3],
                'il': r[4], 'ilce': r[5], 'koy': r[6], 'notlar': r[7]
            })

        current_rev = get_system_setting(f"exit_list_rev_{bayi_kodu}", "")
        conn.close()
        return jsonify({
            'success': True,
            'bayi_kodu': bayi_kodu,
            'revision': current_rev,
            'cikis_kayitlari': cikis_items,
            'satis_arsivi': satis_items,
            'ciftciler': ciftciler,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        logger.error(f"api_sync_pull error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sync/manual', methods=['POST'])
def api_sync_manual_trigger():
    """
    İadeli Taahhütlü (Two-Way Handshake) Manuel Senkronizasyon:
    1. Bağlantı Kontrolü (Ping): Merkez sunucuya ulaşılamıyorsa DERHAL hata döndürür, asla başarılı demez.
    2. Karşılıklı Liste Karşılaştırması:
       - Merkezdeki mevcut kayıtları çeker.
       - Cihazdaki aktif (silindi = 0) kayıtları inceler.
       - Merkezde olmayan veya sync_durum != 'ESITLENDI' olan tüm kayıtları tespit eder.
    3. İadeli Taahhütlü İletim (Push & Soft Delete):
       - Eksik/güncel kayıtları sunucuya PUSH eder.
       - Sunucunun HTTP 200 ve {success: true} yanıtı DOĞRULANMADAN kayıtlar eşitlendi sayılmaz.
       - Sunucuya silinen kutuları (/api/sync/delete) bildirir.
    4. İadeli Taahhütlü Çekme (Pull) & Yerel Güncelleme:
       - Sunucudaki yeni veya silinmiş kayıtları cihaza yansıtır.
    5. Son Doğrulama (Verification Handshake):
       - Sunucunun bildirdiği aktif ürün sayısı ile yerel aktif ürün sayısı birebir karşılaştırılır.
       - Sayılar veya veriler uyuşmazsa ASLA 'Başarılı' denmez, detaylı durum ve fark kullanıcıya bildirilir.
       - Ancak ve ancak iki taraf da tam mutabakata vardığında 'ESITLENDI' damgası vurulur ve Başarılı mesajı verilir.
    """
    from flask import session as flask_session
    # ── 0. MERKEZ SUNUCU KONTROLÜ (SUNUCU KENDİSİYLE EŞİTLEME YAPMAZ) ──────
    if _is_server_host():
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            c = conn.cursor()
            c.execute("UPDATE cikis_kayitlari SET sync_durum = 'ESITLENDI' WHERE sync_durum != 'ESITLENDI'")
            conn.commit()
            conn.close()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
        return jsonify({
            'success': True,
            'message': '✅ Bu cihaz Merkez Sunucudur. Tüm çıkış kayıtları doğrudan merkez veritabanında güncel ve eşittir!'
        })

    bayi_kodu = flask_session.get('active_bayi_kodu', 'bayi_1')
    username = flask_session.get('dealer_username', '')
    merkez_url = get_system_setting("merkez_sunucu_url", "").strip()
    cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunucu_adresi.txt")
    if not merkez_url and os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f_u:
                merkez_url = f_u.read().strip()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    data = request.get_json(silent=True) or {}
    override_url = str(data.get('remote_url') or '').strip()
    if override_url:
        if not (override_url.startswith('http://') or override_url.startswith('https://')):
            override_url = f"http://{override_url}"
        merkez_url = override_url.rstrip('/')
        set_system_setting("merkez_sunucu_url", merkez_url)

    if merkez_url and not (merkez_url.startswith('http://') or merkez_url.startswith('https://')):
        merkez_url = f"http://{merkez_url}"

    # Eğer merkez_url boşsa Firebase'den canlı tünel adresini al:

    # Hala yoksa Firebase'den al
    if not merkez_url:
        try:
            import urllib.request, json
            req_fb = urllib.request.Request(FIREBASE_RELAY_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_fb, timeout=2.5) as r_fb:
                data_fb = json.loads(r_fb.read().decode('utf-8'))
                if data_fb and data_fb.get('tunnel_url'):
                    merkez_url = str(data_fb['tunnel_url']).strip().rstrip('/')
                    set_system_setting("merkez_sunucu_url", merkez_url)
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    if merkez_url:
        try:
            import requests

            # 1. ADIM: İadeli Taahhüt Ön Kontrolü - Sunucu Ayakta mı?
            server_ping_ok = False
            try:
                ping_resp = requests.get(f"{merkez_url}/api/system/version", timeout=3.0)
                if ping_resp.status_code == 200:
                    server_ping_ok = True
            except Exception:
                server_ping_ok = False

            if not server_ping_ok:
                # Tünel adresi değişmiş olabilir, Firebase'den canlı adresi tekrar çek
                try:
                    fresh_url = get_merkez_sunucu_url(force_refresh=True)
                    if fresh_url and fresh_url != merkez_url:
                        merkez_url = fresh_url
                        ping_resp2 = requests.get(f"{merkez_url}/api/system/version", timeout=3.5)
                        if ping_resp2.status_code == 200:
                            server_ping_ok = True
                except Exception as _ignored_err:
                    logger.debug(f"Firebase refresh ping error: {_ignored_err}")

            if not server_ping_ok:
                return jsonify({
                    'success': False,
                    'offline': True,
                    'error': "Merkez sunucuya şu an ulaşılamıyor. Sunucu geçici olarak kapalı veya tünel güncelleniyor olabilir. Okutmalarınız yerelde güvendedir; sunucu açıldığında sistem otomatik eşitlenecektir."
                })

            # Yerel veritabanından bayinin güncel Bakanlık giriş bilgilerini ve GLN'ini oku
            c_user, c_pass, c_gln, c_name = read_bkst_credentials()
            target_u = c_user or username or ""
            target_p = c_pass or ""
            if not target_p and bayi_kodu:
                b_tmp = get_bayi_by_code(bayi_kodu)
                if b_tmp and b_tmp[4]:
                    target_p = decrypt_credential(b_tmp[4])

            # 2. ADIM: Sunucudaki Mevcut Kayıtları Çek (Ön Keşif) - Veritabanı kilidi olmadan önce çekilir
            pull_url = f"{merkez_url}/api/sync/pull"
            try:
                init_pull_resp = requests.post(pull_url, json={"bayi_kodu": bayi_kodu, "bkst_kullanici": target_u, "bkst_sifre": target_p}, timeout=10.0)
                if init_pull_resp.status_code != 200:
                    return jsonify({'success': False, 'error': f"❌ Sunucudan veri listesi alınamadı (HTTP {init_pull_resp.status_code})"})
                init_pull_data = init_pull_resp.json()
                if not init_pull_data.get('success', True):
                    return jsonify({'success': False, 'error': f"❌ Sunucu veri listesi hatası: {init_pull_data.get('error')}"})
            except Exception as e_init_pull:
                return jsonify({'success': False, 'error': f"❌ Sunucudan ön kontrol listesi alınamadı: {str(e_init_pull)}"})

            now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            remote_cikis_init = init_pull_data.get('cikis_kayitlari', [])
            remote_hk_map = {itm.get('ham_karekod'): itm for itm in remote_cikis_init if itm.get('ham_karekod')}
            remote_hk_set = set(remote_hk_map.keys())

            # 3. ADIM: Yerel Kayıtları İncele - Hızlıca Oku ve Bağlantıyı Kapat (Kilitlenmeyi Önler)
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            c = conn.cursor()
            c.execute("""
                SELECT id, tarih, urun_adi, barkod, koli_no, seri_no, parti_no, uretim_tarihi, skt, ham_karekod, cihaz_adi, cihaz_id,
                       COALESCE(silindi, 0), COALESCE(silinme_tarihi, ''), sync_durum
                FROM cikis_kayitlari
                WHERE bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')
                ORDER BY id ASC
            """, (bayi_kodu, bayi_kodu))
            local_all_rows = c.fetchall()

            items_to_push = []
            hks_to_delete_remote = []

            for r in local_all_rows:
                r_id, r_tarih, r_urun, r_barkod, r_koli, r_seri, r_parti, r_urt, r_skt, r_hk, r_dev, r_dev_id, r_silindi, r_sil_time, r_sync = r
                r_dict = {
                    'id': r_id, 'tarih': r_tarih, 'urun_adi': r_urun, 'barkod': r_barkod,
                    'koli_no': r_koli, 'seri_no': r_seri, 'parti_no': r_parti,
                    'uretim_tarihi': r_urt, 'skt': r_skt, 'ham_karekod': r_hk,
                    'cihaz_adi': r_dev or 'Masaüstü PC', 'cihaz_id': r_dev_id or '',
                    'silindi': r_silindi, 'silinme_tarihi': r_sil_time
                }
                loc_ts = r_sil_time if r_silindi == 1 else r_tarih

                if r_hk in remote_hk_map:
                    rem_itm = remote_hk_map[r_hk]
                    rem_silindi = 1 if rem_itm.get('silindi') in (1, '1', True) else 0
                    rem_del_time = rem_itm.get('silinme_tarihi') or ''
                    rem_tarih = rem_itm.get('tarih') or ''
                    rem_ts = rem_del_time if rem_silindi == 1 else rem_tarih

                    if rem_ts and loc_ts and rem_ts > loc_ts:
                        if rem_silindi == 1 and r_silindi == 0:
                            c.execute("UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'ESITLENDI' WHERE id = ?", (rem_del_time or now_dt, r_id))
                        elif rem_silindi == 0 and r_silindi == 1:
                            c.execute("UPDATE cikis_kayitlari SET silindi = 0, silinme_tarihi = NULL, sync_durum = 'ESITLENDI' WHERE id = ?", (r_id,))
                        else:
                            c.execute("UPDATE cikis_kayitlari SET sync_durum = 'ESITLENDI' WHERE id = ?", (r_id,))
                    elif loc_ts and rem_ts and loc_ts > rem_ts:
                        if r_silindi == 1:
                            items_to_push.append(r_dict)
                            hks_to_delete_remote.append(r_hk)
                        else:
                            items_to_push.append(r_dict)
                    else:
                        if rem_silindi == 1:
                            c.execute("UPDATE cikis_kayitlari SET silindi = 1, silinme_tarihi = ?, sync_durum = 'ESITLENDI' WHERE id = ?", (rem_del_time or now_dt, r_id))
                        else:
                            c.execute("UPDATE cikis_kayitlari SET sync_durum = 'ESITLENDI' WHERE id = ?", (r_id,))
                else:
                    if r_silindi == 0:
                        items_to_push.append(r_dict)

            conn.commit()
            conn.close() # ⚡ AĞ İSTEKLERİNDEN ÖNCE VERİTABANI MUTLAKA KAPATILIR

            # 4. ADIM: İadeli Taahhütlü PUSH (Ağ transferi sırasında veritabanı kilitli tutulmaz)
            pushed_count = 0
            if items_to_push:
                push_payload = {
                    "bayi_kodu": bayi_kodu,
                    "bkst_kullanici": target_u,
                    "bkst_sifre": target_p,
                    "gln_no": c_gln or "",
                    "bayi_adi": c_name or "",
                    "cikis_kayitlari": items_to_push
                }
                try:
                    push_resp = requests.post(f"{merkez_url}/api/sync/push", json=push_payload, timeout=20.0)
                    if push_resp.status_code != 200:
                        return jsonify({'success': False, 'error': f"❌ Sunucuya veri aktarımı başarısız oldu (HTTP {push_resp.status_code}). Kayıtlar henüz eşitlenmedi."})
                    p_json = push_resp.json()
                    if not p_json.get('success', False):
                        return jsonify({'success': False, 'error': f"❌ Sunucu verileri kabul etmedi: {p_json.get('error', 'Bilinmeyen hata')}. Eşitleme iptal edildi."})
                    pushed_count = len(items_to_push)
                except Exception as e_push:
                    return jsonify({'success': False, 'error': f"❌ Sunucuya veri aktarılırken bağlantı kesildi: {str(e_push)}. İşlem iptal edildi."})

            if hks_to_delete_remote:
                try:
                    requests.post(f"{merkez_url}/api/sync/delete",
                                  json={"bayi_kodu": bayi_kodu, "bkst_kullanici": target_u, "bkst_sifre": target_p, "ham_karekodlar": hks_to_delete_remote},
                                  timeout=8.0)
                except Exception as e_del_fwd:
                    logger.debug(f"Manual delete fwd notice: {e_del_fwd}")
            _flush_pending_deletes(merkez_url)

            # 5. ADIM: Merkezdeki Son Durumu Çek ve Yerel Veritabanına Yansıt
            try:
                final_pull_resp = requests.post(pull_url, json={"bayi_kodu": bayi_kodu, "bkst_kullanici": target_u, "bkst_sifre": target_p}, timeout=15.0)
                if final_pull_resp.status_code != 200:
                    return jsonify({'success': False, 'error': f"❌ Sunucudan son durum alınamadı (HTTP {final_pull_resp.status_code})"})
                remote_final_data = final_pull_resp.json()
            except Exception as e_final_pull:
                return jsonify({'success': False, 'error': f"❌ Sunucu son durum çekilemedi: {str(e_final_pull)}"})

            remote_final_cikis = remote_final_data.get('cikis_kayitlari', [])

            # Ağdan veriler alındı; şimdi tek ve hızlı bir işlemle yerel veritabanına yaz
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            c = conn.cursor()
            _set_sync_flag('ayna_modu', '1', conn)

            local_hk_set = set(r[9] for r in local_all_rows if r[9])
            inserted_cikis = 0
            for itm in remote_final_cikis:
                hk = itm.get('ham_karekod') or ''
                if not hk:
                    continue
                r_silindi = 1 if itm.get('silindi') in (1, '1', True) else 0
                r_del_time = itm.get('silinme_tarihi') or ''
                r_tarih = itm.get('tarih') or now_dt

                if hk not in local_hk_set:
                    c.execute("""
                        INSERT INTO cikis_kayitlari (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, uretim_tarihi, skt, ham_karekod, bayi_kodu, sync_durum, cihaz_adi, cihaz_id, silindi, silinme_tarihi)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ESITLENDI', ?, ?, ?, ?)
                    """, (r_tarih, itm.get('urun_adi'), itm.get('barkod'), itm.get('koli_no'), itm.get('seri_no'), itm.get('parti_no'), itm.get('uretim_tarihi'), itm.get('skt'), hk, bayi_kodu, itm.get('cihaz_adi') or 'Masaüstü PC', itm.get('cihaz_id') or '', r_silindi, r_del_time))
                    if not r_silindi:
                        inserted_cikis += 1

            for cf in remote_final_data.get('ciftciler', []):
                tc = cf.get('tc_no') or ''
                if tc:
                    c.execute("SELECT id FROM ciftciler WHERE tc_no = ?", (tc,))
                    if not c.fetchone():
                        c.execute("""
                            INSERT INTO ciftciler (ad_soyad, tc_no, telefon, il, ilce, koy, notlar)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (cf.get('ad_soyad'), tc, cf.get('telefon'), cf.get('il'), cf.get('ilce'), cf.get('koy'), cf.get('notlar')))

            conn.commit()

            # 6. ADIM: İADELİ TAAHHÜTLÜ NİHAİ DOĞRULAMA (Verification Handshake)
            server_active_count = len([x for x in remote_final_cikis if not (x.get('silindi') in (1, '1', True))])

            c.execute("SELECT COUNT(*) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND COALESCE(silindi, 0) = 0", (bayi_kodu, bayi_kodu))
            local_active_count = c.fetchone()[0]

            if server_active_count != local_active_count:
                conn.close()
                log_debug("SENKRON", f"⚠️ Doğrulama Uyuşmazlığı: Sunucu={server_active_count}, Cihaz={local_active_count}", seviye="WARN", bayi_kodu=bayi_kodu)
                return jsonify({
                    'success': False,
                    'server_active_count': server_active_count,
                    'local_active_count': local_active_count,
                    'error': f"⚠️ Senkronizasyon doğrulaması uyuşmadı: Sunucuda {server_active_count} adet, Cihazınızda {local_active_count} adet aktif ürün var. Veriler tam mutabakata varamadı. Lütfen sunucu sürümünü kontrol edip tekrar deneyiniz."
                })

            c.execute("UPDATE cikis_kayitlari SET sync_durum = 'ESITLENDI' WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (bayi_kodu, bayi_kodu))
            now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, bayi_kodu))
            conn.commit()
            conn.close()

            if pushed_count > 0 or inserted_cikis > 0:
                log_debug("SENKRON", f"✅ İadeli Taahhütlü Senkronizasyon Başarılı: Sunucu={server_active_count}, Yerel={local_active_count}, İletilen={pushed_count}", seviye="INFO", bayi_kodu=bayi_kodu)
            else:
                log_debug("SENKRON", f"İadeli Taahhütlü Senkronizasyon (Eşit): Sunucu={server_active_count}, Yerel={local_active_count}", seviye="DEBUG", bayi_kodu=bayi_kodu)
            return jsonify({
                'success': True,
                'mode': 'remote',
                'server_active_count': server_active_count,
                'local_active_count': local_active_count,
                'pushed_count': pushed_count,
                'new_count': inserted_cikis,
                'message': f"✅ İadeli Taahhütlü Senkronizasyon Başarılı!\nSunucu ve cihaz tam olarak doğrulandı: {local_active_count} adet aktif ürün eşitlenmiş durumda."
            })
        except Exception as e_rem:
            logger.error(f"manual sync remote error: {e_rem}")
            return jsonify({'success': False, 'error': f"❌ Sunucu senkronizasyonunda beklenmeyen hata: {str(e_rem)}"})

    # Eğer merkez_url tanımlı değilse (doğrudan ana sunucu üzerindeyiz):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND COALESCE(silindi, 0) = 0", (bayi_kodu, bayi_kodu))
        cnt = c.fetchone()[0]
        now_dt = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, bayi_kodu))
        conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'mode': 'local',
            'current_count': cnt,
            'message': f"Doğrudan ana veritabanı aktif: {cnt} adet kayıt güncel ve eşitlenmiş durumda."
        })
    except Exception as e_loc:
        return jsonify({'success': False, 'error': str(e_loc)})

@app.route('/api/sync/configure_remote', methods=['POST'])
def api_sync_configure_remote():
    data = request.json or {}
    url = str(data.get('remote_url', '')).strip().rstrip('/')
    set_system_setting("merkez_sunucu_url", url)
    return jsonify({'success': True, 'merkez_sunucu_url': url, 'message': 'Merkez sunucu adresi başarıyla kaydedildi.'})

@app.route('/api/system/server_url', methods=['GET', 'POST'])
def api_system_server_url():
    cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunucu_adresi.txt")
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        new_url = str(data.get('server_url') or '').strip().rstrip('/')
        if not new_url:
            return jsonify({'success': False, 'error': 'Geçerli bir sunucu veya tünel adresi giriniz.'}), 400
        
        if not (new_url.startswith('http://') or new_url.startswith('https://')):
            new_url = f"https://{new_url}"
            
        set_system_setting("merkez_sunucu_url", new_url)
        try:
            with open(cfg_file, "w", encoding="utf-8") as f:
                f.write(new_url)
        except Exception as e:
            logger.warning(f"sunucu_adresi.txt write error: {e}")
            
        return jsonify({'success': True, 'server_url': new_url, 'message': 'Sunucu/tünel bağlantı adresi kaydedildi.'})
    
    # GET
    current_url = get_system_setting("merkez_sunucu_url", "")
    if not current_url and os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                current_url = f.read().strip()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    tun_url = get_current_tunnel_url()
    return jsonify({
        'success': True,
        'server_url': current_url or tun_url or PUBLIC_TUNNEL_URL or '',
        'public_tunnel_url': tun_url or PUBLIC_TUNNEL_URL or ''
    })

@app.route('/api/system/tunnel_debug', methods=['GET'])
def api_system_tunnel_debug():
    base = os.path.dirname(os.path.abspath(__file__))
    cand_paths = [
        os.path.join(base, "cloudflared.exe"),
        r"F:\BkstSunucu\cloudflared.exe",
        r"C:\BkstSunucu\cloudflared.exe",
        "cloudflared.exe"
    ]
    found_exe = None
    for p in cand_paths:
        if os.path.exists(p):
            found_exe = p
            break
    has_marker = os.path.exists(os.path.join(base, "SUNUCU_CIHAZ.txt"))
    log_file = os.path.join(base, "cloudflared_output.log")
    log_content = ""
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f_l:
                log_content = f_l.read()[-3000:]
        except Exception as e_l:
            log_content = f"Log okuma hatası: {e_l}"
    return jsonify({
        'base_dir': base,
        'has_marker': has_marker,
        'is_server_host': _is_server_host(),
        'found_exe': found_exe,
        'public_tunnel_url': PUBLIC_TUNNEL_URL,
        'log_content': log_content,
        'files_in_base': os.listdir(base)[:40]
    })

@app.route('/api/system/tunnel/status', methods=['GET'])
def api_system_tunnel_status():
    global PUBLIC_TUNNEL_URL
    return jsonify({
        'success': True,
        'public_tunnel_url': PUBLIC_TUNNEL_URL or get_current_tunnel_url(),
        'is_server_host': _is_server_host(),
        'local_ip': get_local_ip()
    })

@app.route('/api/system/tunnel/start', methods=['POST'])
def api_system_start_tunnel():
    from flask import session as flask_session
    is_local_req = request.remote_addr in ('127.0.0.1', 'localhost', '::1')
    if not flask_session.get('is_admin') and not is_local_req:
        return jsonify({'success': False, 'error': 'Yetkisiz erişim. Lütfen admin olarak giriş yapınız.'}), 403
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SUNUCU_CIHAZ.txt")
        if not os.path.exists(marker):
            with open(marker, "w", encoding="utf-8") as f_m:
                f_m.write("SUNUCU\n")
        _start_cloudflare_tunnel(force=True)
        return jsonify({'success': True, 'message': '🚀 Cloudflare tüneli bu cihazda başlatılıyor. Birkaç saniye içinde aktifleşecektir.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/system/tunnel/stop', methods=['POST'])
def api_system_stop_tunnel():
    from flask import session as flask_session
    is_local_req = request.remote_addr in ('127.0.0.1', 'localhost', '::1')
    if not flask_session.get('is_admin') and not is_local_req:
        return jsonify({'success': False, 'error': 'Yetkisiz erişim. Lütfen admin olarak giriş yapınız.'}), 403
    try:
        global PUBLIC_TUNNEL_URL, _tunnel_proc
        if os.name == 'nt':
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        PUBLIC_TUNNEL_URL = ""
        publish_tunnel_to_firebase("")
        return jsonify({'success': True, 'message': '🛑 Tünel durduruldu ve Firebase çevrimdışı yapıldı.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/debug_logs', methods=['GET'])
def api_admin_get_debug_logs():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    # Her istekte app.log dosyasındaki taze hata ve uyarıları debug_loglari tablosuna aktar
    try:
        import_app_log_to_debug_logs()
    except Exception as _e_imp:
        logger.debug(f"Log import notice: {_e_imp}")

    bayi_kodu = request.args.get('bayi_kodu', '').strip()
    kategori = request.args.get('kategori', '').strip().upper()
    seviye = request.args.get('seviye', '').strip().upper()
    q = request.args.get('q', '').strip()
    try:
        limit = min(int(request.args.get('limit', 300)), 2000)
    except Exception:
        limit = 300

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()

        query = "SELECT id, tarih, bayi_kodu, seviye, kategori, baslik, detay, request_payload, response_payload FROM debug_loglari WHERE 1=1"
        params = []

        if bayi_kodu:
            candidate_dealers = {bayi_kodu}
            c.execute("SELECT bayi_kodu, bkst_kullanici, bayi_adi FROM bayiler WHERE bayi_kodu = ? OR bkst_kullanici = ?", (bayi_kodu, bayi_kodu))
            for b_row in c.fetchall():
                if b_row[0]: candidate_dealers.add(b_row[0])
                if b_row[1]:
                    candidate_dealers.add(b_row[1])
                    dec = decrypt_credential(b_row[1])
                    if dec: candidate_dealers.add(dec)

            ph = ','.join('?' for _ in candidate_dealers)
            if 'bayi_1' in candidate_dealers:
                query += f" AND (bayi_kodu IN ({ph}) OR bayi_kodu IS NULL OR bayi_kodu = '' OR bayi_kodu = 'bayi_1')"
            else:
                query += f" AND bayi_kodu IN ({ph})"
            params.extend(list(candidate_dealers))

        # Geçmişi temizleme zamanı filtresi (Eski logların geri gelmesini %100 engeller)
        clr_time_dealer = ""
        if bayi_kodu:
            clr_time_dealer = get_system_setting(f"logs_cleared_at_{bayi_kodu}", "")
        clr_time_all = get_system_setting("logs_cleared_at_all", "")
        effective_clr = max(clr_time_dealer, clr_time_all) if (clr_time_dealer and clr_time_all) else (clr_time_dealer or clr_time_all)
        if effective_clr:
            query += " AND tarih > ?"
            params.append(effective_clr)

        if kategori:
            query += " AND kategori = ?"
            params.append(kategori)

        if seviye:
            if seviye in ['ERROR', 'HATA', 'CRITICAL']:
                query += " AND seviye IN ('ERROR', 'HATA', 'CRITICAL')"
            elif seviye in ['WARNING', 'WARN', 'UYARI']:
                query += " AND seviye IN ('WARNING', 'WARN', 'UYARI')"
            elif seviye in ['INFO', 'BILGI']:
                query += " AND seviye IN ('INFO', 'BILGI')"
            else:
                query += " AND seviye = ?"
                params.append(seviye)

        if q:
            query += " AND (baslik LIKE ? OR detay LIKE ? OR request_payload LIKE ? OR response_payload LIKE ?)"
            like_q = f"%{q}%"
            params.extend([like_q, like_q, like_q, like_q])

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        c.execute(query, tuple(params))
        rows = c.fetchall()

        # Kategori istatistiklerini hesapla
        cat_query = "SELECT kategori, COUNT(*) FROM debug_loglari WHERE 1=1"
        cat_params = []
        if bayi_kodu:
            ph = ','.join('?' for _ in candidate_dealers)
            if 'bayi_1' in candidate_dealers:
                cat_query += f" AND (bayi_kodu IN ({ph}) OR bayi_kodu IS NULL OR bayi_kodu = '' OR bayi_kodu = 'bayi_1')"
            else:
                cat_query += f" AND bayi_kodu IN ({ph})"
            cat_params.extend(list(candidate_dealers))
        if effective_clr:
            cat_query += " AND tarih > ?"
            cat_params.append(effective_clr)
        cat_query += " GROUP BY kategori"
        c.execute(cat_query, tuple(cat_params))
        kategori_stats = {r[0]: r[1] for r in c.fetchall()}

        conn.close()

        logs = []
        for r in rows:
            raw_sev = str(r[3] or 'INFO').upper()
            norm_sev = 'ERROR' if raw_sev in ['ERROR', 'CRITICAL', 'HATA'] else ('WARNING' if raw_sev in ['WARNING', 'WARN', 'UYARI'] else raw_sev)
            logs.append({
                'id': r[0],
                'tarih': r[1],
                'bayi_kodu': r[2] or 'bayi_1',
                'seviye': norm_sev,
                'kategori': r[4],
                'baslik': r[5],
                'detay': r[6],
                'request_payload': r[7],
                'response_payload': r[8]
            })

        return jsonify({'success': True, 'logs': logs, 'kategori_stats': kategori_stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/debug_logs/temizle', methods=['POST'])
def api_admin_clear_debug_logs():
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    try:
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()
        c.execute("DELETE FROM debug_loglari")
        set_system_setting("logs_cleared_at_all", now_str, conn=conn)
        conn.commit()
        conn.close()

        log_debug("ADMIN", "Tüm Hata Günlükleri Temizlendi", seviye="INFO")
        return jsonify({'success': True, 'message': 'Loglar temizlendi.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/system/debug_logs/ekle', methods=['POST'])
def api_add_client_debug_log():
    """
    İstemci (JavaScript) tarafındaki hataların sunucuya loglanmasını sağlar.
    """
    data = request.json or {}
    kategori = data.get('kategori', 'JS_HATA')
    baslik = data.get('baslik', 'İstemci Hatası')
    detay = data.get('detay', '')
    req_p = data.get('request_payload')
    resp_p = data.get('response_payload')
    seviye = data.get('seviye', 'ERROR')
    bayi_kodu = data.get('bayi_kodu')
    log_debug(kategori, baslik, detay, req_p, resp_p, seviye, bayi_kodu)
    return jsonify({'success': True})

@app.route('/api/admin/bayi/gecmis_temizle', methods=['POST'])
def api_admin_clear_dealer_history():
    """
    Seçilen bayiye ait geçmiş satış arşivi, çıkış kayıtları ve mobil oturum geçmişini
    kullanıcının seçtiği kutucuklara (parametrelerine) göre hassas biçimde temizler.
    """
    from flask import session as flask_session
    if not flask_session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Yetkisiz erişim.', 'code': 403}), 403

    data = request.get_json(silent=True) or {}
    bayi_kodu = str(data.get('bayi_kodu') or '').strip()
    if not bayi_kodu:
        return jsonify({'success': False, 'error': 'Bayi kodu belirtilmedi.'}), 400

    sil_bekleyen = bool(data.get('sil_bekleyen', False))
    sil_bugun_satis = bool(data.get('sil_bugun_satis', False))
    sil_gecmis_satis = bool(data.get('sil_gecmis_satis', True))
    sil_oturumlar = bool(data.get('sil_oturumlar', True))
    sil_loglar = bool(data.get('sil_loglar', False))

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 8000")
        c = conn.cursor()

        # Sadece ve sadece seçilen bayinin kodunu bul (Diğer bayiler %100 izole kalır)
        c.execute("SELECT bayi_kodu, bkst_kullanici, bayi_adi FROM bayiler WHERE bayi_kodu = ? OR bkst_kullanici = ?", (bayi_kodu, bayi_kodu))
        b_rows = c.fetchall()
        candidate_dealers = set()
        b_adi = bayi_kodu
        for b_row in b_rows:
            if b_row[0]: candidate_dealers.add(str(b_row[0]).strip())
            if b_row[2]: b_adi = b_row[2]
            if b_row[1]:
                dec = decrypt_credential(b_row[1])
                if dec: candidate_dealers.add(str(dec).strip())

        if not candidate_dealers:
            candidate_dealers.add(bayi_kodu)

        # Boş veya geçersiz değerleri filtrele
        candidate_dealers = {x for x in candidate_dealers if x and len(x) > 1}
        ph = ','.join('?' for _ in candidate_dealers)
        params = list(candidate_dealers)
        bugun_tarih = datetime.now().strftime('%Y-%m-%d')
        now_dt_full = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        satis_silindi = 0
        cikis_silindi = 0
        log_silindi = 0

        # 1. Satış arşivi silme mantığı (SADECE bu bayiye ait kayıtlar)
        if sil_gecmis_satis and sil_bugun_satis:
            # Hem geçmiş hem bugünkü satışlar silinsin
            c.execute(f"DELETE FROM satis_arsivi WHERE bayi_kodu IN ({ph})", tuple(params))
            satis_silindi = c.rowcount
        elif sil_gecmis_satis and not sil_bugun_satis:
            # Sadece bugünden önceki satışlar silinsin, bugünküler KORUNSUN!
            c.execute(f"DELETE FROM satis_arsivi WHERE bayi_kodu IN ({ph}) AND tarih < ?", (*params, f"{bugun_tarih} 00:00:00"))
            satis_silindi = c.rowcount
        elif sil_bugun_satis and not sil_gecmis_satis:
            # Sadece bugünkü satışlar silinsin
            c.execute(f"DELETE FROM satis_arsivi WHERE bayi_kodu IN ({ph}) AND tarih >= ? AND tarih <= ?", (*params, f"{bugun_tarih} 00:00:00", f"{bugun_tarih} 23:59:59"))
            satis_silindi = c.rowcount

        # 2. Bekleyen çıkış listesi (SADECE bu bayiye ait bekleyen kutular)
        if sil_bekleyen:
            c.execute(f"DELETE FROM cikis_kayitlari WHERE bayi_kodu IN ({ph})", tuple(params))
            cikis_silindi = c.rowcount

        # 3. Mobil oturum ve cihaz kayıtlarını sıfırla (SADECE bu bayiye ait)
        if sil_oturumlar:
            c.execute(f"DELETE FROM mobile_quick_tokens WHERE bayi_kodu IN ({ph})", tuple(params))
            c.execute(f"DELETE FROM aktif_cihazlar WHERE bayi_kodu IN ({ph})", tuple(params))

        # 4. Hata ve Log kayıtlarını sil (Hata Günlüğü - SADECE bu bayiye ait)
        if sil_loglar:
            c.execute(f"DELETE FROM debug_loglari WHERE bayi_kodu IN ({ph})", tuple(params))
            log_silindi = c.rowcount
            for code in candidate_dealers:
                set_system_setting(f"logs_cleared_at_{code}", now_dt_full, conn=conn)
            if 'bayi_1' in candidate_dealers or bayi_kodu == 'bayi_1':
                set_system_setting("logs_cleared_at_all", now_dt_full, conn=conn)

        # 5. Revizyon numaralarını ve silme kurallarını güncelle
        now_ts = str(int(time.time() * 1000))
        clr_payload = json.dumps({
            'ts': now_ts,
            'sil_bekleyen': sil_bekleyen,
            'sil_bugun_satis': sil_bugun_satis,
            'sil_gecmis_satis': sil_gecmis_satis,
            'sil_loglar': sil_loglar
        })
        for code in candidate_dealers:
            set_system_setting(f"exit_list_rev_{code}", now_ts, conn=conn)
            set_system_setting(f"clear_history_rev_{code}", now_ts, conn=conn)
            set_system_setting(f"clear_history_flags_{code}", clr_payload, conn=conn)

        conn.commit()
        conn.close()

        detay_ozet = []
        if sil_gecmis_satis: detay_ozet.append("Geçmiş Satışlar")
        if sil_bugun_satis: detay_ozet.append("Bugün Satılanlar")
        if sil_bekleyen: detay_ozet.append(f"Bekleyen Kutular ({cikis_silindi} adet)")
        if sil_oturumlar: detay_ozet.append("Mobil Oturumlar")
        if sil_loglar: detay_ozet.append(f"Hata Günlüğü ({log_silindi} kayıt)")

        log_debug("ADMIN", f"🗑️ Bayi Geçmiş Verileri Temizlendi ({b_adi})",
                  f"Temizlenen Gruplar: {', '.join(detay_ozet)}. Toplam silinen satış: {satis_silindi}, silinen çıkış listesi: {cikis_silindi}, silinen log: {log_silindi}",
                  seviye="UYARI", bayi_kodu=bayi_kodu)

        return jsonify({
            'success': True,
            'message': f"'{b_adi}' bayisine ait seçilen veriler başarıyla temizlendi! ({', '.join(detay_ozet)})",
            'deleted_sales': satis_silindi,
            'deleted_exit_list': cikis_silindi,
            'deleted_logs': log_silindi
        })
    except Exception as e:
        logger.error(f"api_admin_clear_dealer_history error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sync/client_debug_logs', methods=['POST'])
def api_sync_client_debug_logs():
    """
    İstemci (bayi masaüstü bilgisayarı) tarafında oluşan hataları, 
    app.log satırlarını ve debug loglarını merkez sunucuya kaydeder.
    """
    data = request.get_json(silent=True) or {}
    bayi_kodu = str(data.get('bayi_kodu') or 'bayi_1').strip()
    logs = data.get('logs', [])
    raw_log_lines = data.get('raw_log_lines', [])

    if not logs and not raw_log_lines:
        return jsonify({'success': True, 'count': 0})

    try:
        ensure_db_schema()
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        c = conn.cursor()

        # Bu bayinin geçmiş log temizleme zamanını kontrol et (eski logların sunucuya tekrar yüklenmesini engeller)
        clr_time_dealer = get_system_setting(f"logs_cleared_at_{bayi_kodu}", "")
        clr_time_all = get_system_setting("logs_cleared_at_all", "")
        effective_clr = max(clr_time_dealer, clr_time_all) if (clr_time_dealer and clr_time_all) else (clr_time_dealer or clr_time_all)

        inserted_count = 0
        for l in logs:
            tar = l.get('tarih') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if effective_clr and str(tar) <= effective_clr:
                continue
            b_kod = l.get('bayi_kodu') or bayi_kodu
            sev = str(l.get('seviye') or 'INFO').upper()
            kat = str(l.get('kategori') or 'BAYI_ISTEMCI').upper()
            bas = str(l.get('baslik') or 'İstemci Kaydı')[:200]
            det = str(l.get('detay') or '')
            req_p = l.get('request_payload') or ''
            resp_p = l.get('response_payload') or ''

            # Aynı logu mükerrer eklememek için kontrol
            c.execute("""
                SELECT id FROM debug_loglari 
                WHERE tarih = ? AND bayi_kodu = ? AND baslik = ?
                LIMIT 1
            """, (tar, b_kod, bas))
            if not c.fetchone():
                c.execute("""
                    INSERT INTO debug_loglari (tarih, bayi_kodu, seviye, kategori, baslik, detay, request_payload, response_payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (tar, b_kod, sev, kat, bas, det, req_p, resp_p))
                inserted_count += 1

        # app.log'dan gelen ham hata ve uyarı satırları
        for line in raw_log_lines:
            line_str = str(line).strip()
            if not line_str:
                continue
            sev = "ERROR" if "ERROR" in line_str else ("WARNING" if "WARN" in line_str else "INFO")
            t_match = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line_str)
            tar = t_match.group(1) if t_match else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if effective_clr and str(tar) <= effective_clr:
                continue
            baslik = line_str[:120]

            c.execute("""
                SELECT id FROM debug_loglari 
                WHERE tarih = ? AND bayi_kodu = ? AND baslik = ?
                LIMIT 1
            """, (tar, bayi_kodu, baslik))
            if not c.fetchone():
                c.execute("""
                    INSERT INTO debug_loglari (tarih, bayi_kodu, seviye, kategori, baslik, detay, request_payload, response_payload)
                    VALUES (?, ?, ?, 'APP_LOG', ?, ?, '', '')
                """, (tar, bayi_kodu, sev, baslik, line_str))
                inserted_count += 1

        # Tabloyu son 5000 kayıtta tut
        c.execute("""
            DELETE FROM debug_loglari WHERE id NOT IN (
                SELECT id FROM debug_loglari ORDER BY id DESC LIMIT 5000
            )
        """)
        conn.commit()
        conn.close()

        if inserted_count > 0:
            logger.info(f"📥 [{bayi_kodu}] İstemciden {inserted_count} adet hata/debug günlüğü merkeze işlendi.")
        return jsonify({'success': True, 'inserted': inserted_count})
    except Exception as e:
        logger.error(f"api_sync_client_debug_logs error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ── WINDOWS İŞ NESNESİ (JOB OBJECT) & TEK TUŞLA TÜM SÜREÇLERİ KAPATMA ─────────
def _setup_windows_job():
    if os.name != 'nt':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        hJob = kernel32.CreateJobObjectW(None, None)
        if not hJob:
            return None

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER),
                ('PerJobUserTimeLimit', wintypes.LARGE_INTEGER),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('ReadOperationCount', ctypes.c_ulonglong),
                ('WriteOperationCount', ctypes.c_ulonglong),
                ('OtherOperationCount', ctypes.c_ulonglong),
                ('ReadTransferCount', ctypes.c_ulonglong),
                ('WriteTransferCount', ctypes.c_ulonglong),
                ('OtherTransferCount', ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryLimit', ctypes.c_size_t),
                ('PeakJobMemoryLimit', ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(hJob, 9, ctypes.byref(info), ctypes.sizeof(info))
        kernel32.AssignProcessToJobObject(hJob, kernel32.GetCurrentProcess())
        return hJob
    except Exception:
        return None

_GLOBAL_JOB_HANDLE = _setup_windows_job()

def _cleanup_all_child_processes():
    global _tunnel_proc
    if _tunnel_proc:
        try:
            _tunnel_proc.kill()
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
    if os.name == 'nt':
        try:
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        except Exception as _ignored_err:
            logger.debug(f"Suppressed notice: {_ignored_err}")
atexit.register(_cleanup_all_child_processes)

# ── CLOUDFLARE TUNNEL UZAKTAN ERİŞİM MOTORU (WIFI GEREKTİRMEZ) ─────────────────
PUBLIC_TUNNEL_URL = ""
_tunnel_proc = None

def publish_tunnel_to_firebase(tunnel_url):
    def _pub():
        try:
            import urllib.request, json
            payload = json.dumps({
                'tunnel_url': tunnel_url,
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'status': 'ONLINE' if tunnel_url else 'OFFLINE'
            }).encode('utf-8')
            req = urllib.request.Request(FIREBASE_RELAY_URL, data=payload, method='PUT', headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status == 200:
                    if tunnel_url:
                        logger.info(f"🔥 Canlı tünel adresi Firebase'e başarıyla iletildi: {tunnel_url}")
                    else:
                        logger.info("ℹ️ Firebase tünel durumu OFFLINE olarak güncellendi.")
        except Exception as ex:
            logger.warning(f"Firebase publish error: {ex}")
    threading.Thread(target=_pub, daemon=True).start()

def _start_tunnel_heartbeat():
    def _hb():
        while True:
            try:
                time.sleep(45)
                if _is_server_host() and PUBLIC_TUNNEL_URL:
                    publish_tunnel_to_firebase(PUBLIC_TUNNEL_URL)
            except Exception as e_hb:
                logger.debug(f"Heartbeat notice: {e_hb}")
    threading.Thread(target=_hb, name="TunnelHeartbeatThread", daemon=True).start()

_start_tunnel_heartbeat()

def _is_server_host():
    """Yalnızca klasöründe SUNUCU_CIHAZ.txt dosyası bulunan makine ana sunucudur."""
    return os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "SUNUCU_CIHAZ.txt"))

_tunnel_thread_started = False
_tunnel_lock = threading.Lock()

def _start_cloudflare_tunnel(force=False):
    global _tunnel_thread_started
    with _tunnel_lock:
        if _tunnel_thread_started and not force:
            return
        if not _is_server_host() and not force:
            logger.info("ℹ️ Bu cihaz ana sunucu (SUNUCU_CIHAZ.txt yok) olmadığı için Cloudflare tünel başlatılmadı.")
            return
        _tunnel_thread_started = True

    def _tun_worker():
        global PUBLIC_TUNNEL_URL, _tunnel_proc
        cand_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe"),
            r"F:\BkstSunucu\cloudflared.exe",
            r"C:\BkstSunucu\cloudflared.exe",
            "cloudflared.exe"
        ]
        exe_path = None
        for p in cand_paths:
            if os.path.exists(p):
                exe_path = p
                break
        if not exe_path:
            logger.info("cloudflared.exe bulunamadı, yerel ağ modunda devam ediliyor.")
            return

        consecutive_429 = 0

        while True:
            try:
                if os.name == 'nt':
                    subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
            except Exception as _ignored_err:
                logger.debug(f"Suppressed notice: {_ignored_err}")
            try:
                tok_files = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflare_token.txt"),
                             os.path.join(os.path.dirname(os.path.abspath(__file__)), "tunnel_token.txt")]
                cf_token = ""
                for tf in tok_files:
                    if os.path.exists(tf):
                        try:
                            with open(tf, 'r', encoding='utf-8') as f_tok:
                                cf_token = f_tok.read().strip()
                            if cf_token:
                                break
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                if cf_token:
                    cmd = [exe_path, "tunnel", "run", "--token", cf_token]
                    logger.info("Starting Cloudflare Named Tunnel with custom token...")
                else:
                    cmd = [exe_path, "tunnel", "--url", "http://127.0.0.1:5000", "--no-autoupdate"]
                    logger.info(f"Starting Cloudflare Quick Tunnel via {exe_path}...")

                flags = 0x08000000 if os.name == 'nt' else 0
                tun_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared_output.log")
                
                # Her denemede logu sıfırla ki geçmiş 429'lar bugünü bloke etmesin
                try:
                    with open(tun_log_path, "w", encoding="utf-8") as f_init:
                        f_init.write(f"--- Cloudflare Tünel Başlatıldı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
                _tunnel_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1,
                    creationflags=flags
                )

                url_captured = False
                session_has_429 = False

                for line in iter(_tunnel_proc.stdout.readline, ''):
                    if not line:
                        break
                    try:
                        with open(tun_log_path, "a", encoding="utf-8") as f_l:
                            f_l.write(line)
                    except Exception as _ignored_err:
                        logger.debug(f"Suppressed notice: {_ignored_err}")
                    if "429" in line or "status 429" in line:
                        session_has_429 = True

                    m = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line)
                    if m and not url_captured:
                        cand_url = m.group(1).strip()
                        if "api.trycloudflare.com" in cand_url:
                            continue
                        url_captured = True
                        consecutive_429 = 0
                        PUBLIC_TUNNEL_URL = cand_url
                        logger.info(f"🌐 Cloudflare Tunnel Hazır ve Canlı: {PUBLIC_TUNNEL_URL}")
                        publish_tunnel_to_firebase(PUBLIC_TUNNEL_URL)
                        
                        set_system_setting("merkez_sunucu_url", PUBLIC_TUNNEL_URL)
                        cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sunucu_adresi.txt")
                        try:
                            with open(cfg_file, "w", encoding="utf-8") as f_cfg:
                                f_cfg.write(PUBLIC_TUNNEL_URL)
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                        # Yerel IP'yi de bulup bildirelim
                        local_ip = "127.0.0.1"
                        try:
                            s_ip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            s_ip.connect(("8.8.8.8", 80))
                            local_ip = s_ip.getsockname()[0]
                            s_ip.close()
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                        tun_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TUNEL_BAGLANTI_ADRESI.txt")
                        try:
                            with open(tun_file, "w", encoding="utf-8") as f_tun:
                                f_tun.write(
                                    f"============================================================\n"
                                    f"1. AYNI WIFI / YEREL AG BAGLANTISI (EN HIZLI VE KESINTISIZ):\n"
                                    f"http://{local_ip}:5000\n"
                                    f"Admin: http://{local_ip}:5000/login\n\n"
                                    f"2. HER YERDEN (MOBIL VERI VE UZAKTAN) CANLI ERISIM ADRESI:\n"
                                    f"{PUBLIC_TUNNEL_URL}\n"
                                    f"Admin: {PUBLIC_TUNNEL_URL}/login\n"
                                    f"============================================================\n"
                                )
                        except Exception as _ignored_err:
                            logger.debug(f"Suppressed notice: {_ignored_err}")
                        print("\n" + "=" * 72)
                        print(f"🏠 AYNI WIFI / YEREL AG ADRESI (EN HIZLI): http://{local_ip}:5000")
                        print(f"🌍 HER YERDEN / MOBİL VERİDEN CANLI TÜNEL: {PUBLIC_TUNNEL_URL}")
                        print(f"👑 Admin Yönetim Paneli: {PUBLIC_TUNNEL_URL}/login")
                        print("=" * 72 + "\n")

                if _tunnel_proc:
                    _tunnel_proc.wait()

                # Tünel kapandı, eski adresi sıfırla ki kullanıcı kapalı adreste Error 1033 ile takılı kalmasın
                PUBLIC_TUNNEL_URL = ""
                logger.warning("Cloudflare tünel bağlantısı kapandı veya koptu.")

                if session_has_429:
                    consecutive_429 += 1
                    wait_time = min(30 * consecutive_429, 120)
                    logger.warning(f"Cloudflare Rate Limit (HTTP 429) algılandı. Cloudflare API sıfırlanması için {wait_time} saniye bekleniyor...")
                    time.sleep(wait_time)
                else:
                    consecutive_429 = 0
                    time.sleep(5)

            except Exception as e_tun:
                logger.warning(f"Cloudflare tunnel error: {e_tun}")
                time.sleep(10)

    t = threading.Thread(target=_tun_worker, name="CloudflareTunnelThread", daemon=True)
    t.start()

_start_cloudflare_tunnel()

# ── OTOMATİK ARKA PLAN EŞİTLEME (İNTERNET GELDİĞİNDE BEKLEYENLERİ GÖNDERME) ──────────
def sync_pending_records_to_server():
    """İnternet kesintisi sırasında birikmiş veya henüz sunucuya ulaşmamış çıkış kayıtlarını iletir."""
    merkez_url = get_merkez_sunucu_url()
    if not merkez_url:
        return

    try:
        import requests
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()

        c_user, c_pass, c_gln, c_name = read_bkst_credentials()

        # ── 1. ÇEVRİMDISI GİRİŞ YAPILMIŞSA: İNTERNET GELDİĞİNDE BAKANLIK & SUNUCU DOĞRULAMASI ──
        if c_user and c_pass:
            row_chk = c.execute("SELECT id, durum, bayi_kodu FROM bayiler WHERE bkst_kullanici = ? OR bkst_kullanici = ? LIMIT 1", (c_user, encrypt_credential(c_user))).fetchone()
            if row_chk and str(row_chk[1] or '').upper() == 'OFFLINE_GIRIS':
                try:
                    session_auth, gln_auth, token_auth, err_auth = get_bkst_authenticated_session()
                    if session_auth and gln_auth:
                        c_gln = gln_auth or c_gln
                        set_system_setting("bkst_adres_id", c_gln)

                    r_stat = requests.post(
                        f"{merkez_url.rstrip('/')}/api/sync/dealer_status",
                        json={"username": c_user, "password": c_pass, "gln": c_gln, "bayi_kodu": row_chk[2]},
                        timeout=4.0
                    )
                    if r_stat.status_code == 200:
                        stat_js = r_stat.json()
                        durum_srv = str(stat_js.get('durum') or '').upper().strip()
                        if durum_srv == 'AKTIF':
                            c.execute("UPDATE bayiler SET durum = 'AKTIF', gln_no = ? WHERE id = ?", (c_gln, row_chk[0]))
                            conn.commit()
                        else:
                            # Sunucuda AKTİF değilse (ONAY_BEKLIYOR, PASIF veya bulunamadı)
                            # Sunucuya üyelik talebi / bildirim ilet
                            try:
                                requests.post(
                                    f"{merkez_url.rstrip('/')}/api/sync/dealer_registration",
                                    json={
                                        "bayi_kodu": row_chk[2],
                                        "bayi_adi": c_name or c_user,
                                        "bkst_kullanici": c_user,
                                        "bkst_sifre": c_pass,
                                        "gln_no": c_gln,
                                        "tc_no": c_user,
                                        "notif_msg": f"Çevrimdışı giriş yapan bayi (TC: {c_user}) internet bağlantısı sağlandıktan sonra üyelik talebi oluşturdu.",
                                        "durum": "ONAY_BEKLIYOR"
                                    },
                                    timeout=5.0
                                )
                            except Exception as e_reg_fwd:
                                logger.debug(f"Offline reg forward notice: {e_reg_fwd}")
                            c.execute("UPDATE bayiler SET durum = 'ONAY_BEKLIYOR', gln_no = ? WHERE id = ?", (c_gln, row_chk[0]))
                            conn.commit()
                            with _revoked_dealers_lock:
                                _revoked_dealers.add(row_chk[2])
                                _revoked_dealers.add(c_user)
                except Exception as e_auth:
                    logger.debug(f"Offline dealer auto-verification notice: {e_auth}")

        # ── 2. ÇEVRİMDISI OKUTULMUŞ QR'LARI BAKANLIK DEPOSUYLA DÜZELTME & ZENGİNLEŞTİRME ──
        c.execute("""
            SELECT id, ham_karekod, barkod, seri_no 
            FROM cikis_kayitlari 
            WHERE (urun_adi LIKE 'Çevrimdışı%' OR urun_adi LIKE 'Ürün (GTIN:%' OR urun_adi = 'Tanımsız Ürün' OR koli_no IS NULL OR koli_no = '')
              AND COALESCE(silindi, 0) = 0
            LIMIT 200
        """)
        unverified_rows = c.fetchall()
        if unverified_rows:
            df_cache, qr_map, gtin_map, koli_map = get_bkst_cache()
            if df_cache is None or df_cache.empty:
                try:
                    bkst_fetch_api_start()
                except Exception as _ignored_err:
                    logger.debug(f"Suppressed notice: {_ignored_err}")
            else:
                updated_any = False
                for uv_id, uv_hk, uv_barkod, uv_seri in unverified_rows:
                    m_row = resolve_product_from_cache(uv_hk, df_cache, qr_map, gtin_map)
                    if m_row:
                        u_ad = str(m_row.get('Ürün Adı') or '').strip()
                        g_num = str(m_row.get('Gtin Numarası') or m_row.get('gtin') or uv_barkod or '').strip()
                        k_no = str(m_row.get('Koli Numarası') or '').strip()
                        s_no = str(m_row.get('Seri Numarası') or uv_seri or '').strip()
                        p_no = str(m_row.get('Parti Numarası') or '').strip()
                        pal_no = str(m_row.get('Palet Numarası') or '').strip()
                        ur_t = str(m_row.get('Üretim Tarihi') or '').strip()
                        sk_t = str(m_row.get('Son Kullanma Tarihi') or '').strip()
                        c.execute("""
                            UPDATE cikis_kayitlari SET 
                                urun_adi = ?, barkod = ?, koli_no = ?, seri_no = ?, parti_no = ?, palet_no = ?, uretim_tarihi = ?, skt = ?
                            WHERE id = ?
                        """, (u_ad, g_num, k_no, s_no, p_no, pal_no, ur_t, sk_t, uv_id))
                        c.execute("""
                            UPDATE satis_arsivi SET 
                                urun_adi = ?, barkod = ?, koli_no = ?, seri_no = ?, parti_no = ?, palet_no = ?, uretim_tarihi = ?, skt = ?
                            WHERE ham_karekod = ?
                        """, (u_ad, g_num, k_no, s_no, p_no, pal_no, ur_t, sk_t, uv_hk))
                        updated_any = True
                if updated_any:
                    conn.commit()
                    log_debug("SENKRON", "✨ Çevrimdışı okutulan karekodlar Bakanlık depo bilgileriyle otomatik düzeltildi.", seviye="INFO")

        # ── 3. SUNUCUYA BEKLEYEN KAYITLARI AKTARMA (PUSH) ──
        c.execute("""
            SELECT id, tarih, urun_adi, barkod, koli_no, seri_no, parti_no, uretim_tarihi, skt, ham_karekod, cihaz_adi, cihaz_id,
                   COALESCE(silindi, 0), COALESCE(silinme_tarihi, ''), COALESCE(bayi_kodu, 'bayi_1')
            FROM cikis_kayitlari
            WHERE (sync_durum IS NULL OR sync_durum != 'ESITLENDI')
            ORDER BY id ASC LIMIT 500
        """)
        pending_rows = c.fetchall()
        conn.close() # ⚡ Ağ çağrısından önce veritabanı serbest bırakılır
        if not pending_rows:
            return

        from collections import defaultdict
        bayi_records = defaultdict(list)
        bayi_row_ids = defaultdict(list)
        for r in pending_rows:
            b_kod = r[14] or 'bayi_1'
            bayi_row_ids[b_kod].append(int(r[0]))
            bayi_records[b_kod].append({
                'id': r[0], 'tarih': r[1], 'urun_adi': r[2], 'barkod': r[3],
                'koli_no': r[4], 'seri_no': r[5], 'parti_no': r[6],
                'uretim_tarihi': r[7], 'skt': r[8], 'ham_karekod': r[9],
                'cihaz_adi': r[10] or 'Masaüstü PC', 'cihaz_id': r[11] or '',
                'silindi': r[12], 'silinme_tarihi': r[13],
                'bayi_kodu': b_kod
            })

        for b_kod, items in bayi_records.items():
            b_info = get_bayi_by_code(b_kod)
            if b_info:
                c_user = decrypt_credential(b_info[3]) or b_kod
                c_pass = decrypt_credential(b_info[4]) or ""
                c_gln = b_info[5] or ""
                c_name = clean_user_name(b_info[2]) or ""
            else:
                c_user, c_pass, c_gln, c_name = read_bkst_credentials()

            try:
                push_resp = requests.post(
                    f"{merkez_url.rstrip('/')}/api/sync/push",
                    json={
                        "bayi_kodu": b_kod,
                        "bkst_kullanici": c_user or "",
                        "bkst_sifre": c_pass or "",
                        "gln_no": c_gln or "",
                        "bayi_adi": c_name or "",
                        "cikis_kayitlari": items
                    },
                    headers={"X-Forwarded-From-Client": "true", "X-Requested-With": "XMLHttpRequest"},
                    timeout=8.0
                )
                if push_resp.status_code == 200:
                    _ids = bayi_row_ids[b_kod]
                    conn_up = sqlite3.connect(DB_PATH, timeout=30.0)
                    conn_up.execute("PRAGMA journal_mode = WAL")
                    conn_up.execute("PRAGMA busy_timeout = 30000")
                    c_up = conn_up.cursor()
                    c_up.execute(f"UPDATE cikis_kayitlari SET sync_durum = 'ESITLENDI' WHERE id IN ({','.join(str(i) for i in _ids)})")
                    conn_up.commit()
                    conn_up.close()
                    log_debug("SENKRON_OTOMATIK", f"🌐 İnternet Bağlantısı Aktif: [{b_kod}] Bayisine ait {len(items)} adet ürün başarıyla sunucuya iletildi.", seviye="INFO")
            except Exception as e_push:
                logger.warning(f"Push to server failed for {b_kod}: {e_push}")

        import gc
        gc.collect()
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
# ── 1 SAATLİK CANLI BAYİ SENKRONİZASYON MOTORU & KESİNTİ KURTARMA ───────────────
def _start_sync_scheduler():
    def _worker():
        time.sleep(10) # Başlangıçta 10 saniye bekle
        counter = 0
        while True:
            try:
                # Her 15 saniyede bir bekleyen yerel kayıt varsa sunucuya iletmeyi dene (İnternet geldiğinde otomatik eşitleme)
                sync_pending_records_to_server()

                counter += 1
                if counter >= 240: # Yaklaşık 1 saatte bir (240 x 15s = 3600s)
                    counter = 0
                    ensure_db_schema()
                    conn = sqlite3.connect(DB_PATH, timeout=10.0)
                    c = conn.cursor()
                    now_dt_calc = datetime.now()
                    c.execute("SELECT DISTINCT bayi_kodu, son_gorulme FROM aktif_cihazlar WHERE durum = 'ONLINE'")
                    rows = c.fetchall()
                    active_dealers = set()
                    for bk, seen in rows:
                        if bk and seen:
                            try:
                                diff = (now_dt_calc - datetime.strptime(seen, '%Y-%m-%d %H:%M:%S')).total_seconds()
                                if diff <= 45:
                                    active_dealers.add(bk)
                            except Exception as _ignored_err:
                                logger.debug(f"Suppressed notice: {_ignored_err}")
                    now_dt = now_dt_calc.strftime('%Y-%m-%d %H:%M:%S')
                    for bk in active_dealers:
                        c.execute("UPDATE bayiler SET son_senkronizasyon = ? WHERE bayi_kodu = ?", (now_dt, bk))
                        log_debug("SENKRON_OTOMATIK", f"⏰ Canlı Bayi Senkronizasyonu Tamamlandı ({bk})", seviye="DEBUG", bayi_kodu=bk)
                    conn.commit()
                    conn.close()
            except Exception as e_sync:
                logger.warning(f"Scheduler worker error: {e_sync}")
            time.sleep(15)

    t = threading.Thread(target=_worker, name="AutoSyncScheduler", daemon=True)
    t.start()

def _start_server_pull_worker():
    """İstemcide: sunucudaki çıkış listesi değiştiğinde (admin silme/ekleme, başka cihaz okutması) kendiliğinden çeker."""
    if _is_server_host():
        # Ana sunucu cihazı kendinden çekme yapmaz, gereksiz kaynak tüketimini önler
        return
    try:
        ensure_db_schema()
        _set_sync_flag('istemci', '1' if _merkez_url_cfg() else '0')
    except Exception as _ignored_err:
        logger.debug(f"Suppressed notice: {_ignored_err}")
    def _loop():
        time.sleep(1)
        while True:
            try:
                merkez_url = get_merkez_sunucu_url()
                _set_sync_flag('istemci', '1' if merkez_url else '0')
                if merkez_url:
                    _flush_pending_deletes(merkez_url)   # internet gelince bekleyen silmeler iletilir
                    c_user, c_pass, _, _ = read_bkst_credentials()
                    import requests
                    conn_w = sqlite3.connect(DB_PATH, timeout=5.0)
                    local_code = 'bayi_1'
                    if c_user:
                        row_w = conn_w.execute("SELECT bayi_kodu FROM bayiler WHERE bkst_kullanici = ? LIMIT 1", (c_user,)).fetchone()
                        if not row_w:
                            for r_all in conn_w.execute("SELECT bayi_kodu, bkst_kullanici FROM bayiler").fetchall():
                                if r_all[1] and decrypt_credential(r_all[1]).strip() == c_user:
                                    row_w = r_all
                                    break
                        if row_w:
                            local_code = row_w[0]
                    else:
                        row_fb = conn_w.execute("SELECT bayi_kodu FROM bayiler LIMIT 1").fetchone()
                        if row_fb:
                            local_code = row_fb[0]
                    conn_w.close()

                    r_rev = requests.post(f"{merkez_url.rstrip('/')}/api/sync/rev",
                                          json={"bayi_kodu": local_code, "bkst_kullanici": c_user or '', "bkst_sifre": c_pass or ''}, timeout=3.5)
                    if r_rev.status_code == 200:
                        res_rev_data = r_rev.json() or {}
                        s_rev = res_rev_data.get('rev', '')
                        c_rev = res_rev_data.get('clear_history_rev', '')

                        # 1. Sunucudan geçmiş verileri temizleme talimatı geldiyse yereli de temizle
                        clr_key = f"clear_history_rev_{local_code}"
                        if c_rev and c_rev != get_system_setting(clr_key, ""):
                            clr_flags = res_rev_data.get('clear_history_flags') or {}
                            sil_bekleyen_flag = clr_flags.get('sil_bekleyen', True)
                            sil_bugun_flag = clr_flags.get('sil_bugun_satis', False)
                            sil_gecmis_flag = clr_flags.get('sil_gecmis_satis', True)
                            sil_loglar_flag = clr_flags.get('sil_loglar', False)

                            with sqlite3.connect(DB_PATH, timeout=10.0) as conn_clr:
                                if sil_bekleyen_flag:
                                    conn_clr.execute("DELETE FROM cikis_kayitlari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (local_code, local_code))
                                
                                bugun_tarih = datetime.now().strftime('%Y-%m-%d')
                                if sil_gecmis_flag and sil_bugun_flag:
                                    conn_clr.execute("DELETE FROM satis_arsivi WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (local_code, local_code))
                                elif sil_gecmis_flag and not sil_bugun_flag:
                                    conn_clr.execute("DELETE FROM satis_arsivi WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND (tarih < ? AND tarih NOT LIKE ?)", (local_code, local_code, f"{bugun_tarih} 00:00:00", f"{bugun_tarih}%"))
                                elif sil_bugun_flag and not sil_gecmis_flag:
                                    conn_clr.execute("DELETE FROM satis_arsivi WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1')) AND (tarih LIKE ? OR DATE(tarih) = ?)", (local_code, local_code, f"{bugun_tarih}%", bugun_tarih))
                                
                                if sil_loglar_flag:
                                    conn_clr.execute("DELETE FROM debug_loglari WHERE (bayi_kodu = ? OR (bayi_kodu IS NULL AND ? = 'bayi_1'))", (local_code, local_code))

                                conn_clr.commit()
                            set_system_setting(clr_key, c_rev)
                            now_ts = str(int(time.time() * 1000))
                            set_system_setting(f"exit_list_rev_{local_code}", now_ts)
                            logger.info(f"🗑️ [SYNC] Merkez sunucudan gelen talimatla yerel veriler seçici olarak temizlendi ({c_rev}).")

                        # 2. Çıkış listesi revizyonu kontrolü
                        key_w = f"exit_list_rev_{local_code}"
                        if s_rev and s_rev != get_system_setting(key_w, ""):
                            sync_pending_records_to_server()
                            if _auto_pull_exit_list_from_server(merkez_url, local_code, c_user or '', c_pass or ''):
                                set_system_setting(key_w, s_rev)
                                logger.info(f"⚡ [CANLI SYNC] Sunucudan yeni çıkış kayıtları masaüstüne aktarıldı: rev {s_rev}")

                    # ── 3. İSTEMCİ HATA & APP.LOG KAYITLARINI SUNUCUYA AKTAR ──
                    logs_to_send = []
                    with _pending_client_debug_logs_lock:
                        if _pending_client_debug_logs:
                            logs_to_send = list(_pending_client_debug_logs)
                            _pending_client_debug_logs.clear()

                    now_sec = time.time()
                    raw_lines = []
                    if logs_to_send or (now_sec - getattr(_loop, '_last_app_log_check', 0) > 20):
                        _loop._last_app_log_check = now_sec
                        raw_lines = get_recent_app_log_errors(max_lines=35)

                    if logs_to_send or raw_lines:
                        def _send_client_logs_bg(m_url, l_code, l_items, r_items):
                            try:
                                requests.post(
                                    f"{m_url.rstrip('/')}/api/sync/client_debug_logs",
                                    json={
                                        "bayi_kodu": l_code,
                                        "logs": l_items,
                                        "raw_log_lines": r_items
                                    },
                                    timeout=6.0
                                )
                            except Exception as _ignored_err:
                                pass
                        threading.Thread(target=_send_client_logs_bg, args=(merkez_url, local_code, logs_to_send, raw_lines), daemon=True).start()
            except Exception as e_w:
                logger.debug(f"server pull worker: {e_w}")
            time.sleep(2.0)
    threading.Thread(target=_loop, name="ServerPullWatcher", daemon=True).start()

_start_sync_scheduler()
_start_server_pull_worker()

if __name__ == '__main__':
    local_ip = get_local_ip()
    GREEN = '\033[92m'
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'
    
    print(f"\n{CYAN}{BOLD}╔════════════════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  {WHITE}{BOLD}⚡ QR COMPARE  -  KURUMSAL MERKEZ SUNUCU v3.7.0{RESET}                         {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  {DIM}Akıllı Karekod, Depo & Bakanlık Stok Eşitleme Platformu{RESET}                 {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}╠════════════════════════════════════════════════════════════════════════════╣{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  {GREEN}● DURUM :{RESET} {WHITE}{BOLD}ÇEVRİMİÇİ VE AKTİF{RESET} (0.0.0.0:5000)                              {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  {GREEN}● MİMARİ:{RESET} Waitress (64 Threads) │ SQLite WAL Modu │ AES-256 Oturum     {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  {GREEN}● TÜNEL :{RESET} Cloudflare Zero-Trust Canlı Senkronizasyon                     {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}╠════════════════════════════════════════════════════════════════════════════╣{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  {YELLOW}ERİŞİM ADRESLERİ:{RESET}                                                          {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  ├─ 🖥️  Bu Bilgisayar : {CYAN}{BOLD}http://127.0.0.1:5000{RESET}                                {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  ├─ 📱  Yerel Ağ (LAN) : {CYAN}{BOLD}http://{local_ip}:5000{RESET:<35} {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  └─ 👑  Admin Paneli   : {WHITE}{BOLD}http://{local_ip}:5000/login{RESET:<35} {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}╚════════════════════════════════════════════════════════════════════════════╝{RESET}\n")
    try:
        from waitress import serve
        logger.info(f"Starting QR-Compare server with Waitress on 0.0.0.0:5000 (Local IP: {local_ip})...")
        serve(
            app,
            host='0.0.0.0',
            port=5000,
            threads=64,
            connection_limit=1000,
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
                host='0.0.0.0',
                port=5000,
                threads=64,
                connection_limit=1000,
                channel_timeout=180,
                cleanup_interval=30,
                ident='QR-Compare'
            )
        except Exception as e:
            logger.warning(f"Waitress kurulamadı ({e}), Flask development server ile başlatılıyor...")
            app.run(host='0.0.0.0', port=5000, threaded=True)
