/* ==========================================================
   main.js — Khung giao diện dùng chung trên mọi trang.

   Lo ba việc: chuyển tab, nút menu trên điện thoại, và menu tài khoản.
   Kèm vài hàm tiện ích (thông báo, vòng xoay chờ) mà các trang khác gọi tới.

   Tab hoạt động thuần bằng CSS: mọi nội dung đều đã có sẵn trong trang, đổi tab
   chỉ là thêm/bớt lớp `active`. Không gọi server, nên chuyển tab tức thì.
   ========================================================== */

document.addEventListener('DOMContentLoaded', function() {
    initNavigation();
    initMobileMenu();
    initUserMenu();
});

function initNavigation() {
    const navItems = document.querySelectorAll('[data-tab]');

    navItems.forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            const tabName = this.getAttribute('data-tab');
            switchTab(tabName);
        });
    });
}

// Ẩn hết tab, hiện tab được chọn, rồi tô sáng mục tương ứng trên thanh điều hướng.
function switchTab(tabName) {
    document.querySelectorAll('.tab-pane').forEach(el => {
        el.classList.remove('active');
    });

    const tab = document.getElementById(tabName + '-tab');
    if (tab) {
        tab.classList.add('active');
    }

    document.querySelectorAll('[data-tab]').forEach(el => {
        el.classList.remove('active');
        if (el.getAttribute('data-tab') === tabName) {
            el.classList.add('active');
        }
    });
}

function initMobileMenu() {
    const toggle = document.getElementById('mobileToggle');
    const sidebar = document.querySelector('.sidebar');
    
    if (toggle && sidebar) {
        toggle.addEventListener('click', function() {
            sidebar.classList.toggle('active');
        });
    }
}

function initUserMenu() {
    const userBtn = document.querySelector('.user-btn');
    const userMenu = document.querySelector('.user-menu');
    
    if (userBtn) {
        document.addEventListener('click', function(e) {
            if (!userMenu || !userMenu.contains(e.target)) {
                const dropdown = userMenu?.querySelector('.dropdown-menu');
                if (dropdown) dropdown.style.display = 'none';
            }
        });
    }
}

/* ---------- Tiện ích dùng chung ---------- */

// Hiện một thông báo nổi, tự biến mất sau 4 giây. api.js gọi hàm này khi lỗi.
function showNotification(message, type = 'info') {
    const container = document.querySelector('.flash-container') || createFlashContainer();
    
    const alert = document.createElement('div');
    alert.className = `alert alert-${type} animate-fade-in`;
    alert.innerHTML = `
        <i class="fas fa-${type === 'success' ? 'check-circle' : 'info-circle'} me-2"></i>
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    container.appendChild(alert);
    
    setTimeout(() => {
        alert.remove();
    }, 4000);
}

function createFlashContainer() {
    const container = document.createElement('div');
    container.className = 'flash-container';
    document.body.appendChild(container);
    return container;
}

function showLoader() {
    const loader = document.createElement('div');
    loader.className = 'loader';
    loader.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    document.body.appendChild(loader);
}

function hideLoader() {
    const loader = document.querySelector('.loader');
    if (loader) loader.remove();
}
