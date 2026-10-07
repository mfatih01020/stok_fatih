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

GREEN = '\033[92m'
CYAN = '\033[96m'
YELLOW = '\033[93m'
RED = '\033[91m'
WHITE = '\033[97m'
BOLD = '\033[1m'
DIM = '\033[2m'
RESET = '\033[0m'

def is_git_installed():
    try:
        res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=2, cwd=BASE_DIR, creationflags=NO_WINDOW)
        return res.returncode == 0
    except Exception:
        return False

def clear_pycache():
    for dirpath, dirnames, filenames in os.walk(BASE_DIR):
        if "__pycache__" in dirnames:
            try:
                shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            except Exception:
                pass

def get_unified_version_info():
    """
    Sürüm bilgisini version.json dosyasından ve git geçmişinden birleştirerek sunar.
    Tek Gerçeklik Kaynağı: version.json
    """
    v_code = "v3.1.0"
    v_commit = "3.1.0"
    v_date = "08.10.2026"
    v_msg = "Sistem Güncel"

    v_path = os.path.join(BASE_DIR, "version.json")
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = v_data.get("version", "v3.1.0")
                v_commit = v_data.get("commit", "3.1.0")
                v_date = v_data.get("date", "08.10.2026")
                v_msg = v_data.get("message", f"{v_code} Sürümü")
        except Exception:
            pass

    commit_hash = ""
    commit_date = ""
    commit_msg = ""
    try:
        head_path = os.path.join(BASE_DIR, '.git', 'HEAD')
        if os.path.exists(head_path):
            with open(head_path, "r", encoding="utf-8", errors="ignore") as f:
                head_content = f.read().strip()

            if head_content.startswith("ref:"):
                ref_rel = head_content.split(": ", 1)[1].strip()
                ref_path = os.path.join(BASE_DIR, '.git', ref_rel)
                if os.path.exists(ref_path):
                    with open(ref_path, "r", encoding="utf-8", errors="ignore") as f:
                        commit_hash = f.read().strip()[:7]
            else:
                commit_hash = head_content[:7]

            log_path = os.path.join(BASE_DIR, '.git', 'logs', 'HEAD')
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [l for l in f.readlines() if l.strip()]
                    if lines:
                        last_line = lines[-1]
                        parts = last_line.strip().split('\t', 1)
                        if len(parts) > 1:
                            commit_msg = parts[1].replace("commit: ", "").replace("checkout: ", "").strip()
                        meta_parts = parts[0].split()
                        if len(meta_parts) >= 5 and meta_parts[-2].isdigit():
                            dt = datetime.fromtimestamp(int(meta_parts[-2]))
                            commit_date = dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass

    final_version_code = f"{v_code} ({commit_hash or v_commit})"
    final_date = commit_date or v_date
    final_message = v_msg or commit_msg or f"{v_code} Sürümü"

    return final_version_code, final_date, final_message

def get_latest_remote_commit_sha(requests_module):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        atom_url = f"https://github.com/mfatih01020/stok_fatih/commits/main.atom?t={time.time_ns()}"
        r = requests_module.get(atom_url, verify=False, timeout=8, headers=headers)
        if r.status_code == 200:
            matches = re.findall(r'/commit/([0-9a-f]{40})', r.text)
            if matches:
                return matches[0]
    except Exception:
        pass
    return "main"

