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
