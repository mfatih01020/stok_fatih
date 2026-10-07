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
        if python_exe.endswith("python.exe"):
            pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
            if os.path.exists(pythonw):
                python_exe = pythonw

        subprocess.Popen([python_exe, "app.py"], cwd=BASE_DIR, creationflags=NO_WINDOW)

        for _ in range(20):
            time.sleep(0.25)
            if is_port_in_use(5000):
                break

    open_as_desktop_app("http://127.0.0.1:5000")

if __name__ == "__main__":
    launch()
