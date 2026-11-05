/* XGBoost charts + real-time IST prediction tables */
(function () {
  var REFRESH_MS = 60000;
  var chartPred = null;
  var chartImp = null;

  function initTrainForm() {
    var form = document.getElementById('xgb-train-form');
    if (!form) return;
    form.addEventListener('submit', function () {
      var btn = document.getElementById('xgb-train-btn');
      if (!btn) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Training…';
    });
  }

  function fillTable(tableId, rows, columns) {
    var table = document.getElementById(tableId);
    if (!table) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    if (!rows || !rows.length) {
      tbody.innerHTML = '<tr><td colspan="' + columns.length + '" class="text-muted">No live rows for the current IST window.</td></tr>';
      return;
    }
    var html = '';
    for (var i = 0; i < rows.length; i++) {
      html += '<tr>';
      for (var c = 0; c < columns.length; c++) {
        var val = rows[i][columns[c]];
        html += '<td>' + (val === undefined || val === null ? '' : val) + '</td>';
      }
      html += '</tr>';
    }
    tbody.innerHTML = html;
  }

  function destroyChart(ch) {
    if (ch && typeof ch.destroy === 'function') ch.destroy();
  }

  function drawCharts(payload) {
    if (typeof Chart === 'undefined') {
      console.error('Chart.js failed to load');
      return;
    }
    if (!payload || !payload.trained) return;

    var comparison = payload.comparison || [];
    var importance = payload.feature_importance || [];
    var predCanvas = document.getElementById('xgbPredChart');
    var impCanvas = document.getElementById('xgbImportanceChart');
    if (!predCanvas || !impCanvas) return;

    destroyChart(chartPred);
    destroyChart(chartImp);

    var maxPoints = 120;
    var cmp = comparison.length > maxPoints ? comparison.slice(-maxPoints) : comparison;
    chartPred = new Chart(predCanvas, {
      type: 'line',
      data: {
        labels: cmp.map(function (r) { return r.timestamp; }),
        datasets: [
          { label: 'Actual Demand', data: cmp.map(function (r) { return r.actual_demand; }), borderColor: '#0B1F3A', pointRadius: 0, tension: 0.15, fill: false },
          { label: 'Predicted Demand', data: cmp.map(function (r) { return r.predicted_demand; }), borderColor: '#E65100', pointRadius: 0, tension: 0.15, fill: false }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { x: { ticks: { maxTicksLimit: 6 } } }
      }
    });

    var top = importance.slice(0, 12).slice().reverse();
    chartImp = new Chart(impCanvas, {
      type: 'bar',
      data: {
        labels: top.map(function (r) { return r.feature; }),
        datasets: [
          {
            label: 'Importance (gain %)',
            data: top.map(function (r) { return r.importance_pct; }),
            backgroundColor: 'rgba(230, 81, 0, 0.75)',
            borderColor: '#0B1F3A',
            borderWidth: 1
          }
        ]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        scales: { x: { beginAtZero: true, title: { display: true, text: 'Gain contribution (%)' } } }
      }
    });
  }

  function applyLiveTables(payload) {
    fillTable(
      'xgb-prediction-table',
      payload.comparison || [],
      ['timestamp', 'route', 'actual_demand', 'predicted_demand', 'error']
    );
    fillTable(
      'xgb-forecast-table',
      payload.forecast || [],
      ['timestamp', 'route', 'forecasted_demand']
    );
    var stamp = document.getElementById('xgb-live-stamp');
    if (stamp) {
      var parts = [];
      if (payload.now_ist) parts.push('now ' + payload.now_ist);
      if (payload.generated_at) parts.push('updated ' + payload.generated_at);
      stamp.textContent = parts.length ? (' · ' + parts.join(' · ')) : '';
    }
  }

  function loadResults() {
    if (!document.getElementById('xgbPredChart') && !document.getElementById('xgb-prediction-table')) return;
    fetch('/admin/models/xgboost/results.json', {
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin'
    })
      .then(function (r) {
        if (!r.ok) throw new Error('Failed to load XGBoost results (' + r.status + ')');
        return r.json();
      })
      .then(function (payload) {
        applyLiveTables(payload);
        if (document.getElementById('xgbPredChart')) drawCharts(payload);
      })
      .catch(function (err) {
        console.error(err);
      });
  }

  initTrainForm();
  loadResults();
  setInterval(loadResults, REFRESH_MS);
})();
