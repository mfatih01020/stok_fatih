import os
import sys
import subprocess
import shutil
import json
import time
import re
from datetime import datetime

# Çalışma dizinini script'in bulunduğu klasöre sabitle
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

SSL_VERIFY = os.environ.get("QR_SSL_VERIFY", "1") == "1"

# Windows Konsolu için ANSI Renk ve UTF-8 Türkçe Karakter Desteğini Aktifleştir
if os.name == 'nt':
    try:
        os.system('')  # Windows VT100 / ANSI escape sequence modunu açar
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

GREEN = '\033[92m'
CYAN = '\033[96m'
YELLOW = '\033[93m'
RED = '\033[91m'
WHITE = '\033[97m'
BOLD = '\033[1m'
DIM = '\033[2m'
RESET = '\033[0m'

def clear_pycache():
    for dirpath, dirnames, filenames in os.walk(BASE_DIR):
        if "__pycache__" in dirnames:
            try:
                shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            except Exception:
                pass

def get_unified_version_info():
    v_code = "v1.0"
    v_commit = ""
    v_date = datetime.now().strftime("%d.%m.%Y")
    v_msg = "Sistem Güncel"

    v_path = os.path.join(BASE_DIR, "version.json")
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

    full_ver = f"{v_code} ({v_commit})" if v_commit else v_code
    return full_ver, v_date, v_msg

def install_dependencies():
    print(f"  {CYAN}[3/3] Gerekli Python kütüphaneleri kontrol ediliyor ve kuruluyor...{RESET}")
    req_file = os.path.join(BASE_DIR, "requirements.txt")
    if os.path.exists(req_file):
        try:
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                capture_output=True, text=True, cwd=BASE_DIR, creationflags=NO_WINDOW
            )
            if res.returncode == 0:
                print(f"  {GREEN}  ✓ Tüm Python paketleri başarıyla doğrulandı ve yüklendi.{RESET}")
            else:
                print(f"  {YELLOW}  • Temel paketler (waitress, flask, pandas, openpyxl, requests) kuruluyor...{RESET}")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "waitress", "flask", "pandas", "openpyxl", "requests", "xlrd"],
                    capture_output=True, text=True, cwd=BASE_DIR, creationflags=NO_WINDOW
                )
                print(f"  {GREEN}  ✓ Temel paketler başarıyla yüklendi.{RESET}")
        except Exception as e:
            print(f"  {YELLOW}  • Paket yükleme uyarısı: {e}{RESET}")

def recompile_exe_if_possible():
    ps1_file = os.path.join(BASE_DIR, "build_exe.ps1")
    if os.path.exists(ps1_file) and os.name == 'nt':
        try:
            subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", "build_exe.ps1"],
                capture_output=True, text=True, cwd=BASE_DIR, creationflags=NO_WINDOW
            )
        except Exception:
            pass

def is_dev_mode():
    if os.environ.get("DEV_MODE") == "1":
        return True
    if os.path.exists(os.path.join(BASE_DIR, ".dev_mode")):
        return True
    return False

def parse_version_tuple(v_str):
    try:
        clean = re.sub(r'[^0-9.]', '', str(v_str))
        parts = [int(p) for p in clean.split('.') if p.isdigit()]
        return tuple(parts)
    except Exception:
        return (0, 0, 0)

