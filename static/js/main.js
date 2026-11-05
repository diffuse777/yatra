/**
 * Bus Optimization System - Main JavaScript
 * Handles UI interactions, AJAX calls, and data visualization
 */

document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Initialize popovers
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function(popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });

    // Toggle sidebar on mobile
    const menuToggle = document.getElementById('menu-toggle');
    if (menuToggle) {
        menuToggle.addEventListener('click', function(e) {
            e.preventDefault();
            document.body.classList.toggle('sidebar-toggled');
            document.querySelector('.sidebar').classList.toggle('toggled');
            
            if (document.querySelector('.sidebar').classList.contains('toggled')) {
                document.querySelector('.sidebar .collapse').classList.remove('show');
            }
        });
    }

    // Close sidebar when clicking on a nav item (mobile view)
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', function() {
            if (window.innerWidth < 768) {
                document.body.classList.remove('sidebar-toggled');
                document.querySelector('.sidebar').classList.remove('toggled');
            }
        });
    });

    // Auto-hide alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert.alert-dismissible');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });

    // Initialize charts if they exist on the page
    if (typeof Chart !== 'undefined') {
        initializeCharts();
    }

    // Initialize any date pickers
    initializeDatePickers();

    // Initialize any data tables
    initializeDataTables();
});

/**
 * Initialize charts using Chart.js
 */
function initializeCharts() {
    // Passenger Flow Chart (Line Chart)
    const passengerCtx = document.getElementById('passengerFlowChart');
    if (passengerCtx) {
        new Chart(passengerCtx, {
            type: 'line',
            data: {
                labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                datasets: [{
                    label: 'Passengers',
                    data: [1200, 1900, 2100, 2500, 2200, 1800, 1500],
                    borderColor: 'rgba(13, 110, 253, 1)',
                    backgroundColor: 'rgba(13, 110, 253, 0.1)',
                    borderWidth: 2,
                    tension: 0.3,
                    fill: true,
                    pointBackgroundColor: '#fff',
                    pointBorderColor: 'rgba(13, 110, 253, 1)',
                    pointBorderWidth: 2,
                    pointRadius: 4,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.8)',
                        titleFont: { size: 14, weight: 'bold' },
                        bodyFont: { size: 13 },
                        padding: 12,
                        displayColors: false,
                        callbacks: {
                            label: function(context) {
                                return `Passengers: ${context.parsed.y.toLocaleString()}`;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: {
                            drawBorder: false,
                            color: 'rgba(0, 0, 0, 0.05)'
                        },
                        ticks: {
                            callback: function(value) {
                                return value >= 1000 ? value / 1000 + 'k' : value;
                            }
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        }
                    }
                }
            }
        });
    }

    // Occupancy Chart (Doughnut)
    const occupancyCtx = document.getElementById('occupancyChart');
    if (occupancyCtx) {
        new Chart(occupancyCtx, {
            type: 'doughnut',
            data: {
                labels: ['Occupied', 'Available'],
                datasets: [{
                    data: [68, 32],
                    backgroundColor: [
                        'rgba(25, 135, 84, 0.9)',
                        'rgba(233, 236, 239, 0.9)'
                    ],
                    borderWidth: 0,
                    cutout: '80%'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            usePointStyle: true,
                            pointStyle: 'circle'
                        }
                    },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.8)',
                        titleFont: { size: 14, weight: 'bold' },
                        bodyFont: { size: 13 },
                        padding: 12,
                        displayColors: false,
                        callbacks: {
                            label: function(context) {
                                return `${context.label}: ${context.parsed}%`;
                            }
                        }
                    }
                },
                cutout: '75%',
                radius: '90%'
            }
        });
    }
}

/**
 * Initialize date pickers
 */
function initializeDatePickers() {
    // Check if flatpickr is available
    if (typeof flatpickr !== 'undefined') {
        flatpickr('.datepicker', {
            dateFormat: 'Y-m-d',
            allowInput: true
        });
        
        flatpickr('.datetimepicker', {
            enableTime: true,
            dateFormat: 'Y-m-d H:i',
            allowInput: true
        });
    }
}

/**
 * Initialize DataTables if present
 */
function initializeDataTables() {
    // Check if DataTable is available
    if (typeof $.fn.DataTable === 'function') {
        $('.datatable').DataTable({
            responsive: true,
            language: {
                search: "_INPUT_",
                searchPlaceholder: "Search...",
                lengthMenu: "Show _MENU_ entries",
                info: "Showing _START_ to _END_ of _TOTAL_ entries",
                infoEmpty: "No entries found",
                infoFiltered: "(filtered from _MAX_ total entries)",
                paginate: {
                    first: "First",
                    last: "Last",
                    next: "Next",
                    previous: "Previous"
                }
            },
            dom: "<'row'<'col-sm-12 col-md-6'l><'col-sm-12 col-md-6'f>>" +
                 "<'row'<'col-sm-12'tr>>" +
                 "<'row'<'col-sm-12 col-md-5'i><'col-sm-12 col-md-7'p>>",
            lengthMenu: [[10, 25, 50, -1], [10, 25, 50, "All"]]
        });
    }
}

