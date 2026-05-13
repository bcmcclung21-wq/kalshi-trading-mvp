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
    const statsEl = document.getElementById('stats');

    const statusText = data.status || 'unknown';
    const marketCount = typeof data.markets_count === 'number' ? data.markets_count : 0;

    if (statusEl) {
        statusEl.textContent = 'Status: ' + statusText + ' | Markets: ' + marketCount;
        statusEl.className = 'status ' + (statusText === 'ok' ? 'ok' : 'error');
    }

    // Markets
    if (marketsEl) {
        const marketList = Array.isArray(data.markets) ? data.markets : [];
        if (marketList.length > 0) {
            marketsEl.innerHTML = marketList.map((market) =>
                '<div class="market-card">' +
                '<b>' + escapeHtml((market && market.title) || 'Untitled') + '</b>' +
                '<span class="tag">' + escapeHtml((market && market.category) || 'unknown') + '</span>' +
                '<span class="meta">conf=' + (market.confidence || 0).toFixed(3) +
                ' liq=' + (market.liquidity || 0).toFixed(0) +
                ' spread=' + (market.spread || 0).toFixed(4) + '</span>' +
                (market.url ? ' <a href="' + escapeHtml(market.url) + '" target="_blank">View</a>' : '') +
                '</div>'
            ).join('');
        } else {
            marketsEl.innerHTML = '<p>No markets loaded yet.</p>';
        }
    }

    // Trades
    if (tradesEl) {
        const tradeList = Array.isArray(data.trades) ? data.trades : [];
        if (tradeList.length > 0) {
            tradesEl.innerHTML = '<h3>Recent Trades</h3>' + tradeList.map((t) =>
                '<div class="trade-row ' + (t.status || '') + '">' +
                '<b>' + escapeHtml(t.market_title || t.market_id || '?') + '</b>' +
                '<span>' + (t.side || '?') + ' @ ' + (t.price || 0).toFixed(4) +
                ' | score=' + (t.total_score || 0) +
                ' | <b>' + (t.status || '?') + '</b></span>' +
                '</div>'
            ).join('');
        } else {
            tradesEl.innerHTML = '<h3>Trades</h3><p>No trades yet.</p>';
        }
    }

    // Stats / Learning Panel
    if (statsEl) {
        const ls = data.learning || {};
        const plan = data.last_plan || {};
        statsEl.innerHTML =
            '<div class="stats-grid">' +
            '<div class="stat-box"><label>Brier Score</label><value>' + (data.brier_score || 0).toFixed(4) + '</value></div>' +
            '<div class="stat-box"><label>Win Rate</label><value>' + ((data.win_rate || 0) * 100).toFixed(1) + '%</value></div>' +
            '<div class="stat-box"><label>Total Trades</label><value>' + (ls.total_trades || 0) + '</value></div>' +
            '<div class="stat-box"><label>Daily PnL</label><value>$' + ((data.daily_stats && data.daily_stats.daily_pnl) || 0).toFixed(2) + '</value></div>' +
            '<div class="stat-box"><label>Trades Today</label><value>' + ((data.daily_stats && data.daily_stats.trades_today) || 0) + '</value></div>' +
            '</div>' +
            (plan.adjustments && plan.adjustments.length ? 
                '<div class="plan-box"><h4>Today\'s Adjustments</h4>' +
                plan.adjustments.map(a => '<div>• <b>' + a.parameter + '</b>: ' + a.change + ' <i>(' + a.reason + ')</i></div>').join('') +
                '</div>' : '') +
            (plan.focus_areas && plan.focus_areas.length ?
                '<div class="focus-box"><h4>Focus Areas</h4>' +
                plan.focus_areas.map(f => '<div>• ' + escapeHtml(f) + '</div>').join('') +
                '</div>' : '');
    }

    autoExecute = !!data.auto_execute;
    allowCombos = !!data.allow_combos;
    updateToggleButtons();
}

function showError(msg) {
    const statusEl = document.getElementById('status');
    const marketsEl = document.getElementById('markets');
    if (statusEl) { statusEl.textContent = 'Error: ' + msg; statusEl.className = 'status error'; }
    if (marketsEl) { marketsEl.innerHTML = '<p class="error">' + escapeHtml(msg) + '</p>'; }
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

document.addEventListener('DOMContentLoaded', () => { startRefresh(); });
