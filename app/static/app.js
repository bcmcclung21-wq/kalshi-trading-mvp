async function refreshSummary() {
  const res = await fetch('/api/summary');
  const data = await res.json();
  const el = document.getElementById('summary');
  if (el) el.textContent = JSON.stringify(data, null, 2);
}
setInterval(refreshSummary, 5000);
