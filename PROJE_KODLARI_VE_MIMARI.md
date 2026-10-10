# QR COMPARE STOK & KAREKOD YÖNETİM SİSTEMİ - TÜM PROJE KODLARI VE MİMARİSİ

> **Sürüm:** v3.2.3 (Geliştirici Modu - BKST Reçetesiz Satış Tarla / Parsel Yönetimi, ÇKS/TÜKAS/KOBUKS Desteği, Aynı Bitki/Aynı İl Kuralı Doğrulaması, Cascading Zararlı/BKU Zinciri, Hata Öngörme & Uyarı Alanları)  
> **Konum:** `c:\Users\fatih\Desktop\asım iş`

---

## 1. Mimari Genel Bakış ve Teknik Özellikler

QR Compare, Bitki Koruma Ürünleri (BKST) stok takibi, karekod eşitleme ve raf/terek sayımı yapmak üzere tasarlanmış modern, yüksek performanslı, çevrimdışı (offline-first) destekli ve tam sertleştirilmiş bir Flask web uygulamasıdır.

### 🌟 Ana Mimari İlkeler ve Güvenlik Tasarımı (v3.2.0):

1. **SQLite Kalıcılığı & WAL Modu (Excel Bağımlılığı Sıfır)**:
   - Tüm iç veri saklama katmanı `cikis_kayitlari.db` SQLite veritabanı üzerinden WAL (`PRAGMA journal_mode=WAL`) ve `synchronous=NORMAL` modunda yürütülür (`bkst_depo_verileri`, `cikis_kayitlari`, `satis_arsivi` tabloları).
   - Excel dosyaları (`bkst_depo_verileri.xlsx`) **kesinlikle dahili depolama olarak kullanılmaz**. Excel indirmeleri ve raporlamaları Flask üzerinden dinamik `io.BytesIO()` bellek akışları ile anlık üretilir.

2. **Waitress Production WSGI Sunucusu**:
   - Werkzeug geliştirme sunucusu yerine çok iş parçacıklı `Waitress` WSGI sunucusu entegre edilmiştir (`threads=32, connection_limit=200, channel_timeout=180, cleanup_interval=30`).
   - Eşzamanlı isteklerde ve büyük veri indirmelerinde bağlantı kopmaları ve sunucu kilitlenmeleri kalıcı olarak engellenmiştir.

3. **Asenkron BKST Worker ve Kesintisiz Arayüz (Non-Blocking)**:
   - Bakanlık veri çekme işlemi (`_do_fetch_api_worker`) arka planda `ThreadPoolExecutor` iş parçacığında asenkron çalışır.
   - Uygulama kapanırken arkadaki iş parçacıklarının temiz kapanabilmesi için `atexit.register(lambda: _fetch_executor.shutdown(wait=False))` koruması mevcuttur.
   - `POST /api/bkst/fetch_api` hemen döner, arayüz `GET /api/bkst/fetch_status` üzerinden durumu sorgular.

4. **Akıllı "Sistem Deaktif" & Güvenli Çevrimdışı (Offline-First) Koruma**:
   - Bakanlığa bağlanılamadığında veya Bakanlık'tan 0 adet veri geldiğinde (`fetched == 0`), yerel SQLite veritabanındaki mevcut stok verileri **asla silinmez**.
   - Sistem otomatik olarak `Sistem Deaktif` moduna geçer, durum rozeti kırmızıya (`.status-indicator.offline`) döner.
   - Giriş ekranında (`/api/system/login`), çevrimdışı geri dönüş (offline fallback) yalnızca gerçek ağ kesintilerinde devreye girer. Hatalı parola girişlerinde parolanın kaydedilmesi engellenmiş, offline oturumlarda `credentials_unverified=True` ile kullanıcı uyarılmaktadır.

5. **Atomik Staging Değişimi (`BEGIN IMMEDIATE` & Güvenli Silme Koruma)**:
   - `save_bkst_data_to_db` fonksiyonu verileri önce `bkst_depo_verileri_staging` geçici tablosuna yazar, ardından tek bir `BEGIN IMMEDIATE` işlemiyle `bkst_depo_verileri` tablosuyla atomik olarak takas eder.
   - `username` boş olduğunda işlem iptal edilir ve koşulsuz `DELETE FROM` engellenmiştir.

6. **İş Parçacığı Güvenliği (`threading.RLock`) & Global Durum İzolasyonu**:
   - Ortak bellek değişkenleri (`_last_update_check_time`, `_cached_update_response`, `_app_bkst_synced`, `bkst_status`, `bkst_message`) global `_state_lock = threading.RLock()` ile yarış durumlarına (race condition) karşı korunur.
   - Kullanıcı bazlı `_compare_cache_map[user_key]` ve `_user_cache_map[user_key]` ile çok kullanıcılı çakışmalar engellenmiştir.

7. **Tam Sertleştirilmiş Oturum Güvenliği & CSRF Koruması**:
   - `local_session_token` çerezi istemciye `HttpOnly=True` ve `SameSite=Strict` olarak verilir. Token JSON yanıtlarında veya `localStorage` içinde sızdırılmaz.
   - Tüm veri değiştiren (`POST`, `PUT`, `DELETE`) API isteklerinde `X-Requested-With: XMLHttpRequest` başlığı zorunludur. `window.apiFetch` bu başlığı otomatik olarak ekler ve 401 yanıtlarında oturum açma sayfasına yönlendirir.
   - Flask gizli anahtarı rastgele 32 baytlık `.flask_secret` dosyası üzerinden dinamik ve kalıcı olarak yönetilir.

8. **XSS & JS Kod Enjeksiyonu Koruması**:
   - Şablonlardaki inline `onclick` fonksiyon çağrıları kaldırılarak `data-group-key` ve `data-id` niteliklerine ve tablo seviyesinde event delegation yapısına dönüştürülmüştür.
   - HTML içeriği ve nitelikleri için `escHtml` ve `escAttr` yardımcıları kullanılır.

9. **Dinamik ve Güvenli SSL / TLS Denetimi (`QR_SSL_VERIFY`)**:
   - Kod tabanındaki tüm `verify=False` parametreleri güvenli varsayılan `SSL_VERIFY` (`QR_SSL_VERIFY=1`) değişkenine bağlanmıştır. MITM (araya girme) riskleri engellenmiştir.

10. **Doğru Tip Dönüşümü, Koli/Palet Toplu Çıkış & Tekrar Okutma Yönetimi (`tekrar_uyari`)**:
    - İlk kez okutulan ürünler kesinlikle `tekrar_uyari = 0` (False) olarak kaydedilir ve yeşil kartla onaylanır.
    - Daha önce okutulmuş bir ürün tekrar okutulduğunda `tekrar_uyari = 1` ve `tekrar_uyari: True` dönülerek arayüzde sarı uyarı kartı ve TEKRAR rozeti gösterilir.
    - Koli/palet barkodu okutulduğunda kolideki tüm ürünler topluca çıkış kayıtlarına işlenir ve arayüzdeki çıkış tablosunda koli içindeki ürünlerin tamamı tek tek listelenir.
    - Karekod eşleştirmelerinde casefold yapılarak büyük/küçük harf varyasyonları duplicate kaçaklarına yol açmaz.

11. **Şablon Değişkenleri 30sn TTL Bellek Önbelleği & Dinamik Şablon Yenileme**:
    - `inject_global_template_vars` her sayfa isteğinde diskten dosya okumak yerine verileri 30 saniyelik TTL ile önbellekten sunarak CPU ve disk I/O yükünü sıfıra indirir.
    - `app.config['TEMPLATES_AUTO_RELOAD'] = True` ile güncellenen şablonlar anlık olarak devreye girer.

12. **Çift Katmanlı Satış Mimarisi: Kalıcı Satış Arşivi (`satis_arsivi`) ve Çalışma Sepeti (`cikis_kayitlari`)**:
    - Kullanıcı iş akışında "Sistemden Çıkacaklar Listesi"ni (`cikis_kayitlari`) aktif bir sevk sepeti olarak kullanır; ürünler Bakanlık sistemine aktarıldıkça veya sevk tamamlandıkça bu listeden silinebilir veya "Tümünü Sil" ile temizlenebilir.
    - Uzun vadeli (yıllara ve aylara yayılan) istatistiklerin kaybolmaması için tüm çıkışlar aynı anda kalıcı `satis_arsivi` tablosuna da yazılır.
    - Çalışma listesinden (`cikis_kayitlari`) satır silinse dahi `satis_arsivi` tablosuna dokunulmaz; İstatistikler & Raporlar modülü doğrudan `satis_arsivi` üzerinden hesaplanır.
    - Mükerrer okutma denetimi (`tekrar_uyari`), hem çalışma listesini hem de `satis_arsivi` tablosunu `UNION ALL` ile tarar; böylece aylar/yıllar önce çıkılmış bir ürün bile okutulsa anında geçmiş çıkış tarihiyle birlikte tespit edilir.
    - **İstatistikleri Sıfırlama (`POST /api/istatistikler/sifirla`)**: İstatistikler sayfasındaki "İstatistikleri Sıfırla" butonu ile kalıcı arşiv (`satis_arsivi`) çift onaylı olarak güvenle sıfırlanabilir. Aktif çalışma listesindeki sepet kayıtları bu işlemden etkilenmez. Tek seferlik migration koruması (`schema_migrations`) sayesinde sistem yeniden başlatıldığında sıfırlanan veriler eski listelerden tekrar geri yüklenmez.

13. **Gelişmiş Barkod & Karekod Doğrulama (GTIN ve Koli Ayrımı)**:
    - Barkod okutma alanına karekod yerine yalnızca GTIN okutulduğunda ürün çıkışı yapılması engellenir; sistem kullanıcıyı "Bu bir karekod değildir, ürünün karekodunu okutunuz" şeklinde uyarır.
    - Koli/palet aramalarında (`find_matching_koli`) 6 karakterden kısa girdilerin yanlışlıkla koli sanılması engellenmiştir.

14. **Bakanlık Depoya Kabul Et Modülü: Gerçek Durum Tespiti (`🟢 Kabul Bekliyor` vs `📦 Stoğa Alınmış`)**:
    - Bakanlık BKST `GetReceivedNotificationList` servisinin her geçerli/iptal edilmemiş faturayı yanıltıcı şekilde `HEADERSTATE: 'AKTIF'` döndürmesi sorunu çözülmüştür.
    - Sistem, gelen bildirimlerin detaylarını eşzamanlı `ThreadPoolExecutor(max_workers=25)` ile paralel sorgulayarak satır bazında `DETAILSTATE == 'ALIMA UYGUN'` olan ürünleri anlık tespit eder.
    - Henüz depoya kabul edilmemiş ürün içeren bildirimler `🟢 Kabul Bekliyor` olarak işaretlenir ve faturadaki bekleyen ürün sayısıyla birlikte listenin en başına sabitlenir. Tüm ürünleri önceden depoya alınmış bildirimler ise `📦 Stoğa Alınmış` olarak gösterilir.
    - Belge detayında ürünler tablo halinde incelenirken her kalemin durumu ('Kabul Bekliyor' veya 'Stoğa Alınmış') açıkça gösterilir. 'Tek Tuşla Depoya Kabul Et' butonu yalnızca kabul bekleyen ürünler varsa aktifleşir; zaten stoğa alınmış faturalarda '✅ Bu Belgedeki Ürünler Zaten Stoğa Alınmış' rozetiyle güvenli biçimde kilitlenir.
    - Bildirim tablosu ile detay tablosu `480px` sabit yükseklik ile simetrik olarak dengelenmiştir.

15. **Single-Instance Mutex & Bring-to-Front Desteği (`Calistir.exe`)**:
    - Windows native C# ile derlenen `Calistir.exe`, `Global\QRCompare_SingleInstance_Mutex` ve Windows API (`EnumWindows`, `SetForegroundWindow`, `ShowWindow`) kullanarak uygulamanın birden fazla açılmasını engeller; çift tıklandığında var olan pencereyi anında öne getirir ve maksimize eder.

---

## 2. Proje Dosya ve Kodları

Aşağıda QR Compare v3.2.0 projesinin tüm kaynak kodları eksiksiz olarak listelenmiştir:

### 📁 `version.json`

```json
{
  "version": "v3.2.1",
  "commit": "3.2.1",
  "date": "09.10.2026",
  "message": "v3.2.1: CSRF & X-Requested-With istemci/sunucu tam uyumluluğu, Reçetesiz Satış SMS doğrulama ve QR okutma düzeltmeleri, regresyon temizlikleri",
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
    "static/chart.umd.min.js",
    "static/favicon.ico",
    "static/favicon.png",
    "static/favicon.svg",
    "static/style.css",
    "templates/cikis.html",
    "templates/cikis_listesi.html",
    "templates/depo_kabul.html",
    "templates/depo_stoklari.html",
    "templates/index.html",
    "templates/istatistikler.html",
    "templates/kullaniciya_satis.html",
    "templates/login.html",
    "version.json"
  ]
}
```

---

### 📁 `requirements.txt`

```text
Flask==3.1.3
pandas==2.2.2
openpyxl==3.1.2
xlrd==2.0.2
requests==2.31.0
Werkzeug==3.1.8
waitress==3.0.0

```

---

### 📁 `.gitignore`

```gitignore
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
.session_token
.flask_secret
.dev_mode
*.log
Hosts_Duzelt.bat

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

### 📁 `KURULUM.md`

```markdown
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

### 📁 `build_exe.ps1`

```powershell
$code = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

// NOT: Mutex yalnızca launcher'ın kendi çift tıklama yarışını engeller.
// Asıl single-instance kontrolü IsPortOpen("127.0.0.1", 5000) ile yapılır.
// Python sunucusu bir kere başladıktan sonra ikinci Calistir.exe çağrısı
// port açık olduğu için yeni Python başlatmaz; sadece pencereyi öne getirir.
public class AppLauncher {
    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    private static extern int GetWindowTextLength(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern void SwitchToThisWindow(IntPtr hWnd, bool fAltTab);

    private const int SW_RESTORE = 9;
    private const int SW_SHOW = 5;
    private const int SW_MAXIMIZE = 3;

    private static IntPtr _foundWindow = IntPtr.Zero;

    private static bool EnumTheWindows(IntPtr hWnd, IntPtr lParam) {
        if (!IsWindowVisible(hWnd)) return true;
        int length = GetWindowTextLength(hWnd);
        if (length == 0) return true;

        StringBuilder builder = new StringBuilder(length + 1);
        GetWindowText(hWnd, builder, builder.Capacity);
        string title = builder.ToString();

        if (title.IndexOf("QR Compare", StringComparison.OrdinalIgnoreCase) >= 0) {
            _foundWindow = hWnd;
            return false;
        }
        return true;
    }

    private static IntPtr FindQrCompareWindow() {
        _foundWindow = IntPtr.Zero;
        EnumWindows(new EnumWindowsProc(EnumTheWindows), IntPtr.Zero);
        return _foundWindow;
    }

    private static void BringWindowToFront(IntPtr hWnd) {
        if (hWnd != IntPtr.Zero) {
            try {
                ShowWindow(hWnd, SW_RESTORE);
                ShowWindow(hWnd, SW_MAXIMIZE);
                SetForegroundWindow(hWnd);
                SwitchToThisWindow(hWnd, true);
            } catch {}
        }
    }

    public static void Main() {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        Directory.SetCurrentDirectory(baseDir);

        // 1. Eğer sunucu (port 5000) zaten açıksa:
        if (IsPortOpen("127.0.0.1", 5000)) {
            IntPtr existingWnd = FindQrCompareWindow();
            if (existingWnd != IntPtr.Zero) {
                // Açık pencereyi öne getir ve yeni tarayıcı açmadan sonlan
                BringWindowToFront(existingWnd);
                return;
            }

            // Port açık ama pencere bulunamadıysa tarayıcıyı aç
            OpenBrowser();
            for (int j = 0; j < 15; j++) {
                Thread.Sleep(200);
                IntPtr w = FindQrCompareWindow();
                if (w != IntPtr.Zero) {
                    BringWindowToFront(w);
                    break;
                }
            }
            return;
        }

        // 2. Sunucu henüz açık değilse: Mutex ile çift tıklama yarışını engelle
        bool createdNew = false;
        using (Mutex mutex = new Mutex(true, "Global\\QRCompare_SingleInstance_Mutex", out createdNew)) {
            if (!createdNew) {
                for (int i = 0; i < 20; i++) {
                    Thread.Sleep(300);
                    if (IsPortOpen("127.0.0.1", 5000)) {
                        IntPtr w = FindQrCompareWindow();
                        if (w != IntPtr.Zero) BringWindowToFront(w);
                        return;
                    }
                }
            }

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
                    Process.Start(psi);
                } catch {}
            }

            for (int i = 0; i < 32; i++) {
                Thread.Sleep(250);
                if (IsPortOpen("127.0.0.1", 5000)) break;
            }

            if (!IsPortOpen("127.0.0.1", 5000)) {
                try {
                    ProcessStartInfo diagPsi = new ProcessStartInfo();
                    diagPsi.FileName = "cmd.exe";
                    diagPsi.Arguments = "/k echo [HATA] Lokal sunucu acilamadi, Python hata ciktisi calistiriliyor... && python app.py";
                    diagPsi.WorkingDirectory = baseDir;
                    diagPsi.UseShellExecute = true;
                    Process.Start(diagPsi);
                    return;
                } catch {}
            }

            OpenBrowser();

            for (int j = 0; j < 15; j++) {
                Thread.Sleep(200);
                IntPtr w = FindQrCompareWindow();
                if (w != IntPtr.Zero) {
                    BringWindowToFront(w);
                    break;
                }
            }
        }
    }

    private static void OpenBrowser() {
        string browserExe = FindBrowserPath();
        if (!string.IsNullOrEmpty(browserExe)) {
            ProcessStartInfo bpsi = new ProcessStartInfo();
            bpsi.FileName = browserExe;
            bpsi.Arguments = "--app=http://127.0.0.1:5000 --start-maximized --window-position=0,0";
            bpsi.UseShellExecute = false;
            bpsi.CreateNoWindow = true;
            try {
                Process.Start(bpsi);
                return;
            } catch {}
        }
        try {
            Process.Start("http://127.0.0.1:5000");
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
            @"C:\Program Files\Python313\pythonw.exe",
            @"C:\Program Files\Python312\pythonw.exe",
            @"C:\Program Files\Python311\pythonw.exe",
            @"C:\Program Files\Python310\pythonw.exe",
            @"C:\Program Files\Python39\pythonw.exe",
            @"C:\Program Files (x86)\Python313\pythonw.exe",
            @"C:\Program Files (x86)\Python312\pythonw.exe",
            @"C:\Program Files (x86)\Python311\pythonw.exe",
            @"C:\Python313\pythonw.exe",
            @"C:\Python312\pythonw.exe",
            @"C:\Python311\pythonw.exe",
            @"C:\Python310\pythonw.exe",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python313\pythonw.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python312\pythonw.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python311\pythonw.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python310\pythonw.exe")
        };

        foreach (string path in candidates) {
            if (File.Exists(path)) return path;
        }

        string pathEnv = Environment.GetEnvironmentVariable("PATH");
        if (!string.IsNullOrEmpty(pathEnv)) {
            foreach (string p in pathEnv.Split(';')) {
                string w = Path.Combine(p.Trim(), "pythonw.exe");
                if (File.Exists(w)) return w;
                string py = Path.Combine(p.Trim(), "python.exe");
                if (File.Exists(py)) return py;
            }
        }

        return "pythonw.exe";
    }

    private static string FindBrowserPath() {
        string[] candidates = new string[] {
            @"C:\Program Files\Google\Chrome\Application\chrome.exe",
            @"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Google\Chrome\Application\chrome.exe"),
            @"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
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
Write-Host "Native Calistir.exe built successfully with Single-Instance & Bring-To-Front support!"

```

---

### 📁 `Calistir.bat`

```batch
@echo off
cd /d "%~dp0"
chcp 65001 > nul
title QR Stok Yonetim Sistemi

:: 1. Python kontrolu
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ============================================================
    echo [HATA] Python bu bilgisayarda bulunamadi!
    echo.
    echo Lutfen https://www.python.org adresinden Python'u indirin.
    echo KURULUM SIRASINDA EN ALTTAKI "Add Python to PATH" KUTUCUGUNU
    echo MUTLAKA ISARETLEYIN!
    echo ============================================================
    echo.
    pause
    exit /b 1
)

:: 2. Gerekli kutuphaneler kontrolu
python -c "import flask, waitress, pandas, openpyxl, requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo ============================================================
    echo [BILGI] Ilk calisma icin gerekli paketler kuruluyor...
    echo (waitress, flask, pandas, openpyxl, requests vb.)
    echo Lutfen bekleyin, bu islem sadece bir kez yapilacaktir...
    echo ============================================================
    echo.
    python -m pip install --upgrade pip >nul 2>&1
    python -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo [BILGI] requirements.txt tam yuklenemedi, temel kutuphaneler kuruluyor...
        python -m pip install flask waitress pandas openpyxl requests xlrd
    )
    echo.
    echo [BASARILI] Tum paketler kuruldu, program baslatiliyor...
    timeout /t 2 >nul
)

:: 3. Calistir
if exist "%~dp0Calistir.exe" (
    start "" "%~dp0Calistir.exe"
) else (
    start "" pythonw launcher.py
)
exit

```

---

### 📁 `Calistir.vbs`

```vbscript
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "Calistir.exe", 0, False

```

---

### 📁 `Guncelle.bat`

```batch
@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 > nul
title QR Stok Yonetim Sistemi - Guncelleyici

echo.
echo ============================================================
echo      QR STOK YONETIM SISTEMI - GUNCELLEME SERVISI
echo ============================================================
echo.

python guncelleme_kontrol.py

echo.
echo ============================================================
echo [TAMAMLANDI] Islem sona erdi.
echo ============================================================
echo.
echo Pencereyi kapatmak icin herhangi bir tusa basin...
pause >nul
exit

```

---

### 📁 `Kapat.bat`

```batch
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

### 📁 `Hosts_Duzelt.bat`

```batch
@echo off
chcp 65001 >nul
title GitHub & Hosts Onarici

:: Yonetici haklari kontrolu
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Yonetici yetkisi isteniyor, lutfen ekranda cikan uyariya EVET deyiniz...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo ========================================================
echo     GITHUB ZIP & BAGLANTI ONARIM ARACI
echo ========================================================
echo.
echo [1/3] Hosts dosyasi yedekleniyor (hosts.bak)...
copy /y "C:\Windows\System32\drivers\etc\hosts" "C:\Windows\System32\drivers\etc\hosts.bak" >nul

echo [2/3] Hosts dosyasindaki hatali GitHub ve 8.8.4.4 yonlendirmeleri temizleniyor...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$path = 'C:\Windows\System32\drivers\etc\hosts'; (Get-Content $path) | Where-Object { $_ -notmatch '8\.8\.4\.4' -and $_ -notmatch 'github\.com' } | Set-Content $path -Force"

echo [3/3] Windows DNS onbellegi sifirlaniyor...
ipconfig /flushdns >nul

echo.
echo ========================================================
echo  [BASARILI] Hosts dosyasi basariyla temizlendi!
echo  Artik GitHub'dan ZIP indirebilir ve sayfalara girebilirsiniz.
echo ========================================================
echo.
pause

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

def find_and_bring_window_to_front():
    if os.name != 'nt':
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        found_hwnd = None

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_windows_callback(hwnd, extra):
            nonlocal found_hwnd
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    if "QR Compare" in buff.value:
                        found_hwnd = hwnd
                        return False
            return True

        user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
        if found_hwnd:
            user32.ShowWindow(found_hwnd, 9)  # SW_RESTORE
            user32.ShowWindow(found_hwnd, 3)  # SW_MAXIMIZE
            user32.SetForegroundWindow(found_hwnd)
            return True
    except Exception:
        pass
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

def ensure_dependencies():
    packages = ["flask", "waitress", "pandas", "openpyxl", "requests"]
    missing = []
    for pkg in packages:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True, creationflags=NO_WINDOW)
        except Exception:
            pass

def launch():
    if is_port_in_use(5000):
        # Uygulama zaten çalışıyorsa var olan pencereyi öne getir
        if find_and_bring_window_to_front():
            return
        # Pencere bulunamadıysa yeni tarayıcı penceresi aç
        open_as_desktop_app("http://127.0.0.1:5000")
        return

    ensure_dependencies()
    py_dir = os.path.dirname(sys.executable)
    pythonw_cand = os.path.join(py_dir, "pythonw.exe")
    target_py = pythonw_cand if os.path.exists(pythonw_cand) else sys.executable
    flags = NO_WINDOW
    if os.name == 'nt':
        flags |= 0x00000008  # DETACHED_PROCESS
    subprocess.Popen([target_py, "app.py"], cwd=BASE_DIR, creationflags=flags)

    for _ in range(32):
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

SSL_VERIFY = os.environ.get("QR_SSL_VERIFY", "1") == "1"

# Windows Konsolu için ANSI Renk ve UTF-8 Türkçe Karakter Desteğini Aktifleştir
if os.name == 'nt':
    try:
        os.system('')  # Windows VT100 / ANSI escape sequence modunu açar
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
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

def clear_pycache():
    for dirpath, dirnames, filenames in os.walk(BASE_DIR):
        if "__pycache__" in dirnames:
            try:
                shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            except Exception:
                pass

def get_unified_version_info():
    v_code = "v1.0"
    v_commit = ""
    v_date = datetime.now().strftime("%d.%m.%Y")
    v_msg = "Sistem Güncel"

    v_path = os.path.join(BASE_DIR, "version.json")
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = str(v_data.get("version", "v1.0")).strip()
                v_commit = str(v_data.get("commit", "")).strip()
                v_date = str(v_data.get("date", "")).strip()
                v_msg = str(v_data.get("message", "Sistem Güncel")).strip()
        except Exception:
            pass

    full_ver = f"{v_code} ({v_commit})" if v_commit else v_code
    return full_ver, v_date, v_msg

def install_dependencies():
    print(f"  {CYAN}[3/3] Gerekli Python kütüphaneleri kontrol ediliyor ve kuruluyor...{RESET}")
    req_file = os.path.join(BASE_DIR, "requirements.txt")
    if os.path.exists(req_file):
        try:
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                capture_output=True, text=True, cwd=BASE_DIR, creationflags=NO_WINDOW
            )
            if res.returncode == 0:
                print(f"  {GREEN}  ✓ Tüm Python paketleri başarıyla doğrulandı ve yüklendi.{RESET}")
            else:
                print(f"  {YELLOW}  • Temel paketler (waitress, flask, pandas, openpyxl, requests) kuruluyor...{RESET}")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "waitress", "flask", "pandas", "openpyxl", "requests", "xlrd"],
                    capture_output=True, text=True, cwd=BASE_DIR, creationflags=NO_WINDOW
                )
                print(f"  {GREEN}  ✓ Temel paketler başarıyla yüklendi.{RESET}")
        except Exception as e:
            print(f"  {YELLOW}  • Paket yükleme uyarısı: {e}{RESET}")

def recompile_exe_if_possible():
    ps1_file = os.path.join(BASE_DIR, "build_exe.ps1")
    if os.path.exists(ps1_file) and os.name == 'nt':
        try:
            subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", "build_exe.ps1"],
                capture_output=True, text=True, cwd=BASE_DIR, creationflags=NO_WINDOW
            )
        except Exception:
            pass

def is_dev_mode():
    if os.environ.get("DEV_MODE") == "1":
        return True
    if os.path.exists(os.path.join(BASE_DIR, ".dev_mode")):
        return True
    return False

def parse_version_tuple(v_str):
    try:
        clean = re.sub(r'[^0-9.]', '', str(v_str))
        parts = [int(p) for p in clean.split('.') if p.isdigit()]
        return tuple(parts)
    except Exception:
        return (0, 0, 0)

def http_update():
    if is_dev_mode():
        print(f"\n{YELLOW}{BOLD} =============================================================={RESET}")
        print(f"{YELLOW}{BOLD}  🔧 [GELİŞTİRİCİ MODU AKTİF] (.dev_mode dosyası mevcut){RESET}")
        print(f"{WHITE}  Yerel kodlar korunuyor, GitHub'dan indirme/ezme yapılmayacak.{RESET}")
        print(f"{YELLOW}{BOLD} =============================================================={RESET}\n")
        install_dependencies()
        return False

    print(f"  {CYAN}[1/3] GitHub sunucusundan en güncel sürüm bilgisi sorgulanıyor...{RESET}")
    try:
        import requests
        import urllib3
        import zipfile
        import io
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        print(f"  {YELLOW}  • requests kütüphanesi yükleniyor...{RESET}")
        subprocess.run([sys.executable, "-m", "pip", "install", "requests", "urllib3"], capture_output=True, creationflags=NO_WINDOW)
        import requests
        import urllib3
        import zipfile
        import io

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }

    timestamp = time.time_ns()
    remote_vurl = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/main/version.json?t={timestamp}"

    try:
        resp = requests.get(remote_vurl, verify=SSL_VERIFY, timeout=10, headers=headers)
        if resp.status_code != 200:
            print(f"  {RED}[HATA] Güncelleme sunucusuna ulaşılamadı (HTTP {resp.status_code}){RESET}")
            return False

        remote_data = resp.json()
        remote_commit = str(remote_data.get("commit", "")).strip()
        remote_version = str(remote_data.get("version", "")).strip()
        remote_date = str(remote_data.get("date", "")).strip()
        remote_msg = str(remote_data.get("message", "")).strip()

        local_vpath = os.path.join(BASE_DIR, "version.json")
        local_commit = ""
        local_version = "v1.0"
        if os.path.exists(local_vpath):
            try:
                with open(local_vpath, "r", encoding="utf-8") as f:
                    v_raw = json.load(f)
                    local_commit = str(v_raw.get("commit", "")).strip()
                    local_version = str(v_raw.get("version", "v1.0")).strip()
            except Exception:
                pass

        # Sürüm karşılaştırması: Uzak sürüm yerel sürümden büyük değilse güncelleme yapma!
        remote_tup = parse_version_tuple(remote_version)
        local_tup = parse_version_tuple(local_version)

        if remote_tup <= local_tup or (local_commit and local_commit == remote_commit):
            print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
            print(f"{GREEN}{BOLD}  🟢 [GÜNCEL] Sisteminiz zaten en son sürümde ({local_version}).{RESET}")
            print(f"{WHITE}  📦 Yerel Sürüm: {local_version} | Uzak Sürüm: {remote_version}{RESET}")
            print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
            install_dependencies()
            return False

        print(f"\n  {YELLOW}{BOLD}[2/3] [🔄 YENİ SÜRÜM TESPİT EDİLDİ: {remote_version}] Dosyalar indiriliyor...{RESET}")

        zip_url = f"https://github.com/mfatih01020/stok_fatih/archive/refs/heads/main.zip?t={timestamp}"
        zip_resp = requests.get(zip_url, verify=SSL_VERIFY, timeout=40, headers=headers)

        if zip_resp.status_code != 200:
            print(f"  {RED}[HATA] Güncelleme zip paketi indirilemedi (HTTP {zip_resp.status_code}){RESET}")
            return False

        # Asla ezilmeyecek kullanıcı dosyaları
        ignored_extensions = ('.db', '.sqlite', '.sqlite3', '.log')
        ignored_filenames = (
            'cikis_kayitlari.db', 'stok.db', 'stok_takip.db', 
            'bakanlik_giris_bilgileri.txt', 'günlük satışlar.txt',
            '.session_token'
        )

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
                try:
                    with zf.open(member) as source, open(dest_path, "wb") as target:
                        target.write(source.read())
                except PermissionError:
                    # Dosya o an kullanımda ise (örneğin Calistir.exe açık ise) atla
                    pass

        with open(local_vpath, "w", encoding="utf-8") as f:
            json.dump(remote_data, f, ensure_ascii=False, indent=2)

        clear_pycache()
        install_dependencies()
        recompile_exe_if_possible()

        print(f"\n{GREEN}{BOLD} =============================================================={RESET}")
        print(f"{GREEN}{BOLD}  🟢 [BAŞARILI] Sistem başarıyla {remote_version} sürümüne güncellendi!{RESET}")
        print(f"{WHITE}{BOLD}  📦 Sürüm: {remote_version} ({remote_commit}) | {remote_date}{RESET}")
        print(f"{WHITE}  📝 Not  : {remote_msg}{RESET}")
        print(f"{GREEN}{BOLD} =============================================================={RESET}\n")
        return True

    except Exception as e:
        print(f"  {RED}[HATA] Güncelleme işlemi sırasında beklenmeyen hata: {e}{RESET}")
        return False

