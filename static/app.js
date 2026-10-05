// ── QR COMPARE APP.JS ──────────────────────────────────────────────────────
console.log("QR Compare app.js loading...");

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

window.triggerBkstFetch = function() {
    console.log("triggerBkstFetch called");
    setBkstUI('fetching', 'Bakanlık verileri çekiliyor...');

    fetch('/api/bkst/fetch', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                setBkstUI('fetching', data.message);
                startBkstPolling();
            } else {
                setBkstUI('error', data.message || 'Veri çekme başlatılamadı.');
            }
        })
        .catch(err => {
            setBkstUI('error', 'Sunucu hatası: ' + err.message);
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
});
