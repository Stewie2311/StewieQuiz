"""
Khởi tạo các extension dùng chung. Tách riêng để tránh import vòng
(circular import) giữa app.py và các blueprint.
"""
import os

from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from authlib.integrations.flask_client import OAuth
from flask_socketio import SocketIO

# Chống tấn công giả mạo yêu cầu (CSRF) trên mọi form POST/PUT/DELETE.
csrf = CSRFProtect()

# Giới hạn tần suất request theo IP (chống brute-force đăng nhập, spam OTP).
# LƯU Ý: "memory://" chỉ đúng khi chạy MỘT tiến trình (1 worker). Nếu sau này
# chạy nhiều worker, đặt RATELIMIT_STORAGE_URI=redis://localhost:6379 trong .env.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
)

# OAuth client (Google Sign-In)
oauth = OAuth()

# Realtime (giám sát webcam trực tiếp). Dùng async_mode='threading' để KHÔNG
# phải monkey-patch eventlet (tránh xung đột với mysql-connector trên Windows);
# vẫn chạy được dưới Werkzeug (dev) lẫn waitress/gunicorn (production), và dùng
# WebSocket khi có simple-websocket.
# CORS: production chỉ cho phép chính tên miền của mình (đặt CORS_ORIGINS trong
# .env, vd "https://stewiequiz.io.vn"); để trống -> '*' cho tiện lúc dev.
_cors = [o.strip() for o in os.getenv('CORS_ORIGINS', '').split(',') if o.strip()]
socketio = SocketIO(async_mode='threading',
                    cors_allowed_origins=_cors or '*')