def http_fallback_update():
    print(f"  {CYAN}  • Güncelleme sunucusu kontrol ediliyor...{RESET}")
    
    try:
        import requests
        import urllib3
        import zipfile
        import io
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        print(f"  {RED}[HATA] Güncelleme için gerekli kütüphaneler bulunamadı.{RESET}")
        return False

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }

    latest_sha = get_latest_remote_commit_sha(requests)
    timestamp = time.time_ns()
    remote_vurl = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/{latest_sha}/version.json?t={timestamp}"

    try:
        resp = requests.get(remote_vurl, verify=False, timeout=10, headers=headers)
        if resp.status_code != 200:
            print(f"  {RED}[HATA] Sunucu yanıt vermedi (HTTP {resp.status_code}){RESET}")
            return False

        remote_data = resp.json()
        remote_commit = str(remote_data.get("commit", "")).strip()
        remote_version = str(remote_data.get("version", "v1.0")).strip()
        remote_date = str(remote_data.get("date", "")).strip()
        remote_msg = str(remote_data.get("message", "")).strip()

        local_vpath = os.path.join(BASE_DIR, "version.json")
        local_commit = ""
        if os.path.exists(local_vpath):
            try:
                with open(local_vpath, "r", encoding="utf-8") as f:
                    local_commit = str(json.load(f).get("commit", "")).strip()
            except Exception:
                pass

        if local_commit and local_commit == remote_commit:
            print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
            print(f"{GREEN}{BOLD}  🟢 [GÜNCEL] Sisteminiz en son sürümde ({remote_version}).{RESET}")
            print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
            return False

        print(f"\n  {YELLOW}{BOLD}[🔄 GÜNCELLEME BULUNDU] Sürüm {remote_version} paketi indiriliyor...{RESET}")

        zip_url = f"https://github.com/mfatih01020/stok_fatih/archive/refs/heads/main.zip?t={timestamp}"
        zip_resp = requests.get(zip_url, verify=False, timeout=35, headers=headers)

        if zip_resp.status_code != 200:
            print(f"  {RED}[HATA] Güncelleme paketi indirilemedi (HTTP {zip_resp.status_code}){RESET}")
            return False

        ignored_extensions = ('.db', '.sqlite', '.sqlite3')
        ignored_filenames = ('cikis_kayitlari.db', 'stok_takip.db', 'bakanlik_giris_bilgileri.txt', 'msedgedriver.exe', 'chromedriver.exe')

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
                with zf.open(member) as source, open(dest_path, "wb") as target:
                    target.write(source.read())

        with open(local_vpath, "w", encoding="utf-8") as f:
            json.dump(remote_data, f, ensure_ascii=False, indent=2)

        clear_pycache()

        print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
        print(f"{GREEN}{BOLD}  🟢 [BAŞARILI] Tüm yeni dosyalar entegre edilerek {remote_version} sürümüne güncellendi.{RESET}")
        print(f"{WHITE}{BOLD}  📦 Sürüm: {remote_version} ({remote_commit}) | {remote_date}{RESET}")
        print(f"{WHITE}  📝 Not  : {remote_msg}{RESET}")
        print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
        return True

    except Exception as e:
        print(f"  {RED}[HATA] Güncelleme işlemi başarısız: {e}{RESET}")
        return False

def force_update():
    print(f"\n{CYAN}{BOLD} =============================================================={RESET}")
    print(f"{WHITE}{BOLD}       QR STOK YÖNETİM SİSTEMİ - GÜNCELLEME KONTROLÜ{RESET}")
    print(f"{CYAN}{BOLD} =============================================================={RESET}\n")

    cur_hash, cur_date, cur_msg = get_unified_version_info()
    print(f"  {WHITE}{BOLD}📌 MEVCUT SÜRÜM BİLGİLERİ:{RESET}")
    print(f"  {DIM}  • Yüklü Sürüm: {RESET}{WHITE}{cur_hash}{RESET}")
    print(f"  {DIM}  • Tarih       : {RESET}{WHITE}{cur_date}{RESET}")
    print(f"  {DIM}  • Not         : {RESET}{WHITE}{cur_msg}{RESET}\n")

    git_works = is_git_installed()
    if git_works:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"
        env["GIT_SSL_NO_VERIFY"] = "true"
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, text=True, cwd=BASE_DIR, creationflags=NO_WINDOW)

        print(f"  {CYAN}[1/2] Sunucu kontrol ediliyor...{RESET}")
        repo_url = "https://github.com/mfatih01020/stok_fatih.git"
        fetch_res = subprocess.run(["git", "-c", "http.sslVerify=false", "fetch", repo_url, "main", "--force"], capture_output=True, text=True, timeout=20, env=env, cwd=BASE_DIR, creationflags=NO_WINDOW)

        if fetch_res.returncode == 0:
            local_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR, creationflags=NO_WINDOW).stdout.strip()
            remote_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "FETCH_HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR, creationflags=NO_WINDOW).stdout.strip()

            if remote_hash and (local_hash != remote_hash or local_hash == "Bilinmiyor" or not local_hash):
                print(f"\n  {YELLOW}{BOLD}[🔄 GÜNCELLEME BULUNDU] Yeni sürüm yükleniyor...{RESET}")
                subprocess.run(["git", "-c", "http.sslVerify=false", "checkout", "-B", "main", "FETCH_HEAD", "--force"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR, creationflags=NO_WINDOW)
                subprocess.run(["git", "-c", "http.sslVerify=false", "reset", "--hard", "FETCH_HEAD"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR, creationflags=NO_WINDOW)
                clear_pycache()

                new_hash, new_date, new_msg = get_unified_version_info()
                print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
                print(f"{GREEN}{BOLD}  🟢 [BAŞARILI] Sistem güncellendi ({new_hash}).{RESET}")
                print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
                return True
            else:
                print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
                print(f"{GREEN}{BOLD}  🟢 [GÜNCEL] Sisteminiz en son sürümde ({cur_hash}).{RESET}")
                print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
                return False

    return http_fallback_update()

if __name__ == "__main__":
    force_update()
