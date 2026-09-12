# Stewie Quiz — Hệ thống tạo câu hỏi & thi trắc nghiệm bằng AI

Ứng dụng web (Flask) cho phép giáo viên tải tài liệu PDF, dùng AI (Google
Gemini) tự sinh câu hỏi trắc nghiệm theo thang Bloom, tạo phòng thi và chấm
điểm tự động; học sinh đăng nhập làm bài, có giám sát webcam và chống rời màn hình.

## 📋 Yêu cầu
- Python 3.10+
- MySQL 5.7+ (hoặc MariaDB tương đương)
- Tài khoản Google Gemini API key

## 🚀 Cài đặt

### 1. Tải mã nguồn & tạo môi trường ảo
```bash
cd <thư_mục_dự_án>
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
```

### 2. Cài thư viện
```bash
pip install -r requirements.txt
```

### 3. Cấu hình biến môi trường
```bash
copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
```
Mở `.env` và điền: `SECRET_KEY`, thông tin `DB_*`, `GEMINI_API_KEY`,
`GIAO_VIEN_CODE`, và (tuỳ chọn) `EMAIL_ADDRESS` / `EMAIL_APP_PASSWORD` để gửi OTP.

### 4. Tạo cơ sở dữ liệu
Tạo database MySQL trùng tên `DB_NAME` trong `.env` và import schema của bạn.

## ▶️ Chạy ứng dụng

**Phát triển (development):**
```bash
python app.py
```
Truy cập http://localhost:5000

**Chạy thật (production) — dùng waitress:**
```bash
set FLASK_ENV=production       # Windows
python serve.py
```

## 🔐 Bảo mật đã tích hợp
- Mật khẩu băm (Werkzeug), xác minh email bằng OTP.
- Chống CSRF trên mọi form (Flask-WTF), header bảo mật, cookie `HttpOnly`/`SameSite`.
- Giới hạn tần suất đăng nhập/OTP (Flask-Limiter) chống brute-force.
- Phân quyền Admin / Giáo viên / Học sinh; kiểm tra quyền sở hữu dữ liệu (chống IDOR).
- Lỗi hệ thống được ghi log (`logs/app.log`), không lộ chi tiết ra người dùng.

## 📁 Cấu trúc chính
```
app.py            # Khởi tạo app, đăng ký extension, error handler, logging
config.py         # Cấu hình theo môi trường + hardening session/CSRF
database.py       # Kết nối MySQL qua connection pool
decorators.py     # login_required / giao_vien_required / admin_required (dùng chung)
extensions.py     # Khởi tạo CSRF + Rate limiter
serve.py          # Chạy production bằng waitress
routes/           # Các blueprint: auth, exam, phong_thi, thi_sinh, admin, ngan_hang
templates/        # Giao diện Jinja2 (kèm templates/errors/ cho 404/500)
static/           # CSS/JS (gồm flash.css + flash.js cho thông báo toast)
```
