import os
import sys
import subprocess
import time
import webbrowser

# Consolu UTF-8 moduna gecir ve pencere basligini ayarla
os.system('title QR Stok Yonetim Sistemi - Baslatici')
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
    print(f" |           {WHITE}{BOLD}*  QR STOK VE BAKANLIK KONTROL SISTEMI (v2.9) * {CYAN}{BOLD}          |")
    print(f" |               {GREEN}{BOLD}[ v2.9 CANLI OTOMATIK GUNCELLEME TESTI ]{CYAN}{BOLD}               |")
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

def is_git_installed():
    try:
        res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=2)
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
        ], capture_output=True, text=True, timeout=120)

        if is_git_installed():
            print_step("1", "Git Otomatik Kurulumu", "ok", "Git basariyla yuklendi!")
            return True
    except Exception:
        pass

    print_step("1", "Git Kurulum Kontrolu", "warn", "Git indiriliyor...")
    webbrowser.open("https://git-scm.com/download/win")
    return False

def get_git_info(commit_ref="HEAD"):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSL_NO_VERIFY"] = "true"
    try:
        cmd_hash = ["git", "-c", "http.sslVerify=false", "rev-parse", "--short", commit_ref]
        cmd_date = ["git", "-c", "http.sslVerify=false", "log", "-1", "--format=%cd", "--date=format:%d.%m.%Y %H:%M", commit_ref]
        cmd_msg  = ["git", "-c", "http.sslVerify=false", "log", "-1", "--format=%s", commit_ref]

        h = subprocess.run(cmd_hash, capture_output=True, text=True, env=env).stdout.strip()
        d = subprocess.run(cmd_date, capture_output=True, text=True, env=env).stdout.strip()
        m = subprocess.run(cmd_msg,  capture_output=True, text=True, env=env).stdout.strip()
        return h, d, m
    except Exception:
        return "Bilinmiyor", "Bilinmiyor", "Bilinmiyor"

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
    git_msg = "Git Hazir" if git_ok else "Git Yuklu Degil"
    print_step("1", "Giris Yapilandirmasi & Sistem", "ok" if git_ok else "warn", f"bakanlik_giris_bilgileri.txt | {git_msg}")

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
            subprocess.run([sys.executable, "-m", "pip", "install"] + missing, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

def check_updates():
    if not is_git_installed():
        print_step("4", "GitHub Otomatik Guncelleme", "warn", "Git bekleniyor...")
        return False

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GIT_SSL_NO_VERIFY"] = "true"

    try:
        repo_check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=3, env=env)
        if repo_check.returncode == 0:
            print_step("4", "GitHub Otomatik Guncelleme", "loading", "GitHub sunucusu kontrol ediliyor...")
            fetch_res = subprocess.run(["git", "-c", "http.sslVerify=false", "fetch", "origin", "main"], capture_output=True, text=True, timeout=30, env=env)
            
            local_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "HEAD"], capture_output=True, text=True, env=env).stdout.strip()
            remote_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "FETCH_HEAD"], capture_output=True, text=True, env=env).stdout.strip()

            cur_h, cur_d, cur_m = get_git_info("HEAD")

            if local_hash and remote_hash and local_hash != remote_hash:
                print_step("4", "GitHub Otomatik Guncelleme", "loading", "Yeni kodlar yukleniyor...")
                subprocess.run(["git", "-c", "http.sslVerify=false", "checkout", "-B", "main", "origin/main"], capture_output=True, text=True, timeout=15, env=env)
                reset_res = subprocess.run(["git", "-c", "http.sslVerify=false", "reset", "--hard", "FETCH_HEAD"], capture_output=True, text=True, timeout=15, env=env)
                if reset_res.returncode != 0:
                    subprocess.run(["git", "-c", "http.sslVerify=false", "pull", "origin", "main"], capture_output=True, text=True, timeout=15, env=env)
                
                new_h, new_d, new_m = get_git_info("HEAD")
                print_step("4", "GitHub Otomatik Guncelleme", "updated", f"Yeni Surum: {new_h} ({new_d})")
                print(f"  {CYAN}  └─ Son Degisiklik: {WHITE}{new_m}{RESET}")
                return True
            else:
                print_step("4", "GitHub Otomatik Guncelleme", "ok", f"Surum: {cur_h} ({cur_d})")
                return False
    except Exception as e:
        print_step("4", "GitHub Otomatik Guncelleme", "warn", f"Guncelleme: {e}")

    return False

def launch_app():
    print_step("5", "Uygulama Sunucusu", "ok", "HTTP 127.0.0.1:5000")
    print(f"\n  {WHITE}{BOLD}" + "-" * 72 + f"{RESET}")
    print(f"  {GREEN}{BOLD}* YONETIM PANELI BASARIYLA BASLATILDI{RESET}")
    print(f"  {CYAN}  Web Adresi :{RESET} {WHITE}{BOLD}http://127.0.0.1:5000{RESET}")
    print(f"  {DIM}  Ipucu: Kapatmak icin bu pencereyi kapatmaniz yeterlidir.{RESET}")
    print(f"  {WHITE}{BOLD}" + "-" * 72 + f"{RESET}\n")

    webbrowser.open("http://127.0.0.1:5000")

    try:
        subprocess.run([sys.executable, "app.py"])
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Uygulama kapatildi.{RESET}")

if __name__ == "__main__":
    print_header()
    check_environment()
    check_libraries()
    check_browser()
    has_updated = check_updates()

    if has_updated:
        print(f"\n  {CYAN}{BOLD}[🔄 GUNCELLEME UYGULANDI]{RESET} {WHITE}Yeni kodlar yuklendi. Otomatik yeniden baslatiliyor...{RESET}\n")
        time.sleep(2)
        os.execv(sys.executable, [sys.executable, "launcher.py"])
    else:
        launch_app()
