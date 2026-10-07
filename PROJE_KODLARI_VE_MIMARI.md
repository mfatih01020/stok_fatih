# QR COMPARE STOK & KAREKOD YÖNETİM SİSTEMİ - TÜM PROJE KODLARI VE MİMARİSİ

> **Sürüm:** v3.1.0 (Son Güncelleme: 08.10.2026)  
> **Konum:** `c:\Users\fatih\Desktop\asım iş`

---

## 1. Mimari Genel Bakış ve Teknik Özellikler

QR Compare, Bitki Koruma Ürünleri (BKST) stok takibi, karekod eşitleme ve raf/terek sayımı yapmak üzere tasarlanmış modern, çevrimdışı (offline-first) destekli bir Flask web uygulamasıdır.

### 🌟 Ana Mimari İlkeler:

1. **Excel Bağımlılığının Kaldırılması (SQLite Persistence)**:
   - Tüm iç veri saklama katmanı `cikis_kayitlari.db` SQLite veritabanı üzerinden yürütülür (`bkst_depo_verileri` ve `cikis_kayitlari` tabloları).
   - Excel dosyaları (`bkst_depo_verileri.xlsx`) **kesinlikle dahili depolama olarak kullanılmaz**. Excel indirmeleri ve raporlamaları Flask üzerinden dinamik `io.BytesIO()` bellek akışları ile anlık üretilir.

2. **Waitress Production WSGI Sunucusu**:
   - Werkzeug geliştirme sunucusu yerine çok iş parçacıklı `Waitress` WSGI sunucusu entegre edilmiştir (`threads=32, connection_limit=200, channel_timeout=180, cleanup_interval=30`).
   - Bu sayede eşzamanlı isteklerde ve büyük veri indirmelerinde bağlantı kopmaları ve sunucu kilitlenmeleri kalıcı olarak engellenmiştir.

3. **Asenkron BKST Worker ve Kesintisiz Arayüz (Non-Blocking)**:
   - Bakanlık veri çekme işlemi (`_do_fetch_api_worker`) arka planda `ThreadPoolExecutor` iş parçacığında asenkron çalışır.
   - `POST /api/bkst/fetch_api` hemen döner, arayüz `GET /api/bkst/fetch_status` üzerinden 1.5 saniyede bir durumu sorgulayarak ilerlemeyi gösterir. Tarayıcı veya istemci istekleri asla zaman aşımına uğramaz.

4. **Akıllı "Sistem Deaktif" & Çevrimdışı (Offline-First) Koruma**:
   - Bakanlığa bağlanılamadığında veya Bakanlık'tan 0 adet veri geldiğinde (`fetched == 0`), yerel SQLite veritabanındaki mevcut stok verileri **asla silinmez**.
   - Sistem otomatik olarak `Sistem Deaktif` moduna geçer, durum rozeti kırmızıya (`.status-indicator.offline`) döner ve kullanıcıya net uyarı gösterilir. Yerel verilerle kesintisiz çalışmaya devam edilir.
   - Bakanlık bağlantısı başarılı olduğunda ve >0 veri çekildiğinde durum otomatik olarak `Sistem Aktif` (yeşil) rozetine güncellenir.

5. **Bağımsız ve Kararlı Masaüstü Başlatıcı (`Calistir.exe` & `launcher.py`)**:
   - Yerel `Calistir.exe` C# başlatıcısı, Python sürecini tarayıcı ömrüne bağlamaz (tarayıcı kapansa veya mevcut oturuma devretse dahi `app.py` sonlandırılmaz). Sunucu arka planda bağımsız bir servis olarak çalışmaya devam eder.

6. **Atomik Staging Değişimi (`bkst_depo_verileri_staging`)**:
   - `save_bkst_data_to_db` fonksiyonu verileri önce `bkst_depo_verileri_staging` geçici tablosuna yazar.
   - Ardından atomik olarak ana tablo `DELETE + INSERT SELECT` ile güncellenir. Bu sayede elektrik/sistem kesintilerinde veri kaybı veya eksik yazma riski tamamen engellenmiştir.

7. **İş Parçacığı Güvenliği (`threading.RLock`)**:
   - Ortak bellek değişkenleri global `_state_lock = threading.RLock()` ile korunur.
   - Veritabanı ve ağ I/O işlemleri kilit bloğunun dışında tutularak yüksek performans ve eşzamanlılık sağlanır.

8. **Yerel Oturum Güvenliği (`.session_token` & `X-Local-Token`)**:
   - Uygulama başlangıcında 32 baytlık rastgele oturum anahtarı üretilir (`.session_token`).
   - Korumalı `/api/*` uç noktaları `X-Local-Token` başlığını kontrol eder. Yetkisiz erişimlerde 401 Unauthorized dönerek kullanıcıyı otomatik oturum açma sayfasına yönlendirir.

9. **LRU Bellek Önbelleği (LRU Memory Cache)**:
   - `get_bkst_cache()` fonksiyonu son 5 kullanıcının veri setini bellekte tutar. Veritabanı güncellendiğinde ilgili kullanıcının önbelleği anında temizlenir (`invalidation`).

10. **Güçlü GTIN Çıkarımı ve GS1 Karekod Ayrıştırma (GTIN Fallback)**:
   - Bakanlık API veya iç veri modellerinden gelen verilerde GTIN alanları (`Gtin Numarası`, `Gtin / Barkod`, `gtin`, `BARKOD`, `Barkod`, `BARCODE`) tam eşleşme ile yakalanır.
   - Herhangi bir nedenle GTIN alanı boş veya eksik gelse dahi, sistem GS1 2D karekod yapısından (`01...` veya `(01)...`) otomatik olarak 14 haneli ürün GTIN/Barkod numarasını (`parse_gs1_qr`) çıkarır.
   - Veritabanı başlangıcında (`init_db`) geriye dönük otomatik onarım mekanizması çalışarak geçmiş tüm çıkış ve stok kayıtlarındaki boş GTIN'leri tamamlar.

---

## 2. Proje Dosya Yapısı ve Kaynak Kodları

### 📁 `.gitignore`

```
# ── KAREKOD & STOK YÖNETİM SİSTEMİ GITIGNORE ──────────────────────────────

# Kullanıcıya Özel Veriler ve Veritabanı (Kesinlikle GitHub'a Yüklenmez)
cikis_kayitlari.db
stok_takip.db
*.db
*.sqlite
*.sqlite3

# Excel ve Veri Dosyaları
bkst_depo_verileri.xlsx
kalan_sistem_envanteri_guncel.xlsx
terek_eksik_urunler_bakanlik_cikis.xlsx
*.xlsx
*.xls
*.csv

# Özel Kullanıcı Notları ve Şifreler
bakanlik_giris_bilgileri.txt
günlük satışlar.txt
*.log

# Tarayıcı ve Sürücü Dosyaları (Her Bilgisayarda Otomatik İndirilir)
msedgedriver.exe
chromedriver.exe
driver_version.txt
edgedriver_tmp/
edgedriver.zip

# Python Geçici Dosyaları
__pycache__/
*.pyc
*.pyo
*.pyd
.pytest_cache/
scratch/
.venv/
env/
venv/

```

---

### 📁 `version.json`

```json
{
  "version": "v3.1.0",
  "commit": "3.1.0",
  "date": "08.10.2026",
  "message": "v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu, otomatik kapanma ve stabilite güncellemeleri",
  "files": [
    ".gitignore",
    "Calistir.bat",
    "Calistir.exe",
    "Calistir.vbs",
    "Guncelle.bat",
    "Kapat.bat",
    "KURULUM.md",
    "app.py",
    "build_exe.ps1",
    "guncelleme_kontrol.py",
    "launcher.py",
    "requirements.txt",
    "static/app.js",
    "static/favicon.ico",
    "static/favicon.png",
    "static/favicon.svg",
    "static/style.css",
    "templates/cikis.html",
    "templates/cikis_listesi.html",
    "templates/index.html",
    "version.json"
  ]
}

```

---

### 📁 `requirements.txt`

```
Flask==3.1.3
pandas==2.2.2
openpyxl==3.1.2
xlrd==2.0.2
requests==2.31.0
Werkzeug==3.1.8
waitress==3.0.0

```

---

### 📁 `KURULUM.md`

```
# QR Stok Yönetim Sistemi - Kurulum Rehberi

## Başka Bir Bilgisayara Taşıma

### Adım 1 – Klasörü Kopyala
`asım iş` klasörünün tamamını (içindeki her şeyiyle birlikte) diğer bilgisayara kopyalayın.
Klasörün içinde şunlar olmalı:
- `app.py`
- `Calistir.bat`  ← çalıştıracağınız dosya
- `requirements.txt`
- `templates/` klasörü
- `static/` klasörü
- `bkst_depo_verileri.xlsx` (varsa)

---

### Adım 2 – Python Kur (sadece bir kez)
Diğer bilgisayarda Python kurulu değilse:

1. https://www.python.org/downloads/ adresine gidin
2. En güncel Python'u indirip kurun
3. **ÖNEMLİ:** Kurulum sırasında "Add Python to PATH" kutucuğunu mutlaka işaretleyin!

---

### Adım 3 – Çalıştır
`Calistir.bat` dosyasına **çift tıklayın**.

- İlk açılışta gerekli paketleri otomatik kuracak (internet gerekli)
- Sonraki açılışlarda direkt çalışacak
- Tarayıcınız otomatik açılacak

---

## Sık Sorulan Sorular

**Python bulunamadı hatası alıyorum?**
Python'un "Add to PATH" seçeneğiyle kurulduğundan emin olun. Kurulumdan sonra bilgisayarı yeniden başlatmayı deneyin.

**Tarayıcı açılmıyor?**
Bat dosyasını çalıştırdıktan sonra tarayıcınıza manuel olarak `http://localhost:5000` yazın.

**İnternet yoksa ne olur?**
Paketler daha önce kurulduysa internet olmadan da çalışır. İlk kurulum için internet gereklidir.

```

---

### 📁 `Calistir.bat`

```powershell
@echo off
cd /d "%~dp0"
start "" "%~dp0Calistir.exe"
exit

```

---

### 📁 `Guncelle.bat`

```powershell
@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Guncelleyici

python guncelleme_kontrol.py
if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo [HATA] Guncelleme islemi basarisiz oldu!
    echo ============================================================
    pause
)
exit

```

---

### 📁 `Kapat.bat`

```powershell
@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Kapatici

echo.
echo ============================================================
echo   QR STOK YÖNETİM SİSTEMİ ARKA PLAN SUNUCUSU KAPATILIYOR...
echo ============================================================
echo.

powershell -Command "Get-Process python,pythonw,py -ErrorAction SilentlyContinue | Stop-Process -Force" > nul 2>&1
taskkill /F /IM python.exe > nul 2>&1
taskkill /F /IM pythonw.exe > nul 2>&1
taskkill /F /IM py.exe > nul 2>&1

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a > nul 2>&1
)

echo.
echo 🟢 Arka plandaki tüm sunucu süreçleri başarıyla kapatıldı.
echo.
timeout /t 2 > nul
exit

```

---

### 📁 `Calistir.vbs`

```powershell
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "Calistir.exe", 0, False

```

---

### 📁 `build_exe.ps1`

```powershell
$code = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Threading;

public class AppLauncher {
    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    private const int SW_MAXIMIZE = 3;

    public static void Main() {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        Directory.SetCurrentDirectory(baseDir);

        Process pythonProc = null;

        if (!IsPortOpen("127.0.0.1", 5000)) {
            string pythonwPath = FindPythonwPath();
            if (!string.IsNullOrEmpty(pythonwPath)) {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = pythonwPath;
                psi.Arguments = "app.py";
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;
                try {
                    pythonProc = Process.Start(psi);
                } catch {}
            }

            for (int i = 0; i < 24; i++) {
                Thread.Sleep(250);
                if (IsPortOpen("127.0.0.1", 5000)) break;
            }
        }

        string browserExe = FindBrowserPath();
        Process browserProc = null;

        if (!string.IsNullOrEmpty(browserExe)) {
            ProcessStartInfo bpsi = new ProcessStartInfo();
            bpsi.FileName = browserExe;
            bpsi.Arguments = "--app=http://127.0.0.1:5000 --start-maximized --window-position=0,0";
            bpsi.UseShellExecute = false;
            bpsi.CreateNoWindow = true;

            try {
                browserProc = Process.Start(bpsi);
            } catch {}
        } else {
            try {
                Process.Start("http://127.0.0.1:5000");
            } catch {}
        }

        for (int j = 0; j < 15; j++) {
            Thread.Sleep(200);
            MaximizeBrowserWindows();
        }

        if (browserProc != null) {
            try {
                browserProc.WaitForExit();
            } catch {}
        }

        try {
            if (pythonProc != null && !pythonProc.HasExited) {
                pythonProc.Kill();
            }
        } catch {}
    }

    private static void MaximizeBrowserWindows() {
        try {
            foreach (Process p in Process.GetProcesses()) {
                string name = p.ProcessName.ToLower();
                if (name.Contains("chrome") || name.Contains("edge")) {
                    IntPtr handle = p.MainWindowHandle;
                    if (handle != IntPtr.Zero && IsWindowVisible(handle)) {
                        ShowWindowAsync(handle, SW_MAXIMIZE);
                        SetForegroundWindow(handle);
                    }
                }
            }
        } catch {}
    }

    private static bool IsPortOpen(string host, int port) {
        try {
            using (TcpClient client = new TcpClient()) {
                IAsyncResult result = client.BeginConnect(host, port, null, null);
                bool success = result.AsyncWaitHandle.WaitOne(400, false);
                if (success) {
                    client.EndConnect(result);
                    return true;
                }
            }
        } catch {}
        return false;
    }

    private static string FindPythonwPath() {
        string[] candidates = new string[] {
            @"C:\Program Files\Python311\pythonw.exe",
            @"C:\Program Files\Python310\pythonw.exe",
            @"C:\Program Files\Python312\pythonw.exe",
            @"C:\Program Files\Python39\pythonw.exe",
            @"C:\Program Files (x86)\Python311\pythonw.exe",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python311\pythonw.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python310\pythonw.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python312\pythonw.exe")
        };

        foreach (string path in candidates) {
            if (File.Exists(path)) return path;
        }

        string pathEnv = Environment.GetEnvironmentVariable("PATH");
        if (!string.IsNullOrEmpty(pathEnv)) {
            foreach (string p in pathEnv.Split(';')) {
                string full = Path.Combine(p.Trim(), "pythonw.exe");
                if (File.Exists(full)) return full;
            }
        }

        return "pythonw.exe";
    }

    private static string FindBrowserPath() {
        string[] candidates = new string[] {
            @"C:\Program Files\Google\Chrome\Application\chrome.exe",
            @"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Google\Chrome\Application\chrome.exe"),
            @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            @"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Microsoft\Edge\Application\msedge.exe")
        };

        foreach (string path in candidates) {
            if (File.Exists(path)) return path;
        }

        return null;
    }
}
"@

Add-Type -TypeDefinition $code -OutputAssembly "Calistir.exe" -OutputType WindowsApplication
Write-Host "Native Calistir.exe built successfully!"

```

---

### 📁 `launcher.py`

```python
import os
import sys
import subprocess
import time
import socket
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

def is_port_in_use(port=5000):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(('127.0.0.1', port)) == 0
    except Exception:
        return False

def open_as_desktop_app(url="http://127.0.0.1:5000"):
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")
    ]
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ]

    for browser_path in chrome_paths + edge_paths:
        if os.path.exists(browser_path):
            try:
                subprocess.Popen([browser_path, f"--app={url}", "--start-maximized", "--window-position=0,0"], creationflags=NO_WINDOW)
                return True
            except Exception:
                pass

    webbrowser.open(url)
    return False

def launch():
    if not is_port_in_use(5000):
        python_exe = sys.executable
        subprocess.Popen([python_exe, "app.py"], cwd=BASE_DIR, creationflags=NO_WINDOW)

        for _ in range(20):
            time.sleep(0.25)
            if is_port_in_use(5000):
                break

    open_as_desktop_app("http://127.0.0.1:5000")

if __name__ == "__main__":
    launch()

```

---

### 📁 `guncelleme_kontrol.py`

```python
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

```

---

### 📁 `app.py`

```python
import sys
import os
import glob
import re
import threading
import time
import sqlite3
import json
import secrets
import logging
from logging.handlers import RotatingFileHandler
import concurrent.futures
from datetime import datetime
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, redirect
import io

class SafeStream:
    def write(self, s):
        pass
    def flush(self):
        pass

if getattr(sys, 'stdout', None) is None:
    sys.stdout = SafeStream()
if getattr(sys, 'stderr', None) is None:
    sys.stderr = SafeStream()

# ── Logging Configuration ───────────────────────────────────────────────────
handler = RotatingFileHandler('app.log', maxBytes=5_000_000, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger('qr_compare')

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "qr_compare_stok_fatih_secret_key_2026_x89")

# ── Global Thread Lock & Memory State ─────────────────────────────────────────
_state_lock = threading.RLock()
_user_cache_map = {}
_cache_access_order = []
bkst_status = "closed"
bkst_message = ""
cached_results = {}
_app_bkst_synced = False

_fetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='bkst_fetch')
_fetch_future = None

# ── Heartbeat Watchdog Thread ─────────────────────────────────────────────────
_last_heartbeat = time.time()

def heartbeat_watchdog():
    while True:
        time.sleep(15)
        if time.time() - _last_heartbeat > 60:
            logger.warning("Heartbeat timeout (60s). Uygulama otomatik yenileniyor/kapatılıyor.")
            # Graceful watchdog logging
            
threading.Thread(target=heartbeat_watchdog, daemon=True).start()

# ── Session Token Authentication Helper ───────────────────────────────────────
SESSION_TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".session_token")

def get_or_create_session_token():
    if os.path.exists(SESSION_TOKEN_PATH):
        try:
            with open(SESSION_TOKEN_PATH, 'r', encoding='utf-8') as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception:
            pass
    token = secrets.token_hex(32)
    try:
        with open(SESSION_TOKEN_PATH, 'w', encoding='utf-8') as f:
            f.write(token)
    except Exception:
        pass
    return token

LOCAL_SESSION_TOKEN = get_or_create_session_token()

# ── Cache-Control & Auth Headers ─────────────────────────────────────────────
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.before_request
def check_authentication():
    path = request.path
    if (path.startswith('/static') or 
        path == '/login' or 
        path == '/api/system/login' or 
        path == '/api/system/heartbeat' or 
        path == '/api/heartbeat'):
        return None

    username, password, _, _ = read_bkst_credentials()
    if not username or not password:
        if path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Kullanıcı oturum açmamış veya giriş bilgileri yok.', 'code': 401}), 401
        return redirect('/login')

    if path.startswith('/api/'):
        client_token = request.headers.get('X-Local-Token')
        if not client_token:
            client_token = request.args.get('token')
        if client_token and client_token != LOCAL_SESSION_TOKEN:
            return jsonify({'success': False, 'error': 'Geçersiz oturum anahtarı', 'code': 401}), 401

    return None

# ── Sürüm & Güncelleme Bilgisi ────────────────────────────────────────────────
def get_version_info():
    try:
        from guncelleme_kontrol import get_unified_version_info
        ver_code, ver_date, ver_msg = get_unified_version_info()
        return {
            "success": True,
            "version": ver_code,
            "commit_hash": ver_code,
            "commit_date": ver_date,
            "commit_msg": ver_msg
        }
    except Exception as e:
        logger.error(f"Error loading version info: {e}")

    v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = v_data.get("version", "v3.1.0")
                return {
                    "success": True,
                    "version": f"{v_code} ({v_data.get('commit', '3.1.0')})",
                    "commit_hash": f"{v_code} ({v_data.get('commit', '3.1.0')})",
                    "commit_date": v_data.get("date", "08.10.2026"),
                    "commit_msg": v_data.get("message", f"{v_code} Sürümü")
                }
        except Exception as e:
            logger.error(f"Error reading version.json fallback: {e}")

    return {
        "success": True,
        "version": "v3.1.0",
        "commit_hash": "v3.1.0",
        "commit_date": "08.10.2026",
        "commit_msg": "v3.1.0 Sürümü"
    }

@app.route('/api/system/heartbeat', methods=['POST', 'GET'])
def system_heartbeat():
    global _last_heartbeat
    _last_heartbeat = time.time()
    return jsonify({"status": "ok"})

@app.route('/api/system/version', methods=['GET'])
def system_version_api():
    return jsonify(get_version_info())

@app.route('/api/heartbeat', methods=['POST', 'GET'])
def api_heartbeat():
    global _last_heartbeat
    _last_heartbeat = time.time()
    return jsonify({'status': 'ok'})

# ── SQLite Veritabanı Katmanı & Staging Swap ──────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cikis_kayitlari.db')

