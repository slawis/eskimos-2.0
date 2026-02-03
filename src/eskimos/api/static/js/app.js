/**
 * Eskimos 2.0 - Dashboard JavaScript
 */

// Toast notifications
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast p-4 rounded-lg shadow-lg ${
        type === 'success' ? 'bg-green-500' :
        type === 'error' ? 'bg-red-500' :
        'bg-blue-500'
    } text-white`;
    toast.textContent = message;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s ease-out reverse';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// HTMX event handlers
document.body.addEventListener('htmx:afterRequest', function(event) {
    // Handle successful POST requests
    if (event.detail.successful && event.detail.requestConfig.verb === 'POST') {
        const path = event.detail.pathInfo.requestPath;

        if (path === '/api/sms/send') {
            try {
                const response = JSON.parse(event.detail.xhr.responseText);
                if (response.success) {
                    showToast('SMS wyslany pomyslnie!', 'success');
                } else {
                    showToast('Blad: ' + (response.error || 'Nieznany'), 'error');
                }
            } catch (e) {
                console.error('Parse error:', e);
            }
        }
    }
});

// Handle HTMX errors
document.body.addEventListener('htmx:responseError', function(event) {
    showToast('Blad polaczenia z serwerem', 'error');
});

// Auto-refresh health status every 30 seconds
setInterval(() => {
    const healthStatus = document.getElementById('health-status');
    if (healthStatus) {
        htmx.trigger(healthStatus, 'load');
    }
}, 30000);

// Keyboard shortcuts
document.addEventListener('keydown', function(event) {
    // Ctrl/Cmd + Enter to submit form
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        const activeElement = document.activeElement;
        if (activeElement.form) {
            activeElement.form.requestSubmit();
        }
    }
});

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('Eskimos 2.0 Dashboard loaded');

    // Auto-focus first input
    const firstInput = document.querySelector('input:not([type="hidden"])');
    if (firstInput) {
        firstInput.focus();
    }
});
