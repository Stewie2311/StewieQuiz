/* ==========================================================
   KIỂM TRA FORM DÙNG CHUNG cho TẤT CẢ form trong hệ thống.

   Mục tiêu: khi một ô bắt buộc còn trống hoặc nhập sai định dạng,
   KHÔNG xoá dữ liệu người dùng đã nhập, mà chỉ:
     - cuộn mượt tới đúng ô lỗi đầu tiên + focus
     - tô viền đỏ (.field-invalid), tự bỏ khi người dùng gõ lại
     - hiện 1 toast nhắc rõ ô nào cần sửa
   Dùng validation sẵn có của HTML (required, type=email, pattern,
   minlength...) nên không cần khai báo lại từng ô.
   ========================================================== */
(function () {
    'use strict';

    function lamSach(label) {
        return label.textContent.replace('*', '').replace(/\s+/g, ' ').trim();
    }

    // Tìm "nhãn" dễ hiểu của 1 ô để hiển thị trong thông báo
    function nhanCuaO(el) {
        if (el.id) {
            var l = document.querySelector('label[for="' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id) + '"]');
            if (l) return lamSach(l);
        }
        var node = el;
        for (var i = 0; i < 4 && node; i++) {
            node = node.parentElement;
            if (!node) break;
            var lab = node.querySelector('label');
            if (lab) return lamSach(lab);
        }
        return el.getAttribute('placeholder') || el.name || 'Trường này';
    }

    function thongBaoLoi(el) {
        var ten = nhanCuaO(el);
        var v = el.validity;
        if (v.valueMissing)       return 'Vui lòng nhập "' + ten + '"';
        if (v.typeMismatch)       return '"' + ten + '" chưa đúng định dạng';
        if (v.patternMismatch)    return '"' + ten + '" không hợp lệ';
        if (v.tooShort)           return '"' + ten + '" cần ít nhất ' + el.minLength + ' ký tự';
        if (v.tooLong)            return '"' + ten + '" quá dài';
        if (v.rangeUnderflow || v.rangeOverflow || v.stepMismatch)
                                  return '"' + ten + '" có giá trị không hợp lệ';
        return 'Vui lòng kiểm tra lại "' + ten + '"';
    }

    function danhDauVaToiO(el) {
        if (window.showToast) showToast(thongBaoLoi(el), 'warning');
        el.classList.add('field-invalid');
        try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.addEventListener('input', function huy() {
            el.classList.remove('field-invalid');
            el.removeEventListener('input', huy);
        });
        el.addEventListener('change', function huy2() {
            el.classList.remove('field-invalid');
            el.removeEventListener('change', huy2);
        });
    }

    function ganChoForm(form) {
        if (form.dataset.tuKiemTra) return;          // tránh gắn 2 lần
        form.dataset.tuKiemTra = '1';
        // Tắt bong bóng mặc định của trình duyệt để dùng giao diện toast của ta
        form.setAttribute('novalidate', 'novalidate');

        form.addEventListener('submit', function (e) {
            // Bỏ qua nếu form tự khai báo không cần kiểm tra
            if (form.hasAttribute('data-skip-validate')) return;
            if (!form.checkValidity()) {
                e.preventDefault();
                e.stopPropagation();
                var oLoi = form.querySelector(':invalid');
                if (oLoi) danhDauVaToiO(oLoi);
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('form').forEach(ganChoForm);
    });
})();
