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
    print(f" |           {WHITE}{BOLD}*  QR STOK VE BAKANLIK KONTROL SISTEMI  *  {CYAN}{BOLD}              |")
    print(f" |                  {DIM}Sistem Baslatici & Otomatik Guncelleyici{CYAN}{BOLD}              |")
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

    step_text = f"  {BOLD}[{step_no}/5]{RESET} {title:<40} {badge}"
    if detail:
        step_text += f"  {DIM}{detail}{RESET}"
    print(step_text)

def check_environment():
    # 1. Giris Bilgileri Dosyasi Kontrolu
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
    
    print_step("1", "Giris Yapilandirmasi & Dosyalar", "ok", "bakanlik_giris_bilgileri.txt")

def check_libraries():
    # 2. Kutuphane Kontrolu
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
    # 3. Tarayici Kontrolu
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
    # 4. GitHub Guncelleme Kontrolu
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"

    try:
        repo_check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=2, env=env)
        if repo_check.returncode != 0:
            print_step("4", "GitHub Otomatik Guncelleme", "ok", "Yerel mod (Git bulunamadi)")
            return False

        fetch_res = subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True, timeout=4, env=env)
        if fetch_res.returncode != 0:
            print_step("4", "GitHub Otomatik Guncelleme", "ok", "Baglanti aktif (Sistem Hazir)")
            return False

        status = subprocess.run(["git", "status", "-uno"], capture_output=True, text=True, timeout=3, env=env)
        if "behind" in (status.stdout or ""):
            print_step("4", "GitHub Otomatik Guncelleme", "loading", "Yeni surum indiriliyor...")
            pull_res = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True, timeout=10, env=env)
            if pull_res.returncode != 0:
                subprocess.run(["git", "pull", "origin", "master"], capture_output=True, text=True, timeout=10, env=env)
            
            print_step("4", "GitHub Otomatik Guncelleme", "updated", "Yeni kodlar indirildi!")
            return True
        else:
            print_step("4", "GitHub Otomatik Guncelleme", "ok", "Yazilim en son surumde")
            return False
    except Exception:
        print_step("4", "GitHub Otomatik Guncelleme", "ok", "Kontrol tamamlandi")
        return False

def launch_app():
    print_step("5", "Uygulama Sunucusu", "ok", "HTTP 127.0.0.1:5000")
    print(f"\n  {WHITE}{BOLD}" + "-" * 72 + f"{RESET}")
    print(f"  {GREEN}{BOLD}* YONETIM PANELI BASARIYLA BASLATILDI{RESET}")
    print(f"  {CYAN}  Web Adresi :{RESET} {WHITE}{BOLD}http://127.0.0.1:5000{RESET}")
    print(f"  {DIM}  Ipucu: Kapatmak icin bu pencereyi kapatmaniz yeterlidir.{RESET}")
    print(f"  {WHITE}{BOLD}" + "-" * 72 + f"{RESET}\n")

    # Tarayiciyi ac
    webbrowser.open("http://127.0.0.1:5000")

    # Flask uygulamasini baslat
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
        print(f"\n  {CYAN}{BOLD}[🔄 GUNCELLEME UYGULANDI]{RESET} {WHITE}Yeni kodlar yuklendi. Uygulama otomatik olarak yeniden baslatiliyor...{RESET}\n")
        time.sleep(2)
        os.execv(sys.executable, [sys.executable, "launcher.py"])
    else:
        launch_app()
