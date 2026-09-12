"""
Điểm vào cho máy chủ WSGI ở PRODUCTION (Linux/VPS).

    gunicorn --workers 1 --threads 8 --bind 127.0.0.1:5000 wsgi:app

⚠️ BẮT BUỘC --workers 1 (một tiến trình duy nhất), vì:
  • Socket.IO (giám sát webcam) giữ danh sách phòng/kết nối trong RAM tiến trình.
    Nhiều worker -> giáo viên nối vào worker A, học sinh gửi hình vào worker B
    -> màn hình giám sát trống trơn.
  • Flask-Limiter đang đếm bằng "memory://" -> mỗi worker đếm riêng, hạn mức sai.
Cần nhiều tiến trình thì phải cài Redis rồi đặt RATELIMIT_STORAGE_URI trong .env
và dùng message queue cho Socket.IO.

Dùng --threads (không phải --workers) để phục vụ nhiều người cùng lúc: khớp với
async_mode='threading' của Socket.IO.
"""
import os

os.environ.setdefault('FLASK_ENV', 'production')

from app import app   # noqa: E402  (phải đặt sau khi set FLASK_ENV)

if __name__ == '__main__':
    app.run()
