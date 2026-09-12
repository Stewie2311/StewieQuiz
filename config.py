"""
Cấu hình ứng dụng, chia theo môi trường (development / production / testing).

Mọi giá trị nhạy cảm đều đọc từ .env, không viết thẳng vào đây — file này nằm
trong git, .env thì không.

Chọn môi trường bằng biến FLASK_ENV; app.py đọc dict `config` ở cuối file.
"""
import os
import secrets
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Cấu hình nền (base) — các môi trường khác kế thừa rồi ghi đè."""
    # Nếu .env không đặt SECRET_KEY thì sinh ngẫu nhiên (an toàn cho dev,
    # nhưng session sẽ mất khi restart — production BẮT BUỘC đặt trong .env).
    SECRET_KEY = os.getenv('SECRET_KEY') or secrets.token_hex(32)

    # Ép về bool đúng cách: chuỗi 'false'/'0'/'' đều coi là False
    DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

    UPLOAD_FOLDER = 'uploads'
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_FILE_SIZE', 52428800))  # 50MB

    # ----- Bảo mật session/cookie -----
    SESSION_COOKIE_HTTPONLY = True            # JS không đọc được cookie -> chống XSS đánh cắp phiên
    SESSION_COOKIE_SAMESITE = 'Lax'           # chống CSRF cơ bản qua cookie
    SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1', 'yes')
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # ----- CSRF -----
    # Không giới hạn thời gian token theo đồng hồ (gắn với phiên) để bài thi
    # kéo dài > 1 giờ vẫn nộp được; token vẫn đổi mỗi phiên đăng nhập.
    WTF_CSRF_TIME_LIMIT = None

    # Database
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_NAME = os.getenv('DB_NAME', 'taocauhoiai')

    # AI API
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

    # Upload
    ALLOWED_EXTENSIONS = {'pdf'}


class DevelopmentConfig(Config):
    """Môi trường phát triển."""
    DEBUG = True


class ProductionConfig(Config):
    """Môi trường chạy thật."""
    DEBUG = False
    SESSION_COOKIE_SECURE = True   # production chạy HTTPS -> chỉ gửi cookie qua HTTPS


class TestingConfig(Config):
    """Môi trường kiểm thử."""
    TESTING = True
    DB_NAME = 'test_taocauhoiai'
    WTF_CSRF_ENABLED = False


# Chọn cấu hình theo biến môi trường FLASK_ENV
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}
