import sys

with open('templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

ids_in_js = [
    'drop-zone-system', 'system-file', 'file-info-system', 'name-system', 'size-system', 'btn-clear-system',
    'drop-zone-sales', 'sales-file', 'file-info-sales', 'name-sales', 'size-sales', 'btn-clear-sales',
    'btn-compare', 'val-total-system', 'val-total-sales', 'val-matched', 'val-remaining',
    'btn-dl-sales', 'btn-dl-remaining', 'btn-dl-report', 'compare-table-wrapper', 'compare-tbody', 'compare-table-count',
    'btn-bkst-open', 'btn-bkst-fetch', 'btn-bkst-close', 'bkst-status-badge', 'bkst-message-box',
    'audit-input-main', 'btn-audit-reset-koli', 'btn-dl-audit-excel', 'audit-msg-box',
    'audit-results-wrapper', 'audit-table-body', 'audit-koli-title', 'audit-cnt-total', 'audit-cnt-ok', 'audit-cnt-missing'
]

missing = []
for el_id in ids_in_js:
    if f'id="{el_id}"' not in html and f"id='{el_id}'" not in html:
        missing.append(el_id)

print('Missing HTML element IDs:', missing)
