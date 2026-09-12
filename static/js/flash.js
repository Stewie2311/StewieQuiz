/* ==========================================================
   THÔNG BÁO (TOAST) + tự gắn CSRF token cho request AJAX
   ========================================================== */
(function () {
    'use strict';

    const ICONS = {
        success: 'fa-circle-check',
        danger:  'fa-circle-xmark',
        warning: 'fa-triangle-exclamation',
        info:    'fa-circle-info',
    };
    const AUTO_HIDE_MS = 5000;   // tự tắt sau 5 giây

    function getContainer() {
        let c = document.querySelector('.flash-container');
        if (!c) {
            c = document.createElement('div');
            c.className = 'flash-container';
            document.body.appendChild(c);
        }
        return c;
    }

    function dismiss(toast) {
        if (!toast || toast.dataset.closing) return;
        toast.dataset.closing = '1';
        toast.classList.add('hide');
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 380);
    }

    // Hàm dùng chung: window.showToast('Nội dung', 'success')
    function showToast(message, type) {
        type = ICONS[type] ? type : 'info';
        const toast = document.createElement('div');
        toast.className = 'flash-toast flash-' + type;
        toast.setAttribute('role', 'alert');
        toast.innerHTML =
            '<span class="flash-icon"><i class="fas ' + ICONS[type] + '"></i></span>' +
            '<div class="flash-body"></div>' +
            '<button type="button" class="flash-close" aria-label="Đóng">&times;</button>' +
            '<span class="flash-progress"></span>';
        toast.querySelector('.flash-body').textContent = message;
        toast.querySelector('.flash-close').addEventListener('click', () => dismiss(toast));

        getContainer().appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));

        let timer = setTimeout(() => dismiss(toast), AUTO_HIDE_MS);
        // Di chuột vào thì tạm dừng đếm ngược cho dễ đọc
        toast.addEventListener('mouseenter', () => {
            clearTimeout(timer);
            const bar = toast.querySelector('.flash-progress');
            if (bar) bar.style.animationPlayState = 'paused';
        });
        toast.addEventListener('mouseleave', () => {
            timer = setTimeout(() => dismiss(toast), 1500);
            const bar = toast.querySelector('.flash-progress');
            if (bar) bar.style.animationPlayState = 'running';
        });
        return toast;
    }

    window.showToast = showToast;
    // Tương thích ngược với code cũ gọi showNotification(msg, type)
    window.showNotification = showToast;

    // Khi tải trang: biến các flash do server render thành toast tự tắt
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-flash]').forEach(function (el) {
            showToast(el.getAttribute('data-flash-message') || el.textContent.trim(),
                      el.getAttribute('data-flash') || 'info');
            el.remove();
        });
    });

    // ---- Tự gắn CSRF token cho mọi fetch() làm thay đổi dữ liệu ----
    const meta = document.querySelector('meta[name="csrf-token"]');
    const CSRF = meta ? meta.getAttribute('content') : null;
    const UNSAFE = /^(POST|PUT|PATCH|DELETE)$/i;

    if (CSRF && window.fetch) {
        const _fetch = window.fetch;
        window.fetch = function (input, init) {
            init = init || {};
            const method = (init.method ||
                (typeof input !== 'string' && input ? input.method : 'GET') || 'GET');
            // Chỉ gắn cho request cùng nguồn (same-origin) và phương thức ghi dữ liệu
            const url = (typeof input === 'string') ? input : (input && input.url) || '';
            const sameOrigin = !/^https?:\/\//i.test(url) ||
                               url.indexOf(window.location.origin) === 0;
            if (UNSAFE.test(method) && sameOrigin) {
                const headers = new Headers(init.headers || (typeof input !== 'string' && input ? input.headers : undefined));
                if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', CSRF);
                init.headers = headers;
            }
            return _fetch(input, init);
        };
    }
})();
