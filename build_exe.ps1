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