def save_bkst_data_to_db(df, username=""):
    if df is None or df.empty:
        return
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()
    
    if username:
        c.execute("DELETE FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?", (username,))
    else:
        c.execute("DELETE FROM bkst_depo_verileri_staging")
        
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    koli_col = find_koli_column(df.columns)
    
    records = df.to_dict(orient="records")
    rows_to_insert = []
    for r in records:
        qr_val = normalize_qr(str(r.get("Karekod", r.get("tam_karekod", r.get("QR", "")))))
        gtin_val = str(r.get("Gtin Numarası", r.get("gtin", r.get("BARKOD", "")))).strip()
        urun_val = str(r.get("Ürün Adı", r.get("urun_adi", r.get("URUNADI", "")))).strip()
        seri_val = str(r.get("Seri Numarası", r.get("seri_no", r.get("SERIALNUMBER", "")))).strip()
        parti_val = str(r.get("Parti Numarası", r.get("parti_no", r.get("SARJNO", "")))).strip()
        raw_koli = r.get(koli_col) if koli_col else r.get("koli_no")
        koli_val = str(raw_koli).strip().upper() if pd.notna(raw_koli) and raw_koli is not None else ""
        if koli_val == "NAN":
            koli_val = ""
        palet_val = str(r.get("Palet Numarası", r.get("palet_no", "")))
        uretim_val = str(r.get("Üretim Tarihi", r.get("uretim_tarihi", "")))
        skt_val = str(r.get("Son Kullanma Tarihi", r.get("skt", r.get("SKT", ""))))
        
        rows_to_insert.append((gtin_val, urun_val, seri_val, parti_val, koli_val, palet_val, uretim_val, skt_val, qr_val, username, now_str))
            
    c.executemany('''INSERT INTO bkst_depo_verileri_staging
        (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', rows_to_insert)
    conn.commit()

    try:
        if username:
            c.execute("DELETE FROM bkst_depo_verileri WHERE kullanici_adi = ?", (username,))
            c.execute('''INSERT INTO bkst_depo_verileri
                (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
                SELECT gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi
                FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?''', (username,))
            c.execute("DELETE FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?", (username,))
        else:
            c.execute("DELETE FROM bkst_depo_verileri")
            c.execute('''INSERT INTO bkst_depo_verileri
                (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
                SELECT gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi
                FROM bkst_depo_verileri_staging''')
            c.execute("DELETE FROM bkst_depo_verileri_staging")
        conn.commit()
    except Exception as e:
        logger.error(f"save_bkst_data_to_db transaction error: {e}", exc_info=True)
        conn.rollback()
        conn.close()
        raise e
    conn.close()

    user_key = username or "default_user"
    with _state_lock:
        _user_cache_map.pop(user_key, None)
        if user_key in _cache_access_order:
            _cache_access_order.remove(user_key)

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute('''CREATE TABLE IF NOT EXISTS cikis_kayitlari (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih        TEXT NOT NULL,
        urun_adi     TEXT,
        barkod       TEXT,
        koli_no      TEXT,
        seri_no      TEXT,
        parti_no     TEXT,
        palet_no     TEXT,
        uretim_tarihi TEXT,
        skt          TEXT,
        ham_karekod  TEXT,
        tekrar_uyari INTEGER DEFAULT 0,
        kullanici_adi TEXT
    )''')
    c.execute("PRAGMA table_info(cikis_kayitlari)")
    cols = [row[1] for row in c.fetchall()]
    if "kullanici_adi" not in cols:
        c.execute("ALTER TABLE cikis_kayitlari ADD COLUMN kullanici_adi TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ham_karekod ON cikis_kayitlari(ham_karekod)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_kullanici_adi ON cikis_kayitlari(kullanici_adi)")

    c.execute('''CREATE TABLE IF NOT EXISTS bkst_depo_verileri (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        gtin             TEXT,
        urun_adi         TEXT,
        seri_no          TEXT,
        parti_no         TEXT,
        koli_no          TEXT,
        palet_no         TEXT,
        uretim_tarihi    TEXT,
        skt              TEXT,
        tam_karekod      TEXT,
        gln              TEXT,
        adres_id         TEXT,
        kullanici_adi    TEXT,
        guncelleme_tarihi TEXT
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_karekod ON bkst_depo_verileri(tam_karekod)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_gtin ON bkst_depo_verileri(gtin)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_kullanici ON bkst_depo_verileri(kullanici_adi)")

    c.execute('''CREATE TABLE IF NOT EXISTS bkst_depo_verileri_staging (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        gtin             TEXT,
        urun_adi         TEXT,
        seri_no          TEXT,
        parti_no         TEXT,
        koli_no          TEXT,
        palet_no         TEXT,
        uretim_tarihi    TEXT,
        skt              TEXT,
        tam_karekod      TEXT,
        gln              TEXT,
        adres_id         TEXT,
        kullanici_adi    TEXT,
        guncelleme_tarihi TEXT
    )''')

    conn.commit()
    conn.close()

    excel_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bkst_depo_verileri.xlsx")
    if os.path.exists(excel_file):
        try:
            username, _, _, _ = read_bkst_credentials()
            df_old = pd.read_excel(excel_file)
            if not df_old.empty:
                save_bkst_data_to_db(df_old, username)
            os.remove(excel_file)
            logger.info("bkst_depo_verileri.xlsx successfully migrated to SQLite DB and removed!")
        except Exception as e:
            logger.error(f"Migration error: {e}")

def read_bkst_credentials():
    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    username = ""
    password = ""
    address_id = ""
    api_key = ""
    
    if os.path.exists(cred_file):
        with open(cred_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("KULLANICI_ADI="):
                    username = line.split("=", 1)[1].strip()
                elif line.startswith("SIFRE="):
                    password = line.split("=", 1)[1].strip()
                elif line.startswith("ADRES_ID="):
                    raw_id = line.split("=", 1)[1].strip()
                    if "-" in raw_id:
                        address_id = raw_id.split("-")[0].strip()
                    else:
                        address_id = raw_id
                elif line.startswith("KEY=") or line.startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    
    return username, password, address_id, api_key

def clean_user_name(name):
    if not name:
        return ""
    name = str(name).strip()
    cleaned = re.sub(r'^\d+[\s\-]+', '', name)
    cleaned = cleaned.split(" (")[0].strip()
    return cleaned if cleaned else name

@app.context_processor
def inject_global_template_vars():
    username, password, address_id, api_key = read_bkst_credentials()
    user_name = "Giriş Yapılmadı"
    if username:
        user_name = username
        cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
        if os.path.exists(cred_file):
            try:
                with open(cred_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip().startswith("KULLANICI_ISIM="):
                            val = line.strip().split("=", 1)[1].strip()
                            if val:
                                user_name = val
                                break
            except Exception:
                pass
        user_name = clean_user_name(user_name or username)

    version_str = "v3.1.0"
    v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = v_data.get("version", "3.1.0")
                if not str(v_code).startswith("v"):
                    version_str = f"v{v_code}"
                else:
                    version_str = str(v_code)
        except Exception:
            pass

    return dict(current_user_name=user_name, current_app_version=version_str)

def normalize_qr(qr):
    if pd.isna(qr):
        return ""
    qr_str = str(qr).strip().replace(" ", "")
    qr_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', qr_str)
    return qr_str.upper()

def parse_gs1_qr(qr_str):
    if not qr_str or pd.isna(qr_str):
        return {"gtin": None, "seri_no": None, "parti_no": None, "skt": None, "uretim_tarihi": None}

    raw = str(qr_str).strip()
    if raw.startswith("]d2") or raw.startswith("]Q3"):
        raw = raw[3:]

    raw_clean = re.sub(r'[\x00-\x1f\x7f-\x9f]', '\x1d', raw)
    tokens = [t for t in raw_clean.split('\x1d') if t]

    result = {
        "gtin": None,
        "seri_no": None,
        "parti_no": None,
        "skt": None,
        "uretim_tarihi": None
    }

    for token in tokens:
        idx = 0
        while idx < len(token):
            if token[idx:].startswith("01") and len(token[idx:]) >= 16 and token[idx+2:idx+16].isdigit():
                if not result["gtin"]:
                    result["gtin"] = token[idx+2:idx+16]
                idx += 16
                continue
            elif token[idx:].startswith("17") and len(token[idx:]) >= 8 and token[idx+2:idx+8].isdigit():
                if not result["skt"]:
                    yy, mm, dd = token[idx+2:idx+4], token[idx+4:idx+6], token[idx+6:idx+8]
                    result["skt"] = f"{dd}.{mm}.20{yy}"
                idx += 8
                continue
            elif token[idx:].startswith("11") and len(token[idx:]) >= 8 and token[idx+2:idx+8].isdigit():
                if not result["uretim_tarihi"]:
                    yy, mm, dd = token[idx+2:idx+4], token[idx+4:idx+6], token[idx+6:idx+8]
                    result["uretim_tarihi"] = f"{dd}.{mm}.20{yy}"
                idx += 8
                continue
            elif token[idx:].startswith("21") and len(token[idx:]) > 2:
                if not result["seri_no"]:
                    result["seri_no"] = token[idx+2:]
                break
            elif token[idx:].startswith("10") and len(token[idx:]) > 2:
                if not result["parti_no"]:
                    result["parti_no"] = token[idx+2:]
                break
            else:
                idx += 1

    if not result["gtin"]:
        digits14 = re.findall(r'\d{14}', raw)
        if digits14:
            result["gtin"] = digits14[0]

    return result

def extract_gtin(qr_str):
    parsed = parse_gs1_qr(qr_str)
    return parsed.get("gtin")

def find_koli_column(cols):
    cols_list = [str(c) for c in cols]
    for c in cols_list:
        if 'koli' in c.lower():
            return c
    for c in cols_list:
        if 'paket' in c.lower():
            return c
    for c in cols_list:
        if 'palet' in c.lower():
            return c
    return None

def get_bkst_cache():
    username, _, _, _ = read_bkst_credentials()
    user_key = username or "default_user"

    with _state_lock:
        if user_key in _user_cache_map:
            if user_key in _cache_access_order:
                _cache_access_order.remove(user_key)
            _cache_access_order.append(user_key)
            return _user_cache_map[user_key]

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        if username:
            df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ''", conn, params=(username,))
        else:
            df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri", conn)
    except Exception as e:
        logger.error(f"Error querying bkst_depo_verileri: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()

    if df is None or df.empty:
        return None, {}, {}, {}

    df = df.rename(columns={
        'gtin': 'Gtin Numarası',
        'urun_adi': 'Ürün Adı',
        'seri_no': 'Seri Numarası',
        'parti_no': 'Parti Numarası',
        'koli_no': 'Koli Numarası',
        'palet_no': 'Palet Numarası',
        'uretim_tarihi': 'Üretim Tarihi',
        'skt': 'Son Kullanma Tarihi',
        'tam_karekod': 'Karekod'
    })

    if 'Karekod' in df.columns:
        df = df.drop_duplicates(subset=['Karekod'])

    koli_dict = {}
    qr_dict = {}
    gtin_dict = {}

    for r in df.to_dict(orient="records"):
        qr_val = normalize_qr(str(r.get("Karekod", "")))
        gtin_val = normalize_qr(str(r.get("Gtin Numarası", "")))
        koli_val = str(r.get("Koli Numarası", "")).strip().upper()

        if qr_val:
            qr_dict[qr_val] = r
        if gtin_val and gtin_val not in gtin_dict:
            gtin_dict[gtin_val] = r

        if koli_val and koli_val != "NAN":
            if koli_val not in koli_dict:
                koli_dict[koli_val] = []
            koli_dict[koli_val].append(r)

    res = (df, qr_dict, gtin_dict, koli_dict)

    with _state_lock:
        _user_cache_map[user_key] = res
        if user_key in _cache_access_order:
            _cache_access_order.remove(user_key)
        _cache_access_order.append(user_key)

        while len(_user_cache_map) > 5 and _cache_access_order:
            oldest_key = _cache_access_order.pop(0)
            _user_cache_map.pop(oldest_key, None)

    return res

init_db()

def build_gtin_name_map():
    mapping = {}
    try:
        df, _, _, _ = get_bkst_cache()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                gtin = normalize_qr(str(row.get('gtin', row.get('Gtin Numarası', ''))))
                name = str(row.get('urun_adi', row.get('Ürün Adı', ''))).strip()
                if gtin and name:
                    mapping[gtin] = name
    except Exception:
        pass
    return mapping

def parse_system_file(file_or_path, gtin_map=None):
    if gtin_map is None:
        gtin_map = {}
    items = []
    
    if isinstance(file_or_path, str):
        file_path = file_or_path
        filename = os.path.basename(file_path)
        if filename.endswith(('.xls', '.xlsx')):
            try:
                xl = pd.ExcelFile(file_path)
                target_sheet = "Tüm Ürünler (QR)" if "Tüm Ürünler (QR)" in xl.sheet_names else xl.sheet_names[0]
                df = pd.read_excel(file_path, sheet_name=target_sheet)
                
                qr_col = None
                for col in df.columns:
                    c_str = str(col).lower()
                    if 'karekod' in c_str or 'qr' in c_str:
                        qr_col = col
                        break
                if qr_col is None:
                    qr_col = df.columns[0]
                    
                name_col = None
                for col in df.columns:
                    if 'ürün adı' in str(col).lower() or 'urun' in str(col).lower() or 'ad' in str(col).lower():
                        name_col = col
                        break
                        
                for _, row in df.iterrows():
                    q = normalize_qr(row[qr_col])
                    if q:
                        p_name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""
                        if not p_name:
                            gtin = extract_gtin(q)
                            p_name = gtin_map.get(gtin, "Bilinmeyen Ürün")
                            
                        metadata = {}
                        for col in df.columns:
                            if col not in [qr_col, name_col]:
                                val = row[col]
                                if pd.notna(val):
                                    metadata[str(col)] = str(val)
                                    
                        items.append({
                            "qr": q,
                            "product_name": p_name,
                            "metadata": metadata
                        })
            except Exception as e:
                logger.error(f"Error parsing system file: {e}")
    else:
        file = file_or_path
        filename = file.filename
        if filename.endswith(('.xls', '.xlsx')):
            try:
                xl = pd.ExcelFile(file)
                target_sheet = "Tüm Ürünler (QR)" if "Tüm Ürünler (QR)" in xl.sheet_names else xl.sheet_names[0]
                df = pd.read_excel(file, sheet_name=target_sheet)
                
                qr_col = None
                for col in df.columns:
                    c_str = str(col).lower()
                    if 'karekod' in c_str or 'qr' in c_str:
                        qr_col = col
                        break
                if qr_col is None:
                    qr_col = df.columns[0]
                    
                name_col = None
                for col in df.columns:
                    if 'ürün adı' in str(col).lower() or 'urun' in str(col).lower() or 'ad' in str(col).lower():
                        name_col = col
                        break
                        
                for _, row in df.iterrows():
                    q = normalize_qr(row[qr_col])
                    if q:
                        p_name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""
                        if not p_name:
                            gtin = extract_gtin(q)
                            p_name = gtin_map.get(gtin, "Bilinmeyen Ürün")
                            
                        metadata = {}
                        for col in df.columns:
                            if col not in [qr_col, name_col]:
                                val = row[col]
                                if pd.notna(val):
                                    metadata[str(col)] = str(val)
                                    
                        items.append({
                            "qr": q,
                            "product_name": p_name,
                            "metadata": metadata
                        })
            except Exception as e:
                logger.error(f"Error parsing system file: {e}")

    return items

def parse_sales_file(file):
    sales_qrs = []
    filename = file.filename
    
    if filename.endswith('.txt'):
        content = file.read().decode('utf-8', errors='ignore')
        for line in content.splitlines():
            q = normalize_qr(line)
            if q:
                sales_qrs.append(q)
    elif filename.endswith(('.xls', '.xlsx')):
        df = pd.read_excel(file)
        qr_col = None
        for col in df.columns:
            col_str = str(col).lower()
            if 'qr' in col_str or 'karekod' in col_str or 'barkod' in col_str:
                qr_col = col
                break
        if qr_col is None:
            qr_col = df.columns[0]
            
        for val in df[qr_col].dropna():
            q = normalize_qr(val)
            if q:
                sales_qrs.append(q)
                
    return sales_qrs

# ── MAIN HTML ROUTES ────────────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect('/cikis')

@app.route('/stok-esitleme')
def stok_esitleme_page():
    return render_template('index.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/cikis')
def cikis_page():
    return render_template('cikis.html')

@app.route('/cikis-listesi')
@app.route('/cikis_listesi')
def cikis_listesi_page():
    return render_template('cikis_listesi.html')

@app.route('/depo_stoklari')
@app.route('/depo-stoklari')
def depo_stoklari_page():
    return render_template('depo_stoklari.html')

@app.route('/depo_kabul')
def depo_kabul_page():
    return render_template('depo_kabul.html')

@app.route('/kullaniciya-satis')
@app.route('/kullaniciya_satis')
def kullaniciya_satis_page():
    return render_template('kullaniciya_satis.html')

# ── STOK KARŞILAŞTIRMA & RAPORLAMA API ──────────────────────────────────────
@app.route('/api/compare', methods=['POST'])
def api_compare():
    try:
        if 'system_file' not in request.files or 'sales_file' not in request.files:
            return jsonify({"success": False, "error": "Lütfen hem Sistem Depo hem de Satış dosyasını yükleyin."})
            
        system_file = request.files['system_file']
        sales_file = request.files['sales_file']
        
        gtin_map = build_gtin_name_map()
        inventory = parse_system_file(system_file, gtin_map)
        sales_qrs = parse_sales_file(sales_file)
        
        if not inventory:
            return jsonify({"success": False, "error": "Sistem Depo dosyasından geçerli karekod okunamadı."})
        if not sales_qrs:
            return jsonify({"success": False, "error": "Satış dosyasından geçerli karekod okunamadı."})
            
        inventory_dict = {item["qr"]: item for item in inventory}
        matched_sales = []
        unmatched_sales = []
        sold_qrs_set = set()
        sales_by_product = {}
        
        koli_stats = {}
        for item in inventory:
            meta = item.get("metadata", {})
            koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO")
            if koli_no:
                koli_no = str(koli_no).strip()
                if koli_no not in koli_stats:
                    koli_stats[koli_no] = {"total": 0, "sold": 0, "product_name": item["product_name"]}
                koli_stats[koli_no]["total"] += 1
        
        for sale_qr in sales_qrs:
            if sale_qr in inventory_dict:
                item = dict(inventory_dict[sale_qr])
                meta = item.get("metadata", {})
                koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO")
                if koli_no:
                    koli_no = str(koli_no).strip()
                    if koli_no in koli_stats:
                        koli_stats[koli_no]["sold"] += 1

                item["aciklama"] = "Bu ürün sistemde görünüyor, sistemden çık"
                matched_sales.append(item)
                sold_qrs_set.add(sale_qr)
                p_name = item["product_name"]
                sales_by_product[p_name] = sales_by_product.get(p_name, 0) + 1
            else:
                gtin = extract_gtin(sale_qr)
                p_name = gtin_map.get(gtin, "Sistem Dışı Ürün (GTIN: {})".format(gtin) if gtin else "Bilinmeyen Karekod formatı")
                unmatched_sales.append({
                    "qr": sale_qr,
                    "product_name": p_name,
                    "aciklama": "Depoda bulunamadı (sistem dışı satış)"
                })
                sales_by_product[p_name] = sales_by_product.get(p_name, 0) + 1
                
        for item in matched_sales:
            meta = item.get("metadata", {})
            koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO")
            if koli_no:
                koli_no = str(koli_no).strip()
                stats = koli_stats.get(koli_no)
                if stats:
                    sold_cnt = stats["sold"]
                    tot_cnt = stats["total"]
                    rem_cnt = max(0, tot_cnt - sold_cnt)
                    if sold_cnt >= tot_cnt:
                        item["Koli Durumu"] = f"Koli {koli_no}: Tamamı Satıldı ({sold_cnt}/{tot_cnt})"
                    else:
                        item["Koli Durumu"] = f"Koli {koli_no}: Kısmi Satıldı ({sold_cnt}/{tot_cnt} - Depoda {rem_cnt} Kaldı)"
            else:
                item["Koli Durumu"] = "Koli Bilgisi Yok"
                
        remaining_inventory = [item for item in inventory if item["qr"] not in sold_qrs_set]
        
        remaining_counts = {}
        for item in remaining_inventory:
            p_name = item["product_name"]
            remaining_counts[p_name] = remaining_counts.get(p_name, 0) + 1
            
        initial_counts = {}
        for item in inventory:
            p_name = item["product_name"]
            initial_counts[p_name] = initial_counts.get(p_name, 0) + 1

        with _state_lock:
            global cached_results
            cached_results = {
                "matched_sales": matched_sales,
                "unmatched_sales": unmatched_sales,
                "remaining_inventory": remaining_inventory,
                "sales_by_product": sales_by_product,
                "initial_counts": initial_counts,
                "remaining_counts": remaining_counts,
                "koli_stats": koli_stats
            }

        return jsonify({
            "success": True,
            "total_initial": len(inventory),
            "total_sold": len(sales_qrs),
            "total_matched": len(matched_sales),
            "total_unmatched": len(unmatched_sales),
            "total_remaining": len(remaining_inventory),
            "stats": {
                "total_system": len(inventory),
                "total_sales": len(sales_qrs),
                "matched": len(matched_sales),
                "remaining": len(remaining_inventory),
                "total_unmatched": len(unmatched_sales)
            },
            "matched_sales": matched_sales,
            "sales_by_product": sales_by_product,
            "remaining_counts": remaining_counts
        })
    except Exception as e:
        logger.error(f"api_compare error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/download/sales', methods=['GET'])
@app.route('/api/download/full_report', methods=['GET'])
def download_sales():
    with _state_lock:
        if not cached_results:
            return "No comparison run yet", 400
        res_copy = dict(cached_results)
        
    matched_rows = []
    for item in res_copy.get("matched_sales", []):
        meta = item.get("metadata", {})
        koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO") or ""
        gtin = extract_gtin(item["qr"]) or meta.get("Gtin Numarası") or meta.get("BARKOD") or ""
        seri_no = meta.get("Seri Numarası") or meta.get("SERINO") or ""
        parti_no = meta.get("Parti Numarası") or meta.get("SARJNO") or ""
        palet_no = meta.get("Palet Numarası") or meta.get("PALETNO") or ""
        uretim = meta.get("Üretim Tarihi") or meta.get("URETIMTARIHI") or ""
        skt = meta.get("Son Kullanma Tarihi") or meta.get("SKT") or ""

        row = {
            "Ürün Adı": item["product_name"],
            "Karekod": item["qr"],
            "Gtin / Barkod": gtin,
            "Koli Numarası": koli_no,
            "Seri Numarası": seri_no,
            "Parti Numarası": parti_no,
            "Palet Numarası": palet_no,
            "Üretim Tarihi": uretim,
            "Son Kullanma Tarihi": skt,
            "Koli / Depo Durumu": item.get("Koli Durumu", ""),
            "Açıklama / İşlem": "Bu ürün sistemde görünüyor, sistemden çık"
        }
        matched_rows.append(row)

    df_matched = pd.DataFrame(matched_rows)
    if not df_matched.empty:
        df_matched = df_matched.sort_values(by=["Ürün Adı"])
        
    unmatched_rows = []
    for item in res_copy.get("unmatched_sales", []):
        unmatched_rows.append({
            "Ürün Adı": item["product_name"],
            "Karekod": item["qr"],
            "Açıklama / Durum": "Depoda Bulunamadı (Sistem Dışı Hatalı Satış!)"
        })
    df_unmatched = pd.DataFrame(unmatched_rows)
    if not df_unmatched.empty:
        df_unmatched = df_unmatched.sort_values(by=["Ürün Adı"])
        
    summary_rows = []
    for p_name, qty in res_copy.get("sales_by_product", {}).items():
        summary_rows.append({
            "Ürün Adı": p_name,
            "Satılan Miktar (Adet)": qty
        })
    df_summary = pd.DataFrame(summary_rows)
    if not df_summary.empty:
        df_summary = df_summary.sort_values(by=["Ürün Adı"])
        
    koli_rows = []
    for koli_no, stats in res_copy.get("koli_stats", {}).items():
        if stats["sold"] > 0:
            tot = stats["total"]
            sold = stats["sold"]
            rem = max(0, tot - sold)
            status = f"Tamamı Satıldı ({sold}/{tot})" if sold >= tot else f"Kısmi Satıldı ({sold}/{tot} - Depoda {rem} Kaldı)"
            koli_rows.append({
                "Koli Numarası": koli_no,
                "Ürün Adı": stats["product_name"],
                "Toplam Ürün": tot,
                "Satılan Ürün": sold,
                "Depoda Kalan": rem,
                "Durum": status
            })
    df_koli = pd.DataFrame(koli_rows)
    if not df_koli.empty:
        df_koli = df_koli.sort_values(by=["Durum", "Koli Numarası"])
        
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if not df_matched.empty:
            df_matched.to_excel(writer, index=False, sheet_name="Düşülecek Satışlar")
        else:
            pd.DataFrame(columns=["Ürün Adı", "Karekod", "Koli Numarası", "Koli / Depo Durumu", "Açıklama / İşlem"]).to_excel(writer, index=False, sheet_name="Düşülecek Satışlar")
            
        if not df_unmatched.empty:
            df_unmatched.to_excel(writer, index=False, sheet_name="Depoda Olmayan Satışlar")
        if not df_koli.empty:
            df_koli.to_excel(writer, index=False, sheet_name="Koli Durum Özeti")
        if not df_summary.empty:
            df_summary.to_excel(writer, index=False, sheet_name="Satış Özet Tablosu")
            
    output.seek(0)
    
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="satis_raporu_detayli.xlsx"
    )

@app.route('/api/download/remaining', methods=['GET'])
def download_remaining():
    with _state_lock:
        if not cached_results:
            return "No comparison run yet", 400
        res_copy = dict(cached_results)
        
    rows = []
    for item in res_copy.get("remaining_inventory", []):
        meta = item.get("metadata", {})
        koli_no = meta.get("Koli Numarası") or meta.get("Paket Numarası") or meta.get("Palet Numarası") or meta.get("KOLINO") or ""
        gtin = extract_gtin(item["qr"]) or meta.get("Gtin Numarası") or meta.get("BARKOD") or ""
        seri_no = meta.get("Seri Numarası") or meta.get("SERINO") or ""
        parti_no = meta.get("Parti Numarası") or meta.get("SARJNO") or ""
        palet_no = meta.get("Palet Numarası") or meta.get("PALETNO") or ""
        uretim = meta.get("Üretim Tarihi") or meta.get("URETIMTARIHI") or ""
        skt = meta.get("Son Kullanma Tarihi") or meta.get("SKT") or ""

        row = {
            "Ürün Adı": item["product_name"],
            "Karekod": item["qr"],
            "Gtin / Barkod": gtin,
            "Koli Numarası": koli_no,
            "Seri Numarası": seri_no,
            "Parti Numarası": parti_no,
            "Palet Numarası": palet_no,
            "Üretim Tarihi": uretim,
            "Son Kullanma Tarihi": skt,
            "Durum": "Depoda Mevcut"
        }
        rows.append(row)
        
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(by=["Ürün Adı"])
        
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Güncel Kalan Envanter")
    output.seek(0)
    
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="kalan_sistem_envanteri_guncel.xlsx"
    )

def find_matching_koli(code, koli_dict):
    if not code or not koli_dict:
        return None, []

    raw_code = str(code).strip().upper()
    clean_code_all = re.sub(r'[^A-Z0-9]', '', raw_code.replace("İ", "I"))
    digits_only = re.sub(r'\D', '', raw_code)

    digits_stripped = digits_only
    if len(digits_only) == 20 and digits_only.startswith('00'):
        digits_stripped = digits_only[2:]

    if raw_code in koli_dict:
        return raw_code, koli_dict[raw_code]

    if code in koli_dict:
        return code, koli_dict[code]

    for k_key, k_rows in koli_dict.items():
        key_str = str(k_key).strip().upper()
        clean_key_all = re.sub(r'[^A-Z0-9]', '', key_str.replace("İ", "I"))
        key_digits = re.sub(r'\D', '', key_str)

        key_digits_stripped = key_digits
        if len(key_digits) == 20 and key_digits.startswith('00'):
            key_digits_stripped = key_digits[2:]

        if clean_code_all and clean_key_all and (clean_code_all == clean_key_all or clean_code_all.endswith(clean_key_all) or clean_key_all.endswith(clean_code_all)):
            return k_key, k_rows

        if digits_stripped and key_digits_stripped:
            if digits_stripped == key_digits_stripped or digits_stripped.lstrip('0') == key_digits_stripped.lstrip('0'):
                return k_key, k_rows

        if digits_only and key_digits and digits_only.lstrip('0') == key_digits.lstrip('0'):
            if len(raw_code) <= 8 or "KOLI" in clean_code_all or "PAKET" in clean_code_all or "PALET" in clean_code_all:
                return k_key, k_rows

    return None, []

@app.route('/api/audit_box', methods=['GET'])
def audit_box():
    code = request.args.get('code', '').strip().upper()
    if not code:
        return jsonify({"success": False, "error": "Barkod veya koli no boş olamaz."})

    df, qr_dict, gtin_dict, koli_dict = get_bkst_cache()
    code_norm = normalize_qr(code)
    target_koli = None
    matched_rows = []
    is_koli_scan = False
    scanned_qr = None

    if df is not None and not df.empty:
        matched_koli_key, matched_koli_rows = find_matching_koli(code, koli_dict)
        if matched_koli_key and matched_koli_rows:
            target_koli = matched_koli_key
            matched_rows = matched_koli_rows
            is_koli_scan = True

        if not target_koli and code_norm in qr_dict:
            item_row = qr_dict[code_norm]
            scanned_qr = str(item_row.get("Karekod", item_row.get("QR", ""))).strip()
            k_col = find_koli_column(item_row.keys())
            if k_col and item_row.get(k_col):
                val = str(item_row[k_col]).strip().upper()
                if val and val != "NAN":
                    target_koli = val
                    matched_rows = koli_dict.get(target_koli, [item_row])
            if not target_koli:
                target_koli = str(item_row.get("Ürün Adı", "Kolisiz Stok Ürün"))
                matched_rows = [item_row]

        if not target_koli:
            for q_key, r_dict in qr_dict.items():
                if code_norm in q_key or q_key in code_norm:
                    item_row = r_dict
                    scanned_qr = str(item_row.get("Karekod", item_row.get("QR", ""))).strip()
                    k_col = find_koli_column(item_row.keys())
                    if k_col and item_row.get(k_col):
                        val = str(item_row[k_col]).strip().upper()
                        if val and val != "NAN":
                            target_koli = val
                            matched_rows = koli_dict.get(target_koli, [item_row])
                    if not target_koli:
                        target_koli = str(item_row.get("Ürün Adı", "Kolisiz Stok Ürün"))
                        matched_rows = [item_row]
                    break

        if not target_koli and code in gtin_dict:
            item_row = gtin_dict[code]
            scanned_qr = str(item_row.get("Karekod", item_row.get("QR", ""))).strip()
            k_col = find_koli_column(item_row.keys())
            if k_col and item_row.get(k_col):
                val = str(item_row[k_col]).strip().upper()
                if val and val != "NAN":
                    target_koli = val
                    matched_rows = koli_dict.get(target_koli, [item_row])
            if not target_koli:
                target_koli = str(item_row.get("Ürün Adı", "Kolisiz Stok Ürün"))
                matched_rows = [item_row]

    if not target_koli or not matched_rows:
        target_koli = "Sistem Dışı Ürün"
        scanned_qr = code_norm if code_norm else code
        matched_rows = [{
            "Ürün Adı": "Sistem Dışı / Bilinmeyen Ürün",
            "Karekod": scanned_qr,
            "Gtin Numarası": extract_gtin(code_norm) or "—",
            "Seri Numarası": "—",
            "Parti Numarası": "—",
            "Palet Numarası": "—"
        }]

    results = []
    seen_qrs = set()
    for row in matched_rows:
        qr_val = str(row.get("Karekod", row.get("QR", ""))).strip()
        if not qr_val or qr_val in seen_qrs:
            continue
        seen_qrs.add(qr_val)

        results.append({
            "product_name": str(row.get("Ürün Adı", "Ürün")),
            "qr": qr_val,
            "koli_no": target_koli,
            "gtin": str(row.get("Gtin Numarası", row.get("BARKOD", ""))),
            "seri_no": str(row.get("Seri Numarası", row.get("SERINO", ""))),
            "parti_no": str(row.get("Parti Numarası", row.get("SARJNO", ""))),
            "palet_no": str(row.get("Palet Numarası", row.get("PALETNO", ""))),
            "uretim_tarihi": str(row.get("Üretim Tarihi", row.get("URETIMTARIHI", ""))),
            "skt": str(row.get("Son Kullanma Tarihi", row.get("SKT", "")))
        })

    return jsonify({
        "success": True,
        "is_koli_scan": is_koli_scan,
        "scanned_qr": scanned_qr,
        "koli_no": target_koli,
        "total_items": len(results),
        "items": results
    })

@app.route('/api/audit_all', methods=['POST', 'GET'])
def audit_all():
    df, qr_dict, gtin_dict, koli_dict = get_bkst_cache()
    if df is None or df.empty:
        return jsonify({"success": False, "error": "Bakanlık depo verisi bulunamadı."})

    gtins = []
    product_names = []

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        gtins = data.get('gtins', [])
        product_names = data.get('product_names', [])
    else:
        gtin_str = request.args.get('gtins', '')
        if gtin_str:
            gtins = [g.strip() for g in gtin_str.split(',') if g.strip()]

    scanned_gtin_norms = set()
    for g in gtins:
        if not g: continue
        digits = re.sub(r'\D', '', str(g)).lstrip('0')
        if digits:
            scanned_gtin_norms.add(digits)

    scanned_pnames = set(str(p).strip().upper() for p in product_names if p and str(p).strip() != '—')

    results = []
    seen_qrs = set()
    koli_col = find_koli_column(df.columns)

    for _, row in df.iterrows():
        r = row.to_dict()
        qr_val = str(r.get("Karekod", r.get("QR", ""))).strip()
        if not qr_val or qr_val in seen_qrs:
            continue

        raw_gtin = str(r.get("Gtin Numarası", r.get("BARKOD", ""))).strip()
        if not raw_gtin or raw_gtin == "—" or raw_gtin == "NAN":
            raw_gtin = extract_gtin(qr_val) or ""

        row_gtin_norm = re.sub(r'\D', '', raw_gtin).lstrip('0')
        row_pname = str(r.get("Ürün Adı", "")).strip().upper()

        gtin_matched = scanned_gtin_norms and row_gtin_norm in scanned_gtin_norms
        pname_matched = scanned_pnames and row_pname in scanned_pnames

        if (scanned_gtin_norms or scanned_pnames) and not (gtin_matched or pname_matched):
            continue

        seen_qrs.add(qr_val)
        koli_val = str(r.get(koli_col, "")).strip() if koli_col and pd.notna(r.get(koli_col)) else "Kolisiz Stok"

        results.append({
            "product_name": str(r.get("Ürün Adı", "Ürün")),
            "qr": qr_val,
            "koli_no": koli_val,
            "gtin": raw_gtin or "—",
            "seri_no": str(r.get("Seri Numarası", r.get("SERINO", ""))),
            "parti_no": str(r.get("Parti Numarası", r.get("SARJNO", ""))),
            "palet_no": str(r.get("Palet Numarası", r.get("PALETNO", ""))),
            "uretim_tarihi": str(r.get("Üretim Tarihi", r.get("URETIMTARIHI", ""))),
            "skt": str(r.get("Son Kullanma Tarihi", r.get("SKT", "")))
        })

    return jsonify({
        "success": True,
        "total_items": len(results),
        "gtin_count": len(scanned_gtin_norms),
        "items": results
    })

@app.route('/api/download/audit_excel', methods=['POST'])
def download_audit_excel():
    try:
        data = request.json
        if not data or not isinstance(data, list):
             return jsonify({"error": "Geçersiz veri"}), 400
             
        df = pd.DataFrame(data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="Eksik Ürünler")
            
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name='koli_sayim_eksikler.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"download_audit_excel error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

def check_internet_connection():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(("8.8.8.8", 53))
        s.close()
        return True
    except Exception:
        return False

# ── BKST ASYNC WORKER & API ROUTES ───────────────────────────────────────────
def _do_fetch_api_worker():
    global bkst_status, bkst_message, _app_bkst_synced
    logger.info("BKST fetch_api worker thread started")
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password or username == "" or password == "":
        with _state_lock:
            bkst_status = "error"
            bkst_message = "❌ HATA: 'bakanlik_giris_bilgileri.txt' dosyasında KULLANICI_ADI veya SIFRE bulunamadı!"
        return

    if not check_internet_connection():
        df_cache, _, _, _ = get_bkst_cache()
        item_count = len(df_cache) if df_cache is not None else 0
        with _state_lock:
            bkst_status = "done"
            bkst_message = f"İnternet bağlantısı yok. Yerel veritabanındaki ({item_count} adet) stok verileri yüklendi."
        return
        
    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest"
        })

        if api_key:
            session.headers.update({"Authorization": f"Bearer {api_key}", "Key": api_key})
        
        try:
            r_home = session.get("https://bkst.tarbil.gov.tr/", verify=False, timeout=(5, 10))
            if r_home.status_code != 200:
                raise Exception(f"Bakanlık sunucu yanıtı HTTP {r_home.status_code}")
        except Exception as e_home:
            logger.warning(f"Ministry home connect error: {e_home}")
            df_cache, _, _, _ = get_bkst_cache()
            item_count = len(df_cache) if df_cache is not None else 0
            with _state_lock:
                bkst_status = "done"
                bkst_message = "Bakanlık sunucusuna erişilemiyor. Yerel çevrimdışı veriler gösteriliyor."
            return

        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=False, timeout=(5, 15))

        if "0" not in res_login.text:
            with _state_lock:
                bkst_status = "error"
                bkst_message = "❌ HATA: Bakanlık kullanıcı adı veya şifreniz yanlış!"
            return

        r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=False, timeout=(5, 10))
        token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
        token2 = token2_match.group(1) if token2_match else token1

        r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=False, timeout=(5, 10))
        gln_guid = address_id
        if r_gln.status_code == 200:
            try:
                gln_data = r_gln.json()
                if isinstance(gln_data, list) and len(gln_data) > 0:
                    gln_guid = str(gln_data[0].get("Value") or "").strip()
            except Exception:
                pass

        if not gln_guid:
            gln_guid = "8aaf058e-7444-48bb-bd74-4077173fa6a8"

        r_grid = session.post("https://bkst.tarbil.gov.tr/Main/GetStockList", data={"CompanyAddressId": gln_guid, "Gtin": "", "__RequestVerificationToken": token2}, verify=False, timeout=(5, 15))
        gtin_list = r_grid.json().get("Data", []) if r_grid.status_code == 200 else []

        all_rows = []
        for g_item in gtin_list:
            gtin_code = g_item.get("BARKOD")
            prod_name = g_item.get("URUNADI")
            if not gtin_code:
                continue

            session.post("https://bkst.tarbil.gov.tr/Main/GetViewReport", data={"gtin": gtin_code, "gln": gln_guid, "__RequestVerificationToken": token2}, verify=False, timeout=(5, 10))
            r_detail = session.post("https://bkst.tarbil.gov.tr/Main/GetStockDetailList", data={"CompanyAddressId": gln_guid, "Gtin": gtin_code, "__RequestVerificationToken": token2}, verify=False, timeout=(5, 15))

            if r_detail.status_code == 200:
                try:
                    d_items = r_detail.json()
                    if isinstance(d_items, list):
                        for item in d_items:
                            koli = item.get("PAKETNO") or item.get("KOLINO") or item.get("PALETNO") or ""
                            all_rows.append({
                                "Koli Numarası": koli,
                                "Ürün Adı": prod_name or item.get("URUNADI") or "",
                                "Karekod": item.get("KAREKOD") or item.get("HAMKAREKOD") or "",
                                "Gtin / Barkod": item.get("BARKOD") or gtin_code,
                                "Seri Numarası": item.get("SERINO") or "",
                                "Parti Numarası": item.get("SARJNO") or "",
                                "Palet Numarası": item.get("PALETNO") or "",
                                "Üretim Tarihi": item.get("URETIMTARIHI") or "",
                                "Son Kullanma Tarihi": item.get("SKT") or ""
                            })
                except Exception:
                    pass

        df = pd.DataFrame(all_rows)
        save_bkst_data_to_db(df, username)

        df_cache, _, _, _ = get_bkst_cache()
        item_count = len(df_cache) if df_cache is not None else len(all_rows)

        with _state_lock:
            _app_bkst_synced = True
            if item_count == 0:
                bkst_status = "done"
                bkst_message = "⚠️ UYARI: Bakanlık sisteminde kayıtlı stok bulunamadı (0 adet)."
            else:
                bkst_status = "done"
                bkst_message = f"🟢 TEBRİKLER! Bakanlık stok verileri API ile başarıyla çekildi! Toplam {item_count} adet ürün hazır."
        logger.info(f"BKST fetch_api worker finished with {item_count} items")

    except Exception as e:
        logger.error(f"BKST fetch_api worker error: {e}", exc_info=True)
        with _state_lock:
            bkst_status = "error"
            bkst_message = "❌ HATA: Bakanlık API bağlantı hatası: " + str(e)

@app.route('/api/bkst/fetch_api', methods=['POST'])
def bkst_fetch_api_start():
    global _fetch_future, bkst_status, bkst_message
    with _state_lock:
        if _fetch_future and not _fetch_future.done():
            return jsonify({"success": False, "error": "Zaten devam eden bir veri çekme işlemi var."})
        bkst_status = "fetching"
        bkst_message = "⚡ Bakanlık verileri API üzerinden çekiliyor..."
        _fetch_future = _fetch_executor.submit(_do_fetch_api_worker)
    return jsonify({"success": True, "status": "started", "message": "Veri çekme işlemi başlatıldı."})

@app.route('/api/bkst/fetch_status', methods=['GET'])
def bkst_fetch_status():
    with _state_lock:
        running = bool(_fetch_future and not _fetch_future.done())
        return jsonify({
            "running": running,
            "status": bkst_status,
            "message": bkst_message
        })

@app.route('/api/bkst/download_api_data', methods=['GET'])
@app.route('/api/bkst/download', methods=['GET'])
def download_api_data():
    df, _, _, _ = get_bkst_cache()
    if df is None or df.empty:
        return jsonify({"error": "Henüz stok verisi çekilmedi."}), 404
        
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Tüm Ürünler (QR)")
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name="bkst_depo_verileri_guncel.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ── SİSTEMDEN ÇIKIŞ KABUL VE İŞLEMLERİ ────────────────────────────────────────
@app.route('/api/depo_stoklari', methods=['GET'])
def api_depo_stoklari():
    try:
        df, _, _, _ = get_bkst_cache()
        rows = []
        if df is not None and not df.empty:
            df_clean = df.fillna("")
            for col in df_clean.columns:
                df_clean[col] = df_clean[col].astype(str)
            rows = df_clean.to_dict(orient="records")

        return jsonify({
            "success": True,
            "products": rows,
            "total": len(rows)
        })
    except Exception as e:
        logger.error(f"api_depo_stoklari error: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "products": [],
            "total": 0,
            "error": str(e)
        })

