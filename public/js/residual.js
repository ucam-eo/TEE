// VQ residual histogram panel.
//
// Visible only for viewports that opted into the fast path (the is-ready
// response surfaces fast_path=true). Fetches
//   /api/viewports/<name>/residual-histogram?year=Y
// and renders a small bar chart at the bottom-right of the viewer.
//
// The module exposes `window.refreshResidualHistogram()` — call it after a
// viewport switch or year change. Re-entrant: it skips redundant fetches when
// (viewport, year) hasn't changed.

let _chart = null;
let _lastKey = null;

function _isFastPath() {
    return !!(window.viewportStatus && window.viewportStatus.fast_path);
}

async function refreshResidualHistogram() {
    const panel = document.getElementById('residual-panel');
    if (!panel) return;
    const name = window.currentViewportName;
    const year = window.currentEmbeddingYear;

    if (!name || !year || !_isFastPath()) {
        panel.style.display = 'none';
        _lastKey = null;
        return;
    }

    const key = `${name}|${year}`;
    panel.style.display = 'block';
    if (key === _lastKey) return;
    _lastKey = key;

    const status = document.getElementById('residual-panel-status');
    status.textContent = 'Loading…';

    try {
        const url = `/api/viewports/${encodeURIComponent(name)}/residual-histogram?year=${encodeURIComponent(year)}`;
        const resp = await fetch(url);
        const data = await resp.json();
        if (!data.success) {
            status.textContent = data.error || 'Failed to fetch';
            return;
        }
        _render(data.histogram);
        status.textContent = '';
    } catch (err) {
        console.error('[VQ residual] fetch failed:', err);
        status.textContent = 'Fetch failed (see console)';
    }
}

function _render(hist) {
    // hist: { n_pixels, bin_edges: [N+1], counts: [N], stats: {mean,p10,p50,p90,p99} }
    const labels = [];
    for (let i = 0; i < hist.counts.length; i++) {
        labels.push(((hist.bin_edges[i] + hist.bin_edges[i + 1]) / 2).toFixed(3));
    }

    const ctx = document.getElementById('residual-chart').getContext('2d');
    if (_chart) _chart.destroy();
    _chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'pixels',
                data: hist.counts,
                backgroundColor: '#4caf50',
                borderWidth: 0,
                categoryPercentage: 1.0,
                barPercentage: 1.0,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            scales: {
                x: { display: false },
                y: { ticks: { font: { size: 9 } } },
            },
            plugins: {
                legend: { display: false },
                tooltip: { enabled: true },
            },
        },
    });

    const s = hist.stats || {};
    const fmt = (v) => (typeof v === 'number' ? v.toFixed(3) : '—');
    document.getElementById('residual-stats').innerHTML =
        `n=${(hist.n_pixels || 0).toLocaleString()}, ` +
        `mean=${fmt(s.mean)}, p50=${fmt(s.p50)}, p90=${fmt(s.p90)}, p99=${fmt(s.p99)}`;
}

window.refreshResidualHistogram = refreshResidualHistogram;
