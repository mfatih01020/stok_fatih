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

// Global Session Authenticated Fetch Helper
window.apiFetch = async function(resource, init = {}) {
    init = init || {};
    const urlStr = typeof resource === 'string' ? resource : (resource && resource.url ? resource.url : '');
    if (init.headers instanceof Headers) {
        if (!init.headers.has('X-Requested-With')) init.headers.append('X-Requested-With', 'XMLHttpRequest');
    } else {
        init.headers = init.headers || {};
        if (!init.headers['X-Requested-With']) init.headers['X-Requested-With'] = 'XMLHttpRequest';
    }
    const response = await fetch(resource, init);
    if (response.status === 401 && urlStr && urlStr.startsWith('/api/') && !urlStr.startsWith('/api/system/heartbeat') && !urlStr.startsWith('/api/system/user_info')) {
        window.location.href = '/login';
    }
    return response;
};

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

// Direct DOM Event Binding
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM loaded, binding event listeners...");

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

