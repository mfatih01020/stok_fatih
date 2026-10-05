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

# Consolu UTF-8 ve ANSI renklere ayarla
os.system('chcp 65001 > nul 2>&1')
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
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

def is_git_installed():
    try:
        res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=2, cwd=BASE_DIR)
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
    Sürüm bilgisini 3 kademeli olarak tespit eder:
    1. .git/HEAD ve logs/HEAD dosya okuması
    2. git komut çıktısı
    3. version.json yerel yedeği
    """
    try:
        head_path = os.path.join(BASE_DIR, '.git', 'HEAD')
        if os.path.exists(head_path):
            with open(head_path, "r", encoding="utf-8", errors="ignore") as f:
                head_content = f.read().strip()

            commit_hash = ""
            if head_content.startswith("ref:"):
                ref_rel = head_content.split(": ", 1)[1].strip()
                ref_path = os.path.join(BASE_DIR, '.git', ref_rel)
                if os.path.exists(ref_path):
                    with open(ref_path, "r", encoding="utf-8", errors="ignore") as f:
                        commit_hash = f.read().strip()[:7]
            else:
                commit_hash = head_content[:7]

            commit_date = ""
            commit_msg = ""
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

            if commit_hash and commit_hash != "Bilinmiyor":
                return f"v1.0 ({commit_hash})", (commit_date or "Canlı Sürüm"), (commit_msg or "Sistem Güncel")
    except Exception:
        pass

    try:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_SSL_NO_VERIFY"] = "true"
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, text=True, cwd=BASE_DIR)
        h = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        d = subprocess.run(["git", "-c", "http.sslVerify=false", "log", "-1", "--format=%cd", "--date=format:%d.%m.%Y %H:%M", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        m = subprocess.run(["git", "-c", "http.sslVerify=false", "log", "-1", "--format=%s", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        if h and h != "Bilinmiyor":
            return f"v1.0 ({h})", (d or "Canlı Sürüm"), (m or "Sistem Güncel")
    except Exception:
        pass

    v_path = os.path.join(BASE_DIR, "version.json")
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = v_data.get("version", "v1.0")
                v_commit = v_data.get("commit", "1.0.0")
                v_date = v_data.get("date", "06.10.2026")
                v_msg = v_data.get("message", "v1.0 Sürümü")
                return f"{v_code} ({v_commit})", v_date, v_msg
        except Exception:
            pass

    return "v1.0 (1.0.0)", "06.10.2026", "QR Stok Yönetim Sistemi v1.0 Sürümü"

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
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        print(f"  {RED}[HATA] Güncelleme için 'requests' kütüphanesi bulunamadı.{RESET}")
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
        files_to_update = remote_data.get("files", [])

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

        print(f"\n  {YELLOW}{BOLD}[🔄 GÜNCELLEME BULUNDU] Sürüm {remote_version} indiriliyor...{RESET}")

        updated_count = 0
        for rel_path in files_to_update:
            raw_url = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/{latest_sha}/{rel_path}?t={timestamp}"
            file_resp = requests.get(raw_url, verify=False, timeout=15, headers=headers)

            if file_resp.status_code == 200:
                dest_path = os.path.join(BASE_DIR, rel_path.replace("/", os.sep))
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with open(dest_path, "wb") as f:
                    f.write(file_resp.content)
                updated_count += 1

        with open(local_vpath, "w", encoding="utf-8") as f:
            json.dump(remote_data, f, ensure_ascii=False, indent=2)

        clear_pycache()

        print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
        print(f"{GREEN}{BOLD}  🟢 [BAŞARILI] Sistem {remote_version} sürümüne güncellendi.{RESET}")
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
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, text=True, cwd=BASE_DIR)

        print(f"  {CYAN}[1/2] Sunucu kontrol ediliyor...{RESET}")
        repo_url = "https://github.com/mfatih01020/stok_fatih.git"
        fetch_res = subprocess.run(["git", "-c", "http.sslVerify=false", "fetch", repo_url, "main", "--force"], capture_output=True, text=True, timeout=20, env=env, cwd=BASE_DIR)

        if fetch_res.returncode == 0:
            local_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
            remote_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "FETCH_HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()

            if remote_hash and (local_hash != remote_hash or local_hash == "Bilinmiyor" or not local_hash):
                print(f"\n  {YELLOW}{BOLD}[🔄 GÜNCELLEME BULUNDU] Yeni sürüm yükleniyor...{RESET}")
                subprocess.run(["git", "-c", "http.sslVerify=false", "checkout", "-B", "main", "FETCH_HEAD", "--force"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR)
                subprocess.run(["git", "-c", "http.sslVerify=false", "reset", "--hard", "FETCH_HEAD"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR)
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