@app.route('/api/cikis/okut', methods=['POST'])
def cikis_okut():
    try:
        data = request.json or {}
        barkod_raw = data.get('barkod', '').strip()
        if not barkod_raw:
            return jsonify({'success': False, 'error': 'Barkod boş olamaz.'})

        barkod_norm = normalize_qr(barkod_raw)
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        if username:
            c.execute('SELECT id, tarih, urun_adi FROM cikis_kayitlari WHERE ham_karekod = ? AND (kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")', (barkod_norm, username))
        else:
            c.execute('SELECT id, tarih, urun_adi FROM cikis_kayitlari WHERE ham_karekod = ?', (barkod_norm,))
        existing = c.fetchone()
        conn.close()

        if existing:
            ex_id, ex_tarih, ex_urun = existing
            return jsonify({
                'success': False,
                'already_exited': True,
                'error': f'Bu ürün zaten depodan çıkarılmış! Ürün: {ex_urun} (Tarih: {ex_tarih})'
            })

        df, qr_map, gtin_map, koli_map = get_bkst_cache()
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'error': 'Bakanlık depo verisi bulunamadı. Lütfen önce verileri çekin.'
            })

        match_row = qr_map.get(barkod_norm) if qr_map else None
        if match_row is None and gtin_map:
            match_row = gtin_map.get(barkod_norm)

        if match_row is None and qr_map:
            for k_qr, r_dict in qr_map.items():
                if barkod_norm in k_qr or k_qr in barkod_norm:
                    match_row = r_dict
                    break

        if match_row is None:
            return jsonify({
                'success': False,
                'error': f'"{barkod_raw}" barkoduna ait ürün Bakanlık depo verisinde bulunamadı.'
            })

        tarih         = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        urun_adi      = str(match_row.get('Ürün Adı', '')).strip()
        barkod_col    = str(match_row.get('Gtin Numarası', '')).strip()
        koli_no       = str(match_row.get('Koli Numarası', '')).strip()
        seri_no       = str(match_row.get('Seri Numarası', '')).strip()
        parti_no      = str(match_row.get('Parti Numarası', '')).strip()
        palet_no      = str(match_row.get('Palet Numarası', '')).strip()
        uretim_tarihi = str(match_row.get('Üretim Tarihi', '')).strip()
        skt           = str(match_row.get('Son Kullanma Tarihi', '')).strip()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute('''INSERT INTO cikis_kayitlari
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)''',
            (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, barkod_norm, username))
        new_id = c.lastrowid
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'tekrar_uyari': False,
            'kayit': {
                'id': new_id,
                'tarih': tarih,
                'urun_adi': urun_adi,
                'barkod': barkod_col,
                'koli_no': koli_no,
                'seri_no': seri_no,
                'parti_no': parti_no,
                'palet_no': palet_no,
                'uretim_tarihi': uretim_tarihi,
                'skt': skt,
                'ham_karekod': barkod_norm
            }
        })
    except Exception as e:
        logger.error(f"cikis_okut error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'Kayıt sırasında hata: {str(e)}'})

@app.route('/api/cikis/toplu_ekle', methods=['POST'])
def cikis_toplu_ekle():
    try:
        data = request.json or {}
        items = data.get('items', [])
        if not items or not isinstance(items, list):
            return jsonify({'success': False, 'error': 'Aktarılacak karekod listesi bulunamadı.'})

        df, qr_map, gtin_map, koli_map = get_bkst_cache()
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        c = conn.cursor()

        if username:
            c.execute('SELECT ham_karekod FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ""', (username,))
        else:
            c.execute('SELECT ham_karekod FROM cikis_kayitlari')
        existing_set = set(row[0] for row in c.fetchall() if row[0])

        insert_rows = []
        added_count = 0
        already_count = 0
        tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for barkod_raw in items:
            barkod_norm = normalize_qr(str(barkod_raw).strip())
            if not barkod_norm:
                continue

            if barkod_norm in existing_set:
                already_count += 1
                continue

            existing_set.add(barkod_norm)

            match_row = qr_map.get(barkod_norm) if qr_map else None
            if match_row is None and gtin_map:
                match_row = gtin_map.get(barkod_norm)

            if match_row is None and qr_map:
                for k_qr, r_dict in qr_map.items():
                    if barkod_norm in k_qr or k_qr in barkod_norm:
                        match_row = r_dict
                        break

            if match_row is None:
                urun_adi = "Tanımsız Ürün"
                barkod_col = ""
                koli_no = ""
                seri_no = ""
                parti_no = ""
                palet_no = ""
                uretim_tarihi = ""
                skt = ""
            else:
                urun_adi      = str(match_row.get('Ürün Adı', '')).strip()
                barkod_col    = str(match_row.get('Gtin Numarası', '')).strip()
                koli_no       = str(match_row.get('Koli Numarası', '')).strip()
                seri_no       = str(match_row.get('Seri Numarası', '')).strip()
                parti_no      = str(match_row.get('Parti Numarası', '')).strip()
                palet_no      = str(match_row.get('Palet Numarası', '')).strip()
                uretim_tarihi = str(match_row.get('Üretim Tarihi', '')).strip()
                skt           = str(match_row.get('Son Kullanma Tarihi', '')).strip()

            insert_rows.append((tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
                                uretim_tarihi, skt, barkod_norm, 0, username))
            added_count += 1

        if insert_rows:
            c.executemany('''INSERT INTO cikis_kayitlari
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', insert_rows)
            conn.commit()

        conn.close()

        return jsonify({
            'success': True,
            'added_count': added_count,
            'already_count': already_count,
            'message': f'{added_count} adet ürün Çıkış Listesine aktarıldı.'
        })
    except Exception as e:
        logger.error(f"cikis_toplu_ekle error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'added_count': 0,
            'already_count': 0,
            'error': f'Toplu ekleme hatası: {str(e)}'
        })

@app.route('/api/cikis/listesi', methods=['GET'])
def cikis_listesi_api():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM cikis_kayitlari ORDER BY id DESC')
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        clean_rows = []
        for row in rows:
            clean_row = {}
            for k, v in row.items():
                clean_row[k] = "" if v is None else str(v)
            clean_rows.append(clean_row)

        return jsonify({'success': True, 'kayitlar': clean_rows, 'toplam': len(clean_rows)})
    except Exception as e:
        logger.error(f"cikis_listesi_api error: {e}", exc_info=True)
        return jsonify({'success': False, 'kayitlar': [], 'toplam': 0, 'error': str(e)})

@app.route('/api/cikis/sil/<int:kayit_id>', methods=['DELETE'])
def cikis_sil(kayit_id):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute('DELETE FROM cikis_kayitlari WHERE id = ?', (kayit_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"cikis_sil error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cikis/temizle', methods=['POST'])
def cikis_temizle():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute('DELETE FROM cikis_kayitlari')
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'mesaj': 'Tüm çıkış kayıtları silindi.'})
    except Exception as e:
        logger.error(f"cikis_temizle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cikis/indir', methods=['GET'])
def cikis_indir():
    username, _, _, _ = read_bkst_credentials()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    if username:
        df = pd.read_sql_query('SELECT * FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "" ORDER BY id DESC', conn, params=(username,))
    else:
        df = pd.read_sql_query('SELECT * FROM cikis_kayitlari ORDER BY id DESC', conn)
    conn.close()

    if 'kullanici_adi' in df.columns:
        df = df.drop(columns=['kullanici_adi'])

    df = df.rename(columns={
        'id':            'ID',
        'tarih':         'Tarih/Saat',
        'urun_adi':      'Ürün Adı',
        'barkod':        'Gtin/Barkod',
        'koli_no':       'Koli Numarası',
        'seri_no':       'Seri Numarası',
        'parti_no':      'Parti Numarası',
        'palet_no':      'Palet Numarası',
        'uretim_tarihi': 'Üretim Tarihi',
        'skt':           'Son Kullanma Tarihi',
        'ham_karekod':   'Tam Karekod',
        'tekrar_uyari':  'Tekrar Okutuldu'
    })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sistemden Çıkışlar')
    output.seek(0)

    fname = f'cikis_listesi_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return send_file(output, as_attachment=True, download_name=fname,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ── BKST AUTHENTICATED SESSION HELPER ────────────────────────────────────────
def get_bkst_authenticated_session():
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password:
        return None, None, None, "Kullanıcı adı veya şifre bulunamadı."
    
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest"
    })
    if api_key:
        session.headers.update({"Authorization": f"Bearer {api_key}", "Key": api_key})

    r_home = session.get("https://bkst.tarbil.gov.tr/", verify=False, timeout=(5, 10))
    token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
    token1 = token_match.group(1) if token_match else ""

    login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
    res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=False, timeout=(5, 15))
    if "0" not in res_login.text:
        return None, None, None, "Bakanlık kullanıcı adı veya şifreniz hatalı."

    r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=False, timeout=(5, 10))
    token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
    token2 = token2_match.group(1) if token2_match else token1

    r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=False, timeout=(5, 10))
    gln_guid = address_id
    if r_gln.status_code == 200:
        try:
            gln_data = r_gln.json()
            if isinstance(gln_data, list) and len(gln_data) > 0:
                gln_guid = str(gln_data[0].get("Value") or "").strip()
        except Exception:
            pass

    return session, gln_guid, token2, None

@app.route('/api/bkst/recetesiz_satis/sms_gonder', methods=['POST'])
def bkst_sms_gonder():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    if not tc_no:
        return jsonify({'success': False, 'error': 'T.C. Kimlik veya Vergi No giriniz.'})

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=False, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'OperationType': 1,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/SendSmsVerificationCode', data=payload, verify=False, timeout=(5, 15))
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('IsSuccess') is True:
                return jsonify({
                    'success': True,
                    'phone_hidden': res_json.get('MobilePhoneHidden', ''),
                    'verification_token': res_json.get('VerificationCode', ''),
                    'message': f"📲 SMS doğrulama kodu {res_json.get('MobilePhoneHidden', '')} numaralı telefona gönderildi."
                })
            else:
                return jsonify({'success': False, 'error': res_json.get('Message') or 'SMS gönderilemedi.'})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})
    except Exception as e:
        logger.error(f"bkst_sms_gonder error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f"SMS Gönderme Hatası: {str(e)}"})

@app.route('/api/bkst/recetesiz_satis/sms_dogrula', methods=['POST'])
def bkst_sms_dogrula():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    sms_code = str(data.get('sms_code', '')).strip()
    verification_token = str(data.get('verification_token', '')).strip()

    if not tc_no or not sms_code or not verification_token:
        return jsonify({'success': False, 'error': 'Eksik parametre. T.C. No ve SMS kodu gereklidir.'})

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=False, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'VerificationCode': verification_token,
            'Code': sms_code,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/CheckSmsVerificationCode', data=payload, verify=False, timeout=(5, 15))
        if res.status_code == 200:
            res_str = res.text.strip().replace('"', '')
            if res_str and res_str != "00000000-0000-0000-0000-000000000000":
                return jsonify({
                    'success': True,
                    'verified_token': res_str,
                    'message': '🟢 SMS doğrulaması başarıyla onaylandı!'
                })
            else:
                return jsonify({'success': False, 'error': '❌ Girilen SMS doğrulama kodu hatalı veya süresi dolmuş.'})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})
    except Exception as e:
        logger.error(f"bkst_sms_dogrula error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f"SMS Doğrulama Hatası: {str(e)}"})

@app.route('/api/bkst/recetesiz_satis', methods=['POST'])
def bkst_recetesiz_satis():
    data = request.json or {}
    tc_no = str(data.get('tc_no', '')).strip()
    verification_token = str(data.get('verification_token', '')).strip()
    karekods = data.get('karekods', [])
    belge_no = str(data.get('belge_no', '')).strip()
    aciklama = str(data.get('aciklama', 'Reçetesiz Satış')).strip()

    if not tc_no:
        return jsonify({'success': False, 'error': 'T.C. Kimlik veya Vergi No boş olamaz.'})
    if not karekods or not isinstance(karekods, list):
        return jsonify({'success': False, 'error': 'Satış yapılacak ürün/karekod bulunamadı.'})

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err or not session:
        return jsonify({'success': False, 'error': err or 'BKST oturumu açılamadı.'})

    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=False, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        df_cache, qr_map, gtin_map, koli_map = get_bkst_cache()

        datasource_items = []
        for raw_qr in karekods:
            norm_qr = normalize_qr(str(raw_qr).strip())
            if not norm_qr:
                continue

            match_row = qr_map.get(norm_qr) or gtin_map.get(norm_qr) if qr_map else None
            if match_row is None and qr_map:
                for k_qr, r_dict in qr_map.items():
                    if norm_qr in k_qr or k_qr in norm_qr:
                        match_row = r_dict
                        break

            gtin = str(match_row.get('Gtin Numarası', '')).strip() if match_row else ""
            seri = str(match_row.get('Seri Numarası', '')).strip() if match_row else ""
            parti = str(match_row.get('Parti Numarası', '')).strip() if match_row else ""
            koli = str(match_row.get('Koli Numarası', '')).strip() if match_row else ""
            urun = str(match_row.get('Ürün Adı', '')).strip() if match_row else "Bitki Koruma Ürünü"
            skt = str(match_row.get('Son Kullanma Tarihi', '')).strip() if match_row else ""

            datasource_items.append({
                "BARKOD": gtin,
                "KAREKOD": norm_qr,
                "SERIALNUMBER": seri,
                "SARJNO": parti,
                "KOLINO": koli,
                "URUNADI": urun,
                "SKT": skt,
                "QUANTITY": 1
            })

        today_str = datetime.now().strftime("%Y.%m.%d")
        payload = {
            'SenderAddress': gln_guid,
            'IdTaxNo': tc_no,
            'DocumentNo': belge_no or f"SATIS-{datetime.now().strftime('%Y%m%d%H%M')}",
            'DocumentDate': today_str,
            'Desc': aciklama,
            'DataSource': json.dumps(datasource_items),
            'ParcelList': '',
            'HarmfulChoice': '',
            'Province': '',
            'VerificationCode': verification_token,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/NewCheckOutNotificationForProducerNonPrescribed', data=payload, verify=False, timeout=(5, 20))

        if res.status_code == 200:
            try:
                res_data = res.json()
                if res_data.get('Result') is True or res_data == 1:
                    username_cur, _, _, _ = read_bkst_credentials()
                    conn = sqlite3.connect(DB_PATH, timeout=30.0)
                    c = conn.cursor()
                    tarih_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    for item in datasource_items:
                        c.execute('''INSERT INTO cikis_kayitlari
                            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)''',
                            (tarih_now, item['URUNADI'], item['BARKOD'], item['KOLINO'], item['SERIALNUMBER'], item['SARJNO'], '', '', item['SKT'], item['KAREKOD'], username_cur))
                    conn.commit()
                    conn.close()

                    return jsonify({
                        'success': True,
                        'message': f"🟢 BAŞARILI! Bakanlık BKST sistemine {len(datasource_items)} adet ürünün Reçetesiz Satış bildirimi API ile tamamlandı!"
                    })
                else:
                    err_msgs = []
                    data_errs = res_data.get('Data', [])
                    if isinstance(data_errs, list):
                        for e in data_errs:
                            if isinstance(e, dict) and e.get('Message'):
                                err_msgs.append(e.get('Message'))
                    err_text = " - ".join(err_msgs) if err_msgs else str(res_data)
                    return jsonify({'success': False, 'error': f"BKST Hata Bildirimi: {err_text}"})
            except Exception:
                return jsonify({'success': False, 'error': f"Bakanlık Yanıtı: {res.text[:300]}"})
        else:
            return jsonify({'success': False, 'error': f"BKST Sunucu Hatası ({res.status_code})"})

    except Exception as e:
        logger.error(f"bkst_recetesiz_satis error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f"API Bağlantı Hatası: {str(e)}"})

def format_date_val(val):
    if not val or str(val).strip() in ["", "None", "nan", "NaN", "null"]:
        return ""
    val_str = str(val).strip()
    if "/Date(" in val_str:
        try:
            m = re.search(r'\d+', val_str)
            if m:
                ts = int(m.group()) / 1000.0
                return datetime.fromtimestamp(ts).strftime("%d.%m.%Y")
        except Exception:
            pass
    if "T" in val_str:
        try:
            clean_iso = val_str.split("T")[0]
            parts = clean_iso.split("-")
            if len(parts) == 3:
                return f"{parts[2]}.{parts[1]}.{parts[0]}"
        except Exception:
            pass
    return val_str

@app.route('/api/depo_kabul/gelen_listesi', methods=['POST', 'GET'])
def api_depo_kabul_gelen_listesi():
    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err:
        return jsonify({"success": False, "error": err})

    notifications = []
    try:
        endpoints = [
            "https://bkst.tarbil.gov.tr/Main/GetReceivedNotificationList",
            "https://bkst.tarbil.gov.tr/Main/GetNotificationList",
            "https://bkst.tarbil.gov.tr/Main/ReceivedNotificationList"
        ]
        
        for ep in endpoints:
            try:
                res = session.post(ep, data={
                    "CompanyAddressId": gln_guid,
                    "NotificationType": "1",
                    "NotificationDirection": "1",
                    "HeaderState": "0",
                    "__RequestVerificationToken": token2
                }, verify=False, timeout=(5, 12))
                
                if res.status_code == 200:
                    try:
                        jdata = res.json()
                        raw_list = jdata.get("Data") if isinstance(jdata, dict) else (jdata if isinstance(jdata, list) else [])
                        if isinstance(raw_list, list) and len(raw_list) > 0:
                            for item in raw_list:
                                op = str(item.get("OPERATION") or item.get("OperationName") or item.get("ISLEMTIPI") or item.get("NotificationType") or item.get("DESCR") or item.get("Operation") or "").upper()
                                direction = str(item.get("NotificationDirection") or item.get("DIRECTION") or item.get("YON") or "").upper()

                                if any(x in op for x in ["SATIS", "SATIŞ", "CIKIS", "ÇIKIŞ", "DEVR", "GÖNDER", "SATIŞA"]):
                                    continue
                                if direction in ["2", "OUT", "OUTGOING", "GİDEN", "GIDEN"]:
                                    continue

                                notifications.append({
                                    "HEADERID": item.get("HEADERID") or item.get("Id") or item.get("ID") or str(item.get("WAYBILLNUMBER", "")),
                                    "WAYBILLNUMBER": item.get("WAYBILLNUMBER") or item.get("WaybillNumber") or item.get("BELGENO") or "-",
                                    "WAYBILLDATE": format_date_val(item.get("WAYBILLDATE") or item.get("WaybillDate") or item.get("TARIH")),
                                    "SENDER": item.get("CompanyTitle") or item.get("SENDER") or item.get("GonderenFirma") or item.get("FIRMA") or "Tedarikçi / Üretici",
                                    "PRODUCTCOUNT": item.get("PRODUCTCOUNT") or item.get("ProductCount") or item.get("ADET") or 0,
                                    "HEADERSTATE": item.get("HEADERSTATE") or item.get("StateDescription") or "Bekliyor",
                                    "OPERATION": "MALALIM",
                                    "products": item.get("products") or []
                                })
                            break
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception as e:
        logger.error(f"BKST gelen bildirim hatası: {e}")

    return jsonify({
        "success": True,
        "notifications": notifications,
        "message": f"Sadece Tipi 'MAL ALIM' (Gelen) olan {len(notifications)} adet bildirim filtreler ile listelendi." if notifications else "Gelen/bekleyen MAL ALIM bildirimi bulunamadı."
    })

@app.route('/api/depo_kabul/detay/<header_id>', methods=['GET'])
def api_depo_kabul_detay(header_id):
    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err:
        return jsonify({"success": False, "error": err, "products": []})

    products = []
    try:
        endpoints = [
            "https://bkst.tarbil.gov.tr/Main/GetReceivedNotificationDetailList",
            "https://bkst.tarbil.gov.tr/Main/GetNotificationDetailList",
            "https://bkst.tarbil.gov.tr/Main/GetNotificationDetail"
        ]
        for ep in endpoints:
            try:
                res = session.post(ep, data={"HeaderId": header_id, "CompanyAddressId": gln_guid, "__RequestVerificationToken": token2}, verify=False, timeout=(5, 12))
                if res.status_code == 200:
                    jdata = res.json()
                    raw_list = jdata.get("Data") if isinstance(jdata, dict) else (jdata if isinstance(jdata, list) else [])
                    if isinstance(raw_list, list) and len(raw_list) > 0:
                        for item in raw_list:
                            products.append({
                                "Koli Numarası": item.get("PAKETNO") or item.get("KOLINO") or item.get("PALETNO") or "",
                                "Ürün Adı": item.get("STOCKNAME") or item.get("URUNADI") or item.get("ProductName") or "",
                                "Karekod": item.get("KAREKOD") or item.get("HAMKAREKOD") or item.get("Barcode") or "",
                                "Gtin / Barkod": item.get("BARCODE") or item.get("GTIN") or item.get("Gtin") or "",
                                "Seri Numarası": item.get("SERIALNUMBER") or item.get("SERINO") or item.get("SerialNumber") or "",
                                "Parti Numarası": item.get("SARJNO") or item.get("LOT") or item.get("BatchNumber") or "",
                                "Palet Numarası": item.get("PALETNO") or "",
                                "Üretim Tarihi": format_date_val(item.get("URETIMTARIHI") or item.get("ProductionDate")),
                                "Son Kullanma Tarihi": format_date_val(item.get("SKT") or item.get("ExpirationDate"))
                            })
                        break
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Detail fetch error: {e}")

    return jsonify({
        "success": True,
        "products": products
    })

@app.route('/api/depo_kabul/onayla', methods=['POST'])
def api_depo_kabul_onayla():
    req_data = request.get_json() or {}
    header_id = req_data.get('header_id')
    incoming_products = req_data.get('products') or []

    session, gln_guid, token2, err = get_bkst_authenticated_session()
    bkst_msg = ""
    if session and gln_guid and token2:
        accept_endpoints = [
            "https://bkst.tarbil.gov.tr/Main/NotificationAccept",
            "https://bkst.tarbil.gov.tr/Main/SaveNotificationAccept",
            "https://bkst.tarbil.gov.tr/Main/ConfirmNotification",
            "https://bkst.tarbil.gov.tr/Main/SaveMalAlim"
        ]
        for ep in accept_endpoints:
            try:
                res = session.post(ep, data={"HeaderId": header_id, "CompanyAddressId": gln_guid, "__RequestVerificationToken": token2}, verify=False, timeout=(5, 12))
                if res.status_code == 200:
                    bkst_msg = "Bakanlık (BKST) mal alım bildirimi onaylandı."
                    break
            except Exception:
                pass

    username, _, _, _ = read_bkst_credentials()
    df_existing, _, _, _ = get_bkst_cache()
    
    cols = ["Koli Numarası", "Ürün Adı", "Karekod", "Gtin / Barkod", "Seri Numarası", "Parti Numarası", "Palet Numarası", "Üretim Tarihi", "Son Kullanma Tarihi"]
    existing_karekods = set()
    if df_existing is not None and not df_existing.empty and 'Karekod' in df_existing.columns:
        existing_karekods = set(df_existing['Karekod'].dropna().astype(str).str.strip())
    
    new_rows = []
    added_count = 0
    for p in incoming_products:
        qr = str(p.get("Karekod") or p.get("KAREKOD") or "").strip()
        if qr and qr in existing_karekods:
            continue
        
        row = {
            "Koli Numarası": p.get("Koli Numarası") or p.get("PAKETNO") or p.get("KOLINO") or "",
            "Ürün Adı": p.get("Ürün Adı") or p.get("STOCKNAME") or p.get("URUNADI") or "",
            "Karekod": qr,
            "Gtin / Barkod": p.get("Gtin / Barkod") or p.get("BARCODE") or p.get("GTIN") or "",
            "Seri Numarası": p.get("Seri Numarası") or p.get("SERIALNUMBER") or p.get("SERINO") or "",
            "Parti Numarası": p.get("Parti Numarası") or p.get("SARJNO") or p.get("LOT") or "",
            "Palet Numarası": p.get("Palet Numarası") or p.get("PALETNO") or "",
            "Üretim Tarihi": format_date_val(p.get("Üretim Tarihi") or p.get("URETIMTARIHI")),
            "Son Kullanma Tarihi": format_date_val(p.get("Son Kullanma Tarihi") or p.get("SKT"))
        }
        new_rows.append(row)
        if qr:
            existing_karekods.add(qr)
        added_count += 1

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        if df_existing is not None and not df_existing.empty:
            updated_df = pd.concat([df_existing, new_df], ignore_index=True)
        else:
            updated_df = new_df
        save_bkst_data_to_db(updated_df, username)

    msg = f"🟢 Mal Alım bildirimi kabul edildi ve {added_count} adet ürün yerel veritabanınıza eklendi."
    if bkst_msg:
        msg += f" ({bkst_msg})"

    return jsonify({
        "success": True,
        "message": msg,
        "added_count": added_count
    })

# ── SİSTEM AUTHENTICATION API ROUTES ─────────────────────────────────────────
@app.route('/api/system/login', methods=['POST'])
def api_system_login():
    with _state_lock:
        global _app_bkst_synced
        _app_bkst_synced = False

    data = request.json or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()
    address_id = str(data.get('address_id', '')).strip()

    if not username or not password:
        return jsonify({'success': False, 'error': 'Kullanıcı adı ve şifre giriniz.'})

    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    user_name = username

    if os.path.exists(cred_file):
        try:
            with open(cred_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith("KULLANICI_ISIM="):
                        val = line.strip().split("=", 1)[1].strip()
                        if val:
                            user_name = val
                            break
        except Exception:
            pass

    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest"
        })

        r_home = session.get("https://bkst.tarbil.gov.tr/", verify=False, timeout=(4, 8))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=False, timeout=(5, 10))
        
        if "0" not in res_login.text:
            return jsonify({'success': False, 'error': 'Bakanlık kullanıcı adı veya şifreniz hatalı.'})

        try:
            jdata = res_login.json()
            if isinstance(jdata, dict):
                fetched_name = jdata.get("NameSurname") or jdata.get("UserName") or jdata.get("Name")
                if fetched_name:
                    user_name = fetched_name
        except Exception:
            pass

        try:
            r_stock = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=False, timeout=(4, 8))
            token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock.text)
            token2 = token2_match.group(1) if token2_match else token1

            r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=False, timeout=(4, 8))
            if r_gln.status_code == 200:
                gln_data = r_gln.json()
                if isinstance(gln_data, list) and len(gln_data) > 0:
                    if not address_id:
                        address_id = str(gln_data[0].get("Value") or "").strip()
                    raw_text = str(gln_data[0].get("Text") or "").strip()
                    if raw_text:
                        clean_name = clean_user_name(raw_text)
                        if clean_name and not clean_name.isdigit():
                            user_name = clean_name
        except Exception:
            pass

        user_name = clean_user_name(user_name)

        lines = [
            "# ==============================================================================",
            "# BAKANLIK BKST GİRİŞ BİLGİLERİ",
            "# ==============================================================================",
            f"KULLANICI_ADI={username}",
            f"SIFRE={password}",
            f"ADRES_ID={address_id}",
            f"KULLANICI_ISIM={user_name}",
            ""
        ]
        with open(cred_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        return jsonify({'success': True, 'message': 'Giriş başarılı ve kaydedildi.', 'user_name': user_name, 'token': LOCAL_SESSION_TOKEN})

    except Exception as e:
        logger.warning(f"api_system_login offline fallback: {e}")
        lines = [
            "# ==============================================================================",
            "# BAKANLIK BKST GİRİŞ BİLGİLERİ",
            "# ==============================================================================",
            f"KULLANICI_ADI={username}",
            f"SIFRE={password}",
            f"ADRES_ID={address_id}",
            f"KULLANICI_ISIM={user_name}",
            ""
        ]
        with open(cred_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        return jsonify({
            'success': True,
            'offline_mode': True,
            'message': 'İnternet bağlantısı yok veya Bakanlık sunucusu erişilemiyor. Yerel Çevrimdışı (Offline) Modda Giriş Yapıldı.',
            'user_name': clean_user_name(user_name),
            'token': LOCAL_SESSION_TOKEN
        })

@app.route('/api/system/user_info', methods=['GET'])
def api_system_user_info():
    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    username, password, address_id, api_key = read_bkst_credentials()
    if not username:
        return jsonify({'success': True, 'username': '', 'user_name': 'Giriş Yapılmadı', 'token': LOCAL_SESSION_TOKEN})

    user_name = username
    if os.path.exists(cred_file):
        try:
            with open(cred_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith("KULLANICI_ISIM="):
                        val = line.strip().split("=", 1)[1].strip()
                        if val:
                            user_name = val
                            break
        except Exception:
            pass

    display_name = clean_user_name(user_name or username)
    return jsonify({
        'success': True,
        'username': username,
        'user_name': display_name,
        'token': LOCAL_SESSION_TOKEN
    })

@app.route('/api/system/sync_status', methods=['GET'])
def api_system_sync_status():
    with _state_lock:
        return jsonify({"synced": _app_bkst_synced})

@app.route('/api/system/check_update', methods=['GET'])
def api_system_check_update():
    try:
        from guncelleme_kontrol import get_unified_version_info, get_latest_remote_commit_sha
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        cur_code, cur_date, cur_msg = get_unified_version_info()
        local_vpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
        local_commit = ""
        if os.path.exists(local_vpath):
            with open(local_vpath, "r", encoding="utf-8") as f:
                local_commit = str(json.load(f).get("commit", "")).strip()

        latest_sha = get_latest_remote_commit_sha(requests)
        if not latest_sha or latest_sha == "main":
            return jsonify({'has_update': False, 'current_version': cur_code})

        headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
        remote_vurl = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/{latest_sha}/version.json?t={time.time_ns()}"
        resp = requests.get(remote_vurl, verify=False, timeout=(4, 8), headers=headers)
        if resp.status_code == 200:
            rdata = resp.json()
            remote_commit = str(rdata.get("commit", "")).strip()
            remote_version = str(rdata.get("version", "v1.0")).strip()
            if remote_commit and remote_commit != local_commit:
                return jsonify({
                    'has_update': True,
                    'current_version': cur_code,
                    'remote_version': remote_version,
                    'remote_commit': remote_commit,
                    'message': rdata.get("message", "Yeni sistem güncellemesi mevcut.")
                })
    except Exception as e:
        logger.error(f"Check update error: {e}")

    return jsonify({'has_update': False})

@app.route('/api/system/apply_update', methods=['POST'])
def api_system_apply_update():
    try:
        from guncelleme_kontrol import force_update
        updated = force_update()
        if updated:
            return jsonify({'success': True, 'updated': True, 'message': 'Güncelleme başarıyla yüklendi!'})
        return jsonify({'success': True, 'updated': False, 'message': 'Sistem zaten güncel.'})
    except Exception as e:
        logger.error(f"Apply update error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'Güncelleme hatası: {str(e)}'})

@app.route('/api/system/logout', methods=['POST'])
def api_system_logout():
    with _state_lock:
        global _app_bkst_synced
        _app_bkst_synced = False

    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    lines = [
        "# ==============================================================================",
        "# BAKANLIK BKST GİRİŞ BİLGİLERİ",
        "# ==============================================================================",
        "KULLANICI_ADI=",
        "SIFRE=",
        "ADRES_ID=",
        ""
    ]
    with open(cred_file, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))

    return jsonify({'success': True, 'message': 'Oturum kapatıldı.'})

if __name__ == '__main__':
    from waitress import serve
    logger.info("Starting QR-Compare server with Waitress (threads=32, port=5000)...")
    serve(
        app,
        host='127.0.0.1',
        port=5000,
        threads=32,
        connection_limit=200,
        channel_timeout=180,
        cleanup_interval=30,
        ident='QR-Compare'
    )

```

---

### 📁 `static/style.css`

```css
/* CSS Design Tokens & Reset */
:root {
    --bg-dark: #0a0b10;
    --card-bg: rgba(20, 22, 33, 0.65);
    --card-border: rgba(255, 255, 255, 0.08);
    --primary: #5865f2;
    --primary-glow: rgba(88, 101, 242, 0.35);
    --success: #10b981;
    --success-glow: rgba(16, 185, 129, 0.25);
    --warning: #f59e0b;
    --danger: #ef4444;
    --text-main: #f3f4f6;
    --text-muted: #9ca3af;
    
    --font-outfit: 'Outfit', sans-serif;
    --font-inter: 'Inter', sans-serif;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, 0.15) transparent;
}

body {
    background-color: var(--bg-dark);
    color: var(--text-main);
    font-family: var(--font-inter);
    min-height: 100vh;
    overflow-x: hidden;
    position: relative;
    font-size: 0.92rem;
    line-height: 1.5;
}

/* Background Glowing Decorators */
.glass-bg-decor1 {
    position: absolute;
    width: 500px;
    height: 500px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(88, 101, 242, 0.14) 0%, rgba(0, 0, 0, 0) 70%);
    top: -100px;
    right: -50px;
    z-index: -1;
    pointer-events: none;
}

.glass-bg-decor2 {
    position: absolute;
    width: 650px;
    height: 650px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(16, 185, 129, 0.08) 0%, rgba(0, 0, 0, 0) 70%);
    bottom: -150px;
    left: -150px;
    z-index: -1;
    pointer-events: none;
}

/* Main Container Layout */
.container {
    width: 100%;
    max-width: 1440px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 1.8rem;
}

/* Header */
.app-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 1.2rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    flex-wrap: wrap;
    gap: 1rem;
}

.logo {
    display: flex;
    align-items: center;
    gap: 0.9rem;
}

.logo-icon {
    font-size: 2.5rem;
    background: linear-gradient(135deg, #818cf8, #3b82f6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.logo h1 {
    font-family: var(--font-outfit);
    font-size: 1.75rem;
    font-weight: 800;
    letter-spacing: -0.5px;
    background: linear-gradient(135deg, #ffffff 60%, #a5b4fc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.logo p {
    font-size: 0.85rem;
    color: var(--text-muted);
    margin-top: 2px;
}

/* Application Top Navigation Bar */
.app-nav {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    background: rgba(15, 23, 42, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.08);
    padding: 0.35rem;
    border-radius: 14px;
    backdrop-filter: blur(12px);
}

.nav-btn {
    color: #94a3b8;
    text-decoration: none;
    font-size: 0.85rem;
    font-weight: 600;
    padding: 0.55rem 1.1rem;
    border-radius: 10px;
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    border: 1px solid transparent;
    white-space: nowrap;
}

.nav-btn:hover {
    color: #f8fafc;
    background: rgba(255, 255, 255, 0.06);
    transform: translateY(-1px);
}

.nav-btn.active {
    color: #ffffff;
    background: linear-gradient(135deg, rgba(88, 101, 242, 0.9), rgba(59, 130, 246, 0.9));
    border-color: rgba(99, 102, 241, 0.4);
    box-shadow: 0 4px 14px rgba(88, 101, 242, 0.35);
}

.nav-btn.active-green {
    color: #ffffff;
    background: linear-gradient(135deg, #059669, #10b981);
    border-color: rgba(16, 185, 129, 0.4);
    box-shadow: 0 4px 14px rgba(16, 185, 129, 0.35);
}

.nav-btn.active-purple {
    color: #ffffff;
    background: linear-gradient(135deg, #7c3aed, #9333ea);
    border-color: rgba(147, 51, 234, 0.4);
    box-shadow: 0 4px 14px rgba(147, 51, 234, 0.35);
}

.header-right {
    display: flex;
    align-items: center;
    gap: 0.8rem;
}

.system-status {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.07);
    padding: 0.45rem 0.9rem;
    border-radius: 50px;
    font-size: 0.85rem;
    font-weight: 500;
}

.status-indicator {
    width: 9px;
    height: 9px;
    border-radius: 50%;
}

.status-indicator.online {
    background-color: var(--success);
    box-shadow: 0 0 10px var(--success-glow);
}

/* Quick Guide Bar (Sade Üst Rehber Barı) */
.quick-guide-bar {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 1rem;
    background: rgba(88, 101, 242, 0.09);
    border: 1px solid rgba(88, 101, 242, 0.25);
    border-radius: 16px;
    padding: 1rem 1.2rem;
}

.guide-item {
    display: flex;
    align-items: center;
    gap: 0.85rem;
    background: rgba(0, 0, 0, 0.25);
    padding: 0.75rem 1rem;
    border-radius: 12px;
}

.guide-num {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: rgba(88, 101, 242, 0.28);
    color: #a5b4fc;
    font-weight: 800;
    font-size: 0.9rem;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}

.guide-item strong {
    display: block;
    font-size: 0.88rem;
    color: #fff;
    font-weight: 700;
}

.guide-item span {
    font-size: 0.78rem;
    color: var(--text-muted);
}

/* Glass Cards & Panels */
.glass-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-radius: 18px;
    padding: 1.6rem;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.28);
    min-width: 0;
}

/* Grid Layout Fixes */
.dashboard-grid {
    display: grid;
    grid-template-columns: 1fr 1.25fr;
    gap: 1.8rem;
    min-width: 0;
}

.dashboard-grid > section {
    min-width: 0;
}

@media (max-width: 1024px) {
    .dashboard-grid {
        grid-template-columns: 1fr;
    }
}

.panel-head-flex {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
    margin-bottom: 1rem;
}

.panel-title-group {
    display: flex;
    align-items: center;
    gap: 0.85rem;
}

.panel-title-group h2, .panel h2 {
    font-family: var(--font-outfit);
    font-size: 1.35rem;
    font-weight: 700;
    color: var(--text-main);
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin: 0;
}

.panel-subtitle {
    font-size: 0.85rem;
    color: var(--text-muted);
    margin: 3px 0 0 0;
}

.icon-primary { color: var(--primary); font-size: 1.5rem; }
.icon-blue { color: #60a5fa; font-size: 1.5rem; }
.icon-purple { color: #a855f7; font-size: 1.5rem; }
.icon-green { color: var(--success); font-size: 1.5rem; }

/* Buttons */
.btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.6rem;
    font-family: var(--font-inter);
    font-size: 0.93rem;
    font-weight: 600;
    padding: 0.75rem 1.4rem;
    border-radius: 12px;
    border: none;
    cursor: pointer;
    transition: all 0.22s ease;
}

.btn-sm {
    padding: 0.55rem 1.1rem;
    font-size: 0.85rem;
}

.btn-primary {
    background: linear-gradient(135deg, #5865f2, #4752c4);
    color: white;
    box-shadow: 0 4px 14px var(--primary-glow);
}

.btn-primary:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 18px rgba(88, 101, 242, 0.45);
}

.btn-success {
    background: linear-gradient(135deg, #10b981, #059669);
    color: white;
    box-shadow: 0 4px 14px var(--success-glow);
}

.btn-success:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 6px 18px rgba(16, 185, 129, 0.4);
}

.btn-purple {
    background: linear-gradient(135deg, #a855f7, #7e22ce);
    color: white;
    box-shadow: 0 4px 14px rgba(168, 85, 247, 0.3);
    border: 1px solid rgba(168, 85, 247, 0.4);
}

.btn-purple:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 18px rgba(168, 85, 247, 0.45);
}

.btn-success:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.btn-outline {
    background: transparent;
    color: var(--text-main);
    border: 1px solid rgba(255, 255, 255, 0.18);
}

.btn-outline:hover {
    background: rgba(255, 255, 255, 0.06);
    border-color: rgba(255, 255, 255, 0.35);
}

.btn-block {
    width: 100%;
}

.margin-top-sm { margin-top: 1rem; }
.margin-top-md { margin-top: 1.4rem; }

/* BKST Controls */
.bkst-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.85rem;
    align-items: center;
    margin-top: 1rem;
}

/* Upload Area */
.upload-zone-wrapper {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.zone-label {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--text-main);
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.upload-area {
    border: 2px dashed rgba(255, 255, 255, 0.14);
    border-radius: 14px;
    padding: 1.4rem 1.2rem;
    text-align: center;
    transition: all 0.25s ease;
    cursor: pointer;
    background: rgba(255, 255, 255, 0.015);
}

.upload-area:hover {
    border-color: var(--primary);
    background: rgba(88, 101, 242, 0.04);
}

.upload-icon {
    font-size: 2rem;
    margin-bottom: 0.5rem;
}

.upload-area h3 {
    font-family: var(--font-outfit);
    font-size: 1rem;
    font-weight: 600;
    margin-bottom: 0.6rem;
}

/* File Info Box */
.file-info {
    display: flex;
    align-items: center;
    gap: 0.9rem;
    background: rgba(255, 255, 255, 0.035);
    border: 1px solid rgba(255, 255, 255, 0.09);
    padding: 0.75rem 1rem;
    border-radius: 12px;
}

.file-icon-selected { font-size: 1.7rem; }

.file-details {
    display: flex;
    flex-direction: column;
    flex-grow: 1;
    overflow: hidden;
}

.file-name {
    font-size: 0.88rem;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
    overflow: hidden;
}

.file-size { font-size: 0.75rem; color: var(--text-muted); }

.btn-clear {
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    padding: 0.3rem;
    font-size: 1.1rem;
}

.btn-clear:hover { color: var(--danger); }

/* Stats Grid (Section 2 metrics) */
.stats-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
    margin-bottom: 1.2rem;
}

@media (max-width: 540px) {
    .stats-grid {
        grid-template-columns: 1fr;
    }
}

.stat-card {
    background: rgba(255, 255, 255, 0.025);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 1rem 1.2rem;
    display: flex;
    align-items: center;
    gap: 1rem;
}

.stat-card.stat-success {
    background: rgba(16, 185, 129, 0.09);
    border-color: rgba(16, 185, 129, 0.28);
}

.stat-card.stat-amber {
    background: rgba(245, 158, 11, 0.09);
    border-color: rgba(245, 158, 11, 0.28);
}

.stat-icon {
    font-size: 1.6rem;
    flex-shrink: 0;
}

.stat-data {
    display: flex;
    flex-direction: column;
}

.stat-value {
    font-family: var(--font-outfit);
    font-size: 1.45rem;
    font-weight: 800;
    line-height: 1.1;
}

.stat-label {
    font-size: 0.78rem;
    color: var(--text-muted);
    margin-top: 3px;
}

.color-blue { color: #60a5fa; }
.color-purple { color: #c084fc; }
.color-green { color: #6ee7b7; }
.color-amber { color: #fcd34d; }

/* Action Download Buttons Group */
.action-buttons-group {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
}

/* Compare Results Table Box */
.compare-results-box {
    border-top: 1px solid var(--card-border);
    padding-top: 1.2rem;
    margin-top: 1.2rem;
    min-width: 0;
}

.compare-table-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.75rem;
    gap: 0.6rem;
}

.compare-table-header h3 {
    font-family: var(--font-outfit);
    font-size: 1.05rem;
    font-weight: 700;
    margin: 0;
}

/* Tables Layout & Horizontal Scroll Container */
.table-scroll-container {
    max-height: 420px;
    overflow-y: auto;
    overflow-x: auto;
    border: 1px solid var(--card-border);
    border-radius: 12px;
    background: rgba(10, 11, 16, 0.55);
    width: 100%;
}

.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
    text-align: left;
    white-space: nowrap;
}

.data-table th {
    background: rgba(88, 101, 242, 0.18);
    color: #a5b4fc;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 0.75rem 0.95rem;
    position: sticky;
    top: 0;
    z-index: 2;
}

.data-table td {
    padding: 0.7rem 0.95rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

/* Badges */
.badge {
    background: rgba(88, 101, 242, 0.12);
    color: #a5b4fc;
    padding: 0.35rem 0.8rem;
    border-radius: 50px;
    font-size: 0.8rem;
    font-weight: 600;
    border: 1px solid rgba(88, 101, 242, 0.25);
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
}

.badge.badge-open    { background: rgba(16,185,129,0.15); color: #10b981; border-color: rgba(16,185,129,0.3); }
.badge.badge-fetching{ background: rgba(245,158,11,0.15); color: #f59e0b; border-color: rgba(245,158,11,0.3); }
.badge.badge-done    { background: rgba(88,101,242,0.15); color: #818cf8; border-color: rgba(88,101,242,0.3); }
.badge.badge-error   { background: rgba(239,68,68,0.15);  color: #ef4444; border-color: rgba(239,68,68,0.3); }
.badge.badge-closed  { background: rgba(88,101,242,0.12); color: #818cf8; border-color: rgba(88,101,242,0.2); }

/* Status message */
.status-msg {
    margin-top: 0.85rem;
    font-size: 0.88rem;
    color: var(--text-main);
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.85rem 1.1rem;
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.08);
}

.status-msg.status-error  { border-color: rgba(239,68,68,0.35); color: #fca5a5; background: rgba(239,68,68,0.12); }
.status-msg.status-done   { border-color: rgba(16,185,129,0.35); color: #6ee7b7; background: rgba(16,185,129,0.12); }
.status-msg.status-fetch  { border-color: rgba(245,158,11,0.35); color: #fde68a; background: rgba(245,158,11,0.12); }

/* ── 3. Bölüm (Terek Sayımı) Styling ──────────────────────────────── */
.audit-panel {
    border: 1px solid rgba(16, 185, 129, 0.35) !important;
    background: rgba(20, 22, 33, 0.8) !important;
}

.scan-input-wrapper {
    position: relative;
    display: flex;
    gap: 0.85rem;
    margin-top: 1.2rem;
}

.scan-icon {
    position: absolute;
    left: 1.1rem;
    top: 50%;
    transform: translateY(-50%);
    color: var(--success);
    font-size: 1.3rem;
}

#audit-input-main {
    flex: 1;
    padding: 0.9rem 1.1rem 0.9rem 3rem;
    font-size: 1.1rem;
    font-weight: 600;
    border-radius: 12px;
    border: 2px solid var(--success);
    background: rgba(255, 255, 255, 0.06);
    color: var(--text-main);
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
}

#audit-input-main:focus {
    box-shadow: 0 0 14px rgba(16, 185, 129, 0.35);
}

.btn-scan {
    white-space: nowrap;
    padding: 0 1.6rem;
}

.audit-msg {
    padding: 0.85rem 1.1rem;
    border-radius: 12px;
    margin-top: 1rem;
    font-size: 0.92rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 0.7rem;
}

.sync-alert-box {
    background: rgba(239, 68, 68, 0.14);
    border: 1px solid rgba(239, 68, 68, 0.45);
    border-radius: 12px;
    padding: 1rem 1.25rem;
    margin-top: 1.2rem;
    display: flex;
    align-items: center;
    gap: 0.9rem;
}

.alert-icon { font-size: 1.6rem; color: #ef4444; flex-shrink: 0; }
.alert-text { font-size: 0.93rem; font-weight: 700; color: #fca5a5; }

.koli-badges-container {
    background: rgba(255, 255, 255, 0.025);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 0.85rem 1.1rem;
    margin-top: 1rem;
}

.koli-badges-label {
    font-size: 0.8rem;
    color: var(--text-muted);
    font-weight: 700;
    display: block;
    margin-bottom: 0.5rem;
}

.koli-badges-list {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
}

.audit-stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin-top: 1rem;
}

.audit-stat-card {
    background: rgba(255, 255, 255, 0.035);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 0.85rem 1.1rem;
}

.audit-stat-card.card-ok {
    background: rgba(16, 185, 129, 0.12);
    border-color: rgba(16, 185, 129, 0.35);
}

.audit-stat-card.card-missing {
    background: rgba(239, 68, 68, 0.12);
    border-color: rgba(239, 68, 68, 0.35);
}

.audit-stat-lbl {
    font-size: 0.75rem;
    color: var(--text-muted);
    text-transform: uppercase;
    display: block;
}

.audit-stat-val {
    font-family: var(--font-outfit);
    font-size: 1.35rem;
    font-weight: 800;
    margin-top: 3px;
    display: block;
}

.text-ok { color: var(--success); }
.text-missing { color: var(--danger); }

.audit-actions {
    display: flex;
    gap: 0.75rem;
    margin-top: 1rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
}

/* Utilities */
.hidden { display: none !important; }

/* Footer Bottom */
.app-footer-bottom {
    text-align: center;
    padding: 1.8rem 0;
    color: var(--text-muted);
    font-size: 0.82rem;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
}

/* ── SOL DİKİNE SIDEBAR TASARIMI (DESKTOP LAYOUT) ───────────────────────── */
body.sidebar-layout {
    margin: 0;
    padding: 0;
    overflow-x: hidden;
    background-color: var(--bg-dark);
}

.app-wrapper {
    display: flex;
    min-height: 100vh;
    width: 100%;
}

.sidebar {
    width: 270px;
    background: linear-gradient(180deg, #0f172a 0%, #090d16 100%);
    border-right: 1px solid rgba(255, 255, 255, 0.08);
    padding: 1.6rem 1.2rem;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    flex-shrink: 0;
    backdrop-filter: blur(20px);
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    height: 100vh;
    z-index: 100;
    overflow-y: auto;
    box-shadow: 4px 0 25px rgba(0, 0, 0, 0.4);
}

.sidebar-brand {
    display: flex;
    align-items: center;
    gap: 0.9rem;
    padding-bottom: 1.4rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    margin-bottom: 1.5rem;
}

.sidebar-brand .logo-icon {
    font-size: 2.2rem;
    background: linear-gradient(135deg, #38bdf8, #818cf8);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    filter: drop-shadow(0 0 10px rgba(56, 189, 248, 0.4));
    animation: pulseLogo 3s ease-in-out infinite;
}

@keyframes pulseLogo {
    0%, 100% { transform: scale(1); filter: drop-shadow(0 0 8px rgba(56, 189, 248, 0.4)); }
    50% { transform: scale(1.06); filter: drop-shadow(0 0 16px rgba(56, 189, 248, 0.8)); }
}

.sidebar-brand h2 {
    font-family: var(--font-outfit);
    font-size: 1.35rem;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: #ffffff;
    margin: 0;
    line-height: 1.2;
}

.sidebar-brand p {
    font-size: 0.78rem;
    color: #38bdf8;
    margin: 2px 0 0 0;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}

.sidebar-menu {
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
    flex: 1;
}

.sidebar-link {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.85rem 1.1rem;
    color: #94a3b8;
    text-decoration: none;
    font-size: 0.92rem;
    font-weight: 600;
    border-radius: 12px;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    border: 1px solid transparent;
    position: relative;
    overflow: hidden;
}

.sidebar-link i {
    font-size: 1.15rem;
    width: 24px;
    text-align: center;
    transition: transform 0.25s ease, color 0.25s ease;
    color: #64748b;
}

.sidebar-link:hover {
    color: #f8fafc;
    background: rgba(255, 255, 255, 0.05);
    border-color: rgba(255, 255, 255, 0.1);
    transform: translateX(4px);
}

.sidebar-link:hover i {
    color: #38bdf8;
    transform: scale(1.15);
}

.sidebar-link.active {
    color: #ffffff;
    background: linear-gradient(135deg, rgba(56, 189, 248, 0.18), rgba(99, 102, 241, 0.22));
    border-color: rgba(56, 189, 248, 0.4);
    box-shadow: 0 4px 18px rgba(56, 189, 248, 0.2);
}

.sidebar-link.active i {
    color: #38bdf8;
    filter: drop-shadow(0 0 8px rgba(56, 189, 248, 0.6));
}

.sidebar-link.active::before {
    content: '';
    position: absolute;
    left: 0;
    top: 15%;
    height: 70%;
    width: 4px;
    background: linear-gradient(180deg, #38bdf8, #818cf8);
    border-radius: 0 4px 4px 0;
    box-shadow: 0 0 10px #38bdf8;
}

.sidebar-footer {
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
    padding-top: 1.2rem;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    margin-top: 1rem;
}

.system-status-pill {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.25);
    padding: 0.5rem 0.9rem;
    border-radius: 10px;
    font-size: 0.82rem;
    font-weight: 600;
    color: #34d399;
}

.version-pill {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    padding: 0.5rem 0.9rem;
    border-radius: 10px;
    font-size: 0.82rem;
    font-weight: 600;
    color: #94a3b8;
    cursor: pointer;
    transition: all 0.2s ease;
}

.version-pill:hover {
    background: rgba(56, 189, 248, 0.12);
    border-color: rgba(56, 189, 248, 0.3);
    color: #38bdf8;
}

.user-profile-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: rgba(56, 189, 248, 0.08);
    border: 1px solid rgba(56, 189, 248, 0.2);
    padding: 0.5rem 0.8rem;
    border-radius: 10px;
    font-size: 0.82rem;
    color: #e2e8f0;
}

.user-profile-info {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    overflow: hidden;
    flex: 1;
    min-width: 0;
}

.user-avatar-icon {
    color: #38bdf8;
    font-size: 1.1rem;
    flex-shrink: 0;
}

.user-name-title {
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 150px;
    display: block;
    cursor: default;
}

.btn-logout-icon {
    background: rgba(239, 68, 68, 0.15);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: #f87171;
    width: 28px;
    height: 28px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.2s ease;
    flex-shrink: 0;
}

.btn-logout-icon:hover {
    background: rgba(239, 68, 68, 0.35);
    color: #ffffff;
    transform: scale(1.05);
}

.main-content {
    flex: 1;
    margin-left: 270px;
    min-width: 0;
    padding: 2rem 2.2rem;
    display: flex;
    flex-direction: column;
    gap: 1.8rem;
    min-height: 100vh;
}

/* Premium Tech Stat Cards for Depo Stokları */
.depo-stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 1.4rem;
    margin-bottom: 0.5rem;
}

.stat-card-tech {
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    padding: 1.25rem 1.4rem;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 1rem;
    cursor: pointer;
    backdrop-filter: blur(14px);
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    overflow: hidden;
}

.stat-card-tech:hover {
    transform: translateY(-3px);
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.35);
}

.card-blue-glow:hover {
    border-color: rgba(56, 189, 248, 0.4);
    box-shadow: 0 8px 24px rgba(56, 189, 248, 0.2);
}

.card-purple-glow:hover {
    border-color: rgba(167, 139, 250, 0.4);
    box-shadow: 0 8px 24px rgba(167, 139, 250, 0.2);
}

.card-amber-glow {
    border-color: rgba(245, 158, 11, 0.25);
    background: rgba(245, 158, 11, 0.04);
}

.card-amber-glow:hover {
    border-color: rgba(245, 158, 11, 0.5);
    box-shadow: 0 8px 24px rgba(245, 158, 11, 0.25);
}

.stat-tech-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.stat-tech-icon {
    width: 44px;
    height: 44px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.3rem;
}

.icon-blue-bg {
    background: rgba(56, 189, 248, 0.15);
    color: #38bdf8;
    border: 1px solid rgba(56, 189, 248, 0.3);
}

.icon-purple-bg {
    background: rgba(167, 139, 250, 0.15);
    color: #a78bfa;
    border: 1px solid rgba(167, 139, 250, 0.3);
}

.icon-amber-bg {
    background: rgba(245, 158, 11, 0.15);
    color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.3);
}

.stat-tech-badge {
    font-size: 0.75rem;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 8px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}

.badge-blue { background: rgba(56, 189, 248, 0.12); color: #38bdf8; }
.badge-purple { background: rgba(167, 139, 250, 0.12); color: #a78bfa; }
.badge-amber { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }

.stat-tech-body {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
}

.stat-tech-val {
    font-family: var(--font-outfit);
    font-size: 2.2rem;
    font-weight: 800;
    line-height: 1;
    margin: 0;
}

.val-blue { color: #38bdf8; }
.val-purple { color: #c084fc; }
.val-amber { color: #f59e0b; }

.stat-tech-lbl {
    font-size: 0.85rem;
    color: var(--text-muted);
    font-weight: 500;
    margin: 0;
}

.stat-tech-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-top: 0.75rem;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
    font-size: 0.78rem;
    color: var(--text-muted);
    font-weight: 600;
    transition: color 0.2s;
}

.stat-card-tech:hover .stat-tech-footer {
    color: #fff;
}

.stat-card-tech:hover .arrow-icon {
    transform: translateX(3px);
    color: var(--primary);
}

.content-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.page-title {
    font-family: var(--font-outfit);
    font-size: 1.6rem;
    font-weight: 800;
    color: #fff;
    margin: 0;
    display: flex;
    align-items: center;
    gap: 0.7rem;
}

.page-subtitle {
    font-size: 0.88rem;
    color: var(--text-muted);
    margin-top: 3px;
}

@media (max-width: 900px) {
    .app-wrapper {
        flex-direction: column;
    }
    .sidebar {
        width: 100%;
        height: auto;
        position: relative;
    }
    .main-content {
        margin-left: 0;
    }
}

```

---

### 📁 `static/app.js`

```javascript
// ── QR COMPARE APP.JS ──────────────────────────────────────────────────────
console.log("QR Compare app.js loading...");

// Global Session Authenticated Fetch Wrapper
window.apiFetch = async function(url, options = {}) {
    options.headers = options.headers || {};
    const token = localStorage.getItem('local_session_token');
    if (token) {
        options.headers['X-Local-Token'] = token;
    }
    try {
        const response = await fetch(url, options);
        if (response.status === 401) {
            window.location.href = '/login';
            return response;
        }
        return response;
    } catch (err) {
        console.error("apiFetch error:", err);
        throw err;
    }
};

(function syncSessionToken() {
    fetch('/api/system/user_info')
        .then(res => res.json())
        .then(data => {
            if (data && data.token) {
                localStorage.setItem('local_session_token', data.token);
            }
        })
        .catch(() => {});
})();

(function initAppWindowControl() {
    if (window.outerWidth < screen.availWidth || window.outerHeight < screen.availHeight) {
        try {
            window.moveTo(0, 0);
            window.resizeTo(screen.availWidth, screen.availHeight);
        } catch (e) {}
    }
})();

(function startHeartbeat() {
    let hbTimer = null;
    function sendPing() {
        window.apiFetch('/api/system/heartbeat', { method: 'POST' }).catch(() => {});
    }
    function startTimer() {
        if (hbTimer) return;
        hbTimer = setInterval(sendPing, 10000);
    }
    function stopTimer() {
        if (hbTimer) { clearInterval(hbTimer); hbTimer = null; }
    }
    sendPing();
    startTimer();
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) stopTimer();
        else { sendPing(); startTimer(); }
    });
})();

// ── Startup Auto Update Engine ──────────────────────────────────────────────
(function checkStartupAutoUpdate() {
    if (sessionStorage.getItem('app_update_checked')) return;
    sessionStorage.setItem('app_update_checked', '1');

    fetch('/api/system/check_update')
        .then(res => res.json())
        .then(data => {
            if (data && data.has_update) {
                showUpdateLoadingScreen(data);
            }
        })
        .catch(err => console.warn("Startup update check error:", err));
})();

function showUpdateLoadingScreen(updateInfo) {
    let overlay = document.getElementById('startupUpdateOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'startupUpdateOverlay';
        overlay.style.cssText = `
            position: fixed;
            top: 0; left: 0; width: 100vw; height: 100vh;
            background: #0f172a;
            color: #ffffff;
            z-index: 999999;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            font-family: 'Outfit', 'Inter', sans-serif;
            text-align: center;
            padding: 20px;
        `;
        overlay.innerHTML = `
            <div style="background: rgba(30, 41, 59, 0.95); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 24px; padding: 40px 30px; max-width: 480px; width: 90%; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.7); backdrop-filter: blur(10px);">
                <div style="width: 80px; height: 80px; margin: 0 auto 24px; background: rgba(56, 189, 248, 0.1); border-radius: 50%; display: flex; align-items: center; justify-content: center;">
                    <i class="fa-solid fa-cloud-arrow-down fa-bounce" style="font-size: 38px; color: #38bdf8;"></i>
                </div>
                <h2 style="font-size: 1.6rem; font-weight: 700; margin-bottom: 12px; color: #f8fafc;">Uygulama Güncelleme Alıyor</h2>
                <p id="updateStatusMsg" style="font-size: 0.95rem; color: #94a3b8; line-height: 1.6; margin-bottom: 24px;">
                    Yeni sürüm (${updateInfo.remote_version || 'Gelişmiş Sürüm'}) algılandı. Güncelleme paketleri indiriliyor, lütfen bekleyin...
                </p>
                <div style="width: 100%; height: 8px; background: #334155; border-radius: 999px; overflow: hidden; position: relative;">
                    <div id="updateProgressBar" style="width: 40%; height: 100%; background: linear-gradient(90deg, #38bdf8, #3b82f6); border-radius: 999px; transition: width 0.4s ease; animation: updateProgressAnim 1.8s infinite linear;"></div>
                </div>
                <p style="font-size: 0.8rem; color: #64748b; margin-top: 18px; font-weight: 500;">
                    <i class="fa-solid fa-circle-info" style="color: #38bdf8; margin-right: 4px;"></i> İşlem tamamlanınca uygulama yeni sürümüyle yeniden başlayacaktır.
                </p>
            </div>
            <style>
                @keyframes updateProgressAnim {
                    0% { transform: translateX(-100%); width: 30%; }
                    50% { width: 60%; }
                    100% { transform: translateX(350%); width: 30%; }
                }
            </style>
        `;
        document.body.appendChild(overlay);
    }

    fetch('/api/system/apply_update', { method: 'POST' })
        .then(res => res.json())
        .then(resData => {
            const msgEl = document.getElementById('updateStatusMsg');
            const barEl = document.getElementById('updateProgressBar');
            if (barEl) {
                barEl.style.animation = 'none';
                barEl.style.width = '100%';
            }
            if (msgEl) {
                msgEl.style.color = '#4ade80';
                msgEl.innerHTML = '<strong>✅ Güncelleme başarıyla tamamlandı!</strong><br>Uygulama güncel haliyle otomatik yeniden başlatılıyor...';
            }
            setTimeout(() => {
                window.location.reload(true);
            }, 1800);
        })
        .catch(err => {
            const msgEl = document.getElementById('updateStatusMsg');
            if (msgEl) {
                msgEl.style.color = '#f87171';
                msgEl.textContent = "Güncelleme sırasında bir aksaklık oluştu, uygulama normal modda başlatılıyor...";
            }
            setTimeout(() => {
                if (overlay) overlay.remove();
            }, 2500);
        });
}

// Global state
window.shelfKoliMap = window.shelfKoliMap || new Map();
window.shelfItems = window.shelfItems || [];
window.scannedQRsInShelf = window.scannedQRsInShelf || new Set();
window.bkstPollTimer = window.bkstPollTimer || null;
window.isAuditAllMode = window.isAuditAllMode || false;
window.allWarehouseItems = window.allWarehouseItems || [];

const BADGE_MAP = {
    closed:     { text: 'Kapalı',                                      cls: 'badge-closed' },
    opening:    { text: 'Tarayıcı Açılıyor…',                          cls: 'badge-fetching'},
    login_page: { text: 'Giriş Bekleniyor (Stok Takibi Sayfasına Gidin)', cls: 'badge-closed' },
    open:       { text: 'Açık (Stok Takibi Sayfasına Gidin)',          cls: 'badge-open'   },
    ready:      { text: '🟢 Hazır (Stok Takibi Sayfasında)',            cls: 'badge-done'   },
    fetching:   { text: 'Veriler Çekiliyor…',                           cls: 'badge-fetching'},
    done:       { text: 'Tamamlandı',                                  cls: 'badge-done'   },
    error:      { text: 'Hata',                                        cls: 'badge-error'  }
};

function escapeHtml(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function isItemScanned(item) {
    if (!item) return false;
    if (item.qr && window.scannedQRsInShelf.has(item.qr)) return true;

    const itemQrClean = (item.qr || '').replace(/[^A-Z0-9]/gi, '').toUpperCase();
    const itemSeriClean = (item.seri_no || '').replace(/[^A-Z0-9]/gi, '').toUpperCase();

    for (let scanned of window.scannedQRsInShelf) {
        const sClean = (scanned || '').replace(/[^A-Z0-9]/gi, '').toUpperCase();
        if (!sClean) continue;

        if (itemQrClean && (sClean === itemQrClean || sClean.includes(itemQrClean) || itemQrClean.includes(sClean))) {
            return true;
        }

        if (itemSeriClean && itemSeriClean.length >= 3 && sClean.includes(itemSeriClean)) {
            return true;
        }
    }

    return false;
}

function setBkstUI(status, message) {
    const bkstBadge  = document.getElementById('bkst-status-badge');
    const bkstMsgBox = document.getElementById('bkst-message-box');
    const btnBkstFetch = document.getElementById('btn-bkst-fetch');
    const btnBkstClose = document.getElementById('btn-bkst-close');

    if (!bkstBadge || !bkstMsgBox) return;
    const info = BADGE_MAP[status] || BADGE_MAP.closed;

    bkstBadge.textContent = info.text;
    bkstBadge.className   = 'badge ' + info.cls;

    bkstMsgBox.classList.remove('hidden', 'status-error', 'status-done', 'status-fetch');
    if (!message) { bkstMsgBox.classList.add('hidden'); return; }

    let icon = 'fa-circle-info';
    let extraClass = '';
    if (status === 'fetching' || status === 'opening') { icon = 'fa-circle-notch fa-spin'; extraClass = 'status-fetch'; }
    if (status === 'done' || status === 'ready')      { icon = 'fa-circle-check';         extraClass = 'status-done';  }
    if (status === 'error' || status === 'login_page') { icon = 'fa-circle-exclamation';   extraClass = 'status-error'; }

    bkstMsgBox.className = 'status-msg' + (extraClass ? ' ' + extraClass : '');
    bkstMsgBox.innerHTML = `<i class="fa-solid ${icon}"></i><span>${escapeHtml(message)}</span>`;

    const existingDl = bkstMsgBox.parentElement ? bkstMsgBox.parentElement.querySelector('.bkst-download-btn') : null;
    if (existingDl) existingDl.remove();
    if (status === 'done' && bkstMsgBox.parentElement) {
        const dl = document.createElement('a');
        dl.href = '/api/bkst/download';
        dl.className = 'bkst-download-btn';
        dl.innerHTML = '<i class="fa-solid fa-file-excel"></i> Excel Dosyasını İndir';
        bkstMsgBox.parentElement.appendChild(dl);
    }

    if (btnBkstFetch) btnBkstFetch.disabled = !(status === 'ready' || status === 'done');
    if (btnBkstClose) btnBkstClose.classList.toggle('hidden', status === 'closed');
}

function startBkstPolling() {
    if (window.bkstPollTimer) return;
    window.bkstPollTimer = setInterval(async () => {
        try {
            const res  = await fetch('/api/bkst/status');
            const data = await res.json();
            setBkstUI(data.status, data.message);
            if (data.status === 'done' || data.status === 'error' || data.status === 'closed') {
                clearInterval(window.bkstPollTimer);
                window.bkstPollTimer = null;
            }
        } catch (_) {}
    }, 2000);
}

// Global button click triggers
window.triggerBkstOpen = function() {
    console.log("triggerBkstOpen called");
    setBkstUI('opening', 'Bakanlık tarayıcısı açılıyor...');

    fetch('/api/bkst/open', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                setBkstUI(data.status, data.message);
                startBkstPolling();
            } else {
                setBkstUI('error', data.message || 'Başlatma hatası oluştu.');
            }
        })
        .catch(err => {
            setBkstUI('error', 'Sunucu ile iletişim kurulamadı: ' + err.message);
        });
};

window.triggerBkstFetchApi = function() {
    console.log("triggerBkstFetchApi called");
    setBkstUI('fetching', '⚡ Bakanlık verileri API üzerinden çekiliyor...');

    const sidebar = document.querySelector('.sidebar');
    if (sidebar) sidebar.style.pointerEvents = 'none';

    window.apiFetch('/api/bkst/fetch_api', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                let statusTimer = setInterval(async () => {
                    try {
                        const res = await window.apiFetch('/api/bkst/fetch_status');
                        const statusData = await res.json();
                        setBkstUI(statusData.status, statusData.message);
                        if (!statusData.running) {
                            clearInterval(statusTimer);
                            if (sidebar) sidebar.style.pointerEvents = 'auto';
                            const dlBtn = document.getElementById('btn-bkst-download-excel');
                            if (dlBtn && statusData.status === 'done') dlBtn.classList.remove('hidden');
                        }
                    } catch (e) {
                        clearInterval(statusTimer);
                        if (sidebar) sidebar.style.pointerEvents = 'auto';
                    }
                }, 10000);
            } else {
                setBkstUI('error', data.error || 'API veri çekme hatası oluştu.');
                if (sidebar) sidebar.style.pointerEvents = 'auto';
            }
        })
        .catch(err => {
            setBkstUI('error', 'Sunucu hatası: ' + err.message);
            if (sidebar) sidebar.style.pointerEvents = 'auto';
        });
};


function showAuditMsg(msg, isError = false) {
    const auditMsgBox = document.getElementById('audit-msg-box');
    if (!auditMsgBox) return;
    auditMsgBox.classList.remove('hidden');
    if (isError) {
        auditMsgBox.style.background = 'rgba(239, 68, 68, 0.15)';
        auditMsgBox.style.border = '1px solid rgba(239, 68, 68, 0.4)';
        auditMsgBox.style.color = '#f87171';
        auditMsgBox.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> <span>${escapeHtml(msg)}</span>`;
    } else {
        auditMsgBox.style.background = 'rgba(16, 185, 129, 0.15)';
        auditMsgBox.style.border = '1px solid rgba(16, 185, 129, 0.4)';
        auditMsgBox.style.color = '#6ee7b7';
        auditMsgBox.innerHTML = `<i class="fa-solid fa-circle-check"></i> <span>${escapeHtml(msg)}</span>`;
    }
}

function hideAuditMsg() {
    const auditMsgBox = document.getElementById('audit-msg-box');
    if (auditMsgBox) auditMsgBox.classList.add('hidden');
}

function rebuildShelfItems() {
    window.shelfItems = [];
    const seenQrs = new Set();
    window.shelfKoliMap.forEach((items) => {
        items.forEach(item => {
            const q = (item.qr || '').trim();
            if (q) {
                if (!seenQrs.has(q)) {
                    seenQrs.add(q);
                    window.shelfItems.push(item);
                }
            } else {
                window.shelfItems.push(item);
            }
        });
    });
}

window.toggleAuditMode = async function() {
    console.log("toggleAuditMode called. Current mode:", window.isAuditAllMode);
    if (window.shelfItems.length === 0) {
        showAuditMsg('⚠️ Henüz hiç ürün okutmadınız! Lütfen önce karşılaştırmak istediğiniz en az 1 ürün veya koli okutun.', true);
        return;
    }

    window.isAuditAllMode = !window.isAuditAllMode;

    if (window.isAuditAllMode) {
        const gtinsSet = new Set();
        const pNamesSet = new Set();

        window.shelfItems.forEach(i => {
            let g = (i.gtin || '').trim();
            if (!g || g === '—') {
                const match = (i.qr || '').match(/01(\d{14})/);
                if (match) g = match[1];
            }
            if (g && g !== '—') gtinsSet.add(g);
            if (i.product_name && i.product_name !== '—') pNamesSet.add(i.product_name);
        });

        const gtins = Array.from(gtinsSet);
        const pNames = Array.from(pNamesSet);

        showAuditMsg('📊 Okutulan ürün kalemleri Bakanlık depodaki tüm stokla karşılaştırılıyor...', false);
        
        try {
            const res = await fetch('/api/audit_all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ gtins: gtins, product_names: pNames })
            });
            const data = await res.json();
            if (data.success) {
                window.allWarehouseItems = data.items || [];
                renderAuditTable();
                const missingCnt = window.allWarehouseItems.filter(i => !isItemScanned(i)).length;
                showAuditMsg(`📊 Okutulan Kalem Karşılaştırma Modu AÇILDI! Bakanlık depodaki toplam ${window.allWarehouseItems.length} kutunun ${missingCnt} adeti tereğinizde EKSİK (Kırmızı renkte listenin en üstünde sıralandı).`, false);
            } else {
                window.isAuditAllMode = false;
                renderAuditTable();
                showAuditMsg('Hata: ' + data.error, true);
                return;
            }
        } catch (err) {
            window.isAuditAllMode = false;
            renderAuditTable();
            showAuditMsg('Sunucu hatası: ' + err.message, true);
            return;
        }
    } else {
        renderAuditTable();
        showAuditMsg('📦 Okutulan Koliler Sayım Moduna Dönüldü.', false);
    }

    const wrapper = document.getElementById('audit-results-wrapper');
    if (wrapper) wrapper.classList.remove('hidden');
};

function renderAuditTable() {
    const tbody = document.getElementById('audit-table-body');
    const elTotal = document.getElementById('audit-cnt-total');
    const elOk = document.getElementById('audit-cnt-ok');
    const elMissing = document.getElementById('audit-cnt-missing');
    const elKolisList = document.getElementById('audit-kolis-list');
    const alertBox = document.getElementById('audit-sync-alert-box');
    const alertText = document.getElementById('audit-sync-alert-text');
    const toggleBtn = document.getElementById('btn-audit-toggle-mode');

    let activeList = window.isAuditAllMode ? [...window.allWarehouseItems] : [...window.shelfItems];

    if (window.isAuditAllMode) {
        // Sort missing items (Tereğimde YOK) first so missing QRs appear at top!
        activeList.sort((a, b) => {
            const aOk = isItemScanned(a) ? 1 : 0;
            const bOk = isItemScanned(b) ? 1 : 0;
            return aOk - bOk;
        });
    }

    if (toggleBtn) {
        if (window.isAuditAllMode) {
            toggleBtn.innerHTML = '<i class="fa-solid fa-boxes-packing"></i> 📦 Okutulan Koliler Moduna Dön (Koli Modu)';
            toggleBtn.style.background = 'linear-gradient(135deg, #a855f7, #7e22ce)';
            toggleBtn.style.borderColor = '#a855f7';
        } else {
            toggleBtn.innerHTML = '<i class="fa-solid fa-layer-group"></i> 📊 Okutulan Kalemleri Tüm Depoyla Karşılaştır';
            toggleBtn.style.background = 'transparent';
            toggleBtn.style.borderColor = 'rgba(255,255,255,0.2)';
        }
    }

    if (elKolisList) {
        if (window.isAuditAllMode) {
            elKolisList.innerHTML = `<span class="badge" style="background: rgba(168,85,247,0.2); color: #e9d5ff; border: 1px solid rgba(168,85,247,0.4); padding: 5px 12px; font-size: 0.85rem; font-weight:700;"><i class="fa-solid fa-layer-group"></i> OKUTULAN KALEMLERİN DEPO EŞLEŞTİRMESİ AKTİF (${activeList.length} Toplam Kutu)</span>`;
        } else if (window.shelfKoliMap.size === 0) {
            elKolisList.innerHTML = '<span class="badge" style="background: rgba(88,101,242,0.2); color: #a5b4fc; border: 1px solid rgba(88,101,242,0.4); padding: 5px 12px; font-size: 0.85rem;">Henüz koli yüklenmedi</span>';
        } else {
            let badges = [];
            window.shelfKoliMap.forEach((items, kNo) => {
                const kOkCount = items.filter(i => isItemScanned(i)).length;
                const isFullOk = kOkCount === items.length && items.length > 0;
                const bg = isFullOk ? 'rgba(16,185,129,0.2)' : 'rgba(88,101,242,0.2)';
                const border = isFullOk ? 'rgba(16,185,129,0.4)' : 'rgba(88,101,242,0.4)';
                const color = isFullOk ? '#6ee7b7' : '#a5b4fc';
                badges.push(`<span class="badge" style="background:${bg}; border:1px solid ${border}; color:${color}; padding:5px 10px; font-size:0.82rem; font-weight:600;"><i class="fa-solid fa-box"></i> Koli ${escapeHtml(kNo)} (${kOkCount}/${items.length})</span>`);
            });
            elKolisList.innerHTML = badges.join(' ');
        }
    }

    const totalCount = activeList.length;
    const okCount = activeList.filter(i => isItemScanned(i)).length;
    const missingCount = totalCount - okCount;

    if (elTotal) elTotal.textContent = totalCount;
    if (elOk) elOk.textContent = okCount;
    if (elMissing) elMissing.textContent = missingCount;

    if (alertBox && alertText) {
        if (activeList.length === 0) {
            alertBox.classList.add('hidden');
        } else if (missingCount > 0) {
            alertBox.classList.remove('hidden');
            alertBox.style.background = 'rgba(239, 68, 68, 0.12)';
            alertBox.style.borderColor = 'rgba(239, 68, 68, 0.35)';
            alertText.style.color = '#f87171';
            alertText.innerHTML = `⚠️ DİKKAT! Toplam <strong>${missingCount} adet ürün/ilaç tereğinizde eksik</strong>. Aşağıdaki listeden Kırmızı renkli olan bu ürünler Bakanlık sisteminden çıkılmalıdır!`;
        } else {
            alertBox.classList.remove('hidden');
            alertBox.style.background = 'rgba(16, 185, 129, 0.12)';
            alertBox.style.borderColor = 'rgba(16, 185, 129, 0.35)';
            alertText.style.color = '#6ee7b7';
            alertText.innerHTML = `🟢 TEBRİKLER! Listedeki tüm koli ve ürünler (${okCount}/${activeList.length}) tereğinizde doğrulandı! Eksik ürün yok.`;
        }
    }

    if (!tbody) return;

    if (activeList.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="padding:2rem; text-align:center; color:var(--text-muted);">Tereğe eklenmiş koli veya ürün bulunamadı. Lütfen QR okutun.</td></tr>';
        return;
    }

    tbody.innerHTML = activeList.map((item, idx) => {
        const isOk = isItemScanned(item);
        const statusHtml = isOk
            ? `<span style="background:rgba(16,185,129,0.18); color:#6ee7b7; border:1px solid rgba(16,185,129,0.4); padding:4px 10px; border-radius:6px; font-weight:700; font-size:0.78rem;"><i class="fa-solid fa-check"></i> Tereğimde VAR</span>`
            : `<span style="background:rgba(239,68,68,0.18); color:#f87171; border:1px solid rgba(239,68,68,0.4); padding:4px 10px; border-radius:6px; font-weight:700; font-size:0.78rem;"><i class="fa-solid fa-xmark"></i> Tereğimde YOK</span>`;

        const noteHtml = isOk
            ? `<span style="color:var(--text-muted); font-size:0.8rem;">Fiziksel depoda doğrulandı</span>`
            : `<span style="color:#f87171; font-weight:600; font-size:0.8rem;"><i class="fa-solid fa-triangle-exclamation"></i> Tereğümde yok, bakanlıktan çık!</span>`;

        return `
            <tr style="border-bottom: 1px solid rgba(255,255,255,0.04); background: ${isOk ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.04)'};">
                <td style="padding: 0.65rem 0.85rem; font-weight:700; color:var(--primary);">${idx + 1}</td>
                <td style="padding: 0.65rem 0.85rem; font-weight:600; color:var(--text-main); white-space:nowrap;">${escapeHtml(item.product_name || '—')}</td>
                <td style="padding: 0.65rem 0.85rem; font-weight:600; color:#a5b4fc; white-space:nowrap;">${escapeHtml(item.koli_no || '—')}</td>
                <td style="padding: 0.65rem 0.85rem; font-family:monospace; font-size:0.78rem; color:var(--text-muted); white-space:nowrap;">${escapeHtml(item.qr || '—')}</td>
                <td style="padding: 0.65rem 0.85rem; white-space:nowrap;">${escapeHtml(item.seri_no || '—')}</td>
                <td style="padding: 0.65rem 0.85rem; white-space:nowrap;">${escapeHtml(item.parti_no || '—')}</td>
                <td style="padding: 0.65rem 0.85rem; white-space:nowrap;">${escapeHtml(item.palet_no || '—')}</td>
                <td style="padding: 0.65rem 0.85rem; text-align:center; white-space:nowrap;">${statusHtml}</td>
                <td style="padding: 0.65rem 0.85rem; white-space:nowrap;">${noteHtml}</td>
            </tr>
        `;
    }).join('');
}

async function handleScanSubmit(rawCode) {
    if (!rawCode) return;
    console.log("handleScanSubmit triggered with code:", rawCode);

    const wrapper = document.getElementById('audit-results-wrapper');
    if (wrapper) wrapper.classList.remove('hidden');

    showAuditMsg(`🔍 Barkod / QR sorgulanıyor: ${rawCode}...`, false);
    const normCode = rawCode.replace(/\s+/g, '').toUpperCase();

    if (window.shelfItems.length > 0) {
        const matchedItem = window.shelfItems.find(i => {
            const iQr = (i.qr || '').replace(/\s+/g, '').toUpperCase();
            const iSeri = (i.seri_no || '').replace(/\s+/g, '').toUpperCase();
            return iQr === normCode || normCode.includes(iQr) || iQr.includes(normCode) || (iSeri && iSeri === normCode);
        });

        if (matchedItem) {
            if (window.scannedQRsInShelf.has(matchedItem.qr)) {
                showAuditMsg(`⚠️ Bu ilacın karekodu (${matchedItem.qr}) zaten tereğinizde okutulmuştu!`, true);
            } else {
                window.scannedQRsInShelf.add(matchedItem.qr);
                renderAuditTable();
                showAuditMsg(`🟢 Ürün tereğinizde doğrulandı (Tereğimde VAR): ${matchedItem.product_name} (Koli: ${matchedItem.koli_no})`, false);
            }
            const mainInput = document.getElementById('audit-input-main');
            if (mainInput) { mainInput.value = ''; mainInput.focus(); }
            return;
        }
    }

    try {
        const res = await fetch('/api/audit_box?code=' + encodeURIComponent(rawCode));
        const data = await res.json();

        if (data.success) {
            const newKoliNo = data.koli_no;
            const newItems = data.items || [];
            const isKoliScan = data.is_koli_scan;
            const scannedQr = data.scanned_qr;

            if (!window.shelfKoliMap.has(newKoliNo)) {
                window.shelfKoliMap.set(newKoliNo, newItems);
                rebuildShelfItems();
            }

            if (isKoliScan) {
                newItems.forEach(item => {
                    if (item.qr) window.scannedQRsInShelf.add(item.qr);
                });
                renderAuditTable();
                showAuditMsg(`📦 Koli (${newKoliNo}) barkodu okutuldu! Kolideki ${newItems.length} adet ürünün TAMAMI tereğümde VAR olarak işaretlendi.`, false);
            } else {
                if (scannedQr) window.scannedQRsInShelf.add(scannedQr);
                const targetMatch = window.shelfItems.find(i => {
                    const iQr = (i.qr || '').replace(/\s+/g, '').toUpperCase();
                    const iSeri = (i.seri_no || '').replace(/\s+/g, '').toUpperCase();
                    return iQr === normCode || normCode.includes(iQr) || iQr.includes(normCode) || (iSeri && iSeri === normCode) || (scannedQr && iQr === scannedQr.replace(/\s+/g, '').toUpperCase());
                });

                if (targetMatch) {
                    window.scannedQRsInShelf.add(targetMatch.qr);
                }

                renderAuditTable();
                if (targetMatch) {
                    showAuditMsg(`🟢 Ürün okundu (Tereğimde VAR): ${targetMatch.product_name} (Koli: ${newKoliNo}).`, false);
                } else {
                    showAuditMsg(`Koli (${newKoliNo}) tereğinize eklendi! Toplam ${newItems.length} ürün stokta bulundu.`, false);
                }
            }

            const wrapper = document.getElementById('audit-results-wrapper');
            if (wrapper) wrapper.classList.remove('hidden');

        } else {
            showAuditMsg('Sorgulama Hatası: ' + (data.error || 'Bilinmeyen hata'), true);
        }
    } catch (err) {
        showAuditMsg('Sunucu hatası: ' + err.message, true);
    } finally {
        const mainInput = document.getElementById('audit-input-main');
        if (mainInput) {
            mainInput.value = '';
            mainInput.focus();
        }
    }
}

window.handleAuditKeypress = function(e) {
    const code = e.keyCode || e.which;
    if (code === 13 || code === 10 || code === 9 || e.key === 'Enter') {
        if (e.preventDefault) e.preventDefault();
        const el = document.getElementById('audit-input-main');
        if (el) {
            const rawCode = el.value.trim();
            el.value = '';
            if (rawCode) handleScanSubmit(rawCode);
        }
    }
};

window.handleAuditChange = function(el) {
    const mainInput = document.getElementById('audit-input-main');
    if (mainInput && mainInput.value.trim()) {
        const rawCode = mainInput.value.trim();
        mainInput.value = '';
        handleScanSubmit(rawCode);
    }
};

window.handleAuditInput = function(el) {
    // Intentionally no-op to prevent premature truncation of barcode scanner input
};

window.triggerAuditSubmit = function() {
    console.log("triggerAuditSubmit called");
    const el = document.getElementById('audit-input-main');
    if (el) {
        const rawCode = el.value.trim();
        el.value = '';
        if (rawCode) handleScanSubmit(rawCode);
    }
};

window.resetAudit = function() {
    console.log("resetAudit called");
    window.shelfKoliMap.clear();
    window.shelfItems = [];
    window.scannedQRsInShelf.clear();
    window.allWarehouseItems = [];
    window.isAuditAllMode = false;

    renderAuditTable();
    hideAuditMsg();

    const wrapper = document.getElementById('audit-results-wrapper');
    if (wrapper) wrapper.classList.add('hidden');

    const mainInput = document.getElementById('audit-input-main');
    if (mainInput) {
        mainInput.value = '';
        mainInput.disabled = false;
        mainInput.placeholder = "Barkod veya İlaç QR okutun (Enter'a basın)...";
        mainInput.focus();
    }

    showAuditMsg('🧹 Terek sayımı temizlendi. Yeni sayım yapabilirsiniz.', false);
};

window.downloadAuditExcel = async function() {
    console.log("downloadAuditExcel called");
    const activeList = window.isAuditAllMode ? window.allWarehouseItems : window.shelfItems;
    if (activeList.length === 0) return;

    const missingItems = activeList.filter(i => !isItemScanned(i)).map(i => ({
        "Koli Numarası": i.koli_no,
        "Ürün Adı": i.product_name,
        "Karekod": i.qr,
        "Gtin": i.gtin,
        "Seri Numarası": i.seri_no,
        "Parti Numarası": i.parti_no,
        "Palet Numarası": i.palet_no,
        "Açıklama": "Tereğümde yok, Bakanlık sitesinden çıkış yapılacak ürün"
    }));

    if (missingItems.length === 0) {
        showAuditMsg(`Tereğinizdeki tüm ürünler fiziken mevcut! Bakanlıktan çıkılacak eksik ürün yok.`, false);
        return;
    }

    const btnAuditExcel = document.getElementById('btn-dl-audit-excel');
    if (btnAuditExcel) {
        btnAuditExcel.disabled = true;
        btnAuditExcel.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> İndiriliyor...';
    }

    try {
        const response = await fetch('/api/download/audit_excel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(missingItems)
        });

        if (!response.ok) throw new Error("Excel oluşturulamadı");

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `terek_eksik_urunler_bakanlik_cikis.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    } catch (err) {
        showAuditMsg("Excel indirme hatası: " + err.message, true);
    } finally {
        if (btnAuditExcel) {
            btnAuditExcel.disabled = false;
            btnAuditExcel.innerHTML = '<i class="fa-solid fa-file-excel"></i> 📥 Tereğümde Olmayan Ürünleri İndir (Excel)';
        }
    }
};

window.transferMissingToCikis = async function() {
    const activeList = window.isAuditAllMode ? window.allWarehouseItems : window.shelfItems;
    if (!activeList || activeList.length === 0) {
        alert("⚠️ Henüz terek sayımı yapılmadı. Lütfen önce koli veya ürün QR okutun.");
        return;
    }

    const missingItems = activeList.filter(i => !isItemScanned(i));

    if (missingItems.length === 0) {
        alert("🟢 Tereğinizdeki tüm ürünler tam! Çıkış listesine aktarılacak eksik ürün bulunmamaktadır.");
        return;
    }

    const count = missingItems.length;
    const confirmMsg = `🔴 EMİN MİSİNİZ?\n\nTereğinizde bulunmayan (eksik) ${count} adet ürünü Çıkış Listesine aktarmak istediğinize emin misiniz?`;
    
    if (!confirm(confirmMsg)) {
        return;
    }

    const qrList = missingItems.map(i => i.qr || i.Karekod || i.ham_karekod).filter(Boolean);

    const btn = document.getElementById('btn-audit-send-cikis');
    const originalHTML = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Aktarılıyor...';
    }

    try {
        const res = await fetch('/api/cikis/toplu_ekle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items: qrList })
        });
        const data = await res.json();
        if (data.success) {
            alert(`✅ BAŞARILI!\n\n${data.added_count} adet eksik ürün Çıkış Listesine aktarıldı.${data.already_count > 0 ? ` (${data.already_count} ürün zaten listedeydi)` : ''}`);
        } else {
            alert(`❌ Hata: ${data.error || 'Aktarım gerçekleştirilemedi.'}`);
        }
    } catch (err) {
        alert(`❌ Bağlantı hatası: ${err.message}`);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
        }
    }
};

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// Immediate polling start
startBkstPolling();

// Direct DOM Event Binding
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM loaded, starting status polling and binding event listeners...");
    startBkstPolling();

    const btnOpen = document.getElementById('btn-bkst-open');
    if (btnOpen) {
        btnOpen.addEventListener('click', (e) => {
            e.preventDefault();
            window.triggerBkstOpen();
        });
    }

    const btnFetch = document.getElementById('btn-bkst-fetch');
    if (btnFetch) {
        btnFetch.addEventListener('click', (e) => {
            e.preventDefault();
            window.triggerBkstFetch();
        });
    }

    const btnFetchApi = document.getElementById('btn-bkst-fetch-api');
    if (btnFetchApi) {
        btnFetchApi.addEventListener('click', (e) => {
            e.preventDefault();
            window.triggerBkstFetchApi();
        });
    }

    const btnAuditSubmit = document.getElementById('btn-audit-submit-trigger');
    if (btnAuditSubmit) {
        btnAuditSubmit.addEventListener('click', (e) => {
            e.preventDefault();
            window.triggerAuditSubmit();
        });
    }

    const dropZoneSystem = document.getElementById('drop-zone-system');
    const systemFileInput = document.getElementById('system-file');
    const fileInfoSystem = document.getElementById('file-info-system');
    const nameSystem = document.getElementById('name-system');
    const sizeSystem = document.getElementById('size-system');
    const btnClearSystem = document.getElementById('btn-clear-system');
    
    const dropZoneSales = document.getElementById('drop-zone-sales');
    const salesFileInput = document.getElementById('sales-file');
    const fileInfoSales = document.getElementById('file-info-sales');
    const nameSales = document.getElementById('name-sales');
    const sizeSales = document.getElementById('size-sales');
    const btnClearSales = document.getElementById('btn-clear-sales');

    const btnCompare = document.getElementById('btn-compare');
    const statTotalInitial = document.getElementById('val-total-system') || document.getElementById('stat-total-initial');
    const statTotalSold = document.getElementById('val-total-sales') || document.getElementById('stat-total-sold');
    const statTotalMatched = document.getElementById('val-matched') || document.getElementById('stat-total-matched');
    const statTotalRemaining = document.getElementById('val-remaining') || document.getElementById('stat-total-remaining');
    
    let fileSystem = null;
    let fileSales = null;

    function checkReadyToCompare() {
        if (btnCompare) btnCompare.disabled = !(fileSystem && fileSales);
    }

    function setupDragAndDrop(dropZone, fileInput, onSelect) {
        if (!dropZone || !fileInput) return;
        ['dragenter', 'dragover'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropZone.classList.add('dragover');
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropZone.classList.remove('dragover');
            }, false);
        });

        dropZone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files.length > 0) { onSelect(files[0]); }
        });

        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) { onSelect(e.target.files[0]); }
        });
    }

    if (dropZoneSystem && systemFileInput) {
        setupDragAndDrop(dropZoneSystem, systemFileInput, (file) => {
            fileSystem = file;
            nameSystem.textContent = file.name;
            sizeSystem.textContent = formatBytes(file.size);
            dropZoneSystem.classList.add('hidden');
            fileInfoSystem.classList.remove('hidden');
            checkReadyToCompare();
        });
    }

    if (dropZoneSales && salesFileInput) {
        setupDragAndDrop(dropZoneSales, salesFileInput, (file) => {
            fileSales = file;
            nameSales.textContent = file.name;
            sizeSales.textContent = formatBytes(file.size);
            dropZoneSales.classList.add('hidden');
            fileInfoSales.classList.remove('hidden');
            checkReadyToCompare();
        });
    }

    if (btnClearSystem) {
        btnClearSystem.addEventListener('click', () => {
            fileSystem = null;
            systemFileInput.value = '';
            fileInfoSystem.classList.add('hidden');
            dropZoneSystem.classList.remove('hidden');
            checkReadyToCompare();
        });
    }

    if (btnClearSales) {
        btnClearSales.addEventListener('click', () => {
            fileSales = null;
            salesFileInput.value = '';
            fileInfoSales.classList.add('hidden');
            dropZoneSales.classList.remove('hidden');
            checkReadyToCompare();
        });
    }

    if (btnCompare) {
        btnCompare.addEventListener('click', async () => {
            if (!fileSystem || !fileSales) return;

            const formData = new FormData();
            formData.append('system_file', fileSystem);
            formData.append('sales_file', fileSales);
            
            btnCompare.disabled = true;
            btnCompare.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Karşılaştırılıyor...`;
            
            try {
                const response = await fetch('/api/compare', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (response.ok && data.success) {
                    if (statTotalInitial) statTotalInitial.textContent = data.stats.total_system;
                    if (statTotalSold) statTotalSold.textContent = data.stats.total_sales;
                    if (statTotalMatched) statTotalMatched.textContent = data.stats.matched;
                    if (statTotalRemaining) statTotalRemaining.textContent = data.stats.remaining;

                    const overviewSection = document.getElementById('results-overview');
                    if (overviewSection) overviewSection.classList.remove('hidden');
                } else {
                    alert('Hata: ' + (data.error || 'Bilinmeyen bir hata oluştu.'));
                }
            } catch (err) {
                console.error(err);
                alert('Karşılaştırma hatası: ' + err.message);
            } finally {
                btnCompare.disabled = false;
                btnCompare.innerHTML = `<i class="fa-solid fa-bolt"></i> Karşılaştır ve Analiz Et`;
            }
        });
    }

    const btnBkstClose = document.getElementById('btn-bkst-close');
    const btnAuditResetKoli = document.getElementById('btn-audit-reset-koli');
    const btnAuditExcel = document.getElementById('btn-dl-audit-excel');
    const btnAuditToggleMode = document.getElementById('btn-audit-toggle-mode');

    if (btnBkstClose) {
        btnBkstClose.addEventListener('click', async () => {
            if (window.bkstPollTimer) { clearInterval(window.bkstPollTimer); window.bkstPollTimer = null; }
            await fetch('/api/bkst/close', { method: 'POST' });
            setBkstUI('closed', '');
        });
    }

    if (btnAuditToggleMode) {
        btnAuditToggleMode.addEventListener('click', (e) => {
            e.preventDefault();
            window.toggleAuditMode();
        });
    }

    if (btnAuditResetKoli) {
        btnAuditResetKoli.addEventListener('click', (e) => {
            e.preventDefault();
            window.resetAudit();
        });
    }

    if (btnAuditExcel) {
        btnAuditExcel.addEventListener('click', (e) => {
            e.preventDefault();
            window.downloadAuditExcel();
        });
    }

    // Sürüm Bilgisi Yükleme
    loadSystemVersion();
});

function loadSystemVersion() {
    fetch('/api/system/version')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const verStr = data.version || 'v3.1.0';
                document.querySelectorAll('#versionText, .version-text').forEach(el => {
                    el.textContent = verStr;
                });
                
                const h = document.getElementById('modalCommitHash');
                const d = document.getElementById('modalCommitDate');
                const m = document.getElementById('modalCommitMsg');
                if (h) h.textContent = data.commit_hash || verStr;
                if (d) d.textContent = data.commit_date || '08.10.2026';
                if (m) m.textContent = data.commit_msg || 'v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu ve stabilite güncellemeleri';
            }
        })
        .catch(e => console.warn('Version check error:', e));
}

window.showVersionModal = function() {
    let modal = document.getElementById('versionModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'versionModal';
        modal.className = 'modal';
        modal.style.cssText = 'display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6); backdrop-filter:blur(4px);';
        document.body.appendChild(modal);
    }
    
    modal.innerHTML = `
    <div style="background:#1e293b; color:#fff; max-width:450px; margin:10% auto; padding:24px; border-radius:16px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:12px; margin-bottom:16px;">
            <h3 style="margin:0; font-size:1.15rem; color:#38bdf8; display:flex; align-items:center; gap:8px;">
                <i class="fa-solid fa-circle-info"></i> Uygulama Sürüm Bilgisi
            </h3>
            <span onclick="closeVersionModal()" style="cursor:pointer; font-size:1.4rem; color:#94a3b8;">&times;</span>
        </div>
        <div style="font-size:0.95rem; line-height:1.8;">
            <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">v3.1.0</span></p>
            <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">08.10.2026</span></p>
            <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
            <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu ve genel stabilite güncellemeleri</div>
            <div style="margin-top:16px; background:rgba(34, 197, 94, 0.15); border:1px solid rgba(34, 197, 94, 0.3); color:#4ade80; padding:8px 12px; border-radius:8px; text-align:center; font-size:0.85rem; font-weight:600;">
                🟢 GitHub Sunucusu ile Eşitlendi & Güncel
            </div>
        </div>
        <div style="margin-top:16px; text-align:right;">
            <button onclick="closeVersionModal()" style="background:#3b82f6; color:#fff; border:none; padding:8px 18px; border-radius:8px; font-weight:600; cursor:pointer;">Kapat</button>
        </div>
    </div>`;
    
    modal.style.display = 'block';
    loadSystemVersion();
};

window.closeVersionModal = function() {
    const modal = document.getElementById('versionModal');
    if (modal) modal.style.display = 'none';
};

// ── Heartbeat Auto-Shutdown Monitor (Sekme Kapatılınca Sunucu Kapanır) ──────────
(function startHeartbeat() {
    function sendPing() {
        fetch('/api/system/heartbeat', { method: 'POST' }).catch(() => {});
    }
    sendPing();
    setInterval(sendPing, 3000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            sendPing();
        }
    });
})();

function cleanUserName(name) {
    if (!name) return "";
    let cleaned = String(name).trim().replace(/^\d+[\s\-]+/, "");
    if (cleaned.includes(" (")) {
        cleaned = cleaned.split(" (")[0].trim();
    }
    return cleaned || String(name).trim();
}

// ── Kullanıcı Bilgisi ve Oturum Kapatma (Logout) ──────────────────────────────────────────
async function loadUserInfo() {
    const userNameEl = document.getElementById('sidebar-user-name');
    const cachedName = localStorage.getItem('cached_user_name');
    if (userNameEl && cachedName && (userNameEl.textContent === 'Giriş Yapılmadı' || !userNameEl.textContent.trim())) {
        userNameEl.textContent = cachedName;
        userNameEl.title = cachedName;
    }
    try {
        const res = await fetch('/api/system/user_info');
        const data = await res.json();
        if (data.unauthenticated) {
            localStorage.removeItem('cached_user_name');
            if (userNameEl) userNameEl.textContent = 'Giriş Yapılmadı';
            if (window.location.pathname !== '/login') {
                window.location.href = '/login';
            }
            return;
        }
        if (userNameEl) {
            const displayName = cleanUserName(data.user_name || data.username || 'Giriş Yapılmadı');
            localStorage.setItem('cached_user_name', displayName);
            if (userNameEl.textContent !== displayName) {
                userNameEl.textContent = displayName;
                userNameEl.title = displayName;
            }
        }
    } catch (e) {
        console.error("User info error:", e);
    }
}

async function logoutUser() {
    if (!confirm("Oturumu kapatmak ve bakanlık giriş bilgilerinizi silmek istediğinize emin misiniz?")) {
        return;
    }
    try {
        sessionStorage.removeItem('bkst_auto_synced');
        localStorage.removeItem('cached_user_name');
        const res = await fetch('/api/system/logout', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            window.location.href = '/login';
        } else {
            alert(data.error || "Oturum kapatılamadı.");
        }
    } catch (e) {
        alert("Bağlantı hatası: " + e.message);
    }
}

window.logoutUser = logoutUser;

// ── Otomatik Bakanlık Veri Senkronizasyonu (Uygulama Açıldığında) ──────────────────────────
async function runAutoBkstSync() {
    if (window.location.pathname === '/login') return;

    if (sessionStorage.getItem('app_launch_synced')) {
        return;
    }
    sessionStorage.setItem('app_launch_synced', 'true');

    let loader = document.getElementById('auto-sync-loader');
    if (!loader) {
        loader = document.createElement('div');
        loader.id = 'auto-sync-loader';
        loader.style.cssText = 'display:flex; position:fixed; z-index:99999; left:0; top:0; width:100%; height:100%; background:rgba(10, 11, 16, 0.94); backdrop-filter:blur(14px); flex-direction:column; align-items:center; justify-content:center; text-align:center;';
        loader.innerHTML = `
            <div style="background:rgba(15, 23, 42, 0.96); border:1px solid rgba(56, 189, 248, 0.35); border-radius:24px; padding:2.8rem 3rem; max-width:520px; width:90%; box-shadow:0 25px 50px rgba(0,0,0,0.8); transition: all 0.3s ease;">
                <div id="loader-icon-box" style="width:80px; height:80px; border-radius:50%; background:rgba(56,189,248,0.15); border:2px solid rgba(56,189,248,0.4); margin:0 auto 1.5rem auto; display:flex; align-items:center; justify-content:center; transition: all 0.3s ease;">
                    <i id="loader-icon" class="fa-solid fa-cloud-arrow-down fa-bounce" style="font-size:2.4rem; color:#38bdf8;"></i>
                </div>
                <h2 id="loader-title" style="font-family:var(--font-outfit, sans-serif); font-size:1.45rem; font-weight:800; color:#fff; margin:0 0 0.6rem 0;">
                    Bakanlıktan Güncel Veriler Çekiliyor...
                </h2>
                <p id="loader-status" style="font-size:0.95rem; color:#94a3b8; margin:0 0 1.6rem 0; line-height:1.6; transition: all 0.3s ease;">
                    Lütfen bekleyin, BKST sunucusundan güncel stok ve karekod verileriniz otomatik çekiliyor.
                </p>
                <div style="background:rgba(255,255,255,0.06); height:6px; border-radius:10px; overflow:hidden; width:100%;">
                    <div id="loader-progress-bar" style="background:linear-gradient(90deg, #38bdf8, #818cf8); height:100%; width:100%; transition: background 0.4s ease, width 0.4s ease;"></div>
                </div>
            </div>
        `;
        document.body.appendChild(loader);
    } else {
        loader.style.display = 'flex';
    }

    const title = document.getElementById('loader-title');
    const status = document.getElementById('loader-status');
    const iconBox = document.getElementById('loader-icon-box');
    const icon = document.getElementById('loader-icon');
    const progressBar = document.getElementById('loader-progress-bar');

    try {
        const res = await fetch('/api/bkst/fetch_api', { method: 'POST' });
        const data = await res.json();

        if (data.unauthenticated) {
            if (loader) loader.style.display = 'none';
            if (window.location.pathname !== '/login') {
                window.location.href = '/login';
            }
            return;
        }

        if (data.success) {
            const count = data.item_count !== undefined ? data.item_count : 0;
            if (title) title.textContent = "✅ Veriler Başarıyla Çekildi!";
            if (status) {
                status.style.color = "#4ade80";
                status.style.fontWeight = "700";
                status.style.fontSize = "1.05rem";
                status.textContent = `Bakanlıktan Toplam ${count} Adet Stok Verisi Çekildi. Sisteme Aktarılıyor...`;
            }
            if (iconBox) {
                iconBox.style.background = "rgba(34, 197, 94, 0.2)";
                iconBox.style.borderColor = "rgba(34, 197, 94, 0.5)";
            }
            if (icon) {
                icon.className = "fa-solid fa-circle-check";
                icon.style.color = "#4ade80";
            }
            if (progressBar) {
                progressBar.style.background = "#22c55e";
            }
        } else {
            if (title) title.textContent = "⚠️ Veri Çekilirken Uyarı";
            if (status) {
                status.style.color = "#f87171";
                status.textContent = data.error || "Bakanlık API'sine bağlanılamadı.";
            }
        }

        await new Promise(resolve => setTimeout(resolve, 2500));
    } catch (err) {
        console.error('Otomatik BKST veri çekme hatası:', err);
        if (title) title.textContent = "❌ Bağlantı Hatası";
        if (status) status.textContent = "Sunucu ile bağlantı kurulamadı.";
        await new Promise(resolve => setTimeout(resolve, 2000));
    } finally {
        if (loader) loader.style.display = 'none';
        if (typeof window.loadWarehouseStock === 'function') {
            window.loadWarehouseStock();
        }
    }
}

async function checkWebSystemUpdate() {
    if (sessionStorage.getItem('update_checked')) return;
    sessionStorage.setItem('update_checked', 'true');
    try {
        const res = await fetch('/api/system/check_update');
        const data = await res.json();
        if (data && data.has_update) {
            showUpdateOverlay(data.remote_version || "Yeni Sürüm", data.message || "Sistem güncelleniyor...");
            await applyWebSystemUpdate();
        }
    } catch (e) {
        console.log("Check update error:", e);
    }
}

function showUpdateOverlay(version, message) {
    let overlay = document.getElementById('web-update-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'web-update-overlay';
        overlay.style.cssText = `
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(15, 23, 42, 0.96); backdrop-filter: blur(12px);
            z-index: 999999; display: flex; align-items: center; justify-content: center;
            font-family: 'Inter', sans-serif; color: #fff;
        `;
        overlay.innerHTML = `
            <div style="background: rgba(30, 41, 59, 0.9); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 16px; padding: 2.5rem 3rem; text-align: center; max-width: 480px; width: 90%; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);">
                <div style="font-size: 3rem; margin-bottom: 1rem;">🔄</div>
                <h2 style="font-size: 1.3rem; font-weight: 700; margin-bottom: 0.5rem; color: #38bdf8;">SİSTEM GÜNCELLEMESİ YÜKLENİYOR</h2>
                <p id="web-update-ver" style="font-size: 0.95rem; color: #94a3b8; margin-bottom: 1.5rem;">Sürüm ${version} indiriliyor. Lütfen bekleyin...</p>
                <div style="width: 100%; height: 8px; background: rgba(255,255,255,0.1); border-radius: 4px; overflow: hidden;">
                    <div id="web-update-bar" style="width: 40%; height: 100%; background: linear-gradient(90deg, #38bdf8, #818cf8); border-radius: 4px; transition: width 0.4s ease;"></div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
    }
}

async function applyWebSystemUpdate() {
    const bar = document.getElementById('web-update-bar');
    const ver = document.getElementById('web-update-ver');
    if (bar) bar.style.width = '75%';
    try {
        const res = await fetch('/api/system/apply_update', { method: 'POST' });
        const data = await res.json();
        if (bar) bar.style.width = '100%';
        if (ver) ver.textContent = "Güncelleme tamamlandı. Yeniden başlatılıyor...";
        
        for (let i = 0; i < 30; i++) {
            await new Promise(r => setTimeout(r, 1000));
            try {
                const checkRes = await fetch('/api/system/version');
                if (checkRes.ok) {
                    break;
                }
            } catch (e) {}
        }
        window.location.reload();
    } catch (e) {
        console.error("Apply update error:", e);
    }
}

function initApp() {
    loadUserInfo();
    runAutoBkstSync();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initApp);
} else {
    initApp();
}


```

---

### 📁 `templates/index.html`

```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Compare</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.png">
    <link rel="shortcut icon" href="/static/favicon.ico">
    <!-- Google Fonts Outfit & Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
    <!-- FontAwesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link rel="stylesheet" href="/static/style.css">
</head>
<body class="sidebar-layout">
    <div class="glass-bg-decor1"></div>
    <div class="glass-bg-decor2"></div>
    
    <div class="app-wrapper">
        <!-- SOL DİKİNE SIDEBAR NAVİGASYON -->
        <aside class="sidebar">
            <div class="sidebar-brand">
                <i class="fa-solid fa-qrcode logo-icon"></i>
                <div>
                    <h2>QR Compare</h2>
                    <p>Akıllı Stok Sistemi</p>
                </div>
            </div>

            <nav class="sidebar-menu">
                <a href="/cikis" class="sidebar-link">
                    <i class="fa-solid fa-box-open"></i>
                    <span>Barkod Okut & Çıkış</span>
                </a>
                <a href="/cikis-listesi" class="sidebar-link">
                    <i class="fa-solid fa-list-check"></i>
                    <span>Çıkış Listesi</span>
                </a>
                <a href="/stok-esitleme" class="sidebar-link active">
                    <i class="fa-solid fa-chart-line"></i>
                    <span>Stok & Eşitleme</span>
                </a>
                <a href="/depo_stoklari" class="sidebar-link">
                    <i class="fa-solid fa-warehouse"></i>
                    <span>Depomdaki Stoklar</span>
                </a>
                <a href="/depo_kabul" class="sidebar-link">
                    <i class="fa-solid fa-boxes-packing"></i>
                    <span>Depoya Kabul Et</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill">
                    <span class="status-indicator online"></span>
                    <span>Sistem Aktif</span>
                </div>
                <div class="user-profile-card">
                    <div class="user-profile-info">
                        <i class="fa-solid fa-user-circle user-avatar-icon"></i>
                        <span id="sidebar-user-name" class="user-name-title">{{ current_user_name }}</span>
                    </div>
                    <button type="button" class="btn-logout-icon" onclick="logoutUser()" title="Oturumdan Çıkış Yap">
                        <i class="fa-solid fa-power-off"></i>
                    </button>
                </div>
                <div class="version-pill" id="versionBadge" onclick="showVersionModal()" title="Sürüm Bilgisi">
                    <i class="fa-solid fa-code-branch" style="color: #38bdf8;"></i>
                    <span id="versionText">{{ current_app_version }}</span>
                </div>
            </div>
        </aside>

        <!-- SAĞ ANA İÇERİK ALANI -->
        <main class="main-content">

        <!-- SÜRÜM & GÜNCELLEME MODALI -->
        <div id="versionModal" class="modal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6); backdrop-filter:blur(4px);">
            <div style="background:#1e293b; color:#fff; max-width:450px; margin:10% auto; padding:24px; border-radius:16px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
                <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:12px; margin-bottom:16px;">
                    <h3 style="margin:0; font-size:1.15rem; color:#38bdf8; display:flex; align-items:center; gap:8px;">
                        <i class="fa-solid fa-circle-info"></i> Uygulama Sürüm Bilgisi
                    </h3>
                    <span onclick="closeVersionModal()" style="cursor:pointer; font-size:1.4rem; color:#94a3b8;">&times;</span>
                </div>
                <div style="font-size:0.95rem; line-height:1.8;">
                    <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">-</span></p>
                    <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">-</span></p>
                    <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                    <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">-</div>
                    <div style="margin-top:16px; background:rgba(34, 197, 94, 0.15); border:1px solid rgba(34, 197, 94, 0.3); color:#4ade80; padding:8px 12px; border-radius:8px; text-align:center; font-size:0.85rem; font-weight:600;">
                        🟢 GitHub Sunucusu ile Eşitlendi & Güncel
                    </div>
                </div>
                <div style="text-align:right; margin-top:20px;">
                    <button onclick="closeVersionModal()" style="background:#3b82f6; color:#fff; border:none; padding:8px 18px; border-radius:8px; font-weight:600; cursor:pointer;">Kapat</button>
                </div>
            </div>
        </div>

        <!-- HIZLI REHBER BAR -->
        <div class="quick-guide-bar">
            <div class="guide-item">
                <span class="guide-num">1</span>
                <div>
                    <strong>Bakanlık Verisi</strong>
                    <span>Sunucudan otomatik çekilir</span>
                </div>
            </div>
            <div class="guide-item">
                <span class="guide-num">2</span>
                <div>
                    <strong>Terek (Raf) Sayımı Yap</strong>
                    <span>Koli / Ürün QR okutarak eksikleri bulun</span>
                </div>
            </div>
            <div class="guide-item">
                <span class="guide-num">3</span>
                <div>
                    <strong>Çıkış Listesine Aktar</strong>
                    <span>Tereğinizde olmayan ürünleri tek tıkla düşüş yapın</span>
                </div>
            </div>
        </div>

        <!-- OTOMATİK YÜKLEME EKRANI (APPLICATION START LOADING OVERLAY) -->
        <div id="auto-sync-loader" style="display:none; position:fixed; z-index:99999; left:0; top:0; width:100%; height:100%; background:rgba(10, 11, 16, 0.94); backdrop-filter:blur(14px); flex-direction:column; align-items:center; justify-content:center; text-align:center;">
            <div style="background:rgba(15, 23, 42, 0.96); border:1px solid rgba(56, 189, 248, 0.35); border-radius:24px; padding:2.8rem 3rem; max-width:520px; width:90%; box-shadow:0 25px 50px rgba(0,0,0,0.8); transition: all 0.3s ease;">
                <div id="loader-icon-box" style="width:80px; height:80px; border-radius:50%; background:rgba(56,189,248,0.15); border:2px solid rgba(56,189,248,0.4); margin:0 auto 1.5rem auto; display:flex; align-items:center; justify-content:center; transition: all 0.3s ease;">
                    <i id="loader-icon" class="fa-solid fa-cloud-arrow-down fa-bounce" style="font-size:2.4rem; color:#38bdf8;"></i>
                </div>
                <h2 id="loader-title" style="font-family:var(--font-outfit); font-size:1.45rem; font-weight:800; color:#fff; margin:0 0 0.6rem 0;">
                    Bakanlıktan Güncel Veriler Çekiliyor...
                </h2>
                <p id="loader-status" style="font-size:0.95rem; color:var(--text-muted); margin:0 0 1.6rem 0; line-height:1.6; transition: all 0.3s ease;">
                    Lütfen bekleyin, BKST sunucusundan güncel stok ve karekod verileriniz otomatik çekiliyor.
                </p>
                <div style="background:rgba(255,255,255,0.06); height:6px; border-radius:10px; overflow:hidden; width:100%;">
                    <div id="loader-progress-bar" style="background:linear-gradient(90deg, #38bdf8, #818cf8); height:100%; width:100%; transition: background 0.4s ease, width 0.4s ease;"></div>
                </div>
            </div>
        </div>

        <!-- Terek (Raf) QR Sayım & Stok Eşitleme Paneli -->
        <section class="panel glass-card audit-panel">
            <div class="panel-head-flex">
                <div class="panel-title-group">
                    <i class="fa-solid fa-box-archive icon-green"></i>
                    <div>
                        <h2>Terek (Raf) QR Sayım & Stok Eşitleme</h2>
                        <p class="panel-subtitle">Koli veya ürün QR okutun. Bakanlık stoğundaki eksik ürünler otomatik tespit edilir.</p>
                    </div>
                </div>
            </div>

            <!-- Barkod Okutma Kutusu -->
            <div class="scan-input-wrapper">
                <i class="fa-solid fa-barcode scan-icon"></i>
                <input type="text" id="audit-input-main" onkeydown="window.handleAuditKeypress(event)" placeholder="Barkod veya İlaç QR okutun (Enter'a basın)..." autocomplete="off">
                <button type="button" id="btn-audit-submit-trigger" class="btn btn-success btn-scan">
                    <i class="fa-solid fa-magnifying-glass"></i> Oku / Ekle
                </button>
            </div>

            <!-- Bildirim Kutusu -->
            <div id="audit-msg-box" class="hidden audit-msg"></div>

            <!-- Sayım Sonuçları Paneli -->
            <div id="audit-results-wrapper" class="hidden">

                <!-- Stok Eşitleme Bildirimi -->
                <div id="audit-sync-alert-box" class="sync-alert-box">
                    <i class="fa-solid fa-triangle-exclamation alert-icon"></i>
                    <div id="audit-sync-alert-text" class="alert-text">
                        Tereğinizde fiziken bulunmayan 🔴 0 adet ürünü Bakanlık sitesinden ÇIKIŞ yapmalısınız!
                    </div>
                </div>

                <!-- İncelenen Koliler -->
                <div class="koli-badges-container">
                    <span class="koli-badges-label"><i class="fa-solid fa-boxes-packing color-blue"></i> İncelenen Koliler:</span>
                    <div id="audit-kolis-list" class="koli-badges-list">
                        <span class="badge">Henüz koli yüklenmedi</span>
                    </div>
                </div>

                <!-- İstatistik Özeti -->
                <div class="audit-stats-grid">
                    <div class="audit-stat-card">
                        <span class="audit-stat-lbl">Bakanlık Koli Stoğu</span>
                        <span id="audit-cnt-total" class="audit-stat-val">0 Adet</span>
                    </div>
                    <div class="audit-stat-card card-ok">
                        <span class="audit-stat-lbl">🟢 Tereğimde VAR</span>
                        <span id="audit-cnt-ok" class="audit-stat-val text-ok">0 Adet</span>
                    </div>
                    <div class="audit-stat-card card-missing">
                        <span class="audit-stat-lbl">🔴 Tereğimde YOK</span>
                        <span id="audit-cnt-missing" class="audit-stat-val text-missing">0 Adet</span>
                    </div>
                </div>

                <!-- Butonlar -->
                <div class="audit-actions">
                    <button class="btn btn-purple btn-sm" id="btn-audit-toggle-mode" type="button">
                        <i class="fa-solid fa-layer-group"></i> 📊 Okutulan Kalemleri Tüm Depoyla Karşılaştır
                    </button>
                    <button class="btn btn-danger btn-sm" id="btn-audit-send-cikis" type="button" onclick="transferMissingToCikis()">
                        <i class="fa-solid fa-box-open"></i> 🔴 Tereğimde Olmayanları Çıkış Listesine Aktar
                    </button>
                    <button class="btn btn-success btn-sm" id="btn-dl-audit-excel" type="button">
                        <i class="fa-solid fa-file-excel"></i> 📥 Tereğümde Olmayan Ürünleri İndir (Excel)
                    </button>
                    <button id="btn-audit-reset-koli" class="btn btn-outline btn-sm" type="button">
                        <i class="fa-solid fa-rotate-left"></i> Sayımı Temizle
                    </button>
                </div>

                <!-- Tablo -->
                <div class="table-scroll-container">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Ürün Adı</th>
                                <th>Koli No</th>
                                <th>Karekod</th>
                                <th>Seri No</th>
                                <th>Parti No</th>
                                <th>Palet No</th>
                                <th style="text-align: center;">Terek Durumu</th>
                                <th>Açıklama</th>
                            </tr>
                        </thead>
                        <tbody id="audit-table-body"></tbody>
                    </table>
                </div>
            </div>
        </section>

        <footer class="app-footer-bottom">
            <p>&copy; 2026 QR Compare - Akıllı Stok & Karekod Eşitleme</p>
        </footer>
        </main>
    </div>

    <script src="/static/app.js?v=20261005_v3000"></script>
</body>
</html>

```

---

### 📁 `templates/cikis.html`

```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Compare</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.png">
    <link rel="shortcut icon" href="/static/favicon.ico">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link rel="stylesheet" href="/static/style.css">
    <style>
        .cikis-layout {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1.5rem;
            margin-top: 1.5rem;
        }
        @media (max-width: 900px) { .cikis-layout { grid-template-columns: 1fr; } }

        .scan-panel {
            display: flex;
            flex-direction: column;
            gap: 1.2rem;
        }
        .scan-input-wrap {
            position: relative;
        }
        .scan-input-wrap i {
            position: absolute;
            left: 1rem;
            top: 50%;
            transform: translateY(-50%);
            color: var(--primary);
            font-size: 1.3rem;
        }
        #barkod-input {
            width: 100%;
            padding: 1rem 1rem 1rem 3rem;
            font-size: 1.2rem;
            font-family: var(--font-inter);
            background: rgba(255,255,255,0.06);
            border: 2px solid var(--primary);
            border-radius: 12px;
            color: var(--text-main);
            outline: none;
            transition: box-shadow .2s, border-color .2s;
            letter-spacing: .5px;
        }
        #barkod-input:focus {
            box-shadow: 0 0 0 4px var(--primary-glow);
        }
        #barkod-input.input-success { border-color: var(--success); box-shadow: 0 0 0 3px var(--success-glow); }
        #barkod-input.input-error   { border-color: var(--danger);  box-shadow: 0 0 0 3px rgba(239,68,68,.25); }

        .result-card {
            background: rgba(20,22,33,0.7);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            padding: 1.4rem 1.5rem;
            min-height: 180px;
            display: flex;
            flex-direction: column;
            gap: .8rem;
            transition: border-color .3s;
        }
        .result-card.success-card { border-color: var(--success); }
        .result-card.error-card   { border-color: var(--danger); }
        .result-card.warn-card    { border-color: var(--warning); }

        .result-card-title {
            font-size: .75rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-muted);
            margin-bottom: .2rem;
        }
        .result-urun-adi {
            font-size: 1.25rem;
            font-family: var(--font-outfit);
            font-weight: 700;
            color: var(--success);
        }
        .result-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: .5rem .8rem;
        }
        .result-field {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }
        .result-field .label {
            font-size: .7rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: .5px;
        }
        .result-field .val {
            font-size: .85rem;
            font-weight: 600;
            color: var(--text-main);
            word-break: break-all;
        }
        .uyari-badge {
            display: inline-flex;
            align-items: center;
            gap: .4rem;
            background: rgba(245,158,11,.15);
            border: 1px solid var(--warning);
            color: var(--warning);
            border-radius: 8px;
            padding: .35rem .7rem;
            font-size: .78rem;
            font-weight: 600;
        }
        .error-msg {
            display: flex;
            align-items: center;
            gap: .6rem;
            color: var(--danger);
            font-size: .95rem;
        }
        .counter-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(88,101,242,.12);
            border: 1px solid rgba(88,101,242,.25);
            border-radius: 10px;
            padding: .7rem 1rem;
        }
        .counter-bar .cnt {
            font-size: 1.8rem;
            font-family: var(--font-outfit);
            font-weight: 800;
            color: var(--primary);
            line-height: 1;
        }
        .counter-bar .cnt-label { font-size: .8rem; color: var(--text-muted); margin-top: 2px; }

        /* Recent table */
        .recent-panel { display: flex; flex-direction: column; gap: .8rem; }
        .recent-scroll { overflow-y: auto; max-height: 480px; }
        .recent-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
        .recent-table thead th {
            background: rgba(88,101,242,.18);
            color: var(--text-muted);
            font-size: .7rem;
            text-transform: uppercase;
            letter-spacing: .5px;
            padding: .55rem .8rem;
            text-align: left;
            position: sticky; top: 0;
        }
        .recent-table tbody tr { border-bottom: 1px solid rgba(255,255,255,.04); transition: background .15s; }
        .recent-table tbody tr:hover { background: rgba(255,255,255,.04); }
        .recent-table tbody td { padding: .55rem .8rem; color: var(--text-main); }
        .recent-table .warn-row td { background: rgba(245,158,11,.06); }
        .badge-sira {
            background: rgba(88,101,242,.2);
            color: var(--primary);
            border-radius: 6px;
            padding: 1px 8px;
            font-size: .75rem;
            font-weight: 700;
        }
        .btn-geri-al {
            background: rgba(239,68,68,.15);
            border: 1px solid rgba(239,68,68,.3);
            color: #f87171;
            border-radius: 6px;
            padding: .3rem .6rem;
            cursor: pointer;
            font-size: .75rem;
            transition: background .15s;
        }
        .btn-geri-al:hover { background: rgba(239,68,68,.3); }

        .nav-strip {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin-bottom: .5rem;
        }
        .nav-strip a {
            color: var(--text-muted);
            text-decoration: none;
            font-size: .85rem;
            display: flex; align-items: center; gap: .4rem;
            transition: color .15s;
        }
        .nav-strip a:hover { color: var(--text-main); }
        .nav-sep { color: var(--text-muted); }
        .nav-current { color: var(--text-main); font-weight: 600; font-size: .85rem; }

        .pulse-dot {
            width: 10px; height: 10px;
            border-radius: 50%;
            background: var(--success);
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { box-shadow: 0 0 0 0 var(--success-glow); }
            50%       { box-shadow: 0 0 0 6px transparent; }
        }
    </style>
</head>
<body class="sidebar-layout">
    <div class="glass-bg-decor1"></div>
    <div class="glass-bg-decor2"></div>

    <div class="app-wrapper">
        <!-- SOL DİKİNE SIDEBAR NAVİGASYON -->
        <aside class="sidebar">
            <div class="sidebar-brand">
                <i class="fa-solid fa-qrcode logo-icon"></i>
                <div>
                    <h2>QR Compare</h2>
                    <p>Akıllı Stok Sistemi</p>
                </div>
            </div>

            <nav class="sidebar-menu">
                <a href="/cikis" class="sidebar-link active">
                    <i class="fa-solid fa-box-open"></i>
                    <span>Barkod Okut & Çıkış</span>
                </a>
                <a href="/cikis-listesi" class="sidebar-link">
                    <i class="fa-solid fa-list-check"></i>
                    <span>Çıkış Listesi</span>
                </a>
                <a href="/stok-esitleme" class="sidebar-link">
                    <i class="fa-solid fa-chart-line"></i>
                    <span>Stok & Eşitleme</span>
                </a>
                <a href="/depo_stoklari" class="sidebar-link">
                    <i class="fa-solid fa-warehouse"></i>
                    <span>Depomdaki Stoklar</span>
                </a>
                <a href="/depo_kabul" class="sidebar-link">
                    <i class="fa-solid fa-boxes-packing"></i>
                    <span>Depoya Kabul Et</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill">
                    <span class="status-indicator online"></span>
                    <span>Sistem Aktif</span>
                </div>
                <div class="user-profile-card">
                    <div class="user-profile-info">
                        <i class="fa-solid fa-user-circle user-avatar-icon"></i>
                        <span id="sidebar-user-name" class="user-name-title">{{ current_user_name }}</span>
                    </div>
                    <button type="button" class="btn-logout-icon" onclick="logoutUser()" title="Oturumdan Çıkış Yap">
                        <i class="fa-solid fa-power-off"></i>
                    </button>
                </div>
                <div class="version-pill" onclick="showVersionModal()" title="Sürüm Bilgisi">
                    <i class="fa-solid fa-code-branch" style="color: #38bdf8;"></i>
                    <span id="versionText">{{ current_app_version }}</span>
                </div>
            </div>
        </aside>

        <!-- SAĞ ANA İÇERİK ALANI -->
        <main class="main-content">
            <header class="content-header">
                <div>
                    <h1 class="page-title"><i class="fa-solid fa-box-open icon-purple"></i> Barkod Okut & Sistemden Çıkış</h1>
                    <p class="page-subtitle">Elden satılan ürünlerinizi hızlıca okutarak stoktan düşün.</p>
                </div>
            </header>

        <!-- REHBER BİLGİLENDİRME KUTUSU -->
        <div style="background: rgba(88, 101, 242, 0.08); border: 1px solid rgba(88, 101, 242, 0.25); border-radius: 14px; padding: 1rem 1.4rem; margin-bottom: 1rem;">
            <div style="display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.5rem;">
                <i class="fa-solid fa-circle-question" style="font-size: 1.3rem; color: #a5b4fc;"></i>
                <h3 style="font-family: var(--font-outfit); font-size: 1.1rem; font-weight: 700; color: #fff; margin: 0;">
                    📦 Barkod Okutarak Ürün Çıkış Rehberi (Hızlı Adımlar)
                </h3>
            </div>
            <div style="font-size: 0.88rem; color: var(--text-muted); line-height: 1.6;">
                <b>1.</b> Barkod okuyucuyu mor kutuya getirin ve karekodu okutun.<br>
                <b>2.</b> Ürün depoda kayıtlıysa 🟢 <b>Kayıt Başarılı</b> olarak düşer.<br>
                <b>3.</b> Çift okutmalarda sistem sizi uyarır.<br>
                <b>4.</b> Hatalı okutmaları sağdaki listeden 🔴 <b>"Geri Al"</b> butonuyla silebilirsiniz.
            </div>
        </div>

        <!-- Page Title Row -->
        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:1rem; margin-bottom:1rem;">
            <div>
                <h2 style="font-family:var(--font-outfit); font-size:1.6rem; font-weight:800; color:var(--text-main);">
                    <i class="fa-solid fa-box-open" style="color:var(--primary);"></i>
                    Sistemden Çıkış Modülü
                </h2>
                <p style="color:var(--text-muted); font-size:.9rem; margin-top:.2rem;">
                    Barkod okutarak ürünleri sisteme kaydedin
                </p>
            </div>
            <a href="/cikis-listesi" class="btn btn-primary" style="text-decoration:none; display:flex; align-items:center; gap:.5rem;">
                <i class="fa-solid fa-list-check"></i>
                Sistemden Çıkacaklar Listesi
            </a>
        </div>

        <!-- Main Layout -->
        <div class="cikis-layout">

            <!-- Sol: Barkod Okutma -->
            <section class="panel glass-card scan-panel">
                <h3 style="font-family:var(--font-outfit); font-weight:700; font-size:1.1rem;">
                    <i class="fa-solid fa-barcode" style="color:var(--primary);"></i>
                    Barkod Okut
                </h3>

                <!-- Counter -->
                <div class="counter-bar">
                    <div>
                        <div class="cnt" id="cnt-toplam">0</div>
                        <div class="cnt-label">Toplam Okutlanan</div>
                    </div>
                    <i class="fa-solid fa-boxes-stacked" style="font-size:1.8rem; color:rgba(88,101,242,.4);"></i>
                </div>

                <!-- Input -->
                <div class="scan-input-wrap">
                    <i class="fa-solid fa-barcode"></i>
                    <input type="text" id="barkod-input"
                           placeholder="Barkodu okutun veya yazın ve Enter'a basın..."
                           autocomplete="off" autocorrect="off" spellcheck="false">
                </div>
                <p style="color:var(--text-muted); font-size:.8rem; text-align:center;">
                    <i class="fa-solid fa-circle-info"></i>
                    Barkod okuyucu Enter gönderince otomatik kayıt yapılır
                </p>

                <!-- Result Card -->
                <div class="result-card" id="result-card">
                    <p class="result-card-title">Son Okutlanan Ürün</p>
                    <div style="flex:1; display:flex; align-items:center; justify-content:center; color:var(--text-muted); font-size:.9rem; gap:.5rem;">
                        <i class="fa-regular fa-circle-dot"></i>
                        Henüz barkod okutulmadı
                    </div>
                </div>
            </section>

            <!-- Sağ: Son Okutlanlar -->
            <section class="panel glass-card recent-panel">
                <div style="display:flex; align-items:center; justify-content:space-between;">
                    <h3 style="font-family:var(--font-outfit); font-weight:700; font-size:1.1rem;">
                        <i class="fa-solid fa-clock-rotate-left" style="color:var(--success);"></i>
                        Son Okutlanlar
                    </h3>
                    <button class="btn btn-outline" id="btn-temizle-son" style="font-size:.78rem; padding:.35rem .8rem;">
                        <i class="fa-solid fa-trash"></i> Tümünü Sil
                    </button>
                </div>

                <div class="recent-scroll">
                    <table class="recent-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Ürün Adı</th>
                                <th>Koli No</th>
                                <th>Seri No</th>
                                <th>Saat</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody id="recent-tbody">
                            <tr><td colspan="6" style="text-align:center; color:var(--text-muted); padding:2rem;">
                                Henüz kayıt yok
                            </td></tr>
                        </tbody>
                    </table>
                </div>

                <a href="/cikis-listesi" class="btn btn-success" style="text-decoration:none; text-align:center; display:flex; align-items:center; justify-content:center; gap:.5rem; font-weight:700;">
                    <i class="fa-solid fa-file-export"></i>
                    Sistemden Çıkacaklar Listesini Gör
                </a>
            </section>
        </div>

        <footer class="app-footer-bottom" style="margin-top:2rem;">
            <p>&copy; 2026 QR Compare - Tüm Hakları Saklıdır.</p>
        </footer>
        </main>
    </div>

<script>
const barkodInput  = document.getElementById('barkod-input');
const resultCard   = document.getElementById('result-card');
const cntToplam    = document.getElementById('cnt-toplam');
const recentTbody  = document.getElementById('recent-tbody');
let   satirSayisi  = 0;

// Sayfa yüklenince input'a odaklan ve mevcut kayıtları getir
window.addEventListener('load', () => {
    barkodInput.focus();
    loadRecent();
});

// Enter'a basınca okut
barkodInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
        const val = barkodInput.value.trim();
        if (val) okutBarkod(val);
    }
});

async function okutBarkod(barkod) {
    barkodInput.disabled = true;
    barkodInput.classList.remove('input-success', 'input-error');

    try {
        const res = await fetch('/api/cikis/okut', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({barkod})
        });
        const data = await res.json();

        if (data.success) {
            barkodInput.classList.add('input-success');
            showResultCard(data.kayit, data.tekrar_uyari);
            prependRow(data.kayit, data.tekrar_uyari);
            cntToplam.textContent = parseInt(cntToplam.textContent || '0') + 1;
        } else {
            barkodInput.classList.add('input-error');
            showErrorCard(data.error);
        }
    } catch(err) {
        barkodInput.classList.add('input-error');
        showErrorCard('Sunucuya bağlanılamadı: ' + err.message);
    } finally {
        barkodInput.value = '';
        barkodInput.disabled = false;
        barkodInput.focus();
        setTimeout(() => barkodInput.classList.remove('input-success','input-error'), 1500);
    }
}

function showResultCard(k, tekrar) {
    const warnHtml = tekrar
        ? `<span class="uyari-badge"><i class="fa-solid fa-triangle-exclamation"></i> Bu barkod daha önce okutulmuştu!</span>`
        : '';
    resultCard.className = 'result-card ' + (tekrar ? 'warn-card' : 'success-card');
    resultCard.innerHTML = `
        <p class="result-card-title"><i class="fa-solid fa-check-circle" style="color:var(--success)"></i> Kayıt Başarılı</p>
        ${warnHtml}
        <div class="result-urun-adi">${esc(k.urun_adi || '—')}</div>
        <div class="result-grid">
            <div class="result-field"><span class="label">Gtin/Barkod</span><span class="val">${esc(k.barkod || '—')}</span></div>
            <div class="result-field"><span class="label">Koli Numarası</span><span class="val">${esc(k.koli_no || '—')}</span></div>
            <div class="result-field"><span class="label">Seri Numarası</span><span class="val">${esc(k.seri_no || '—')}</span></div>
            <div class="result-field"><span class="label">Parti Numarası</span><span class="val">${esc(k.parti_no || '—')}</span></div>
            <div class="result-field"><span class="label">SKT</span><span class="val">${esc(k.skt || '—')}</span></div>
            <div class="result-field"><span class="label">Kayıt Zamanı</span><span class="val">${esc(k.tarih || '—')}</span></div>
        </div>`;
}

function showErrorCard(msg) {
    resultCard.className = 'result-card error-card';
    resultCard.innerHTML = `
        <p class="result-card-title"><i class="fa-solid fa-circle-xmark" style="color:var(--danger)"></i> Hata</p>
        <div class="error-msg"><i class="fa-solid fa-triangle-exclamation"></i> ${esc(msg)}</div>`;
}

function prependRow(k, tekrar) {
    satirSayisi++;
    const emptyRow = recentTbody.querySelector('td[colspan]');
    if (emptyRow) emptyRow.closest('tr').remove();

    const tr = document.createElement('tr');
    if (tekrar) tr.classList.add('warn-row');
    tr.dataset.id = k.id;
    tr.innerHTML = `
        <td><span class="badge-sira">${satirSayisi}</span></td>
        <td style="max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${esc(k.urun_adi)}">${esc(k.urun_adi || '—')}</td>
        <td>${esc(k.koli_no || '—')}</td>
        <td>${esc(k.seri_no || '—')}</td>
        <td style="font-size:.75rem; color:var(--text-muted);">${(k.tarih || '').split(' ')[1] || ''}</td>
        <td><button class="btn-geri-al" onclick="geriAl(${k.id}, this)"><i class="fa-solid fa-rotate-left"></i></button></td>`;
    recentTbody.prepend(tr);
}

async function geriAl(id, btn) {
    if (!confirm('Bu kaydı geri almak istediğinize emin misiniz?')) return;
    const res = await fetch(`/api/cikis/sil/${id}`, {method:'DELETE'});
    const data = await res.json();
    if (data.success) {
        btn.closest('tr').remove();
        const cnt = parseInt(cntToplam.textContent || '0');
        cntToplam.textContent = Math.max(0, cnt - 1);
        if (!recentTbody.children.length) {
            recentTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:2rem;">Henüz kayıt yok</td></tr>';
        }
    }
}

async function loadRecent() {
    const res  = await fetch('/api/cikis/listesi');
    const data = await res.json();
    if (!data.success) return;
    cntToplam.textContent = data.toplam;
    if (!data.kayitlar.length) return;
    recentTbody.innerHTML = '';
    satirSayisi = data.toplam;
    let no = data.toplam;
    data.kayitlar.forEach(k => {
        const tr = document.createElement('tr');
        if (k.tekrar_uyari) tr.classList.add('warn-row');
        tr.dataset.id = k.id;
        tr.innerHTML = `
            <td><span class="badge-sira">${no--}</span></td>
            <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(k.urun_adi)}">${esc(k.urun_adi||'—')}</td>
            <td>${esc(k.koli_no||'—')}</td>
            <td>${esc(k.seri_no||'—')}</td>
            <td style="font-size:.75rem;color:var(--text-muted);">${(k.tarih||'').split(' ')[1]||''}</td>
            <td><button class="btn-geri-al" onclick="geriAl(${k.id},this)"><i class="fa-solid fa-rotate-left"></i></button></td>`;
        recentTbody.appendChild(tr);
    });
}

document.getElementById('btn-temizle-son').addEventListener('click', async () => {
    if (!confirm('Tüm çıkış kayıtları silinecek. Emin misiniz?')) return;
    const res  = await fetch('/api/cikis/temizle', {method:'POST'});
    const data = await res.json();
    if (data.success) {
        recentTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:2rem;">Henüz kayıt yok</td></tr>';
        cntToplam.textContent = '0';
        resultCard.className = 'result-card';
        resultCard.innerHTML = `<p class="result-card-title">Son Okutlanan Ürün</p>
            <div style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:.9rem;gap:.5rem;">
            <i class="fa-regular fa-circle-dot"></i> Henüz barkod okutulmadı</div>`;
    }
});

function esc(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
    </script>

    <!-- SÜRÜM & GÜNCELLEME MODALI -->
    <div id="versionModal" class="modal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6); backdrop-filter:blur(4px);">
        <div style="background:#1e293b; color:#fff; max-width:450px; margin:10% auto; padding:24px; border-radius:16px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:12px; margin-bottom:16px;">
                <h3 style="margin:0; font-size:1.15rem; color:#38bdf8; display:flex; align-items:center; gap:8px;">
                    <i class="fa-solid fa-circle-info"></i> Uygulama Sürüm Bilgisi
                </h3>
                <span onclick="closeVersionModal()" style="cursor:pointer; font-size:1.4rem; color:#94a3b8;">&times;</span>
            </div>
            <div style="font-size:0.95rem; line-height:1.8;">
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">v3.1.0</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">08.10.2026</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu, otomatik kapanma ve stabilite güncellemeleri</div>
                <div style="margin-top:16px; background:rgba(34, 197, 94, 0.15); border:1px solid rgba(34, 197, 94, 0.3); color:#4ade80; padding:8px 12px; border-radius:8px; text-align:center; font-size:0.85rem; font-weight:600;">
                    🟢 GitHub Sunucusu ile Eşitlendi & Güncel
                </div>
            </div>
            <div style="margin-top:16px; text-align:right;">
                <button onclick="closeVersionModal()" style="background:#3b82f6; color:#fff; border:none; padding:8px 18px; border-radius:8px; font-weight:600; cursor:pointer;">Kapat</button>
            </div>
        </div>
    </div>

    <script src="/static/app.js?v=20261005_v3000"></script>
</body>
</html>

```

---

### 📁 `templates/cikis_listesi.html`

```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Compare</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.png">
    <link rel="shortcut icon" href="/static/favicon.ico">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link rel="stylesheet" href="/static/style.css">
    <style>
        .list-topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        .stats-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        .stat-box {
            background: rgba(20,22,33,.6);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 1rem 1.2rem;
            display: flex;
            flex-direction: column;
            gap: .3rem;
        }
        .stat-box .s-val {
            font-size: 2rem;
            font-family: var(--font-outfit);
            font-weight: 800;
            line-height: 1;
        }
        .stat-box .s-lbl { font-size: .75rem; color: var(--text-muted); }

        .search-bar {
            display: flex;
            gap: .8rem;
            margin-bottom: 1rem;
            flex-wrap: wrap;
        }
        .search-bar input {
            flex: 1;
            min-width: 200px;
            padding: .6rem 1rem;
            background: rgba(255,255,255,.06);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            color: var(--text-main);
            font-family: var(--font-inter);
            outline: none;
        }
        .search-bar input:focus { border-color: var(--primary); }
        .search-bar select {
            padding: .6rem .8rem;
            background: rgba(255,255,255,.06);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            color: var(--text-main);
            outline: none;
        }

        .table-wrap { overflow-x: auto; }
        .full-table {
            width: 100%;
            border-collapse: collapse;
            font-size: .83rem;
        }
        .full-table thead th {
            background: rgba(88,101,242,.18);
            color: var(--text-muted);
            font-size: .7rem;
            text-transform: uppercase;
            letter-spacing: .6px;
            padding: .65rem .8rem;
            text-align: left;
            white-space: nowrap;
            cursor: pointer;
            user-select: none;
        }
        .full-table thead th:hover { color: var(--text-main); }
        .full-table thead th.sorted { color: var(--primary); }
        .full-table thead th.no-sort { cursor: default; }
        .full-table tbody tr {
            border-bottom: 1px solid rgba(255,255,255,.04);
            transition: background .12s;
        }
        .full-table tbody tr:hover { background: rgba(255,255,255,.04); }
        .full-table tbody td { padding: .55rem .8rem; color: var(--text-main); white-space: nowrap; }
        .full-table .warn-row td { background: rgba(245,158,11,.05); }
        .full-table .warn-row td:first-child { border-left: 3px solid var(--warning); }

        /* Sıra no + silme butonu hücresi */
        .td-sira {
            display: flex;
            align-items: center;
            gap: .4rem;
        }
        .badge-sira {
            background: rgba(88,101,242,.2);
            color: var(--primary);
            border-radius: 6px;
            padding: 2px 8px;
            font-size: .75rem;
            font-weight: 700;
            min-width: 28px;
            text-align: center;
        }
        .btn-del {
            background: rgba(239,68,68,.15);
            border: 1px solid rgba(239,68,68,.3);
            color: #f87171;
            border-radius: 6px;
            padding: .25rem .5rem;
            cursor: pointer;
            font-size: .72rem;
            transition: background .15s;
            white-space: nowrap;
            line-height: 1.4;
        }
        .btn-del:hover { background: rgba(239,68,68,.35); }

        /* Karekod sütunu */
        .td-karekod {
            font-family: monospace;
            font-size: .75rem;
            color: var(--text-muted);
            max-width: 180px;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .empty-state {
            text-align: center;
            padding: 4rem 2rem;
            color: var(--text-muted);
        }
        .empty-state i { font-size: 3rem; margin-bottom: 1rem; display: block; opacity: .3; }
        .empty-state a { color: var(--primary); text-decoration: none; font-weight: 600; }

        .tekrar-badge {
            display: inline-block;
            background: rgba(245,158,11,.15);
            color: var(--warning);
            border-radius: 4px;
            padding: 1px 5px;
            font-size: .7rem;
            font-weight: 700;
        }

        .action-btns { display: flex; gap: .7rem; flex-wrap: wrap; }

        .nav-strip {
            display: flex; align-items: center; gap: 1rem; margin-bottom: .5rem;
        }
        .nav-strip a { color: var(--text-muted); text-decoration: none; font-size:.85rem;
            display:flex; align-items:center; gap:.4rem; transition:color .15s; }
        .nav-strip a:hover { color: var(--text-main); }
        .nav-sep { color: var(--text-muted); }
        .nav-current { color: var(--text-main); font-weight:600; font-size:.85rem; }
    </style>
</head>
<body class="sidebar-layout">
    <div class="glass-bg-decor1"></div>
    <div class="glass-bg-decor2"></div>

    <div class="app-wrapper">
        <!-- SOL DİKİNE SIDEBAR NAVİGASYON -->
        <aside class="sidebar">
            <div class="sidebar-brand">
                <i class="fa-solid fa-qrcode logo-icon"></i>
                <div>
                    <h2>QR Compare</h2>
                    <p>Akıllı Stok Sistemi</p>
                </div>
            </div>

            <nav class="sidebar-menu">
                <a href="/cikis" class="sidebar-link">
                    <i class="fa-solid fa-box-open"></i>
                    <span>Barkod Okut & Çıkış</span>
                </a>
                <a href="/cikis-listesi" class="sidebar-link active">
                    <i class="fa-solid fa-list-check"></i>
                    <span>Çıkış Listesi</span>
                </a>
                <a href="/stok-esitleme" class="sidebar-link">
                    <i class="fa-solid fa-chart-line"></i>
                    <span>Stok & Eşitleme</span>
                </a>
                <a href="/depo_stoklari" class="sidebar-link">
                    <i class="fa-solid fa-warehouse"></i>
                    <span>Depomdaki Stoklar</span>
                </a>
                <a href="/depo_kabul" class="sidebar-link">
                    <i class="fa-solid fa-boxes-packing"></i>
                    <span>Depoya Kabul Et</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill">
                    <span class="status-indicator online"></span>
                    <span>Sistem Aktif</span>
                </div>
                <div class="user-profile-card">
                    <div class="user-profile-info">
                        <i class="fa-solid fa-user-circle user-avatar-icon"></i>
                        <span id="sidebar-user-name" class="user-name-title">{{ current_user_name }}</span>
                    </div>
                    <button type="button" class="btn-logout-icon" onclick="logoutUser()" title="Oturumdan Çıkış Yap">
                        <i class="fa-solid fa-power-off"></i>
                    </button>
                </div>
                <div class="version-pill" onclick="showVersionModal()" title="Sürüm Bilgisi">
                    <i class="fa-solid fa-code-branch" style="color: #38bdf8;"></i>
                    <span id="versionText">{{ current_app_version }}</span>
                </div>
            </div>
        </aside>

        <!-- SAĞ ANA İÇERİK ALANI -->
        <main class="main-content">
            <header class="content-header">
                <div>
                    <h1 class="page-title"><i class="fa-solid fa-list-check icon-primary"></i> Sistemden Çıkacaklar Listesi</h1>
                    <p class="page-subtitle">Sistemden düşümü yapılan tüm ürünlerin geçmiş kayıtları ve Excel çıktıları.</p>
                </div>
            </header>

        <!-- REHBER BİLGİLENDİRME KUTUSU -->
        <div style="background: rgba(88, 101, 242, 0.08); border: 1px solid rgba(88, 101, 242, 0.25); border-radius: 14px; padding: 1rem 1.4rem; margin-bottom: 1rem;">
            <div style="display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.5rem;">
                <i class="fa-solid fa-list-check" style="font-size: 1.3rem; color: #a5b4fc;"></i>
                <h3 style="font-family: var(--font-outfit); font-size: 1.1rem; font-weight: 700; color: #fff; margin: 0;">
                    📋 Çıkış Yapılan Ürünler Listesi Rehberi (Hızlı Adımlar)
                </h3>
            </div>
            <div style="font-size: 0.88rem; color: var(--text-muted); line-height: 1.6;">
                • <b>Arama:</b> Arama kutusuna ürün adı veya koli no yazın.<br>
                • <b>Excel İndir:</b> Sağ üstteki <b>"Excel İndir"</b> butonundan tüm çıkışları bilgisayara aktarın.<br>
                • <b>Silme (Geri Alma):</b> İlgili satırın yanındaki 🔴 <b>Çöp Kovası</b> butonuna basarak kaydı silin.
            </div>
        </div>

        <!-- Stats Row -->
        <div class="stats-row">
            <div class="stat-box">
                <div class="s-val" id="stat-toplam" style="color:var(--primary);">—</div>
                <div class="s-lbl">Toplam Kayıt</div>
            </div>
            <div class="stat-box">
                <div class="s-val" id="stat-tekil" style="color:var(--success);">—</div>
                <div class="s-lbl">Tekil Ürün</div>
            </div>
            <div class="stat-box">
                <div class="s-val" id="stat-tekrar" style="color:var(--warning);">—</div>
                <div class="s-lbl">Tekrar Okutlan</div>
            </div>
        </div>

        <!-- Main Card -->
        <section class="panel glass-card">
            <div class="list-topbar">
                <h2 style="font-family:var(--font-outfit); font-size:1.3rem; font-weight:800;">
                    <i class="fa-solid fa-list-check" style="color:var(--primary);"></i>
                    Sistemden Çıkacaklar Listesi
                </h2>
                <div class="action-btns">
                    <a href="/cikis" class="btn btn-outline"
                       style="text-decoration:none; display:flex; align-items:center; gap:.4rem; font-size:.85rem;">
                        <i class="fa-solid fa-barcode"></i> Barkod Okut
                    </a>
                    <a href="/api/cikis/indir" class="btn btn-success"
                       style="text-decoration:none; display:flex; align-items:center; gap:.4rem; font-size:.85rem;">
                        <i class="fa-solid fa-file-excel"></i> Excel İndir
                    </a>
                    <button class="btn btn-outline" id="btn-tumunu-sil"
                            style="font-size:.85rem; border-color:rgba(239,68,68,.4); color:#f87171;">
                        <i class="fa-solid fa-trash"></i> Tümünü Sil
                    </button>
                </div>
            </div>

            <!-- Arama -->
            <div class="search-bar">
                <input type="text" id="search-input"
                       placeholder="Ürün adı, karekod, koli no ile ara..."
                       autocomplete="off">
                <select id="filter-col">
                    <option value="all">Tüm Sütunlar</option>
                    <option value="urun_adi">Ürün Adı</option>
                    <option value="ham_karekod">Karekod</option>
                    <option value="barkod">Barkod/Gtin</option>
                    <option value="koli_no">Koli No</option>
                    <option value="seri_no">Seri No</option>
                    <option value="parti_no">Parti No</option>
                </select>
            </div>

            <!-- Tablo -->
            <div class="table-wrap">
                <table class="full-table" id="main-table">
                    <thead>
                        <tr>
                            <!-- Sıra no + silme butonu -->
                            <th class="no-sort" style="width:90px;">#  &nbsp;Sil</th>
                            <!-- Karekod en başta -->
                            <th onclick="sortBy('ham_karekod')">
                                Karekod <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('urun_adi')">
                                Ürün Adı <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('koli_no')">
                                Koli No <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('seri_no')">
                                Seri No <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('parti_no')">
                                Parti No <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('palet_no')">
                                Palet No <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('barkod')">
                                Gtin/Barkod <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('uretim_tarihi')">
                                Üretim Tar. <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('skt')">
                                SKT <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                            <th onclick="sortBy('tarih')">
                                Kayıt Zamanı <i class="fa-solid fa-sort sort-ico"></i>
                            </th>
                        </tr>
                    </thead>
                    <tbody id="main-tbody">
                        <tr><td colspan="11">
                            <div class="empty-state">
                                <i class="fa-solid fa-spinner fa-spin"></i>
                                <p>Yükleniyor...</p>
                            </div>
                        </td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Tablo altı bilgi -->
            <div style="margin-top:1rem; display:flex; align-items:center; justify-content:space-between;
                        color:var(--text-muted); font-size:.8rem; flex-wrap:wrap; gap:.5rem;">
                <span id="visible-count"></span>
                <span>
                    <i class="fa-solid fa-triangle-exclamation" style="color:var(--warning);"></i>
                    Sarı satır = aynı barkod birden fazla okutulmuş
                </span>
            </div>
        </section>

        <footer class="app-footer-bottom" style="margin-top:2rem;">
            <p>&copy; 2026 QR Compare - Tüm Hakları Saklıdır.</p>
        </footer>
        </main>
    </div>

<script>
let allData  = [];
let sortCol  = 'id';
let sortAsc  = false;

window.addEventListener('load', loadList);

async function loadList() {
    try {
        const res  = await fetch('/api/cikis/listesi');
        const data = await res.json();
        if (!data.success) return;
        allData = data.kayitlar;
        updateStats();
        render();
    } catch(e) {
        document.getElementById('main-tbody').innerHTML =
            `<tr><td colspan="11"><div class="empty-state">
                <i class="fa-solid fa-circle-xmark"></i>
                <p>Sunucuya bağlanılamadı: ${e.message}</p>
             </div></td></tr>`;
    }
}

function updateStats() {
    document.getElementById('stat-toplam').textContent = allData.length;
    const tekilSet = new Set(allData.map(r => r.ham_karekod));
    document.getElementById('stat-tekil').textContent  = tekilSet.size;
    document.getElementById('stat-tekrar').textContent = allData.filter(r => r.tekrar_uyari).length;
}

function render() {
    const q     = document.getElementById('search-input').value.toLowerCase();
    const col   = document.getElementById('filter-col').value;
    const tbody = document.getElementById('main-tbody');

    let filtered = allData.filter(r => {
        if (!q) return true;
        if (col === 'all')
            return Object.values(r).some(v => String(v||'').toLowerCase().includes(q));
        return String(r[col]||'').toLowerCase().includes(q);
    });

    // Sırala
    filtered.sort((a, b) => {
        let va = String(a[sortCol]||''), vb = String(b[sortCol]||'');
        if (!isNaN(va) && !isNaN(vb)) { va = Number(va); vb = Number(vb); }
        if (va < vb) return sortAsc ? -1 : 1;
        if (va > vb) return sortAsc ?  1 : -1;
        return 0;
    });

    document.getElementById('visible-count').textContent =
        `${filtered.length} / ${allData.length} kayıt gösteriliyor`;

    if (!filtered.length) {
        tbody.innerHTML = `<tr><td colspan="11"><div class="empty-state">
            <i class="fa-solid fa-box-open"></i>
            <p>${allData.length
                ? 'Arama sonucu bulunamadı.'
                : 'Henüz çıkış kaydı yok. <a href="/cikis">Barkod okutmak için tıklayın.</a>'}</p>
        </div></td></tr>`;
        return;
    }

    tbody.innerHTML = filtered.map((r, i) => `
        <tr class="${r.tekrar_uyari ? 'warn-row' : ''}" data-id="${r.id}">

            <!-- # + Silme butonu -->
            <td>
                <div class="td-sira">
                    <span class="badge-sira">${i + 1}</span>
                    <button class="btn-del" onclick="silKayit(${r.id}, this)"
                            title="Bu kaydı sil">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            </td>

            <!-- Karekod — EN BAŞTA -->
            <td class="td-karekod" title="${esc(r.ham_karekod)}">${esc(r.ham_karekod||'—')}</td>

            <!-- Ürün Adı -->
            <td style="max-width:220px; overflow:hidden; text-overflow:ellipsis;" title="${esc(r.urun_adi)}">
                ${r.tekrar_uyari ? '<span class="tekrar-badge">TEKRAR</span> ' : ''}${esc(r.urun_adi||'—')}
            </td>

            <td>${esc(r.koli_no||'—')}</td>
            <td>${esc(r.seri_no||'—')}</td>
            <td>${esc(r.parti_no||'—')}</td>
            <td>${esc(r.palet_no||'—')}</td>
            <td style="font-family:monospace; font-size:.78rem;">${esc(r.barkod||'—')}</td>
            <td>${esc(r.uretim_tarihi||'—')}</td>
            <td>${esc(r.skt||'—')}</td>
            <td style="font-size:.76rem; color:var(--text-muted);">${esc(r.tarih||'—')}</td>
        </tr>`).join('');
}

function sortBy(col) {
    sortAsc = (sortCol === col) ? !sortAsc : true;
    sortCol = col;
    // Başlık vurgusu
    document.querySelectorAll('.full-table thead th').forEach(th => th.classList.remove('sorted'));
    document.querySelectorAll('.full-table thead th').forEach(th => {
        if (th.onclick && th.getAttribute('onclick') && th.getAttribute('onclick').includes(`'${col}'`))
            th.classList.add('sorted');
    });
    render();
}

async function silKayit(id, btn) {
    if (!confirm('Bu kaydı silmek istiyor musunuz?')) return;
    btn.disabled = true;
    const res  = await fetch(`/api/cikis/sil/${id}`, {method: 'DELETE'});
    const data = await res.json();
    if (data.success) {
        allData = allData.filter(r => r.id !== id);
        updateStats();
        render();
    } else {
        btn.disabled = false;
        alert('Silme işlemi başarısız.');
    }
}

document.getElementById('btn-tumunu-sil').addEventListener('click', async () => {
    if (!confirm(`Toplam ${allData.length} kaydın TAMAMI silinecek. Emin misiniz?`)) return;
    const res  = await fetch('/api/cikis/temizle', {method: 'POST'});
    const data = await res.json();
    if (data.success) {
        allData = [];
        updateStats();
        render();
    }
});

document.getElementById('search-input').addEventListener('input', render);
document.getElementById('filter-col').addEventListener('change', render);

function esc(str) {
    return String(str||'')
        .replace(/&/g,'&amp;')
        .replace(/</g,'&lt;')
        .replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;');
}
    </script>

    <!-- SÜRÜM & GÜNCELLEME MODALI -->
    <div id="versionModal" class="modal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6); backdrop-filter:blur(4px);">
        <div style="background:#1e293b; color:#fff; max-width:450px; margin:10% auto; padding:24px; border-radius:16px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:12px; margin-bottom:16px;">
                <h3 style="margin:0; font-size:1.15rem; color:#38bdf8; display:flex; align-items:center; gap:8px;">
                    <i class="fa-solid fa-circle-info"></i> Uygulama Sürüm Bilgisi
                </h3>
                <span onclick="closeVersionModal()" style="cursor:pointer; font-size:1.4rem; color:#94a3b8;">&times;</span>
            </div>
            <div style="font-size:0.95rem; line-height:1.8;">
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">v3.1.0</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">08.10.2026</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu, otomatik kapanma ve stabilite güncellemeleri</div>
                <div style="margin-top:16px; background:rgba(34, 197, 94, 0.15); border:1px solid rgba(34, 197, 94, 0.3); color:#4ade80; padding:8px 12px; border-radius:8px; text-align:center; font-size:0.85rem; font-weight:600;">
                    🟢 GitHub Sunucusu ile Eşitlendi & Güncel
                </div>
            </div>
            <div style="margin-top:16px; text-align:right;">
                <button onclick="closeVersionModal()" style="background:#3b82f6; color:#fff; border:none; padding:8px 18px; border-radius:8px; font-weight:600; cursor:pointer;">Kapat</button>
            </div>
        </div>
    </div>

    <script src="/static/app.js?v=20261005_v3000"></script>
</body>
</html>

```

---

### 📁 `templates/depo_stoklari.html`

```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Compare</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.png">
    <link rel="shortcut icon" href="/static/favicon.ico">
    <!-- Google Fonts Outfit & Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <!-- FontAwesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link rel="stylesheet" href="/static/style.css">
    <style>
        .filter-btn-group {
            display: flex;
            gap: 0.6rem;
            flex-wrap: wrap;
            margin-bottom: 1.2rem;
        }
        .filter-tab {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: var(--text-muted);
            padding: 0.6rem 1.1rem;
            border-radius: 10px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            transition: all 0.2s ease;
        }
        .filter-tab:hover {
            color: #fff;
            background: rgba(255, 255, 255, 0.08);
        }
        .filter-tab.active-tab {
            background: linear-gradient(135deg, #2563eb, #3b82f6);
            color: #fff;
            border-color: rgba(59, 130, 246, 0.4);
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
        }
        .filter-tab.active-tab-warn {
            background: linear-gradient(135deg, #d97706, #f59e0b);
            color: #fff;
            border-color: rgba(245, 158, 11, 0.4);
            box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
        }
        .product-link-btn {
            color: #38bdf8;
            font-weight: 700;
            text-decoration: none;
            cursor: pointer;
            transition: color 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .product-link-btn:hover {
            color: #7dd3fc;
            text-decoration: underline;
        }
        .badge-skt-critical {
            background: rgba(239, 68, 68, 0.18);
            color: #f87171;
            border: 1px solid rgba(239, 68, 68, 0.35);
            padding: 3px 8px;
            border-radius: 6px;
            font-weight: 700;
            font-size: 0.78rem;
            white-space: nowrap;
        }
        .badge-skt-warn {
            background: rgba(245, 158, 11, 0.18);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.35);
            padding: 3px 8px;
            border-radius: 6px;
            font-weight: 700;
            font-size: 0.78rem;
            white-space: nowrap;
        }
        .badge-skt-ok {
            background: rgba(16, 185, 129, 0.15);
            color: #6ee7b7;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.78rem;
            white-space: nowrap;
        }
        /* Table Layout Optimizations */
        .kabul-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.86rem;
        }
        .kabul-table th, .kabul-table td {
            padding: 0.75rem 0.9rem;
            vertical-align: middle;
        }
        .kabul-table th {
            background: rgba(30, 41, 59, 0.95);
            position: sticky;
            top: 0;
            z-index: 5;
        }
        .nowrap-cell {
            white-space: nowrap;
        }
        .prod-name-clamp {
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            word-break: break-word;
            max-width: 340px;
            line-height: 1.4;
        }
    </style>
</head>
<body class="sidebar-layout">
    <div class="glass-bg-decor1"></div>
    <div class="glass-bg-decor2"></div>

    <div class="app-wrapper">
        <!-- SOL DİKİNE SIDEBAR NAVİGASYON -->
        <aside class="sidebar">
            <div class="sidebar-brand">
                <i class="fa-solid fa-qrcode logo-icon"></i>
                <div>
                    <h2>QR Compare</h2>
                    <p>Akıllı Stok Sistemi</p>
                </div>
            </div>

            <nav class="sidebar-menu">
                <a href="/cikis" class="sidebar-link">
                    <i class="fa-solid fa-box-open"></i>
                    <span>Barkod Okut & Çıkış</span>
                </a>
                <a href="/cikis-listesi" class="sidebar-link">
                    <i class="fa-solid fa-list-check"></i>
                    <span>Çıkış Listesi</span>
                </a>
                <a href="/stok-esitleme" class="sidebar-link">
                    <i class="fa-solid fa-chart-line"></i>
                    <span>Stok & Eşitleme</span>
                </a>
                <a href="/depo_stoklari" class="sidebar-link active">
                    <i class="fa-solid fa-warehouse"></i>
                    <span>Depomdaki Stoklar</span>
                </a>
                <a href="/depo_kabul" class="sidebar-link">
                    <i class="fa-solid fa-boxes-packing"></i>
                    <span>Depoya Kabul Et</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill">
                    <span class="status-indicator online"></span>
                    <span>Sistem Aktif</span>
                </div>
                <div class="user-profile-card">
                    <div class="user-profile-info">
                        <i class="fa-solid fa-user-circle user-avatar-icon"></i>
                        <span id="sidebar-user-name" class="user-name-title">{{ current_user_name }}</span>
                    </div>
                    <button type="button" class="btn-logout-icon" onclick="logoutUser()" title="Oturumdan Çıkış Yap">
                        <i class="fa-solid fa-power-off"></i>
                    </button>
                </div>
                <div class="version-pill" onclick="showVersionModal()" title="Sürüm Bilgisi">
                    <i class="fa-solid fa-code-branch" style="color: #38bdf8;"></i>
                    <span id="versionText">{{ current_app_version }}</span>
                </div>
            </div>
        </aside>

        <!-- SAĞ ANA İÇERİK ALANI -->
        <main class="main-content">
            <!-- HEADER BAR -->
            <header class="content-header">
                <div>
                    <h1 class="page-title"><i class="fa-solid fa-warehouse icon-blue"></i> Depomdaki Stoklar</h1>
                    <p class="page-subtitle">Sisteminizde ve yerel veritabanınızda kayıtlı tüm stoklar ve karekod detayları.</p>
                </div>
                <div class="header-actions">
                    <button class="btn btn-primary" id="btn-fetch-api" type="button" onclick="triggerApiFetch()">
                        <i class="fa-solid fa-cloud-arrow-down"></i> ⚡ BKST'den Güncel Verileri Çek (API)
                    </button>
                    <a href="/api/bkst/download_api_data" class="btn btn-success" download>
                        <i class="fa-solid fa-file-excel"></i> Stok Listesini Excel İndir
                    </a>
                </div>
            </header>

            <!-- İSTATİSTİK KARTLARI -->
            <div class="depo-stats-grid">
                <!-- Stat Card 1: Kalem -->
                <div class="stat-card-tech card-blue-glow" onclick="setFilterMode('kalem')" title="İlaç çeşitlerine göre gruplanmış özet görünüm">
                    <div class="stat-tech-header">
                        <div class="stat-tech-icon icon-blue-bg">
                            <i class="fa-solid fa-capsules"></i>
                        </div>
                        <span class="stat-tech-badge badge-blue">İlaç Çeşitleri</span>
                    </div>
                    <div class="stat-tech-body">
                        <h2 class="stat-tech-val val-blue" id="stat-kalem">0</h2>
                        <p class="stat-tech-lbl">Farklı İlaç Çeşidi (Kalem)</p>
                    </div>
                    <div class="stat-tech-footer">
                        <span><i class="fa-solid fa-list"></i> Özet Tabloya Geç</span>
                        <i class="fa-solid fa-chevron-right arrow-icon"></i>
                    </div>
                </div>

                <!-- Stat Card 2: Kutu -->
                <div class="stat-card-tech card-purple-glow" onclick="setFilterMode('kutu')" title="Tüm karekodları tekil liste halinde incele">
                    <div class="stat-tech-header">
                        <div class="stat-tech-icon icon-purple-bg">
                            <i class="fa-solid fa-boxes-stacked"></i>
                        </div>
                        <span class="stat-tech-badge badge-purple">Tekil Karekodlar</span>
                    </div>
                    <div class="stat-tech-body">
                        <h2 class="stat-tech-val val-purple" id="stat-total">0</h2>
                        <p class="stat-tech-lbl">Depodaki Toplam Kutu (Karekod)</p>
                    </div>
                    <div class="stat-tech-footer">
                        <span><i class="fa-solid fa-barcode"></i> Tüm Karekodları Gör</span>
                        <i class="fa-solid fa-chevron-right arrow-icon"></i>
                    </div>
                </div>

                <!-- Stat Card 3: SKT Alarmı -->
                <div class="stat-card-tech card-amber-glow" onclick="setFilterMode('skt')" title="Son Kullanma Tarihi 90 günden az kalan kritik ürünler">
                    <div class="stat-tech-header">
                        <div class="stat-tech-icon icon-amber-bg">
                            <i class="fa-solid fa-clock-rotate-left"></i>
                        </div>
                        <span class="stat-tech-badge badge-amber">Kritik SKT Alarmı</span>
                    </div>
                    <div class="stat-tech-body">
                        <h2 class="stat-tech-val val-amber" id="stat-skt">0</h2>
                        <p class="stat-tech-lbl">⚠️ SKT'si 3 Aydan Az Kalanlar</p>
                    </div>
                    <div class="stat-tech-footer">
                        <span><i class="fa-solid fa-filter"></i> Kritik Ürünleri Süz</span>
                        <i class="fa-solid fa-chevron-right arrow-icon"></i>
                    </div>
                </div>
            </div>

            <!-- FİLTRE & GÖRÜNÜM SEÇİM BAR I -->
            <section class="panel glass-card">
                <div class="filter-btn-group">
                    <button class="filter-tab active-tab" id="tab-kalem" onclick="setFilterMode('kalem')">
                        <i class="fa-solid fa-layer-group"></i> 📋 Kalem Kalem Listele (Özet)
                    </button>
                    <button class="filter-tab" id="tab-kutu" onclick="setFilterMode('kutu')">
                        <i class="fa-solid fa-boxes-stacked"></i> 🔍 Tüm Karekodlar (Tekil Liste)
                    </button>
                    <button class="filter-tab" id="tab-skt" onclick="setFilterMode('skt')" style="border-color: rgba(245, 158, 11, 0.4);">
                        <i class="fa-solid fa-clock-rotate-left" style="color:#f59e0b;"></i> ⚠️ SKT'si 3 Aydan Az Kalanlar (<span id="btn-skt-cnt">0</span>)
                    </button>
                </div>

                <div class="search-bar-wrap" style="display:flex; gap:0.8rem; margin-bottom:1rem;">
                    <div class="search-input-box" style="flex:1; position:relative;">
                        <i class="fa-solid fa-magnifying-glass" style="position:absolute; left:1rem; top:50%; transform:translateY(-50%); color:var(--text-muted);"></i>
                        <input type="text" id="input-search" placeholder="Ürün Adı, GTIN, Karekod, Parti No veya Koli No ile arayın..." style="width:100%; padding:0.75rem 1rem 0.75rem 2.8rem; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); border-radius:10px; color:#fff; font-family:var(--font-inter); outline:none;">
                    </div>
                    <button class="btn btn-outline" type="button" id="btn-reset-search">
                        <i class="fa-solid fa-rotate-left"></i> Sıfırla
                    </button>
                </div>

                <!-- STOK TABLOSU -->
                <div class="data-table-wrapper margin-top-md" style="max-height: 600px; overflow-x: auto;">
                    <table class="kabul-table" id="table-stoklar">
                        <thead id="thead-stoklar">
                            <!-- JS ile Dinamik Doldurulur -->
                        </thead>
                        <tbody id="tbody-stoklar">
                            <tr>
                                <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 3rem;">
                                    <i class="fa-solid fa-spinner fa-spin" style="font-size: 1.5rem;"></i><br><br>Depo stok verileri yükleniyor...
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </section>
        </main>
    </div>

    <!-- ÜRÜN KAREKOD DETAY MODALI -->
    <div id="productDetailModal" class="modal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.75); backdrop-filter:blur(8px);">
        <div style="background:#1e293b; color:#fff; max-width:960px; width:92%; margin:3% auto; padding:24px; border-radius:20px; border:1px solid rgba(255,255,255,0.15); box-shadow:0 25px 35px -5px rgba(0,0,0,0.8); max-height:88vh; display:flex; flex-direction:column;">
            
            <!-- Modal Header -->
            <div style="display:flex; justify-content:space-between; align-items:flex-start; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:14px; margin-bottom:14px; flex-wrap:wrap; gap:1rem;">
                <div>
                    <h3 style="margin:0; font-size:1.25rem; color:#38bdf8; display:flex; align-items:center; gap:8px;" id="modal-product-title">
                        <i class="fa-solid fa-capsules"></i> Ürün Karekod Detayları
                    </h3>
                    <p style="margin:4px 0 0 0; font-size:0.85rem; color:#94a3b8;" id="modal-product-gtin">GTIN: -</p>
                </div>
                <div style="display:flex; align-items:center; gap:1rem;">
                    <span class="badge" id="modal-count-badge" style="background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); padding:6px 14px; border-radius:10px; font-weight:700; font-size:0.9rem;">
                        0 Adet Kutu
                    </span>
                    <span onclick="closeDetailModal()" style="cursor:pointer; font-size:1.6rem; color:#94a3b8; line-height:1; transition:color 0.2s;">&times;</span>
                </div>
            </div>

            <!-- Modal Live Search Input -->
            <div style="margin-bottom: 12px; position: relative;">
                <i class="fa-solid fa-magnifying-glass" style="position:absolute; left:1rem; top:50%; transform:translateY(-50%); color:var(--text-muted);"></i>
                <input type="text" id="modal-search-input" placeholder="🔍 Bu ilaç içinde Karekod, Koli No, Seri No veya Parti No ile arayın..." style="width:100%; padding:0.65rem 1rem 0.65rem 2.7rem; background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.12); border-radius:10px; color:#fff; font-family:var(--font-inter); font-size:0.88rem; outline:none;">
            </div>

            <!-- Modal Data Table -->
            <div class="data-table-wrapper" style="flex:1; max-height: 480px; overflow-y: auto; overflow-x: auto; border-radius:12px; border:1px solid rgba(255,255,255,0.08);">
                <table class="kabul-table" style="width:100%; border-collapse:collapse; font-size:0.85rem;">
                    <thead style="position:sticky; top:0; background:#0f172a; z-index:5;">
                        <tr>
                            <th style="width:45px; text-align:center;">#</th>
                            <th style="white-space:nowrap;">Karekod</th>
                            <th style="white-space:nowrap;">Seri No</th>
                            <th style="white-space:nowrap;">Parti No</th>
                            <th style="white-space:nowrap;">Koli No</th>
                            <th style="white-space:nowrap;">SKT</th>
                        </tr>
                    </thead>
                    <tbody id="modal-tbody-details">
                        <!-- JS ile doldurulur -->
                    </tbody>
                </table>
            </div>

            <div style="margin-top:16px; display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:0.82rem; color:var(--text-muted);" id="modal-footer-stats">Görüntülenen: 0 / 0</span>
                <button onclick="closeDetailModal()" class="btn btn-primary" style="padding:8px 24px;">Kapat</button>
            </div>
        </div>
    </div>

    <!-- SÜRÜM MODALI -->
    <!-- SÜRÜM & GÜNCELLEME MODALI -->
    <div id="versionModal" class="modal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6); backdrop-filter:blur(4px);">
        <div style="background:#1e293b; color:#fff; max-width:450px; margin:10% auto; padding:24px; border-radius:16px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:12px; margin-bottom:16px;">
                <h3 style="margin:0; font-size:1.15rem; color:#38bdf8; display:flex; align-items:center; gap:8px;">
                    <i class="fa-solid fa-circle-info"></i> Uygulama Sürüm Bilgisi
                </h3>
                <span onclick="closeVersionModal()" style="cursor:pointer; font-size:1.4rem; color:#94a3b8;">&times;</span>
            </div>
            <div style="font-size:0.95rem; line-height:1.8;">
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">v3.1.0</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">08.10.2026</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu, otomatik kapanma ve stabilite güncellemeleri</div>
                <div style="margin-top:16px; background:rgba(34, 197, 94, 0.15); border:1px solid rgba(34, 197, 94, 0.3); color:#4ade80; padding:8px 12px; border-radius:8px; text-align:center; font-size:0.85rem; font-weight:600;">
                    🟢 GitHub Sunucusu ile Eşitlendi & Güncel
                </div>
            </div>
            <div style="margin-top:16px; text-align:right;">
                <button onclick="closeVersionModal()" style="background:#3b82f6; color:#fff; border:none; padding:8px 18px; border-radius:8px; font-weight:600; cursor:pointer;">Kapat</button>
            </div>
        </div>
    </div>

    <script>
        let allProducts = [];
        let currentMode = 'kalem'; // 'kalem', 'kutu', 'skt'
        let currentModalProducts = [];

        const thead = document.getElementById('thead-stoklar');
        const tbody = document.getElementById('tbody-stoklar');
        const inputSearch = document.getElementById('input-search');
        const btnReset = document.getElementById('btn-reset-search');
        
        const statTotal = document.getElementById('stat-total');
        const statKalem = document.getElementById('stat-kalem');
        const statSkt = document.getElementById('stat-skt');
        const btnSktCnt = document.getElementById('btn-skt-cnt');

        const tabKalem = document.getElementById('tab-kalem');
        const tabKutu = document.getElementById('tab-kutu');
        const tabSkt = document.getElementById('tab-skt');

        async function loadDepoStoklari() {
            try {
                const res = await fetch('/api/depo_stoklari');
                const data = await res.json();
                if (data.success && data.products) {
                    allProducts = data.products;
                    updateSummaryStats();
                    renderView();
                } else {
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:2rem;">Depoda henüz kayıtlı ürün bulunamadı.</td></tr>';
                }
            } catch (err) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#ef4444; padding:2rem;">Veriler yüklenirken hata oluştu: ' + esc(err.message) + '</td></tr>';
            }
        }

        async function triggerApiFetch() {
            const btn = document.getElementById('btn-fetch-api');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Veriler Çekiliyor...';
            
            try {
                const res = await fetch('/api/bkst/fetch_api', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    alert('✅ BKST verileri başarıyla güncellendi!');
                    await loadDepoStoklari();
                } else {
                    alert('❌ Hata: ' + (data.error || 'Veri çekilemedi.'));
                }
            } catch (err) {
                alert('❌ Bağlantı Hatası: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }

        // Tarih Hesabı & SKT Kontrolü
        function parseSKTDate(sktStr) {
            if (!sktStr) return null;
            sktStr = String(sktStr).trim();
            if (sktStr.includes('.')) {
                const p = sktStr.split('.');
                if (p.length === 3) return new Date(parseInt(p[2], 10), parseInt(p[1], 10) - 1, parseInt(p[0], 10));
            }
            if (sktStr.includes('-')) {
                const p = sktStr.split('-');
                if (p.length === 3) return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
            }
            return null;
        }

        function getDaysUntilSKT(sktStr) {
            const dt = parseSKTDate(sktStr);
            if (!dt) return 9999;
            const today = new Date();
            today.setHours(0,0,0,0);
            const diffTime = dt.getTime() - today.getTime();
            return Math.ceil(diffTime / (1000 * 60 * 60 * 24));
        }

        function updateSummaryStats() {
            statTotal.textContent = allProducts.length;

            const gtinGroupMap = {};
            let sktCriticalCount = 0;

            allProducts.forEach(p => {
                const gtin = p['Gtin / Barkod'] || p.BARCODE || p.GTIN || 'BELİRSİZ';
                const name = p['Ürün Adı'] || p.STOCKNAME || p.URUNADI || 'İsimsiz Ürün';
                const key = gtin + '___' + name;

                if (!gtinGroupMap[key]) {
                    gtinGroupMap[key] = [];
                }
                gtinGroupMap[key].push(p);

                const skt = p['Son Kullanma Tarihi'] || p.SKT;
                const days = getDaysUntilSKT(skt);
                if (days <= 90) {
                    sktCriticalCount++;
                }
            });

            const kalemCount = Object.keys(gtinGroupMap).length;
            statKalem.textContent = kalemCount;
            statSkt.textContent = sktCriticalCount;
            btnSktCnt.textContent = sktCriticalCount;
        }

        function setFilterMode(mode) {
            currentMode = mode;
            [tabKalem, tabKutu, tabSkt].forEach(t => t.classList.remove('active-tab', 'active-tab-warn'));

            if (mode === 'kalem') tabKalem.classList.add('active-tab');
            else if (mode === 'kutu') tabKutu.classList.add('active-tab');
            else if (mode === 'skt') tabSkt.classList.add('active-tab-warn');

            renderView();
        }

        function renderView() {
            const q = inputSearch.value.trim().toLowerCase();

            let filtered = allProducts;
            if (q) {
                filtered = allProducts.filter(p => {
                    const searchStr = [
                        p['Ürün Adı'], p.STOCKNAME, p.URUNADI,
                        p['Karekod'], p.KAREKOD,
                        p['Gtin / Barkod'], p.BARCODE, p.GTIN,
                        p['Seri Numarası'], p.SERIALNUMBER, p.SERINO,
                        p['Parti Numarası'], p.SARJNO, p.LOT,
                        p['Koli Numarası'], p.PAKETNO
                    ].map(v => String(v || '').toLowerCase()).join(' ');
                    return searchStr.includes(q);
                });
            }

            if (currentMode === 'skt') {
                filtered = filtered.filter(p => {
                    const skt = p['Son Kullanma Tarihi'] || p.SKT;
                    return getDaysUntilSKT(skt) <= 90;
                });
            }

            if (currentMode === 'kalem') {
                renderKalemTable(filtered);
            } else {
                renderKutuTable(filtered);
            }
        }

        // 1. KALEM KALEM ÖZET TABLOSU
        function renderKalemTable(items) {
            thead.innerHTML = `
                <tr>
                    <th style="width: 45px; text-align:center;">#</th>
                    <th>İlaç / Ürün Adı</th>
                    <th class="nowrap-cell">GTIN / Barkod</th>
                    <th style="text-align:center;" class="nowrap-cell">Depodaki Stok (Kutu Adedi)</th>
                    <th class="nowrap-cell">En Yakın SKT</th>
                    <th style="text-align:center;" class="nowrap-cell">İşlem</th>
                </tr>
            `;

            if (!items || items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted); padding:2.5rem;">Arama kriterlerine uygun ilaç kalemi bulunamadı.</td></tr>';
                return;
            }

            const groups = {};
            items.forEach(p => {
                const gtin = p['Gtin / Barkod'] || p.BARCODE || p.GTIN || '-';
                const name = p['Ürün Adı'] || p.STOCKNAME || p.URUNADI || 'Tanımsız Ürün';
                const key = gtin + '___' + name;

                if (!groups[key]) {
                    groups[key] = { gtin, name, key, products: [] };
                }
                groups[key].products.push(p);
            });

            const groupKeys = Object.keys(groups);

            tbody.innerHTML = groupKeys.map((key, idx) => {
                const g = groups[key];
                const count = g.products.length;

                let minDays = 9999;
                let earliestSKTStr = '-';
                g.products.forEach(p => {
                    const skt = p['Son Kullanma Tarihi'] || p.SKT || '';
                    const days = getDaysUntilSKT(skt);
                    if (days < minDays) {
                        minDays = days;
                        earliestSKTStr = skt;
                    }
                });

                let sktBadge = `<span class="badge-skt-ok">${esc(earliestSKTStr)}</span>`;
                if (minDays <= 0 && minDays !== 9999) {
                    sktBadge = `<span class="badge-skt-critical">🔴 SÜRESİ DOLMUŞ (${esc(earliestSKTStr)})</span>`;
                } else if (minDays <= 90 && minDays !== 9999) {
                    sktBadge = `<span class="badge-skt-warn">⚠️ ${minDays} Gün Kaldı (${esc(earliestSKTStr)})</span>`;
                }

                return `
                    <tr>
                        <td style="text-align:center;"><span class="badge-sira">${idx + 1}</span></td>
                        <td>
                            <a class="product-link-btn" onclick="openDetailModal('${esc(key)}')">
                                <i class="fa-solid fa-box-open" style="color:#38bdf8;"></i>
                                <span class="prod-name-clamp" title="${esc(g.name)}"><b>${esc(g.name)}</b></span>
                            </a>
                        </td>
                        <td class="nowrap-cell"><span style="font-family:monospace;">${esc(g.gtin)}</span></td>
                        <td style="text-align:center;" class="nowrap-cell">
                            <span style="background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); padding:4px 12px; border-radius:8px; font-weight:800; font-size:0.9rem;">
                                ${count} Adet
                            </span>
                        </td>
                        <td class="nowrap-cell">${sktBadge}</td>
                        <td style="text-align:center;" class="nowrap-cell">
                            <button class="btn btn-outline btn-sm" onclick="openDetailModal('${esc(key)}')">
                                <i class="fa-solid fa-magnifying-glass-plus"></i> Karekodları Gör (${count})
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');

            window.currentGroups = groups;
        }

        // 2. KUTU BAZLI TEKİL KAREKOD TABLOSU
        function renderKutuTable(items) {
            thead.innerHTML = `
                <tr>
                    <th style="width: 45px; text-align:center;">#</th>
                    <th>Ürün Adı</th>
                    <th class="nowrap-cell">Karekod</th>
                    <th class="nowrap-cell">GTIN / Barkod</th>
                    <th class="nowrap-cell">Seri No</th>
                    <th class="nowrap-cell">Parti No</th>
                    <th class="nowrap-cell">Koli No</th>
                    <th class="nowrap-cell">SKT</th>
                </tr>
            `;

            if (!items || items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:2.5rem;">Arama kriterlerine uygun karekod bulunamadı.</td></tr>';
                return;
            }

            tbody.innerHTML = items.map((p, idx) => {
                const qr = p['Karekod'] || p.KAREKOD || '-';
                const name = p['Ürün Adı'] || p.STOCKNAME || p.URUNADI || '-';
                const gtin = p['Gtin / Barkod'] || p.BARCODE || p.GTIN || '-';
                const seri = p['Seri Numarası'] || p.SERIALNUMBER || p.SERINO || '-';
                const parti = p['Parti Numarası'] || p.SARJNO || p.LOT || '-';
                const koli = p['Koli Numarası'] || p.PAKETNO || p.KOLINO || '-';
                const skt = p['Son Kullanma Tarihi'] || p.SKT || '-';

                const days = getDaysUntilSKT(skt);
                let sktBadge = `<span style="color:#6ee7b7;" class="nowrap-cell">${esc(skt)}</span>`;
                if (days <= 0 && days !== 9999) {
                    sktBadge = `<span class="badge-skt-critical">🔴 SÜRESİ DOLMUŞ (${esc(skt)})</span>`;
                } else if (days <= 90 && days !== 9999) {
                    sktBadge = `<span class="badge-skt-warn">⚠️ ${days} Gün Kaldı (${esc(skt)})</span>`;
                }

                return `
                    <tr>
                        <td style="text-align:center;"><span class="badge-sira">${idx + 1}</span></td>
                        <td><span class="prod-name-clamp" title="${esc(name)}"><b>${esc(name)}</b></span></td>
                        <td style="font-family:monospace; font-size:0.78rem; color:#38bdf8;" class="nowrap-cell">${esc(qr)}</td>
                        <td class="nowrap-cell">${esc(gtin)}</td>
                        <td class="nowrap-cell">${esc(seri)}</td>
                        <td class="nowrap-cell">${esc(parti)}</td>
                        <td class="nowrap-cell"><span style="font-family:monospace; color:#fbbf24;">${esc(koli)}</span></td>
                        <td class="nowrap-cell">${sktBadge}</td>
                    </tr>
                `;
            }).join('');
        }

        // DETAY MODALI AÇMA VE MODAL İÇİ LİVE ARAMA
        window.openDetailModal = function(groupKey) {
            const g = window.currentGroups ? window.currentGroups[groupKey] : null;
            if (!g) return;

            currentModalProducts = g.products || [];

            document.getElementById('modal-product-title').innerHTML = '<i class="fa-solid fa-capsules"></i> ' + esc(g.name);
            document.getElementById('modal-product-gtin').textContent = 'GTIN / Barkod: ' + (g.gtin || '-');
            document.getElementById('modal-count-badge').textContent = currentModalProducts.length + ' Adet Kutu';

            const modalSearchInput = document.getElementById('modal-search-input');
            modalSearchInput.value = '';

            renderModalTable(currentModalProducts);
            document.getElementById('productDetailModal').style.display = 'block';
        };

        function renderModalTable(items) {
            const modalTbody = document.getElementById('modal-tbody-details');
            const footerStats = document.getElementById('modal-footer-stats');

            footerStats.textContent = `Görüntülenen: ${items.length} / ${currentModalProducts.length} Kutu`;

            if (!items || items.length === 0) {
                modalTbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted); padding:2rem;">Arama kriterine uygun karekod veya koli bulunamadı.</td></tr>';
                return;
            }

            modalTbody.innerHTML = items.map((p, i) => {
                const qr = p['Karekod'] || p.KAREKOD || '-';
                const seri = p['Seri Numarası'] || p.SERIALNUMBER || p.SERINO || '-';
                const parti = p['Parti Numarası'] || p.SARJNO || p.LOT || '-';
                const koli = p['Koli Numarası'] || p.PAKETNO || p.KOLINO || '-';
                const skt = p['Son Kullanma Tarihi'] || p.SKT || '-';

                const days = getDaysUntilSKT(skt);
                let sktBadge = `<span style="color:#6ee7b7; white-space:nowrap;">${esc(skt)}</span>`;
                if (days <= 0 && days !== 9999) {
                    sktBadge = `<span class="badge-skt-critical" style="white-space:nowrap;">🔴 SÜRESİ DOLMUŞ (${esc(skt)})</span>`;
                } else if (days <= 90 && days !== 9999) {
                    sktBadge = `<span class="badge-skt-warn" style="white-space:nowrap;">⚠️ ${days} Gün Kaldı (${esc(skt)})</span>`;
                }

                return `
                    <tr>
                        <td style="text-align:center;"><span class="badge-sira">${i + 1}</span></td>
                        <td style="font-family:monospace; color:#38bdf8; font-size:0.8rem; white-space:nowrap;">${esc(qr)}</td>
                        <td style="white-space:nowrap;">${esc(seri)}</td>
                        <td style="white-space:nowrap;">${esc(parti)}</td>
                        <td style="white-space:nowrap;"><span style="font-family:monospace; color:#fbbf24;">${esc(koli)}</span></td>
                        <td>${sktBadge}</td>
                    </tr>
                `;
            }).join('');
        }

        document.getElementById('modal-search-input').addEventListener('input', (e) => {
            const q = e.target.value.trim().toLowerCase();
            if (!q) {
                renderModalTable(currentModalProducts);
                return;
            }
            const filtered = currentModalProducts.filter(p => {
                const str = [
                    p['Karekod'], p.KAREKOD,
                    p['Seri Numarası'], p.SERIALNUMBER, p.SERINO,
                    p['Parti Numarası'], p.SARJNO, p.LOT,
                    p['Koli Numarası'], p.PAKETNO,
                    p['Son Kullanma Tarihi'], p.SKT
                ].map(v => String(v || '').toLowerCase()).join(' ');
                return str.includes(q);
            });
            renderModalTable(filtered);
        });

        window.closeDetailModal = function() {
            document.getElementById('productDetailModal').style.display = 'none';
        };

        inputSearch.addEventListener('input', renderView);

        btnReset.addEventListener('click', () => {
            inputSearch.value = '';
            renderView();
        });

        function esc(str) {
            return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        loadDepoStoklari();
    </script>
    <script src="/static/app.js?v=20261005_v3000"></script>
</body>
</html>

```

---

### 📁 `templates/depo_kabul.html`

```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Compare</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.png">
    <link rel="shortcut icon" href="/static/favicon.ico">
    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <!-- FontAwesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link rel="stylesheet" href="/static/style.css">
    <style>
        .page-container {
            max-width: 1440px;
            margin: 0 auto;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }
        .nav-strip {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin-bottom: .2rem;
            flex-wrap: wrap;
        }
        .nav-strip a {
            color: var(--text-muted);
            text-decoration: none;
            font-size: .85rem;
            display: flex;
            align-items: center;
            gap: .4rem;
            padding: .4rem .8rem;
            border-radius: 8px;
            background: rgba(255,255,255,.03);
            border: 1px solid rgba(255,255,255,.07);
            transition: all .2s;
        }
        .nav-strip a:hover, .nav-strip a.active {
            color: var(--text-main);
            background: rgba(88,101,242,.2);
            border-color: rgba(88,101,242,.4);
        }
        .nav-strip a.active-green {
            color: #6ee7b7;
            background: rgba(16,185,129,.2);
            border-color: rgba(16,185,129,.4);
        }

        .kabul-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1.5rem;
        }
        @media (max-width: 1024px) {
            .kabul-grid { grid-template-columns: 1fr; }
        }

        .data-table-wrapper {
            overflow-x: auto;
            max-height: 420px;
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.08);
            background: rgba(15, 23, 42, 0.6);
        }
        .kabul-table {
            width: 100%;
            border-collapse: collapse;
            font-size: .85rem;
        }
        .kabul-table th {
            background: rgba(30, 41, 59, 0.9);
            color: var(--text-muted);
            font-weight: 600;
            text-align: left;
            padding: .75rem .9rem;
            border-bottom: 1px solid rgba(255,255,255,0.08);
            position: sticky;
            top: 0;
            z-index: 2;
        }
        .kabul-table td {
            padding: .7rem .9rem;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            color: var(--text-main);
        }
        .kabul-table tbody tr {
            cursor: pointer;
            transition: background .15s;
        }
        .kabul-table tbody tr:hover {
            background: rgba(88,101,242,0.12);
        }
        .kabul-table tbody tr.selected-row {
            background: rgba(16, 185, 129, 0.18) !important;
            border-left: 3px solid #10b981;
        }

        .btn-kabul-big {
            background: linear-gradient(135deg, #10b981, #059669);
            color: #fff;
            font-size: 1.1rem;
            font-weight: 700;
            padding: 0.95rem 1.6rem;
            border-radius: 14px;
            border: none;
            cursor: pointer;
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.7rem;
            box-shadow: 0 6px 20px rgba(16,185,129,0.35);
            transition: all .2s ease;
        }
        .btn-kabul-big:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(16,185,129,0.5);
        }
        .btn-kabul-big:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            box-shadow: none;
            transform: none;
        }

        .badge-type {
            background: rgba(139, 92, 246, 0.2);
            color: #c084fc;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
        }
    </style>
</head>
<body class="sidebar-layout">
    <div class="glass-bg-decor1"></div>
    <div class="glass-bg-decor2"></div>

    <div class="app-wrapper">
        <!-- SOL DİKİNE SIDEBAR NAVİGASYON -->
        <aside class="sidebar">
            <div class="sidebar-brand">
                <i class="fa-solid fa-qrcode logo-icon"></i>
                <div>
                    <h2>QR Compare</h2>
                    <p>Akıllı Stok Sistemi</p>
                </div>
            </div>

            <nav class="sidebar-menu">
                <a href="/cikis" class="sidebar-link">
                    <i class="fa-solid fa-box-open"></i>
                    <span>Barkod Okut & Çıkış</span>
                </a>
                <a href="/cikis-listesi" class="sidebar-link">
                    <i class="fa-solid fa-list-check"></i>
                    <span>Çıkış Listesi</span>
                </a>
                <a href="/stok-esitleme" class="sidebar-link">
                    <i class="fa-solid fa-chart-line"></i>
                    <span>Stok & Eşitleme</span>
                </a>
                <a href="/depo_stoklari" class="sidebar-link">
                    <i class="fa-solid fa-warehouse"></i>
                    <span>Depomdaki Stoklar</span>
                </a>
                <a href="/depo_kabul" class="sidebar-link active">
                    <i class="fa-solid fa-boxes-packing"></i>
                    <span>Depoya Kabul Et</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill">
                    <span class="status-indicator online"></span>
                    <span>Sistem Aktif</span>
                </div>
                <div class="user-profile-card">
                    <div class="user-profile-info">
                        <i class="fa-solid fa-user-circle user-avatar-icon"></i>
                        <span id="sidebar-user-name" class="user-name-title">{{ current_user_name }}</span>
                    </div>
                    <button type="button" class="btn-logout-icon" onclick="logoutUser()" title="Oturumdan Çıkış Yap">
                        <i class="fa-solid fa-power-off"></i>
                    </button>
                </div>
                <div class="version-pill" onclick="showVersionModal()" title="Sürüm Bilgisi">
                    <i class="fa-solid fa-code-branch" style="color: #38bdf8;"></i>
                    <span id="versionText">{{ current_app_version }}</span>
                </div>
            </div>
        </aside>

        <!-- SAĞ ANA İÇERİK ALANI -->
        <main class="main-content">
            <header class="content-header">
                <div>
                    <h1 class="page-title"><i class="fa-solid fa-boxes-packing icon-green"></i> Depoya Kabul Et & Gelen Bildirimler</h1>
                    <p class="page-subtitle">Toptancı ve üreticilerden size kesilen gelen faturaları görüntüleyin ve depoya kabul edin.</p>
                </div>
            </header>

        <!-- ÜÇLÜ PANEL LAYOUT -->
        <div class="kabul-grid">
            
            <!-- PANEL 1: GELEN BİLDİRİMLER (BKST) -->
            <section class="panel glass-card">
                <div class="panel-head-flex">
                    <div class="panel-title-group">
                        <i class="fa-solid fa-inbox icon-primary"></i>
                        <div>
                            <h2>1. Bölüm: Gelen / Bekleyen Bildirimler</h2>
                            <p class="panel-subtitle">Toptancı veya üreticilerden size kesilen gelen irsaliye/faturalar.</p>
                        </div>
                    </div>
                </div>

                <div style="background:rgba(16, 185, 129, 0.1); border:1px solid rgba(16, 185, 129, 0.3); border-radius:10px; padding:0.75rem 1rem; margin-top:0.8rem; font-size:0.82rem; color:#6ee7b7; display:flex; align-items:center; gap:0.6rem;">
                    <i class="fa-solid fa-shield-halved" style="font-size:1.2rem; color:#10b981;"></i>
                    <div>
                        <b>Güvenlik Filtresi Aktif:</b> Sadece tedarikçilerden size kesilen <b>"MAL ALIM" (Gelen)</b> bildirimleri listelenir. Satış veya çıkış bildirimleriniz buraya düşmez.
                    </div>
                </div>

                <div class="bkst-controls margin-top-sm" style="display:flex; gap:0.8rem; flex-wrap:wrap;">
                    <button type="button" id="btn-fetch-incoming" class="btn btn-primary" style="flex:1;">
                        <i class="fa-solid fa-cloud-arrow-down"></i> 📥 1. Gelen Mal Alım Bildirimlerini Çek (BKST)
                    </button>
                </div>

                <div id="incoming-msg-box" class="status-msg hidden margin-top-sm"></div>

                <div class="data-table-wrapper margin-top-md">
                    <table class="kabul-table" id="table-incoming">
                        <thead>
                            <tr>
                                <th style="width:40px;">Seç</th>
                                <th>Gönderici Firma / GLN</th>
                                <th>Belge No</th>
                                <th>Tarih</th>
                                <th>Tip</th>
                                <th>Adet</th>
                                <th>Durum</th>
                            </tr>
                        </thead>
                        <tbody id="tbody-incoming">
                            <tr>
                                <td colspan="7" style="text-align:center; color:var(--text-muted); padding:2.5rem;">
                                    Henüz bildirim çekilmedi. <b>"Gelen Bildirimleri Çek"</b> butonuna basarak BKST üzerindeki faturaları listeleyebilirsiniz.
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </section>

            <!-- PANEL 2: SEÇİLEN BİLDİRİM DETAYLARI & KABUL ET -->
            <section class="panel glass-card">
                <div class="panel-head-flex">
                    <div class="panel-title-group">
                        <i class="fa-solid fa-boxes-packing icon-green"></i>
                        <div>
                            <h2>2. Bölüm: Bildirim Detayı & Depoya Kabul</h2>
                            <p class="panel-subtitle">Seçilen belgedeki karekodlar ve depoya ekleme işlemi.</p>
                        </div>
                    </div>
                </div>

                <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); padding:0.9rem 1.1rem; border-radius:12px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase;">Seçilen Belge No:</span>
                        <h3 id="lbl-selected-doc" style="margin-top:2px; font-family:var(--font-outfit); font-size:1.15rem; color:#38bdf8;">Henüz Belge Seçilmedi</h3>
                    </div>
                    <div style="text-align:right;">
                        <span style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase;">Ürün Sayısı:</span>
                        <h3 id="lbl-selected-count" style="margin-top:2px; font-family:var(--font-outfit); font-size:1.15rem; color:#10b981;">0 Adet</h3>
                    </div>
                </div>

                <!-- TEK TUŞLA DEPOYA KABUL ET BUTONU -->
                <button type="button" id="btn-accept-warehouse" class="btn-kabul-big margin-top-md" disabled>
                    <i class="fa-solid fa-circle-check"></i> 🟢 Tek Tuşla Depoya Kabul Et (MALALIM)
                </button>

                <div id="accept-msg-box" class="status-msg hidden margin-top-sm"></div>

                <!-- DETAY ÜRÜN TABLOSU -->
                <div class="data-table-wrapper margin-top-md" style="max-height: 280px;">
                    <table class="kabul-table" id="table-details">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Karekod</th>
                                <th>Ürün Adı</th>
                                <th>GTIN / Barkod</th>
                                <th>Seri No</th>
                                <th>SKT</th>
                            </tr>
                        </thead>
                        <tbody id="tbody-details">
                            <tr>
                                <td colspan="6" style="text-align:center; color:var(--text-muted); padding:2rem;">
                                    Lütfen sol taraftan detaylarını görmek istediğiniz bir bildirime tıklayın.
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </section>

        </div>
        </main>
    </div>

    <!-- SÜRÜM & GÜNCELLEME MODALI -->
    <div id="versionModal" class="modal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6); backdrop-filter:blur(4px);">
        <div style="background:#1e293b; color:#fff; max-width:450px; margin:10% auto; padding:24px; border-radius:16px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:12px; margin-bottom:16px;">
                <h3 style="margin:0; font-size:1.15rem; color:#38bdf8; display:flex; align-items:center; gap:8px;">
                    <i class="fa-solid fa-circle-info"></i> Uygulama Sürüm Bilgisi
                </h3>
                <span onclick="closeVersionModal()" style="cursor:pointer; font-size:1.4rem; color:#94a3b8;">&times;</span>
            </div>
            <div style="font-size:0.95rem; line-height:1.8;">
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">v3.1.0</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">08.10.2026</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu, otomatik kapanma ve stabilite güncellemeleri</div>
                <div style="margin-top:16px; background:rgba(34, 197, 94, 0.15); border:1px solid rgba(34, 197, 94, 0.3); color:#4ade80; padding:8px 12px; border-radius:8px; text-align:center; font-size:0.85rem; font-weight:600;">
                    🟢 GitHub Sunucusu ile Eşitlendi & Güncel
                </div>
            </div>
            <div style="margin-top:16px; text-align:right;">
                <button onclick="closeVersionModal()" style="background:#3b82f6; color:#fff; border:none; padding:8px 18px; border-radius:8px; font-weight:600; cursor:pointer;">Kapat</button>
            </div>
        </div>
    </div>

    <script>
        let selectedNotificationData = null;

        const btnFetchIncoming = document.getElementById('btn-fetch-incoming');
        const btnAcceptWarehouse = document.getElementById('btn-accept-warehouse');
        const incomingMsgBox = document.getElementById('incoming-msg-box');
        const acceptMsgBox = document.getElementById('accept-msg-box');
        const tbodyIncoming = document.getElementById('tbody-incoming');
        const tbodyDetails = document.getElementById('tbody-details');
        const lblSelectedDoc = document.getElementById('lbl-selected-doc');
        const lblSelectedCount = document.getElementById('lbl-selected-count');

        // Gelen Bildirimleri Çek
        btnFetchIncoming.addEventListener('click', async () => {
            btnFetchIncoming.disabled = true;
            btnFetchIncoming.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> BKST Sunucusuna Bağlanılıyor...';
            showMsg(incomingMsgBox, 'loading', 'Bakanlık gelen bildirim listesi sorgulanıyor, lütfen bekleyin...');

            try {
                const res = await fetch('/api/depo_kabul/gelen_listesi', { method: 'POST' });
                const data = await res.json();

                if (data.success) {
                    showMsg(incomingMsgBox, 'success', data.message || 'Gelen bildirimler başarıyla çekildi.');
                    renderIncomingTable(data.notifications || []);
                } else {
                    showMsg(incomingMsgBox, 'error', data.error || 'Gelen bildirimler çekilemedi.');
                }
            } catch (err) {
                showMsg(incomingMsgBox, 'error', 'Bağlantı hatası: ' + err.message);
            } finally {
                btnFetchIncoming.disabled = false;
                btnFetchIncoming.innerHTML = '<i class="fa-solid fa-cloud-arrow-down"></i> 📥 1. Gelen Bildirimleri Çek (BKST)';
            }
        });

        // Bildirim Tablosunu Bas
        function renderIncomingTable(items) {
            // Güvenlik Önlemi: Satış veya Çıkış türündeki tüm bildirimleri kesin olarak süz
            const validItems = (items || []).filter(item => {
                const op = String(item.OPERATION || item.OperationName || '').toUpperCase();
                return !op.includes('SATIS') && !op.includes('SATIŞ') && !op.includes('CIKIS') && !op.includes('ÇIKIŞ') && !op.includes('GİDEN');
            });

            if (!validItems || validItems.length === 0) {
                tbodyIncoming.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:2rem;">Tipi "MAL ALIM" olan gelen/bekleyen bildirim bulunamadı.</td></tr>';
                return;
            }

            tbodyIncoming.innerHTML = validItems.map((item, index) => `
                <tr onclick="selectNotification(${index}, this)" id="row-incoming-${index}">
                    <td style="text-align:center;"><i class="fa-regular fa-circle radio-icon"></i></td>
                    <td><b>${esc(item.CompanyTitle || item.SENDER || 'Bilinmiyor')}</b></td>
                    <td><span style="font-family:monospace; color:#38bdf8;">${esc(item.WAYBILLNUMBER || '-')}</span></td>
                    <td>${esc(item.WAYBILLDATE || item.OPERATIONDATE || '-')}</td>
                    <td><span class="badge-type" style="background:rgba(16,185,129,0.2); color:#6ee7b7; border:1px solid rgba(16,185,129,0.4); padding:2px 8px; border-radius:6px; font-weight:700;">MAL ALIM</span></td>
                    <td><b>${item.PRODUCTCOUNT || 0}</b></td>
                    <td><span style="color:#10b981;">${esc(item.HEADERSTATE || 'Bekliyor')}</span></td>
                </tr>
            `).join('');

            window.incomingItems = validItems;
        }

        // Satır Seçimi ve Detay Getirme
        window.selectNotification = async function(index, trEl) {
            document.querySelectorAll('#tbody-incoming tr').forEach(r => {
                r.classList.remove('selected-row');
                const ic = r.querySelector('.radio-icon');
                if (ic) ic.className = 'fa-regular fa-circle radio-icon';
            });

            trEl.classList.add('selected-row');
            const icon = trEl.querySelector('.radio-icon');
            if (icon) icon.className = 'fa-solid fa-circle-dot radio-icon';

            const item = window.incomingItems[index];
            selectedNotificationData = item;

            lblSelectedDoc.textContent = item.WAYBILLNUMBER || 'Belge #' + (index+1);
            lblSelectedCount.textContent = (item.PRODUCTCOUNT || 0) + ' Adet';
            btnAcceptWarehouse.disabled = false;

            // Detayları Yükle
            tbodyDetails.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted); padding:2rem;"><i class="fa-solid fa-spinner fa-spin"></i> Ürün detayları getiriliyor...</td></tr>';

            try {
                const res = await fetch('/api/depo_kabul/detay/' + encodeURIComponent(item.HEADERID || item.WAYBILLNUMBER || index));
                const data = await res.json();
                if (data.success && data.products) {
                    renderDetailsTable(data.products);
                } else {
                    renderDetailsTable(item.products || []);
                }
            } catch (err) {
                renderDetailsTable(item.products || []);
            }
        };

        function renderDetailsTable(prods) {
            if (!prods || prods.length === 0) {
                tbodyDetails.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted); padding:1.5rem;">Bu bildirime ait karekod detayı bulunamadı.</td></tr>';
                return;
            }

            tbodyDetails.innerHTML = prods.map((p, i) => `
                <tr>
                    <td>${i+1}</td>
                    <td style="font-family:monospace; font-size:0.78rem; color:#6ee7b7;">${esc(p.Karekod || p.KAREKOD || '-')}</td>
                    <td><b>${esc(p['Ürün Adı'] || p.STOCKNAME || '-')}</b></td>
                    <td>${esc(p['Gtin / Barkod'] || p.BARCODE || '-')}</td>
                    <td>${esc(p['Seri Numarası'] || p.SERIALNUMBER || '-')}</td>
                    <td>${esc(p['Son Kullanma Tarihi'] || p.SKT || '-')}</td>
                </tr>
            `).join('');
        }

        // Tek Tuşla Depoya Kabul Et
        btnAcceptWarehouse.addEventListener('click', async () => {
            if (!selectedNotificationData) return;

            btnAcceptWarehouse.disabled = true;
            btnAcceptWarehouse.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Depoya Kabul Ediliyor (MALALIM)...';
            showMsg(acceptMsgBox, 'loading', 'Mal alım bildirimi yapılıyor ve ürünler deponuza ekleniyor...');

            try {
                const res = await fetch('/api/depo_kabul/onayla', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        header_id: selectedNotificationData.HEADERID,
                        waybill_number: selectedNotificationData.WAYBILLNUMBER,
                        product_count: selectedNotificationData.PRODUCTCOUNT,
                        products: selectedNotificationData.products || []
                    })
                });

                const data = await res.json();
                if (data.success) {
                    showMsg(acceptMsgBox, 'success', data.message || '🟢 Bildirim deponuza başarıyla kabul edildi!');
                    btnAcceptWarehouse.innerHTML = '<i class="fa-solid fa-check-double"></i> 🟢 Depoya Kabul Edildi!';
                    btnAcceptWarehouse.style.background = '#059669';
                } else {
                    showMsg(acceptMsgBox, 'error', data.error || 'Depoya kabul edilemedi.');
                    btnAcceptWarehouse.disabled = false;
                    btnAcceptWarehouse.innerHTML = '<i class="fa-solid fa-circle-check"></i> 🟢 Tek Tuşla Depoya Kabul Et (MALALIM)';
                }
            } catch (err) {
                showMsg(acceptMsgBox, 'error', 'Hata: ' + err.message);
                btnAcceptWarehouse.disabled = false;
                btnAcceptWarehouse.innerHTML = '<i class="fa-solid fa-circle-check"></i> 🟢 Tek Tuşla Depoya Kabul Et (MALALIM)';
            }
        });

        function showMsg(box, type, txt) {
            box.className = 'status-msg ' + (type === 'loading' ? 'msg-loading' : type === 'success' ? 'msg-success' : 'msg-error');
            box.innerHTML = txt;
            box.classList.remove('hidden');
        }

        function esc(str) {
            return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

    </script>
    <script src="/static/app.js?v=20261005_v3000"></script>
</body>
</html>

```

---

### 📁 `templates/kullaniciya_satis.html`

```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Compare</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.png">
    <link rel="shortcut icon" href="/static/favicon.ico">
    <!-- Google Fonts Outfit & Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <!-- FontAwesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link rel="stylesheet" href="/static/style.css">
    <style>
        .satis-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 1.6rem;
            margin-top: 1.2rem;
        }

        .satis-card {
            background: rgba(15, 23, 42, 0.75);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 20px;
            padding: 1.8rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 1.4rem;
            backdrop-filter: blur(16px);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }

        .satis-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.45);
        }

        .card-prescribed:hover { border-color: rgba(56, 189, 248, 0.45); box-shadow: 0 10px 30px rgba(56, 189, 248, 0.2); }
        .card-non-prescribed { border-color: rgba(52, 211, 153, 0.3); background: linear-gradient(145deg, rgba(15, 23, 42, 0.85), rgba(6, 78, 59, 0.25)); }
        .card-non-prescribed:hover { border-color: rgba(52, 211, 153, 0.55); box-shadow: 0 12px 32px rgba(52, 211, 153, 0.25); }
        .card-contracted:hover { border-color: rgba(167, 139, 250, 0.45); box-shadow: 0 10px 30px rgba(167, 139, 250, 0.2); }
        .card-out-of-scope:hover { border-color: rgba(251, 191, 36, 0.45); box-shadow: 0 10px 30px rgba(251, 191, 36, 0.2); }

        .satis-card-header {
            display: flex;
            align-items: center;
            gap: 1.2rem;
        }

        .satis-card-icon {
            width: 60px;
            height: 60px;
            border-radius: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.8rem;
            flex-shrink: 0;
        }

        .icon-blue-glow { background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }
        .icon-green-glow { background: rgba(52, 211, 153, 0.18); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.35); filter: drop-shadow(0 0 10px rgba(52, 211, 153, 0.3)); }
        .icon-purple-glow { background: rgba(167, 139, 250, 0.15); color: #a78bfa; border: 1px solid rgba(167, 139, 250, 0.3); }
        .icon-amber-glow { background: rgba(251, 191, 36, 0.15); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.3); }

        .satis-card-title {
            font-family: var(--font-outfit);
            font-size: 1.25rem;
            font-weight: 800;
            color: #ffffff;
            margin: 0 0 4px 0;
        }

        .satis-card-desc {
            font-size: 0.88rem;
            color: var(--text-muted);
            line-height: 1.5;
            margin: 0;
        }

        .btn-satis-action {
            width: 100%;
            padding: 0.85rem 1.2rem;
            border-radius: 12px;
            font-weight: 700;
            font-size: 0.9rem;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.6rem;
            text-decoration: none;
            cursor: pointer;
            border: none;
            transition: all 0.2s ease;
        }

        .btn-blue { background: linear-gradient(135deg, #0284c7, #38bdf8); color: #fff; box-shadow: 0 4px 14px rgba(2, 132, 199, 0.3); }
        .btn-blue:hover { background: linear-gradient(135deg, #0369a1, #0284c7); transform: translateY(-1px); }

        .btn-green { background: linear-gradient(135deg, #059669, #10b981); color: #fff; box-shadow: 0 4px 16px rgba(16, 185, 129, 0.35); }
        .btn-green:hover { background: linear-gradient(135deg, #047857, #059669); transform: translateY(-1px); }

        .btn-purple { background: linear-gradient(135deg, #7c3aed, #a78bfa); color: #fff; box-shadow: 0 4px 14px rgba(124, 58, 237, 0.3); }
        .btn-purple:hover { background: linear-gradient(135deg, #6d28d9, #7c3aed); transform: translateY(-1px); }

        .btn-amber { background: linear-gradient(135deg, #d97706, #fbbf24); color: #fff; box-shadow: 0 4px 14px rgba(217, 119, 6, 0.3); }
        .btn-amber:hover { background: linear-gradient(135deg, #b45309, #d97706); transform: translateY(-1px); }

        .bkst-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.76rem;
            color: #cbd5e1;
            font-weight: 600;
        }

        .modal-input-field {
            width: 100%;
            padding: 0.75rem 1rem;
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 10px;
            color: #fff;
            font-family: var(--font-inter);
            font-size: 0.92rem;
            outline: none;
            transition: border-color 0.2s;
        }

        .modal-input-field:focus {
            border-color: #10b981;
            box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.25);
        }

        .step-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 26px;
            height: 26px;
            border-radius: 50%;
            background: rgba(255,255,255,0.1);
            color: #94a3b8;
            font-weight: 800;
            font-size: 0.8rem;
        }

        .step-pill.active {
            background: #10b981;
            color: #fff;
            box-shadow: 0 0 10px rgba(16, 185, 129, 0.4);
        }
    </style>
</head>
<body class="sidebar-layout">
    <div class="glass-bg-decor1"></div>
    <div class="glass-bg-decor2"></div>

    <div class="app-wrapper">
        <!-- SOL DİKİNE SIDEBAR NAVİGASYON -->
        <aside class="sidebar">
            <div class="sidebar-brand">
                <i class="fa-solid fa-qrcode logo-icon"></i>
                <div>
                    <h2>QR Compare</h2>
                    <p>Akıllı Stok Sistemi</p>
                </div>
            </div>

            <nav class="sidebar-menu">
                <a href="/cikis" class="sidebar-link">
                    <i class="fa-solid fa-box-open"></i>
                    <span>Barkod Okut & Çıkış</span>
                </a>
                <a href="/cikis-listesi" class="sidebar-link">
                    <i class="fa-solid fa-list-check"></i>
                    <span>Çıkış Listesi</span>
                </a>
                <a href="/stok-esitleme" class="sidebar-link">
                    <i class="fa-solid fa-chart-line"></i>
                    <span>Stok & Eşitleme</span>
                </a>
                <a href="/depo_stoklari" class="sidebar-link">
                    <i class="fa-solid fa-warehouse"></i>
                    <span>Depomdaki Stoklar</span>
                </a>
                <a href="/depo_kabul" class="sidebar-link">
                    <i class="fa-solid fa-boxes-packing"></i>
                    <span>Depoya Kabul Et</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link active">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill">
                    <span class="status-indicator online"></span>
                    <span>Sistem Aktif</span>
                </div>
                <div class="user-profile-card">
                    <div class="user-profile-info">
                        <i class="fa-solid fa-user-circle user-avatar-icon"></i>
                        <span id="sidebar-user-name" class="user-name-title">{{ current_user_name }}</span>
                    </div>
                    <button type="button" class="btn-logout-icon" onclick="logoutUser()" title="Oturumdan Çıkış Yap">
                        <i class="fa-solid fa-power-off"></i>
                    </button>
                </div>
                <div class="version-pill" onclick="showVersionModal()" title="Sürüm Bilgisi">
                    <i class="fa-solid fa-code-branch" style="color: #38bdf8;"></i>
                    <span id="versionText">{{ current_app_version }}</span>
                </div>
            </div>
        </aside>

        <!-- SAĞ ANA İÇERİK ALANI -->
        <main class="main-content">
            <!-- HEADER BAR -->
            <header class="content-header">
                <div>
                    <h1 class="page-title"><i class="fa-solid fa-user-tag icon-blue"></i> Kullanıcıya Satış İşlemleri</h1>
                    <p class="page-subtitle">Bakanlık BKST sistemi üzerinden Reçeteli, Reçetesiz, Taahhütlü ve Kapsam Dışı ürün satış bildirimleri.</p>
                </div>
                <div class="header-actions">
                    <a href="https://bkst.tarbil.gov.tr/Main/SellToProducer" target="_blank" class="btn btn-outline">
                        <i class="fa-solid fa-arrow-up-right-from-square"></i> BKST Satış Paneline Git
                    </a>
                </div>
            </header>

            <!-- SATIŞ TÜRÜ SEÇİM KARTLARI -->
            <section class="panel glass-card">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem; margin-bottom:0.8rem;">
                    <div>
                        <h2 style="font-family:var(--font-outfit); font-size:1.25rem; font-weight:700; color:#fff; margin:0;">
                            <i class="fa-solid fa-layer-group color-blue"></i> Satış Bildirim Türünü Seçin
                        </h2>
                        <p style="font-size:0.85rem; color:var(--text-muted); margin:4px 0 0 0;">
                            Aşağıdaki butonları kullanarak Reçetesiz Satış bildirimini SMS doğrulaması dahil uygulama içinden API ile yapabilirsiniz.
                        </p>
                    </div>
                </div>

                <div class="satis-grid">
                    <!-- 1. Reçeteli Satış -->
                    <div class="satis-card card-prescribed">
                        <div class="satis-card-header">
                            <div class="satis-card-icon icon-blue-glow">
                                <i class="fa-solid fa-file-prescription"></i>
                            </div>
                            <div>
                                <h3 class="satis-card-title">Reçeteli Satış</h3>
                                <span class="bkst-badge"><i class="fa-solid fa-shield-halved"></i> BKST Reçete Modülü</span>
                            </div>
                        </div>
                        <p class="satis-card-desc">
                            Reçeteye bağlı bitki koruma ürünleri ve ilaçların üreticiye / kullanıcıya reçeteli satış bildirimini gerçekleştirin.
                        </p>
                        <button class="btn-satis-action btn-blue" onclick="openBkstPage('SellToProducerPrescribed')">
                            <i class="fa-solid fa-arrow-right-to-bracket"></i> BKST Reçeteli Satış Ekranını Aç
                        </button>
                    </div>

                    <!-- 2. Reçetesiz Satış (SMS & API ENTEGRELİ) -->
                    <div class="satis-card card-non-prescribed">
                        <div class="satis-card-header">
                            <div class="satis-card-icon icon-green-glow">
                                <i class="fa-solid fa-cart-shopping"></i>
                            </div>
                            <div>
                                <h3 class="satis-card-title">Reçetesiz Satış</h3>
                                <span class="bkst-badge" style="background:rgba(16, 185, 129, 0.2); color:#6ee7b7; border-color:rgba(16, 185, 129, 0.4);">
                                    <i class="fa-solid fa-mobile-screen-button"></i> 📲 SMS + API Destekli
                                </span>
                            </div>
                        </div>
                        <p class="satis-card-desc">
                            Müşteri telefonuna <b>SMS doğrulama kodu göndererek</b> Reçetesiz Satış bildirimini uygulama içinden doğrudan tamamlayın.
                        </p>
                        <div style="display:flex; flex-direction:column; gap:0.6rem;">
                            <button class="btn-satis-action btn-green" onclick="openRecetesizModal()">
                                <i class="fa-solid fa-mobile-screen-button"></i> 📲 SMS Doğrulamalı Satış Yap (API)
                            </button>
                            <button onclick="openBkstPage('SellToProducerNonPrescribed')" style="background:transparent; color:#94a3b8; border:none; font-size:0.8rem; cursor:pointer; text-decoration:underline;">
                                🌐 Bakanlık BKST Sayfasında Aç
                            </button>
                        </div>
                    </div>

                    <!-- 3. Taahhütlü Satış -->
                    <div class="satis-card card-contracted">
                        <div class="satis-card-header">
                            <div class="satis-card-icon icon-purple-glow">
                                <i class="fa-solid fa-file-signature"></i>
                            </div>
                            <div>
                                <h3 class="satis-card-title">Taahhütlü Satış</h3>
                                <span class="bkst-badge"><i class="fa-solid fa-file-contract"></i> Taahhüt Sözleşmeli</span>
                            </div>
                        </div>
                        <p class="satis-card-desc">
                            Taahhüt belgesi veya özel protokoller kapsamında üreticiye gerçekleştirilen ürün satış bildirimlerini yapın.
                        </p>
                        <button class="btn-satis-action btn-purple" onclick="openBkstPage('SellToProducerContracted')">
                            <i class="fa-solid fa-arrow-right-to-bracket"></i> BKST Taahhütlü Satış Ekranını Aç
                        </button>
                    </div>

                    <!-- 4. Kapsam Dışı Satış -->
                    <div class="satis-card card-out-of-scope">
                        <div class="satis-card-header">
                            <div class="satis-card-icon icon-amber-glow">
                                <i class="fa-solid fa-box-archive"></i>
                            </div>
                            <div>
                                <h3 class="satis-card-title">Kapsam Dışı Satış</h3>
                                <span class="bkst-badge"><i class="fa-solid fa-circle-exclamation"></i> Kapsam Dışı</span>
                            </div>
                        </div>
                        <p class="satis-card-desc">
                            BKST takip sistemi standart kapsamı dışında kalan istisnai veya özel ürün satış bildirimi işlemlerini yürütün.
                        </p>
                        <button class="btn-satis-action btn-amber" onclick="openBkstPage('SellToProducerOutOfScope')">
                            <i class="fa-solid fa-arrow-right-to-bracket"></i> BKST Kapsam Dışı Satış Ekranını Aç
                        </button>
                    </div>
                </div>
            </section>
        </main>
    </div>

    <!-- ⚡ REÇETESİZ SATIŞ (SMS + API) MODALI -->
    <div id="recetesizSatisModal" class="modal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.85); backdrop-filter:blur(8px);">
        <div style="background:#1e293b; color:#fff; max-width:720px; width:92%; margin:3% auto; padding:28px; border-radius:24px; border:1px solid rgba(52,211,153,0.35); box-shadow:0 25px 50px rgba(0,0,0,0.85); max-height:92vh; display:flex; flex-direction:column;">
            
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:14px; margin-bottom:16px;">
                <h3 style="margin:0; font-size:1.3rem; color:#34d399; display:flex; align-items:center; gap:10px;">
                    <i class="fa-solid fa-cart-shopping"></i> Reçetesiz Satış Bildirimi (SMS + API)
                </h3>
                <span onclick="closeRecetesizModal()" style="cursor:pointer; font-size:1.6rem; color:#94a3b8; line-height:1;">&times;</span>
            </div>

            <!-- ADIM GÖSTERGESİ -->
            <div style="display:flex; align-items:center; gap:1.5rem; background:rgba(15,23,42,0.6); padding:10px 16px; border-radius:12px; margin-bottom:1.2rem; border:1px solid rgba(255,255,255,0.06);">
                <div style="display:flex; align-items:center; gap:8px;">
                    <span id="step-pill-1" class="step-pill active">1</span>
                    <span style="font-size:0.85rem; font-weight:600; color:#fff;">T.C. & SMS İste</span>
                </div>
                <i class="fa-solid fa-chevron-right" style="color:var(--text-muted); font-size:0.75rem;"></i>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span id="step-pill-2" class="step-pill">2</span>
                    <span style="font-size:0.85rem; font-weight:600; color:var(--text-muted);" id="lbl-step-2">SMS Kodu Doğrula</span>
                </div>
                <i class="fa-solid fa-chevron-right" style="color:var(--text-muted); font-size:0.75rem;"></i>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span id="step-pill-3" class="step-pill">3</span>
                    <span style="font-size:0.85rem; font-weight:600; color:var(--text-muted);" id="lbl-step-3">Ürün QR Okut & Gönder</span>
                </div>
            </div>

            <!-- FORM GİRDİLERİ -->
            <div style="display:flex; flex-direction:column; gap:1rem; margin-bottom:1rem;">
                
                <!-- ADIM 1: T.C. Kimlik & SMS Gönderme -->
                <div style="display:grid; grid-template-columns: 1fr auto; gap:0.8rem; align-items:end;">
                    <div>
                        <label style="font-size:0.85rem; font-weight:700; color:#cbd5e1; display:block; margin-bottom:6px;">
                            <i class="fa-solid fa-id-card" style="color:#34d399;"></i> Müşteri T.C. / Vergi No <span style="color:#ef4444;">*</span>
                        </label>
                        <input type="text" id="api-tc-no" class="modal-input-field" placeholder="Müşteri T.C. veya Vergi No girin..." autocomplete="off">
                    </div>
                    <button id="btn-send-sms" onclick="sendSmsCode()" class="btn btn-primary" style="height:43px; padding:0 20px; font-size:0.88rem; font-weight:700; white-space:nowrap;">
                        <i class="fa-solid fa-paper-plane"></i> 📲 SMS Kodu Gönder
                    </button>
                </div>

                <!-- BİLDİRİM BANNER'I -->
                <div id="sms-status-banner" style="display:none; padding:10px 14px; border-radius:10px; font-size:0.88rem; font-weight:600;"></div>

                <!-- ADIM 2: SMS Kodu Girme (Varsayılan Gizli) -->
                <div id="step-sms-verify-box" style="display:none; background:rgba(52, 211, 153, 0.08); border:1px solid rgba(52, 211, 153, 0.25); padding:14px; border-radius:14px;">
                    <div style="display:grid; grid-template-columns: 1fr auto; gap:0.8rem; align-items:end;">
                        <div>
                            <label style="font-size:0.85rem; font-weight:700; color:#34d399; display:block; margin-bottom:6px;">
                                <i class="fa-solid fa-key"></i> Telefondaki 6 Haneli SMS Doğrulama Kodu <span style="color:#ef4444;">*</span>
                            </label>
                            <input type="text" id="api-sms-code" class="modal-input-field" placeholder="SMS kodunu buraya yazın..." autocomplete="off" style="border-color:rgba(52,211,153,0.4);">
                        </div>
                        <button id="btn-verify-sms" onclick="verifySmsCode()" class="btn btn-green" style="height:43px; padding:0 22px; font-size:0.88rem; font-weight:700;">
                            <i class="fa-solid fa-circle-check"></i> ✅ Kodu Doğrula
                        </button>
                    </div>
                </div>

                <!-- ADIM 3: Barkod Okutma & Opsiyonel Belge No (Kilitli/Açılabilir) -->
                <div id="step-products-box" style="display:none; flex-direction:column; gap:1rem;">
                    <div style="display:grid; grid-template-columns: 1fr; gap:0.8rem;">
                        <div>
                            <label style="font-size:0.85rem; font-weight:700; color:#cbd5e1; display:block; margin-bottom:6px;">
                                <i class="fa-solid fa-file-invoice" style="color:#38bdf8;"></i> Fatura / Belge No (Opsiyonel)
                            </label>
                            <input type="text" id="api-belge-no" class="modal-input-field" placeholder="İrsaliye / Belge No (Opsiyonel)" autocomplete="off">
                        </div>
                    </div>

                    <div>
                        <label style="font-size:0.85rem; font-weight:700; color:#cbd5e1; display:block; margin-bottom:6px;">
                            <i class="fa-solid fa-barcode" style="color:#fbbf24;"></i> Satılacak İlaç QR Okutun (Enter'a basın)
                        </label>
                        <input type="text" id="api-barcode-input" class="modal-input-field" placeholder="Barkod veya Karekod okutun..." autocomplete="off">
                    </div>

                    <!-- OKUTULAN ÜRÜNLER TABLOSU -->
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px;">
                        <span style="font-size:0.88rem; font-weight:700; color:#fff;">
                            <i class="fa-solid fa-list-check color-blue"></i> Satılacak Ürün Listesi
                        </span>
                        <span id="api-item-count-badge" class="badge" style="background:rgba(52,211,153,0.18); color:#34d399; border:1px solid rgba(52,211,153,0.3); padding:4px 12px; border-radius:8px; font-weight:700;">
                            0 Adet Ürün
                        </span>
                    </div>

                    <div class="data-table-wrapper" style="max-height: 180px; overflow-y: auto; border-radius:12px; border:1px solid rgba(255,255,255,0.08);">
                        <table class="kabul-table" style="width:100%; border-collapse:collapse; font-size:0.84rem;">
                            <thead style="position:sticky; top:0; background:#0f172a; z-index:5;">
                                <tr>
                                    <th style="width:40px; text-align:center;">#</th>
                                    <th>Karekod</th>
                                    <th style="text-align:center; width:60px;">İşlem</th>
                                </tr>
                            </thead>
                            <tbody id="api-scanned-tbody">
                                <tr>
                                    <td colspan="3" style="text-align:center; color:var(--text-muted); padding:1.5rem;">
                                        Henüz karekod okutulmadı. Yukarıdaki kutuya okutun.
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>

            <div style="display:flex; justify-content:space-between; align-items:center; gap:1rem; margin-top:auto;">
                <button onclick="closeRecetesizModal()" class="btn btn-outline" style="padding:10px 20px;">Kapat</button>
                <button id="btn-submit-api-satis" onclick="submitRecetesizSatisApi()" class="btn btn-green" style="display:none; padding:10px 28px; font-size:0.95rem;">
                    <i class="fa-solid fa-paper-plane"></i> ⚡ BKST'ye Bildirimi Tamamla (API)
                </button>
            </div>
        </div>
    </div>

    <!-- SÜRÜM & GÜNCELLEME MODALI -->
    <div id="versionModal" class="modal" style="display:none; position:fixed; z-index:9999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.6); backdrop-filter:blur(4px);">
        <div style="background:#1e293b; color:#fff; max-width:450px; margin:10% auto; padding:24px; border-radius:16px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:12px; margin-bottom:16px;">
                <h3 style="margin:0; font-size:1.15rem; color:#38bdf8; display:flex; align-items:center; gap:8px;">
                    <i class="fa-solid fa-circle-info"></i> Uygulama Sürüm Bilgisi
                </h3>
                <span onclick="closeVersionModal()" style="cursor:pointer; font-size:1.4rem; color:#94a3b8;">&times;</span>
            </div>
            <div style="font-size:0.95rem; line-height:1.8;">
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">v3.1.0</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">08.10.2026</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">v3.1.0: Tam ekran masaüstü modu, SQLite DB entegrasyonu, otomatik kapanma ve stabilite güncellemeleri</div>
                <div style="margin-top:16px; background:rgba(34, 197, 94, 0.15); border:1px solid rgba(34, 197, 94, 0.3); color:#4ade80; padding:8px 12px; border-radius:8px; text-align:center; font-size:0.85rem; font-weight:600;">
                    🟢 GitHub Sunucusu ile Eşitlendi & Güncel
                </div>
            </div>
            <div style="margin-top:16px; text-align:right;">
                <button onclick="closeVersionModal()" style="background:#3b82f6; color:#fff; border:none; padding:8px 18px; border-radius:8px; font-weight:600; cursor:pointer;">Kapat</button>
            </div>
        </div>
    </div>

    <script>
        let apiScannedQRs = [];
        let currentVerificationToken = '';
        let isSmsVerified = false;

        function openBkstPage(targetAction) {
            const baseUrl = 'https://bkst.tarbil.gov.tr/Main/';
            window.open(baseUrl + targetAction, '_blank');
        }

        function openRecetesizModal() {
            document.getElementById('recetesizSatisModal').style.display = 'block';
            setTimeout(() => {
                const inp = document.getElementById('api-tc-no');
                if (inp) inp.focus();
            }, 200);
        }

        function closeRecetesizModal() {
            document.getElementById('recetesizSatisModal').style.display = 'none';
        }

        // ADIM 1: SMS KODU İSTE
        async function sendSmsCode() {
            const tcNo = document.getElementById('api-tc-no').value.trim();
            const btnSms = document.getElementById('btn-send-sms');
            const banner = document.getElementById('sms-status-banner');

            if (!tcNo) {
                alert('⚠️ Lütfen Müşteri T.C. veya Vergi No giriniz!');
                document.getElementById('api-tc-no').focus();
                return;
            }

            btnSms.disabled = true;
            btnSms.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Gönderiliyor...';
            banner.style.display = 'none';

            try {
                const res = await fetch('/api/bkst/recetesiz_satis/sms_gonder', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tc_no: tcNo })
                });

                const data = await res.json();
                if (data.success) {
                    currentVerificationToken = data.verification_token;
                    banner.style.display = 'block';
                    banner.style.background = 'rgba(56, 189, 248, 0.15)';
                    banner.style.border = '1px solid rgba(56, 189, 248, 0.35)';
                    banner.style.color = '#38bdf8';
                    banner.innerHTML = `<i class="fa-solid fa-mobile-screen"></i> ${esc(data.message)}`;

                    document.getElementById('step-sms-verify-box').style.display = 'block';
                    document.getElementById('step-pill-2').classList.add('active');
                    document.getElementById('lbl-step-2').style.color = '#fff';

                    setTimeout(() => {
                        document.getElementById('api-sms-code').focus();
                    }, 200);
                } else {
                    banner.style.display = 'block';
                    banner.style.background = 'rgba(239, 68, 68, 0.15)';
                    banner.style.border = '1px solid rgba(239, 68, 68, 0.35)';
                    banner.style.color = '#f87171';
                    banner.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Hata: ${esc(data.error)}`;
                }
            } catch (err) {
                alert('❌ Bağlantı hatası: ' + err.message);
            } finally {
                btnSms.disabled = false;
                btnSms.innerHTML = '<i class="fa-solid fa-paper-plane"></i> 📲 SMS Kodu Gönder';
            }
        }

        // ADIM 2: SMS KODUNU DOĞRULA
        async function verifySmsCode() {
            const tcNo = document.getElementById('api-tc-no').value.trim();
            const smsCode = document.getElementById('api-sms-code').value.trim();
            const btnVerify = document.getElementById('btn-verify-sms');
            const banner = document.getElementById('sms-status-banner');

            if (!smsCode) {
                alert('⚠️ Lütfen telefona gelen 6 haneli SMS kodunu giriniz!');
                document.getElementById('api-sms-code').focus();
                return;
            }

            btnVerify.disabled = true;
            btnVerify.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Doğrulanıyor...';

            try {
                const res = await fetch('/api/bkst/recetesiz_satis/sms_dogrula', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        tc_no: tcNo,
                        sms_code: smsCode,
                        verification_token: currentVerificationToken
                    })
                });

                const data = await res.json();
                if (data.success) {
                    isSmsVerified = true;
                    if (data.verified_token) {
                        currentVerificationToken = data.verified_token;
                    }

                    banner.style.display = 'block';
                    banner.style.background = 'rgba(34, 197, 94, 0.18)';
                    banner.style.border = '1px solid rgba(34, 197, 94, 0.4)';
                    banner.style.color = '#4ade80';
                    banner.innerHTML = `<i class="fa-solid fa-circle-check"></i> ${esc(data.message)}`;

                    document.getElementById('api-tc-no').disabled = true;
                    document.getElementById('api-sms-code').disabled = true;
                    btnVerify.disabled = true;
                    document.getElementById('btn-send-sms').disabled = true;

                    // Step 3 unlock
                    document.getElementById('step-products-box').style.display = 'flex';
                    document.getElementById('btn-submit-api-satis').style.display = 'inline-flex';
                    document.getElementById('step-pill-3').classList.add('active');
                    document.getElementById('lbl-step-3').style.color = '#fff';

                    setTimeout(() => {
                        document.getElementById('api-barcode-input').focus();
                    }, 200);
                } else {
                    banner.style.display = 'block';
                    banner.style.background = 'rgba(239, 68, 68, 0.15)';
                    banner.style.border = '1px solid rgba(239, 68, 68, 0.35)';
                    banner.style.color = '#f87171';
                    banner.innerHTML = `<i class="fa-solid fa-circle-xmark"></i> ${esc(data.error)}`;
                }
            } catch (err) {
                alert('❌ Bağlantı hatası: ' + err.message);
            } finally {
                if (!isSmsVerified) {
                    btnVerify.disabled = false;
                    btnVerify.innerHTML = '<i class="fa-solid fa-circle-check"></i> ✅ Kodu Doğrula';
                }
            }
        }

        // Barkod okutma listener
        document.getElementById('api-barcode-input').addEventListener('keydown', function(e) {
            if (e.key === 'Enter' || e.keyCode === 13) {
                e.preventDefault();
                const code = this.value.trim();
                this.value = '';
                if (code) {
                    if (apiScannedQRs.includes(code)) {
                        alert('⚠️ Bu karekod zaten listeye eklenmiş!');
                        return;
                    }
                    apiScannedQRs.push(code);
                    renderApiScannedTable();
                }
            }
        });

        function removeApiItem(idx) {
            apiScannedQRs.splice(idx, 1);
            renderApiScannedTable();
        }

        function renderApiScannedTable() {
            const tbody = document.getElementById('api-scanned-tbody');
            const badge = document.getElementById('api-item-count-badge');
            badge.textContent = apiScannedQRs.length + ' Adet Ürün';

            if (apiScannedQRs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:1.5rem;">Henüz karekod okutulmadı. Yukarıdaki kutuya okutun.</td></tr>';
                return;
            }

            tbody.innerHTML = apiScannedQRs.map((qr, i) => `
                <tr>
                    <td style="text-align:center; font-weight:700; color:#64748b;">${i + 1}</td>
                    <td style="font-family:monospace; color:#38bdf8; font-size:0.82rem;">${esc(qr)}</td>
                    <td style="text-align:center;">
                        <button onclick="removeApiItem(${i})" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.4); padding:3px 8px; border-radius:6px; cursor:pointer;">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </td>
                </tr>
            `).join('');
        }

        // ADIM 3: SATIŞ BİLDİRİMİNİ TAMAMLA
        async function submitRecetesizSatisApi() {
            const tcNo = document.getElementById('api-tc-no').value.trim();
            const belgeNo = document.getElementById('api-belge-no').value.trim();
            const btn = document.getElementById('btn-submit-api-satis');

            if (!isSmsVerified || !currentVerificationToken) {
                alert('⚠️ Lütfen önce SMS doğrulama adımı tamamlayın!');
                return;
            }

            if (apiScannedQRs.length === 0) {
                alert('⚠️ Lütfen en az 1 adet satılacak ürün / karekod okutun!');
                document.getElementById('api-barcode-input').focus();
                return;
            }

            btn.disabled = true;
            const originalHTML = btn.innerHTML;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> BKST Sunucusuna Bildiriliyor...';

            try {
                const res = await fetch('/api/bkst/recetesiz_satis', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        tc_no: tcNo,
                        verification_token: currentVerificationToken,
                        belge_no: belgeNo,
                        karekods: apiScannedQRs
                    })
                });

                const data = await res.json();
                if (data.success) {
                    alert(data.message || '🟢 Reçetesiz Satış bildirimi başarıyla tamamlandı!');
                    apiScannedQRs = [];
                    renderApiScannedTable();
                    closeRecetesizModal();
                    location.reload();
                } else {
                    alert(data.error || '❌ Satış bildirimi yapılırken hata oluştu.');
                }
            } catch (err) {
                alert('❌ Bağlantı hatası: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
            }
        }

        function esc(str) {
            return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

    </script>
    <script src="/static/app.js?v=20261005_v3000"></script>
</body>
</html>

```

---

### 📁 `templates/login.html`

```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QR Compare</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.png">
    <link rel="shortcut icon" href="/static/favicon.ico">
    <!-- Google Fonts Outfit & Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <!-- FontAwesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link rel="stylesheet" href="/static/style.css">
    <style>
        body {
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            background-color: var(--bg-dark);
            overflow: hidden;
        }

        .login-card-wrapper {
            width: 100%;
            max-width: 440px;
            padding: 2.5rem;
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid rgba(56, 189, 248, 0.25);
            border-radius: 24px;
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(20px);
            position: relative;
            z-index: 10;
        }

        .login-brand {
            text-align: center;
            margin-bottom: 2rem;
        }

        .login-brand i {
            font-size: 3rem;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
            filter: drop-shadow(0 0 12px rgba(56, 189, 248, 0.4));
        }

        .login-brand h1 {
            font-family: var(--font-outfit);
            font-size: 1.8rem;
            font-weight: 800;
            color: #ffffff;
            margin: 0;
        }

        .login-brand p {
            font-size: 0.88rem;
            color: var(--text-muted);
            margin-top: 6px;
        }

        .form-group {
            margin-bottom: 1.3rem;
        }

        .form-label {
            display: block;
            font-size: 0.85rem;
            font-weight: 700;
            color: #cbd5e1;
            margin-bottom: 8px;
        }

        .form-input {
            width: 100%;
            padding: 0.85rem 1.1rem;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 12px;
            color: #ffffff;
            font-family: var(--font-inter);
            font-size: 0.95rem;
            outline: none;
            transition: all 0.2s ease;
        }

        .form-input:focus {
            border-color: #38bdf8;
            box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.25);
            background: rgba(255, 255, 255, 0.08);
        }

        .btn-login {
            width: 100%;
            padding: 0.95rem;
            background: linear-gradient(135deg, #2563eb, #3b82f6);
            color: #ffffff;
            font-family: var(--font-outfit);
            font-size: 1rem;
            font-weight: 700;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            box-shadow: 0 6px 20px rgba(37, 99, 235, 0.35);
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.6rem;
            margin-top: 1.8rem;
        }

        .btn-login:hover {
            background: linear-gradient(135deg, #1d4ed8, #2563eb);
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(37, 99, 235, 0.45);
        }

        .login-alert {
            display: none;
            padding: 10px 14px;
            border-radius: 10px;
            font-size: 0.85rem;
            font-weight: 600;
            margin-bottom: 1.2rem;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="glass-bg-decor1"></div>
    <div class="glass-bg-decor2"></div>

    <div class="login-card-wrapper">
        <div class="login-brand">
            <i class="fa-solid fa-qrcode"></i>
            <h1>QR Compare</h1>
            <p>Bakanlık BKST Giriş Paneli</p>
        </div>

        <div id="loginAlert" class="login-alert"></div>

        <form id="loginForm" onsubmit="handleLoginSubmit(event)">
            <div class="form-group">
                <label class="form-label" for="username">
                    <i class="fa-solid fa-user" style="color:#38bdf8;"></i> Bakanlık T.C. / Kullanıcı Adı
                </label>
                <input type="text" id="username" class="form-input" placeholder="T.C. Kimlik Numaranızı girin..." required autocomplete="off">
            </div>

            <div class="form-group">
                <label class="form-label" for="password">
                    <i class="fa-solid fa-lock" style="color:#34d399;"></i> Bakanlık Şifresi
                </label>
                <input type="password" id="password" class="form-input" placeholder="BKST şifrenizi girin..." required autocomplete="off">
            </div>

            <button type="submit" id="btnLoginSubmit" class="btn-login">
                <i class="fa-solid fa-right-to-bracket"></i> Giriş Yap ve Kaydet
            </button>
        </form>
    </div>

    <script>
        async function handleLoginSubmit(e) {
            e.preventDefault();
            const username = document.getElementById('username').value.trim();
            const password = document.getElementById('password').value.trim();
            const btn = document.getElementById('btnLoginSubmit');
            const alertBox = document.getElementById('loginAlert');

            if (!username || !password) {
                showAlert('Lütfen Kullanıcı Adı ve Şifre alanlarını doldurun.', true);
                return;
            }

            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Doğrulanıyor...';
            alertBox.style.display = 'none';

            try {
                const res = await fetch('/api/system/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                const data = await res.json();

                if (data.success) {
                    sessionStorage.clear();
                    showAlert('🟢 Giriş başarılı! Yönlendiriliyorsunuz...', false);
                    setTimeout(() => {
                        window.location.href = '/cikis?force_sync=1';
                    }, 800);
                } else {
                    showAlert('❌ ' + (data.error || 'Giriş yapılamadı. Bilgilerinizi kontrol edin.'), true);
                }
            } catch (err) {
                showAlert('❌ Bağlantı hatası: ' + err.message, true);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Giriş Yap ve Kaydet';
            }
        }

        function showAlert(msg, isError) {
            const alertBox = document.getElementById('loginAlert');
            alertBox.style.display = 'block';
            if (isError) {
                alertBox.style.background = 'rgba(239, 68, 68, 0.15)';
                alertBox.style.border = '1px solid rgba(239, 68, 68, 0.35)';
                alertBox.style.color = '#f87171';
            } else {
                alertBox.style.background = 'rgba(34, 197, 94, 0.15)';
                alertBox.style.border = '1px solid rgba(34, 197, 94, 0.35)';
                alertBox.style.color = '#4ade80';
            }
            alertBox.innerHTML = msg;
        }

        // Heartbeat (Sunucunun login ekranındayken kapanmasını önler)
        (function startHeartbeat() {
            function sendPing() {
                fetch('/api/system/heartbeat', { method: 'POST' }).catch(() => {});
            }
            sendPing();
            setInterval(sendPing, 3000);
        })();
    </script>
</body>
</html>

```

---

