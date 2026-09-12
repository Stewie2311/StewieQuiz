/* ==========================================================
   api.js — Gọi API JSON cho gọn.

   Bọc fetch() lại để mọi nơi gọi API đều xử lý lỗi giống nhau: lỗi mạng hay
   lỗi HTTP đều hiện một thông báo cho người dùng rồi ném tiếp, thay vì im lặng
   để trang treo và không ai biết chuyện gì đã xảy ra.

   Dùng: API.get(url), API.post(url, data), API.put(...), API.delete(...)
   ========================================================== */

const API = {
    async request(url, options = {}) {
        try {
            const response = await fetch(url, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API Error:', error);
            showNotification('Lỗi kết nối!', 'danger');
            throw error;
        }
    },

    get(url) {
        return this.request(url);
    },

    post(url, data) {
        return this.request(url, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    },

    put(url, data) {
        return this.request(url, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },

    delete(url) {
        return this.request(url, {
            method: 'DELETE'
        });
    }
};
