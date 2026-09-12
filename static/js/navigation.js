/* ==========================================================
   navigation.js — Bản hướng đối tượng của việc chuyển tab.

   Làm cùng việc với switchTab() trong main.js, chỉ khác là nhớ được tab trước
   đó nên chỉ phải ẩn đúng một tab thay vì quét ẩn toàn bộ.
   ========================================================== */

class Navigation {
    constructor() {
        this.currentTab = 'dashboard';
        this.init();
    }

    init() {
        this.setupNavigation();
        this.setupSidebar();
    }

    setupNavigation() {
        const navItems = document.querySelectorAll('[data-tab]');
        navItems.forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const tabName = item.getAttribute('data-tab');
                this.switchTab(tabName);
            });
        });
    }

    setupSidebar() {
        const sidebarItems = document.querySelectorAll('.sidebar-item');
        sidebarItems.forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                document.querySelector('.sidebar')?.classList.remove('active');
            });
        });
    }

    switchTab(tabName) {
        const prevTab = this.currentTab;
        this.currentTab = tabName;

        const prevPane = document.getElementById(prevTab + '-tab');
        if (prevPane) {
            prevPane.classList.remove('active');
        }

        const newPane = document.getElementById(tabName + '-tab');
        if (newPane) {
            newPane.classList.add('active');
        }

        // Tô sáng mục đang mở trên thanh điều hướng.
        document.querySelectorAll('[data-tab]').forEach(el => {
            if (el.getAttribute('data-tab') === tabName) {
                el.classList.add('active');
            } else {
                el.classList.remove('active');
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new Navigation();
});
