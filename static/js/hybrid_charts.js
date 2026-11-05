/* Hybrid LSTM+XGBoost charts + prediction table */
(function () {
  var charts = {};

  function initTrainForm() {
    var form = document.getElementById('hybrid-train-form');
    if (!form) return;
    form.addEventListener('submit', function () {
      var btn = document.getElementById('hybrid-train-btn');
      if (!btn) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Training…';
    });
  }

  function destroy(id) {
    if (charts[id] && typeof charts[id].destroy === 'function') {
      charts[id].destroy();
      charts[id] = null;
    }
  }

  function fillTable(rows) {
    var table = document.getElementById('hybrid-prediction-table');
    if (!table) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    if (!rows || !rows.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="text-muted">No prediction rows.</td></tr>';
      return;
    }
    var html = '';
    var maxRows = Math.min(rows.length, 400);
    for (var i = 0; i < maxRows; i++) {
      var r = rows[i];
      html += '<tr>' +
        '<td>' + (r.timestamp || '') + '</td>' +
        '<td>' + (r.route || '') + '</td>' +
        '<td>' + r.actual_demand + '</td>' +
        '<td>' + r.lstm_prediction + '</td>' +
        '<td>' + r.xgboost_prediction + '</td>' +
        '<td>' + r.hybrid_prediction + '</td>' +
        '<td>' + r.lstm_error + '</td>' +
        '<td>' + r.xgboost_error + '</td>' +
        '<td>' + r.hybrid_error + '</td>' +
        '</tr>';
    }
    tbody.innerHTML = html;
  }

  function lineChart(canvasId, labels, actual, pred, predLabel, color) {
    var el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;
    destroy(canvasId);
    charts[canvasId] = new Chart(el, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          { label: 'Actual', data: actual, borderColor: '#0B1F3A', pointRadius: 0, tension: 0.15, fill: false },
          { label: predLabel, data: pred, borderColor: color, pointRadius: 0, tension: 0.15, fill: false }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { boxWidth: 12 } } },
        scales: { x: { ticks: { maxTicksLimit: 5 } } }
      }
    });
  }

  function draw(payload) {
    if (!payload || !payload.trained) return;
    var preds = payload.predictions || [];
    fillTable(preds);

    var maxPoints = 120;
    var slice = preds.length > maxPoints ? preds.slice(-maxPoints) : preds;
    var labels = slice.map(function (r) { return r.timestamp; });
    var actual = slice.map(function (r) { return r.actual_demand; });

    lineChart('hybridLstmChart', labels, actual,
      slice.map(function (r) { return r.lstm_prediction; }), 'LSTM', '#1565C0');
    lineChart('hybridXgbChart', labels, actual,
      slice.map(function (r) { return r.xgboost_prediction; }), 'XGBoost', '#2E7D32');
    lineChart('hybridPredChart', labels, actual,
      slice.map(function (r) { return r.hybrid_prediction; }), 'Hybrid', '#E65100');

    var errEl = document.getElementById('hybridErrChart');
    if (errEl && typeof Chart !== 'undefined') {
      destroy('hybridErrChart');
      charts.hybridErrChart = new Chart(errEl, {
        type: 'bar',
        data: {
          labels: labels,
          datasets: [{
            label: 'Hybrid |Error|',
            data: slice.map(function (r) { return r.hybrid_error; }),
            backgroundColor: 'rgba(230, 81, 0, 0.55)'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { x: { ticks: { maxTicksLimit: 6 } } }
        }
      });
    }

    var cmp = (payload.comparison && payload.comparison.chart_series) || null;
    var cmpEl = document.getElementById('hybridCmpChart');
    if (cmp && cmpEl && typeof Chart !== 'undefined') {
      destroy('hybridCmpChart');
      charts.hybridCmpChart = new Chart(cmpEl, {
        type: 'bar',
        data: {
          labels: cmp.labels || ['LSTM', 'XGBoost', 'Hybrid'],
          datasets: [
            { label: 'MAE', data: cmp.mae, backgroundColor: '#0B1F3A' },
            { label: 'RMSE', data: cmp.rmse, backgroundColor: '#1565C0' },
            { label: 'MAPE', data: cmp.mape, backgroundColor: '#FF8F00' },
            { label: 'R²', data: cmp.r2, backgroundColor: '#E65100' }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom' } }
        }
      });
    }
  }

  function load() {
    fetch('/admin/models/hybrid-lstm-xgboost/results.json', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(draw)
      .catch(function (err) { console.error('Hybrid results load failed', err); });
  }

  initTrainForm();
  load();
})();
