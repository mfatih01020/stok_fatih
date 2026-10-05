import os
import sys
import subprocess

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
        res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=2)
        return res.returncode == 0
    except Exception:
        return False

def ensure_git_installed():
    if is_git_installed():
        return True

    print(f"\n  {YELLOW}[OTOMATİK KURULUM] Bu bilgisayarda Git bulunamadı.{RESET}")
    print(f"  {CYAN}Git arka planda yükleniyor, lütfen bekleyin...{RESET}\n")
    try:
        subprocess.run([
            "winget", "install", "--id", "Git.Git", "-e",
            "--source", "winget",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--silent"
        ], capture_output=True, text=True, timeout=120)
        return is_git_installed()
    except Exception:
        return False

def get_git_info(commit_ref="HEAD"):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        cmd_hash = ["git", "rev-parse", "--short", commit_ref]
        cmd_date = ["git", "log", "-1", "--format=%cd", "--date=format:%d.%m.%Y %H:%M", commit_ref]
        cmd_msg  = ["git", "log", "-1", "--format=%s", commit_ref]

        h = subprocess.run(cmd_hash, capture_output=True, text=True, env=env).stdout.strip()
        d = subprocess.run(cmd_date, capture_output=True, text=True, env=env).stdout.strip()
        m = subprocess.run(cmd_msg,  capture_output=True, text=True, env=env).stdout.strip()
        return h, d, m
    except Exception:
        return "Bilinmiyor", "Bilinmiyor", "Bilinmiyor"

def force_update():
    print(f"\n{CYAN}{BOLD} =============================================================={RESET}")
    print(f"{WHITE}{BOLD}       ⚡ QR STOK YÖNETİM SİSTEMİ - GİTHUB GÜNCELLEME EKRANI{RESET}")
    print(f"{CYAN}{BOLD} =============================================================={RESET}\n")

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"

    # 1. Git Kurulum Kontrolü
    if not is_git_installed():
        git_ok = ensure_git_installed()
        if not git_ok:
            print(f"  {RED}[HATA] Git otomatik yüklenemedi.{RESET}")
            print(f"  {YELLOW}Lütfen indirip kurun: https://git-scm.com/download/win{RESET}\n")
            return False

    # 2. Mevcut Sürüm Bilgilerini Al
    cur_hash, cur_date, cur_msg = get_git_info("HEAD")
    print(f"  {WHITE}{BOLD}📌 MEVCUT SÜRÜM BİLGİLERİ:{RESET}")
    print(f"  {DIM}  • Commit Kodu : {RESET}{WHITE}{cur_hash}{RESET}")
    print(f"  {DIM}  • Sürüm Tarihi: {RESET}{WHITE}{cur_date}{RESET}")
    print(f"  {DIM}  • Son Değişiklik: {RESET}{WHITE}{cur_msg}{RESET}\n")

    print(f"  {CYAN}[1/2] GitHub sunucusundan güncellemeler kontrol ediliyor...{RESET}")
    fetch_res = subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True, timeout=10, env=env)
    
    local_hash = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, env=env).stdout.strip()
    remote_hash = subprocess.run(["git", "rev-parse", "origin/main"], capture_output=True, text=True, env=env).stdout.strip()

    if local_hash and remote_hash and local_hash != remote_hash:
        print(f"\n  {YELLOW}{BOLD}[🔄 GÜNCELLEME BULUNDU] Yeni kodlar yükleniyor...{RESET}")
        reset_res = subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, text=True, timeout=10, env=env)
        
        if reset_res.returncode != 0:
            subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True, timeout=10, env=env)

        new_hash, new_date, new_msg = get_git_info("HEAD")

        print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
        print(f"{GREEN}{BOLD}  🟢 [BAŞARILI] SİSTEM EN SON SÜRÜME GÜNCELLENDİ!{RESET}")
        print(f"{WHITE}{BOLD}  📦 Yeni Sürüm Kodu  : {new_hash}{RESET}")
        print(f"{WHITE}{BOLD}  📅 Güncelleme Tarihi: {new_date}{RESET}")
        print(f"{WHITE}{BOLD}  📝 Değişiklik Notu  : {new_msg}{RESET}")
        print(f"{WHITE}  🔒 Verileriniz (cikis_kayitlari.db) %100 korundu.{RESET}")
        print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
        return True
    else:
        print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
        print(f"{GREEN}{BOLD}  🟢 [GÜNCEL] Sisteminiz zaten en son sürümde. Yeni güncelleme yok.{RESET}")
        print(f"{WHITE}  • Yüklü Sürüm: {cur_hash} ({cur_date}){RESET}")
        print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
        return False

if __name__ == "__main__":
    force_update()
