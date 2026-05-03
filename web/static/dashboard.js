/* dashboard.js — Comatic Bridge order table + modal logic */

// Injected from template: ORDERS, UNPAID, UNFULFILLED, BANK
// (defined as window globals before this script runs)

// ── Tab switching ────────────────────────────────────────────────────────────
function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}

// ── Modal ────────────────────────────────────────────────────────────────────
function openModal(orderId, orderMeta) {
  document.getElementById('modal-title').textContent = 'Order ' + orderMeta.shopify_order_name;
  const body = document.getElementById('modal-body');
  body.innerHTML = buildModalHeader(orderMeta) +
    '<div class="modal-section"><h3>Line Items</h3><div id="items-slot"><em style="color:var(--muted)">Loading…</em></div></div>';
  document.getElementById('orderModal').classList.add('open');

  fetch('/api/order-items/' + orderId)
    .then(r => r.json())
    .then(data => {
      document.getElementById('items-slot').innerHTML = buildItemsTable(data.items || []);
    })
    .catch(err => {
      document.getElementById('items-slot').innerHTML = '<em style="color:var(--red)">Error: ' + err + '</em>';
    });
}

function closeModal() {
  document.getElementById('orderModal').classList.remove('open');
}

document.getElementById('orderModal').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

// ── Modal HTML builders ───────────────────────────────────────────────────────
function buildModalHeader(m) {
  return `
  <div class="modal-section">
    <h3>Order Info</h3>
    <div class="info-grid">
      <div class="info-row"><span>Date:</span>${(m.order_date||'').substring(0,10)||'—'}</div>
      <div class="info-row"><span>Currency:</span>${m.currency||'—'}</div>
      <div class="info-row"><span>Total:</span><strong>${fmt(m.total_price)} ${m.currency||''}</strong></div>
      <div class="info-row"><span>Shipping:</span>${fmt(m.shipping_cost)} ${m.currency||''}</div>
      <div class="info-row"><span>Gateway:</span>${m.payment_gateway||'—'}</div>
      <div class="info-row"><span>Payment:</span>${m.financial_status||'—'}</div>
      <div class="info-row"><span>Fulfillment:</span>${m.fulfillment_status||'—'}</div>
      <div class="info-row"><span>Shipped:</span>${m.shipped_date ? m.shipped_date.substring(0,10) : '—'}</div>
    </div>
  </div>
  <div class="modal-section">
    <h3>Ship To</h3>
    <div class="info-grid">
      <div class="info-row"><span>Name:</span>${esc(m.shipping_name||'—')}</div>
      <div class="info-row"><span>Address:</span>${esc(m.shipping_address1||'—')}</div>
      <div class="info-row"><span>City:</span>${esc(m.shipping_city||'—')} ${esc(m.shipping_zip||'')}</div>
      <div class="info-row"><span>Country:</span>${esc(m.shipping_country_code||'—')}</div>
      <div class="info-row"><span>Email:</span>${esc(m.customer_email||'—')}</div>
    </div>
  </div>`;
}

function buildItemsTable(items) {
  if (!items.length) return '<em style="color:var(--muted)">No items found.</em>';
  let html = `<table class="items-tbl"><thead><tr>
    <th>Product</th><th>SKU</th><th>Qty</th><th>List</th>
    <th>Paid/unit</th><th>Discount</th><th>Total Paid</th><th>Collections</th><th>Tags</th>
  </tr></thead><tbody>`;
  items.forEach(item => {
    const cols = parseJ(item.collections).map(c => `<span class="pill">${esc(c)}</span>`).join('');
    const tags = parseJ(item.tags).map(t => `<span class="pill pill-amber">${esc(t)}</span>`).join('');
    html += `<tr>
      <td><strong>${esc(item.product_name||'—')}</strong></td>
      <td style="font-family:monospace;color:var(--muted)">${esc(item.sku||'—')}</td>
      <td>${item.quantity}</td>
      <td style="color:var(--muted)">${fmt(item.original_unit_price)}</td>
      <td>${fmt(item.discounted_unit_price)}</td>
      <td style="color:var(--amber)">${item.total_discount > 0 ? '-' + Number(item.total_discount).toFixed(2) : '—'}</td>
      <td><strong style="color:var(--green)">${fmt(item.total_paid)}</strong></td>
      <td>${cols||'—'}</td>
      <td>${tags||'—'}</td>
    </tr>`;
  });
  return html + '</tbody></table>';
}

// ── Table builder ─────────────────────────────────────────────────────────────
function financialBadge(s) {
  s = (s||'').toUpperCase();
  if (s === 'PAID') return '<span class="badge b-green">Paid</span>';
  if (s === 'PENDING' || s === 'PARTIALLY_PAID') return `<span class="badge b-amber">${s}</span>`;
  return `<span class="badge b-muted">${s||'—'}</span>`;
}

function fulfillBadge(s) {
  s = (s||'').toUpperCase();
  if (s === 'FULFILLED' || s === 'RESTOCKED') return '<span class="badge b-green">Fulfilled</span>';
  if (s === 'PARTIAL')     return '<span class="badge b-amber">Partial</span>';
  if (s === 'UNFULFILLED') return '<span class="badge b-red">Unfulfilled</span>';
  return `<span class="badge b-muted">${s||'—'}</span>`;
}

function payForm(o) {
  if ((o.financial_status||'').toUpperCase() === 'PAID') return '';
  const opts = Object.entries(BANK).map(([name, code]) =>
    `<option value="${esc(code)}">${esc(name)}</option>`).join('');
  return `<form action="/mark-as-paid/${o.shopify_order_id}" method="post" class="pay-form" onclick="event.stopPropagation()">
    <select name="payment_method" required>
      <option value="" disabled selected>Account…</option>${opts}
    </select>
    <button type="submit" class="btn-pay">&#10003; Mark Paid</button>
  </form>`;
}

function buildTable(list, mountId) {
  const el = document.getElementById(mountId);
  if (!list.length) { el.innerHTML = '<div class="empty">No orders in this category.</div>'; return; }
  let html = `<table><thead><tr>
    <th>Order</th><th>Date</th><th>Customer</th><th>Ship To</th>
    <th>Amount</th><th>Payment</th><th>Fulfillment</th><th>Action</th>
  </tr></thead><tbody>`;
  list.forEach(o => {
    const meta = encodeURIComponent(JSON.stringify(o));
    html += `<tr onclick="openModal('${o.shopify_order_id}', JSON.parse(decodeURIComponent('${meta}')))">
      <td><strong style="color:var(--blue)">${esc(o.shopify_order_name||'—')}</strong></td>
      <td style="color:var(--muted);white-space:nowrap">${(o.order_date||'').substring(0,10)||'—'}</td>
      <td style="font-size:12px">${esc(o.customer_email||'—')}</td>
      <td><span class="badge b-muted">${esc(o.shipping_country_code||'—')}</span> ${esc(o.shipping_city||'')}</td>
      <td><strong>${fmt(o.total_price)}</strong> <small style="color:var(--muted)">${esc(o.currency||'')}</small></td>
      <td>${financialBadge(o.financial_status)}</td>
      <td>${fulfillBadge(o.fulfillment_status)}</td>
      <td>${payForm(o)}</td>
    </tr>`;
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

// Render all tables on load
buildTable(window.ORDERS,      'table-all');
buildTable(window.UNPAID,      'table-unpaid');
buildTable(window.UNFULFILLED, 'table-unfulfilled');
