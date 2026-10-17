/**
 * BKST QR-Compare - Otomatik Cihaz & Canlı Oturum Takip Motoru
 * Herhangi bir ayar gerektirmeden cihazı tanımlar ve 15 saniyede bir merkezi sunucuya periyodik canlılık bildirir.
 */
(function() {
    'use strict';

    // 1. Giriş ekranındayken veya henüz giriş yapılmamışsa bayi canlılığı gönderme
    const curPath = window.location.pathname.toLowerCase();
    if (curPath === '/login') {
        return; // Kullanıcı henüz giriş yapmadıysa cihaz kaydedilmez
    }

    // 2. Kalıcı Benzersiz Cihaz Kimliği (Device ID)
    function getOrCreateDeviceId() {
        let devId = localStorage.getItem('bkst_device_id');
        if (!devId) {
            devId = 'dev_' + Math.random().toString(36).substring(2, 9) + Date.now().toString(36);
            localStorage.setItem('bkst_device_id', devId);
        }
        document.cookie = `bkst_device_id=${encodeURIComponent(devId)}; path=/; max-age=31536000; SameSite=Lax`;
        return devId;
    }

    // 3. Akıllı Cihaz & Tarayıcı Tespiti
    function detectDeviceInfo() {
        let customName = localStorage.getItem('bkst_device_name');
        if (customName && customName.trim()) {
            return customName.trim();
        }

        const ua = navigator.userAgent;
        let os = "PC";
        if (/Windows/i.test(ua)) os = "Windows PC";
        else if (/Android/i.test(ua)) os = "Android Cihaz";
        else if (/iPhone/i.test(ua)) os = "iPhone";
        else if (/iPad/i.test(ua)) os = "iPad Tablet";
        else if (/Macintosh|Mac OS/i.test(ua)) os = "Mac";
        else if (/Linux/i.test(ua)) os = "Linux PC";

        let browser = "";
        if (/Edg\//i.test(ua)) browser = "Edge";
        else if (/Chrome\//i.test(ua)) browser = "Chrome";
        else if (/Safari\//i.test(ua)) browser = "Safari";
        else if (/Firefox\//i.test(ua)) browser = "Firefox";

        return browser ? `${os} (${browser})` : os;
    }

    // 4. Aktif Sayfa Tespiti
    function getActivePageName() {
        const p = window.location.pathname.toLowerCase();
        if (p.includes('/admin')) return '👑 Yönetim Paneli';
        if (p.includes('/kullaniciya-satis')) return '🛒 Kullanıcıya Satış Ekranı';
        if (p.includes('/cikis_listesi')) return '📋 Çıkış Listesi Ekranı';
        if (p.includes('/cikis')) return '📦 Çıkış İşlemleri (Barkod Okutma)';
        if (p.includes('/depo_stoklari')) return '🏢 Depo Stokları Ekranı';
        if (p.includes('/depo_kabul')) return '📥 Depo Kabul Ekranı';
        if (p.includes('/istatistikler')) return '📊 İstatistikler Ekranı';
        return document.title || 'Uygulama Açık';
    }

    // 5. 15 Saniyede Bir Canlılık Sinyali Gönderimi (Heartbeat)
    let isSending = false;
    async function sendDeviceHeartbeat() {
        if (isSending) return;
        if (window.location.pathname.toLowerCase() === '/login') return;

        isSending = true;
        try {
            const payload = {
                cihaz_id: getOrCreateDeviceId(),
                cihaz_adi: detectDeviceInfo(),
                aktif_sayfa: getActivePageName()
            };

            const csrfHeader = {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            };

            const res = await fetch('/api/system/heartbeat', {
                method: 'POST',
                headers: csrfHeader,
                body: JSON.stringify(payload)
            });

            if (res.status === 401 || res.status === 403) {
                try {
                    const errData = await res.json();
                    if (errData && (errData.status === 'revoked' || errData.unauthorized || errData.redirect)) {
                        localStorage.removeItem('bkst_staff_token');
                        window.location.replace(errData.redirect || '/login?error=oturum_sonlandi');
                        return;
                    }
                } catch(e) {}
                localStorage.removeItem('bkst_staff_token');
                window.location.replace('/login?error=oturum_sonlandi');
                return;
            }

            if (res.ok) {
                const data = await res.json();
                if (data && (data.status === 'revoked' || data.unauthorized)) {
                    localStorage.removeItem('bkst_staff_token');
                    window.location.replace(data.redirect || '/login?error=oturum_sonlandi');
                    return;
                }
                if (typeof window.updateSystemStatusPill === 'function' && data) {
                    if (data.running) {
                        window.updateSystemStatusPill('fetching', 'Veriler Çekiliyor...');
                    } else if (data.status === 'revoked' || data.unauthorized) {
                        window.updateSystemStatusPill(false, 'Yetkisiz Oturum', data.message);
                    } else if (data.server_connected || data.online === true || data.is_main_server) {
                        window.updateSystemStatusPill(true, 'Merkez Sunucu Canlı');
                    } else if (navigator.onLine) {
                        window.updateSystemStatusPill(true, 'Sistem Aktif (Yerel Mod)');
                    } else {
                        window.updateSystemStatusPill(false, 'Çevrimdışı (İnternet Yok)', data.message);
                    }
                }

                // SUNUCUDAN OTOMATİK GÜNCELLEME ALMA (SORGU SUALSİZ)
                if (data && data.exit_list_rev) {
                    const lastKnownRev = localStorage.getItem('bkst_last_exit_list_rev');
                    if (lastKnownRev !== data.exit_list_rev) {
                        localStorage.setItem('bkst_last_exit_list_rev', data.exit_list_rev);
                        // Arka planda sessizce eşitle ve ekranı otomatik güncelle
                        const refreshUi = function() {
                            ['loadRecent', 'loadList', 'loadRecentScans', 'loadExitList'].forEach(function(fn) {
                                if (typeof window[fn] === 'function') { window[fn](); }
                            });
                            if (typeof window.loadWarehouseStock === 'function') { window.loadWarehouseStock(); }
                        };
                        fetch('/api/sync/manual', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
                        }).then(r => r.json()).then(refreshUi).catch(refreshUi);
                    }
                }
            }
        } catch (_) {
        } finally {
            isSending = false;
        }
    }

    // İlk yüklemede ve periyodik canlılık bildirimi (mobilde 5 sn, masaüstünde 15 sn)
    let intervalTimer = null;
    const isMobileDevice = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
    const heartbeatIntervalMs = isMobileDevice ? 5000 : 15000;

    function startDeviceTimer() {
        if (intervalTimer) return;
        intervalTimer = setInterval(sendDeviceHeartbeat, heartbeatIntervalMs);
    }

    function stopDeviceTimer() {
        if (intervalTimer) {
            clearInterval(intervalTimer);
            intervalTimer = null;
        }
    }

    let lastManualSyncTime = 0;
    function triggerThrottledSync() {
        const now = Date.now();
        if (now - lastManualSyncTime < 15000) return; // En fazla 15 saniyede bir çalışabilir, tarayıcıyı dondurmaz
        lastManualSyncTime = now;
        fetch('/api/sync/manual', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
        }).then(r => r.json()).then(syncRes => {
            if (syncRes && syncRes.success) {
                if (typeof loadRecent === 'function') loadRecent();
                if (typeof loadList === 'function') loadList();
                if (typeof loadRecentScans === 'function') loadRecentScans();
                if (typeof loadExitList === 'function') loadExitList();
                if (typeof window.loadWarehouseStock === 'function') window.loadWarehouseStock();
            }
        }).catch(() => {});
    }

    function onForegroundResume() {
        sendDeviceHeartbeat();
        startDeviceTimer();
        triggerThrottledSync();
    }

    sendDeviceHeartbeat();
    startDeviceTimer();

    // Uygulama yeni açıldığında sunucudaki silme/ekleme durumunu anında yerel ile uzlaştır
    setTimeout(function() {
        fetch('/api/sync/manual', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
        }).then(r => r.json()).then(function(syncRes) {
            if (syncRes && syncRes.success) {
                ['loadRecent', 'loadList', 'loadRecentScans', 'loadExitList'].forEach(function(fn) {
                    if (typeof window[fn] === 'function') { window[fn](); }
                });
                if (typeof window.loadWarehouseStock === 'function') { window.loadWarehouseStock(); }
            }
        }).catch(function() {});
    }, 800);

    // Sekme arka plandayken CPU/RAM koruması için döngüyü durdur, geri dönüldüğünde anında eşitle
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            stopDeviceTimer();
        } else {
            onForegroundResume();
        }
    });

    window.addEventListener('focus', function() {
        if (!document.hidden) {
            onForegroundResume();
        }
    });

    // İnternet geri geldiğinde (online olduğunda) bekleyen verileri anında sunucuya senkronize et
    window.addEventListener('online', function() {
        console.log("İnternet bağlantısı geri geldi, otomatik senkronizasyon tetikleniyor...");
        sendDeviceHeartbeat();
        triggerThrottledSync();
    });

    // Kullanıcı uygulamayı/pencereyi kapattığı an sunucuya anında kapanış sinyali gönder
    function notifyDeviceClosed() {
        try {
            const devId = localStorage.getItem('bkst_device_id');
            if (devId) {
                const payload = JSON.stringify({ cihaz_id: devId });
                const blob = new Blob([payload], { type: 'application/json' });
                navigator.sendBeacon('/api/system/cihaz_kapat', blob);
            }
        } catch (_) {}
    }
    window.addEventListener('beforeunload', notifyDeviceClosed);
    window.addEventListener('pagehide', notifyDeviceClosed);

    window.bkstSetDeviceName = function(newName) {
        if (newName && newName.trim()) {
            localStorage.setItem('bkst_device_name', newName.trim());
            sendDeviceHeartbeat();
        }
    };
})();
