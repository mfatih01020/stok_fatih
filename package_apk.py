import os
import zipfile
import json
import shutil
import subprocess

def build_qr_compare_apk(src_apk="QR_Compare.apk", dst_apk="QR_Compare.apk", server_url="https://upper-reasons-bouquet-realty.trycloudflare.com"):
    print(f"Building {dst_apk} for target {server_url}...")
    
    if not os.path.exists(src_apk):
        src_apk = os.path.join("static", "QR_Compare.apk")
    if not os.path.exists(src_apk):
        print(f"Error: {src_apk} not found.")
        return False

    temp_unaligned = "temp_unaligned.apk"
    signed_out_dir = "signed_out"
    shutil.rmtree(signed_out_dir, ignore_errors=True)
    os.makedirs(signed_out_dir, exist_ok=True)

    files = {}
    with zipfile.ZipFile(src_apk, "r") as z_in:
        for item in z_in.infolist():
            if item.filename.startswith("META-INF/"):
                continue  # strip old signatures
            content = z_in.read(item.filename)
            # 1. Fix App Name in binary resources.arsc: replace 'My WebView' with 'QR Compare'
            if item.filename == "resources.arsc":
                content = content.replace(b"My WebView", b"QR Compare")
            files[item.filename] = content

    # 2. Update assets/settings.json to point to server login URL
    target_start_url = f"{server_url.rstrip('/')}/login"
    settings = {
        "sites": [
            {
                "url": target_start_url,
                "title": "QR Compare"
            }
        ],
        "other": f"{server_url.rstrip('/')}/",
        "remote_debug": True,
        "console_log": True,
        "web_settings": {
            "JavaScriptEnabled": True,
            "DomStorageEnabled": True,
            "AllowFileAccess": True
        }
    }
    files["assets/settings.json"] = json.dumps(settings, indent=2).encode("utf-8")

    # 3. Update assets/local/index.html with branded splash screen
    local_html = f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>QR Compare</title>
    <style>
        body {{
            margin: 0; padding: 0;
            background: #0b0f19; color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex; flex-direction: column;
            align-items: center; justify-content: center;
            height: 100vh; text-align: center;
            user-select: none;
        }}
        .card {{
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 24px;
            padding: 36px 24px;
            width: 86%;
            max-width: 340px;
            box-shadow: 0 25px 50px rgba(0,0,0,0.7);
            display: flex; flex-direction: column; align-items: center;
        }}
        .logo {{
            width: 64px; height: 64px;
            background: linear-gradient(135deg, #0284c7, #6366f1);
            border-radius: 18px;
            display: flex; align-items: center; justify-content: center;
            font-size: 2.2rem; color: #fff;
            margin-bottom: 16px;
            box-shadow: 0 8px 24px rgba(2, 132, 199, 0.4);
        }}
        .spinner {{
            width: 32px; height: 32px;
            border: 3px solid rgba(255, 255, 255, 0.1);
            border-top-color: #38bdf8;
            border-radius: 50%;
            animation: spin 0.85s infinite linear;
            margin: 18px 0 12px 0;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        h2 {{ font-size: 1.4rem; margin: 0 0 4px 0; color: #fff; font-weight: 800; letter-spacing: 0.5px; }}
        .sub {{ font-size: 0.75rem; color: #94a3b8; font-weight: 700; margin-bottom: 4px; letter-spacing: 0.5px; }}
        p {{ font-size: 0.86rem; color: #38bdf8; margin: 0; font-weight: 500; }}
        .badge {{ margin-top: 18px; font-size: 0.72rem; color: #64748b; font-family: monospace; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">&#9889;</div>
        <h2>QR COMPARE</h2>
        <div class="sub">AKILLI KAREKOD & STOK SİSTEMİ</div>
        <div class="spinner"></div>
        <p>Canlı Sunucu Bağlantısı Kuruluyor...</p>
        <div class="badge" id="b-status">Firebase Canlı Tünel Senkronizasyonu</div>
    </div>
    <script>
        const FB_URL = "https://qr-stok-64d79-default-rtdb.europe-west1.firebasedatabase.app/server_info.json";
        const fallbackUrl = "{target_start_url}";
        
        function setStatus(text, isError) {{
            var b = document.getElementById('b-status');
            if (b) {{
                b.textContent = text;
                if (isError) b.style.color = '#f87171';
            }}
        }}

        function resolveLiveTunnel() {{
            var saved = localStorage.getItem("custom_server_url") || localStorage.getItem("last_live_tunnel");
            var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            var timeoutId = null;
            var fetchOpts = {{
                cache: "no-store",
                headers: {{ "Cache-Control": "no-cache", "Pragma": "no-cache" }}
            }};
            if (controller) {{
                fetchOpts.signal = controller.signal;
                timeoutId = setTimeout(function() {{ controller.abort(); }}, 3500);
            }}

            fetch(FB_URL + "?ts=" + Date.now(), fetchOpts)
                .then(function(r) {{ return r.json(); }})
                .then(function(data) {{
                    if (timeoutId) clearTimeout(timeoutId);
                    if (data && data.status !== 'OFFLINE' && data.tunnel_url && data.tunnel_url.startsWith('http')) {{
                        var live = data.tunnel_url.replace(/\\/+$/, '') + "/login";
                        localStorage.setItem("last_live_tunnel", live);
                        setStatus("Bağlantı kuruldu, açılıyor...");
                        window.location.replace(live);
                    }} else if (saved) {{
                        setStatus("Son çalışan adrese bağlanılıyor...");
                        window.location.replace(saved);
                    }} else {{
                        window.location.replace(fallbackUrl);
                    }}
                }})
                .catch(function(e) {{
                    if (timeoutId) clearTimeout(timeoutId);
                    if (saved) {{
                        setStatus("Kayıtlı adrese bağlanılıyor...");
                        window.location.replace(saved);
                    }} else {{
                        window.location.replace(fallbackUrl);
                    }}
                }});
        }}
        setTimeout(resolveLiveTunnel, 80);
    </script>
</body>
</html>"""
    files["assets/local/index.html"] = local_html.encode("utf-8")

    # 4. Write unsigned APK
    with zipfile.ZipFile(temp_unaligned, "w", compression=zipfile.ZIP_DEFLATED) as z_out:
        for name, content in sorted(files.items()):
            z_out.writestr(name, content)

    # 5. Sign with uber-apk-signer (zipalign + v1 + v2 + v3)
    java_exe = os.path.join("tools", "jre", "bin", "java.exe")
    jar_path = os.path.join("tools", "uber-apk-signer.jar")

    cmd = [
        java_exe, "-jar", jar_path,
        "-a", temp_unaligned,
        "-o", signed_out_dir
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("Signer error:\n", res.stderr)
        return False

    # Find the output signed file in signed_out_dir
    signed_apk = None
    for f in os.listdir(signed_out_dir):
        if f.endswith(".apk"):
            signed_apk = os.path.join(signed_out_dir, f)
            break

    if not signed_apk or not os.path.exists(signed_apk):
        print("Error: signed APK not found in output directory.")
        return False

    # Copy to destination paths
    shutil.copy2(signed_apk, dst_apk)
    static_apk = os.path.join("static", "QR_Compare.apk")
    shutil.copy2(signed_apk, static_apk)

    # Also copy directly to connected phone if available
    phone_download = r"C:\Users\fatih\CrossDevice\Redmi Note 13 Pro+ 5G\storage\Download\QR_Compare.apk"
    try:
        if os.path.exists(os.path.dirname(phone_download)):
            shutil.copy2(signed_apk, phone_download)
            print(f"[OK] Copied directly to phone: {phone_download}")
    except Exception as e_phone:
        print(f"Phone copy notice: {e_phone}")

    # Cleanup temp files
    if os.path.exists(temp_unaligned):
        os.remove(temp_unaligned)
    shutil.rmtree(signed_out_dir, ignore_errors=True)

    final_size = os.path.getsize(dst_apk)
    print(f"[OK] Build complete! {dst_apk} is ready and valid ({final_size} bytes).")
    return True

if __name__ == "__main__":
    build_qr_compare_apk()
