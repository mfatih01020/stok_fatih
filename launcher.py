import os
import sys
import subprocess
import time
import shutil
import json
import re
import webbrowser
from datetime import datetime

# Çalışma dizinini script'in bulunduğu klasöre sabitle
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# Consolu UTF-8 moduna gecir ve pencere basligini ayarla
os.system('title QR Stok Yonetim Sistemi (v1.0)')
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.system('chcp 65001 > nul 2>&1')
os.system('')

# ANSI Renk Kodlari
GREEN = '\033[92m'
CYAN = '\033[96m'
YELLOW = '\033[93m'
RED = '\033[91m'
WHITE = '\033[97m'
BOLD = '\033[1m'
DIM = '\033[2m'
RESET = '\033[0m'

def print_header():
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"{CYAN}{BOLD}")
    print(" +------------------------------------------------------------------------+")
    print(" |                                                                        |")
    print(f" |           {WHITE}{BOLD}*  QR STOK VE BAKANLIK KONTROL SİSTEMİ (v1.0) * {CYAN}{BOLD}          |")
    print(" |                                                                        |")
    print(" +------------------------------------------------------------------------+")
    print(f"{RESET}")

def print_step(step_no, title, status="ok", detail=""):
    if status == "ok":
        badge = f"{GREEN}[[ HAZIR ]]{RESET}"
    elif status == "updated":
        badge = f"{CYAN}[[ GUNCEL ]]{RESET}"
    elif status == "warn":
        badge = f"{YELLOW}[[ UYARI ]]{RESET}"
    elif status == "error":
        badge = f"{RED}[[ HATA ]]{RESET}"
    elif status == "loading":
        badge = f"{YELLOW}[[ KONTROL EDILIYOR... ]]{RESET}"

    step_text = f"  {BOLD}[{step_no}/5]{RESET} {title:<38} {badge}"
    if detail:
        step_text += f"  {DIM}{detail}{RESET}"
    print(step_text)

def clear_pycache():
    for dirpath, dirnames, filenames in os.walk(BASE_DIR):
        if "__pycache__" in dirnames:
            try:
                shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            except Exception:
                pass

def is_git_installed():
    try:
        res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=2, cwd=BASE_DIR)
        return res.returncode == 0
    except Exception:
        return False

def ensure_git_installed():
    if is_git_installed():
        return True

    print_step("1", "Git Kurulum Kontrolu", "loading", "Git yukleniyor, lutfen bekleyin...")
    try:
        subprocess.run([
            "winget", "install", "--id", "Git.Git", "-e",
            "--source", "winget",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--silent"
        ], capture_output=True, text=True, timeout=120, cwd=BASE_DIR)

        if is_git_installed():
            print_step("1", "Git Otomatik Kurulumu", "ok", "Git basariyla yuklendi!")
            return True
    except Exception:
        pass

    return False

