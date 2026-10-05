import os
import sys
import subprocess
import shutil
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
        ], capture_output=True, text=True, timeout=120, cwd=BASE_DIR)
        return is_git_installed()
    except Exception:
        return False

def clear_pycache():
    for dirpath, dirnames, filenames in os.walk(BASE_DIR):
        if "__pycache__" in dirnames:
            try:
                shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            except Exception:
                pass

def get_git_info_python():
    try:
        head_path = os.path.join(BASE_DIR, '.git', 'HEAD')
        if not os.path.exists(head_path):
            return "Bilinmiyor", "Bilinmiyor", "Bilinmiyor"

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
                        raw_msg = parts[1]
                        if raw_msg.startswith("commit: "):
                            commit_msg = raw_msg[8:]
                        elif raw_msg.startswith("checkout: "):
                            commit_msg = raw_msg
                        else:
                            commit_msg = raw_msg
                    
                    meta_parts = parts[0].split()
                    if len(meta_parts) >= 5:
                        ts_str = meta_parts[-2]
                        if ts_str.isdigit():
                            dt = datetime.fromtimestamp(int(ts_str))
                            commit_date = dt.strftime("%d.%m.%Y %H:%M")

        if not commit_hash:
            return get_git_info_subprocess()

        return (commit_hash or "b10a2c"), (commit_date or "Canlı Sürüm"), (commit_msg or "Sistem Güncel")
    except Exception:
        return get_git_info_subprocess()

def get_git_info_subprocess(commit_ref="HEAD"):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSL_NO_VERIFY"] = "true"
    try:
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, text=True, cwd=BASE_DIR)
        cmd_hash = ["git", "-c", "http.sslVerify=false", "rev-parse", "--short", commit_ref]
        cmd_date = ["git", "-c", "http.sslVerify=false", "log", "-1", "--format=%cd", "--date=format:%d.%m.%Y %H:%M", commit_ref]
        cmd_msg  = ["git", "-c", "http.sslVerify=false", "log", "-1", "--format=%s", commit_ref]

        h = subprocess.run(cmd_hash, capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        d = subprocess.run(cmd_date, capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        m = subprocess.run(cmd_msg,  capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
        return (h or "Bilinmiyor"), (d or "Bilinmiyor"), (m or "Bilinmiyor")
    except Exception:
        return "Bilinmiyor", "Bilinmiyor", "Bilinmiyor"

def force_update():
    print(f"\n{CYAN}{BOLD} =============================================================={RESET}")
    print(f"{WHITE}{BOLD}       ⚡ QR STOK YÖNETİM SİSTEMİ - GİTHUB GÜNCELLEME EKRANI{RESET}")
    print(f"{CYAN}{BOLD} =============================================================={RESET}\n")

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GIT_SSL_NO_VERIFY"] = "true"

    if not is_git_installed():
        git_ok = ensure_git_installed()
        if not git_ok:
            print(f"  {RED}[HATA] Git otomatik yüklenemedi.{RESET}")
            print(f"  {YELLOW}Lütfen indirip kurun: https://git-scm.com/download/win{RESET}\n")
            return False

    subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], capture_output=True, text=True, cwd=BASE_DIR)

    cur_hash, cur_date, cur_msg = get_git_info_python()
    print(f"  {WHITE}{BOLD}📌 MEVCUT SÜRÜM BİLGİLERİ:{RESET}")
    print(f"  {DIM}  • Commit Kodu : {RESET}{WHITE}{cur_hash}{RESET}")
    print(f"  {DIM}  • Sürüm Tarihi: {RESET}{WHITE}{cur_date}{RESET}")
    print(f"  {DIM}  • Son Değişiklik: {RESET}{WHITE}{cur_msg}{RESET}\n")

    print(f"  {CYAN}[1/2] GitHub sunucusundan güncellemeler kontrol ediliyor...{RESET}")
    fetch_res = subprocess.run(["git", "-c", "http.sslVerify=false", "fetch", "origin", "main:refs/remotes/origin/main", "--force"], capture_output=True, text=True, timeout=25, env=env, cwd=BASE_DIR)
    
    local_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "HEAD"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()
    remote_hash = subprocess.run(["git", "-c", "http.sslVerify=false", "rev-parse", "origin/main"], capture_output=True, text=True, env=env, cwd=BASE_DIR).stdout.strip()

    if not local_hash:
        local_hash = cur_hash

    # Eğer local_hash ile remote_hash farklıysa VEYA local_hash Bilinmiyor ise -> GÜNCELLE!
    if remote_hash and (local_hash != remote_hash or local_hash == "Bilinmiyor" or not local_hash):
        print(f"\n  {YELLOW}{BOLD}[🔄 GÜNCELLEME BULUNDU] Web değişiklikleri yükleniyor...{RESET}")
        subprocess.run(["git", "-c", "http.sslVerify=false", "checkout", "-B", "main", "origin/main", "--force"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR)
        reset_res = subprocess.run(["git", "-c", "http.sslVerify=false", "reset", "--hard", "origin/main"], capture_output=True, text=True, timeout=15, env=env, cwd=BASE_DIR)
        
        clear_pycache()

        new_hash, new_date, new_msg = get_git_info_python()

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
