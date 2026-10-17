// QR Compare - Offline First & Auto-Relay Service Worker (Cloudflare 1033 Auto-Healer)
const CACHE_NAME = 'qr-compare-v3.9.0';
const OFFLINE_URLS = [
    '/cikis',
    '/stok-esitleme',
    '/cikis-listesi',
    '/depo_stoklari',
    '/kullaniciya-satis',
    '/mobil_sayim',
    '/static/style.css',
    '/static/app.js',
    '/static/device_tracker.js',
    '/static/html5-qrcode.min.js',
    '/static/manifest.json',
    '/static/favicon.svg',
    '/static/favicon.png'
];

// Install: Temel arayüz dosyalarını önbelleğe al
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            return cache.addAll(OFFLINE_URLS).catch(err => {
                console.warn('SW pre-cache notice:', err);
            });
        }).then(() => self.skipWaiting())
    );
});

// Activate: Eski önbellekleri temizle
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => {
            return Promise.all(
                keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
            );
        }).then(() => self.clients.claim())
    );
});

function getOfflineHtml() {
    return `<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Sunucu Tüneli Güncelleniyor - QR Compare</title>
<style>
  body { margin: 0; padding: 0; background: #090d16; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; text-align: center; }
  .card { background: rgba(22, 28, 45, 0.95); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 20px; padding: 36px 28px; max-width: 440px; margin: 20px; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7); }
  .icon-wrap { width: 70px; height: 70px; margin: 0 auto 18px; background: rgba(56, 189, 248, 0.15); border: 2px solid rgba(56, 189, 248, 0.4); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #38bdf8; font-size: 30px; }
  .spinner { width: 36px; height: 36px; border: 3px solid rgba(255,255,255,0.1); border-top-color: #38bdf8; border-radius: 50%; animation: spin 1s infinite linear; margin: 0 auto 16px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  h1 { font-size: 1.35rem; font-weight: 700; margin: 0 0 10px; color: #ffffff; }
  p { font-size: 0.9rem; line-height: 1.6; color: #94a3b8; margin: 0 0 20px; }
  .btn-live { display: inline-flex; align-items: center; justify-content: center; gap: 8px; background: linear-gradient(135deg, #4f46e5, #6366f1); color: #fff; border: none; padding: 12px 24px; font-size: 0.95rem; font-weight: 700; border-radius: 12px; cursor: pointer; text-decoration: none; width: 100%; box-sizing: border-box; box-shadow: 0 4px 15px rgba(79, 70, 229, 0.4); }
  .btn-retry { margin-top: 10px; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); color: #cbd5e1; padding: 10px 20px; border-radius: 10px; font-size: 0.85rem; width: 100%; box-sizing: border-box; cursor: pointer; }
  .status-badge { background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3); color: #38bdf8; padding: 6px 12px; border-radius: 8px; font-size: 0.8rem; margin-bottom: 16px; font-family: monospace; word-break: break-all; }
</style>
</head>
<body>
  <div class="card">
    <div class="icon-wrap">⚡</div>
    <div class="spinner" id="spin"></div>
    <h1 id="title">Canlı Sunucu Aranıyor...</h1>
    <p id="desc">Eski tünel adresi değişmiş olabilir. Firebase üzerinden güncel sunucu adresi sorgulanıyor...</p>
    <div class="status-badge" id="live-url" style="display:none;"></div>
    <a href="#" class="btn-live" id="btn-go" style="display:none;">🚀 Güncel Adrese Bağlan</a>
    <button class="btn-retry" onclick="checkLiveServer()">🔄 Yeniden Sorgula</button>
  </div>
  <script>
    const FB_RELAY = "https://qr-stok-64d79-default-rtdb.europe-west1.firebasedatabase.app/server_info.json";
    function checkLiveServer() {
      document.getElementById('spin').style.display = 'block';
      document.getElementById('title').textContent = "Canlı Sunucu Aranıyor...";
      fetch(FB_RELAY)
        .then(r => r.json())
        .then(data => {
          document.getElementById('spin').style.display = 'none';
          if (data && data.tunnel_url && data.tunnel_url.startsWith('http')) {
            const liveUrl = data.tunnel_url.replace(/\/+$/, '');
            const target = liveUrl + (window.location.pathname.startsWith('/static') ? '/cikis' : window.location.pathname) + window.location.search;
            
            // Eğer sayfa zaten bu URL ise tekrar kendine yönlendirip sonsuz döngüye girme!
            if (window.location.origin === liveUrl) {
              document.getElementById('title').textContent = "Sunucu Çevrimdışı (502 / Kapalı)";
              document.getElementById('desc').textContent = "Uzak tünel sunucusu şu an aktif yanıt vermiyor. Sunucu bilgisayarı açıldığında otomatik bağlanabilirsiniz.";
              document.getElementById('live-url').textContent = liveUrl + " (Bağlantı Yok)";
              document.getElementById('live-url').style.display = 'block';
              return;
            }

            document.getElementById('title').textContent = "Yeni Sunucu Adresi Bulundu!";
            document.getElementById('desc').textContent = "Yeni tünel adresine otomatik yönlendiriliyorsunuz...";
            document.getElementById('live-url').textContent = liveUrl;
            document.getElementById('live-url').style.display = 'block';
            document.getElementById('btn-go').href = target;
            document.getElementById('btn-go').style.display = 'inline-flex';
            setTimeout(() => {
              window.location.replace(target);
            }, 600);
          } else {
            document.getElementById('title').textContent = "Sunucu Çevrimdışı";
            document.getElementById('desc').textContent = "Sunucu şu an aktif bir tünel açmamış görünüyor. Lütfen bilgisayarınızdaki sunucuyu açık tutunuz.";
          }
        })
        .catch(err => {
          document.getElementById('spin').style.display = 'none';
          document.getElementById('title').textContent = "Bağlantı Kurulamadı";
          document.getElementById('desc').textContent = "İnternet bağlantınızı kontrol ediniz.";
        });
    }
    checkLiveServer();
  </script>
</body>
</html>`;
}

