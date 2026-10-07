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

        // Python sunucusu arka planda bağımsız bir servis olarak çalışmaya devam etmeli.
        // Tarayıcının hemen dönmesi durumunda sunucu asla kapatılmamalıdır.
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
