/* ============================================================
   room-nav.js — Tráo tab "Cài đặt phòng" <-> "Giám sát" KHÔNG tải lại trang.
   - Bấm tab -> fetch trang kia ở dạng fragment (?frag=1) -> thay nội dung
     bên trong #roomHost -> chạy lại <script> của view mới.
   - Chỉ 1 view tồn tại trong DOM tại một thời điểm => CSS 2 view không đụng nhau.
   - Trước khi tráo, gọi window.__roomCleanup() để dừng timer/Socket.IO của view cũ.
   - Script này nằm NGOÀI #roomHost nên tồn tại xuyên suốt (không bị tráo).
   ============================================================ */
(function () {
    if (window.__roomNavInit) return;   // chỉ gắn 1 lần cho mỗi lần tải trang thật
    window.__roomNavInit = true;

    // <script> chèn bằng innerHTML KHÔNG tự chạy -> tạo lại node để thực thi.
    function runScripts(root) {
        root.querySelectorAll('script').forEach(function (old) {
            var s = document.createElement('script');
            for (var i = 0; i < old.attributes.length; i++) {
                s.setAttribute(old.attributes[i].name, old.attributes[i].value);
            }
            if (!old.src) s.textContent = old.textContent;
            old.parentNode.replaceChild(s, old);
        });
    }

    function setLoading(on) {
        var host = document.getElementById('roomHost');
        if (!host) return;
        host.style.transition = 'opacity .16s ease';
        host.style.opacity = on ? '0.35' : '1';
    }

    function swap(url, push) {
        var host = document.getElementById('roomHost');
        if (!host) { window.location.href = url; return; }

        // Dừng realtime (timer/Socket.IO) của view đang hiển thị.
        if (typeof window.__roomCleanup === 'function') {
            try { window.__roomCleanup(); } catch (e) {}
        }
        window.__roomCleanup = null;

        setLoading(true);
        var u = url + (url.indexOf('?') >= 0 ? '&' : '?') + 'frag=1';
        fetch(u, { headers: { 'X-Requested-With': 'fetch' }, credentials: 'same-origin' })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.text();
            })
            .then(function (html) {
                var tmp = document.createElement('div');
                tmp.innerHTML = html;
                var fresh = tmp.querySelector('#roomHost');
                host.innerHTML = fresh ? fresh.innerHTML : html;
                runScripts(host);
                setLoading(false);
                window.scrollTo(0, 0);
                if (push) history.pushState({ room: 1 }, '', url);
            })
            .catch(function () { window.location.href = url; });   // lỗi mạng -> tải thường
    }

    // Bắt click trên toàn document (uỷ quyền) -> sống sót qua mỗi lần tráo.
    document.addEventListener('click', function (e) {
        var a = e.target.closest ? e.target.closest('.rp-nav-tab') : null;
        if (!a) return;
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;  // cho mở tab mới
        e.preventDefault();
        if (a.classList.contains('active')) return;
        swap(a.getAttribute('href'), true);
    });

    // Nút Back/Forward: tải lại theo URL để hiển thị đúng view.
    window.addEventListener('popstate', function () { window.location.reload(); });
})();