/**
 * AJAX helper function
 * @param {string} url - The URL to send the request to
 * @param {string} method - The HTTP method (GET, POST, etc.)
 * @param {Object} data - The data to send with the request
 * @param {Function} callback - The callback function to handle the response
 * @param {boolean} isJson - Whether to send data as JSON
 */
function ajaxRequest(url, method, data, callback, isJson = true) {
    const options = {
        method: method,
        headers: {}
    };

    if (data) {
        if (isJson) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(data);
        } else {
            const formData = new FormData();
            for (const key in data) {
                formData.append(key, data[key]);
            }
            options.body = formData;
        }
    }

    // Add CSRF token if available
    const csrfToken = document.querySelector('meta[name="csrf-token"]');
    if (csrfToken) {
        options.headers['X-CSRFToken'] = csrfToken.content;
    }

    fetch(url, options)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => callback(null, data))
        .catch(error => callback(error, null));
}

/**
 * Show a toast notification
 * @param {string} message - The message to display
 * @param {string} type - The type of notification (success, error, warning, info)
 * @param {number} duration - How long to show the notification in milliseconds
 */
function showToast(message, type = 'info', duration = 5000) {
    // Check if toast container exists, if not create it
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.style.position = 'fixed';
        toastContainer.style.top = '20px';
        toastContainer.style.right = '20px';
        toastContainer.style.zIndex = '9999';
        document.body.appendChild(toastContainer);
    }

    // Create toast element
    const toast = document.createElement('div');
    toast.className = `toast show align-items-center text-white bg-${type} border-0`;
    toast.role = 'alert';
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    
    // Add toast content
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;

    // Add to container
    toastContainer.appendChild(toast);

    // Auto-remove after duration
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, duration);

    // Add close button functionality
    const closeButton = toast.querySelector('.btn-close');
    if (closeButton) {
        closeButton.addEventListener('click', () => {
            toast.classList.remove('show');
            setTimeout(() => {
                toast.remove();
            }, 300);
        });
    }
}

