/* ==========================================================
   utils.js — Vài hàm tiện ích nhỏ dùng khắp nơi.
   ========================================================== */

// Hoãn gọi hàm cho tới khi người dùng ngừng thao tác `wait` mili-giây.
// Dùng cho ô tìm kiếm: gõ 10 chữ mà bắn 10 request thì rất phí.
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function formatDate(date) {
    const d = new Date(date);
    return d.toLocaleDateString('vi-VN') + ' ' + d.toLocaleTimeString('vi-VN');
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('Đã sao chép!', 'success');
    });
}

function formatNumber(num) {
    return new Intl.NumberFormat('vi-VN').format(num);
}

function pluralize(count, word) {
    return count === 1 ? word : word + 's';
}