// Fetch: Ağ yoksa önbellekten sun & Cloudflare 1033 Hatasını Firebase ile otomatik onar
self.addEventListener('fetch', event => {
    const req = event.request;
    const url = new URL(req.url);

    // Yerel makine veya yerel ağ üzerinden bağlanırken ASLA uzak tünele yönlendirme yapma!
    const isLocal = ['localhost', '127.0.0.1'].includes(url.hostname) || 
                    url.hostname.startsWith('192.168.') || 
                    url.hostname.startsWith('10.') || 
                    url.hostname.endsWith('.local');

    if (isLocal) {
        event.respondWith(
            fetch(req).catch(async () => {
                const cached = await caches.match(req);
                if (cached) return cached;
                return new Response('Yerel Çevrimdışı Mod Devrede', { status: 200, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
            })
        );
        return;
    }

    // API istekleri doğrudan yerel sunucuya gitsin
    if (url.pathname.startsWith('/api/') || req.method !== 'GET') {
        event.respondWith(
            fetch(req).catch(() => {
                return new Response(JSON.stringify({ success: false, offline: true, error: 'Çevrimdışı Mod' }), {
                    headers: { 'Content-Type': 'application/json' }
                });
            })
        );
        return;
    }

    // HTML ve Statik Dosyalar: Network First, Fallback to Cache / Firebase Relay
    event.respondWith(
        fetch(req)
            .then(async response => {
                // Cloudflare 1033 / 530 / 502 / 504 / 521 / 522 Tünel Hatası Yakalama
                if (response && (response.status === 1033 || response.status === 530 || response.status === 502 || response.status === 503 || response.status === 504 || response.status === 521 || response.status === 522)) {
                    if (req.mode === 'navigate') {
                        return new Response(getOfflineHtml(), {
                            status: 200,
                            headers: { 'Content-Type': 'text/html; charset=utf-8' }
                        });
                    }
                }
                if (response && response.status === 200) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
                }
                return response;
            })
            .catch(async () => {
                // Tünel kapalı veya ağ hatası durumunda Firebase yönlendiricisini devreye al
                if (req.mode === 'navigate') {
                    return new Response(getOfflineHtml(), {
                        status: 200,
                        headers: { 'Content-Type': 'text/html; charset=utf-8' }
                    });
                }

                const cached = await caches.match(req);
                if (cached) return cached;

                return new Response('Çevrimdışı Mod Devrede', { status: 200, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
            })
    );
});
