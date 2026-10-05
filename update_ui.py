import os

# 1. Update templates/index.html
with open('templates/index.html', 'rb') as f:
    content_html = f.read().decode('utf-8', errors='replace')

old_actions_group = """                <!-- Export Action Buttons -->
                <div class="action-buttons-group">
                    <a href="/api/download/remaining" class="btn btn-success btn-block hidden" id="btn-dl-remaining">
                        <i class="fa-solid fa-file-excel"></i> Güncel Kalan Envanter Listesini İndir (Excel)
                    </a>
                    
                    <a href="/api/download/full_report" class="btn btn-outline btn-block hidden" id="btn-dl-report">
                        <i class="fa-solid fa-file-invoice"></i> Detaylı Karşılaştırma Raporunu İndir
                    </a>
                </div>"""

new_actions_group = """                <!-- Export Action Buttons -->
                <div class="action-buttons-group" style="display:flex; flex-direction:column; gap:0.6rem;">
                    <a href="/api/download/sales" class="btn btn-primary btn-block hidden" id="btn-dl-sales" style="background:linear-gradient(135deg, #5865f2, #4752c4); color:#fff; font-weight:700; padding:0.8rem 1rem; border-radius:10px; text-decoration:none; text-align:center;">
                        <i class="fa-solid fa-file-excel"></i> Sistemden Düşülecek Satışlar Listesini İndir (Excel)
                    </a>
                    
                    <a href="/api/download/remaining" class="btn btn-success btn-block hidden" id="btn-dl-remaining" style="text-decoration:none; text-align:center;">
                        <i class="fa-solid fa-file-excel"></i> Güncel Kalan Envanter Listesini İndir (Excel)
                    </a>
                    
                    <a href="/api/download/full_report" class="btn btn-outline btn-block hidden" id="btn-dl-report" style="text-decoration:none; text-align:center;">
                        <i class="fa-solid fa-file-invoice"></i> Detaylı Karşılaştırma Raporunu İndir
                    </a>
                </div>

                <!-- Live Results Table: Sistemden Çıkılması Gereken Ürünler -->
                <div id="compare-table-wrapper" class="hidden margin-top-sm" style="border-top: 1px solid var(--card-border); padding-top: 1.2rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.8rem; flex-wrap: wrap; gap: 0.5rem;">
                        <div>
                            <h3 style="font-family: var(--font-outfit); font-size: 1.1rem; color: var(--text-main); font-weight: 700;">
                                <i class="fa-solid fa-list-check" style="color: var(--primary);"></i> Sistemden Çıkılması Gereken Ürünler Listesi
                            </h3>
                            <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 2px;">
                                Aşağıdaki ürünler sistem depoda görünüyor ve satışı yapılmış. Sistemden düşmeniz gerekiyor.
                            </p>
                        </div>
                        <span id="compare-table-count" class="badge" style="background: rgba(88,101,242,0.2); color: #a5b4fc; border: 1px solid rgba(88,101,242,0.4); padding: 4px 10px; font-weight: 700;">0 Ürün</span>
                    </div>

                    <div style="max-height: 380px; overflow-y: auto; overflow-x: auto; border: 1px solid var(--card-border); border-radius: 10px; background: rgba(10,11,16,0.4);">
                        <table style="width: 100%; border-collapse: collapse; font-size: 0.8rem;">
                            <thead>
                                <tr style="background: rgba(88,101,242,0.18); color: var(--text-muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; position: sticky; top: 0; text-align: left; white-space: nowrap;">
                                    <th style="padding: 0.6rem 0.8rem; width: 40px;">#</th>
                                    <th style="padding: 0.6rem 0.8rem;">Ürün Adı</th>
                                    <th style="padding: 0.6rem 0.8rem;">Karekod</th>
                                    <th style="padding: 0.6rem 0.8rem;">Koli No</th>
                                    <th style="padding: 0.6rem 0.8rem;">Seri No</th>
                                    <th style="padding: 0.6rem 0.8rem;">Parti No</th>
                                    <th style="padding: 0.6rem 0.8rem;">Palet No</th>
                                    <th style="padding: 0.6rem 0.8rem;">Koli / Depo Durumu</th>
                                    <th style="padding: 0.6rem 0.8rem;">Açıklama / İşlem</th>
                                </tr>
                            </thead>
                            <tbody id="compare-tbody">
                            </tbody>
                        </table>
                    </div>
                </div>"""

if old_actions_group in content_html:
    content_html = content_html.replace(old_actions_group, new_actions_group)
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(content_html)
    print('templates/index.html güncellendi.')

# 2. Update static/app.js to populate the live compare table
with open('static/app.js', 'rb') as f:
    content_js = f.read().decode('utf-8', errors='replace')