def get_unified_version_info():
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
                return f"v1.0 ({commit_hash})", (commit_date or "Canli Surum"), (commit_msg or "Sistem Guncel")
    except Exception:
        pass

    try:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_SSL_NO_VERIFY"] = "true"
        h = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        d = subprocess.run(["git", "-c", "http.sslVerify=false", "log", "-1", "--format=%cd", "--date=format:%d.%m.%Y %H:%M", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        m = subprocess.run(["git", "-c", "http.sslVerify=false", "log", "-1", "--format=%s", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        if h and h != "Bilinmiyor":
            return f"v1.0 ({h})", (d or "Canli Surum"), (m or "Sistem Guncel")
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
                v_msg = v_data.get("message", "v1.0 Surumu")
                return f"{v_code} ({v_commit})", v_date, v_msg
        except Exception:
            pass

    return "v1.0 (1.0.0)", "06.10.2026", "v1.0 Surumu"

def check_environment():
    txt_path = "bakanlik_giris_bilgileri.txt"
    tpl_path = "bakanlik_giris_bilgileri.template.txt"
    if not os.path.exists(txt_path) and os.path.exists(tpl_path):
        try:
            with open(tpl_path, "r", encoding="utf-8") as f_in:
                content = f_in.read()
            with open(txt_path, "w", encoding="utf-8") as f_out:
                f_out.write(content)
        except Exception:
            pass
    
    git_ok = ensure_git_installed()
    git_msg = "Git Hazir" if git_ok else "HTTP Motoru Aktif"
    print_step("1", "Giris Yapilandirmasi & Sistem", "ok", f"bakanlik_giris_bilgileri.txt | {git_msg}")

def check_libraries():
    required = ["flask", "pandas", "openpyxl", "requests", "selenium"]
    missing = []
    for req in required:
        try:
            __import__(req)
        except ImportError:
            missing.append(req)
            
    if missing:
        print_step("2", "Gerekli Kutuphaneler", "loading", f"Eksikler kuruluyor: {', '.join(missing)}")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install"] + missing, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=BASE_DIR)
            print_step("2", "Gerekli Kutuphaneler", "ok", "Tum paketler yuklendi")
        except Exception as e:
            print_step("2", "Gerekli Kutuphaneler", "error", f"Yukleme hatasi: {e}")
    else:
        py_ver = f"Python {sys.version.split()[0]}"
        print_step("2", "Gerekli Python Kutuphaneleri", "ok", f"{py_ver} | Flask, Pandas, Requests")

def check_browser():
    chrome1 = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    chrome2 = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    edge1 = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    edge2 = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"

    found = "Varsayilan Sistem Tarayicisi"
    if os.path.exists(edge1) or os.path.exists(edge2):
        found = "Microsoft Edge"
    elif os.path.exists(chrome1) or os.path.exists(chrome2):
        found = "Google Chrome"

    print_step("3", "Tarayici Destegi", "ok", found)

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

def http_fallback_update_launcher():
    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache"
        }

        latest_sha = get_latest_remote_commit_sha(requests)
        timestamp = time.time_ns()
        remote_vurl = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/{latest_sha}/version.json?t={timestamp}"

        resp = requests.get(remote_vurl, verify=False, timeout=10, headers=headers)
        if resp.status_code == 200:
            remote_data = resp.json()
            remote_commit = str(remote_data.get("commit", "")).strip()
            remote_version = str(remote_data.get("version", "v1.0")).strip()
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
                cur_h, cur_d, _ = get_unified_version_info()
                print_step("4", "Otomatik Guncelleme", "ok", f"Surum: {cur_h}")
                return False

            print_step("4", "Otomatik Guncelleme", "loading", f"Yeni sürüm indiriliyor: {remote_version}")
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
            print_step("4", "Otomatik Guncelleme", "updated", f"Guncellendi ({remote_version})")
            return True
    except Exception as e:
        print_step("4", "Otomatik Guncelleme", "warn", f"Baglanti: {e}")
    
    cur_h, cur_d, _ = get_unified_version_info()
    print_step("4", "Otomatik Guncelleme", "ok", f"Surum: {cur_h}")
    return False

def check_updates():
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GIT_SSL_NO_VERIFY"] = "true"

    if is_git_installed():
        try:
            subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, text=True, cwd=BASE_DIR)
            repo_check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=3, env=env, cwd=BASE_DIR)
            if repo_check.returncode == 0:
                print_step("4", "Otomatik Guncelleme", "loading", "Sunucu kontrol ediliyor...")
                repo_url = "https://github.com/mfatih01020/stok_fatih.git"
                fetch_res = subprocess.run(["git", "-c", "http.sslVerify=false", "fetch", repo_url, "main", "--force"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR)

                if fetch_res.returncode == 0:
                    local_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
                    remote_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "FETCH_HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()

                    cur_h, cur_d, cur_m = get_unified_version_info()
                    if not local_hash:
                        local_hash = cur_h

                    if remote_hash and (local_hash != remote_hash or local_hash == "Bilinmiyor" or not local_hash):
                        print_step("4", "Otomatik Guncelleme", "loading", "Yukleniyor...")
                        subprocess.run(["git", "-c", "http.sslVerify=false", "checkout", "-B", "main", "FETCH_HEAD", "--force"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR)
                        subprocess.run(["git", "-c", "http.sslVerify=false", "reset", "--hard", "FETCH_HEAD"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR)

                        clear_pycache()

                        new_h, new_d, new_m = get_unified_version_info()
                        print_step("4", "Otomatik Guncelleme", "updated", f"Surum: {new_h}")
                        return True
                    else:
                        print_step("4", "Otomatik Guncelleme", "ok", f"Surum: {cur_h}")
                        return False
        except Exception:
            pass

    return http_fallback_update_launcher()

def kill_existing_flask():
    try:
        res = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if ":5000 " in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                if pid.isdigit() and int(pid) != os.getpid():
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    except Exception:
        pass

def launch_app():
    kill_existing_flask()
    print_step("5", "Uygulama Sunucusu", "ok", "HTTP 127.0.0.1:5000")
    print(f"\n  {WHITE}{BOLD}" + "-" * 72 + f"{RESET}")
    print(f"  {GREEN}{BOLD}* YÖNETİM PANELİ BAŞARIYLA BAŞLATILDI (v1.0){RESET}")
    print(f"  {CYAN}  Web Adresi :{RESET} {WHITE}{BOLD}http://127.0.0.1:5000{RESET}")
    print(f"  {WHITE}{BOLD}" + "-" * 72 + f"{RESET}\n")

    webbrowser.open("http://127.0.0.1:5000")

    try:
        subprocess.run([sys.executable, "app.py"], cwd=BASE_DIR)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Uygulama kapatildi.{RESET}")

if __name__ == "__main__":
    print_header()
    check_environment()
    check_libraries()
    check_browser()
    has_updated = check_updates()

    if has_updated:
        print(f"\n  {CYAN}{BOLD}[🔄 GÜNCELLEME UYGULANDI]{RESET} {WHITE}Yeni sürüm yüklendi. Otomatik yeniden başlatılıyor...{RESET}\n")
        time.sleep(2)
        os.execv(sys.executable, [sys.executable, "launcher.py"])
    else:
        launch_app()
