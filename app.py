import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, jsonify # type: ignore
from flask_wtf.csrf import CSRFError # type: ignore
from werkzeug.middleware.proxy_fix import ProxyFix # type: ignore
from config import config
from extensions import csrf, limiter, oauth, socketio
from routes.realtime import register_socketio
from routes.auth      import auth_bp
from routes.exam      import exam_bp
from routes.phong_thi import phong_thi_bp
from routes.thi_sinh  import thi_sinh_bp
from routes.admin     import admin_bp
from routes.ngan_hang import ngan_hang_bp
from routes.lop       import lop_bp


def create_app():
    app = Flask(__name__)

    # Nạp config theo môi trường (development/production/testing)
    env = os.getenv('FLASK_ENV', 'development')
    app.config.from_object(config.get(env, config['default']))

    # Production bắt buộc đặt SECRET_KEY cố định trong .env
    if env == 'production' and not os.getenv('SECRET_KEY'):
        raise RuntimeError(
            "Thiếu SECRET_KEY trong .env! Production bắt buộc phải đặt "
            "một SECRET_KEY cố định và bí mật."
        )

    # Chạy sau reverse proxy (nginx): tin các header X-Forwarded-* của nó.
    # KHÔNG có bước này thì:
    #   - url_for(_external=True) sinh http:// -> Google OAuth báo redirect_uri_mismatch
    #   - rate limit thấy mọi người dùng đều là IP của nginx (127.0.0.1) -> chung hạn mức
    # Chỉ bật ở production (dev chạy trực tiếp, không có proxy nào phía trước).
    if env == 'production':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs('logs', exist_ok=True)

    _setup_logging(app)
    _register_extensions(app)
    _register_blueprints(app)
    _register_session_guard(app)
    _register_security_headers(app)
    _register_error_handlers(app)
    _register_health(app)

    return app


def _register_session_guard(app):
    """
    Đồng bộ QUYỀN và TÌNH TRẠNG tài khoản theo thời gian thực.

    Trước đây vai trò (role) và tình trạng (khóa/xóa) chỉ được nạp vào session
    LÚC ĐĂNG NHẬP. Vì vậy khi admin đổi vai trò / khóa / xóa một tài khoản đang
    đăng nhập, thao tác đó chỉ thực sự có hiệu lực SAU KHI người đó tự đăng xuất.

    Hook này kiểm tra lại DB ở mỗi request của người đã đăng nhập để thao tác
    của admin có hiệu lực NGAY ở request kế tiếp, đồng thời BÁO cho người dùng:
      - Tài khoản bị XÓA  -> đăng xuất + thông báo.
      - Tài khoản bị KHÓA -> đăng xuất + thông báo.
      - Vai trò THAY ĐỔI  -> cập nhật session + thông báo.
    """
    from flask import session, redirect, url_for, flash # type: ignore
    from database import get_db_connection

    # Tài nguyên tĩnh và route đăng xuất: bỏ qua để khỏi tốn truy vấn và
    # tránh vòng lặp chuyển hướng.
    BO_QUA = {'static', 'auth.logout'}

    VAI_TRO_LABEL = {'admin': 'Quản trị viên',
                     'giao_vien': 'Giáo viên',
                     'hoc_sinh': 'Học sinh'}

    @app.before_request
    def dong_bo_tai_khoan():
        if 'user_id' not in session or request.endpoint in BO_QUA:
            return

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT role, trang_thai FROM nguoi_dung WHERE id=%s",
                (session['user_id'],)
            )
            user = cursor.fetchone()
            cursor.close()
            conn.close()
        except Exception:
            # DB trục trặc tạm thời -> không chặn người dùng, bỏ qua lần này.
            return

        # 1) Tài khoản đã bị XÓA khỏi hệ thống
        if not user:
            session.clear()
            flash('Tài khoản của bạn đã bị quản trị viên xóa khỏi hệ thống.',
                  'danger')
            return redirect(url_for('auth.login'))

        # 2) Tài khoản bị KHÓA
        if user['trang_thai'] == 'locked':
            session.clear()
            flash('Tài khoản của bạn đã bị quản trị viên khóa. '
                  'Vui lòng liên hệ quản trị viên để được hỗ trợ.', 'danger')
            return redirect(url_for('auth.login'))

        # 3) Vai trò bị THAY ĐỔI -> đồng bộ session + đưa về trang chủ ứng với
        #    quyền mới và báo cho người dùng.
        if user['role'] != session.get('role'):
            nhan = VAI_TRO_LABEL.get(user['role'], user['role'])
            session['role'] = user['role']
            flash(f'Quyền tài khoản của bạn vừa được quản trị viên thay đổi '
                  f'thành "{nhan}".', 'info')
            return redirect(url_for('exam.index'))


