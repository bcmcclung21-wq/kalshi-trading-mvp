/* Poly Trading MVP Dashboard */
let autoExecute = false;
let allowCombos = false;
let refreshInterval = null;

async function fetchDashboard() {
  try {
    const res = await fetch('/api/dashboard');
    if (!res.ok) {
      const text = await res.text();
      console.error('Dashboard HTTP error:', res.status, text);
      showError('Server error ' + res.status);
      return;
    }

    const data = await res.json();
    render(data);
  } catch (err) {
    console.error('Dashboard fetch error:', err);
    showError('Network error: ' + err.message);
  }
}

function render(data) {
  if (!data || typeof data !== 'object') {
    showError('Invalid response from server');
    return;
  }

  const statusEl = document.getElementById('status');
  const marketsEl = document.getElementById('markets');
  const tradesEl = document.getElementById('trades');

  const statusText = data.status || 'unknown';
  const marketCount = typeof data.markets_count === 'number' ? data.markets_count : 0;

  if (statusEl) {
    statusEl.textContent = 'Status: ' + statusText + ' | Markets: ' + marketCount;
    statusEl.className = 'status ' + (statusText === 'ok' ? 'ok' : 'error');
  }

  if (marketsEl) {
    const marketList = Array.isArray(data.markets) ? data.markets : [];
    if (marketList.length > 0) {
      marketsEl.innerHTML = marketList
        .map((market) =>
          '<div class="market">' +
            '<strong>' + escapeHtml((market && market.title) || 'Untitled') + '</strong>' +
            '<span class="cat">' + escapeHtml((market && market.category) || 'unknown') + '</span>' +
            '<a href="' + escapeHtml((market && market.url) || '#') + '" target="_blank">View</a>' +
          '</div>'
        )
        .join('');
    } else {
      marketsEl.innerHTML = '<div class="empty">No markets loaded yet.</div>';
    }
  }

  if (tradesEl) {
    tradesEl.innerHTML = '<div class="empty">No trades yet.</div>';
  }

  autoExecute = !!data.auto_execute;
  allowCombos = !!data.allow_combos;
  updateToggleButtons();
}

function showError(msg) {
  const statusEl = document.getElementById('status');
  const marketsEl = document.getElementById('markets');

  if (statusEl) {
    statusEl.textContent = 'Error: ' + msg;
    statusEl.className = 'status error';
  }

  if (marketsEl) {
    marketsEl.innerHTML = '<div class="error">' + escapeHtml(msg) + '</div>';
  }
}

function escapeHtml(text) {
  if (text == null) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

function updateToggleButtons() {
  const autoBtn = document.getElementById('btn-auto');
  const combosBtn = document.getElementById('btn-combos');

  if (autoBtn) autoBtn.classList.toggle('active', autoExecute);
  if (combosBtn) combosBtn.classList.toggle('active', allowCombos);
}

function startRefresh() {
  if (refreshInterval) clearInterval(refreshInterval);
  fetchDashboard();
  refreshInterval = setInterval(fetchDashboard, 5000);
}

document.addEventListener('DOMContentLoaded', () => {
  startRefresh();
});
