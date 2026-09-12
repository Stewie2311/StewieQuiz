/* ==========================================================
   auth.js — Chỉ dùng cho trang đăng nhập và đăng ký.

   Việc duy nhất: nút con mắt bật/tắt xem mật khẩu. Xử lý cho cả hai ô (một ở
   trang đăng nhập, một ở trang đăng ký) — hai ô có id khác nhau nên phải gắn
   sự kiện riêng, và ô nào không có trên trang hiện tại thì bỏ qua.
   ========================================================== */

document.addEventListener('DOMContentLoaded', function() {
    initPasswordToggle();
});

function initPasswordToggle() {
    const toggleBtn = document.getElementById('togglePw');
    const passwordInput = document.getElementById('passwordInput');
    
    if (toggleBtn && passwordInput) {
        toggleBtn.addEventListener('click', function() {
            const icon = this.querySelector('i');
            if (passwordInput.type === 'password') {
                passwordInput.type = 'text';
                icon.classList.remove('fa-eye');
                icon.classList.add('fa-eye-slash');
            } else {
                passwordInput.type = 'password';
                icon.classList.remove('fa-eye-slash');
                icon.classList.add('fa-eye');
            }
        });
    }

    // Ô mật khẩu ở trang đăng ký.
    const regToggle = document.getElementById('toggleRegPw');
    const regInput = document.getElementById('regPasswordInput');
    
    if (regToggle && regInput) {
        regToggle.addEventListener('click', function() {
            const icon = this.querySelector('i');
            if (regInput.type === 'password') {
                regInput.type = 'text';
                icon.classList.remove('fa-eye');
                icon.classList.add('fa-eye-slash');
            } else {
                regInput.type = 'password';
                icon.classList.remove('fa-eye-slash');
                icon.classList.add('fa-eye');
            }
        });
    }
}