def _register_health(app):
    """Endpoint nhẹ cho uptime monitor / load balancer kiểm tra sống-chết."""
    from database import get_db_connection

    @app.route('/health')
    def health():
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            conn.close()
            return {'status': 'ok', 'db': 'up'}, 200
        except Exception:
            app.logger.exception('Health check: DB không kết nối được')
            return {'status': 'degraded', 'db': 'down'}, 503


class _RequestContextFilter(logging.Filter):
    """Chèn địa chỉ IP của request vào log (an toàn cả khi ngoài request)."""
    def filter(self, record):
        try:
            record.remote_addr = request.remote_addr or '-'
        except Exception:
            record.remote_addr = '-'
        return True


def _setup_logging(app):
    """Ghi log ra file xoay vòng (rotating) để truy vết sự cố ở production."""
    if app.debug or app.testing:
        return
    handler = RotatingFileHandler(
        os.path.join('logs', 'app.log'),
        maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    handler.addFilter(_RequestContextFilter())
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s [%(remote_addr)s] %(message)s '
        '(%(module)s:%(lineno)d)'
    ))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Khởi động ứng dụng')


def _register_extensions(app):
    csrf.init_app(app)
    limiter.init_app(app)
    oauth.init_app(app)
    socketio.init_app(app)
    register_socketio(socketio)
    oauth.register(
        name='google',
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )


def _register_blueprints(app):
    for bp in (auth_bp, exam_bp, phong_thi_bp,
               thi_sinh_bp, admin_bp, ngan_hang_bp, lop_bp):
        app.register_blueprint(bp)


def _register_security_headers(app):
    """Thêm các HTTP header bảo mật cơ bản vào mọi phản hồi."""
    @app.after_request
    def set_secure_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        resp.headers.setdefault(
            'Permissions-Policy',
            'camera=(self), microphone=(), geolocation=()'
        )
        return resp


def _wants_json():
    """Đoán xem client đang gọi API (AJAX) hay mở trang web bình thường."""
    best = request.accept_mimetypes.best_match(['application/json', 'text/html'])
    return (best == 'application/json'
            and request.accept_mimetypes[best]
            >= request.accept_mimetypes['text/html']) or request.is_json


def _register_error_handlers(app):
    @app.errorhandler(CSRFError)
    def handle_csrf(e):
        if _wants_json():
            return jsonify(error='Phiên làm việc đã hết hạn, vui lòng tải lại trang.'), 400
        return render_template('errors/error.html',
                               code=400,
                               title='Yêu cầu không hợp lệ',
                               message='Phiên làm việc đã hết hạn hoặc biểu mẫu '
                                       'không hợp lệ. Vui lòng tải lại trang và thử lại.'), 400

    @app.errorhandler(403)
    def handle_403(e):
        return render_template('errors/error.html',
                               code=403,
                               title='Không có quyền truy cập',
                               message='Bạn không có quyền xem nội dung này.'), 403

    @app.errorhandler(404)
    def handle_404(e):
        if _wants_json():
            return jsonify(error='Không tìm thấy tài nguyên.'), 404
        return render_template('errors/error.html',
                               code=404,
                               title='Không tìm thấy trang',
                               message='Trang bạn tìm không tồn tại hoặc đã bị di chuyển.'), 404

    @app.errorhandler(413)
    def handle_413(e):
        return render_template('errors/error.html',
                               code=413,
                               title='File quá lớn',
                               message='File tải lên vượt quá dung lượng cho phép (tối đa 50MB).'), 413

    @app.errorhandler(429)
    def handle_429(e):
        return render_template('errors/error.html',
                               code=429,
                               title='Thao tác quá nhanh',
                               message='Bạn thao tác quá nhiều lần. Vui lòng chờ một lát rồi thử lại.'), 429

    @app.errorhandler(500)
    def handle_500(e):
        # Ghi log chi tiết cho dev, nhưng KHÔNG lộ ra người dùng.
        app.logger.exception('Lỗi hệ thống chưa bắt được')
        if _wants_json():
            return jsonify(error='Đã có lỗi xảy ra, vui lòng thử lại sau.'), 500
        return render_template('errors/error.html',
                               code=500,
                               title='Lỗi hệ thống',
                               message='Đã có lỗi xảy ra phía máy chủ. Chúng tôi đã ghi nhận '
                                       'và sẽ khắc phục sớm. Vui lòng thử lại sau.'), 500


app = create_app()


if __name__ == '__main__':
    # Chạy qua socketio.run để bật kênh realtime (giám sát video trực tiếp).
    # allow_unsafe_werkzeug=True cho phép dùng server dev của Werkzeug ở chế độ này.
    socketio.run(app, debug=app.config['DEBUG'], host='0.0.0.0', port=5000,
                 allow_unsafe_werkzeug=True)
