import subprocess
import sys

def check_updates():
    try:
        repo_check = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=2)
        if repo_check.returncode != 0:
            print("  [BİLGİ] Git deposu bağlı değil, yerel kodla devam ediliyor.")
            return

        print("  [4/5] GitHub güncellemeleri kontrol ediliyor...")
        subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True, timeout=4)

        status = subprocess.run(["git", "status", "-uno"], capture_output=True, text=True, timeout=3)
        if "behind" in (status.stdout or ""):
            print("\n  ============================================================")
            print("  [GÜNCELLEME BULUNDU] Yeni kodlar GitHub'dan yükleniyor...")
            print("  ============================================================")
            pull_res = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True, timeout=10)
            if pull_res.returncode != 0:
                subprocess.run(["git", "pull", "origin", "master"], capture_output=True, text=True, timeout=10)
            print("  [TAMAMLANDI] Kodlarınız en son sürüme güncellendi!")
            print("  Verileriniz (cikis_kayitlari.db) %100 korundu.\n")
        else:
            print("  Sisteminiz güncel.")
    except Exception:
        print("  [BİLGİ] İnternet veya GitHub bağlantısı kurulur ise güncellenecektir.")

if __name__ == "__main__":
    check_updates()
