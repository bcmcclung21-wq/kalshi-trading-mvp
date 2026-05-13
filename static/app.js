async function load() {
    try {
        const r = await fetch('/api/dashboard');
        const d = await r.json();
        document.getElementById('app').innerHTML = `
            <h1>Poly Trading MVP</h1>
            <div class="card">
                <h2>Markets: ${d.markets} | Candidates: ${d.candidates} | Orders: ${d.orders}</h2>
                <p>Calibration: ${d.calibration.status} | Brier: ${d.calibration.brier}</p>
                <p>Auth: ${d.execution_posture.auth_ok} | Auto: ${d.execution_posture.auto_execute}</p>
            </div>
            <div class="card">
                <pre>${JSON.stringify(d, null, 2)}</pre>
            </div>
        `;
    } catch(e) {
        document.getElementById('app').innerHTML = `<p class="error">Error: ${e.message}</p>`;
    }
}
load();
setInterval(load, 5000);
