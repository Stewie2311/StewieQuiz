/* ==========================================================
   Hiệu ứng giao diện cao cấp:
   1) Ripple khi click nút/menu
   2) Cascade: khi đổi tab, các item con đổ xuống lần lượt
   3) Đếm số động cho các thẻ thống kê
   ========================================================== */
(function () {
    'use strict';

    var REDUCE = window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ---------- 1) RIPPLE ----------
    var RIPPLE_SEL = '.nav-link, .btn, .btn-auth, .pf-tab-btn, .btn-act, .nav-pills .nav-link';
    document.addEventListener('click', function (e) {
        if (REDUCE) return;
        var el = e.target.closest(RIPPLE_SEL);
        if (!el || el.hasAttribute('disabled') || el.classList.contains('disabled')) return;
        var rect = el.getBoundingClientRect();
        if (!rect.width) return;
        var size = Math.max(rect.width, rect.height);
        var ripple = document.createElement('span');
        ripple.className = 'ui-ripple';
        ripple.style.width = ripple.style.height = size + 'px';
        ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
        ripple.style.top  = (e.clientY - rect.top  - size / 2) + 'px';
        el.appendChild(ripple);
        setTimeout(function () { ripple.remove(); }, 600);
    }, false);

    // ---------- 2) CASCADE khi đổi tab ----------
    var CASCADE_SEL = [
        '.feature-card', '.main-func-card', '.adv-item', '.section-eyebrow',
        '.room-card-sys', '.section-card',
        '.data-table tbody tr', '.stat-mini', '.pf-stat',
        '.list-toolbar', '.ngan-hang-section', '.bloom-legend'
    ].join(', ');

    function cascade(pane) {
        // ĐÃ TẮT hiệu ứng "đổ item lần lượt" (ui-rise có blur trên nhiều phần tử)
        // vì gây GIẬT và không đồng đều giữa các tab (tab nhiều dữ liệu càng nặng).
        // Giờ mỗi tab chỉ mờ vào (paneFade) mượt & đồng nhất. Chỉ giữ việc đếm số.
        demSoTrong(pane);
    }

    var container = document.querySelector('.tabs-container');
    if (container && 'MutationObserver' in window) {
        var obs = new MutationObserver(function (muts) {
            muts.forEach(function (m) {
                var p = m.target;
                if (p.classList && p.classList.contains('tab-pane') &&
                    p.classList.contains('active') && p.parentElement === container) {
                    cascade(p);
                }
            });
        });
        container.querySelectorAll(':scope > .tab-pane').forEach(function (p) {
            obs.observe(p, { attributes: true, attributeFilter: ['class'] });
        });
    }

    // ---------- 3) ĐẾM SỐ ĐỘNG ----------
    var COUNT_SEL = '.stat-mini-val, .pf-stat .v';
    var SO_RE = /^\s*\d+([.,]\d+)?\s*$/;

    function demSo(el) {
        if (el.dataset.counted) return;
        var raw = el.textContent.trim();
        if (!SO_RE.test(raw)) return;
        el.dataset.counted = '1';
        var laThapPhan = /[.,]/.test(raw);
        var dich = parseFloat(raw.replace(',', '.'));
        if (REDUCE || !isFinite(dich)) { return; }
        var batDau = performance.now(), thoiLuong = 900;
        function buoc(now) {
            var p = Math.min((now - batDau) / thoiLuong, 1);
            var e = 1 - Math.pow(1 - p, 3);          // ease-out cubic
            var v = dich * e;
            el.textContent = laThapPhan ? v.toFixed(1) : Math.round(v).toString();
            if (p < 1) requestAnimationFrame(buoc);
            else el.textContent = laThapPhan ? dich.toFixed(1) : Math.round(dich).toString();
        }
        requestAnimationFrame(buoc);
    }

    function demSoTrong(root) {
        (root || document).querySelectorAll(COUNT_SEL).forEach(demSo);
    }

    // Đếm số cho các thẻ thống kê đang hiển thị khi tải trang (vd: trang Hồ sơ, Kết quả)
    document.addEventListener('DOMContentLoaded', function () {
        if ('IntersectionObserver' in window) {
            var io = new IntersectionObserver(function (entries, ob) {
                entries.forEach(function (en) {
                    if (en.isIntersecting) { demSo(en.target); ob.unobserve(en.target); }
                });
            }, { threshold: 0.4 });
            document.querySelectorAll(COUNT_SEL).forEach(function (el) { io.observe(el); });
        } else {
            demSoTrong(document);
        }
    });
})();