def force_update():
    print(f"\n{CYAN}{BOLD} =============================================================={RESET}")
    print(f"{WHITE}{BOLD}       QR STOK YÖNETİM SİSTEMİ - GÜNCELLEME KONTROLÜ{RESET}")
    print(f"{CYAN}{BOLD} =============================================================={RESET}\n")

    cur_hash, cur_date, cur_msg = get_unified_version_info()
    print(f"  {WHITE}{BOLD}📌 MEVCUT YÜKLÜ SÜRÜM:{RESET}")
    print(f"  {DIM}  • Sürüm Kodu: {RESET}{WHITE}{cur_hash}{RESET}")
    print(f"  {DIM}  • Tarih     : {RESET}{WHITE}{cur_date}{RESET}")
    print(f"  {DIM}  • Not       : {RESET}{WHITE}{cur_msg}{RESET}\n")

    return http_update()

if __name__ == "__main__":
    success = force_update()

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
from datetime import datetime, timedelta
import random
import subprocess
import socket
import atexit
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
else:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

if getattr(sys, 'stderr', None) is None:
    sys.stderr = SafeStream()
else:
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ── Logging Configuration ───────────────────────────────────────────────────
handler = RotatingFileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.log'), maxBytes=5_000_000, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger('qr_compare')

SSL_VERIFY = os.environ.get("QR_SSL_VERIFY", "1") == "1"

# ── Dynamic Flask Secret Key ─────────────────────────────────────────────────
FLASK_SECRET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".flask_secret")

def _get_or_create_flask_secret():
    if os.path.exists(FLASK_SECRET_PATH):
        try:
            with open(FLASK_SECRET_PATH, 'r', encoding='utf-8') as f:
                s = f.read().strip()
                if s:
                    return s
        except Exception:
            pass
    secret = secrets.token_hex(32)
    try:
        with open(FLASK_SECRET_PATH, 'w', encoding='utf-8') as f:
            f.write(secret)
    except Exception:
        pass
    return secret

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or _get_or_create_flask_secret()
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ── Global Thread Lock & Memory State ─────────────────────────────────────────
_state_lock = threading.RLock()
_user_cache_map = {}
_cache_access_order = []
_compare_cache_map = {}
bkst_status = "closed"
bkst_message = ""
_app_bkst_synced = False

_fetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='bkst_fetch')
_fetch_future = None

# ── Template Vars TTL Cache ───────────────────────────────────────────────────
_template_vars_cache = {
    'expires_at': 0,
    'user_name': '', 'v_code': '', 'full_commit': '',
    'v_date': '', 'v_msg': ''
}
_template_vars_lock = threading.Lock()

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
    if request.cookies.get('local_session_token') != LOCAL_SESSION_TOKEN:
        response.set_cookie('local_session_token', LOCAL_SESSION_TOKEN, httponly=True, samesite='Strict')
    return response

@app.before_request
def check_authentication():
    path = request.path
    if (path.startswith('/static') or 
        path == '/login' or 
        path == '/api/system/login' or 
        path == '/api/system/check_update' or 
        path == '/api/system/apply_update' or 
        path == '/api/system/version' or 
        path == '/api/system/heartbeat' or 
        path == '/api/heartbeat'):
        return None

    username, password, _, _ = read_bkst_credentials()
    if not username or not password:
        if path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Kullanıcı oturum açmamış veya giriş bilgileri yok.', 'code': 401}), 401
        return redirect('/login')

    if path.startswith('/api/'):
        client_tokens = [
            request.cookies.get('local_session_token'),
            request.headers.get('X-Local-Token'),
            request.headers.get('X-Session-Token'),
            request.args.get('token')
        ]
        # Ignore empty, None, and literal strings 'undefined', 'null'
        valid_tokens = [t.strip() for t in client_tokens if t and str(t).strip().lower() not in ('undefined', 'null', 'none', '')]

        if not any(t == LOCAL_SESSION_TOKEN for t in valid_tokens):
            return jsonify({'success': False, 'error': 'Geçersiz veya eksik oturum anahtarı', 'code': 401}), 401

        # CSRF Koruması: Veri değiştiren isteklerde X-Requested-With veya Same-Origin zorunludur
        if request.method in ['POST', 'PUT', 'DELETE']:
            is_xhr = (request.headers.get('X-Requested-With') == 'XMLHttpRequest')
            sec_fetch = (request.headers.get('Sec-Fetch-Site') or '').lower()
            is_same_origin = (sec_fetch == 'same-origin')

            host = request.headers.get('Host', '')
            origin = request.headers.get('Origin', '')
            referer = request.headers.get('Referer', '')
            if host:
                if origin and (host in origin):
                    is_same_origin = True
                if referer and (host in referer):
                    is_same_origin = True

            if not (is_xhr or is_same_origin):
                return jsonify({'success': False,
                                'error': 'CSRF koruması: X-Requested-With başlığı eksik',
                                'code': 403}), 403

    return None

# ── Sürüm & Güncelleme Bilgisi ────────────────────────────────────────────────
@app.route('/api/system/version', methods=['GET'])
def get_version_info():
    v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
    v_code = "v1.0"
    v_commit = ""
    v_date = datetime.now().strftime("%d.%m.%Y")
    v_msg = "Sistem Güncel"
    if os.path.exists(v_path):
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                v_code = str(v_data.get("version", "v1.0")).strip()
                v_commit = str(v_data.get("commit", "")).strip()
                v_date = str(v_data.get("date", "")).strip()
                v_msg = str(v_data.get("message", "Sistem Güncel")).strip()
        except Exception as e:
            logger.error(f"Error reading version.json: {e}")

    full_hash = f"{v_code} ({v_commit})" if v_commit else v_code
    return jsonify({
        "success": True,
        "version": v_code,
        "commit_hash": full_hash,
        "commit_date": v_date,
        "commit_msg": v_msg
    })

@app.route('/api/system/heartbeat', methods=['POST', 'GET'])
def system_heartbeat():
    payload = _bkst_state_payload()
    payload["local_count"] = _local_item_count_cached()
    return jsonify(payload)

@app.route('/api/heartbeat', methods=['POST', 'GET'])
def api_heartbeat():
    return jsonify({'status': 'ok'})

# ── SQLite Veritabanı Katmanı & Staging Swap ──────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cikis_kayitlari.db')