old_js_logic = """            if (data.success) {
                // Update metrics safely
                if (statTotalInitial) statTotalInitial.textContent = data.total_initial;
                if (statTotalSold) statTotalSold.textContent = data.total_sold;
                if (statTotalMatched) statTotalMatched.textContent = data.total_matched;
                if (statTotalRemaining) statTotalRemaining.textContent = data.total_remaining;
                
                // Show download buttons
                if (btnDlRemaining) btnDlRemaining.classList.remove('hidden');
                if (btnDlReport) btnDlReport.classList.remove('hidden');
                if (downloadsContainer) downloadsContainer.classList.remove('hidden');
                if (chartSection) chartSection.classList.remove('hidden');
                
                // Render products sales list if available
                if (productSalesList) renderSalesChart(data.sales_by_product);
                
                // Show success feedback
                alert(`Karşılaştırma Tamamlandı!\\nDepodan ${data.total_matched} adet eşleşen karekod düşüldü.`);
            }"""

new_js_logic = """            if (data.success) {
                // Update metrics safely
                if (statTotalInitial) statTotalInitial.textContent = data.total_initial;
                if (statTotalSold) statTotalSold.textContent = data.total_sold;
                if (statTotalMatched) statTotalMatched.textContent = data.total_matched;
                if (statTotalRemaining) statTotalRemaining.textContent = data.total_remaining;
                
                // Show download buttons
                const btnDlSales = document.getElementById('btn-dl-sales');
                if (btnDlSales) btnDlSales.classList.remove('hidden');
                if (btnDlRemaining) btnDlRemaining.classList.remove('hidden');
                if (btnDlReport) btnDlReport.classList.remove('hidden');
                
                // Populate live compare table
                const wrapper = document.getElementById('compare-table-wrapper');
                const tbody = document.getElementById('compare-tbody');
                const countBadge = document.getElementById('compare-table-count');

                function escapeHtml(str) {
                    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
                }

                if (wrapper) wrapper.classList.remove('hidden');
                if (countBadge) countBadge.textContent = `${data.total_matched} Ürün Düşülecek`;

                if (tbody && data.matched_sales) {
                    tbody.innerHTML = data.matched_sales.map((item, idx) => {
                        const meta = item.metadata || {};
                        const koliNo = meta['Koli Numarası'] || meta['Paket Numarası'] || meta['Palet Numarası'] || meta['KOLINO'] || '—';
                        const seriNo = meta['Seri Numarası'] || meta['SERINO'] || '—';
                        const partiNo = meta['Parti Numarası'] || meta['SARJNO'] || '—';
                        const paletNo = meta['Palet Numarası'] || meta['PALETNO'] || '—';
                        const koliDurumu = item['Koli Durumu'] || item.koli_durumu || '—';
                        const aciklama = item.aciklama || 'Bu ürün sistemde görünüyor, sistemden çık';

                        return `
                            <tr style="border-bottom: 1px solid rgba(255,255,255,0.04);">
                                <td style="padding: 0.55rem 0.8rem; font-weight:700; color:var(--primary);">${idx + 1}</td>
                                <td style="padding: 0.55rem 0.8rem; font-weight:600; color:var(--text-main); white-space:nowrap;">${escapeHtml(item.product_name || '—')}</td>
                                <td style="padding: 0.55rem 0.8rem; font-family:monospace; font-size:0.75rem; color:var(--text-muted); white-space:nowrap;">${escapeHtml(item.qr || '—')}</td>
                                <td style="padding: 0.55rem 0.8rem; white-space:nowrap;">${escapeHtml(koliNo)}</td>
                                <td style="padding: 0.55rem 0.8rem; white-space:nowrap;">${escapeHtml(seriNo)}</td>
                                <td style="padding: 0.55rem 0.8rem; white-space:nowrap;">${escapeHtml(partiNo)}</td>
                                <td style="padding: 0.55rem 0.8rem; white-space:nowrap;">${escapeHtml(paletNo)}</td>
                                <td style="padding: 0.55rem 0.8rem; font-weight:600; color:#6ee7b7; white-space:nowrap;">${escapeHtml(koliDurumu)}</td>
                                <td style="padding: 0.55rem 0.8rem; white-space:nowrap;">
                                    <span style="background:rgba(245,158,11,0.15); color:#f59e0b; border:1px solid rgba(245,158,11,0.4); padding:3px 9px; border-radius:6px; font-size:0.75rem; font-weight:600; display:inline-flex; align-items:center; gap:5px;">
                                        <i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(aciklama)}
                                    </span>
                                </td>
                            </tr>
                        `;
                    }).join('');
                }
                
                // Show success feedback
                alert(`Karşılaştırma Tamamlandı!\\nSistemden düşülmesi gereken ${data.total_matched} adet eşleşen karekod bulundu.`);
            }"""

if old_js_logic in content_js:
    content_js = content_js.replace(old_js_logic, new_js_logic)
    with open('static/app.js', 'w', encoding='utf-8') as f:
        f.write(content_js)
    print('static/app.js güncellendi.')
