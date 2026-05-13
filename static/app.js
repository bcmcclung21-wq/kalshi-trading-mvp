/* Poly Trading MVP Dashboard */
let autoExecute = false;
let allowCombos = false;
let refreshInterval = null;

async function fetchDashboard() {
    try {
        const res = await fetch('/api/dashboard');
        if (!res.ok) {
            console.error('Dashboard fetch failed:', res.status, res.statusText);
            showError('Dashboard unavailable (HTTP ' + res.status + ')');
            return;
        }
        const data = await res.json();
        render(data);
    } catch (err) {
        console.error('Dashboard fetch error:', err);
        showError('Failed to load dashboard: ' + err.message);
    }
}

function render(data) {
    const statusEl = document.getElementById('status');
    const marketsEl = document.getElementById('markets');
    const tradesEl = document.getElementById('trades');

    const statusText = data && data.status ? data.status : 'unknown';
    const marketCount = data && data.markets_count !== undefined ? data.markets_count : 0;

    if (statusEl) {
        statusEl.textContent = 'Status: ' + statusText + ' | Markets: ' + marketCount;
        statusEl.className = 'status ' + (statusText === 'ok' ? 'ok' : 'error');
    }

    if (marketsEl) {
        if (data && data.markets && data.markets.length > 0) {
            marketsEl.innerHTML = data.markets.map(m =>
                '<div class="market">' +
                '<strong>' + escapeHtml(m.title || 'Untitled') + '</strong>' +
                '<span class="cat">' + escapeHtml(m.category || 'unknown') + '</span>' +
                '<a href="' + escapeHtml(m.url || '#') + '" target="_blank">View</a>' +
                '</div>'
            ).join('');
        } else {
            marketsEl.innerHTML = '<div class="empty">No markets loaded yet.</div>';
        }
    }

    if (tradesEl) {
        tradesEl.innerHTML = '<div class="empty">No trades yet.</div>';
    }

    if (data) {
        autoExecute = !!data.auto_execute;
        allowCombos = !!data.allow_combos;
        updateToggleButtons();
    }
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
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function updateToggleButtons() {
    const aeBtn = document.getElementById('btn-auto');
    const acBtn = document.getElementById('btn-combos');
    if (aeBtn) aeBtn.classList.toggle('active', autoExecute);
    if (acBtn) acBtn.classList.toggle('active', allowCombos);
}

function startRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    fetchDashboard();
    refreshInterval = setInterval(fetchDashboard, 5000);
}

document.addEventListener('DOMContentLoaded', function() {
    startRefresh();
});
