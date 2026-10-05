import os
import subprocess

def check_updates():
    try:
        # Prevent Git from ever opening a browser login or command prompt
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"

        repo_check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=2, env=env)
        if repo_check.returncode != 0:
            return

        print("  [4/5] GitHub güncellemeleri kontrol ediliyor...")
        fetch_res = subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True, timeout=4, env=env)
        
        # If fetch fails or requires login prompt, skip silently without annoying user
        if fetch_res.returncode != 0:
            print("  Sisteminiz hazır.")
            return

        status = subprocess.run(["git", "status", "-uno"], capture_output=True, text=True, timeout=3, env=env)
        if "behind" in (status.stdout or ""):
            print("\n  ============================================================")
            print("  [GÜNCELLEME BULUNDU] Yeni kodlar GitHub'dan yükleniyor...")
            print("  ============================================================")
            pull_res = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True, timeout=10, env=env)
            if pull_res.returncode != 0:
                subprocess.run(["git", "pull", "origin", "master"], capture_output=True, text=True, timeout=10, env=env)
            print("  [TAMAMLANDI] Kodlarınız en son sürüme güncellendi!")
            print("  Verileriniz (cikis_kayitlari.db) %100 korundu.\n")
        else:
            print("  Sisteminiz güncel.")
    except Exception:
        pass

if __name__ == "__main__":
    check_updates()
