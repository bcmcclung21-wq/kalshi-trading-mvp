function fmt(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function pct(v) {
  const n = Number(v || 0);
  return `${(n * 100).toFixed(2)}%`;
}

function score(v) {
  const n = Number(v || 0);
  return n.toFixed(2);
}

function currency(v) {
  const n = Number(v || 0);
  return `$${n.toFixed(2)}`;
}

function yesNo(v) {
  return v ? "Yes" : "No";
}

function makeList(items) {
  if (!items || (Array.isArray(items) && items.length === 0)) {
    return '<div class="muted">No data</div>';
  }
  if (Array.isArray(items)) {
    return `<ul>${items.map(x => `<li>${fmt(typeof x === "string" ? x : JSON.stringify(x))}</li>`).join("")}</ul>`;
  }
  const keys = Object.keys(items);
  if (!keys.length) return '<div class="muted">No data</div>';
  return `<ul>${keys.map(k => `<li><strong>${k}:</strong> ${fmt(typeof items[k] === "string" ? items[k] : JSON.stringify(items[k]))}</li>`).join("")}</ul>`;
}

function renderKPIs(data) {
  const totals = data.totals || {};
  const cards = [
    ["Markets", totals.market_count || 0],
    ["Candidates", totals.candidate_count || 0],
    ["Orders", totals.order_count || 0],
    ["Submitted", totals.submitted_count || 0],
    ["Wins", totals.won_count || 0],
    ["Losses", totals.lost_count || 0],
    ["Settled", totals.settled_count || 0],
    ["Realized PnL", currency(totals.gross_realized_pnl || 0)],
  ];
  document.getElementById("kpis").innerHTML = cards.map(([label, value]) => `
    <article class="metric-card">
      <div class="metric-label">${label}</div>
      <div class="metric-value">${value}</div>
    </article>
  `).join("");
}

function renderSystemHealth(data) {
  const boot = data.boot_status || {};
  const engine = data.engine_summary?.engine || data.engine_summary?.engine_summary?.engine || {};
  document.getElementById("systemHealth").innerHTML = `
    <ul>
      <li><strong>Stage:</strong> ${fmt(boot.stage)}</li>
      <li><strong>Init DB OK:</strong> ${yesNo(boot.init_db_ok)}</li>
      <li><strong>Engine Started:</strong> ${yesNo(boot.engine_started)}</li>
      <li><strong>Uptime:</strong> ${fmt(boot.uptime_sec)}s</li>
      <li><strong>Last Sync:</strong> ${fmt(engine.last_sync_at)}</li>
      <li><strong>Last Cycle:</strong> ${fmt(engine.last_cycle_at)}</li>
      <li><strong>Last Reconcile:</strong> ${fmt(engine.last_reconcile_at)}</li>
      <li><strong>Last Audit:</strong> ${fmt(engine.last_audit_at)}</li>
      <li><strong>Last Error:</strong> ${fmt(engine.last_error)}</li>
    </ul>
  `;
}

function renderExecutionPosture(data) {
  const runtime = data.runtime || {};
  const engine = data.engine_summary?.engine || data.engine_summary?.engine_summary?.engine || {};
  document.getElementById("executionPosture").innerHTML = `
    <ul>
      <li><strong>Auth OK:</strong> ${yesNo(engine.auth_ok)}</li>
      <li><strong>AUTO_EXECUTE:</strong> ${yesNo(runtime.auto_execute)}</li>
      <li><strong>ALLOW_COMBOS:</strong> ${yesNo(runtime.allow_combos)}</li>
      <li><strong>SAME_DAY_ONLY:</strong> ${yesNo(runtime.same_day_only)}</li>
      <li><strong>MIN_MINUTES_TO_CLOSE:</strong> ${fmt(runtime.min_minutes_to_close)}</li>
      <li><strong>MAX_DAYS_TO_CLOSE:</strong> ${fmt(runtime.max_days_to_close)}</li>
      <li><strong>MAX_ORDERS_PER_CYCLE:</strong> ${fmt(runtime.max_orders_per_cycle)}</li>
      <li><strong>MAX_CATEGORY_EXPOSURE_PCT:</strong> ${pct(runtime.max_category_exposure_pct || 0)}</li>
    </ul>
  `;
}

function renderAuditPanels(data) {
  const audit = data.latest_audit || {};
  document.getElementById("latestIssues").innerHTML = makeList(audit.issues || {});
  document.getElementById("latestImprovements").innerHTML = makeList(audit.improvements || []);
  document.getElementById("whatWentWell").innerHTML = `
    <ul>
      <li><strong>Total Trades:</strong> ${fmt(audit.total_trades, 0)}</li>
      <li><strong>Wins:</strong> ${fmt(audit.wins, 0)}</li>
      <li><strong>Losses:</strong> ${fmt(audit.losses, 0)}</li>
      <li><strong>Win Rate:</strong> ${score(audit.win_rate || 0)}</li>
      <li><strong>Gross PnL:</strong> ${currency(audit.gross_pnl || 0)}</li>
      <li><strong>By Category:</strong> ${fmt(JSON.stringify(audit.by_category || {}))}</li>
    </ul>
  `;
  document.getElementById("learningPanel").innerHTML = `
    <h3>Feature Breakdown</h3>
    ${makeList(audit.feature_breakdown || {})}
    <h3>Calibration</h3>
    ${makeList(audit.calibration || {})}
    <h3>Learning Summary</h3>
    ${makeList(audit.learning_summary || {})}
  `;
}

function renderCandidates(data) {
  const rows = (data.candidates || []).map(row => `
    <tr>
      <td>${fmt(row.cycle_at)}</td>
      <td>${fmt(row.ticker)}</td>
      <td>${fmt(row.category)}</td>
      <td>${fmt(row.side)}</td>
      <td>${score(row.entry_price)}</td>
      <td>${score(row.total_score)}</td>
      <td>${score(row.projection_score)}</td>
      <td>${score(row.confidence_score)}</td>
      <td>${fmt(row.rationale)}</td>
    </tr>
  `).join("");
  document.getElementById("candidatesTable").innerHTML = rows || `<tr><td colspan="9" class="muted">No candidate runs yet</td></tr>`;
}

function renderOrders(data) {
  const rows = (data.orders || []).map(row => `
    <tr>
      <td>${fmt(row.created_at)}</td>
      <td>${fmt(row.ticker)}</td>
      <td>${fmt(row.category)}</td>
      <td>${fmt(row.side)}</td>
      <td>${fmt(row.count)}</td>
      <td>${fmt(row.price_cents)}</td>
      <td>${pct(row.bankroll_pct || 0)}</td>
      <td><span class="status status-${String(row.status || "").toLowerCase()}">${fmt(row.status)}</span></td>
      <td>${currency(row.realized_pnl || 0)}</td>
      <td>${yesNo(row.dry_run)}</td>
    </tr>
  `).join("");
  document.getElementById("ordersTable").innerHTML = rows || `<tr><td colspan="10" class="muted">No orders yet</td></tr>`;
}

function renderPositions(data) {
  const rows = (data.positions || []).map(row => `
    <tr>
      <td>${fmt(row.snapshot_at)}</td>
      <td>${fmt(row.ticker)}</td>
      <td>${fmt(row.category)}</td>
      <td>${fmt(row.side)}</td>
      <td>${fmt(row.quantity)}</td>
      <td>${score(row.avg_price)}</td>
      <td>${fmt(row.status)}</td>
    </tr>
  `).join("");
  document.getElementById("positionsTable").innerHTML = rows || `<tr><td colspan="7" class="muted">No positions yet</td></tr>`;
}

function renderNotes(data) {
  const notes = data.research_notes || [];
  document.getElementById("notesPanel").innerHTML = notes.length ? notes.map(note => `
    <article class="note">
      <div class="note-head">
        <strong>${fmt(note.category)}</strong>
        <span>${fmt(note.ticker)}</span>
      </div>
      <div class="note-scores">
        P:${score(note.projection_score)} · R:${score(note.research_score)} · C:${score(note.confidence_score)} · Confirm:${score(note.confirmation_score)} · EV:${score(note.ev_bonus)}
      </div>
      <div>${fmt(note.rationale)}</div>
      <div class="muted">Tags: ${fmt((note.tags || []).join(", "), "none")}</div>
    </article>
  `).join("") : `<div class="muted">No research notes yet</div>`;
}

function renderAll(data) {
  renderKPIs(data);
  renderSystemHealth(data);
  renderExecutionPosture(data);
  renderAuditPanels(data);
  renderCandidates(data);
  renderOrders(data);
  renderPositions(data);
  renderNotes(data);
}

async function refreshDashboard() {
  const res = await fetch('/api/dashboard');
  const data = await res.json();
  renderAll(data);
}

document.getElementById("refreshBtn")?.addEventListener("click", refreshDashboard);

renderAll(window.__INITIAL_DASHBOARD__ || {});
setInterval(refreshDashboard, 10000);
