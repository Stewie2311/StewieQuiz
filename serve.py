"""
Chạy ứng dụng ở chế độ PRODUCTION bằng waitress (WSGI server thuần Python,
chạy tốt trên Windows). KHÔNG dùng app.run() / Werkzeug cho production.

    set FLASK_ENV=production
    python serve.py

Cấu hình qua biến môi trường: HOST, PORT, WEB_THREADS.
"""
import os
from waitress import serve
from app import app

if __name__ == '__main__':
    host    = os.getenv('HOST', '0.0.0.0')
    port    = int(os.getenv('PORT', 5000))
    threads = int(os.getenv('WEB_THREADS', 8))
    print(f" * Production server (waitress) chạy tại http://{host}:{port}")
    serve(app, host=host, port=port, threads=threads)