def save_bkst_data_to_db(df, username=""):
    if not username:
        logger.warning("save_bkst_data_to_db: username boş, işlem iptal.")
        return
    if df is None:
        return
    if isinstance(df, pd.DataFrame) and df.empty:
        return
    if isinstance(df, list) and len(df) == 0:
        return

    ensure_db_schema()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()

    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?", (username,))

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if isinstance(df, pd.DataFrame):
            koli_col = find_koli_column(df.columns)
            records = df.to_dict(orient="records")
        else:
            koli_col = None
            records = df

        rows_to_insert = []
        for r in records:
            qr_val = normalize_qr(str(r.get("Karekod", r.get("tam_karekod", r.get("QR", "")))))
            gtin_val = ""
            for k in ("Gtin Numarası", "Gtin / Barkod", "Gtin/Barkod", "gtin", "GTIN", "BARKOD", "Barkod", "BARCODE", "Barcode"):
                v = r.get(k)
                if v is not None and str(v).strip() and str(v).strip().upper() != "NAN":
                    gtin_val = str(v).strip()
                    break
            if not gtin_val and qr_val:
                parsed = parse_gs1_qr(qr_val)
                if parsed and parsed.get("gtin"):
                    gtin_val = str(parsed["gtin"]).strip()

            urun_val = str(r.get("Ürün Adı", r.get("urun_adi", r.get("URUNADI", "")))).strip()
            seri_val = str(r.get("Seri Numarası", r.get("seri_no", r.get("SERIALNUMBER", "")))).strip()
            parti_val = str(r.get("Parti Numarası", r.get("parti_no", r.get("SARJNO", "")))).strip()
            raw_koli = r.get(koli_col) if koli_col else (r.get("Koli Numarası") or r.get("koli_no") or r.get("PAKETNO") or r.get("KOLINO"))
            koli_val = str(raw_koli).strip().upper() if raw_koli is not None else ""
            if koli_val in ("NAN", "NONE", "NULL"):
                koli_val = ""
            palet_val = str(r.get("Palet Numarası", r.get("palet_no", ""))).strip()
            uretim_val = str(r.get("Üretim Tarihi", r.get("uretim_tarihi", ""))).strip()
            skt_val = str(r.get("Son Kullanma Tarihi", r.get("skt", r.get("SKT", "")))).strip()

            rows_to_insert.append((gtin_val, urun_val, seri_val, parti_val, koli_val, palet_val, uretim_val, skt_val, qr_val, username, now_str))

        c.executemany('''INSERT INTO bkst_depo_verileri_staging
            (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', rows_to_insert)

        c.execute("DELETE FROM bkst_depo_verileri WHERE kullanici_adi = ?", (username,))
        c.execute('''INSERT INTO bkst_depo_verileri
            (gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi)
            SELECT gtin, urun_adi, seri_no, parti_no, koli_no, palet_no, uretim_tarihi, skt, tam_karekod, kullanici_adi, guncelleme_tarihi
            FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?''', (username,))
        c.execute("DELETE FROM bkst_depo_verileri_staging WHERE kullanici_adi = ?", (username,))
        conn.commit()
    except Exception as e:
        logger.error(f"save_bkst_data_to_db transaction error: {e}", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        ensure_db_schema()
        raise e
    finally:
        try:
            conn.close()
        except Exception:
            pass

    user_key = username or "default_user"
    with _state_lock:
        _user_cache_map.pop(user_key, None)
        if user_key in _cache_access_order:
            _cache_access_order.remove(user_key)

def ensure_db_schema(conn=None):
    close_at_end = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        close_at_end = True
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")

    # 1. cikis_kayitlari tablosu
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
    cikis_cols = {row[1].lower() for row in c.fetchall()}
    for col, col_type in [
        ("tarih", "TEXT NOT NULL DEFAULT ''"),
        ("urun_adi", "TEXT"),
        ("barkod", "TEXT"),
        ("koli_no", "TEXT"),
        ("seri_no", "TEXT"),
        ("parti_no", "TEXT"),
        ("palet_no", "TEXT"),
        ("uretim_tarihi", "TEXT"),
        ("skt", "TEXT"),
        ("ham_karekod", "TEXT"),
        ("tekrar_uyari", "INTEGER DEFAULT 0"),
        ("kullanici_adi", "TEXT")
    ]:
        if col.lower() not in cikis_cols:
            try:
                c.execute(f"ALTER TABLE cikis_kayitlari ADD COLUMN {col} {col_type}")
            except Exception as e:
                logger.warning(f"Could not add column {col} to cikis_kayitlari: {e}")

    # 2. bkst_depo_verileri tablosu
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
    c.execute("PRAGMA table_info(bkst_depo_verileri)")
    bkst_cols = {row[1].lower() for row in c.fetchall()}
    for col in [
        "gtin", "urun_adi", "seri_no", "parti_no", "koli_no",
        "palet_no", "uretim_tarihi", "skt", "tam_karekod",
        "gln", "adres_id", "kullanici_adi", "guncelleme_tarihi"
    ]:
        if col.lower() not in bkst_cols:
            try:
                c.execute(f"ALTER TABLE bkst_depo_verileri ADD COLUMN {col} TEXT")
            except Exception as e:
                logger.warning(f"Could not add column {col} to bkst_depo_verileri: {e}")

    # 3. bkst_depo_verileri_staging tablosu
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
    c.execute("PRAGMA table_info(bkst_depo_verileri_staging)")
    staging_cols = {row[1].lower() for row in c.fetchall()}
    for col in [
        "gtin", "urun_adi", "seri_no", "parti_no", "koli_no",
        "palet_no", "uretim_tarihi", "skt", "tam_karekod",
        "gln", "adres_id", "kullanici_adi", "guncelleme_tarihi"
    ]:
        if col.lower() not in staging_cols:
            try:
                c.execute(f"ALTER TABLE bkst_depo_verileri_staging ADD COLUMN {col} TEXT")
            except Exception as e:
                logger.warning(f"Could not add column {col} to bkst_depo_verileri_staging: {e}")

    # 4. satis_arsivi tablosu (Kalıcı Satış & İstatistik Arşivi)
    c.execute('''CREATE TABLE IF NOT EXISTS satis_arsivi (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih         TEXT NOT NULL DEFAULT '',
        urun_adi      TEXT,
        barkod        TEXT,
        koli_no       TEXT,
        seri_no       TEXT,
        parti_no      TEXT,
        palet_no      TEXT,
        uretim_tarihi TEXT,
        skt           TEXT,
        ham_karekod   TEXT,
        tekrar_uyari  INTEGER DEFAULT 0,
        kullanici_adi TEXT,
        durum         TEXT DEFAULT 'CIKIS_YAPILDI'
    )''')

    # İndeksler
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_ham_karekod ON cikis_kayitlari(ham_karekod)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_kullanici_adi ON cikis_kayitlari(kullanici_adi)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_karekod ON bkst_depo_verileri(tam_karekod)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_gtin ON bkst_depo_verileri(gtin)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bkst_kullanici ON bkst_depo_verileri(kullanici_adi)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_arsivi_tarih ON satis_arsivi(tarih)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_arsivi_qr ON satis_arsivi(ham_karekod)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_arsivi_seri ON satis_arsivi(seri_no)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_satis_arsivi_urun ON satis_arsivi(urun_adi)")
    except Exception:
        pass

    # Otomatik ilk aktarım: cikis_kayitlari'ndan satis_arsivi'ne tek seferlik ilk geçiş aktarımı
    try:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (key TEXT PRIMARY KEY, migrated_at TEXT)")
        c.execute("SELECT 1 FROM schema_migrations WHERE key = 'v315_initial_archive_backfill'")
        if not c.fetchone():
            c.execute('''
                INSERT INTO satis_arsivi (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, durum)
                SELECT tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, 'CIKIS_YAPILDI'
                FROM cikis_kayitlari
            ''')
            c.execute("INSERT OR REPLACE INTO schema_migrations (key, migrated_at) VALUES ('v315_initial_archive_backfill', ?)", 
                      (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
    except Exception as e_backfill:
        logger.warning(f"satis_arsivi backfill notice: {e_backfill}")

    conn.commit()
    if close_at_end:
        conn.close()

def init_db():
    ensure_db_schema()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    # ── Otomatik GTIN Onarımı / Geriye Dönük Veri Doldurma ────────────────────
    try:
        # bkst_depo_verileri boş gtin'leri karekoddan doldur
        c.execute("SELECT id, tam_karekod FROM bkst_depo_verileri WHERE gtin IS NULL OR gtin = ''")
        for row_id, qr_code in c.fetchall():
            if qr_code:
                parsed = parse_gs1_qr(qr_code)
                if parsed and parsed.get("gtin"):
                    c.execute("UPDATE bkst_depo_verileri SET gtin = ? WHERE id = ?", (str(parsed["gtin"]), row_id))

        # cikis_kayitlari boş barkod'ları karekoddan doldur
        c.execute("SELECT id, ham_karekod FROM cikis_kayitlari WHERE barkod IS NULL OR barkod = ''")
        for row_id, qr_code in c.fetchall():
            if qr_code:
                parsed = parse_gs1_qr(qr_code)
                if parsed and parsed.get("gtin"):
                    c.execute("UPDATE cikis_kayitlari SET barkod = ? WHERE id = ?", (str(parsed["gtin"]), row_id))

        # bkst_depo_verileri içindeki eşleşen tam_karekod'u cikis_kayitlari'na yaz (seri no ile okutulmuş kayıtları onar)
        c.execute('''
            SELECT ck.id, bv.tam_karekod
            FROM cikis_kayitlari ck
            JOIN bkst_depo_verileri bv ON LOWER(ck.seri_no) = LOWER(bv.seri_no)
            WHERE (ck.ham_karekod IS NULL OR length(ck.ham_karekod) < 20 OR ck.ham_karekod = ck.seri_no)
              AND bv.tam_karekod IS NOT NULL AND length(bv.tam_karekod) >= 20
        ''')
        for ck_id, full_qr in c.fetchall():
            c.execute("UPDATE cikis_kayitlari SET ham_karekod = ? WHERE id = ?", (full_qr, ck_id))

        # Yinelenen seri numarası veya karekod durumunda tekrar_uyari flag'lerini onar:
        # İlk çıkış (en küçük id) -> 0, sonraki çıkışlar (büyük id'ler) -> 1
        c.execute("SELECT id, seri_no, ham_karekod, barkod FROM cikis_kayitlari ORDER BY id ASC")
        rows = c.fetchall()
        seen_keys = set()
        for r_id, s_no, qr_val, b_val in rows:
            key_qr = f"qr:{qr_val.strip().casefold()}" if qr_val and len(qr_val.strip()) >= 16 else None
            key_seri = f"seri:{s_no.strip().casefold()}_{b_val or ''}" if s_no and s_no.strip() else None

            is_duplicate = False
            if key_qr and key_qr in seen_keys:
                is_duplicate = True
            if key_seri and key_seri in seen_keys:
                is_duplicate = True

            if key_qr: seen_keys.add(key_qr)
            if key_seri: seen_keys.add(key_seri)

            c.execute("UPDATE cikis_kayitlari SET tekrar_uyari = ? WHERE id = ?", (1 if is_duplicate else 0, r_id))
    except Exception as e:
        logger.error(f"GTIN and duplicate backfill migration error: {e}")

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
                    guid_match = re.search(r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', raw_id)
                    if guid_match:
                        address_id = guid_match.group(1).strip()
                    elif " - " in raw_id:
                        address_id = raw_id.split(" - ")[0].strip()
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
    now = time.time()
    with _template_vars_lock:
        if now > _template_vars_cache['expires_at']:
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

            v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
            v_code = "v1.0"
            v_commit = ""
            v_date = datetime.now().strftime("%d.%m.%Y")
            v_msg = "Sistem Güncel"
            if os.path.exists(v_path):
                try:
                    with open(v_path, "r", encoding="utf-8") as f:
                        v_data = json.load(f)
                        v_code = str(v_data.get("version", "v1.0")).strip()
                        v_commit = str(v_data.get("commit", "")).strip()
                        v_date = str(v_data.get("date", "")).strip()
                        v_msg = str(v_data.get("message", "Sistem Güncel")).strip()
                except Exception:
                    pass

            full_commit = f"{v_code} ({v_commit})" if v_commit else v_code
            _template_vars_cache.update({
                'expires_at': now + 30,
                'user_name': user_name,
                'v_code': v_code,
                'full_commit': full_commit,
                'v_date': v_date,
                'v_msg': v_msg
            })

        user_name = _template_vars_cache['user_name']
        v_code = _template_vars_cache['v_code']
        full_commit = _template_vars_cache['full_commit']
        v_date = _template_vars_cache['v_date']
        v_msg = _template_vars_cache['v_msg']

    global bkst_online, bkst_status
    is_offline = (bkst_online is False or bkst_status in ("offline", "error"))
    system_status_text = "Sistem Deaktif" if is_offline else "Sistem Aktif"
    system_status_cls = "offline" if is_offline else "online"

    return dict(
        current_user_name=user_name,
        current_app_version=v_code,
        current_app_commit=full_commit,
        current_app_date=v_date,
        current_app_msg=v_msg,
        bkst_online=bkst_online,
        is_system_active=not is_offline,
        system_status_text=system_status_text,
        system_status_cls=system_status_cls,
        local_session_token=LOCAL_SESSION_TOKEN
    )

def normalize_qr(qr):
    if pd.isna(qr):
        return ""
    qr_str = str(qr).strip().replace(" ", "")
    qr_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', qr_str)
    return qr_str

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
                    yy_int = int(yy)
                    century = "19" if 50 <= yy_int <= 99 else "20"
                    result["skt"] = f"{dd}.{mm}.{century}{yy}"
                idx += 8
                continue
            elif token[idx:].startswith("11") and len(token[idx:]) >= 8 and token[idx+2:idx+8].isdigit():
                if not result["uretim_tarihi"]:
                    yy, mm, dd = token[idx+2:idx+4], token[idx+4:idx+6], token[idx+6:idx+8]
                    yy_int = int(yy)
                    century = "19" if 50 <= yy_int <= 99 else "20"
                    result["uretim_tarihi"] = f"{dd}.{mm}.{century}{yy}"
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
    if not username:
        # FIX-CACHE-ANON: username yoksa DB'den oku, bellek önbelleğine yazmadan döndür
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        try:
            df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri", conn)
        except Exception:
            df = pd.DataFrame()
        finally:
            try:
                conn.close()
            except Exception:
                pass

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
                qr_dict[qr_val.casefold()] = r
            if gtin_val:
                if gtin_val not in gtin_dict:
                    gtin_dict[gtin_val] = r
                if gtin_val.casefold() not in gtin_dict:
                    gtin_dict[gtin_val.casefold()] = r

            if koli_val and koli_val != "NAN":
                if koli_val not in koli_dict:
                    koli_dict[koli_val] = []
                koli_dict[koli_val].append(r)

        return (df, qr_dict, gtin_dict, koli_dict)

    user_key = username
    with _state_lock:
        if user_key in _user_cache_map:
            if user_key in _cache_access_order:
                _cache_access_order.remove(user_key)
            _cache_access_order.append(user_key)
            return _user_cache_map[user_key]

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ''", conn, params=(username,))
    except Exception as e:
        logger.error(f"Error querying bkst_depo_verileri, repairing schema: {e}")
        try:
            ensure_db_schema()
            df = pd.read_sql_query("SELECT * FROM bkst_depo_verileri WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ''", conn, params=(username,))
        except Exception:
            df = pd.DataFrame()
    finally:
        try:
            conn.close()
        except Exception:
            pass

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
            qr_dict[qr_val.casefold()] = r
        if gtin_val:
            if gtin_val not in gtin_dict:
                gtin_dict[gtin_val] = r
            if gtin_val.casefold() not in gtin_dict:
                gtin_dict[gtin_val.casefold()] = r

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

def check_is_parti_no(code, df):
    if not code or df is None or df.empty:
        return False, 0, ""
    code_clean = str(code).strip()
    candidates = [code_clean.casefold()]
    if code_clean.startswith("(10)") and len(code_clean) > 4:
        candidates.append(code_clean[4:].strip().casefold())
    elif code_clean.startswith("10") and len(code_clean) > 2:
        candidates.append(code_clean[2:].strip().casefold())

    for cand in candidates:
        if not cand:
            continue
        matches = [
            r for r in df.to_dict(orient="records")
            if str(r.get("Parti Numarası", "")).strip().casefold() == cand
        ]
        if matches:
            urun_adi = str(matches[0].get("Ürün Adı", "")).strip()
            return True, len(matches), urun_adi

    return False, 0, ""

def check_is_gtin_no(code, df, gtin_map):
    if not code or df is None or df.empty:
        return False, 0, ""
    code_str = str(code).strip()

    # Tam bir GS1 karekod ise (içinde seri numarası varsa), bu saf GTIN değildir!
    parsed = parse_gs1_qr(code_str)
    if parsed and parsed.get('seri_no'):
        return False, 0, ""

    norm = normalize_qr(code_str)
    candidates = [norm]
    if norm.isdigit():
        candidates.append(norm.lstrip('0'))
        if len(norm) == 13:
            candidates.append('0' + norm)
        elif len(norm) == 14 and norm.startswith('0'):
            candidates.append(norm[1:])

    matched_name = ""
    for cand in candidates:
        if cand and gtin_map and cand in gtin_map:
            matched_name = str(gtin_map[cand].get('Ürün Adı', '')).strip()
            break

    if not matched_name and norm.isdigit() and len(norm) in (8, 12, 13, 14):
        for r in df.to_dict(orient="records"):
            r_gtin = normalize_qr(str(r.get("Gtin Numarası") or r.get("Gtin / Barkod") or r.get("gtin") or ""))
            if r_gtin in candidates:
                matched_name = str(r.get("Ürün Adı", "")).strip()
                break

    if matched_name:
        count = sum(
            1 for r in df.to_dict(orient="records")
            if normalize_qr(str(r.get("Gtin Numarası") or r.get("Gtin / Barkod") or r.get("gtin") or "")) in candidates
        )
        return True, count, matched_name

    return False, 0, ""

def resolve_product_from_cache(code, df, qr_map, gtin_map):
    if not code:
        return None
    code_norm = normalize_qr(code)
    code_case = code_norm.casefold()

    # Parti Numarası tekil kutu olarak asla eşleşmemelidir
    is_parti, _, _ = check_is_parti_no(code_norm, df)
    if is_parti:
        return None

    # GTIN / Barkod tekil kutu olarak asla eşleşmemelidir
    is_gtin, _, _ = check_is_gtin_no(code_norm, df, gtin_map)
    if is_gtin:
        return None

    # 1. Tam karekod eşleşmesi
    if qr_map and code_norm in qr_map:
        return qr_map[code_norm]

    # 2. GS1 Karekod ayrıştırma denemesi (Karekod içindeki seri ve gtin)
    parsed = parse_gs1_qr(code_norm)
    if parsed and parsed.get('seri_no'):
        p_seri = str(parsed['seri_no']).strip().casefold()
        p_gtin = str(parsed.get('gtin', '')).strip().casefold()
        if df is not None and not df.empty:
            for r in df.to_dict(orient="records"):
                r_seri = str(r.get("Seri Numarası", "")).strip().casefold()
                if r_seri and r_seri == p_seri:
                    r_gtin = str(r.get("Gtin Numarası") or r.get("Gtin / Barkod") or "").strip().casefold()
                    if not p_gtin or not r_gtin or p_gtin == r_gtin:
                        return r

    # 3. Seri Numarası doğrudan eşleşmesi (Kullanıcı barkod yerine seri no okuttuysa)
    if df is not None and not df.empty:
        for r in df.to_dict(orient="records"):
            r_seri = str(r.get("Seri Numarası", "")).strip().casefold()
            if r_seri and r_seri == code_case:
                return r

    return None

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

@app.route('/istatistikler')
@app.route('/raporlar')
def istatistikler_page():
    return render_template('istatistikler.html')

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

        username_cred, _, _, _ = read_bkst_credentials()
        user_key = username_cred or "_anon"

        with _state_lock:
            _compare_cache_map[user_key] = {
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
    username_cred, _, _, _ = read_bkst_credentials()
    user_key = username_cred or "_anon"
    with _state_lock:
        cached_data = _compare_cache_map.get(user_key)
        if not cached_data:
            return "No comparison run yet", 400
        res_copy = dict(cached_data)
        
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
    username_cred, _, _, _ = read_bkst_credentials()
    user_key = username_cred or "_anon"
    with _state_lock:
        cached_data = _compare_cache_map.get(user_key)
        if not cached_data:
            return "No comparison run yet", 400
        res_copy = dict(cached_data)
        
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

        if clean_code_all and clean_key_all and len(clean_code_all) >= 6 and len(clean_key_all) >= 6 and clean_code_all == clean_key_all:
            return k_key, k_rows

        if (digits_stripped and key_digits_stripped
            and len(digits_stripped) >= 6 and len(key_digits_stripped) >= 6
            and digits_stripped == key_digits_stripped):
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

        # Koli değilse ve bir GTIN / Çizgi Barkod veya Parti No ise REDDET (rastgele ürün seçilmesini engelle)
        if not is_koli_scan:
            is_gtin, g_count, g_urun = check_is_gtin_no(code_norm, df, gtin_dict)
            if is_gtin:
                return jsonify({
                    "success": False,
                    "is_gtin_no": True,
                    "error": f'"{code}" bir GTIN / Çizgi Barkod numarasıdır ({g_urun}). Bu barkod tekil bir ilaca ait karekod değildir. Lütfen kutu üzerindeki 2D Karekodu (DataMatrix) okutunuz.'
                })
            if code_norm.isdigit() and len(code_norm) in (8, 12, 13, 14):
                return jsonify({
                    "success": False,
                    "is_gtin_no": True,
                    "error": f'"{code}" bir ürün çizgi barkodudur (GTIN). Tekil ilaç sayımı için lütfen kutu üzerindeki 2D Karekodu (DataMatrix) okutunuz.'
                })
            is_parti, p_count, p_urun = check_is_parti_no(code_norm, df)
            if is_parti:
                return jsonify({
                    "success": False,
                    "is_parti_no": True,
                    "error": f'"{code}" bir Parti Numarasıdır ({p_urun}). Bu numara üretim grubunu temsil eder. Lütfen kutu üzerindeki 2D Karekodu (DataMatrix) okutunuz.'
                })

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

        if not target_koli and len(code_norm) >= 20:
            for q_key, r_dict in qr_dict.items():
                if len(q_key) >= 20 and (code_norm in q_key or q_key in code_norm):
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

    if not target_koli or not matched_rows:
        if len(code_norm) < 18:
            return jsonify({
                "success": False,
                "error": f'"{code}" geçerli bir 2D Karekod (DataMatrix) veya koli numarası değildir. Lütfen kutu üzerindeki karekodu okutunuz.'
            })
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
# bkst_online: None = henüz denenmedi, True = Bakanlık'tan veri alındı, False = bağlantı/veri sorunu
bkst_online = None
bkst_fetched_count = 0


_local_count_cache = {"val": 0, "ts": 0}
_local_count_lock = threading.Lock()

def _local_item_count():
    try:
        df_cache, _, _, _ = get_bkst_cache()
        return len(df_cache) if df_cache is not None else 0
    except Exception:
        return 0

def _local_item_count_cached():
    with _local_count_lock:
        now = time.monotonic()
        if now - _local_count_cache["ts"] < 30:
            return _local_count_cache["val"]
    v = _local_item_count()
    with _local_count_lock:
        _local_count_cache["val"] = v
        _local_count_cache["ts"] = time.monotonic()
    return v


def _set_bkst_state(status, message, online, fetched=0):
    global bkst_status, bkst_message, bkst_online, bkst_fetched_count
    with _state_lock:
        bkst_status = status
        bkst_message = message
        bkst_online = online
        bkst_fetched_count = fetched


def _mark_offline(reason):
    """Bakanlığa ulaşılamadı / veri gelmedi: yerel veriler korunur, sistem DEAKTİF işaretlenir."""
    local_cnt = _local_item_count()
    msg = (f"⚠️ SİSTEM DEAKTİF: {reason} Bakanlık verisi güncellenemedi. "
           f"Yerel veritabanındaki son kayıtlı {local_cnt} adet stok kullanılıyor.")
    logger.warning(f"BKST offline: {reason} (local={local_cnt})")
    _set_bkst_state("offline", msg, False, 0)


def _do_fetch_api_worker():
    global _app_bkst_synced
    logger.info("BKST fetch_api worker thread started")
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password:
        _set_bkst_state("error", "❌ HATA: Giriş bilgileri (KULLANICI_ADI / SIFRE) bulunamadı! Lütfen tekrar giriş yapın.", False)
        return

    if not check_internet_connection():
        _mark_offline("İnternet bağlantısı yok.")
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

        # 1) Ana sayfa
        try:
            r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(5, 10))
        except Exception as e_home:
            _mark_offline(f"Bakanlık sunucusuna (bkst.tarbil.gov.tr) ulaşılamıyor ({type(e_home).__name__}).")
            return
        if r_home.status_code != 200:
            _mark_offline(f"Bakanlık sunucusu hata döndürdü (HTTP {r_home.status_code}).")
            return

        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        # 2) Giriş
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf",
                                 data={"tcNo": username, "sifre": password, "__RequestVerificationToken": token1},
                                 verify=SSL_VERIFY, timeout=(5, 15))
        if res_login.status_code != 200:
            _mark_offline(f"Bakanlık giriş servisi yanıt vermiyor (HTTP {res_login.status_code}).")
            return
        if "0" not in res_login.text:
            _set_bkst_state("error", "❌ HATA: Bakanlık kullanıcı adı veya şifreniz yanlış! Sistem deaktif.", False)
            return

        # 3) Stok sayfası + GLN
        r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(5, 10))
        token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
        token2 = token2_match.group(1) if token2_match else token1

        gln_guid = address_id
        if not gln_guid or len(gln_guid) < 32:
            for f_type in ["0", "1", "2"]:
                try:
                    r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN",
                                         data={"FirmType": f_type, "__RequestVerificationToken": token2},
                                         verify=SSL_VERIFY, timeout=(5, 10))
                    if r_gln.status_code == 200:
                        gln_data = r_gln.json()
                        if isinstance(gln_data, list) and len(gln_data) > 0:
                            val = str(gln_data[0].get("Value") or "").strip()
                            if val and len(val) >= 32:
                                gln_guid = val
                                break
                except Exception:
                    pass
                if gln_guid and len(gln_guid) >= 32:
                    break
        if not gln_guid or len(gln_guid) < 32:
            gln_guid = address_id or ""
            if not gln_guid or len(gln_guid) < 32:
                _mark_offline("Bakanlık şirket adres (GLN) bilgisi bulunamadı. Lütfen tekrar giriş yapınız.")
                return

        # 4) Stok listesi
        r_grid = session.post("https://bkst.tarbil.gov.tr/Main/GetStockList",
                              data={"CompanyAddressId": gln_guid, "Gtin": "", "__RequestVerificationToken": token2},
                              verify=SSL_VERIFY, timeout=(5, 15))
        if r_grid.status_code != 200:
            _mark_offline(f"Bakanlık stok listesi servisi yanıt vermiyor (HTTP {r_grid.status_code}).")
            return
        try:
            grid_json = r_grid.json()
        except Exception:
            _mark_offline("Bakanlık stok listesi geçersiz yanıt döndürdü (oturum düşmüş olabilir).")
            return
        gtin_list = grid_json.get("Data", []) if isinstance(grid_json, dict) else []

        # 5) Detaylar
        all_rows = []
        detail_errors = 0
        for g_item in gtin_list:
            gtin_code = g_item.get("BARKOD")
            prod_name = g_item.get("URUNADI")
            if not gtin_code:
                continue
            try:
                session.post("https://bkst.tarbil.gov.tr/Main/GetViewReport",
                             data={"gtin": gtin_code, "gln": gln_guid, "__RequestVerificationToken": token2},
                             verify=SSL_VERIFY, timeout=(5, 10))
                r_detail = session.post("https://bkst.tarbil.gov.tr/Main/GetStockDetailList",
                                        data={"CompanyAddressId": gln_guid, "Gtin": gtin_code, "__RequestVerificationToken": token2},
                                        verify=SSL_VERIFY, timeout=(5, 15))
            except Exception:
                detail_errors += 1
                continue
            if r_detail.status_code != 200:
                detail_errors += 1
                continue
            try:
                d_items = r_detail.json()
            except Exception:
                detail_errors += 1
                continue
            if isinstance(d_items, list):
                for item in d_items:
                    koli = item.get("PAKETNO") or item.get("KOLINO") or item.get("PALETNO") or ""
                    all_rows.append({
                        "Koli Numarası": koli,
                        "Ürün Adı": prod_name or item.get("URUNADI") or "",
                        "Karekod": item.get("KAREKOD") or item.get("HAMKAREKOD") or "",
                        "Gtin Numarası": item.get("BARKOD") or gtin_code or "",
                        "Gtin / Barkod": item.get("BARKOD") or gtin_code or "",
                        "gtin": item.get("BARKOD") or gtin_code or "",
                        "Seri Numarası": item.get("SERINO") or "",
                        "Parti Numarası": item.get("SARJNO") or "",
                        "Palet Numarası": item.get("PALETNO") or "",
                        "Üretim Tarihi": item.get("URETIMTARIHI") or "",
                        "Son Kullanma Tarihi": item.get("SKT") or ""
                    })

        fetched = len(all_rows)
        if fetched == 0:
            # 0 adet veri = Bakanlık tarafında sorun. Yerel veriyi SİLMEYİZ.
            if not gtin_list:
                _mark_offline("Bakanlık'tan 0 adet stok verisi geldi (stok listesi boş döndü).")
            else:
                _mark_offline(f"Bakanlık'tan 0 adet karekod detayı alınabildi ({detail_errors} istek başarısız).")
            return

        try:
            save_bkst_data_to_db(pd.DataFrame(all_rows), username)
        except Exception as db_err:
            logger.error(f"Error saving to db, attempting auto-repair: {db_err}", exc_info=True)
            try:
                ensure_db_schema()
                save_bkst_data_to_db(pd.DataFrame(all_rows), username)
            except Exception as retry_err:
                logger.error(f"Failed to save to db after repair: {retry_err}", exc_info=True)
                _mark_offline(f"Bakanlık verileri çekildi ancak veritabanı kayıt hatası oluştu ({type(retry_err).__name__}).")
                return

        with _state_lock:
            _app_bkst_synced = True
        msg = f"🟢 SİSTEM AKTİF: Bakanlık'tan {fetched} adet stok verisi başarıyla çekildi."
        if detail_errors:
            msg += f" ({detail_errors} ürün detayı alınamadı.)"
        _set_bkst_state("done", msg, True, fetched)
        logger.info(f"BKST fetch_api worker finished: fetched={fetched}, detail_errors={detail_errors}")

    except Exception as e:
        logger.error(f"BKST fetch_api worker error: {e}", exc_info=True)
        _mark_offline(f"Bakanlık bağlantı hatası ({type(e).__name__}).")


@app.route('/api/bkst/fetch_api', methods=['POST'])
def bkst_fetch_api_start():
    global _fetch_future, bkst_status, bkst_message
    with _state_lock:
        if _fetch_future and not _fetch_future.done():
            return jsonify({"success": True, "status": "already_running",
                            "message": "Zaten devam eden bir veri çekme işlemi var, sonucu bekleniyor."})
        bkst_status = "fetching"
        bkst_message = "⚡ Bakanlık verileri API üzerinden çekiliyor..."
        _fetch_future = _fetch_executor.submit(_do_fetch_api_worker)
    return jsonify({"success": True, "status": "started", "message": "Veri çekme işlemi başlatıldı."})


def _bkst_state_payload():
    with _state_lock:
        running = bool(_fetch_future and not _fetch_future.done())
        return {
            "running": running,
            "status": bkst_status,
            "message": bkst_message,
            "online": bkst_online,
            "fetched_count": bkst_fetched_count,
        }


@app.route('/api/bkst/fetch_status', methods=['GET'])
def bkst_fetch_status():
    payload = _bkst_state_payload()
    payload["local_count"] = _local_item_count_cached()
    return jsonify(payload)

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
            raw_rows = df_clean.to_dict(orient="records")
            for r in raw_rows:
                gtin_val = r.get('Gtin Numarası') or r.get('Gtin / Barkod') or r.get('gtin') or r.get('GTIN') or r.get('BARCODE') or r.get('barkod') or ''
                karekod_str = r.get('Karekod') or r.get('tam_karekod') or r.get('ham_karekod') or r.get('KAREKOD') or ''
                if (not gtin_val or gtin_val in ('-', 'None', 'nan', 'null', 'BELİRSİZ')) and karekod_str:
                    parsed = parse_gs1_qr(karekod_str)
                    if parsed and parsed.get('gtin'):
                        gtin_val = str(parsed['gtin'])
                    elif karekod_str.startswith('01') and len(karekod_str) >= 16:
                        gtin_val = karekod_str[2:16]
                    else:
                        m14 = re.findall(r'\d{14}', karekod_str)
                        if m14:
                            gtin_val = m14[0]
                r['Gtin Numarası'] = gtin_val
                r['Gtin / Barkod'] = gtin_val
                r['gtin'] = gtin_val
                r['GTIN'] = gtin_val
                rows.append(r)

        total_count = len(rows)
        page_param = request.args.get('page')
        size_param = request.args.get('size')

        if page_param is not None or size_param is not None:
            try:
                page = max(1, int(page_param or 1))
            except Exception:
                page = 1
            try:
                size = min(2000, max(1, int(size_param or 500)))
            except Exception:
                size = 500
            start_idx = (page - 1) * size
            end_idx = start_idx + size
            paged_products = rows[start_idx:end_idx]
            has_more = end_idx < total_count
            return jsonify({
                "success": True,
                "products": paged_products,
                "total": total_count,
                "page": page,
                "size": size,
                "has_more": has_more
            })

        return jsonify({
            "success": True,
            "products": rows,
            "total": total_count,
            "page": 1,
            "size": total_count,
            "has_more": False
        })
    except Exception as e:
        logger.error(f"api_depo_stoklari error: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "products": [],
            "total": 0,
            "page": 1,
            "size": 0,
            "has_more": False,
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

        df, qr_map, gtin_map, koli_map = get_bkst_cache()
        if df is None or df.empty:
            return jsonify({
                'success': False,
                'error': 'Bakanlık depo verisi bulunamadı. Lütfen önce verileri çekin.'
            })

        # Koli / Palet toplu okutma kontrolü
        koli_key = barkod_norm.upper()
        if koli_map and (koli_key in koli_map or barkod_norm in koli_map):
            koli_items = koli_map.get(koli_key) or koli_map.get(barkod_norm) or []
            if koli_items:
                conn = sqlite3.connect(DB_PATH, timeout=30.0)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                c = conn.cursor()
                if username:
                    c.execute('SELECT ham_karekod FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ""', (username,))
                else:
                    c.execute('SELECT ham_karekod FROM cikis_kayitlari')
                existing_set = set((row[0] or "").casefold() for row in c.fetchall() if row[0])

                tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                insert_rows = []
                inserted_records = []
                last_inserted = None
                has_any_tekrar = False

                for r_item in koli_items:
                    item_qr = normalize_qr(str(r_item.get("Karekod", "")))
                    if not item_qr:
                        continue
                    item_is_tekrar = item_qr.casefold() in existing_set
                    if item_is_tekrar:
                        has_any_tekrar = True
                    existing_set.add(item_qr.casefold())

                    u_adi = str(r_item.get('Ürün Adı', '')).strip()
                    b_col = str(r_item.get('Gtin Numarası') or r_item.get('Gtin / Barkod') or r_item.get('gtin') or '').strip()
                    k_no  = str(r_item.get('Koli Numarası', '')).strip()
                    s_no  = str(r_item.get('Seri Numarası', '')).strip()
                    p_no  = str(r_item.get('Parti Numarası', '')).strip()
                    pal_no = str(r_item.get('Palet Numarası', '')).strip()
                    ur_t  = str(r_item.get('Üretim Tarihi', '')).strip()
                    sk_t  = str(r_item.get('Son Kullanma Tarihi', '')).strip()

                    insert_rows.append((tarih, u_adi, b_col, k_no, s_no, p_no, pal_no, ur_t, sk_t, item_qr, 1 if item_is_tekrar else 0, username))
                    rec = {
                        'tarih': tarih, 'urun_adi': u_adi, 'barkod': b_col, 'koli_no': k_no,
                        'seri_no': s_no, 'parti_no': p_no, 'palet_no': pal_no, 'uretim_tarihi': ur_t,
                        'skt': sk_t, 'ham_karekod': item_qr, 'tekrar_uyari': 1 if item_is_tekrar else 0
                    }
                    inserted_records.append(rec)
                    last_inserted = rec

                if insert_rows:
                    c.executemany('''INSERT INTO cikis_kayitlari
                        (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                         uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', insert_rows)

                    c.execute("SELECT max(id) FROM cikis_kayitlari")
                    max_id = c.fetchone()[0] or len(insert_rows)
                    first_id = max_id - len(insert_rows) + 1
                    for idx, r_rec in enumerate(inserted_records):
                        r_rec['id'] = first_id + idx

                    # Kalıcı satış ve istatistik arşivine de ekle
                    c.executemany('''INSERT INTO satis_arsivi
                        (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                         uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, durum)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CIKIS_YAPILDI')''',
                        insert_rows)
                    conn.commit()
                conn.close()

                return jsonify({
                    'success': True,
                    'is_bulk': True,
                    'tekrar_uyari': has_any_tekrar,
                    'count': len(insert_rows),
                    'message': f'{koli_key} kolisindeki {len(insert_rows)} adet ürün başarıyla çıkış yapıldı.',
                    'kayit': last_inserted,
                    'kayitlar': inserted_records
                })

        # Parti Numarası kontrolü: Parti numarası tekil kutuyu değil tüm partiyi temsil eder
        is_parti, p_count, p_urun = check_is_parti_no(barkod_norm, df)
        if is_parti:
            return jsonify({
                'success': False,
                'is_parti_no': True,
                'error': f'"{barkod_raw}" bir Parti Numarasıdır ({p_urun} - Depoda bu partiye ait {p_count} adet ürün var). Parti numarası üretimdeki bir grubu temsil eder ve tekil bir kutuya ait değildir. Çıkış yapabilmek için lütfen kutu üzerindeki Karekodu veya Seri Numarasını okutunuz.'
            })

        # GTIN Barkod kontrolü: GTIN tekil kutuyu değil genel ürün tanımını temsil eder
        is_gtin, g_count, g_urun = check_is_gtin_no(barkod_norm, df, gtin_map)
        if is_gtin:
            return jsonify({
                'success': False,
                'is_gtin_no': True,
                'error': f'"{barkod_raw}" bir GTIN / Çizgi Barkod numarasıdır ({g_urun} - Depoda bu barkoda ait {g_count} adet ürün var). Bu numara tekil bir kutuya ait karekod değildir. Çıkış yapabilmek için lütfen kutu üzerindeki Karekodu (DataMatrix) veya Seri Numarasını okutunuz.'
            })

        # Tekil ürün kontrolü: Önce ürünü Bakanlık deposundan tam eşleştir
        match_row = resolve_product_from_cache(barkod_norm, df, qr_map, gtin_map)
        if match_row is None:
            return jsonify({
                'success': False,
                'error': f'"{barkod_raw}" barkoduna / seri numarasına ait ürün Bakanlık depo verisinde bulunamadı.'
            })

        tarih         = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        urun_adi      = str(match_row.get('Ürün Adı', '')).strip()
        barkod_col    = str(match_row.get('Gtin Numarası') or match_row.get('Gtin / Barkod') or match_row.get('gtin') or match_row.get('BARKOD') or match_row.get('barkod') or '').strip()
        real_karekod  = str(match_row.get('Karekod') or '').strip() or barkod_norm
        koli_no       = str(match_row.get('Koli Numarası', '')).strip()
        seri_no       = str(match_row.get('Seri Numarası', '')).strip()
        parti_no      = str(match_row.get('Parti Numarası', '')).strip()
        palet_no      = str(match_row.get('Palet Numarası', '')).strip()
        uretim_tarihi = str(match_row.get('Üretim Tarihi', '')).strip()
        skt           = str(match_row.get('Son Kullanma Tarihi', '')).strip()

        if not barkod_col and real_karekod:
            parsed = parse_gs1_qr(real_karekod)
            if parsed and parsed.get('gtin'):
                barkod_col = str(parsed['gtin']).strip()
        if not seri_no and real_karekod:
            parsed = parse_gs1_qr(real_karekod)
            if parsed and parsed.get('seri_no'):
                seri_no = str(parsed['seri_no']).strip()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()

        # Çıkış kayıtlarında bu ürünün daha önce çıkış yapılıp yapılmadığını denetle:
        # 1) Gerçek tam karekod ile eşleşme
        # 2) Okutulan ham değer ile eşleşme
        # 3) Aynı Seri Numarası + GTIN ile eşleşme (kullanıcı ister karekod ister seri no okutmuş olsun yakalar)
        if username:
            c.execute('''
                SELECT id, tarih, urun_adi, ham_karekod, seri_no
                FROM (
                    SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod FROM cikis_kayitlari
                    UNION ALL
                    SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod FROM satis_arsivi
                )
                WHERE (kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")
                  AND (
                      LOWER(ham_karekod) = LOWER(?)
                      OR LOWER(ham_karekod) = LOWER(?)
                      OR (
                          seri_no IS NOT NULL AND seri_no != ""
                          AND LOWER(seri_no) = LOWER(?)
                          AND (? = "" OR barkod = ? OR barkod IS NULL OR barkod = "")
                      )
                  )
                ORDER BY id ASC LIMIT 1
            ''', (username, real_karekod, barkod_norm, seri_no, barkod_col, barkod_col))
        else:
            c.execute('''
                SELECT id, tarih, urun_adi, ham_karekod, seri_no
                FROM (
                    SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod FROM cikis_kayitlari
                    UNION ALL
                    SELECT id, tarih, urun_adi, ham_karekod, seri_no, kullanici_adi, barkod FROM satis_arsivi
                )
                WHERE (
                    LOWER(ham_karekod) = LOWER(?)
                    OR LOWER(ham_karekod) = LOWER(?)
                    OR (
                        seri_no IS NOT NULL AND seri_no != ""
                        AND LOWER(seri_no) = LOWER(?)
                        AND (? = "" OR barkod = ? OR barkod IS NULL OR barkod = "")
                    )
                )
                ORDER BY id ASC LIMIT 1
            ''', (real_karekod, barkod_norm, seri_no, barkod_col, barkod_col))

        existing = c.fetchone()
        is_tekrar = False
        ex_tarih = ""
        if existing:
            is_tekrar = True
            ex_tarih = existing[1] or ""

        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        # Karekod sütununa kullanıcının girdiği seri no yerine ürünün GERÇEK TAM KAREKODU kaydedilir
        c.execute('''INSERT INTO cikis_kayitlari
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username))
        new_id = c.lastrowid

        # Kalıcı satış & istatistik arşivine de ekle
        c.execute('''INSERT INTO satis_arsivi
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi, durum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CIKIS_YAPILDI')''',
            (tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
             uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username))
        conn.commit()
        conn.close()

        res_obj = {
            'success': True,
            'tekrar_uyari': is_tekrar,
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
                'ham_karekod': real_karekod,
                'tekrar_uyari': 1 if is_tekrar else 0
            }
        }
        if is_tekrar:
            res_obj['warning'] = f'Bu ürün daha önce depodan çıkarılmış! (Önceki çıkış tarihi: {ex_tarih})'
        return jsonify(res_obj)

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
            c.execute('SELECT ham_karekod, seri_no, barkod FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = ""', (username,))
        else:
            c.execute('SELECT ham_karekod, seri_no, barkod FROM cikis_kayitlari')
        existing_rows = c.fetchall()
        existing_qr_set = set((row[0] or "").casefold() for row in existing_rows if row[0])
        existing_seri_set = set((row[1] or "").casefold() for row in existing_rows if row[1])

        insert_rows = []
        added_count = 0
        already_count = 0
        tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for barkod_raw in items:
            raw_str = str(barkod_raw).strip()
            if not raw_str:
                continue

            # Parti Numarası tekil ürün olarak listeye eklenmemelidir
            is_parti, _, _ = check_is_parti_no(raw_str, df)
            if is_parti:
                logger.warning(f"cikis_toplu_ekle: '{raw_str}' parti numarası olduğu için tekil çıkışa eklenmedi.")
                continue

            # GTIN / Barkod tekil ürün olarak listeye eklenmemelidir
            is_gtin, _, _ = check_is_gtin_no(raw_str, df, gtin_map)
            if is_gtin:
                logger.warning(f"cikis_toplu_ekle: '{raw_str}' GTIN numarası olduğu için tekil çıkışa eklenmedi.")
                continue

            match_row = resolve_product_from_cache(raw_str, df, qr_map, gtin_map)
            if match_row:
                real_karekod = str(match_row.get('Karekod') or '').strip() or normalize_qr(raw_str)
                urun_adi = str(match_row.get('Ürün Adı', '')).strip()
                barkod_col = str(match_row.get('Gtin Numarası') or match_row.get('Gtin / Barkod') or match_row.get('gtin') or '').strip()
                koli_no = str(match_row.get('Koli Numarası', '')).strip()
                seri_no = str(match_row.get('Seri Numarası', '')).strip()
                parti_no = str(match_row.get('Parti Numarası', '')).strip()
                palet_no = str(match_row.get('Palet Numarası', '')).strip()
                uretim_tarihi = str(match_row.get('Üretim Tarihi', '')).strip()
                skt = str(match_row.get('Son Kullanma Tarihi', '')).strip()
            else:
                real_karekod = normalize_qr(raw_str)
                parsed = parse_gs1_qr(real_karekod)
                urun_adi = "Tanımsız Ürün"
                barkod_col = str(parsed.get('gtin') or '').strip()
                koli_no = ""
                seri_no = str(parsed.get('seri_no') or '').strip()
                parti_no = str(parsed.get('parti_no') or '').strip()
                palet_no = ""
                uretim_tarihi = str(parsed.get('uretim_tarihi') or '').strip()
                skt = str(parsed.get('skt') or '').strip()

            is_tekrar = (real_karekod.casefold() in existing_qr_set) or (seri_no and seri_no.casefold() in existing_seri_set)
            if is_tekrar:
                already_count += 1

            existing_qr_set.add(real_karekod.casefold())
            if seri_no:
                existing_seri_set.add(seri_no.casefold())

            insert_rows.append((tarih, urun_adi, barkod_col, koli_no, seri_no, parti_no, palet_no,
                                uretim_tarihi, skt, real_karekod, 1 if is_tekrar else 0, username))
            added_count += 1

        if insert_rows:
            c.executemany('''INSERT INTO cikis_kayitlari
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', insert_rows)
            # Kalıcı satış & istatistik arşivine de ekle
            c.executemany('''INSERT INTO satis_arsivi
                (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no,
                 uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                insert_rows)
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
        username, _, _, _ = read_bkst_credentials()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if username:
            c.execute('SELECT * FROM cikis_kayitlari WHERE kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "" ORDER BY id DESC', (username,))
        else:
            c.execute('SELECT * FROM cikis_kayitlari ORDER BY id DESC')
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        clean_rows = []
        for row in rows:
            clean_row = {}
            for k, v in row.items():
                if k == 'tekrar_uyari':
                    clean_row['tekrar_uyari'] = 1 if (v in (1, '1', True)) else 0
                else:
                    clean_row[k] = "" if v is None else str(v)
            if not clean_row.get('barkod') and clean_row.get('ham_karekod'):
                parsed = parse_gs1_qr(clean_row['ham_karekod'])
                if parsed and parsed.get('gtin'):
                    clean_row['barkod'] = str(parsed['gtin'])
            clean_row['gtin'] = clean_row.get('barkod', '')
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

    if 'barkod' in df.columns and 'ham_karekod' in df.columns:
        for idx, row in df.iterrows():
            b_val = str(row.get('barkod') or '').strip()
            if not b_val and pd.notna(row.get('ham_karekod')):
                parsed = parse_gs1_qr(str(row['ham_karekod']))
                if parsed and parsed.get('gtin'):
                    df.at[idx, 'barkod'] = str(parsed['gtin'])

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

# ── İSTATİSTİKLER & SATIŞ RAPORLARI API ──────────────────────────────────────
@app.route('/api/istatistikler/ozet', methods=['GET'])
def api_istatistikler_ozet():
    try:
        yil = request.args.get('yil', 'tum').strip()
        baslangic = request.args.get('baslangic', '').strip()
        bitis = request.args.get('bitis', '').strip()
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Mevcut tüm yılları topla
        c.execute("""
            SELECT DISTINCT substr(tarih, 1, 4) as yr
            FROM satis_arsivi
            WHERE tarih IS NOT NULL AND length(tarih) >= 4 AND substr(tarih, 1, 4) GLOB '[1-2][0-9][0-9][0-9]'
            ORDER BY yr DESC
        """)
        db_years = [r['yr'] for r in c.fetchall() if r['yr']]
        default_years = ['2026', '2025', '2024', '2023']
        all_years = sorted(list(set(db_years + default_years)), reverse=True)

        where_parts = []
        params = []
        if username:
            where_parts.append('(kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")')
            params.append(username)
        if yil and yil != 'tum':
            where_parts.append("substr(tarih, 1, 4) = ?")
            params.append(yil)
        if baslangic:
            where_parts.append("substr(tarih, 1, 10) >= ?")
            params.append(baslangic)
        if bitis:
            where_parts.append("substr(tarih, 1, 10) <= ?")
            params.append(bitis)

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # KPI Özeti
        c.execute(f"""
            SELECT 
                COUNT(*) as toplam_adet,
                COUNT(DISTINCT urun_adi) as tekil_urun,
                COUNT(CASE WHEN tekrar_uyari = 1 THEN 1 END) as tekrar_adet,
                COUNT(CASE WHEN koli_no IS NOT NULL AND koli_no != '' THEN 1 END) as koli_adet,
                COUNT(CASE WHEN palet_no IS NOT NULL AND palet_no != '' THEN 1 END) as palet_adet,
                COUNT(DISTINCT substr(tarih, 1, 10)) as aktif_gun,
                MIN(tarih) as ilk_tarih,
                MAX(tarih) as son_tarih
            FROM satis_arsivi
            {where_sql}
        """, params)
        kpi_raw = dict(c.fetchone() or {})
        toplam_adet = kpi_raw.get('toplam_adet') or 0
        tekil_urun = kpi_raw.get('tekil_urun') or 0
        tekrar_adet = kpi_raw.get('tekrar_adet') or 0
        koli_adet = kpi_raw.get('koli_adet') or 0
        palet_adet = kpi_raw.get('palet_adet') or 0
        tekil_adet = max(0, toplam_adet - koli_adet - palet_adet)
        aktif_gun = kpi_raw.get('aktif_gun') or 0
        gunluk_ort = round(toplam_adet / max(1, aktif_gun), 1) if aktif_gun else 0
        tekrar_orani = round((tekrar_adet / max(1, toplam_adet)) * 100, 1)

        # Lider Ürün
        c.execute(f"""
            SELECT urun_adi, COUNT(*) as adet
            FROM satis_arsivi
            {where_sql}
            GROUP BY urun_adi
            ORDER BY adet DESC
            LIMIT 1
        """, params)
        lider_row = c.fetchone()
        lider_urun = lider_row['urun_adi'] if lider_row else "Veri Yok"
        lider_adet = lider_row['adet'] if lider_row else 0

        # Aylık Dağılım (12 Ay: Ocak - Aralık)
        ay_isimleri = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        aylik_sayilar = [0] * 12
        c.execute(f"""
            SELECT substr(tarih, 6, 2) as ay_no, COUNT(*) as adet
            FROM satis_arsivi
            {where_sql}
            GROUP BY ay_no
            ORDER BY ay_no ASC
        """, params)
        for r in c.fetchall():
            ay_str = r['ay_no']
            if ay_str and ay_str.isdigit():
                idx = int(ay_str) - 1
                if 0 <= idx < 12:
                    aylik_sayilar[idx] = r['adet']

        # Yıllık Karşılaştırma (Tüm Yıllar)
        u_filter = "WHERE (kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = '')" if username else ""
        u_p = [username] if username else []
        c.execute(f"""
            SELECT substr(tarih, 1, 4) as yr, COUNT(*) as adet
            FROM satis_arsivi
            {u_filter}
            GROUP BY yr
            HAVING yr IS NOT NULL AND length(yr) = 4 AND yr GLOB '[1-2][0-9][0-9][0-9]'
            ORDER BY yr ASC
        """, u_p)
        yillik_dict = {str(y): 0 for y in all_years}
        for r in c.fetchall():
            yillik_dict[str(r['yr'])] = r['adet']
        yillik_labels = sorted(yillik_dict.keys())
        yillik_values = [yillik_dict[y] for y in yillik_labels]

        # En Çok Satan Ürünler (Top 30)
        c.execute(f"""
            SELECT 
                urun_adi, 
                COALESCE(barkod, '') as barkod,
                COUNT(*) as adet,
                COUNT(CASE WHEN koli_no != '' THEN 1 END) as koli_sayisi,
                COUNT(CASE WHEN palet_no != '' THEN 1 END) as palet_sayisi,
                MAX(tarih) as son_cikis
            FROM satis_arsivi
            {where_sql}
            GROUP BY urun_adi, barkod
            ORDER BY adet DESC
            LIMIT 30
        """, params)
        top_urunler = []
        for r in c.fetchall():
            ad = r['adet']
            pct = round((ad / max(1, toplam_adet)) * 100, 1)
            top_urunler.append({
                'urun_adi': r['urun_adi'] or 'İsimsiz Ürün',
                'barkod': r['barkod'] or '-',
                'adet': ad,
                'koli_sayisi': r['koli_sayisi'],
                'palet_sayisi': r['palet_sayisi'],
                'yuzde': pct,
                'son_cikis': r['son_cikis'] or ''
            })

        # Haftanın Günleri
        gunluk_dagilim = [0] * 7
        c.execute(f"""
            SELECT strftime('%w', tarih) as gun_no, COUNT(*) as adet
            FROM satis_arsivi
            {where_sql}
            GROUP BY gun_no
        """, params)
        for r in c.fetchall():
            g = r['gun_no']
            if g is not None and str(g).isdigit():
                idx = int(g)
                if 0 <= idx < 7:
                    gunluk_dagilim[idx] = r['adet']
        hafta_gunleri = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
        hafta_degerleri = gunluk_dagilim[1:] + gunluk_dagilim[:1]

        # Parti Dağılımı
        parti_where = (where_sql + " AND parti_no IS NOT NULL AND parti_no != ''") if where_sql else "WHERE parti_no IS NOT NULL AND parti_no != ''"
        c.execute(f"""
            SELECT 
                COALESCE(parti_no, 'Belirtilmemiş') as parti,
                urun_adi,
                COUNT(*) as adet,
                MAX(skt) as skt
            FROM satis_arsivi
            {parti_where}
            GROUP BY parti, urun_adi
            ORDER BY adet DESC
            LIMIT 20
        """, params)
        top_partiler = [dict(r) for r in c.fetchall()]

        # Depo Stoğu Karşılaştırması
        c.execute("SELECT COUNT(*) as depo_toplam, COUNT(DISTINCT urun_adi) as depo_kalem FROM bkst_depo_verileri")
        depo_row = dict(c.fetchone() or {})

        conn.close()

        return jsonify({
            'success': True,
            'filtre': {
                'yil': yil,
                'baslangic': baslangic,
                'bitis': bitis
            },
            'mevcut_yillar': all_years,
            'kpi': {
                'toplam_cikis': toplam_adet,
                'tekil_urun_sayisi': tekil_urun,
                'lider_urun': lider_urun,
                'lider_adet': lider_adet,
                'koli_adet': koli_adet,
                'palet_adet': palet_adet,
                'tekil_adet': tekil_adet,
                'koli_orani': round((koli_adet / max(1, toplam_adet)) * 100, 1),
                'tekil_orani': round((tekil_adet / max(1, toplam_adet)) * 100, 1),
                'gunluk_ortalama': gunluk_ort,
                'aktif_gun': aktif_gun,
                'tekrar_adet': tekrar_adet,
                'tekrar_orani': tekrar_orani,
                'depo_mevcut_stok': depo_row.get('depo_toplam', 0),
                'depo_kalem_sayisi': depo_row.get('depo_kalem', 0)
            },
            'aylik_grafik': {
                'etiketler': ay_isimleri,
                'veriler': aylik_sayilar
            },
            'yillik_grafik': {
                'etiketler': yillik_labels,
                'veriler': yillik_values
            },
            'haftalik_grafik': {
                'etiketler': hafta_gunleri,
                'veriler': hafta_degerleri
            },
            'en_cok_satanlar': top_urunler,
            'parti_dagilimi': top_partiler
        })
    except Exception as e:
        logger.error(f"api_istatistikler_ozet error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/excel_indir', methods=['GET'])
def api_istatistikler_excel_indir():
    try:
        yil = request.args.get('yil', 'tum').strip()
        baslangic = request.args.get('baslangic', '').strip()
        bitis = request.args.get('bitis', '').strip()
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        where_parts = []
        params = []
        if username:
            where_parts.append('(kullanici_adi = ? OR kullanici_adi IS NULL OR kullanici_adi = "")')
            params.append(username)
        if yil and yil != 'tum':
            where_parts.append("substr(tarih, 1, 4) = ?")
            params.append(yil)
        if baslangic:
            where_parts.append("substr(tarih, 1, 10) >= ?")
            params.append(baslangic)
        if bitis:
            where_parts.append("substr(tarih, 1, 10) <= ?")
            params.append(bitis)

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # Fetch detail rows
        c.execute(f"""
            SELECT id, tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari
            FROM satis_arsivi
            {where_sql}
            ORDER BY id DESC
        """, params)
        detail_rows = [dict(r) for r in c.fetchall()]

        # Fetch top products
        c.execute(f"""
            SELECT urun_adi, COALESCE(barkod, '') as barkod, COUNT(*) as adet,
                   COUNT(CASE WHEN koli_no != '' THEN 1 END) as koli_adet,
                   COUNT(CASE WHEN koli_no = '' AND palet_no = '' THEN 1 END) as tekil_adet,
                   MAX(tarih) as son_cikis
            FROM satis_arsivi
            {where_sql}
            GROUP BY urun_adi, barkod
            ORDER BY adet DESC
        """, params)
        top_products = [dict(r) for r in c.fetchall()]

        # Monthly counts
        c.execute(f"""
            SELECT substr(tarih, 6, 2) as ay_no, COUNT(*) as adet
            FROM satis_arsivi
            {where_sql}
            GROUP BY ay_no
            ORDER BY ay_no ASC
        """, params)
        monthly_map = {r['ay_no']: r['adet'] for r in c.fetchall()}

        conn.close()

        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb = openpyxl.Workbook()
        
        # Style helpers
        font_title = Font(name='Segoe UI', size=14, bold=True, color='0F172A')
        font_sub = Font(name='Segoe UI', size=10, italic=True, color='64748B')
        font_hdr = Font(name='Segoe UI', size=10, bold=True, color='FFFFFF')
        font_bold = Font(name='Segoe UI', size=10, bold=True, color='0F172A')
        font_norm = Font(name='Segoe UI', size=10, color='1E293B')
        fill_hdr = PatternFill(start_color='1E293B', end_color='1E293B', fill_type='solid')
        fill_zebra = PatternFill(start_color='F8FAFC', end_color='F8FAFC', fill_type='solid')
        border_thin = Side(border_style='thin', color='CBD5E1')
        cell_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

        # SHEET 1: ÖZET RAPOR
        ws1 = wb.active
        ws1.title = "Genel Özet"
        ws1.views.sheetView[0].showGridLines = True
        ws1['A1'] = "QR COMPARE - SATIŞ VE ÇIKIŞ İSTATİSTİK RAPORU"
        ws1['A1'].font = font_title
        period_str = f"Filtre Dönemi: Yıl: {yil if yil != 'tum' else 'Tüm Yıllar'}"
        if baslangic or bitis:
            period_str += f" | {baslangic or 'İlk'} - {bitis or 'Son'}"
        ws1['A2'] = f"{period_str} | Rapor Tarihi: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        ws1['A2'].font = font_sub

        total_cnt = len(detail_rows)
        kpi_data = [
            ("Toplam Çıkış Adedi (Kutu)", total_cnt),
            ("Çıkış Yapılan Kalem Sayısı", len(top_products)),
            ("Koli İle Çıkış Yapılan Adet", sum(r.get('koli_adet', 0) for r in top_products)),
            ("Tekil Kutu Çıkış Adedi", sum(r.get('tekil_adet', 0) for r in top_products)),
            ("En Çok Satan Ürün", top_products[0]['urun_adi'] if top_products else "-"),
            ("En Çok Satan Ürün Satış Adedi", top_products[0]['adet'] if top_products else 0),
        ]
        ws1.cell(row=4, column=1, value="METRİK").fill = fill_hdr
        ws1.cell(row=4, column=1).font = font_hdr
        ws1.cell(row=4, column=2, value="DEĞER").fill = fill_hdr
        ws1.cell(row=4, column=2).font = font_hdr

        for idx, (m_label, m_val) in enumerate(kpi_data, start=5):
            c1 = ws1.cell(row=idx, column=1, value=m_label)
            c2 = ws1.cell(row=idx, column=2, value=m_val)
            c1.font = font_bold
            c2.font = font_norm
            c1.border = cell_border
            c2.border = cell_border
            if idx % 2 == 1:
                c1.fill = fill_zebra
                c2.fill = fill_zebra

        ws1.column_dimensions['A'].width = 35
        ws1.column_dimensions['B'].width = 25

        # SHEET 2: ÜRÜN BAZLI SATIŞLAR
        ws2 = wb.create_sheet(title="Ürün Bazlı Satışlar")
        ws2.views.sheetView[0].showGridLines = True
        hdrs2 = ["Sıra", "Ürün Adı", "GTIN / Barkod", "Toplam Adet", "Pazar Payı (%)", "Koli Çıkışı", "Tekil Çıkış", "Son Çıkış Tarihi"]
        for c_idx, h_text in enumerate(hdrs2, start=1):
            cell = ws2.cell(row=1, column=c_idx, value=h_text)
            cell.font = font_hdr
            cell.fill = fill_hdr
            cell.alignment = Alignment(horizontal='center' if c_idx in [1, 4, 5, 6, 7] else 'left')

        for r_idx, p in enumerate(top_products, start=2):
            pct = round((p['adet'] / max(1, total_cnt)) * 100, 1)
            vals = [
                r_idx - 1,
                p['urun_adi'],
                p['barkod'],
                p['adet'],
                f"%{pct}",
                p.get('koli_adet', 0),
                p.get('tekil_adet', 0),
                p.get('son_cikis', '')
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws2.cell(row=r_idx, column=col_idx, value=val)
                cell.font = font_norm
                cell.border = cell_border
                if r_idx % 2 == 1:
                    cell.fill = fill_zebra

        ws2.column_dimensions['A'].width = 8
        ws2.column_dimensions['B'].width = 35
        ws2.column_dimensions['C'].width = 20
        ws2.column_dimensions['D'].width = 15
        ws2.column_dimensions['E'].width = 15
        ws2.column_dimensions['F'].width = 15
        ws2.column_dimensions['G'].width = 15
        ws2.column_dimensions['H'].width = 22

        # SHEET 3: AYLIK SATIŞ DAĞILIMI
        ws3 = wb.create_sheet(title="Aylık Dağılım")
        ws3.views.sheetView[0].showGridLines = True
        ws3.cell(row=1, column=1, value="Ay Numarası").fill = fill_hdr
        ws3.cell(row=1, column=1).font = font_hdr
        ws3.cell(row=1, column=2, value="Ay Adı").fill = fill_hdr
        ws3.cell(row=1, column=2).font = font_hdr
        ws3.cell(row=1, column=3, value="Satış Adedi (Kutu)").fill = fill_hdr
        ws3.cell(row=1, column=3).font = font_hdr
        ws3.cell(row=1, column=4, value="Yüzde Pay (%)").fill = fill_hdr
        ws3.cell(row=1, column=4).font = font_hdr

        ay_adlari = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        for m_idx in range(1, 13):
            m_str = f"{m_idx:02d}"
            cnt = monthly_map.get(m_str, 0)
            pct = round((cnt / max(1, total_cnt)) * 100, 1)
            row_num = m_idx + 1
            ws3.cell(row=row_num, column=1, value=m_idx).border = cell_border
            ws3.cell(row=row_num, column=2, value=ay_adlari[m_idx - 1]).border = cell_border
            ws3.cell(row=row_num, column=3, value=cnt).border = cell_border
            ws3.cell(row=row_num, column=4, value=f"%{pct}").border = cell_border
            if row_num % 2 == 1:
                for c_i in range(1, 5):
                    ws3.cell(row=row_num, column=c_i).fill = fill_zebra

        ws3.column_dimensions['A'].width = 14
        ws3.column_dimensions['B'].width = 18
        ws3.column_dimensions['C'].width = 22
        ws3.column_dimensions['D'].width = 16

        # SHEET 4: TÜM ÇIKIŞ KAYITLARI
        ws4 = wb.create_sheet(title="Ham Çıkış Kayıtları")
        ws4.views.sheetView[0].showGridLines = True
        hdrs4 = ["ID", "Tarih/Saat", "Ürün Adı", "GTIN/Barkod", "Seri No", "Parti No", "Koli No", "Palet No", "Üretim Tarihi", "SKT", "Tam Karekod", "Tekrar"]
        for c_idx, h_text in enumerate(hdrs4, start=1):
            cell = ws4.cell(row=1, column=c_idx, value=h_text)
            cell.font = font_hdr
            cell.fill = fill_hdr

        for r_idx, row in enumerate(detail_rows, start=2):
            vals = [
                row.get('id', ''),
                row.get('tarih', ''),
                row.get('urun_adi', ''),
                row.get('barkod', ''),
                row.get('seri_no', ''),
                row.get('parti_no', ''),
                row.get('koli_no', ''),
                row.get('palet_no', ''),
                row.get('uretim_tarihi', ''),
                row.get('skt', ''),
                row.get('ham_karekod', ''),
                'EVET' if row.get('tekrar_uyari') == 1 else 'HAYIR'
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws4.cell(row=r_idx, column=col_idx, value=val)
                cell.font = font_norm
                cell.border = cell_border

        for col_l in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L']:
            ws4.column_dimensions[col_l].width = 18
        ws4.column_dimensions['C'].width = 30
        ws4.column_dimensions['K'].width = 40

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"satis_istatistik_raporu_{yil}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        logger.error(f"api_istatistikler_excel_indir error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/ornek_gecmis_ekle', methods=['POST'])
def api_istatistikler_ornek_gecmis_ekle():
    try:
        username, _, _, _ = read_bkst_credentials()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()

        # Depodaki gerçek ürünlerden örnek al
        c.execute("SELECT DISTINCT gtin, urun_adi, parti_no, uretim_tarihi, skt FROM bkst_depo_verileri LIMIT 15")
        warehouse_prods = c.fetchall()

        if not warehouse_prods:
            warehouse_prods = [
                ("08699258170119", "Agnoround 20x1 LT", "A6868148", "05.08.2024", "05.08.2028"),
                ("08693814003187", "KORTAC 100 EC 1 LT", "K992011", "12.02.2024", "12.02.2027"),
                ("08681128520308", "EMALDA 1000 ML.", "EM88291", "10.04.2024", "10.04.2028"),
                ("08699514012014", "DORADO 500 SC 1 LT", "D77124", "01.06.2024", "01.06.2028"),
                ("08680123456789", "TEBUCONAZOLE 250 EW", "TB5521", "15.01.2024", "15.01.2027")
            ]

        demo_rows = []
        serial_counter = 500000

        for year in [2024, 2025]:
            for month in range(1, 13):
                monthly_count = random.randint(12, 32)
                for _ in range(monthly_count):
                    serial_counter += 1
                    prod = random.choice(warehouse_prods)
                    gtin, u_name, p_no, ur_t, sk_t = prod[0], prod[1], prod[2], prod[3], prod[4]
                    day = random.randint(1, 28)
                    hour = random.randint(8, 18)
                    minute = random.randint(0, 59)
                    second = random.randint(0, 59)
                    tarih = f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

                    is_koli = random.random() < 0.65
                    koli_no = f"004869{random.randint(1000000000, 9999999999)}" if is_koli else ""
                    seri_no = f"{serial_counter}"
                    tam_qr = f"DEMO_HISTORICAL_{gtin}_{seri_no}_{p_no}"

                    demo_rows.append((
                        tarih, u_name, gtin, koli_no, seri_no, p_no, "",
                        ur_t, sk_t, tam_qr, 0, username or "demo_gecmis"
                    ))

        c.executemany("""
            INSERT INTO satis_arsivi 
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, demo_rows)
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'2024 ve 2025 yıllarına ait toplam {len(demo_rows)} adet gerçekçi geçmiş satış kaydı başarıyla eklendi.',
            'eklenen_adet': len(demo_rows)
        })
    except Exception as e:
        logger.error(f"api_istatistikler_ornek_gecmis_ekle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/ornek_gecmis_temizle', methods=['POST'])
def api_istatistikler_ornek_gecmis_temizle():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM satis_arsivi WHERE ham_karekod LIKE 'DEMO_HISTORICAL_%'")
        deleted_cnt = c.rowcount
        conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'message': f'Örnek geçmiş satış verileri temizlendi ({deleted_cnt} kayıt silindi).',
            'silinen_adet': deleted_cnt
        })
    except Exception as e:
        logger.error(f"api_istatistikler_ornek_gecmis_temizle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/sifirla', methods=['POST'])
def api_istatistikler_sifirla():
    try:
        data = request.get_json(silent=True) or {}
        yil = str(data.get('yil', 'tum')).strip()
        username, _, _, _ = read_bkst_credentials()

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()

        if yil and yil != 'tum':
            c.execute("DELETE FROM satis_arsivi WHERE substr(tarih, 1, 4) = ?", (yil,))
            msg = f"{yil} yılına ait tüm satış ve istatistik kayıtları başarıyla sıfırlandı."
        else:
            c.execute("DELETE FROM satis_arsivi")
            msg = "Kalıcı satış arşivi ve tüm geçmiş istatistik verileri başarıyla sıfırlandı."

        deleted_cnt = c.rowcount
        conn.commit()
        conn.close()

        logger.info(f"api_istatistikler_sifirla: {deleted_cnt} kayıt silindi (yil={yil})")
        return jsonify({
            'success': True,
            'message': msg,
            'silinen_adet': deleted_cnt
        })
    except Exception as e:
        logger.error(f"api_istatistikler_sifirla error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/istatistikler/excel_yukle', methods=['POST'])
def api_istatistikler_excel_yukle():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Lütfen bir Excel dosyası seçin.'})
        f = request.files['file']
        if not f.filename:
            return jsonify({'success': False, 'error': 'Dosya adı boş olamaz.'})

        username, _, _, _ = read_bkst_credentials()
        df = pd.read_excel(f)
        if df.empty:
            return jsonify({'success': False, 'error': 'Yüklenen dosya boş.'})

        col_map = {}
        for c in df.columns:
            c_str = str(c).strip().lower()
            if 'tarih' in c_str or 'date' in c_str: col_map['tarih'] = c
            elif 'ürün' in c_str or 'urun' in c_str or 'name' in c_str: col_map['urun_adi'] = c
            elif 'barkod' in c_str or 'gtin' in c_str or 'barcode' in c_str: col_map['barkod'] = c
            elif 'seri' in c_str or 'serial' in c_str: col_map['seri_no'] = c
            elif 'parti' in c_str or 'lot' in c_str: col_map['parti_no'] = c
            elif 'koli' in c_str: col_map['koli_no'] = c
            elif 'palet' in c_str: col_map['palet_no'] = c
            elif 'karekod' in c_str or 'qr' in c_str: col_map['ham_karekod'] = c

        if 'urun_adi' not in col_map:
            return jsonify({'success': False, 'error': "Excel dosyasında 'Ürün Adı' sütunu bulunamadı."})

        insert_rows = []
        for _, r in df.iterrows():
            t_val = str(r.get(col_map.get('tarih', ''), '')).strip() if 'tarih' in col_map else ''
            if not t_val or t_val in ('NaT', 'nan', 'None'):
                t_val = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            u_val = str(r.get(col_map['urun_adi'], '')).strip()
            if not u_val or u_val in ('nan', 'None'): continue

            b_val = str(r.get(col_map.get('barkod', ''), '')).strip() if 'barkod' in col_map else ''
            s_val = str(r.get(col_map.get('seri_no', ''), '')).strip() if 'seri_no' in col_map else ''
            p_val = str(r.get(col_map.get('parti_no', ''), '')).strip() if 'parti_no' in col_map else ''
            k_val = str(r.get(col_map.get('koli_no', ''), '')).strip() if 'koli_no' in col_map else ''
            pal_val = str(r.get(col_map.get('palet_no', ''), '')).strip() if 'palet_no' in col_map else ''
            qr_val = str(r.get(col_map.get('ham_karekod', ''), '')).strip() if 'ham_karekod' in col_map else f"IMPORT_{b_val}_{s_val}"

            insert_rows.append((
                t_val, u_val, b_val, k_val, s_val, p_val, pal_val,
                "", "", qr_val, 0, username or ""
            ))

        if not insert_rows:
            return jsonify({'success': False, 'error': 'Excel dosyasında geçerli kayıt bulunamadı.'})

        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.executemany("""
            INSERT INTO satis_arsivi
            (tarih, urun_adi, barkod, koli_no, seri_no, parti_no, palet_no, uretim_tarihi, skt, ham_karekod, tekrar_uyari, kullanici_adi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, insert_rows)
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Excel dosyasından toplam {len(insert_rows)} adet geçmiş satış kaydı başarıyla sisteme aktarıldı.',
            'eklenen_adet': len(insert_rows)
        })
    except Exception as e:
        logger.error(f"api_istatistikler_excel_yukle error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

# ── BKST AUTHENTICATED SESSION HELPER ────────────────────────────────────────
_bkst_session_cache = {"session": None, "gln": None, "token2": None, "ts": 0}
_bkst_session_lock = threading.Lock()

def get_bkst_authenticated_session():
    username, password, address_id, api_key = read_bkst_credentials()
    if not username or not password:
        return None, None, None, "Kullanıcı adı veya şifre bulunamadı."

    now = time.monotonic()
    with _bkst_session_lock:
        if (_bkst_session_cache["session"] is not None 
            and _bkst_session_cache["gln"] 
            and (now - _bkst_session_cache["ts"] < 300)):
            return _bkst_session_cache["session"], _bkst_session_cache["gln"], _bkst_session_cache["token2"], None

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

    r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(5, 10))
    token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
    token1 = token_match.group(1) if token_match else ""

    login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
    res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=SSL_VERIFY, timeout=(5, 15))
    if "0" not in res_login.text:
        return None, None, None, "Bakanlık kullanıcı adı veya şifreniz hatalı."

    r_stock_page = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(5, 10))
    token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock_page.text)
    token2 = token2_match.group(1) if token2_match else token1

    r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=SSL_VERIFY, timeout=(5, 10))
    gln_guid = address_id
    if r_gln.status_code == 200:
        try:
            gln_data = r_gln.json()
            if isinstance(gln_data, list) and len(gln_data) > 0:
                gln_guid = str(gln_data[0].get("Value") or "").strip()
        except Exception:
            pass

    with _bkst_session_lock:
        _bkst_session_cache.update({
            "session": session,
            "gln": gln_guid,
            "token2": token2,
            "ts": time.monotonic()
        })

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

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'OperationType': 1,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/SendSmsVerificationCode', data=payload, verify=SSL_VERIFY, timeout=(5, 15))
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

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        payload = {
            'IdTaxNo': tc_no,
            'PrescriptionNumber': '',
            'VerificationCode': verification_token,
            'Code': sms_code,
            '__RequestVerificationToken': token
        }

        res = session.post('https://bkst.tarbil.gov.tr/Main/CheckSmsVerificationCode', data=payload, verify=SSL_VERIFY, timeout=(5, 15))
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

        r_page = session.get('https://bkst.tarbil.gov.tr/Main/SellToProducerNonPrescribed', verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        token = token_match.group(1) if token_match else token2

        df_cache, qr_map, gtin_map, koli_map = get_bkst_cache()

        datasource_items = []
        for raw_qr in karekods:
            norm_qr = normalize_qr(str(raw_qr).strip())
            if not norm_qr:
                continue

            match_row = resolve_product_from_cache(norm_qr, df_cache, qr_map, gtin_map)

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

        res = session.post('https://bkst.tarbil.gov.tr/Main/NewCheckOutNotificationForProducerNonPrescribed', data=payload, verify=SSL_VERIFY, timeout=(5, 20))

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

    # Token'ı ReceivedNotificationList sayfasından al
    try:
        r_page = session.get("https://bkst.tarbil.gov.tr/Main/ReceivedNotificationList", verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        if token_match:
            token2 = token_match.group(1)
    except Exception:
        pass

    req_data = request.get_json(silent=True) or {}
    period = req_data.get('period') or request.args.get('period') or 'son_180'
    start_date = req_data.get('start_date') or request.args.get('start_date')
    end_date = req_data.get('end_date') or request.args.get('end_date')

    now = datetime.now()
    range_label = ""
    if period == 'son_30':
        start_date = (now - timedelta(days=30)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 30 Gün ({start_date} - {end_date})"
    elif period == 'son_90':
        start_date = (now - timedelta(days=90)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 90 Gün ({start_date} - {end_date})"
    elif period == 'son_180':
        start_date = (now - timedelta(days=180)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 6 Ay / Sezon ({start_date} - {end_date})"
    elif period in ['son_365', 'son_1_yil', 'bu_yil']:
        start_date = (now - timedelta(days=365)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 1 Yıl ({start_date} - {end_date})"
    elif period == 'tum':
        start_date = ""
        end_date = ""
        range_label = "Tüm Zamanlar"
    elif not start_date:
        # Varsayılan: Son 180 Gün (6 Ay / Sezon) - Yıl geçişlerinde (örneğin 2027 başında 2026 sonu faturaları) KESİNLİKLE kesilmez!
        start_date = (now - timedelta(days=180)).strftime('%d.%m.%Y')
        end_date = now.strftime('%d.%m.%Y')
        range_label = f"Son 6 Ay / Sezon ({start_date} - {end_date})"
    else:
        range_label = f"Özel Tarih ({start_date} - {end_date or 'Bugün'})"

    notifications = []
    try:
        payload = {
            "CompanyAddressId": gln_guid,
            "SenderGln": "",
            "DocumentNo": "",
            "StartDate": start_date or "",
            "EndDate": end_date or "",
            "NotificationType": "",
            "page": 1,
            "pageSize": 100,
            "__RequestVerificationToken": token2
        }
        res = session.post("https://bkst.tarbil.gov.tr/Main/GetReceivedNotificationList", data=payload, verify=SSL_VERIFY, timeout=(5, 15))
        if res.status_code == 200:
            jdata = res.json()
            raw_list = jdata.get("Data") if isinstance(jdata, dict) else (jdata if isinstance(jdata, list) else [])
            for item in raw_list:
                state = str(item.get("HEADERSTATE") or item.get("StateDescription") or "").upper()
                if "IPTAL" in state or "İPTAL" in state:
                    continue

                op_raw = str(item.get("OPERATION") or "MALALIM").upper()
                op_display = "MAL ALIM" if op_raw in ["SATIS", "MALALIM"] else op_raw

                notifications.append({
                    "HEADERID": item.get("HEADERID") or item.get("Id") or item.get("ID") or str(item.get("WAYBILLNUMBER", "")),
                    "WAYBILLNUMBER": item.get("WAYBILLNUMBER") or item.get("WaybillNumber") or item.get("BELGENO") or "-",
                    "WAYBILLDATE": format_date_val(item.get("WAYBILLDATE") or item.get("WaybillDate") or item.get("TARIH")),
                    "SENDER": item.get("CompanyTitle") or item.get("SENDER") or item.get("GonderenFirma") or item.get("FIRMA") or "Tedarikçi / Üretici",
                    "PRODUCTCOUNT": item.get("PRODUCTCOUNT") or item.get("ProductCount") or item.get("ADET") or 0,
                    "HEADERSTATE": "Stoğa Alınmış",
                    "OPERATION": op_display,
                    "products": []
                })

            # Her bildirimin ürün detaylarını sorgulayarak 'Kabul Bekliyor' mu yoksa 'Stoğa Alınmış' mı olduğunu belirle
            _bkst_fetch_lock = threading.Lock()

            def resolve_header_state(notif):
                h_id = notif.get("HEADERID")
                if not h_id:
                    return h_id, "Stoğa Alınmış", 0
                try:
                    with _bkst_fetch_lock:
                        r = session.post(
                            "https://bkst.tarbil.gov.tr/Main/GetNotificationDetailList",
                            data={"CompanyAddressId": gln_guid, "HeaderId": h_id, "__RequestVerificationToken": token2},
                            verify=SSL_VERIFY,
                            timeout=(3, 8)
                        )
                    if r.status_code == 200:
                        d = r.json()
                        d_list = d if isinstance(d, list) else (d.get("Data", []) if isinstance(d, dict) else [])
                        waiting_cnt = sum(1 for x in d_list if "ALIMA UYGUN" in str(x.get("DETAILSTATE", "")).upper() and "DEĞİL" not in str(x.get("DETAILSTATE", "")).upper() and "DEGIL" not in str(x.get("DETAILSTATE", "")).upper())

                        # Eski/Arşiv Bildirim Koruması: 180 günden eski faturalar ticari/yasal olarak
                        # beklemede olamaz. Bakanlık sisteminden silinmiş veya zaman aşımına uğramış
                        # hayalet kayıtların yanlışlıkla 'Kabul Bekliyor' olarak öne çıkmasını engelle.
                        w_date_str = str(notif.get("WAYBILLDATE") or "")
                        is_ancient = False
                        try:
                            d_parts = w_date_str.split(".")
                            if len(d_parts) == 3:
                                notif_dt = datetime(int(d_parts[2]), int(d_parts[1]), int(d_parts[0]))
                                if (datetime.now() - notif_dt).days > 180:
                                    is_ancient = True
                        except Exception:
                            pass

                        if waiting_cnt > 0 and not is_ancient:
                            return h_id, "Kabul Bekliyor", waiting_cnt
                        return h_id, "Stoğa Alınmış", 0
                except Exception as e:
                    logger.warning(f"resolve_header_state hatası (h_id={h_id}): {e}")
                return h_id, "Stoğa Alınmış", 0

            # Bildirimleri kontrollü eşzamanlı sorgula (max_workers=5)
            state_map = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                for h_id, state_str, w_cnt in executor.map(resolve_header_state, notifications):
                    state_map[h_id] = (state_str, w_cnt)

            for n in notifications:
                h_id = n.get("HEADERID")
                if h_id in state_map:
                    st, w_cnt = state_map[h_id]
                    n["HEADERSTATE"] = st
                    n["WAITINGCOUNT"] = w_cnt
                else:
                    n["HEADERSTATE"] = "Stoğa Alınmış"
                    n["WAITINGCOUNT"] = 0

            # Kabul Bekleyen bildirimleri en başa getir (kullanıcı hemen görsün)
            notifications.sort(key=lambda x: 0 if x.get("HEADERSTATE") == "Kabul Bekliyor" else 1)

    except Exception as e:
        logger.error(f"BKST gelen bildirim hatası: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"BKST sunucusundan bildirimler çekilirken hata oluştu: {str(e)}"})

    kabul_bekleyen_sayisi = sum(1 for n in notifications if n.get("HEADERSTATE") == "Kabul Bekliyor")
    msg = f"Toplam {len(notifications)} bildirim incelendi [{range_label}]. ({kabul_bekleyen_sayisi} adet Kabul Bekliyor, {len(notifications)-kabul_bekleyen_sayisi} adet Stoğa Alınmış)" if notifications else f"Belirtilen dönemde [{range_label}] gelen bildirim bulunamadı."

    return jsonify({
        "success": True,
        "notifications": notifications,
        "kabul_bekleyen_sayisi": kabul_bekleyen_sayisi,
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "range_label": range_label,
        "message": msg
    })

@app.route('/api/depo_kabul/detay/<header_id>', methods=['GET'])
def api_depo_kabul_detay(header_id):
    session, gln_guid, token2, err = get_bkst_authenticated_session()
    if err:
        return jsonify({"success": False, "error": err, "products": []})

    try:
        r_page = session.get("https://bkst.tarbil.gov.tr/Main/ReceivedNotificationList", verify=SSL_VERIFY, timeout=(5, 10))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_page.text)
        if token_match:
            token2 = token_match.group(1)
    except Exception:
        pass

    products = []
    waiting_count = 0
    in_stock_count = 0
    try:
        res = session.post(
            "https://bkst.tarbil.gov.tr/Main/GetNotificationDetailList",
            data={"CompanyAddressId": gln_guid, "HeaderId": header_id, "__RequestVerificationToken": token2},
            verify=SSL_VERIFY,
            timeout=(5, 15)
        )
        if res.status_code == 200:
            jdata = res.json()
            raw_list = jdata if isinstance(jdata, list) else (jdata.get("Data", []) if isinstance(jdata, dict) else [])
            for item in raw_list:
                gtin = item.get("BARCODE") or item.get("GTIN") or item.get("Gtin") or ""
                qr = item.get("KAREKOD") or item.get("HAMKAREKOD") or item.get("Barcode") or ""
                seri = item.get("SERIALNUMBER") or item.get("SERINO") or item.get("SerialNumber") or ""
                parti = item.get("LOTNUMBER") or item.get("SARJNO") or item.get("LOT") or item.get("BatchNumber") or ""
                koli = item.get("CARRIERLABEL1") or item.get("PAKETNO") or item.get("KOLINO") or ""
                palet = item.get("CARRIERLABEL2") or item.get("PALETNO") or ""
                urun_adi = item.get("STOCKNAME") or item.get("URUNADI") or item.get("ProductName") or "Bitki Koruma Ürünü"
                ur_tarih = format_date_val(item.get("PRODUCTIONDATE") or item.get("URETIMTARIHI") or item.get("ProductionDate"))
                skt_val = format_date_val(item.get("SKT") or item.get("ExpirationDate"))

                d_state = str(item.get("DETAILSTATE") or "").upper()
                if "ALIMA UYGUN" in d_state and "DEĞİL" not in d_state and "DEGIL" not in d_state:
                    product_durum = "Kabul Bekliyor"
                    waiting_count += 1
                else:
                    product_durum = "Stoğa Alınmış"
                    in_stock_count += 1

                products.append({
                    "Koli Numarası": koli,
                    "Ürün Adı": urun_adi,
                    "Karekod": qr,
                    "Gtin / Barkod": gtin,
                    "gtin": gtin,
                    "Seri Numarası": seri,
                    "Parti Numarası": parti,
                    "Palet Numarası": palet,
                    "Üretim Tarihi": ur_tarih,
                    "Son Kullanma Tarihi": skt_val,
                    "durum": product_durum
                })
    except Exception as e:
        logger.error(f"Detail fetch error: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"Detay çekilirken hata oluştu: {str(e)}", "products": []})

    overall_status = "Kabul Bekliyor" if waiting_count > 0 else "Stoğa Alınmış"

    return jsonify({
        "success": True,
        "products": products,
        "overall_status": overall_status,
        "bekleyen_adet": waiting_count,
        "stoktaki_adet": in_stock_count
    })

@app.route('/api/depo_kabul/onayla', methods=['POST'])
def api_depo_kabul_onayla():
    req_data = request.get_json() or {}
    header_id = req_data.get('header_id')
    incoming_products = req_data.get('products') or []

    session, gln_guid, token2, err = get_bkst_authenticated_session()

    # Eğer ön yüzden ürün listesi boş geldiyse arka planda detay servisini çağır
    if not incoming_products and header_id and session and gln_guid:
        try:
            r_detail = session.post(
                "https://bkst.tarbil.gov.tr/Main/GetNotificationDetailList",
                data={"CompanyAddressId": gln_guid, "HeaderId": header_id, "__RequestVerificationToken": token2},
                verify=SSL_VERIFY,
                timeout=(5, 15)
            )
            if r_detail.status_code == 200:
                jd = r_detail.json()
                incoming_products = jd if isinstance(jd, list) else (jd.get("Data", []) if isinstance(jd, dict) else [])
        except Exception as e_fetch:
            logger.warning(f"api_depo_kabul_onayla: Otomatik detay çekme hatası: {e_fetch}")

    bkst_msg = ""
    if session and gln_guid and token2 and header_id:
        accept_endpoints = [
            "https://bkst.tarbil.gov.tr/Main/NotificationAccept",
            "https://bkst.tarbil.gov.tr/Main/SaveNotificationAccept",
            "https://bkst.tarbil.gov.tr/Main/ConfirmNotification",
            "https://bkst.tarbil.gov.tr/Main/SaveMalAlim"
        ]
        for ep in accept_endpoints:
            try:
                res = session.post(ep, data={"HeaderId": header_id, "CompanyAddressId": gln_guid, "__RequestVerificationToken": token2}, verify=SSL_VERIFY, timeout=(5, 12))
                if res.status_code == 200:
                    bkst_msg = "Bakanlık (BKST) bildirimi onaylandı."
                    break
            except Exception:
                pass

    username, _, _, _ = read_bkst_credentials()
    df_existing, _, _, _ = get_bkst_cache()
    
    existing_karekods = set()
    if df_existing is not None and not df_existing.empty and 'Karekod' in df_existing.columns:
        existing_karekods = set(str(k).strip().casefold() for k in df_existing['Karekod'].dropna() if str(k).strip())
    
    new_rows = []
    added_count = 0
    for p in incoming_products:
        qr = str(p.get("Karekod") or p.get("KAREKOD") or "").strip()
        if qr and qr.casefold() in existing_karekods:
            continue
        
        gtin_parsed = p.get("Gtin Numarası") or p.get("Gtin / Barkod") or p.get("BARCODE") or p.get("GTIN") or ""
        if not gtin_parsed and qr:
            parsed_qr = parse_gs1_qr(qr)
            if parsed_qr and parsed_qr.get("gtin"):
                gtin_parsed = parsed_qr["gtin"]

        row = {
            "Koli Numarası": p.get("Koli Numarası") or p.get("CARRIERLABEL1") or p.get("PAKETNO") or p.get("KOLINO") or "",
            "Ürün Adı": p.get("Ürün Adı") or p.get("STOCKNAME") or p.get("URUNADI") or "",
            "Karekod": qr,
            "Gtin Numarası": gtin_parsed,
            "Gtin / Barkod": gtin_parsed,
            "gtin": gtin_parsed,
            "Seri Numarası": p.get("Seri Numarası") or p.get("SERIALNUMBER") or p.get("SERINO") or "",
            "Parti Numarası": p.get("Parti Numarası") or p.get("LOTNUMBER") or p.get("SARJNO") or p.get("LOT") or "",
            "Palet Numarası": p.get("Palet Numarası") or p.get("CARRIERLABEL2") or p.get("PALETNO") or "",
            "Üretim Tarihi": format_date_val(p.get("Üretim Tarihi") or p.get("PRODUCTIONDATE") or p.get("URETIMTARIHI")),
            "Son Kullanma Tarihi": format_date_val(p.get("Son Kullanma Tarihi") or p.get("SKT"))
        }
        new_rows.append(row)
        if qr:
            existing_karekods.add(qr.casefold())
        added_count += 1

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        if df_existing is not None and not df_existing.empty:
            updated_df = pd.concat([df_existing, new_df], ignore_index=True)
        else:
            updated_df = new_df
        save_bkst_data_to_db(updated_df, username)
        # Önbelleği temizle ki yeni ürünler anında depomdaki stoklar ve çıkışta aktif olsun
        with _state_lock:
            _user_cache_map.pop(username or "_anon", None)

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

        r_home = session.get("https://bkst.tarbil.gov.tr/", verify=SSL_VERIFY, timeout=(4, 8))
        token_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_home.text)
        token1 = token_match.group(1) if token_match else ""

        login_payload = {"tcNo": username, "sifre": password, "__RequestVerificationToken": token1}
        res_login = session.post("https://bkst.tarbil.gov.tr/UserOperation/GetUserInf", data=login_payload, verify=SSL_VERIFY, timeout=(5, 10))
        
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
            r_stock = session.get("https://bkst.tarbil.gov.tr/Main/StockList", verify=SSL_VERIFY, timeout=(4, 8))
            token2_match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', r_stock.text)
            token2 = token2_match.group(1) if token2_match else token1

            r_gln = session.post("https://bkst.tarbil.gov.tr/Partial/GetGLN", data={"FirmType": "0", "__RequestVerificationToken": token2}, verify=SSL_VERIFY, timeout=(4, 8))
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

        with _bkst_session_lock:
            _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0})
        with _template_vars_lock:
            _template_vars_cache['expires_at'] = 0

        return jsonify({'success': True, 'message': 'Giriş başarılı ve kaydedildi.', 'user_name': user_name, 'token': LOCAL_SESSION_TOKEN})

    except Exception as e:
        logger.warning(f"api_system_login offline fallback check: {e}")
        is_net_error = isinstance(e, (requests.ConnectionError, requests.Timeout,
                                      requests.RequestException, socket.gaierror,
                                      urllib3.exceptions.HTTPError))
        if not is_net_error:
            return jsonify({'success': False, 'error': f'Giriş hatası: {str(e)}'})

        saved_u, saved_p, saved_name, saved_a = read_bkst_credentials()
        if saved_u == username and saved_p == password:
            return jsonify({
                'success': True,
                'offline_mode': True,
                'message': 'İnternet bağlantısı yok. Kayıtlı bilgilerle çevrimdışı (offline) modda giriş yapıldı.',
                'user_name': clean_user_name(saved_name or username),
                'token': LOCAL_SESSION_TOKEN
            })
        return jsonify({'success': False, 'error': 'Bakanlık sunucusuna bağlanılamadı ve girilen bilgiler kayıtlı çevrimdışı bilgilerle eşleşmiyor.'})

@app.route('/api/system/user_info', methods=['GET'])
def api_system_user_info():
    cred_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bakanlik_giris_bilgileri.txt")
    username, password, address_id, api_key = read_bkst_credentials()
    if not username:
        return jsonify({'success': True, 'username': '', 'user_name': 'Giriş Yapılmadı'})

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
        'user_name': display_name
    })

@app.route('/api/system/sync_status', methods=['GET'])
def api_system_sync_status():
    payload = _bkst_state_payload()
    with _state_lock:
        payload["synced"] = _app_bkst_synced
    return jsonify(payload)

_last_update_check_time = 0
_cached_update_response = {'has_update': False}

@app.route('/api/system/check_update', methods=['GET'])
def api_system_check_update():
    global _last_update_check_time, _cached_update_response

    # Geliştirici modu kontrolü (.dev_mode dosyası veya DEV_MODE env)
    dev_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dev_mode")
    if os.path.exists(dev_path) or os.environ.get("DEV_MODE") == "1":
        return jsonify({'has_update': False})

    now = time.time()
    # Son 3 dakika içinde kontrol edildiyse önbellekten dön (gereksiz ağ gecikmesini önler)
    if now - _last_update_check_time < 180 and _cached_update_response is not None:
        return jsonify(_cached_update_response)

    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        local_vpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
        local_commit = ""
        cur_code = "v1.0"
        if os.path.exists(local_vpath):
            with open(local_vpath, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                local_commit = str(v_data.get("commit", "")).strip()
                cur_code = str(v_data.get("version", "v1.0")).strip()

        headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache, no-store, must-revalidate"}
        remote_vurl = f"https://raw.githubusercontent.com/mfatih01020/stok_fatih/main/version.json?t={time.time_ns()}"
        resp = requests.get(remote_vurl, verify=SSL_VERIFY, timeout=(4, 8), headers=headers)
        if resp.status_code == 200:
            rdata = resp.json()
            remote_commit = str(rdata.get("commit", "")).strip()
            remote_version = str(rdata.get("version", "v1.0")).strip()
            _last_update_check_time = now

            def _parse_version_tuple(v_str):
                try:
                    clean = re.sub(r'[^0-9.]', '', str(v_str))
                    parts = [int(p) for p in clean.split('.') if p.isdigit()]
                    return tuple(parts)
                except Exception:
                    return (0, 0, 0)

            remote_tup = _parse_version_tuple(remote_version)
            local_tup = _parse_version_tuple(cur_code)

            if remote_tup > local_tup:
                _cached_update_response = {
                    'has_update': True,
                    'current_version': cur_code,
                    'remote_version': remote_version,
                    'remote_commit': remote_commit,
                    'message': rdata.get("message", "Yeni sistem güncellemesi mevcut.")
                }
                return jsonify(_cached_update_response)
            else:
                _cached_update_response = {'has_update': False}
                return jsonify(_cached_update_response)
    except Exception as e:
        logger.error(f"Check update error: {e}")

    return jsonify({'has_update': False})

@app.route('/api/system/apply_update', methods=['POST'])
def api_system_apply_update():
    try:
        from guncelleme_kontrol import force_update
        updated = force_update()
        if updated:
            def _restart_process():
                time.sleep(1.2)
                try:
                    logger.info("Restarting QR-Compare server process after update...")
                    base_dir = os.path.dirname(os.path.abspath(__file__))
                    py_dir = os.path.dirname(sys.executable)
                    pythonw_cand = os.path.join(py_dir, "pythonw.exe")
                    target_py = pythonw_cand if os.path.exists(pythonw_cand) else sys.executable
                    flags = 0x08000000 if os.name == 'nt' else 0
                    if os.name == 'nt':
                        flags |= 0x00000008
                    subprocess.Popen([target_py, "app.py"], cwd=base_dir, creationflags=flags)
                except Exception as ex:
                    logger.error(f"Restart error: {ex}")
                finally:
                    os._exit(0)

            threading.Thread(target=_restart_process, daemon=True).start()
            return jsonify({'success': True, 'updated': True, 'message': 'Güncelleme başarıyla yüklendi! Program yeniden başlatılıyor...'})
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

    with _bkst_session_lock:
        _bkst_session_cache.update({"session": None, "gln": None, "token2": None, "ts": 0})
    with _template_vars_lock:
        _template_vars_cache['expires_at'] = 0

    return jsonify({'success': True, 'message': 'Oturum kapatıldı.'})

if __name__ == '__main__':
    try:
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
    except ImportError:
        logger.warning("Waitress bulunamadı, otomatik pip ile yüklenmeye çalışılıyor...")
        try:
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "waitress"], check=True)
            from waitress import serve
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
        except Exception as e:
            logger.warning(f"Waitress kurulamadı ({e}), Flask development server ile başlatılıyor...")
            app.run(host='127.0.0.1', port=5000, threaded=True)

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

.status-indicator.offline {
    background-color: #ef4444;
    box-shadow: 0 0 10px rgba(239, 68, 68, 0.7);
}

.status-indicator.fetching {
    background-color: #f59e0b;
    box-shadow: 0 0 10px rgba(245, 158, 11, 0.7);
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
    transition: all 0.3s ease;
}

.system-status-pill.offline {
    background: rgba(239, 68, 68, 0.12);
    border-color: rgba(239, 68, 68, 0.35);
    color: #f87171;
}

.system-status-pill.fetching {
    background: rgba(245, 158, 11, 0.12);
    border-color: rgba(245, 158, 11, 0.35);
    color: #fbbf24;
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

// Global HTML & Attribute Escaping Helpers
window.esc = function(str) {
    return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};

window.escAttr = function(str) {
    return String(str || '')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};

// Global Session Authenticated Fetch Helper & Native Fetch Interceptor
const _nativeGlobalFetch = window.fetch;
window.fetch = async function(resource, init = {}) {
    init = init || {};
    const urlStr = typeof resource === 'string' ? resource : (resource && resource.url ? resource.url : '');
    if (urlStr && (urlStr.startsWith('/api/') || urlStr.includes('/api/'))) {
        if (init.headers instanceof Headers) {
            if (!init.headers.has('X-Requested-With')) init.headers.append('X-Requested-With', 'XMLHttpRequest');
        } else {
            init.headers = init.headers || {};
            if (!init.headers['X-Requested-With']) init.headers['X-Requested-With'] = 'XMLHttpRequest';
        }
    }
    const response = await _nativeGlobalFetch.call(window, resource, init);
    if (response.status === 401 && urlStr && urlStr.startsWith('/api/') && !urlStr.startsWith('/api/system/heartbeat') && !urlStr.startsWith('/api/system/user_info')) {
        window.location.href = '/login';
    }
    return response;
};
window.apiFetch = window.fetch;

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
    async function sendPing() {
        try {
            const res = await window.apiFetch('/api/system/heartbeat', { method: 'POST' });
            if (res && res.ok) {
                const data = await res.json();
                if (data && typeof window.updateSystemStatusPill === 'function') {
                    if (data.running) {
                        window.updateSystemStatusPill('fetching', 'Veriler Çekiliyor...');
                    } else if (data.online === false || data.status === 'offline' || data.status === 'error') {
                        window.updateSystemStatusPill(false, 'Sistem Deaktif', data.message);
                    } else if (data.online === true && data.fetched_count > 0) {
                        window.updateSystemStatusPill(true, 'Sistem Aktif');
                    }
                }
            }
        } catch (_) {}
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

// ── Unified Startup Auto Update Engine ───────────────────────────────────────
async function performStartupUpdateCheck() {
    // Sadece oturumun İLK açılışında bir kez çalışır. Sayfalar arası geçişlerde veya buton tıklamalarında ASLA tekrar çalışmaz!
    if (sessionStorage.getItem('startup_update_checked')) {
        return false;
    }
    sessionStorage.setItem('startup_update_checked', 'true');

    try {
        const res = await fetch('/api/system/check_update');
        if (!res.ok) return false;
        const data = await res.json();
        if (data && data.has_update) {
            console.log("Startup Update Found:", data);
            await showUpdateScreenAndApply(data);
            return true;
        }
    } catch (e) {
        console.warn("Startup update check error:", e);
    }
    return false;
}

function showUpdateScreenAndApply(updateInfo) {
    return new Promise(resolve => {
        let overlay = document.getElementById('startupUpdateOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'startupUpdateOverlay';
            overlay.style.cssText = `
                position: fixed;
                top: 0; left: 0; width: 100vw; height: 100vh;
                background: #0f172a;
                color: #ffffff;
                z-index: 9999999;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                font-family: 'Outfit', 'Inter', sans-serif;
                text-align: center;
                padding: 20px;
            `;
            const remVer = (updateInfo && updateInfo.remote_version) ? updateInfo.remote_version : 'yeni sürüm';
            overlay.innerHTML = `
                <div style="background: rgba(30, 41, 59, 0.95); border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 24px; padding: 40px 32px; max-width: 480px; width: 90%; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.8); backdrop-filter: blur(12px);">
                    <div style="width: 80px; height: 80px; margin: 0 auto 20px; background: rgba(56, 189, 248, 0.12); border-radius: 50%; display: flex; align-items: center; justify-content: center;">
                        <i class="fa-solid fa-cloud-arrow-down fa-bounce" style="font-size: 38px; color: #38bdf8;"></i>
                    </div>
                    <h2 style="font-size: 1.55rem; font-weight: 800; margin-bottom: 10px; color: #f8fafc;">Uygulama Güncelleniyor</h2>
                    <p id="updateStatusMsg" style="font-size: 0.95rem; color: #94a3b8; line-height: 1.6; margin-bottom: 24px;">
                        Yeni sürüm (${remVer}) tespit edildi. Güncelleme paketleri indiriliyor ve sisteme entegre ediliyor...
                    </p>
                    <div style="width: 100%; height: 8px; background: #334155; border-radius: 999px; overflow: hidden; position: relative;">
                        <div id="updateProgressBar" style="width: 45%; height: 100%; background: linear-gradient(90deg, #38bdf8, #3b82f6); border-radius: 999px; transition: width 0.4s ease; animation: updateProgressAnim 1.8s infinite linear;"></div>
                    </div>
                    <p id="updateSubStatus" style="font-size: 0.82rem; color: #64748b; margin-top: 18px; font-weight: 500;">
                        <i class="fa-solid fa-circle-info" style="color: #38bdf8; margin-right: 4px;"></i> İşlem tamamlandığında program sıfırdan otomatik başlatılacaktır.
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
            .then(async resData => {
                const msgEl = document.getElementById('updateStatusMsg');
                const barEl = document.getElementById('updateProgressBar');
                const subEl = document.getElementById('updateSubStatus');

                if (resData.success && resData.updated) {
                    if (barEl) {
                        barEl.style.animation = 'none';
                        barEl.style.width = '100%';
                    }
                    if (msgEl) {
                        msgEl.style.color = '#4ade80';
                        msgEl.innerHTML = '<strong>✅ Güncelleme Başarıyla Tamamlandı!</strong><br>Program sıfırdan yeniden başlatılıyor...';
                    }
                    if (subEl) subEl.textContent = 'Yeni sistem yükleniyor, lütfen bekleyin...';

                    // Sunucunun yeni process ile ayağa kalkmasını bekle (Heartbeat Polling)
                    await new Promise(r => setTimeout(r, 2200));
                    for (let i = 0; i < 30; i++) {
                        await new Promise(r => setTimeout(r, 800));
                        try {
                            const ping = await fetch('/api/system/heartbeat', { method: 'POST' });
                            if (ping.ok) break;
                        } catch (_) {}
                    }
                    window.location.reload(true);
                } else {
                    if (msgEl) msgEl.textContent = resData.message || "Sistem zaten güncel.";
                    setTimeout(() => {
                        if (overlay) overlay.remove();
                        resolve();
                    }, 1200);
                }
            })
            .catch(err => {
                console.error("Apply update error:", err);
                const msgEl = document.getElementById('updateStatusMsg');
                if (msgEl) {
                    msgEl.style.color = '#f87171';
                    msgEl.textContent = "Güncelleme sırasında bir aksaklık oluştu, normal modda başlatılıyor...";
                }
                setTimeout(() => {
                    if (overlay) overlay.remove();
                    resolve();
                }, 2000);
            });
    });
}

// Global state
window.shelfKoliMap = window.shelfKoliMap || new Map();
window.shelfItems = window.shelfItems || [];
window.scannedQRsInShelf = window.scannedQRsInShelf || new Set();
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

        if (itemQrClean && (sClean === itemQrClean || (sClean.length >= 20 && itemQrClean.length >= 20 && (sClean.includes(itemQrClean) || itemQrClean.includes(sClean))))) {
            return true;
        }

        if (itemSeriClean && itemSeriClean.length >= 4 && (sClean === itemSeriClean || (sClean.length >= 20 && sClean.includes(itemSeriClean)))) {
            return true;
        }
    }

    return false;
}

function setBkstUI(status, message) {
    const bkstBadge  = document.getElementById('bkst-status-badge');
    const bkstMsgBox = document.getElementById('bkst-message-box');
    const btnBkstFetch = document.getElementById('btn-bkst-fetch');

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
}

window.triggerBkstFetchApi = function() {
    console.log("triggerBkstFetchApi called");
    setBkstUI('fetching', '⚡ Bakanlık verileri API üzerinden çekiliyor...');
    if (window.updateSystemStatusPill) window.updateSystemStatusPill('fetching', 'Bağlantı Kuruluyor...');

    const sidebar = document.querySelector('.sidebar');
    if (sidebar) sidebar.style.pointerEvents = 'none';

    window.apiFetch('/api/bkst/fetch_api', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                let pollAttempts = 0;
                let statusTimer = setInterval(async () => {
                    pollAttempts++;
                    try {
                        const res = await window.apiFetch('/api/bkst/fetch_status');
                        const statusData = await res.json();
                        setBkstUI(statusData.status, statusData.message);
                        if (!statusData.running || pollAttempts > 45) {
                            clearInterval(statusTimer);
                            if (sidebar) sidebar.style.pointerEvents = 'auto';

                            if (statusData.online === true && statusData.fetched_count > 0) {
                                if (window.updateSystemStatusPill) {
                                    window.updateSystemStatusPill(true, 'Sistem Aktif', `Bakanlıktan ${statusData.fetched_count} adet stok çekildi.`);
                                }
                                const dlBtn = document.getElementById('btn-bkst-download-excel');
                                if (dlBtn) dlBtn.classList.remove('hidden');
                            } else {
                                if (window.updateSystemStatusPill) {
                                    window.updateSystemStatusPill(false, 'Sistem Deaktif', statusData.message || 'Bakanlık bağlantı sorunu (0 adet veri).');
                                }
                                alert(`⚠️ Bakanlık Bağlantı Sorunu: 0 adet veri çekildi!\n\nSistem Deaktif moduna alındı.\n\n${statusData.message || 'Yerel veritabanındaki son kayıtlı stoklar korunuyor.'}`);
                            }
                        }
                    } catch (e) {
                        clearInterval(statusTimer);
                        if (sidebar) sidebar.style.pointerEvents = 'auto';
                        if (window.updateSystemStatusPill) window.updateSystemStatusPill(false, 'Sistem Deaktif', 'Bağlantı hatası');
                    }
                }, 1500);
            } else {
                setBkstUI('error', data.error || 'API veri çekme hatası oluştu.');
                if (window.updateSystemStatusPill) window.updateSystemStatusPill(false, 'Sistem Deaktif', data.error);
                if (sidebar) sidebar.style.pointerEvents = 'auto';
            }
        })
        .catch(err => {
            setBkstUI('error', 'Sunucu hatası: ' + err.message);
            if (window.updateSystemStatusPill) window.updateSystemStatusPill(false, 'Sistem Deaktif', err.message);
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

function saveAuditState() {
    try {
        const state = {
            koliEntries: Array.from(window.shelfKoliMap.entries()),
            scannedQRs: Array.from(window.scannedQRsInShelf),
            isAuditAllMode: !!window.isAuditAllMode,
            allWarehouseItems: window.allWarehouseItems || []
        };
        sessionStorage.setItem('qr_audit_shelf_state', JSON.stringify(state));
        try { localStorage.removeItem('qr_audit_shelf_state'); } catch (_) {}
    } catch (e) {
        console.warn("Audit state save error:", e);
    }
}

function loadAuditState() {
    try {
        try { localStorage.removeItem('qr_audit_shelf_state'); } catch (_) {}
        const raw = sessionStorage.getItem('qr_audit_shelf_state');
        if (!raw) return;
        const state = JSON.parse(raw);
        if (!state || !state.koliEntries || state.koliEntries.length === 0) return;

        window.shelfKoliMap = new Map(state.koliEntries);
        window.scannedQRsInShelf = new Set(state.scannedQRs || []);
        window.isAuditAllMode = !!state.isAuditAllMode;
        window.allWarehouseItems = state.allWarehouseItems || [];

        rebuildShelfItems();
        renderAuditTable();

        const wrapper = document.getElementById('audit-results-wrapper');
        if (wrapper && window.shelfItems.length > 0) {
            wrapper.classList.remove('hidden');
        }
    } catch (e) {
        console.warn("Audit state restore error:", e);
    }
}

function clearAuditState() {
    try {
        sessionStorage.removeItem('qr_audit_shelf_state');
        localStorage.removeItem('qr_audit_shelf_state');
    } catch (e) {}
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
                saveAuditState();
                const missingCnt = window.allWarehouseItems.filter(i => !isItemScanned(i)).length;
                showAuditMsg(`📊 Okutulan Kalem Karşılaştırma Modu AÇILDI! Bakanlık depodaki toplam ${window.allWarehouseItems.length} kutunun ${missingCnt} adeti tereğinizde EKSİK (Kırmızı renkte listenin en üstünde sıralandı).`, false);
            } else {
                window.isAuditAllMode = false;
                renderAuditTable();
                saveAuditState();
                showAuditMsg('Hata: ' + data.error, true);
                return;
            }
        } catch (err) {
            window.isAuditAllMode = false;
            renderAuditTable();
            saveAuditState();
            showAuditMsg('Sunucu hatası: ' + err.message, true);
            return;
        }
    } else {
        renderAuditTable();
        saveAuditState();
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
            return iQr === normCode || (normCode.length >= 20 && (normCode.includes(iQr) || iQr.includes(normCode))) || (iSeri && iSeri === normCode);
        });

        if (matchedItem) {
            if (window.scannedQRsInShelf.has(matchedItem.qr)) {
                showAuditMsg(`⚠️ Bu ilacın karekodu (${matchedItem.qr}) zaten tereğinizde okutulmuştu!`, true);
            } else {
                window.scannedQRsInShelf.add(matchedItem.qr);
                renderAuditTable();
                saveAuditState();
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
                saveAuditState();
                showAuditMsg(`📦 Koli (${newKoliNo}) barkodu okutuldu! Kolideki ${newItems.length} adet ürünün TAMAMI tereğümde VAR olarak işaretlendi.`, false);
            } else {
                if (scannedQr) window.scannedQRsInShelf.add(scannedQr);
                const targetMatch = window.shelfItems.find(i => {
                    const iQr = (i.qr || '').replace(/\s+/g, '').toUpperCase();
                    const iSeri = (i.seri_no || '').replace(/\s+/g, '').toUpperCase();
                    return iQr === normCode || (normCode.length >= 20 && (normCode.includes(iQr) || iQr.includes(normCode))) || (iSeri && iSeri === normCode) || (scannedQr && iQr === scannedQr.replace(/\s+/g, '').toUpperCase());
                });

                if (targetMatch) {
                    window.scannedQRsInShelf.add(targetMatch.qr);
                }

                renderAuditTable();
                saveAuditState();
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
    clearAuditState();

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
            saveAuditState();
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

// Direct DOM Event Binding
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM loaded, binding event listeners...");
    loadAuditState();

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

    const btnAuditResetKoli = document.getElementById('btn-audit-reset-koli');
    const btnAuditExcel = document.getElementById('btn-dl-audit-excel');
    const btnAuditToggleMode = document.getElementById('btn-audit-toggle-mode');

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
                const verStr = data.version || '';
                if (verStr) {
                    document.querySelectorAll('#versionText, .version-text').forEach(el => {
                        el.textContent = verStr;
                    });
                }
                
                const h = document.getElementById('modalCommitHash');
                const d = document.getElementById('modalCommitDate');
                const m = document.getElementById('modalCommitMsg');
                if (h && data.commit_hash) h.textContent = data.commit_hash;
                if (d && data.commit_date) d.textContent = data.commit_date;
                if (m && data.commit_msg) m.textContent = data.commit_msg;
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
            <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">Yükleniyor...</span></p>
            <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">-</span></p>
            <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
            <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">Yükleniyor...</div>
            <div style="margin-top:16px; background:rgba(34, 197, 94, 0.15); border:1px solid rgba(34, 197, 94, 0.3); color:#4ade80; padding:8px 12px; border-radius:8px; text-align:center; font-size:0.85rem; font-weight:600;">
                🟢 Sistem Güncel
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

// ── Sistem Durumu ve Durum Rozeti (Sistem Aktif / Sistem Deaktif) ───────────
window.updateSystemStatusPill = function(isOnline, statusText, details) {
    const pills = document.querySelectorAll('.system-status-pill');
    pills.forEach(pill => {
        const ind = pill.querySelector('.status-indicator');
        const textSpan = pill.querySelector('span:not(.status-indicator)');
        
        pill.classList.remove('offline', 'fetching');
        if (ind) ind.classList.remove('online', 'offline', 'fetching');
        
        if (isOnline === true) {
            if (ind) ind.classList.add('online');
            if (textSpan) textSpan.textContent = statusText || 'Sistem Aktif';
            pill.title = details || 'Bakanlık bağlantısı aktif, güncel veriler senkronize.';
        } else if (isOnline === false) {
            pill.classList.add('offline');
            if (ind) ind.classList.add('offline');
            if (textSpan) textSpan.textContent = statusText || 'Sistem Deaktif';
            pill.title = details || 'Bakanlığa bağlanılamadı. Sistem yerel veritabanı ile deaktif modda çalışıyor.';
        } else if (isOnline === 'fetching') {
            pill.classList.add('fetching');
            if (ind) ind.classList.add('fetching');
            if (textSpan) textSpan.textContent = statusText || 'Bağlantı Kuruluyor...';
            pill.title = details || 'Bakanlık verileri güncelleniyor...';
        }
    });
};

// ── Otomatik Bakanlık Veri Senkronizasyonu (Uygulama Açıldığında) ──────────────────────────
async function runAutoBkstSync(force = false) {
    if (window.location.pathname === '/login') return;

    const urlForce = window.location.search.includes('force_sync=1');
    const alreadySynced = sessionStorage.getItem('app_launch_synced');

    if (!force && !urlForce && alreadySynced) {
        return;
    }
    sessionStorage.setItem('app_launch_synced', 'true');

    if (urlForce) {
        try {
            window.history.replaceState({}, document.title, window.location.pathname);
        } catch (_) {}
    }

    let loader = document.getElementById('auto-sync-loader');
    if (!loader) {
        loader = document.createElement('div');
        loader.id = 'auto-sync-loader';
        loader.style.cssText = 'display:flex; position:fixed; z-index:99999; left:0; top:0; width:100%; height:100%; background:rgba(10, 11, 16, 0.94); backdrop-filter:blur(14px); flex-direction:column; align-items:center; justify-content:center; text-align:center;';
        loader.innerHTML = `
            <div style="background:rgba(15, 23, 42, 0.96); border:1px solid rgba(56, 189, 248, 0.35); border-radius:24px; padding:2.8rem 3rem; max-width:540px; width:92%; box-shadow:0 25px 50px rgba(0,0,0,0.8); transition: all 0.3s ease;">
                <div id="loader-icon-box" style="width:80px; height:80px; border-radius:50%; background:rgba(56,189,248,0.15); border:2px solid rgba(56,189,248,0.4); margin:0 auto 1.5rem auto; display:flex; align-items:center; justify-content:center; transition: all 0.3s ease;">
                    <i id="loader-icon" class="fa-solid fa-cloud-arrow-down fa-bounce" style="font-size:2.4rem; color:#38bdf8;"></i>
                </div>
                <h2 id="loader-title" style="font-family:var(--font-outfit, sans-serif); font-size:1.45rem; font-weight:800; color:#fff; margin:0 0 0.6rem 0;">
                    Bakanlıktan Güncel Veriler Çekiliyor...
                </h2>
                <div id="loader-status" style="font-size:0.95rem; color:#94a3b8; margin:0 0 1.6rem 0; line-height:1.6; transition: all 0.3s ease;">
                    Lütfen bekleyin, BKST sunucusundan güncel stok ve karekod verileriniz API üzerinden çekiliyor.
                </div>
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

    window.updateSystemStatusPill('fetching', 'Bağlantı Kuruluyor...');

    try {
        const startRes = await window.apiFetch('/api/bkst/fetch_api', { method: 'POST' });
        const startData = await startRes.json();

        if (startData.unauthenticated) {
            if (loader) loader.style.display = 'none';
            if (window.location.pathname !== '/login') {
                window.location.href = '/login';
            }
            return;
        }

        // 2. setInterval ile her 3 saniyede bir fetch_status sorgula (Maks 100 deneme = 5 dakika)
        await new Promise((resolve) => {
            let attempts = 0;
            const maxAttempts = 100;
            const pollInterval = setInterval(async () => {
                attempts++;
                try {
                    const sRes = await window.apiFetch('/api/bkst/fetch_status');
                    if (sRes.ok) {
                        const statusData = await sRes.json();
                        if (statusData.message && status) {
                            status.textContent = statusData.message;
                        }

                        if (!statusData.running || attempts >= maxAttempts) {
                            clearInterval(pollInterval);

                            if (statusData.online === true && statusData.fetched_count > 0) {
                                window.updateSystemStatusPill(true, 'Sistem Aktif', `Bakanlıktan ${statusData.fetched_count} adet stok çekildi.`);
                                if (title) title.textContent = "Tamamlandı";
                                if (status) {
                                    status.style.color = "#4ade80";
                                    status.style.fontWeight = "700";
                                    status.style.fontSize = "1.05rem";
                                    status.textContent = `🟢 Sistem Aktif: Bakanlıktan toplam ${statusData.fetched_count} adet stok verisi çekildi. Sisteme aktarıldı.`;
                                }
                                if (iconBox) {
                                    iconBox.style.background = "rgba(34, 197, 94, 0.2)";
                                    iconBox.style.borderColor = "rgba(34, 197, 94, 0.5)";
                                }
                                if (icon) {
                                    icon.className = "fa-solid fa-circle-check";
                                    icon.style.color = "#4ade80";
                                }
                                if (progressBar) progressBar.style.background = "#22c55e";
                                setTimeout(resolve, 2000);
                            } else {
                                const failReason = statusData.message || "Bakanlık API'sine bağlanırken sorun oluştu (0 adet veri çekildi).";
                                const localCount = statusData.local_count || 0;
                                window.updateSystemStatusPill(false, 'Sistem Deaktif', failReason);

                                if (title) {
                                    title.textContent = "⚠️ BAKANLIK BAĞLANTI UYARISI";
                                    title.style.color = "#f87171";
                                }
                                if (status) {
                                    status.style.color = "#fca5a5";
                                    status.style.fontWeight = "600";
                                    status.style.fontSize = "0.95rem";
                                    status.innerHTML = `
                                        <div style="margin-bottom:0.6rem; color:#ef4444; font-weight:800; font-size:1.05rem;">
                                            ⚠️ 0 Adet Veri Çekildi (Bakanlığa Bağlanılamadı)!
                                        </div>
                                        <div style="color:#cbd5e1; font-size:0.88rem; margin-bottom:0.8rem;">${failReason}</div>
                                        <div style="padding:0.75rem; background:rgba(239,68,68,0.15); border:1px solid rgba(239,68,68,0.3); border-radius:10px; color:#fecaca; text-align:left; line-height:1.5;">
                                            <div style="font-weight:700; color:#f87171; margin-bottom:3px;">🔴 Sistem Durumu: SİSTEM DEAKTİF</div>
                                            <div style="font-size:0.83rem;">Yerel veritabanındaki son kayıtlı <strong>${localCount}</strong> adet stok verisi korunuyor ve kesintisiz kullanılmaya devam ediliyor.</div>
                                        </div>
                                    `;
                                }
                                if (iconBox) {
                                    iconBox.style.background = "rgba(239, 68, 68, 0.2)";
                                    iconBox.style.borderColor = "rgba(239, 68, 68, 0.5)";
                                }
                                if (icon) {
                                    icon.className = "fa-solid fa-triangle-exclamation";
                                    icon.style.color = "#ef4444";
                                }
                                if (progressBar) progressBar.style.background = "#ef4444";
                                setTimeout(resolve, 3500);
                            }
                        }
                    }
                } catch (e) {
                    if (attempts >= maxAttempts) {
                        clearInterval(pollInterval);
                        if (title) title.textContent = "Bağlantı Hatası";
                        if (status) status.textContent = "Sunucu ile bağlantı kurulamadı veya zaman aşımına uğradı.";
                        setTimeout(resolve, 2000);
                    }
                }
            }, 3000);
        });

    } catch (err) {
        console.error('Otomatik BKST veri çekme hatası:', err);
        window.updateSystemStatusPill(false, 'Sistem Deaktif', 'Sunucu ile iletişim kurulamadı.');
        if (title) title.textContent = "Bağlantı Hatası";
        if (status) status.innerHTML = `<span style="color:#ef4444;">Sunucu ile bağlantı kurulamadı. Sistem Deaktif durumdadır.</span>`;
        await new Promise(resolve => setTimeout(resolve, 2000));
    } finally {
        if (loader) loader.style.display = 'none';
        if (typeof window.loadWarehouseStock === 'function') {
            window.loadWarehouseStock();
        }
    }
}

async function checkWebSystemUpdate() {
    return await performStartupUpdateCheck();
}

async function initApp() {
    loadUserInfo();
    loadSystemVersion();

    // 1. Sadece oturumun ilk açılışında arka planda güncelleme kontrolü yap
    // (sessionStorage sayesinde butonlara basıldığında veya sayfa geçişlerinde ASLA tekrar çalışmaz)
    const hasUpdate = await performStartupUpdateCheck();
    if (hasUpdate) {
        return;
    }

    // 2. Bakanlık Senkronizasyonu Kontrolü:
    // Sunucunun senkronizasyon durumunu sorgula
    let serverNeedsSync = false;
    try {
        const syncRes = await fetch('/api/system/sync_status');
        if (syncRes.ok) {
            const syncData = await syncRes.json();
            if (!syncData.synced) {
                serverNeedsSync = true;
            }
        }
    } catch (_) {}

    const urlForce = window.location.search.includes('force_sync=1');
    const sessionSynced = sessionStorage.getItem('app_launch_synced');

    // Sunucu yeni başladıysa VEYA zorlama varsa VEYA bu oturumda henüz veri çekilmediyse veri çek
    if (serverNeedsSync || urlForce || !sessionSynced) {
        await runAutoBkstSync(true);
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initApp);
} else {
    initApp();
}


```

---

### 📁 `static/favicon.svg`

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#1e293b"/>
    </linearGradient>
    <linearGradient id="cyanGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8"/>
      <stop offset="100%" stop-color="#0284c7"/>
    </linearGradient>
    <linearGradient id="indigoGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#818cf8"/>
      <stop offset="100%" stop-color="#4f46e5"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="110" fill="url(#bgGrad)"/>
  <rect x="70" y="70" width="130" height="130" rx="28" fill="none" stroke="url(#cyanGrad)" stroke-width="24"/>
  <rect x="108" y="108" width="54" height="54" rx="14" fill="url(#cyanGrad)"/>
  <rect x="312" y="70" width="130" height="130" rx="28" fill="none" stroke="url(#cyanGrad)" stroke-width="24"/>
  <rect x="350" y="108" width="54" height="54" rx="14" fill="url(#cyanGrad)"/>
  <rect x="70" y="312" width="130" height="130" rx="28" fill="none" stroke="url(#cyanGrad)" stroke-width="24"/>
  <rect x="108" y="350" width="54" height="54" rx="14" fill="url(#cyanGrad)"/>
  <rect x="312" y="312" width="44" height="44" rx="12" fill="url(#cyanGrad)"/>
  <rect x="398" y="312" width="44" height="44" rx="12" fill="url(#cyanGrad)"/>
  <rect x="312" y="398" width="44" height="44" rx="12" fill="url(#cyanGrad)"/>
  <rect x="398" y="398" width="44" height="44" rx="12" fill="url(#cyanGrad)"/>
  <rect x="355" y="355" width="44" height="44" rx="12" fill="url(#indigoGrad)"/>
  <rect x="236" y="70" width="40" height="130" rx="14" fill="url(#cyanGrad)"/>
  <rect x="70" y="236" width="130" height="40" rx="14" fill="url(#cyanGrad)"/>
  <rect x="236" y="312" width="40" height="130" rx="14" fill="url(#indigoGrad)"/>
  <rect x="312" y="236" width="130" height="40" rx="14" fill="url(#cyanGrad)"/>
</svg>
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
                <a href="/istatistikler" class="sidebar-link">
                    <i class="fa-solid fa-chart-pie"></i>
                    <span>İstatistikler & Raporlar</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill {{ 'offline' if not is_system_active else '' }}">
                    <span class="status-indicator {{ system_status_cls | default('online') }}"></span>
                    <span>{{ system_status_text | default('Sistem Aktif') }}</span>
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
                    <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">{{ current_app_commit }}</span></p>
                    <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">{{ current_app_date }}</span></p>
                    <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                    <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">{{ current_app_msg }}</div>
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

    <script src="/static/app.js?v=20261009_v322"></script>
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
                    if (data.token) {
                        localStorage.setItem('local_session_token', data.token);
                    }
                    sessionStorage.removeItem('app_launch_synced');
                    sessionStorage.setItem('startup_update_checked', 'true');
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
            setInterval(sendPing, 10000);
        })();

        // İlk Açılışta Arka Plan Güncelleme Kontrolü (Login Formu Öncesi)
        (async function checkLoginStartupUpdate() {
            if (sessionStorage.getItem('startup_update_checked')) {
                return;
            }
            sessionStorage.setItem('startup_update_checked', 'true');

            try {
                const res = await fetch('/api/system/check_update');
                if (res.ok) {
                    const data = await res.json();
                    if (data && data.has_update) {
                        showLoginUpdateOverlay(data);
                    }
                }
            } catch (e) {
                console.warn("Login update check error:", e);
            }
        })();

        function showLoginUpdateOverlay(updateInfo) {
            let overlay = document.getElementById('loginUpdateOverlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.id = 'loginUpdateOverlay';
                overlay.style.cssText = `
                    position: fixed;
                    top: 0; left: 0; width: 100vw; height: 100vh;
                    background: #0f172a;
                    color: #ffffff;
                    z-index: 9999999;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    font-family: 'Outfit', 'Inter', sans-serif;
                    text-align: center;
                    padding: 20px;
                `;
                overlay.innerHTML = `
                    <div style="background: rgba(30, 41, 59, 0.95); border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 24px; padding: 40px 32px; max-width: 480px; width: 90%; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.8); backdrop-filter: blur(12px);">
                        <div style="width: 80px; height: 80px; margin: 0 auto 20px; background: rgba(56, 189, 248, 0.12); border-radius: 50%; display: flex; align-items: center; justify-content: center;">
                            <i class="fa-solid fa-cloud-arrow-down fa-bounce" style="font-size: 38px; color: #38bdf8;"></i>
                        </div>
                        <h2 style="font-size: 1.55rem; font-weight: 800; margin-bottom: 10px; color: #f8fafc;">Uygulama Güncelleniyor</h2>
                        <p id="loginUpdateMsg" style="font-size: 0.95rem; color: #94a3b8; line-height: 1.6; margin-bottom: 24px;">
                            Yeni sürüm (${updateInfo.remote_version || 'v3.1.x'}) tespit edildi. Güncelleme paketleri indiriliyor ve sisteme entegre ediliyor...
                        </p>
                        <div style="width: 100%; height: 8px; background: #334155; border-radius: 999px; overflow: hidden; position: relative;">
                            <div id="loginUpdateBar" style="width: 45%; height: 100%; background: linear-gradient(90deg, #38bdf8, #3b82f6); border-radius: 999px; transition: width 0.4s ease; animation: updateProgressAnim 1.8s infinite linear;"></div>
                        </div>
                        <p id="loginUpdateSub" style="font-size: 0.82rem; color: #64748b; margin-top: 18px; font-weight: 500;">
                            <i class="fa-solid fa-circle-info" style="color: #38bdf8; margin-right: 4px;"></i> İşlem tamamlandığında program sıfırdan otomatik başlatılacaktır.
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
                .then(async data => {
                    const msgEl = document.getElementById('loginUpdateMsg');
                    const barEl = document.getElementById('loginUpdateBar');
                    const subEl = document.getElementById('loginUpdateSub');

                    if (data.success && data.updated) {
                        if (barEl) {
                            barEl.style.animation = 'none';
                            barEl.style.width = '100%';
                        }
                        if (msgEl) {
                            msgEl.style.color = '#4ade80';
                            msgEl.innerHTML = '<strong>✅ Güncelleme Başarıyla Tamamlandı!</strong><br>Program sıfırdan yeniden başlatılıyor...';
                        }
                        if (subEl) subEl.textContent = 'Yeni sistem yükleniyor, lütfen bekleyin...';

                        await new Promise(r => setTimeout(r, 2200));
                        for (let i = 0; i < 30; i++) {
                            await new Promise(r => setTimeout(r, 800));
                            try {
                                const ping = await fetch('/api/system/heartbeat', { method: 'POST' });
                                if (ping.ok) break;
                            } catch (_) {}
                        }
                        window.location.reload(true);
                    } else {
                        if (msgEl) msgEl.textContent = data.message || "Sistem zaten güncel.";
                        setTimeout(() => { if (overlay) overlay.remove(); }, 1200);
                    }
                })
                .catch(err => {
                    console.error("Login apply update error:", err);
                    setTimeout(() => { if (overlay) overlay.remove(); }, 2000);
                });
        }
    </script>
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
                <a href="/istatistikler" class="sidebar-link">
                    <i class="fa-solid fa-chart-pie"></i>
                    <span>İstatistikler & Raporlar</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill {{ 'offline' if not is_system_active else '' }}">
                    <span class="status-indicator {{ system_status_cls | default('online') }}"></span>
                    <span>{{ system_status_text | default('Sistem Aktif') }}</span>
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
        const res = await (window.apiFetch || fetch)('/api/cikis/okut', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({barkod})
        });
        const data = await res.json();

        if (data.success) {
            barkodInput.classList.add('input-success');
            if (data.is_bulk && data.kayitlar && data.kayitlar.length) {
                showBulkResultCard(data);
                data.kayitlar.forEach(item => {
                    prependRow(item, item.tekrar_uyari);
                });
                cntToplam.textContent = parseInt(cntToplam.textContent || '0') + data.kayitlar.length;
            } else {
                showResultCard(data.kayit, data.tekrar_uyari);
                prependRow(data.kayit, data.tekrar_uyari);
                cntToplam.textContent = parseInt(cntToplam.textContent || '0') + 1;
            }
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

function showBulkResultCard(data) {
    const k = data.kayit || {};
    const warnHtml = data.tekrar_uyari
        ? `<span class="uyari-badge"><i class="fa-solid fa-triangle-exclamation"></i> Kolide daha önce okutulmuş mükerrer ürünler var!</span>`
        : '';
    resultCard.className = 'result-card ' + (data.tekrar_uyari ? 'warn-card' : 'success-card');
    resultCard.innerHTML = `
        <p class="result-card-title"><i class="fa-solid fa-boxes-stacked" style="color:var(--success)"></i> Toplu Koli Çıkışı Başarılı (${data.count} Adet)</p>
        ${warnHtml}
        <div class="result-urun-adi">${esc(k.urun_adi || 'Koli')} <span style="font-size:0.88rem; color:#38bdf8; font-weight:600;">(${data.count} Kutu Eklendi)</span></div>
        <div class="result-grid">
            <div class="result-field"><span class="label">Koli Numarası</span><span class="val">${esc(k.koli_no || '—')}</span></div>
            <div class="result-field"><span class="label">Eklenen Kutu</span><span class="val" style="color:#10b981; font-weight:700;">${data.count} Adet</span></div>
            <div class="result-field"><span class="label">Gtin/Barkod</span><span class="val">${esc(k.barkod || '—')}</span></div>
            <div class="result-field"><span class="label">Parti Numarası</span><span class="val">${esc(k.parti_no || '—')}</span></div>
            <div class="result-field"><span class="label">SKT</span><span class="val">${esc(k.skt || '—')}</span></div>
            <div class="result-field"><span class="label">Kayıt Zamanı</span><span class="val">${esc(k.tarih || '—')}</span></div>
        </div>`;
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
    const res = await (window.apiFetch || fetch)(`/api/cikis/sil/${id}`, {
        method: 'DELETE',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
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
    const res  = await (window.apiFetch || fetch)('/api/cikis/listesi', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
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
    const res  = await (window.apiFetch || fetch)('/api/cikis/temizle', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
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
    if (window.esc) return window.esc(str);
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
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
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">{{ current_app_commit }}</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">{{ current_app_date }}</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">{{ current_app_msg }}</div>
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
                <a href="/istatistikler" class="sidebar-link">
                    <i class="fa-solid fa-chart-pie"></i>
                    <span>İstatistikler & Raporlar</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill {{ 'offline' if not is_system_active else '' }}">
                    <span class="status-indicator {{ system_status_cls | default('online') }}"></span>
                    <span>{{ system_status_text | default('Sistem Aktif') }}</span>
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
                • <b>Silme (Geri Alma):</b> İlgili satırın yanındaki 🔴 <b>Çöp Kovası</b> butonuna basarak kaydı silin.<br>
                • <b style="color: #60a5fa;">💡 Kalıcı İstatistik Güvencesi:</b> Bu çalışma listesinden satır silseniz veya <b>"Tümünü Sil"</b> yapsanız dahi, tüm çıkışlar <b>"İstatistikler & Raporlar"</b> bölümündeki kalıcı satış veri tabanında korunur; yıllık satış istatistikleriniz asla kaybolmaz.
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

function isTekrarKayit(r) {
    if (!r) return false;
    return r.tekrar_uyari === 1 || r.tekrar_uyari === '1';
}

function updateStats() {
    document.getElementById('stat-toplam').textContent = allData.length;
    const tekilSet = new Set(allData.map(r => r.ham_karekod));
    document.getElementById('stat-tekil').textContent  = tekilSet.size;
    document.getElementById('stat-tekrar').textContent = allData.filter(r => isTekrarKayit(r)).length;
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

    tbody.innerHTML = filtered.map((r, i) => {
        const isTekrar = isTekrarKayit(r);
        return `
        <tr class="${isTekrar ? 'warn-row' : ''}" data-id="${r.id}">

            <!-- # + Silme butonu -->
            <td>
                <div class="td-sira">
                    <span class="badge-sira">${i + 1}</span>
                    <button class="btn-del" data-id="${r.id}" onclick="silKayit(${r.id}, this)"
                            title="Bu kaydı sil">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            </td>

            <!-- Karekod — EN BAŞTA -->
            <td class="td-karekod" title="${esc(r.ham_karekod)}">${esc(r.ham_karekod||'—')}</td>

            <!-- Ürün Adı -->
            <td style="max-width:220px; overflow:hidden; text-overflow:ellipsis;" title="${esc(r.urun_adi)}">
                ${isTekrar ? '<span class="tekrar-badge">TEKRAR</span> ' : ''}${esc(r.urun_adi||'—')}
            </td>

            <td>${esc(r.koli_no||'—')}</td>
            <td>${esc(r.seri_no||'—')}</td>
            <td>${esc(r.parti_no||'—')}</td>
            <td>${esc(r.palet_no||'—')}</td>
            <td style="font-family:monospace; font-size:.78rem;">${esc(r.barkod||'—')}</td>
            <td>${esc(r.uretim_tarihi||'—')}</td>
            <td>${esc(r.skt||'—')}</td>
            <td style="font-size:.76rem; color:var(--text-muted);">${esc(r.tarih||'—')}</td>
        </tr>`;
    }).join('');
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
    if (btn) btn.disabled = true;
    try {
        const fetchFn = window.apiFetch || fetch;
        const res  = await fetchFn(`/api/cikis/sil/${id}`, {method: 'DELETE'});
        const data = await res.json();
        if (data.success) {
            allData = allData.filter(r => String(r.id) !== String(id));
            updateStats();
            render();
        } else {
            if (btn) btn.disabled = false;
            alert('Silme işlemi başarısız: ' + (data.error || ''));
        }
    } catch (e) {
        if (btn) btn.disabled = false;
        alert('Silme hatası: ' + e.message);
    }
}

// Delegated delete listener on tbody
const mainTbody = document.getElementById('main-tbody');
if (mainTbody) {
    mainTbody.addEventListener('click', async (e) => {
        const btn = e.target.closest('.btn-del');
        if (!btn) return;
        const id = btn.getAttribute('data-id');
        if (id) {
            await silKayit(id, btn);
        }
    });
}

document.getElementById('btn-tumunu-sil').addEventListener('click', async () => {
    if (!confirm(`Çıkış yapılacaklar listesindeki ${allData.length} kayıt temizlenecek.\n\n💡 Not: Bu işlem genel satış ve rapor istatistiklerinizi etkilemez; istatistikleriniz kalıcı arşivde saklanmaya devam eder.\n\nOnaylıyor musunuz?`)) return;
    try {
        const fetchFn = window.apiFetch || fetch;
        const res  = await fetchFn('/api/cikis/temizle', {method: 'POST'});
        const data = await res.json();
        if (data.success) {
            allData = [];
            updateStats();
            render();
        } else {
            alert('Temizleme hatası: ' + (data.error || ''));
        }
    } catch (e) {
        alert('İşlem hatası: ' + e.message);
    }
});

document.getElementById('search-input').addEventListener('input', render);
document.getElementById('filter-col').addEventListener('change', render);

function esc(str) {
    if (window.esc) return window.esc(str);
    return String(str||'')
        .replace(/&/g,'&amp;')
        .replace(/</g,'&lt;')
        .replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;')
        .replace(/'/g,'&#39;');
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
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">{{ current_app_commit }}</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">{{ current_app_date }}</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">{{ current_app_msg }}</div>
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
                <a href="/istatistikler" class="sidebar-link">
                    <i class="fa-solid fa-chart-pie"></i>
                    <span>İstatistikler & Raporlar</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill {{ 'offline' if not is_system_active else '' }}">
                    <span class="status-indicator {{ system_status_cls | default('online') }}"></span>
                    <span>{{ system_status_text | default('Sistem Aktif') }}</span>
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
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">{{ current_app_commit }}</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">{{ current_app_date }}</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">{{ current_app_msg }}</div>
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
        window.LOCAL_SESSION_TOKEN = "{{ local_session_token }}";
        if (window.LOCAL_SESSION_TOKEN && window.LOCAL_SESSION_TOKEN !== 'undefined') {
            localStorage.setItem('local_session_token', window.LOCAL_SESSION_TOKEN);
        } else if (localStorage.getItem('local_session_token') === 'undefined' || localStorage.getItem('local_session_token') === 'null') {
            localStorage.removeItem('local_session_token');
        }

        function getAuthHeaders(extraHeaders = {}) {
            const token = window.LOCAL_SESSION_TOKEN || localStorage.getItem('local_session_token') || '';
            const headers = {
                'X-Requested-With': 'XMLHttpRequest',
                ...extraHeaders
            };
            if (token && token !== 'undefined' && token !== 'null') {
                headers['X-Local-Token'] = token;
                headers['X-Session-Token'] = token;
            }
            return headers;
        }

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
                const res = await fetch('/api/depo_stoklari', {
                    headers: getAuthHeaders()
                });
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
            if (window.updateSystemStatusPill) window.updateSystemStatusPill('fetching', 'Bağlantı Kuruluyor...');
            
            try {
                const res = await fetch('/api/bkst/fetch_api', { 
                    method: 'POST',
                    headers: getAuthHeaders({ 'X-Requested-With': 'XMLHttpRequest' })
                });
                const data = await res.json();
                if (data.success) {
                    let pollAttempts = 0;
                    let finalStatus = null;
                    while (pollAttempts < 45) {
                        await new Promise(r => setTimeout(r, 1500));
                        pollAttempts++;
                        try {
                            const sRes = await fetch('/api/bkst/fetch_status');
                            if (sRes.ok) {
                                finalStatus = await sRes.json();
                                if (!finalStatus.running) break;
                            }
                        } catch (_) {}
                    }

                    if (finalStatus && finalStatus.online === true && finalStatus.fetched_count > 0) {
                        if (window.updateSystemStatusPill) window.updateSystemStatusPill(true, 'Sistem Aktif', `Bakanlıktan ${finalStatus.fetched_count} adet stok çekildi.`);
                        alert(`🟢 BKST verileri başarıyla güncellendi! Toplam ${finalStatus.fetched_count} adet stok sisteme yüklendi. (Sistem Aktif)`);
                        await loadDepoStoklari();
                    } else {
                        const msg = (finalStatus && finalStatus.message) || 'Bakanlık sunucusundan 0 adet veri çekildi.';
                        const locCnt = (finalStatus && finalStatus.local_count) || 0;
                        if (window.updateSystemStatusPill) window.updateSystemStatusPill(false, 'Sistem Deaktif', msg);
                        alert(`⚠️ BAKANLIK BAĞLANTI UYARISI: 0 adet veri çekildi!\n\n🔴 Sistem Deaktif durumdadır.\n\n${msg}\n\nYerel veritabanındaki son kayıtlı ${locCnt} adet stok kullanılmaya devam ediliyor.`);
                        await loadDepoStoklari();
                    }
                } else {
                    if (window.updateSystemStatusPill) window.updateSystemStatusPill(false, 'Sistem Deaktif', data.error);
                    alert('❌ Hata: ' + (data.error || 'Veri çekilemedi. Sistem Deaktif.'));
                }
            } catch (err) {
                if (window.updateSystemStatusPill) window.updateSystemStatusPill(false, 'Sistem Deaktif', err.message);
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

        function getGtin(p) {
            if (!p) return '-';
            let g = p['Gtin Numarası'] || p['Gtin / Barkod'] || p.GTIN || p.gtin || p.BARCODE || p.barkod || '';
            g = String(g).trim();
            if (g && g !== '-' && g !== 'None' && g !== 'nan' && g !== 'null' && g !== 'BELİRSİZ') {
                return g;
            }
            let qr = String(p['Karekod'] || p.tam_karekod || p.ham_karekod || p.KAREKOD || '').trim();
            if (qr.startsWith(']d2') || qr.startsWith(']Q3')) {
                qr = qr.substring(3);
            }
            if (qr.startsWith('01') && qr.length >= 16 && /^\d{14}$/.test(qr.substring(2, 16))) {
                return qr.substring(2, 16);
            }
            const match = qr.match(/(?:^|\D)01(\d{14})/);
            if (match) return match[1];
            const d14 = qr.match(/\d{14}/);
            if (d14) return d14[0];
            return '-';
        }

        function updateSummaryStats() {
            statTotal.textContent = allProducts.length;

            const gtinGroupMap = {};
            let sktCriticalCount = 0;

            allProducts.forEach(p => {
                const gtin = getGtin(p);
                const name = p['Ürün Adı'] || p.STOCKNAME || p.URUNADI || 'İsimsiz Ürün';
                const key = (gtin !== '-' ? gtin : '') + '___' + name;

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
                        getGtin(p), p['Gtin Numarası'], p['Gtin / Barkod'], p.BARCODE, p.GTIN,
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
                const name = p['Ürün Adı'] || p.STOCKNAME || p.URUNADI || 'Tanımsız Ürün';
                const gtin = getGtin(p);
                const key = (gtin !== '-' ? gtin : '') + '___' + name;

                if (!groups[key]) {
                    groups[key] = { gtin, name, key, products: [] };
                } else if (groups[key].gtin === '-' && gtin !== '-') {
                    groups[key].gtin = gtin;
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
                            <a class="product-link-btn btn-open-detail" data-group-key="${escAttr(key)}" style="cursor:pointer;">
                                <i class="fa-solid fa-box-open" style="color:#38bdf8;"></i>
                                <span class="prod-name-clamp" title="${esc(g.name)}"><b>${esc(g.name)}</b></span>
                            </a>
                        </td>
                        <td class="nowrap-cell"><span style="font-family:monospace; font-weight:600; color:#38bdf8;">${esc(g.gtin)}</span></td>
                        <td style="text-align:center;" class="nowrap-cell">
                            <span style="background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); padding:4px 12px; border-radius:8px; font-weight:800; font-size:0.9rem;">
                                ${count} Adet
                            </span>
                        </td>
                        <td class="nowrap-cell">${sktBadge}</td>
                        <td style="text-align:center;" class="nowrap-cell">
                            <button class="btn btn-outline btn-sm btn-open-detail" data-group-key="${escAttr(key)}" type="button">
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
                const gtin = getGtin(p);
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
                        <td class="nowrap-cell" style="font-family:monospace; color:#38bdf8; font-weight:600;">${esc(gtin)}</td>
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

        // Event delegation for opening detail modal
        tbody.addEventListener('click', (e) => {
            const trigger = e.target.closest('.btn-open-detail');
            if (trigger) {
                const groupKey = trigger.getAttribute('data-group-key');
                if (groupKey) openDetailModal(groupKey);
            }
        });

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
            if (window.esc) return window.esc(str);
            return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g, '&#39;');
        }
        function escAttr(str) {
            if (window.escAttr) return window.escAttr(str);
            return String(str || '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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
            overflow-y: auto;
            height: 480px;
            max-height: 480px;
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

        .btn-period-pill {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.12);
            color: var(--text-muted);
            padding: 0.35rem 0.85rem;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-period-pill:hover {
            background: rgba(88, 101, 242, 0.2);
            color: var(--text-main);
            border-color: rgba(88, 101, 242, 0.4);
        }
        .btn-period-pill.active {
            background: #2563eb;
            color: #fff;
            border-color: #3b82f6;
            font-weight: 600;
            box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);
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
                <a href="/istatistikler" class="sidebar-link">
                    <i class="fa-solid fa-chart-pie"></i>
                    <span>İstatistikler & Raporlar</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill {{ 'offline' if not is_system_active else '' }}">
                    <span class="status-indicator {{ system_status_cls | default('online') }}"></span>
                    <span>{{ system_status_text | default('Sistem Aktif') }}</span>
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

                <div class="period-filter-wrapper margin-top-sm" style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:0.6rem; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); padding:0.6rem 0.9rem; border-radius:10px;">
                    <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
                        <span style="font-size:0.8rem; color:var(--text-muted); font-weight:600;"><i class="fa-regular fa-calendar-days" style="color:#38bdf8;"></i> Dönem:</span>
                        <div class="period-pills" id="period-pill-group" style="display:inline-flex; gap:0.4rem; flex-wrap:wrap;">
                            <button type="button" class="btn-period-pill" data-period="son_30" title="Son 30 gün içinde kesilen faturalar">Son 30 Gün</button>
                            <button type="button" class="btn-period-pill" data-period="son_90" title="Son 3 ay içinde kesilen faturalar">Son 90 Gün</button>
                            <button type="button" class="btn-period-pill active" data-period="son_180" title="Son 6 ay (Sezon) - Yıl devirlerinden etkilenmez">Son 6 Ay (Sezon)</button>
                            <button type="button" class="btn-period-pill" data-period="son_365" title="Son 1 yıl içindeki faturalar">Son 1 Yıl</button>
                            <button type="button" class="btn-period-pill" data-period="tum" title="Tüm zamanlar (Filtresiz)">Tüm Zamanlar</button>
                        </div>
                    </div>
                    <div id="period-range-indicator" style="font-size:0.75rem; color:#38bdf8; font-weight:600;">
                        <i class="fa-solid fa-clock-rotate-left"></i> <span id="lbl-range-info">Son 6 Ay / Sezon</span>
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
                <div class="data-table-wrapper margin-top-md">
                    <table class="kabul-table" id="table-details">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Karekod</th>
                                <th>Ürün Adı</th>
                                <th>GTIN / Barkod</th>
                                <th>Seri No</th>
                                <th>SKT</th>
                                <th style="text-align:center;">Durum</th>
                            </tr>
                        </thead>
                        <tbody id="tbody-details">
                            <tr>
                                <td colspan="7" style="text-align:center; color:var(--text-muted); padding:2rem;">
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
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">{{ current_app_commit }}</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">{{ current_app_date }}</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">{{ current_app_msg }}</div>
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
        window.LOCAL_SESSION_TOKEN = "{{ local_session_token }}";
        if (window.LOCAL_SESSION_TOKEN && window.LOCAL_SESSION_TOKEN !== 'undefined') {
            localStorage.setItem('local_session_token', window.LOCAL_SESSION_TOKEN);
        } else if (localStorage.getItem('local_session_token') === 'undefined' || localStorage.getItem('local_session_token') === 'null') {
            localStorage.removeItem('local_session_token');
        }

        function getAuthHeaders(extraHeaders = {}) {
            const token = window.LOCAL_SESSION_TOKEN || localStorage.getItem('local_session_token') || '';
            const headers = {
                'X-Requested-With': 'XMLHttpRequest',
                ...extraHeaders
            };
            if (token && token !== 'undefined' && token !== 'null') {
                headers['X-Local-Token'] = token;
                headers['X-Session-Token'] = token;
            }
            return headers;
        }

        let selectedNotificationData = null;
        let currentPeriod = localStorage.getItem('depo_kabul_period') || 'son_180';

        function updatePeriodPillsUI(activePeriod) {
            document.querySelectorAll('.btn-period-pill').forEach(b => {
                const isAct = (b.getAttribute('data-period') === activePeriod);
                b.classList.toggle('active', isAct);
            });
        }
        updatePeriodPillsUI(currentPeriod);

        // Dönem butonuna tıklandığında anında Bakanlık sorgusunu çalıştır
        document.querySelectorAll('.btn-period-pill').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const p = btn.getAttribute('data-period') || 'son_180';
                currentPeriod = p;
                localStorage.setItem('depo_kabul_period', p);
                updatePeriodPillsUI(p);
                fetchIncomingNotifications(p);
            });
        });

        const btnFetchIncoming = document.getElementById('btn-fetch-incoming');
        const btnAcceptWarehouse = document.getElementById('btn-accept-warehouse');
        const incomingMsgBox = document.getElementById('incoming-msg-box');
        const acceptMsgBox = document.getElementById('accept-msg-box');
        const tbodyIncoming = document.getElementById('tbody-incoming');
        const tbodyDetails = document.getElementById('tbody-details');
        const lblSelectedDoc = document.getElementById('lbl-selected-doc');
        const lblSelectedCount = document.getElementById('lbl-selected-count');

        // Gelen Bildirimleri Çeken Ana Fonksiyon
        async function fetchIncomingNotifications(periodToFetch) {
            const period = periodToFetch || currentPeriod || 'son_180';
            btnFetchIncoming.disabled = true;
            btnFetchIncoming.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> BKST Sunucusundan Bildirimler Çekiliyor...';
            showMsg(incomingMsgBox, 'loading', 'Bakanlık gelen bildirim listesi sorgulanıyor, lütfen bekleyin...');

            try {
                const res = await fetch('/api/depo_kabul/gelen_listesi', {
                    method: 'POST',
                    headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ period: period })
                });
                const data = await res.json();

                if (data.success) {
                    showMsg(incomingMsgBox, 'success', data.message || 'Gelen bildirimler başarıyla çekildi.');
                    const rangeLbl = document.getElementById('lbl-range-info');
                    if (rangeLbl && data.range_label) {
                        rangeLbl.textContent = data.range_label;
                    }
                    renderIncomingTable(data.notifications || []);
                    try {
                        sessionStorage.setItem('bkst_incoming_cache', JSON.stringify({
                            notifications: data.notifications || [],
                            message: data.message || '',
                            period: period,
                            range_label: data.range_label || '',
                            timestamp: Date.now()
                        }));
                    } catch (e) {}
                } else {
                    showMsg(incomingMsgBox, 'error', data.error || 'Gelen bildirimler çekilemedi.');
                }
            } catch (err) {
                showMsg(incomingMsgBox, 'error', 'Bağlantı hatası: ' + err.message);
            } finally {
                btnFetchIncoming.disabled = false;
                btnFetchIncoming.innerHTML = '<i class="fa-solid fa-cloud-arrow-down"></i> 📥 1. Gelen Mal Alım Bildirimlerini Çek (BKST)';
            }
        }

        btnFetchIncoming.addEventListener('click', () => {
            fetchIncomingNotifications(currentPeriod);
        });

        // Sayfa ilk açıldığında veya sekmeler arası geçişte önceki çekilen bildirimleri geri yükle
        try {
            const cachedRaw = sessionStorage.getItem('bkst_incoming_cache');
            if (cachedRaw) {
                const cached = JSON.parse(cachedRaw);
                if (cached && cached.notifications && cached.notifications.length > 0) {
                    if (cached.period) {
                        currentPeriod = cached.period;
                        updatePeriodPillsUI(currentPeriod);
                    }
                    const rangeLbl = document.getElementById('lbl-range-info');
                    if (rangeLbl && cached.range_label) {
                        rangeLbl.textContent = cached.range_label;
                    }
                    renderIncomingTable(cached.notifications);
                    showMsg(incomingMsgBox, 'success', cached.message || `Önceki sorgu sonuçları yüklendi (${cached.notifications.length} bildirim).`);
                }
            }
        } catch (e) {
            console.warn("Önbellek geri yükleme hatası:", e);
        }

        // Bildirim Tablosunu Bas
        function renderIncomingTable(items) {
            const validItems = (items || []).filter(item => {
                const state = String(item.HEADERSTATE || '').toUpperCase();
                return !state.includes('IPTAL') && !state.includes('İPTAL');
            });

            if (!validItems || validItems.length === 0) {
                tbodyIncoming.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:2rem;">Gelen bildirim bulunamadı.</td></tr>';
                return;
            }

            tbodyIncoming.innerHTML = validItems.map((item, index) => {
                const isKabulBekliyor = (item.HEADERSTATE === 'Kabul Bekliyor');
                const waitingCnt = item.WAITINGCOUNT || 0;
                const badgeHtml = isKabulBekliyor
                    ? `<span style="background:rgba(16,185,129,0.18); color:#34d399; border:1px solid rgba(16,185,129,0.4); padding:3px 10px; border-radius:999px; font-weight:700; font-size:0.78rem; display:inline-flex; align-items:center; gap:5px;"><i class="fa-solid fa-circle-dot" style="font-size:0.6rem;"></i> Kabul Bekliyor${waitingCnt > 0 ? ` (${waitingCnt} bekleyen)` : ''}</span>`
                    : `<span style="background:rgba(56,189,248,0.12); color:#93c5fd; border:1px solid rgba(56,189,248,0.25); padding:3px 10px; border-radius:999px; font-weight:600; font-size:0.78rem; display:inline-flex; align-items:center; gap:5px;"><i class="fa-solid fa-boxes-packing" style="font-size:0.7rem;"></i> Stoğa Alınmış</span>`;

                return `
                <tr onclick="selectNotification(${index}, this)" id="row-incoming-${index}">
                    <td style="text-align:center;"><i class="fa-regular fa-circle radio-icon"></i></td>
                    <td><b>${esc(item.CompanyTitle || item.SENDER || 'Bilinmiyor')}</b></td>
                    <td><span style="font-family:monospace; color:#38bdf8;">${esc(item.WAYBILLNUMBER || '-')}</span></td>
                    <td>${esc(item.WAYBILLDATE || item.OPERATIONDATE || '-')}</td>
                    <td><span class="badge-type" style="background:rgba(16,185,129,0.2); color:#6ee7b7; border:1px solid rgba(16,185,129,0.4); padding:2px 8px; border-radius:6px; font-weight:700;">MAL ALIM</span></td>
                    <td><b>${item.PRODUCTCOUNT || 0}</b></td>
                    <td>${badgeHtml}</td>
                </tr>
                `;
            }).join('');

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

            // Detayları Yükle
            tbodyDetails.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:2rem;"><i class="fa-solid fa-spinner fa-spin"></i> Ürün detayları getiriliyor...</td></tr>';

            try {
                const res = await fetch('/api/depo_kabul/detay/' + encodeURIComponent(item.HEADERID || item.WAYBILLNUMBER || index), {
                    headers: getAuthHeaders()
                });
                const data = await res.json();
                if (data.success && data.products && data.products.length > 0) {
                    selectedNotificationData.products = data.products;
                    lblSelectedCount.textContent = data.products.length + ' Adet';

                    const isWaiting = (data.overall_status === 'Kabul Bekliyor') || (data.bekleyen_adet && data.bekleyen_adet > 0);

                    lblSelectedDoc.innerHTML = esc(item.WAYBILLNUMBER || 'Belge #' + (index+1)) + ' ' + 
                        (isWaiting 
                            ? '<span style="font-size:0.75rem; background:rgba(16,185,129,0.2); color:#34d399; padding:2px 8px; border-radius:6px; font-weight:700;">🟢 Kabul Bekliyor</span>' 
                            : '<span style="font-size:0.75rem; background:rgba(56,189,248,0.2); color:#93c5fd; padding:2px 8px; border-radius:6px; font-weight:600;">📦 Stoğa Alınmış</span>');

                    if (isWaiting) {
                        btnAcceptWarehouse.disabled = false;
                        btnAcceptWarehouse.style.background = '#10b981';
                        btnAcceptWarehouse.style.cursor = 'pointer';
                        btnAcceptWarehouse.style.opacity = '1';
                        btnAcceptWarehouse.innerHTML = `<i class="fa-solid fa-circle-check"></i> 🟢 Tek Tuşla Depoya Kabul Et (${data.bekleyen_adet || data.products.length} Adet Bekliyor)`;
                    } else {
                        btnAcceptWarehouse.disabled = true;
                        btnAcceptWarehouse.style.background = 'rgba(255,255,255,0.08)';
                        btnAcceptWarehouse.style.cursor = 'not-allowed';
                        btnAcceptWarehouse.style.opacity = '0.6';
                        btnAcceptWarehouse.innerHTML = `<i class="fa-solid fa-check-double"></i> ✅ Bu Belgedeki Ürünler Zaten Stoğa Alınmış`;
                    }

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
                tbodyDetails.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:1.5rem;">Bu bildirime ait karekod detayı bulunamadı.</td></tr>';
                return;
            }

            tbodyDetails.innerHTML = prods.map((p, i) => {
                const isBekliyor = (p.durum === 'Kabul Bekliyor');
                const durumPill = isBekliyor
                    ? `<span style="color:#34d399; font-weight:700; font-size:0.8rem;"><i class="fa-solid fa-circle-dot"></i> Kabul Bekliyor</span>`
                    : `<span style="color:#93c5fd; font-size:0.8rem;"><i class="fa-solid fa-boxes-packing"></i> Stoğa Alınmış</span>`;

                return `
                <tr>
                    <td>${i+1}</td>
                    <td style="font-family:monospace; font-size:0.78rem; color:#6ee7b7;">${esc(p.Karekod || p.KAREKOD || '-')}</td>
                    <td><b>${esc(p['Ürün Adı'] || p.STOCKNAME || '-')}</b></td>
                    <td>${esc(p['Gtin / Barkod'] || p.BARCODE || '-')}</td>
                    <td>${esc(p['Seri Numarası'] || p.SERIALNUMBER || '-')}</td>
                    <td>${esc(p['Son Kullanma Tarihi'] || p.SKT || '-')}</td>
                    <td style="text-align:center;">${durumPill}</td>
                </tr>
                `;
            }).join('');
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
                    headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
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

                    if (selectedNotificationData) {
                        selectedNotificationData.HEADERSTATE = 'Stoğa Alınmış';
                        selectedNotificationData.WAITINGCOUNT = 0;
                        if (selectedNotificationData.products) {
                            selectedNotificationData.products.forEach(p => p.durum = 'Stoğa Alınmış');
                            renderDetailsTable(selectedNotificationData.products);
                        }
                    }
                    if (window.incomingItems) {
                        renderIncomingTable(window.incomingItems);
                        try {
                            sessionStorage.setItem('bkst_incoming_cache', JSON.stringify({
                                notifications: window.incomingItems,
                                message: 'Bildirimler güncellendi.',
                                period: currentPeriod,
                                timestamp: Date.now()
                            }));
                        } catch (e) {}
                    }
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

    </script>
    <script src="/static/app.js?v=20261009_v322"></script>
</body>
</html>

```

---

### 📁 `templates/istatistikler.html`

```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>İstatistikler & Satış Raporları - QR Compare</title>
    
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.png">
    <link rel="shortcut icon" href="/static/favicon.ico">

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link rel="stylesheet" href="/static/style.css">
    <script src="/static/chart.umd.min.js"></script>
    <script>
        window.ensureChartJs = function(callback) {
            if (typeof Chart !== 'undefined') {
                if (callback) callback();
                return;
            }
            const s = document.createElement('script');
            s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js';
            s.onload = () => { if (callback) callback(); };
            s.onerror = () => { console.warn('Chart.js CDN yüklenemedi.'); };
            document.head.appendChild(s);
        };
        if (typeof Chart === 'undefined') {
            window.ensureChartJs();
        }
    </script>

    <style>
        .stats-kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }

        .stats-kpi-card {
            background: rgba(15, 23, 42, 0.7);
            backdrop-filter: blur(14px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
            padding: 1.25rem 1.4rem;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }

        .stats-kpi-card:hover {
            transform: translateY(-2px);
            border-color: rgba(16, 185, 129, 0.3);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 0 15px rgba(16, 185, 129, 0.1);
        }

        .stats-kpi-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--card-accent, #10b981), transparent);
        }

        .kpi-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 0.6rem;
        }

        .kpi-label {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #94a3b8;
            font-weight: 600;
        }

        .kpi-icon {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1rem;
            background: rgba(255, 255, 255, 0.05);
            color: var(--card-accent, #10b981);
        }

        .kpi-value {
            font-family: var(--font-outfit);
            font-size: 1.85rem;
            font-weight: 700;
            color: #fff;
            line-height: 1.2;
            margin-bottom: 0.25rem;
        }

        .kpi-sub {
            font-size: 0.78rem;
            color: #64748b;
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }

        .kpi-sub .badge-pill {
            padding: 0.15rem 0.45rem;
            border-radius: 6px;
            font-size: 0.7rem;
            font-weight: 600;
        }

        /* Filter Toolbar */
        .filter-toolbar {
            background: rgba(15, 23, 42, 0.65);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
            padding: 1rem 1.4rem;
            margin-bottom: 1.75rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 1rem;
        }

        .year-pills {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            flex-wrap: wrap;
        }

        .year-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #cbd5e1;
            padding: 0.45rem 0.9rem;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .year-btn:hover {
            background: rgba(255, 255, 255, 0.1);
            color: #fff;
            border-color: rgba(255, 255, 255, 0.25);
        }

        .year-btn.active {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            border-color: #10b981;
            color: #fff;
            box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);
        }

        .custom-date-box {
            display: none;
            align-items: center;
            gap: 0.5rem;
        }

        .custom-date-box.show {
            display: flex;
        }

        .date-input {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #fff;
            padding: 0.45rem 0.75rem;
            border-radius: 8px;
            font-size: 0.82rem;
            font-family: inherit;
            outline: none;
        }

        .date-input:focus {
            border-color: #10b981;
        }

        .action-btns {
            display: flex;
            align-items: center;
            gap: 0.6rem;
            flex-wrap: wrap;
        }

        .btn-act {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            text-decoration: none;
            border: none;
        }

        .btn-excel {
            background: linear-gradient(135deg, #059669 0%, #047857 100%);
            color: #fff;
            box-shadow: 0 4px 12px rgba(5, 150, 105, 0.25);
        }

        .btn-excel:hover {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            box-shadow: 0 6px 16px rgba(16, 185, 129, 0.35);
        }

        .btn-print {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #e2e8f0;
        }

        .btn-print:hover {
            background: rgba(255, 255, 255, 0.15);
            color: #fff;
        }

        .btn-sample {
            background: rgba(99, 102, 241, 0.15);
            border: 1px solid rgba(99, 102, 241, 0.3);
            color: #a5b4fc;
        }

        .btn-sample:hover {
            background: rgba(99, 102, 241, 0.25);
            color: #c7d2fe;
        }

        /* Charts Grid */
        .charts-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(480px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        @media (max-width: 992px) {
            .charts-grid {
                grid-template-columns: 1fr;
            }
        }

        .chart-box {
            background: rgba(15, 23, 42, 0.7);
            backdrop-filter: blur(14px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
            padding: 1.4rem;
            position: relative;
        }

        .chart-box-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 1.2rem;
        }

        .chart-title {
            font-family: var(--font-outfit);
            font-size: 1.05rem;
            font-weight: 600;
            color: #f1f5f9;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .chart-subtitle {
            font-size: 0.78rem;
            color: #64748b;
        }

        .chart-canvas-container {
            position: relative;
            height: 280px;
            width: 100%;
        }

        /* Table Section */
        .stats-table-section {
            background: rgba(15, 23, 42, 0.7);
            backdrop-filter: blur(14px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
            padding: 1.5rem;
            margin-bottom: 2rem;
        }

        .tab-nav {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            margin-bottom: 1.25rem;
            padding-bottom: 0.5rem;
        }

        .tab-btn {
            background: transparent;
            border: none;
            color: #94a3b8;
            padding: 0.6rem 1.1rem;
            border-radius: 8px;
            font-size: 0.9rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .tab-btn:hover {
            color: #fff;
            background: rgba(255, 255, 255, 0.04);
        }

        .tab-btn.active {
            color: #10b981;
            background: rgba(16, 185, 129, 0.1);
        }

        .table-responsive {
            overflow-x: auto;
        }

        .stats-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 0.88rem;
        }

        .stats-table th {
            background: rgba(0, 0, 0, 0.25);
            color: #94a3b8;
            font-weight: 600;
            text-align: left;
            padding: 0.85rem 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            white-space: nowrap;
        }

        .stats-table td {
            padding: 0.85rem 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            color: #e2e8f0;
            vertical-align: middle;
        }

        .stats-table tr:hover td {
            background: rgba(255, 255, 255, 0.02);
        }

        .progress-bar-wrap {
            width: 100px;
            height: 6px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 999px;
            overflow: hidden;
            display: inline-block;
            vertical-align: middle;
            margin-right: 0.5rem;
        }

        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, #10b981, #06b6d4);
            border-radius: 999px;
        }

        /* Print Media */
        @media print {
            .sidebar, .glass-bg-decor1, .glass-bg-decor2, .filter-toolbar, .tab-nav, .btn-act {
                display: none !important;
            }
            body, .app-wrapper, .main-content {
                background: #fff !important;
                color: #000 !important;
                padding: 0 !important;
                margin: 0 !important;
            }
            .stats-kpi-card, .chart-box, .stats-table-section {
                border: 1px solid #ccc !important;
                box-shadow: none !important;
                background: #fff !important;
                color: #000 !important;
            }
            .kpi-value, .chart-title {
                color: #000 !important;
            }
            .stats-table th {
                background: #eee !important;
                color: #000 !important;
            }
            .stats-table td {
                color: #000 !important;
            }
        }
    </style>
</head>
<body class="sidebar-layout">
    <div class="glass-bg-decor1"></div>
    <div class="glass-bg-decor2"></div>
    
    <div class="app-wrapper">
        <!-- SOL SIDEBAR NAVİGASYON -->
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
                <a href="/istatistikler" class="sidebar-link active">
                    <i class="fa-solid fa-chart-pie"></i>
                    <span>İstatistikler & Raporlar</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill {{ 'offline' if not is_system_active else '' }}">
                    <span class="status-indicator {{ system_status_cls | default('online') }}"></span>
                    <span>{{ system_status_text | default('Sistem Aktif') }}</span>
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
                    <span>{{ current_app_version }}</span>
                </div>
            </div>
        </aside>

        <!-- ANA İÇERİK ALANI -->
        <main class="main-content">
            <!-- Header Bar -->
            <div class="top-nav-bar" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem; margin-bottom:1.5rem;">
                <div>
                    <h1 style="font-family:var(--font-outfit); font-size:1.6rem; font-weight:700; color:#fff; display:flex; align-items:center; gap:0.6rem;">
                        <i class="fa-solid fa-chart-pie" style="color:#10b981;"></i>
                        İstatistikler & Satış Raporları
                    </h1>
                    <p style="font-size:0.86rem; color:#94a3b8; margin-top:2px;">
                        Geçmiş yıllar, aylık dağılımlar ve ürün bazlı detaylı çıkış istatistikleri
                    </p>
                </div>
                <div>
                    <button id="btn-reset-stats-header" style="background: linear-gradient(135deg, #ef4444, #dc2626); color:#fff; border:none; padding:0.65rem 1.3rem; border-radius:10px; font-weight:700; font-size:0.92rem; display:flex; align-items:center; gap:0.55rem; cursor:pointer; box-shadow: 0 4px 14px rgba(239,68,68,0.4);">
                        <i class="fa-solid fa-trash-can"></i> İstatistikleri Sıfırla
                    </button>
                </div>
            </div>

            <!-- Filter Toolbar -->
            <div class="filter-toolbar">
                <div class="year-pills" id="year-pills-container">
                    <span style="font-size:0.8rem; font-weight:600; color:#64748b; margin-right:0.3rem;"><i class="fa-solid fa-calendar"></i> DÖNEM:</span>
                    <button class="year-btn active" data-year="tum">Tüm Yıllar</button>
                    <button class="year-btn" data-year="2026">2026</button>
                    <button class="year-btn" data-year="2025">2025</button>
                    <button class="year-btn" data-year="2024">2024</button>
                    <button class="year-btn" data-year="2023">2023</button>
                    <button class="year-btn" data-year="custom"><i class="fa-solid fa-sliders"></i> Özel Aralık</button>
                </div>

                <div class="custom-date-box" id="custom-date-box">
                    <input type="date" id="input-date-start" class="date-input" title="Başlangıç Tarihi">
                    <span style="color:#64748b;">-</span>
                    <input type="date" id="input-date-end" class="date-input" title="Bitiş Tarihi">
                    <button class="year-btn" id="btn-apply-custom-date" style="background:#10b981; color:#fff; border-color:#10b981;">Filtrele</button>
                </div>

                <div class="action-btns">
                    <button class="btn-act btn-excel" id="btn-download-excel" title="Detaylı Excel Raporu İndir">
                        <i class="fa-solid fa-file-excel"></i> Excel İndir (.xlsx)
                    </button>
                    <button class="btn-act btn-print" onclick="window.print()" title="Raporu Yazdır veya PDF olarak kaydet">
                        <i class="fa-solid fa-print"></i> Yazdır / PDF
                    </button>
                    <button class="btn-act btn-sample" id="btn-sample-data" title="2024-2025 yıllarına ait test verisi ekle">
                        <i class="fa-solid fa-wand-magic-sparkles"></i> Örnek Geçmiş Veri
                    </button>
                    <button class="btn-act btn-reset" id="btn-reset-stats" style="background:rgba(239, 68, 68, 0.16); border:1px solid rgba(239, 68, 68, 0.4); color:#fca5a5;" title="Kalıcı satış arşivi ve tüm geçmiş istatistik verilerini sıfırla">
                        <i class="fa-solid fa-trash-can"></i> İstatistikleri Sıfırla
                    </button>
                </div>
            </div>

            <!-- 6 KPI CARDS -->
            <div class="stats-kpi-grid">
                <!-- KPI 1 -->
                <div class="stats-kpi-card" style="--card-accent: #10b981;">
                    <div class="kpi-head">
                        <span class="kpi-label">Toplam Çıkış Adedi</span>
                        <div class="kpi-icon"><i class="fa-solid fa-boxes-stacked"></i></div>
                    </div>
                    <div class="kpi-value" id="kpi-total-exits">0</div>
                    <div class="kpi-sub">
                        <span>Seçilen dönemdeki toplam ürün</span>
                    </div>
                </div>

                <!-- KPI 2 -->
                <div class="stats-kpi-card" style="--card-accent: #06b6d4;">
                    <div class="kpi-head">
                        <span class="kpi-label">Satılan Kalem Çeşidi</span>
                        <div class="kpi-icon"><i class="fa-solid fa-shapes"></i></div>
                    </div>
                    <div class="kpi-value" id="kpi-unique-products">0</div>
                    <div class="kpi-sub">
                        <span>Farklı ilaç / ürün çeşidi</span>
                    </div>
                </div>

                <!-- KPI 3 -->
                <div class="stats-kpi-card" style="--card-accent: #f59e0b;">
                    <div class="kpi-head">
                        <span class="kpi-label">En Çok Satan Lider Ürün</span>
                        <div class="kpi-icon"><i class="fa-solid fa-trophy"></i></div>
                    </div>
                    <div class="kpi-value" id="kpi-top-product" style="font-size:1.25rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="-">-</div>
                    <div class="kpi-sub">
                        <span id="kpi-top-product-count" class="badge-pill" style="background:rgba(245,158,11,0.2); color:#fbbf24;">0 Adet</span>
                        <span>çıkış ile lider</span>
                    </div>
                </div>

                <!-- KPI 4 -->
                <div class="stats-kpi-card" style="--card-accent: #8b5cf6;">
                    <div class="kpi-head">
                        <span class="kpi-label">Koli / Tekil Dağılımı</span>
                        <div class="kpi-icon"><i class="fa-solid fa-pallet"></i></div>
                    </div>
                    <div class="kpi-value" id="kpi-carton-ratio">-%</div>
                    <div class="kpi-sub">
                        <span id="kpi-carton-detail">0 Koli / 0 Tekil Kutu</span>
                    </div>
                </div>

                <!-- KPI 5 -->
                <div class="stats-kpi-card" style="--card-accent: #3b82f6;">
                    <div class="kpi-head">
                        <span class="kpi-label">Günlük Çıkış Ortalaması</span>
                        <div class="kpi-icon"><i class="fa-solid fa-bolt"></i></div>
                    </div>
                    <div class="kpi-value" id="kpi-daily-avg">0</div>
                    <div class="kpi-sub">
                        <span>Kutu / aktif gün başına</span>
                    </div>
                </div>

                <!-- KPI 6 -->
                <div class="stats-kpi-card" style="--card-accent: #ec4899;">
                    <div class="kpi-head">
                        <span class="kpi-label">Tekrar Okutma Oranı</span>
                        <div class="kpi-icon"><i class="fa-solid fa-triangle-exclamation"></i></div>
                    </div>
                    <div class="kpi-value" id="kpi-repeat-ratio">0%</div>
                    <div class="kpi-sub">
                        <span id="kpi-repeat-count">0 mükerrer okutma</span>
                    </div>
                </div>
            </div>

            <!-- CHARTS GRID -->
            <div class="charts-grid">
                <!-- CHART 1: Aylık Satış Dağılımı -->
                <div class="chart-box">
                    <div class="chart-box-header">
                        <div>
                            <div class="chart-title"><i class="fa-solid fa-chart-column" style="color:#10b981;"></i> Aylık Çıkış Dağılımı</div>
                            <div class="chart-subtitle" id="chart1-subtitle">Yıl içi 12 ayın satış adetleri</div>
                        </div>
                    </div>
                    <div class="chart-canvas-container">
                        <canvas id="canvas-monthly"></canvas>
                    </div>
                </div>

                <!-- CHART 2: Yıllık Karşılaştırma -->
                <div class="chart-box">
                    <div class="chart-box-header">
                        <div>
                            <div class="chart-title"><i class="fa-solid fa-chart-line" style="color:#06b6d4;"></i> Yıllık Satış Karşılaştırması</div>
                            <div class="chart-subtitle">Tüm yıllara ait toplam çıkış trendi</div>
                        </div>
                    </div>
                    <div class="chart-canvas-container">
                        <canvas id="canvas-yearly"></canvas>
                    </div>
                </div>

                <!-- CHART 3: En Çok Satanlar Pazar Payı -->
                <div class="chart-box">
                    <div class="chart-box-header">
                        <div>
                            <div class="chart-title"><i class="fa-solid fa-chart-pie" style="color:#f59e0b;"></i> En Çok Satan Ürünler Payı</div>
                            <div class="chart-subtitle">Lider ürünlerin toplam çıkıştaki yüzdesi</div>
                        </div>
                    </div>
                    <div class="chart-canvas-container">
                        <canvas id="canvas-top-products"></canvas>
                    </div>
                </div>

                <!-- CHART 4: Haftanın Günleri -->
                <div class="chart-box">
                    <div class="chart-box-header">
                        <div>
                            <div class="chart-title"><i class="fa-solid fa-calendar-week" style="color:#8b5cf6;"></i> Gün Bazlı Çıkış Yoğunluğu</div>
                            <div class="chart-subtitle">Haftanın günlerine göre operasyon dağılımı</div>
                        </div>
                    </div>
                    <div class="chart-canvas-container">
                        <canvas id="canvas-weekday"></canvas>
                    </div>
                </div>
            </div>

            <!-- DETAYLI TABLOLAR & YÖNETİM -->
            <div class="stats-table-section">
                <div class="tab-nav">
                    <button class="tab-btn active" data-tab="tab-products">
                        <i class="fa-solid fa-list-ol"></i> En Çok Satan Ürünler
                    </button>
                    <button class="tab-btn" data-tab="tab-batches">
                        <i class="fa-solid fa-barcode"></i> Parti / Lot Dağılımı
                    </button>
                    <button class="tab-btn" data-tab="tab-monthly-detail">
                        <i class="fa-solid fa-calendar-days"></i> Aylık Tablo
                    </button>
                    <button class="tab-btn" data-tab="tab-import">
                        <i class="fa-solid fa-file-import"></i> Geçmiş Satış Yükle (Excel)
                    </button>
                </div>

                <!-- TAB 1: ÜRÜNLER -->
                <div class="tab-content" id="tab-products">
                    <div style="margin-bottom:1rem; display:flex; justify-content:space-between; align-items:center;">
                        <input type="text" id="input-search-products" placeholder="Ürün adı veya GTIN ile ara..." style="background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12); padding:0.5rem 0.9rem; border-radius:8px; color:#fff; font-size:0.85rem; width:280px; outline:none;">
                        <span style="font-size:0.8rem; color:#64748b;" id="lbl-product-count">0 ürün listeleniyor</span>
                    </div>
                    <div class="table-responsive">
                        <table class="stats-table">
                            <thead>
                                <tr>
                                    <th style="width:50px;">Sıra</th>
                                    <th>Ürün Adı</th>
                                    <th>GTIN / Barkod</th>
                                    <th style="text-align:right;">Toplam Çıkış</th>
                                    <th style="text-align:center;">Koli Payı</th>
                                    <th>Pazar Payı</th>
                                    <th>Son Çıkış Tarihi</th>
                                </tr>
                            </thead>
                            <tbody id="tbody-top-products">
                                <tr><td colspan="7" style="text-align:center; color:#64748b; padding:2rem;"><i class="fa-solid fa-spinner fa-spin"></i> İstatistikler yükleniyor...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- TAB 2: PARTİ / LOT -->
                <div class="tab-content" id="tab-batches" style="display:none;">
                    <div class="table-responsive">
                        <table class="stats-table">
                            <thead>
                                <tr>
                                    <th style="width:50px;">Sıra</th>
                                    <th>Parti Numarası</th>
                                    <th>Ürün Adı</th>
                                    <th style="text-align:right;">Çıkış Adedi</th>
                                    <th>Son Kullanma Tarihi (SKT)</th>
                                </tr>
                            </thead>
                            <tbody id="tbody-batches">
                                <tr><td colspan="5" style="text-align:center; color:#64748b; padding:2rem;">Parti verisi yükleniyor...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- TAB 3: AYLIK DETAY -->
                <div class="tab-content" id="tab-monthly-detail" style="display:none;">
                    <div class="table-responsive">
                        <table class="stats-table">
                            <thead>
                                <tr>
                                    <th style="width:60px;">Ay</th>
                                    <th>Dönem</th>
                                    <th style="text-align:right;">Çıkış Adedi (Kutu)</th>
                                    <th>Dönem İçi Payı</th>
                                </tr>
                            </thead>
                            <tbody id="tbody-monthly-detail">
                                <tr><td colspan="4" style="text-align:center; color:#64748b; padding:2rem;">Aylık veriler yükleniyor...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- TAB 4: EXCEL İLE GEÇMİŞ SATIŞ YÜKLEME -->
                <div class="tab-content" id="tab-import" style="display:none;">
                    <div style="max-width:650px; background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:1.5rem;">
                        <h3 style="font-size:1.05rem; font-weight:600; color:#fff; margin-bottom:0.4rem;">
                            <i class="fa-solid fa-file-excel" style="color:#10b981;"></i> Eski Yıllara Ait Satışları İçe Aktar
                        </h3>
                        <p style="font-size:0.82rem; color:#94a3b8; line-height:1.5; margin-bottom:1.2rem;">
                            Geçmiş yıllara (2023, 2024, 2025 vb.) ait eski satış kayıtlarınızı Excel formatında sisteme yükleyebilirsiniz. 
                            Dosyanızda <b>Tarih</b>, <b>Ürün Adı</b>, <b>Barkod/GTIN</b> ve <b>Parti No</b> sütunlarının bulunması yeterlidir.
                        </p>

                        <div style="display:flex; gap:0.75rem; align-items:center; margin-bottom:1rem;">
                            <input type="file" id="input-excel-file" accept=".xlsx, .xls" style="background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.15); padding:0.5rem; border-radius:8px; color:#cbd5e1; font-size:0.85rem; flex:1;">
                            <button id="btn-upload-excel" class="btn-act btn-excel">
                                <i class="fa-solid fa-cloud-arrow-up"></i> Yükle ve Aktar
                            </button>
                        </div>
                        <div id="upload-status-msg" style="font-size:0.85rem; margin-top:0.5rem;"></div>

                        <hr style="border:none; border-top:1px solid rgba(255,255,255,0.08); margin:1.5rem 0;">

                        <div style="display:flex; align-items:center; justify-content:space-between;">
                            <div>
                                <h4 style="font-size:0.9rem; font-weight:600; color:#cbd5e1;">Test & Demo Geçmiş Verisi</h4>
                                <p style="font-size:0.78rem; color:#64748b;">2024 ve 2025 yıllarına ait otomatik gerçekçi satış kayıtları oluşturun.</p>
                            </div>
                            <div style="display:flex; gap:0.5rem;">
                                <button id="btn-add-sample-data" class="btn-act btn-sample">
                                    <i class="fa-solid fa-plus-circle"></i> Örnek Veri Ekle
                                </button>
                                <button id="btn-clear-sample-data" class="btn-act" style="background:rgba(239,68,68,0.15); border:1px solid rgba(239,68,68,0.3); color:#f87171;">
                                    <i class="fa-solid fa-trash-can"></i> Temizle
                                </button>
                            </div>
                        </div>

                        <hr style="border:none; border-top:1px solid rgba(255,255,255,0.08); margin:1.5rem 0;">

                        <div style="display:flex; align-items:center; justify-content:space-between; background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25); border-radius:10px; padding:1.1rem 1.25rem;">
                            <div>
                                <h4 style="font-size:0.92rem; font-weight:700; color:#fca5a5; display:flex; align-items:center; gap:0.45rem;">
                                    <i class="fa-solid fa-triangle-exclamation"></i> Geçmiş İstatistikleri Sıfırla
                                </h4>
                                <p style="font-size:0.78rem; color:#94a3b8; margin-top:3px; line-height:1.4;">
                                    Kalıcı satış arşivindeki tüm geçmiş istatistik verilerini sıfırlar. Aktif çalışma listeniz bundan etkilenmez.
                                </p>
                            </div>
                            <div>
                                <button id="btn-reset-stats-tab" class="btn-act" style="background:rgba(239,68,68,0.22); border:1px solid rgba(239,68,68,0.5); color:#fca5a5; font-weight:700; padding:0.6rem 1.1rem; white-space:nowrap;">
                                    <i class="fa-solid fa-trash-can"></i> Tümünü Sıfırla
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </main>
    </div>

    <!-- JAVASCRIPT LOGIC -->
    <script>
        window.LOCAL_SESSION_TOKEN = "{{ local_session_token }}";
        if (window.LOCAL_SESSION_TOKEN && window.LOCAL_SESSION_TOKEN !== 'undefined') {
            localStorage.setItem('local_session_token', window.LOCAL_SESSION_TOKEN);
        } else if (localStorage.getItem('local_session_token') === 'undefined' || localStorage.getItem('local_session_token') === 'null') {
            localStorage.removeItem('local_session_token');
        }

        function getAuthHeaders(extraHeaders = {}) {
            const token = window.LOCAL_SESSION_TOKEN || localStorage.getItem('local_session_token') || '';
            const headers = {
                'X-Requested-With': 'XMLHttpRequest',
                ...extraHeaders
            };
            if (token && token !== 'undefined' && token !== 'null') {
                headers['X-Local-Token'] = token;
                headers['X-Session-Token'] = token;
            }
            return headers;
        }

        // Global State
        let currentYear = 'tum';
        let customStart = '';
        let customEnd = '';
        let statsData = null;

        // Chart instances
        let chartMonthly = null;
        let chartYearly = null;
        let chartTopProducts = null;
        let chartWeekday = null;

        // Elements
        const yearPillsContainer = document.getElementById('year-pills-container');
        const customDateBox = document.getElementById('custom-date-box');
        const inputDateStart = document.getElementById('input-date-start');
        const inputDateEnd = document.getElementById('input-date-end');
        const btnApplyCustomDate = document.getElementById('btn-apply-custom-date');
        const btnDownloadExcel = document.getElementById('btn-download-excel');
        const btnSampleData = document.getElementById('btn-sample-data');
        const btnAddSampleData = document.getElementById('btn-add-sample-data');
        const btnClearSampleData = document.getElementById('btn-clear-sample-data');
        const btnResetStatsHeader = document.getElementById('btn-reset-stats-header');
        const btnResetStats = document.getElementById('btn-reset-stats');
        const btnResetStatsTab = document.getElementById('btn-reset-stats-tab');
        const btnUploadExcel = document.getElementById('btn-upload-excel');
        const inputExcelFile = document.getElementById('input-excel-file');
        const uploadStatusMsg = document.getElementById('upload-status-msg');

        // Initialize
        function initStatistics() {
            setupYearFilters();
            setupTabs();
            setupSearch();
            loadStatistics();
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initStatistics);
        } else {
            initStatistics();
        }

        // Setup Year Filter Buttons
        function setupYearFilters() {
            yearPillsContainer.addEventListener('click', (e) => {
                const btn = e.target.closest('.year-btn');
                if (!btn) return;

                const yr = btn.dataset.year;
                if (!yr) return;

                document.querySelectorAll('.year-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');

                if (yr === 'custom') {
                    customDateBox.classList.add('show');
                } else {
                    customDateBox.classList.remove('show');
                    currentYear = yr;
                    customStart = '';
                    customEnd = '';
                    loadStatistics();
                }
            });

            btnApplyCustomDate.addEventListener('click', () => {
                customStart = inputDateStart.value;
                customEnd = inputDateEnd.value;
                if (!customStart && !customEnd) {
                    alert('Lütfen en az bir tarih seçin.');
                    return;
                }
                currentYear = 'tum';
                loadStatistics();
            });

            btnDownloadExcel.addEventListener('click', () => {
                let url = `/api/istatistikler/excel_indir?yil=${encodeURIComponent(currentYear)}`;
                if (customStart) url += `&baslangic=${encodeURIComponent(customStart)}`;
                if (customEnd) url += `&bitis=${encodeURIComponent(customEnd)}`;
                window.location.href = url;
            });

            // Sample historical data buttons
            const handleAddSample = async () => {
                if (!confirm('2024 ve 2025 yıllarına ait gerçekçi örnek satış kayıtları eklensin mi?')) return;
                try {
                    const res = await fetch('/api/istatistikler/ornek_gecmis_ekle', {
                        method: 'POST',
                        headers: getAuthHeaders({ 'Content-Type': 'application/json' })
                    });
                    const d = await res.json();
                    if (d.success) {
                        alert(d.message);
                        loadStatistics();
                    } else {
                        alert('Hata: ' + (d.error || 'İşlem başarısız'));
                    }
                } catch (e) {
                    alert('Bağlantı hatası: ' + e.message);
                }
            };

            const handleClearSample = async () => {
                if (!confirm('Eklenen örnek geçmiş satış verileri silinsin mi?')) return;
                try {
                    const res = await fetch('/api/istatistikler/ornek_gecmis_temizle', {
                        method: 'POST',
                        headers: getAuthHeaders({ 'Content-Type': 'application/json' })
                    });
                    const d = await res.json();
                    if (d.success) {
                        alert(d.message);
                        loadStatistics();
                    } else {
                        alert('Hata: ' + (d.error || 'İşlem başarısız'));
                    }
                } catch (e) {
                    alert('Bağlantı hatası: ' + e.message);
                }
            };

            btnSampleData.addEventListener('click', handleAddSample);
            btnAddSampleData.addEventListener('click', handleAddSample);
            btnClearSampleData.addEventListener('click', handleClearSample);

            // Reset All Historical Statistics Handler
            const handleResetStats = async () => {
                const yearText = (currentYear && currentYear !== 'tum') ? `${currentYear} yılı dahil tüm yıllara ait` : 'tüm yıllara ait';
                const promptMsg = `⚠️ DİKKAT: Kalıcı satış arşivindeki ${yearText} tüm geçmiş istatistik verileri sıfırlanacaktır.\n\n` +
                                  `• Bu işlem geri alınamaz!\n` +
                                  `• 'Barkod Okut & Çıkış' bölümündeki güncel çalışma sepetinizdeki ürünler SİLİNMEZ.\n\n` +
                                  `Geçmiş istatistik verilerini kalıcı olarak sıfırlamak istiyor musunuz?`;
                if (!confirm(promptMsg)) return;

                if (!confirm(`Son onay: Kalıcı arşivdeki tüm geçmiş satış verileri tamamen temizleniyor. Emin misiniz?`)) return;

                try {
                    const res = await fetch('/api/istatistikler/sifirla', {
                        method: 'POST',
                        headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
                        body: JSON.stringify({ yil: 'tum' })
                    });
                    const d = await res.json();
                    if (d.success) {
                        alert(`✅ ${d.message} (${d.silinen_adet || 0} kayıt temizlendi)`);
                        loadStatistics();
                    } else {
                        alert('Hata: ' + (d.error || 'Sıfırlama işlemi başarısız'));
                    }
                } catch (e) {
                    alert('Bağlantı hatası: ' + e.message);
                }
            };

            if (btnResetStatsHeader) btnResetStatsHeader.addEventListener('click', handleResetStats);
            if (btnResetStats) btnResetStats.addEventListener('click', handleResetStats);
            if (btnResetStatsTab) btnResetStatsTab.addEventListener('click', handleResetStats);

            // Excel Upload
            btnUploadExcel.addEventListener('click', async () => {
                const file = inputExcelFile.files[0];
                if (!file) {
                    alert('Lütfen bir Excel dosyası (.xlsx) seçin.');
                    return;
                }
                const formData = new FormData();
                formData.append('file', file);

                btnUploadExcel.disabled = true;
                btnUploadExcel.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Aktarılıyor...';
                uploadStatusMsg.innerHTML = '<span style="color:#94a3b8;"><i class="fa-solid fa-spinner fa-spin"></i> Dosya işleniyor, lütfen bekleyin...</span>';

                try {
                    const res = await fetch('/api/istatistikler/excel_yukle', {
                        method: 'POST',
                        headers: getAuthHeaders(),
                        body: formData
                    });
                    const d = await res.json();
                    if (d.success) {
                        uploadStatusMsg.innerHTML = `<span style="color:#10b981;"><i class="fa-solid fa-circle-check"></i> ${d.message}</span>`;
                        inputExcelFile.value = '';
                        loadStatistics();
                    } else {
                        uploadStatusMsg.innerHTML = `<span style="color:#ef4444;"><i class="fa-solid fa-circle-xmark"></i> Hata: ${d.error}</span>`;
                    }
                } catch (e) {
                    uploadStatusMsg.innerHTML = `<span style="color:#ef4444;"><i class="fa-solid fa-circle-xmark"></i> Bağlantı hatası: ${e.message}</span>`;
                } finally {
                    btnUploadExcel.disabled = false;
                    btnUploadExcel.innerHTML = '<i class="fa-solid fa-cloud-arrow-up"></i> Yükle ve Aktar';
                }
            });
        }

        // Setup Tab Navigation
        function setupTabs() {
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');

                    btn.classList.add('active');
                    const tabId = btn.dataset.tab;
                    const content = document.getElementById(tabId);
                    if (content) content.style.display = 'block';
                });
            });
        }

        // Setup Product Search
        function setupSearch() {
            const input = document.getElementById('input-search-products');
            input.addEventListener('input', () => {
                const q = input.value.trim().toLowerCase();
                if (!statsData || !statsData.en_cok_satanlar) return;
                const filtered = statsData.en_cok_satanlar.filter(p => 
                    (p.urun_adi || '').toLowerCase().includes(q) ||
                    (p.barkod || '').toLowerCase().includes(q)
                );
                renderTopProductsTable(filtered);
            });
        }

        function showTableError(msg) {
            const errHtml = `<tr><td colspan="7" style="text-align:center; color:#f87171; padding:2rem;"><i class="fa-solid fa-triangle-exclamation"></i> Veri yüklenemedi: ${esc(msg)}</td></tr>`;
            const tb1 = document.getElementById('tbody-top-products');
            if (tb1) tb1.innerHTML = errHtml;
            const tb2 = document.getElementById('tbody-batches');
            if (tb2) tb2.innerHTML = `<tr><td colspan="5" style="text-align:center; color:#f87171; padding:2rem;"><i class="fa-solid fa-triangle-exclamation"></i> Veri yüklenemedi: ${esc(msg)}</td></tr>`;
            const tb3 = document.getElementById('tbody-monthly-detail');
            if (tb3) tb3.innerHTML = `<tr><td colspan="4" style="text-align:center; color:#f87171; padding:2rem;"><i class="fa-solid fa-triangle-exclamation"></i> Veri yüklenemedi: ${esc(msg)}</td></tr>`;
        }

        // Load Statistics from API
        async function loadStatistics() {
            let url = `/api/istatistikler/ozet?yil=${encodeURIComponent(currentYear)}`;
            if (customStart) url += `&baslangic=${encodeURIComponent(customStart)}`;
            if (customEnd) url += `&bitis=${encodeURIComponent(customEnd)}`;

            try {
                const res = await (window.apiFetch || fetch)(url, { headers: getAuthHeaders() });
                const d = await res.json();

                if (d.success) {
                    statsData = d;
                    // Tabloları ve kartları önce yükle (grafik hatası olsa bile tablolar aksamaz)
                    try { renderKpiCards(d.kpi); } catch(e) { console.error('renderKpiCards error:', e); }
                    try { renderTopProductsTable(d.en_cok_satanlar || []); } catch(e) { console.error('renderTopProductsTable error:', e); }
                    try { renderBatchesTable(d.parti_dagilimi || []); } catch(e) { console.error('renderBatchesTable error:', e); }
                    try { renderMonthlyTable(d.aylik_grafik); } catch(e) { console.error('renderMonthlyTable error:', e); }
                    try { updateDynamicYearPills(d.mevcut_yillar || []); } catch(e) { console.error('updateDynamicYearPills error:', e); }
                    try { renderCharts(d); } catch(e) { console.error('renderCharts error:', e); }
                } else {
                    console.error('İstatistik yükleme hatası:', d.error);
                    showTableError(d.error || 'İstatistikler sunucudan alınamadı.');
                }
            } catch (err) {
                console.error('API hatası:', err);
                showTableError(err.message || 'Sunucuya bağlanılamadı.');
            }
        }

        // Update Dynamic Year Pills if new years exist
        function updateDynamicYearPills(years) {
            if (!years || !yearPillsContainer) return;
            years.forEach(yr => {
                const existing = document.querySelector(`.year-btn[data-year="${yr}"]`);
                if (!existing && yr) {
                    const btn = document.createElement('button');
                    btn.className = 'year-btn';
                    btn.dataset.year = yr;
                    btn.textContent = yr;
                    const customBtn = document.querySelector('.year-btn[data-year="custom"]');
                    if (customBtn) {
                        yearPillsContainer.insertBefore(btn, customBtn);
                    } else {
                        yearPillsContainer.appendChild(btn);
                    }
                }
            });
        }

        // Render KPI Cards
        function renderKpiCards(kpi) {
            if (!kpi) return;
            const elTotal = document.getElementById('kpi-total-exits');
            if (elTotal) elTotal.textContent = Number(kpi.toplam_cikis || 0).toLocaleString('tr-TR');
            const elUnique = document.getElementById('kpi-unique-products');
            if (elUnique) elUnique.textContent = Number(kpi.tekil_urun_sayisi || 0).toLocaleString('tr-TR') + ' Çeşit';
            
            const topName = kpi.lider_urun || '-';
            const topEl = document.getElementById('kpi-top-product');
            if (topEl) {
                topEl.textContent = topName;
                topEl.title = topName;
            }
            const elTopCount = document.getElementById('kpi-top-product-count');
            if (elTopCount) elTopCount.textContent = (kpi.lider_adet || 0) + ' Kutu';

            const elCarton = document.getElementById('kpi-carton-ratio');
            if (elCarton) elCarton.textContent = `%${kpi.koli_orani || 0} Koli`;
            const elCartonDetail = document.getElementById('kpi-carton-detail');
            if (elCartonDetail) elCartonDetail.textContent = `${kpi.koli_adet || 0} Koli / ${kpi.tekil_adet || 0} Tekil`;

            const elDailyAvg = document.getElementById('kpi-daily-avg');
            if (elDailyAvg) elDailyAvg.textContent = Number(kpi.gunluk_ortalama || 0).toLocaleString('tr-TR');
            const elRepeat = document.getElementById('kpi-repeat-ratio');
            if (elRepeat) elRepeat.textContent = `%${kpi.tekrar_orani || 0}`;
            const elRepeatCount = document.getElementById('kpi-repeat-count');
            if (elRepeatCount) elRepeatCount.textContent = `${kpi.tekrar_adet || 0} mükerrer okutma`;

            const elSub = document.getElementById('chart1-subtitle');
            if (elSub) elSub.textContent = currentYear === 'tum' ? 'Tüm zamanların aylık toplam çıkışları' : `${currentYear} yılının 12 aylık çıkış dağılımı`;
        }

        // Render Charts using Chart.js
        function renderCharts(d) {
            if (!d) return;
            if (typeof Chart === 'undefined') {
                console.warn('Chart.js kütüphanesi henüz hazır değil, grafik çizimi ertelendi.');
                if (window.ensureChartJs) {
                    window.ensureChartJs(() => renderCharts(d));
                }
                return;
            }

            // Destroy existing instances safely
            if (chartMonthly) { try { chartMonthly.destroy(); } catch(e){} chartMonthly = null; }
            if (chartYearly) { try { chartYearly.destroy(); } catch(e){} chartYearly = null; }
            if (chartTopProducts) { try { chartTopProducts.destroy(); } catch(e){} chartTopProducts = null; }
            if (chartWeekday) { try { chartWeekday.destroy(); } catch(e){} chartWeekday = null; }

            const chartFont = { family: "'Inter', sans-serif", size: 11 };
            const chartGrid = { color: 'rgba(255, 255, 255, 0.05)', borderColor: 'rgba(255, 255, 255, 0.1)' };
            const chartTicks = { color: '#94a3b8', font: chartFont };

            // 1. Monthly Chart (Bar)
            const cMonthlyEl = document.getElementById('canvas-monthly');
            if (cMonthlyEl && d.aylik_grafik) {
                const ctxMonthly = cMonthlyEl.getContext('2d');
                const monthlyGrad = ctxMonthly.createLinearGradient(0, 0, 0, 260);
                monthlyGrad.addColorStop(0, 'rgba(16, 185, 129, 0.85)');
                monthlyGrad.addColorStop(1, 'rgba(16, 185, 129, 0.15)');

                chartMonthly = new Chart(ctxMonthly, {
                    type: 'bar',
                    data: {
                        labels: d.aylik_grafik.etiketler || [],
                        datasets: [{
                            label: 'Çıkış Adedi (Kutu)',
                            data: d.aylik_grafik.veriler || [],
                            backgroundColor: monthlyGrad,
                            borderColor: '#10b981',
                            borderWidth: 1.5,
                            borderRadius: 6,
                            maxBarThickness: 32
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: { backgroundColor: '#1e293b', titleColor: '#fff', bodyColor: '#cbd5e1', padding: 10, cornerRadius: 8 }
                        },
                        scales: {
                            x: { grid: { display: false }, ticks: chartTicks },
                            y: { grid: chartGrid, ticks: chartTicks, beginAtZero: true }
                        }
                    }
                });
            }

            // 2. Yearly Comparison Chart (Bar)
            const cYearlyEl = document.getElementById('canvas-yearly');
            if (cYearlyEl && d.yillik_grafik) {
                const ctxYearly = cYearlyEl.getContext('2d');
                const yearlyGrad = ctxYearly.createLinearGradient(0, 0, 0, 260);
                yearlyGrad.addColorStop(0, 'rgba(6, 182, 212, 0.85)');
                yearlyGrad.addColorStop(1, 'rgba(6, 182, 212, 0.15)');

                chartYearly = new Chart(ctxYearly, {
                    type: 'bar',
                    data: {
                        labels: d.yillik_grafik.etiketler || [],
                        datasets: [{
                            label: 'Yıllık Satış (Kutu)',
                            data: d.yillik_grafik.veriler || [],
                            backgroundColor: yearlyGrad,
                            borderColor: '#06b6d4',
                            borderWidth: 1.5,
                            borderRadius: 8,
                            maxBarThickness: 44
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: { backgroundColor: '#1e293b', titleColor: '#fff', bodyColor: '#cbd5e1', padding: 10, cornerRadius: 8 }
                        },
                        scales: {
                            x: { grid: { display: false }, ticks: chartTicks },
                            y: { grid: chartGrid, ticks: chartTicks, beginAtZero: true }
                        }
                    }
                });
            }

            // 3. Top Products (Doughnut)
            const cTopEl = document.getElementById('canvas-top-products');
            if (cTopEl) {
                const top5 = (d.en_cok_satanlar || []).slice(0, 5);
                const otherSum = (d.en_cok_satanlar || []).slice(5).reduce((acc, p) => acc + (p.adet || 0), 0);
                const pieLabels = top5.map(p => {
                    const name = String((p && p.urun_adi) || 'Tanımsız Ürün');
                    return name.length > 18 ? name.substring(0, 18) + '...' : name;
                });
                const pieData = top5.map(p => (p && p.adet) || 0);
                if (otherSum > 0) {
                    pieLabels.push('Diğerleri');
                    pieData.push(otherSum);
                }

                const ctxPie = cTopEl.getContext('2d');
                chartTopProducts = new Chart(ctxPie, {
                    type: 'doughnut',
                    data: {
                        labels: pieLabels.length ? pieLabels : ['Veri Yok'],
                        datasets: [{
                            data: pieData.length ? pieData : [1],
                            backgroundColor: [
                                '#10b981', '#06b6d4', '#f59e0b', '#8b5cf6', '#ec4899', '#64748b'
                            ],
                            borderWidth: 2,
                            borderColor: '#0f172a'
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { position: 'right', labels: { color: '#cbd5e1', font: chartFont, boxWidth: 12, padding: 12 } },
                            tooltip: { backgroundColor: '#1e293b', titleColor: '#fff', bodyColor: '#cbd5e1', padding: 10, cornerRadius: 8 }
                        },
                        cutout: '65%'
                    }
                });
            }

            // 4. Weekday Distribution (Bar)
            const cWeekdayEl = document.getElementById('canvas-weekday');
            if (cWeekdayEl && d.haftalik_grafik) {
                const ctxWeekday = cWeekdayEl.getContext('2d');
                const weekdayGrad = ctxWeekday.createLinearGradient(0, 0, 0, 260);
                weekdayGrad.addColorStop(0, 'rgba(139, 92, 246, 0.85)');
                weekdayGrad.addColorStop(1, 'rgba(139, 92, 246, 0.15)');

                chartWeekday = new Chart(ctxWeekday, {
                    type: 'bar',
                    data: {
                        labels: d.haftalik_grafik.etiketler || [],
                        datasets: [{
                            label: 'Çıkış Adedi',
                            data: d.haftalik_grafik.veriler || [],
                            backgroundColor: weekdayGrad,
                            borderColor: '#8b5cf6',
                            borderWidth: 1.5,
                            borderRadius: 6,
                            maxBarThickness: 30
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: { backgroundColor: '#1e293b', titleColor: '#fff', bodyColor: '#cbd5e1', padding: 10, cornerRadius: 8 }
                        },
                        scales: {
                            x: { grid: { display: false }, ticks: chartTicks },
                            y: { grid: chartGrid, ticks: chartTicks, beginAtZero: true }
                        }
                    }
                });
            }
        }

        // Render Top Products Table
        function renderTopProductsTable(list) {
            const tbody = document.getElementById('tbody-top-products');
            const countEl = document.getElementById('lbl-product-count');
            if (countEl) countEl.textContent = `${list.length} ürün listeleniyor`;

            if (!tbody) return;
            if (!list || list.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:#64748b; padding:2rem;">Bu döneme ait çıkış kaydı bulunamadı.</td></tr>';
                return;
            }

            tbody.innerHTML = list.map((p, idx) => `
                <tr>
                    <td style="color:#64748b; font-weight:600;">${idx + 1}</td>
                    <td><b style="color:#fff;">${esc(p.urun_adi || 'Tanımsız Ürün')}</b></td>
                    <td style="font-family:monospace; color:#94a3b8; font-size:0.82rem;">${esc(p.barkod || '-')}</td>
                    <td style="text-align:right; font-family:var(--font-outfit); font-weight:700; color:#10b981; font-size:0.95rem;">${Number(p.adet || 0).toLocaleString('tr-TR')}</td>
                    <td style="text-align:center;"><span style="background:rgba(255,255,255,0.06); padding:0.2rem 0.5rem; border-radius:6px; font-size:0.75rem;">${p.koli_sayisi || 0} Koli</span></td>
                    <td>
                        <div class="progress-bar-wrap"><div class="progress-bar-fill" style="width: ${Math.min(100, p.yuzde || 0)}%;"></div></div>
                        <span style="font-size:0.78rem; color:#94a3b8;">%${p.yuzde || 0}</span>
                    </td>
                    <td style="font-size:0.8rem; color:#64748b;">${esc(p.son_cikis || '-')}</td>
                </tr>
            `).join('');
        }

        // Render Batches Table
        function renderBatchesTable(list) {
            const tbody = document.getElementById('tbody-batches');
            if (!tbody) return;
            if (!list || list.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#64748b; padding:2rem;">Parti verisi bulunamadı.</td></tr>';
                return;
            }

            tbody.innerHTML = list.map((b, idx) => `
                <tr>
                    <td style="color:#64748b;">${idx + 1}</td>
                    <td><span style="font-family:monospace; background:rgba(245,158,11,0.12); color:#fbbf24; padding:0.2rem 0.5rem; border-radius:6px; font-size:0.82rem; font-weight:600;">${esc(b.parti || 'Belirtilmemiş')}</span></td>
                    <td><b style="color:#fff;">${esc(b.urun_adi || 'Tanımsız Ürün')}</b></td>
                    <td style="text-align:right; font-weight:700; color:#10b981;">${Number(b.adet || 0).toLocaleString('tr-TR')}</td>
                    <td style="color:#94a3b8; font-size:0.82rem;">${esc(b.skt || '-')}</td>
                </tr>
            `).join('');
        }

        // Render Monthly Table
        function renderMonthlyTable(monthly) {
            const tbody = document.getElementById('tbody-monthly-detail');
            if (!monthly || !monthly.etiketler) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:#64748b;">Veri bulunamadı.</td></tr>';
                return;
            }

            const total = monthly.veriler.reduce((a, b) => a + b, 0);

            tbody.innerHTML = monthly.etiketler.map((mName, idx) => {
                const cnt = monthly.veriler[idx] || 0;
                const pct = total > 0 ? ((cnt / total) * 100).toFixed(1) : 0;
                return `
                    <tr>
                        <td style="color:#64748b; font-weight:600;">${idx + 1}</td>
                        <td><b>${mName}</b></td>
                        <td style="text-align:right; font-weight:700; color:${cnt > 0 ? '#10b981' : '#64748b'};">${Number(cnt).toLocaleString('tr-TR')}</td>
                        <td>
                            <div class="progress-bar-wrap"><div class="progress-bar-fill" style="width: ${Math.min(100, pct)}%;"></div></div>
                            <span style="font-size:0.78rem; color:#94a3b8;">%${pct}</span>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function esc(str) {
            if (window.esc) return window.esc(str);
            if (!str) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        async function logoutUser() {
            if (!confirm('Oturumdan çıkmak istediğinize emin misiniz?')) return;
            try {
                await fetch('/api/system/logout', { method: 'POST', headers: getAuthHeaders() });
            } catch (e) {}
            localStorage.removeItem('local_session_token');
            window.location.href = '/login';
        }
    </script>
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
                <a href="/istatistikler" class="sidebar-link">
                    <i class="fa-solid fa-chart-pie"></i>
                    <span>İstatistikler & Raporlar</span>
                </a>
                <a href="/kullaniciya-satis" class="sidebar-link active">
                    <i class="fa-solid fa-user-tag"></i>
                    <span>Kullanıcıya Satış (Demo)</span>
                </a>
            </nav>

            <div class="sidebar-footer">
                <div class="system-status-pill {{ 'offline' if not is_system_active else '' }}">
                    <span class="status-indicator {{ system_status_cls | default('online') }}"></span>
                    <span>{{ system_status_text | default('Sistem Aktif') }}</span>
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
                <p style="margin:6px 0;"><strong>📦 Sürüm Kodu:</strong> <span id="modalCommitHash" style="color:#38bdf8; font-family:monospace; font-weight:bold;">{{ current_app_commit }}</span></p>
                <p style="margin:6px 0;"><strong>📅 Son Güncelleme:</strong> <span id="modalCommitDate" style="color:#f1f5f9;">{{ current_app_date }}</span></p>
                <p style="margin:6px 0;"><strong>📝 Son Değişiklik Notu:</strong></p>
                <div id="modalCommitMsg" style="background:#0f172a; padding:10px 14px; border-radius:8px; font-size:0.85rem; color:#cbd5e1; border:1px solid rgba(255,255,255,0.05); margin-top:4px;">{{ current_app_msg }}</div>
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
                const res = await (window.apiFetch || fetch)('/api/bkst/recetesiz_satis/sms_gonder', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
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
                const res = await (window.apiFetch || fetch)('/api/bkst/recetesiz_satis/sms_dogrula', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
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
                const res = await (window.apiFetch || fetch)('/api/bkst/recetesiz_satis', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
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
            if (window.esc) return window.esc(str);
            return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
        }

    </script>
    <script src="/static/app.js?v=20261005_v3000"></script>
</body>
</html>

```

---

### 📁 `static/chart.umd.min.js` (3. Parti Kütüphane)

> **Chart.js v4.4.1 (UMD Minified)** kütüphanesi çevrimdışı (offline) kullanım için yerel olarak barındırılmaktadır. Boyutu ~200 KB minified JS olduğu için doküman bütünlüğünü korumak adına kaynak kodu `static/chart.umd.min.js` dosyasında yer almaktadır.

---

### 📁 `Calistir.exe` (Derlenmiş Windows Uygulaması)

> `build_exe.ps1` scripti çalıştırılarak doğrudan yerel C# derleyicisi (`Add-Type`) üzerinden üretilen 64-bit bağımsız Windows başlatıcı ikili dosyasıdır. Kaynak kodu yukarıdaki `build_exe.ps1` içerisinde yer almaktadır.

---
