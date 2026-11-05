/* LSTM demand forecasting charts + real-time prediction tables */
(function () {
  var REFRESH_MS = 60000; // refresh live tables/charts every 60s
  var chartLoss = null;
  var chartPred = null;
  var chartForecast = null;

  function initTrainForm() {
    var form = document.getElementById('lstm-train-form');
    if (!form) return;
    form.addEventListener('submit', function () {
      var btn = document.getElementById('lstm-train-btn');
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
      tbody.innerHTML = '<tr><td colspan="' + columns.length + '" class="text-muted">No live rows yet. Train the model first.</td></tr>';
      return;
    }
    var html = '';
    for (var i = 0; i < rows.length; i++) {
      html += '<tr>';
      for (var c = 0; c < columns.length; c++) {
        var key = columns[c];
        var val = rows[i][key];
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
    if (!payload || !payload.trained || !payload.history || !payload.history.loss) {
      return;
    }

    var history = payload.history;
    var comparison = payload.comparison || [];
    var forecast = payload.forecast || [];

    destroyChart(chartLoss);
    destroyChart(chartPred);
    destroyChart(chartForecast);

    var lossLabels = history.loss.map(function (_, i) { return 'Dataset pass ' + (i + 1); });
    chartLoss = new Chart(document.getElementById('lossChart'), {
      type: 'line',
      data: {
        labels: lossLabels,
        datasets: [
          { label: 'Training error (on dataset)', data: history.loss, borderColor: '#0B1F3A', tension: 0.2, fill: false },
          { label: 'Check error (held-out part of dataset)', data: history.val_loss, borderColor: '#E65100', tension: 0.2, fill: false }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { title: { display: true, text: 'How many times the model read the full dataset' } },
          y: { beginAtZero: true, title: { display: true, text: 'Prediction error' } }
        }
      }
    });

    var maxPoints = 120;
    var cmp = comparison.length > maxPoints ? comparison.slice(-maxPoints) : comparison;
    chartPred = new Chart(document.getElementById('predChart'), {
      type: 'line',
      data: {
        labels: cmp.map(function (r) { return r.timestamp; }),
        datasets: [
          { label: 'Actual', data: cmp.map(function (r) { return r.actual_demand; }), borderColor: '#0B1F3A', pointRadius: 0, tension: 0.15, fill: false },
          { label: 'Predicted', data: cmp.map(function (r) { return r.predicted_demand; }), borderColor: '#E65100', pointRadius: 0, tension: 0.15, fill: false }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { x: { ticks: { maxTicksLimit: 8 } } }
      }
    });

    chartForecast = new Chart(document.getElementById('forecastChart'), {
      type: 'line',
      data: {
        labels: forecast.map(function (r) { return r.timestamp; }),
        datasets: [
          {
            label: 'Forecasted Demand',
            data: forecast.map(function (r) { return r.forecasted_demand; }),
            borderColor: '#1A3A5C',
            backgroundColor: 'rgba(230, 81, 0, 0.15)',
            fill: true,
            tension: 0.2
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { x: { ticks: { maxTicksLimit: 8 } } }
      }
    });
  }

  function applyLiveTables(payload) {
    fillTable('lstm-prediction-table', payload.comparison || [], ['timestamp', 'actual_demand', 'predicted_demand']);
    fillTable('lstm-forecast-table', payload.forecast || [], ['timestamp', 'forecasted_demand']);
    var stamp = document.getElementById('lstm-live-stamp');
    if (stamp) {
      var t = payload.generated_at || (payload.live && payload.live.generated_at) || '';
      stamp.textContent = t ? (' · updated ' + t) : '';
    }
  }

  function loadAndDraw() {
    if (!document.getElementById('lossChart') && !document.getElementById('lstm-prediction-table')) return;
    fetch('/admin/models/lstm/results.json', {
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin'
    })
      .then(function (r) {
        if (!r.ok) throw new Error('Failed to load LSTM results (' + r.status + ')');
        return r.json();
      })
      .then(function (payload) {
        applyLiveTables(payload);
        if (document.getElementById('lossChart')) {
          drawCharts(payload);
        }
      })
      .catch(function (err) {
        console.error(err);
      });
  }

  initTrainForm();
  loadAndDraw();
  setInterval(loadAndDraw, REFRESH_MS);
})();