def http_update():
    if is_dev_mode():
        print(f"\n{YELLOW}{BOLD} =============================================================={RESET}")
        print(f"{YELLOW}{BOLD}  🔧 [GELİŞTİRİCİ MODU AKTİF] (.dev_mode dosyası mevcut){RESET}")
        print(f"{WHITE}  Yerel kodlar korunuyor, GitHub'dan indirme/ezme yapılmayacak.{RESET}")
        print(f"{YELLOW}{BOLD} =============================================================={RESET}\n")
        install_dependencies()
        return False

    print(f"  {CYAN}[1/3] GitHub sunucusundan en güncel sürüm bilgisi sorgulanıyor...{RESET}")
    try:
        import requests
        import urllib3
        import zipfile
        import io
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        print(f"  {YELLOW}  • requests kütüphanesi yükleniyor...{RESET}")
        subprocess.run([sys.executable, "-m", "pip", "install", "requests", "urllib3"], capture_output=True, creationflags=NO_WINDOW)
        import requests
        import urllib3
        import zipfile
        import io

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }

    timestamp = time.time_ns()
    remote_vurl = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/main/version.json?t={timestamp}"

    try:
        resp = requests.get(remote_vurl, verify=SSL_VERIFY, timeout=10, headers=headers)
        if resp.status_code != 200:
            print(f"  {RED}[HATA] Güncelleme sunucusuna ulaşılamadı (HTTP {resp.status_code}){RESET}")
            return False

        remote_data = resp.json()
        remote_commit = str(remote_data.get("commit", "")).strip()
        remote_version = str(remote_data.get("version", "")).strip()
        remote_date = str(remote_data.get("date", "")).strip()
        remote_msg = str(remote_data.get("message", "")).strip()

        local_vpath = os.path.join(BASE_DIR, "version.json")
        local_commit = ""
        local_version = "v1.0"
        if os.path.exists(local_vpath):
            try:
                with open(local_vpath, "r", encoding="utf-8") as f:
                    v_raw = json.load(f)
                    local_commit = str(v_raw.get("commit", "")).strip()
                    local_version = str(v_raw.get("version", "v1.0")).strip()
            except Exception:
                pass

        # Sürüm karşılaştırması: Uzak sürüm yerel sürümden büyük değilse güncelleme yapma!
        remote_tup = parse_version_tuple(remote_version)
        local_tup = parse_version_tuple(local_version)

        if remote_tup <= local_tup or (local_commit and local_commit == remote_commit):
            print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
            print(f"{GREEN}{BOLD}  🟢 [GÜNCEL] Sisteminiz zaten en son sürümde ({local_version}).{RESET}")
            print(f"{WHITE}  📦 Yerel Sürüm: {local_version} | Uzak Sürüm: {remote_version}{RESET}")
            print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
            install_dependencies()
            return False

        print(f"\n  {YELLOW}{BOLD}[2/3] [🔄 YENİ SÜRÜM TESPİT EDİLDİ: {remote_version}] Dosyalar indiriliyor...{RESET}")

        zip_url = f"https://github.com/mfatih01020/stok_fatih/archive/refs/heads/main.zip?t={timestamp}"
        zip_resp = requests.get(zip_url, verify=SSL_VERIFY, timeout=40, headers=headers)

        if zip_resp.status_code != 200:
            print(f"  {RED}[HATA] Güncelleme zip paketi indirilemedi (HTTP {zip_resp.status_code}){RESET}")
            return False

        # Asla ezilmeyecek kullanıcı dosyaları
        ignored_extensions = ('.db', '.sqlite', '.sqlite3', '.log')
        ignored_filenames = (
            'cikis_kayitlari.db', 'stok.db', 'stok_takip.db', 
            'bakanlik_giris_bilgileri.txt', 'günlük satışlar.txt',
            '.session_token'
        )

        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                parts = member.filename.split('/', 1)
                if len(parts) < 2:
                    continue
                rel_path = parts[1]

                filename = os.path.basename(rel_path)
                if filename in ignored_filenames or filename.endswith(ignored_extensions) or rel_path.startswith('.git/'):
                    continue

                dest_path = os.path.join(BASE_DIR, rel_path.replace('/', os.sep))
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                try:
                    with zf.open(member) as source, open(dest_path, "wb") as target:
                        target.write(source.read())
                except PermissionError:
                    # Dosya o an kullanımda ise (örneğin Calistir.exe açık ise) atla
                    pass

        with open(local_vpath, "w", encoding="utf-8") as f:
            json.dump(remote_data, f, ensure_ascii=False, indent=2)

        clear_pycache()
        install_dependencies()
        recompile_exe_if_possible()

        print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
        print(f"{GREEN}{BOLD}  🟢 [BAŞARILI] Sistem başarıyla {remote_version} sürümüne güncellendi!{RESET}")
        print(f"{WHITE}{BOLD}  📦 Sürüm: {remote_version} ({remote_commit}) | {remote_date}{RESET}")
        print(f"{WHITE}  📝 Not  : {remote_msg}{RESET}")
        print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
        return True

    except Exception as e:
        print(f"  {RED}[HATA] Güncelleme işlemi sırasında beklenmeyen hata: {e}{RESET}")
        return False

def force_update():
    print(f"\n{CYAN}{BOLD} =============================================================={RESET}")
    print(f"{WHITE}{BOLD}       QR STOK YÖNETİM SİSTEMİ - GÜNCELLEME KONTROLÜ{RESET}")
    print(f"{CYAN}{BOLD} =============================================================={RESET}\n")

    cur_hash, cur_date, cur_msg = get_unified_version_info()
    print(f"  {WHITE}{BOLD}📌 MEVCUT YÜKLÜ SÜRÜM:{RESET}")
    print(f"  {DIM}  • Sürüm Kodu: {RESET}{WHITE}{cur_hash}{RESET}")
    print(f"  {DIM}  • Tarih     : {RESET}{WHITE}{cur_date}{RESET}")
    print(f"  {DIM}  • Not       : {RESET}{WHITE}{cur_msg}{RESET}\n")

    return http_update()

if __name__ == "__main__":
    success = force_update()