// Dashboard specific functions
const Dashboard = {
    // Initialize dashboard
    init: function() {
        if (document.getElementById('dashboardPage')) {
            this.loadDashboardData();
            this.initEventListeners();
            setInterval(() => this.loadDashboardData(), 30000);
        }
    },
    
    // Initialize event listeners
    initEventListeners: function() {
        // Refresh button
        const refreshBtn = document.getElementById('refreshDashboard');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.loadDashboardData();
                showToast('Dashboard data refreshed', 'success');
            });
        }
        
        // Chart period buttons
        document.querySelectorAll('.chart-period').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const period = btn.dataset.period;
                this.updateChartPeriod(period);
            });
        });
    },
    
    // Load all dashboard data
    loadDashboardData: function() {
        this.loadStats();
        this.loadPassengerFlowData(7);
        this.loadOccupancyData();
        this.loadRecentEvents();
        this.loadBusesNeedingAttention();
    },
    
    // Update chart period
    updateChartPeriod: function(days) {
        // Update active button
        document.querySelectorAll('.chart-period').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.period === days);
        });
        this.loadPassengerFlowData(days);
    },
    
    // Load dashboard statistics
    loadStats: function() {
        ajaxRequest('/api/dashboard/stats', 'GET', null, (response) => {
            if (response.success) {
                const stats = response.data;
                document.getElementById('activeBusesCount').textContent = stats.active_buses || 0;
                document.getElementById('passengersToday').textContent = stats.passengers_today || 0;
                document.getElementById('avgWaitingTime').textContent = (stats.avg_waiting_time || 0).toFixed(1) + 'min';
                document.getElementById('avgOccupancy').textContent = (stats.avg_occupancy || 0).toFixed(0) + '%';
            }
        });
    },
    
    // Load passenger flow data
    loadPassengerFlowData: function(days) {
        ajaxRequest(`/api/dashboard/passenger-flow?days=${days}`, 'GET', null, (response) => {
            if (response.success) {
                this.renderPassengerFlowChart(response.data);
            }
        });
    },
    
    // Load occupancy distribution data
    loadOccupancyData: function() {
        ajaxRequest('/api/dashboard/occupancy-distribution', 'GET', null, (response) => {
            if (response.success) {
                this.renderOccupancyChart(response.data);
            }
        });
    },
    
    // Load recent events
    loadRecentEvents: function() {
        ajaxRequest('/api/events/recent', 'GET', null, (response) => {
            if (response.success) {
                this.renderRecentEvents(response.data);
            }
        });
    },
    
    // Load buses needing attention
    loadBusesNeedingAttention: function() {
        ajaxRequest('/api/buses/needing-attention', 'GET', null, (response) => {
            if (response.success) {
                this.renderBusesNeedingAttention(response.data);
            }
        });
    },
    
    // Render passenger flow chart
    renderPassengerFlowChart: function(data) {
        const ctx = document.getElementById('passengerFlowChart').getContext('2d');
        
        // Destroy existing chart if it exists
        if (window.passengerFlowChart) {
            window.passengerFlowChart.destroy();
        }
        
        window.passengerFlowChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels || [],
                datasets: [{
                    label: 'Passengers',
                    data: data.values || [],
                    backgroundColor: 'rgba(0, 123, 255, 0.1)',
                    borderColor: 'rgba(0, 123, 255, 1)',
                    borderWidth: 2,
                    tension: 0.3,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { mode: 'index', intersect: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Number of Passengers' }
                    },
                    x: { title: { display: true, text: 'Date' } }
                }
            }
        });
    },
    
    // Render occupancy distribution chart
    renderOccupancyChart: function(data) {
        const ctx = document.getElementById('occupancyChart').getContext('2d');
        
        if (window.occupancyChart) {
            window.occupancyChart.destroy();
        }
        
        window.occupancyChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Low (0-30%)', 'Medium (31-70%)', 'High (71-100%)'],
                datasets: [{
                    data: [
                        data.low_occupancy || 0,
                        data.medium_occupancy || 0,
                        data.high_occupancy || 0
                    ],
                    backgroundColor: [
                        'rgba(40, 167, 69, 0.8)',
                        'rgba(255, 193, 7, 0.8)',
                        'rgba(220, 53, 69, 0.8)'
                    ],
                    borderColor: [
                        'rgba(40, 167, 69, 1)',
                        'rgba(255, 193, 7, 1)',
                        'rgba(220, 53, 69, 1)'
                    ],
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom' },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const value = context.raw;
                                const percentage = total > 0 ? Math.round((value / total) * 100) : 0;
                                return `${context.label}: ${value} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    },
    
    // Render recent events
    renderRecentEvents: function(events) {
        const container = document.getElementById('recentEventsList');
        if (!container) return;
        
        if (!events || events.length === 0) {
            container.innerHTML = '<div class="text-center py-4 text-muted">No recent events found</div>';
            return;
        }
        
        container.innerHTML = events.map(event => `
            <div class="list-group-item list-group-item-action">
                <div class="d-flex w-100 justify-content-between">
                    <h6 class="mb-1">${event.title}</h6>
                    <small class="text-muted" title="${new Date(event.timestamp).toLocaleString()}">
                        ${this.formatTimeAgo(event.timestamp)}
                    </small>
                </div>
                <p class="mb-1">${event.description}</p>
                ${event.bus_number ? `<small class="text-muted">Bus #${event.bus_number}</small>` : ''}
            </div>
        `).join('');
    },
    
    // Render buses needing attention
    renderBusesNeedingAttention: function(buses) {
        const container = document.getElementById('attentionBusesList');
        const countElement = document.getElementById('attentionBusesCount');
        
        if (!container) return;
        
        // Update count
        if (countElement) {
            countElement.textContent = buses.length || 0;
        }
        
        if (!buses || buses.length === 0) {
            container.innerHTML = '<div class="text-center py-4 text-muted">No buses need attention</div>';
            return;
        }
        
        container.innerHTML = buses.map(bus => `
            <div class="list-group-item list-group-item-action">
                <div class="d-flex w-100 justify-content-between align-items-center">
                    <div>
                        <h6 class="mb-0">Bus #${bus.bus_number}</h6>
                        <small class="text-muted">${bus.issue}</small>
                    </div>
                    <span class="badge bg-${this.getStatusBadgeClass(bus.status)}">
                        ${bus.status}
                    </span>
                </div>
                ${bus.last_seen ? `
                    <div class="mt-2">
                        <small class="text-muted">
                            Last seen: ${new Date(bus.last_seen).toLocaleString()}
                        </small>
                    </div>
                ` : ''}
            </div>
        `).join('');
    },
    
    // Get badge class based on status
    getStatusBadgeClass: function(status) {
        const statusMap = {
            'delayed': 'warning',
            'maintenance': 'danger',
            'full': 'danger',
            'active': 'success',
            'inactive': 'secondary'
        };
        return statusMap[status.toLowerCase()] || 'secondary';
    },
    
    // Format time ago
    formatTimeAgo: function(timestamp) {
        const seconds = Math.floor((new Date() - new Date(timestamp)) / 1000);
        let interval = Math.floor(seconds / 31536000);
        if (interval >= 1) return interval + 'y ago';
        interval = Math.floor(seconds / 2592000);
        if (interval >= 1) return interval + 'mo ago';
        interval = Math.floor(seconds / 86400);
        if (interval >= 1) return interval + 'd ago';
        interval = Math.floor(seconds / 3600);
        if (interval >= 1) return interval + 'h ago';
        interval = Math.floor(seconds / 60);
        if (interval >= 1) return interval + 'm ago';
        return 'just now';
    }
};

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    Dashboard.init();
});

// Make functions available globally
window.ajaxRequest = ajaxRequest;
window.showToast = showToast;
