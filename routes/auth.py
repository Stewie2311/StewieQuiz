"""
TÀI KHOẢN: đăng ký, đăng nhập, hồ sơ cá nhân.

Bố cục file (theo thứ tự từ trên xuống):
  1. Hàm kiểm tra dữ liệu  — mật khẩu, tên đăng nhập, số điện thoại, ảnh.
  2. Đăng ký + xác minh    — điền form -> gửi mã OTP về email -> nhập đúng mã
                             thì tài khoản mới thực sự được tạo.
  3. Đăng nhập / đăng xuất — bằng mật khẩu, hoặc bằng tài khoản Google.
  4. Quên mật khẩu         — cũng qua mã OTP gửi email.
  5. Hồ sơ cá nhân         — sửa thông tin, đổi mật khẩu, đổi ảnh đại diện.

BA ĐIỂM CẦN NHỚ:
  - Tài khoản chờ xác minh KHÔNG nằm trong cơ sở dữ liệu. Nó nằm tạm trong
    session (khóa 'dang_ky_cho') cho tới khi nhập đúng OTP. Nhờ vậy email giả
    hoặc bỏ ngang giữa chừng không để lại rác trong bảng nguoi_dung.
  - Vai trò 'giao_vien' phải có MÃ MỜI (biến GIAO_VIEN_CODE trong .env), nếu
    không ai đăng ký cũng tự phong mình làm giáo viên và mở phòng thi được.
  - Mật khẩu mạnh chỉ áp cho tài khoản MỚI và khi đổi mật khẩu, KHÔNG áp lúc
    đăng nhập — nếu không, người dùng cũ có mật khẩu yếu sẽ bị khóa ngoài.
"""
import os
import re
import time
import uuid
import random
from flask import (Blueprint, render_template, request, # type: ignore
                   redirect, session, flash, url_for, current_app)
from werkzeug.security import generate_password_hash, check_password_hash # type: ignore
from database import get_db_connection
from extensions import limiter, oauth, csrf
import email_utils

auth_bp = Blueprint('auth', __name__)

# Mã mời giáo viên, đặt trong .env. Để trống = KHÔNG AI đăng ký được vai trò
# giáo viên (chặn hoàn toàn), chứ không phải "ai cũng được" — xem hàm register.
GIAO_VIEN_CODE = os.getenv('GIAO_VIEN_CODE', '')

OTP_HAN_DUNG = 10 * 60  # mã xác minh email sống 10 phút

VAI_TRO_LABEL = {
    'admin'    : 'Quản trị viên',
    'giao_vien': 'Giáo viên',
    'hoc_sinh' : 'Học sinh',
}


# ============== KIỂM TRA DỮ LIỆU NGƯỜI DÙNG NHẬP ==============
# Quy ước chung: trả về None khi hợp lệ, hoặc CHUỖI mô tả lỗi để đem đi flash.

def _sdt_hop_le(sdt):
    """Số điện thoại Việt Nam: đúng 10 chữ số và bắt đầu bằng số 0."""
    return bool(re.fullmatch(r'0\d{9}', (sdt or '').strip()))


def _kiem_tra_mat_khau(mk):
    """Yêu cầu mật khẩu mạnh: >= 8 ký tự, có hoa, thường, số và ký tự đặc biệt.

    Chỉ gọi khi TẠO mật khẩu (đăng ký, đổi, đặt lại) — tuyệt đối không gọi ở
    hàm login, nếu không mọi tài khoản cũ có mật khẩu yếu sẽ không vào được nữa.
    """
    mk = mk or ''
    if len(mk) < 8:
        return 'Mật khẩu phải có ít nhất 8 ký tự.'
    if not re.search(r'[A-Z]', mk):
        return 'Mật khẩu phải có ít nhất 1 chữ IN HOA (A-Z).'
    if not re.search(r'[a-z]', mk):
        return 'Mật khẩu phải có ít nhất 1 chữ thường (a-z).'
    if not re.search(r'\d', mk):
        return 'Mật khẩu phải có ít nhất 1 chữ số (0-9).'
    if not re.search(r'[^A-Za-z0-9\s]', mk):
        return 'Mật khẩu phải có ít nhất 1 ký tự đặc biệt (vd: ! @ # $ %).'
    return None


def _kiem_tra_username(u):
    """Tên đăng nhập: 4–30 ký tự, chỉ chữ/số/gạch dưới, phải có ít nhất 1 chữ cái."""
    u = u or ''
    if not re.fullmatch(r'[A-Za-z0-9_]{4,30}', u):
        return ('Tên đăng nhập phải dài 4–30 ký tự và chỉ gồm chữ cái, số, '
                'dấu gạch dưới (_) — không dấu, không khoảng trắng.')
    if not re.search(r'[A-Za-z]', u):
        return 'Tên đăng nhập phải có ít nhất một chữ cái.'
    return None


AVATAR_MAX_MB  = 5


def _avatar_dir():
    """Thư mục chứa ảnh đại diện, dưới dạng đường dẫn TUYỆT ĐỐI.

    Trước đây hằng số này là đường dẫn TƯƠNG ĐỐI ('static/avatars'). Python tính
    đường dẫn tương đối từ THƯ MỤC LÀM VIỆC của tiến trình (CWD), không phải từ
    vị trí file mã nguồn. Chạy dev bằng `python app.py` thì CWD đúng là thư mục
    dự án nên không sao; nhưng trên VPS, gunicorn/systemd thường có CWD là '/',
    nên nó đi ghi vào '/static/avatars' -> không có quyền -> PermissionError ->
    lỗi 500 khi người dùng đổi ảnh.

    Neo vào `current_app.root_path` (thư mục chứa app.py) thì CWD là gì cũng kệ.
    """
    return os.path.join(current_app.root_path, 'static', 'avatars')


def _loai_anh(head):
    """Đoán định dạng ảnh từ vài byte đầu file; None nếu không phải ảnh.

    Nhìn "chữ ký" nhị phân chứ không nhìn đuôi tên: kẻ xấu chỉ cần đổi tên
    shell.php thành shell.png là qua được nếu ta tin vào phần mở rộng.
    """
    if head[:4] == b'\x89PNG':                       return 'png'
    if head[:3] == b'\xff\xd8\xff':                  return 'jpg'
    if head[:6] in (b'GIF87a', b'GIF89a'):           return 'gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP': return 'webp'
    return None

# ============== ĐĂNG KÝ (form -> OTP email -> tạo tài khoản) ==============

def _luu_tai_khoan(tt):
    """Ghi tài khoản đã xác minh vào DB. Trả về (True, None) hoặc (False, lỗi).

    Vẫn phải kiểm tra trùng username/email lần nữa ở đây, dù register đã kiểm
    rồi: giữa lúc người dùng đi mở hộp thư tìm mã OTP, một người khác hoàn toàn
    có thể đăng ký chen ngay chính email/tên đó.
    """
    conn   = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id FROM nguoi_dung WHERE username=%s OR email=%s",
            (tt['username'], tt['email'])
        )
        if cursor.fetchone():
            return False, 'Tên đăng nhập hoặc Email đã được sử dụng!'

        cursor.execute(
            """INSERT INTO nguoi_dung
               (ho_ten, ngay_sinh, email, sdt, ma_so_sv, lop,
                username, password, role)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tt['ho_ten'], tt['ngay_sinh'], tt['email'], tt['sdt'],
             tt['ma_so_sv'], tt['lop'], tt['username'],
             tt['password'], tt['role'])
        )
        conn.commit()
        return True, None
    finally:
        cursor.close()
        conn.close()


@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("8 per minute", methods=['POST'])
def register():
    """Nhận form đăng ký, kiểm tra mọi thứ, rồi gửi mã OTP thay vì tạo tài khoản ngay.

    Tài khoản chỉ ra đời ở bước `xac_minh_email`. Ở đây thông tin (kèm mật khẩu
    ĐÃ băm) chỉ nằm tạm trong session.

    Ngoại lệ: nếu .env chưa cấu hình email thì bỏ qua bước OTP và tạo tài khoản
    luôn — để chạy dev trên máy không có SMTP vẫn đăng ký được.
    """
    if request.method == 'POST':
        email     = request.form['email'].strip()
        username  = request.form['username']
        password  = request.form['password']

        # Form đăng ký cố tình rút gọn, không hỏi họ tên. Nhưng cột ho_ten là
        # NOT NULL, nên tạm lấy username điền vào; người dùng sửa lại ở trang
        # hồ sơ sau.
        ho_ten    = request.form.get('ho_ten', '').strip() or username
        ngay_sinh = request.form.get('ngay_sinh', '').strip() or None
        sdt       = request.form.get('sdt', '').strip() or None
        ma_so_sv  = request.form.get('ma_so_sv', '').strip() or None
        lop       = request.form.get('lop', '').strip() or None

        loi = _kiem_tra_username(username)
        if loi:
            flash(loi, 'danger')
            return render_template('auth/register.html')
        loi = _kiem_tra_mat_khau(password)
        if loi:
            flash(loi, 'danger')
            return render_template('auth/register.html')

        # Vai trò mặc định luôn là học sinh. Muốn thành giáo viên phải nhập đúng
        # mã mời — không có bước này thì học sinh tự phong mình làm giáo viên,
        # rồi tự mở phòng thi và xem đáp án.
        role_chon = request.form.get('role', 'hoc_sinh')
        ma_gv     = request.form.get('ma_giao_vien', '').strip()

        if role_chon == 'giao_vien':
            if not GIAO_VIEN_CODE or ma_gv != GIAO_VIEN_CODE:
                flash('Mã mời giáo viên không đúng! '
                      'Liên hệ quản trị viên để được cấp mã.', 'danger')
                return render_template('auth/register.html')
            role = 'giao_vien'
        else:
            role = 'hoc_sinh'

        if sdt and not _sdt_hop_le(sdt):
            flash('Số điện thoại không hợp lệ! Phải gồm 10 chữ số và bắt đầu bằng 0.', 'danger')
            return render_template('auth/register.html')

        # Bắt trùng username/email TỪ BÂY GIỜ. Để tới sau khi xác minh OTP mới
        # báo thì người dùng đã mất công đi lấy mã trong hộp thư, quay lại chỉ
        # để nghe "tên này có người dùng rồi" — rất khó chịu.
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM nguoi_dung WHERE username=%s OR email=%s",
            (username, email)
        )
        da_ton_tai = cursor.fetchone()
        cursor.close()
        conn.close()
        if da_ton_tai:
            flash('Tên đăng nhập hoặc Email đã được sử dụng!', 'danger')
            return render_template('auth/register.html')

        # Băm mật khẩu NGAY, trước khi cất vào session — không để mật khẩu thô
        # nằm trong cookie phiên dù chỉ vài phút chờ OTP.
        thong_tin = {
            'ho_ten': ho_ten, 'ngay_sinh': ngay_sinh, 'email': email,
            'sdt': sdt, 'ma_so_sv': ma_so_sv, 'lop': lop,
            'username': username, 'role': role,
            'password': generate_password_hash(password),
        }

        if not email_utils.cau_hinh_email_ok():
            ok, loi = _luu_tai_khoan(thong_tin)
            if ok:
                flash('Đăng ký thành công! Vui lòng đăng nhập.', 'success')
                return redirect(url_for('auth.login'))
            flash(loi, 'danger')
            return render_template('auth/register.html')

        ma_otp = f'{random.randint(0, 999999):06d}'
        try:
            email_utils.gui_ma_xac_minh(email, ma_otp, ho_ten)
        except Exception as e:
            flash(f'Không gửi được mã xác minh tới email này. '
                  f'Kiểm tra lại địa chỉ email. ({e})', 'danger')
            return render_template('auth/register.html')

        session['dang_ky_cho'] = {
            'thong_tin': thong_tin,
            'ma_otp': ma_otp,
            'het_han': time.time() + OTP_HAN_DUNG,
        }
        flash(f'Đã gửi mã xác minh tới {email}. '
              f'Vui lòng kiểm tra hộp thư (kể cả mục Spam).', 'info')
        return redirect(url_for('auth.xac_minh_email'))

    return render_template('auth/register.html')


@auth_bp.route('/register/xac-minh', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=['POST'])
def xac_minh_email():
    """Trang nhập mã OTP đã gửi về email để hoàn tất đăng ký."""
    cho = session.get('dang_ky_cho')
    if not cho:
        flash('Phiên đăng ký đã hết hạn. Vui lòng đăng ký lại.', 'warning')
        return redirect(url_for('auth.register'))

    email = cho['thong_tin']['email']

    if request.method == 'POST':
        ma_nhap = request.form.get('ma_otp', '').strip()

        if time.time() > cho['het_han']:
            session.pop('dang_ky_cho', None)
            flash('Mã xác minh đã hết hạn. Vui lòng đăng ký lại.', 'warning')
            return redirect(url_for('auth.register'))

        if ma_nhap != cho['ma_otp']:
            flash('Mã xác minh không đúng. Vui lòng thử lại.', 'danger')
            return render_template('auth/xac_minh_email.html', email=email)

        # Đúng mã: tới đây tài khoản mới thật sự được ghi vào DB.
        ok, loi = _luu_tai_khoan(cho['thong_tin'])
        session.pop('dang_ky_cho', None)
        if ok:
            flash('Xác minh thành công! Tài khoản đã được tạo, '
                  'vui lòng đăng nhập.', 'success')
            return redirect(url_for('auth.login'))
        flash(loi, 'danger')
        return redirect(url_for('auth.register'))

    return render_template('auth/xac_minh_email.html', email=email)


@auth_bp.route('/register/gui-lai', methods=['POST'])
@limiter.limit("3 per minute")
def gui_lai_ma():
    """Gửi lại mã OTP mới cho phiên đăng ký đang chờ."""
    cho = session.get('dang_ky_cho')
    if not cho:
        flash('Phiên đăng ký đã hết hạn. Vui lòng đăng ký lại.', 'warning')
        return redirect(url_for('auth.register'))

    email  = cho['thong_tin']['email']
    ho_ten = cho['thong_tin']['ho_ten']
    ma_otp = f'{random.randint(0, 999999):06d}'
    try:
        email_utils.gui_ma_xac_minh(email, ma_otp, ho_ten)
    except Exception as e:
        flash(f'Không gửi lại được mã xác minh. ({e})', 'danger')
        return redirect(url_for('auth.xac_minh_email'))

    cho['ma_otp']  = ma_otp
    cho['het_han'] = time.time() + OTP_HAN_DUNG
    session['dang_ky_cho'] = cho
    flash(f'Đã gửi lại mã xác minh mới tới {email}.', 'info')
    return redirect(url_for('auth.xac_minh_email'))


# ============== ĐĂNG NHẬP / ĐĂNG XUẤT ==============

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=['POST'])
def login():
    """Đăng nhập bằng tên đăng nhập + mật khẩu.

    Giới hạn 10 lần/phút để chặn dò mật khẩu tự động. Thông báo lỗi cố tình nói
    chung chung "sai tên đăng nhập hoặc mật khẩu", không tách riêng, để kẻ dò
    không biết được tên nào có thật.
    """
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn   = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM nguoi_dung WHERE username=%s", (username,)
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and check_password_hash(user['password'], password):
            if user.get('trang_thai') == 'locked':
                flash('Tài khoản bị khóa! Liên hệ quản trị viên.', 'danger')
                return redirect(url_for('auth.login'))
            
            # Xóa sạch phiên cũ rồi mới cấp phiên mới. Chống session fixation:
            # kẻ xấu dụ nạn nhân dùng một session id hắn biết trước, chờ nạn
            # nhân đăng nhập, rồi dùng chính id đó để vào tài khoản họ.
            session.clear()
            session.permanent = True     # áp PERMANENT_SESSION_LIFETIME (8 giờ)

            session['user_id']  = user['id']
            session['username'] = user['username']
            session['ho_ten']   = user['ho_ten']
            session['email']    = user.get('email') or ''
            session['ma_so_sv'] = user.get('ma_so_sv') or ''
            session['lop']      = user.get('lop') or ''
            session['avatar']   = user.get('avatar') or ''

            # Rơi về vai trò thấp nhất nếu DB thiếu role — an toàn hơn là để
            # trống rồi bị các decorator hiểu nhầm.
            session['role']     = user.get('role', 'hoc_sinh')

            return redirect(url_for('exam.index'))

        flash('Sai tên đăng nhập hoặc mật khẩu!', 'danger')
    google_enabled = bool(os.getenv('GOOGLE_CLIENT_ID'))
    return render_template('auth/login.html', google_enabled=google_enabled)


@auth_bp.route('/logout')
def logout():
    """Đăng xuất: xóa toàn bộ phiên."""
    session.clear()
    return redirect(url_for('auth.login'))


# ==========================================
# ĐĂNG NHẬP BẰNG GOOGLE (OAuth 2.0 / OpenID Connect)
#
# Hai route đi thành cặp: `login_google` đẩy người dùng sang Google, Google xử
# lý xong thì gọi ngược về `login_google_callback`.
#
# Cả hai đều @csrf.exempt vì Google gửi request tới mà không hề biết token CSRF
# của ta. Đổi lại, bản thân giao thức OAuth đã có tham số `state` chống giả mạo.
#
# Email chưa được Google xác minh thì TỪ CHỐI — nếu không, người ta tự dựng một
# miền email, khai bừa địa chỉ của giáo viên, và chiếm tài khoản đó.
# ==========================================
@auth_bp.route('/login/google')
@csrf.exempt
def login_google():
    """Đẩy người dùng sang trang đồng ý của Google."""
    redirect_uri = url_for('auth.login_google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/login/google/callback')
@csrf.exempt
def login_google_callback():
    """Google gọi về đây sau khi người dùng bấm đồng ý.

    Khớp tài khoản theo EMAIL. Chưa có ai dùng email đó thì tự tạo tài khoản
    học sinh mới, nên người dùng không phải đăng ký thủ công lần nào.
    """
    try:
        token     = oauth.google.authorize_access_token()
        user_info = token.get('userinfo') or oauth.google.userinfo()
    except Exception as e:
        print(f'[Google OAuth ERROR] {type(e).__name__}: {e}', flush=True)
        flash('Đăng nhập Google thất bại. Vui lòng thử lại.', 'danger')
        return redirect(url_for('auth.login'))

    email    = (user_info.get('email') or '').strip().lower()
    verified = user_info.get('email_verified', False)
    # In ra email Google thực sự trả về. Lỗi hay gặp nhất khi hỗ trợ người dùng
    # là họ đang đăng nhập bằng một tài khoản Google khác tài khoản đã đăng ký
    # mà không hay biết; dòng log này chỉ ra ngay.
    print(f'[Google OAuth] email={email!r} verified={verified!r}', flush=True)

    if not email or not verified:
        flash('Tài khoản Google chưa xác minh email. Vui lòng dùng tài khoản khác.', 'warning')
        return redirect(url_for('auth.login'))

    # LOWER(TRIM(...)) cả hai vế: email trong DB có thể được nhập với chữ hoa
    # hoặc dính khoảng trắng, so thẳng là trượt và hệ thống lại tạo tài khoản
    # trùng lặp cho cùng một người.
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, ho_ten, username, role, trang_thai FROM nguoi_dung WHERE LOWER(TRIM(email))=%s",
        (email,)
    )
    user = cursor.fetchone()

    # Email lạ -> tự tạo tài khoản HỌC SINH. Không bao giờ tự cấp vai trò giáo
    # viên qua đường này: vai trò đó luôn phải qua mã mời.
    tai_khoan_moi = False
    if not user:
        ho_ten_gg = (user_info.get('name') or email.split('@')[0]).strip()[:100] or 'Người dùng'
        # Lấy phần trước dấu @ làm tên đăng nhập; nếu đụng hàng thì nối thêm
        # số (an, an2, an3...) cho tới khi tìm được tên còn trống.
        base = ''.join(c for c in email.split('@')[0].lower() if c.isalnum() or c == '_') or 'user'
        base = base[:40]
        username = base
        i = 1
        while True:
            cursor.execute("SELECT 1 FROM nguoi_dung WHERE username=%s", (username,))
            if not cursor.fetchone():
                break
            i += 1
            username = f'{base}{i}'
        # Cột password là NOT NULL nhưng người này sẽ luôn vào bằng Google, nên
        # đặt một chuỗi ngẫu nhiên không ai đoán được và cũng không ai cần biết.
        mat_khau_ao = generate_password_hash(os.urandom(24).hex())
        try:
            cursor.execute(
                "INSERT INTO nguoi_dung (username, password, ho_ten, email, role) "
                "VALUES (%s,%s,%s,%s,'hoc_sinh')",
                (username, mat_khau_ao, ho_ten_gg, email)
            )
            conn.commit()
            cursor.execute(
                "SELECT id, ho_ten, username, role, trang_thai FROM nguoi_dung WHERE id=%s",
                (cursor.lastrowid,)
            )
            user = cursor.fetchone()
            tai_khoan_moi = True
        except Exception as e:
            conn.rollback()
            print(f'[Google OAuth] Tạo tài khoản tự động lỗi: {e}', flush=True)
            # Nhiều khả năng người dùng bấm đăng nhập hai lần, request kia đã
            # tạo xong tài khoản và ta vấp UNIQUE. Đọc lại theo email là có.
            cursor.execute(
                "SELECT id, ho_ten, username, role, trang_thai FROM nguoi_dung WHERE LOWER(TRIM(email))=%s",
                (email,)
            )
            user = cursor.fetchone()

    cursor.close()
    conn.close()

    if not user:
        flash('Không tạo được tài khoản từ Google. Vui lòng thử lại hoặc đăng ký thủ công.', 'danger')
        return redirect(url_for('auth.login'))

    if user['trang_thai'] == 'locked':
        flash('Tài khoản của bạn đã bị khóa. Vui lòng liên hệ quản trị viên.', 'danger')
        return redirect(url_for('auth.login'))

    # Từ đây giống hệt đăng nhập thường: phiên mới tinh, rồi nạp thông tin.
    session.clear()
    session.permanent = True
    session['user_id']  = user['id']
    session['username'] = user['username']
    session['ho_ten']   = user['ho_ten']
    session['role']     = user['role']

    if tai_khoan_moi:
        flash(f'Đã tạo tài khoản mới từ Google cho {user["ho_ten"]} (vai trò Học sinh). '
              'Chào mừng bạn!', 'success')
    else:
        flash(f'Chào mừng {user["ho_ten"]}! Đăng nhập Google thành công.', 'success')
    return redirect(url_for('exam.index'))


@auth_bp.route('/chinh-sach-bao-mat')
def chinh_sach_bao_mat():
    """Trang chính sách bảo mật — công khai, Google yêu cầu có khi duyệt OAuth."""
    return render_template('chinh_sach.html')


# ==========================================
# QUÊN MẬT KHẨU (nhập email -> nhận OTP -> đặt mật khẩu mới)
#
# Điểm tinh tế: hệ thống KHÔNG tiết lộ email nào có trong cơ sở dữ liệu. Dù
# email lạ hay quen, người dùng đều thấy đúng một thông báo và đều được đưa
# sang trang nhập mã. Với email lạ, ta vẫn tạo phiên chờ nhưng để ma_otp=None,
# nên không mã nào khớp được. Nếu báo thẳng "email không tồn tại", kẻ xấu chỉ
# việc thử hàng loạt địa chỉ để lọc ra danh sách người dùng thật.
# ==========================================
@auth_bp.route('/quen-mat-khau', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=['POST'])
def quen_mat_khau():
    """Nhận email, gửi mã đặt lại mật khẩu (nếu email đó có thật)."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip()

        if not email_utils.cau_hinh_email_ok():
            flash('Hệ thống chưa cấu hình email nên chưa thể đặt lại mật khẩu. '
                  'Vui lòng liên hệ quản trị viên.', 'warning')
            return redirect(url_for('auth.quen_mat_khau'))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, ho_ten FROM nguoi_dung WHERE email=%s", (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user:
            ma_otp = f'{random.randint(0, 999999):06d}'
            try:
                email_utils.gui_ma_dat_lai_mat_khau(email, ma_otp, user['ho_ten'])
            except Exception:
                current_app.logger.exception('Lỗi gửi mã đặt lại mật khẩu')
                flash('Không gửi được email. Vui lòng thử lại sau.', 'danger')
                return redirect(url_for('auth.quen_mat_khau'))
            session['reset_cho'] = {
                'user_id': user['id'], 'email': email,
                'ma_otp': ma_otp, 'het_han': time.time() + OTP_HAN_DUNG,
            }
        else:
            # Email không có thật: vẫn dựng phiên chờ y hệt, chỉ khác là không
            # có mã. Người ngoài nhìn vào không phân biệt được hai trường hợp.
            session['reset_cho'] = {
                'user_id': None, 'email': email,
                'ma_otp': None, 'het_han': time.time() + OTP_HAN_DUNG,
            }

        flash('Nếu email tồn tại trong hệ thống, chúng tôi đã gửi mã đặt lại mật khẩu. '
              'Vui lòng kiểm tra hộp thư (kể cả mục Spam).', 'info')
        return redirect(url_for('auth.dat_lai_mat_khau'))

    return render_template('auth/quen_mat_khau.html')


@auth_bp.route('/dat-lai-mat-khau', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=['POST'])
def dat_lai_mat_khau():
    """Nhập mã OTP + mật khẩu mới để hoàn tất việc đặt lại."""
    cho = session.get('reset_cho')
    if not cho:
        flash('Phiên đặt lại mật khẩu đã hết hạn. Vui lòng thử lại.', 'warning')
        return redirect(url_for('auth.quen_mat_khau'))

    email = cho['email']

    if request.method == 'POST':
        ma_nhap = request.form.get('ma_otp', '').strip()
        mk_moi  = request.form.get('mat_khau_moi', '')
        mk_xn   = request.form.get('xac_nhan', '')

        if time.time() > cho['het_han']:
            session.pop('reset_cho', None)
            flash('Mã đã hết hạn. Vui lòng yêu cầu lại.', 'warning')
            return redirect(url_for('auth.quen_mat_khau'))
        loi_mk = _kiem_tra_mat_khau(mk_moi)
        if loi_mk:
            flash(loi_mk, 'warning')
            return render_template('auth/dat_lai_mat_khau.html', email=email)
        if mk_moi != mk_xn:
            flash('Xác nhận mật khẩu không khớp!', 'warning')
            return render_template('auth/dat_lai_mat_khau.html', email=email)
        # Gộp hai trường hợp vào chung một câu trả lời: gõ sai mã, và email vốn
        # không tồn tại (ma_otp là None). Người dùng thật và kẻ dò đều chỉ nhận
        # được đúng dòng "Mã xác minh không đúng".
        if not cho['ma_otp'] or ma_nhap != cho['ma_otp']:
            flash('Mã xác minh không đúng. Vui lòng thử lại.', 'danger')
            return render_template('auth/dat_lai_mat_khau.html', email=email)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE nguoi_dung SET password=%s WHERE id=%s",
                           (generate_password_hash(mk_moi), cho['user_id']))
            conn.commit()
            session.pop('reset_cho', None)
            flash('Đặt lại mật khẩu thành công! Vui lòng đăng nhập.', 'success')
            return redirect(url_for('auth.login'))
        except Exception:
            conn.rollback()
            current_app.logger.exception('Lỗi khi đặt lại mật khẩu')
            flash('Có lỗi xảy ra. Vui lòng thử lại.', 'danger')
            return render_template('auth/dat_lai_mat_khau.html', email=email)
        finally:
            cursor.close()
            conn.close()

    return render_template('auth/dat_lai_mat_khau.html', email=email)

# ============== HỒ SƠ CÁ NHÂN (xem, sửa, đổi mật khẩu, đổi ảnh) ==============

@auth_bp.route('/profile')
def profile():
    """Trang hồ sơ. Khối thống kê hiển thị khác nhau tùy vai trò.

    Giáo viên/Admin: số đề đã tạo, số phòng đã mở.
    Học sinh: số lần đã đi thi — đếm trong bảng thi_sinh theo email hoặc MSSV,
    vì lúc vào phòng thi học sinh khai hai thứ đó chứ không mang theo user_id.
    """
    if 'user_id' not in session:
        flash('Vui lòng đăng nhập!', 'warning')
        return redirect(url_for('auth.login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    stats = None
    try:
        cursor.execute(
            "SELECT ho_ten, email, sdt, ngay_sinh, ma_so_sv, lop, role, username, avatar "
            "FROM nguoi_dung WHERE id = %s", (session['user_id'],)
        )
        user_info = cursor.fetchone()
        if user_info:
            session['avatar'] = user_info.get('avatar') or ''

        if user_info and user_info['role'] in ('giao_vien', 'admin'):
            cursor.execute("SELECT COUNT(*) AS c FROM de_thi WHERE user_id=%s",
                           (session['user_id'],))
            so_de = cursor.fetchone()['c']
            cursor.execute("SELECT COUNT(*) AS c FROM phong_thi WHERE user_id=%s",
                           (session['user_id'],))
            so_phong = cursor.fetchone()['c']
            stats = {'so_de': so_de, 'so_phong': so_phong}
        elif user_info:
            email = (user_info.get('email') or '').strip()
            mssv  = (user_info.get('ma_so_sv') or '').strip()
            # Vế `AND %s <> ''` canh chừng trường hợp hồ sơ bỏ trống email hoặc
            # MSSV: thiếu nó, điều kiện thành `email = ''` và sẽ đếm luôn cả các
            # thí sinh khác cũng bỏ trống ô đó.
            cursor.execute("""
                SELECT COUNT(*) AS c FROM thi_sinh
                WHERE (email = %s AND %s <> '')
                   OR (ma_so_sv = %s AND %s <> '')
            """, (email, email, mssv, mssv))
            stats = {'so_lan_thi': cursor.fetchone()['c']}
    except Exception as e:
        flash(f'Lỗi: {e}', 'danger')
        user_info = None
    finally:
        cursor.close()
        conn.close()

    vai_tro = VAI_TRO_LABEL.get(user_info['role'], user_info['role']) if user_info else ''
    return render_template('profile.html', user=user_info,
                           vai_tro=vai_tro, stats=stats)


@auth_bp.route('/profile/cap_nhat', methods=['POST'])
def cap_nhat_ho_so():
    """Cập nhật thông tin cá nhân.

    Email KHÔNG nằm trong câu UPDATE, dù form có gửi lên đi nữa. Email đã được
    xác minh bằng OTP lúc đăng ký và là thứ dùng để khớp bài thi cũng như khôi
    phục mật khẩu; cho sửa tự do là mở đường chiếm tài khoản người khác.
    """
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    ho_ten    = request.form.get('ho_ten', '').strip()
    sdt       = request.form.get('sdt', '').strip()
    ngay_sinh = request.form.get('ngay_sinh') or None
    # Chỉ form của học sinh mới có 2 ô này; giáo viên gửi lên rỗng -> thành None.
    ma_so_sv  = request.form.get('ma_so_sv', '').strip() or None
    lop       = request.form.get('lop', '').strip() or None

    if not ho_ten:
        flash('Họ tên không được để trống!', 'danger')
        return redirect(url_for('auth.profile'))
    if sdt and not _sdt_hop_le(sdt):
        flash('Số điện thoại không hợp lệ! Phải gồm 10 chữ số và bắt đầu bằng 0.', 'warning')
        return redirect(url_for('auth.profile'))

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "UPDATE nguoi_dung SET ho_ten=%s, sdt=%s, ngay_sinh=%s, "
            "ma_so_sv=%s, lop=%s WHERE id=%s",
            (ho_ten, sdt, ngay_sinh, ma_so_sv, lop, session['user_id'])
        )
        conn.commit()
        # Cập nhật session ngay, vì luồng vào phòng thi tự điền MSSV/lớp lấy từ
        # đây. Quên bước này thì người dùng vừa sửa MSSV xong, vào thi vẫn bị
        # điền mã cũ cho tới lần đăng nhập kế tiếp.
        session['ho_ten']   = ho_ten
        session['ma_so_sv'] = ma_so_sv or ''
        session['lop']      = lop or ''
        flash('Đã cập nhật thông tin cá nhân!', 'success')
    except Exception:
        conn.rollback()
        current_app.logger.exception('Lỗi khi cập nhật hồ sơ')
        flash('Có lỗi xảy ra khi cập nhật. Vui lòng thử lại.', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('auth.profile'))


@auth_bp.route('/profile/doi_mat_khau', methods=['POST'])
def doi_mat_khau():
    """Đổi mật khẩu; bắt nhập lại mật khẩu hiện tại để xác nhận đúng chủ tài khoản."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    mk_cu  = request.form.get('mat_khau_cu', '')
    mk_moi = request.form.get('mat_khau_moi', '')
    mk_xn  = request.form.get('xac_nhan', '')

    loi_mk = _kiem_tra_mat_khau(mk_moi)
    if loi_mk:
        flash(loi_mk, 'warning')
        return redirect(url_for('auth.profile'))
    if mk_moi != mk_xn:
        flash('Xác nhận mật khẩu không khớp!', 'warning')
        return redirect(url_for('auth.profile'))

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT password FROM nguoi_dung WHERE id=%s", (session['user_id'],))
        row = cursor.fetchone()
        if not row or not check_password_hash(row['password'], mk_cu):
            flash('Mật khẩu hiện tại không đúng!', 'danger')
            return redirect(url_for('auth.profile'))

        cursor.execute(
            "UPDATE nguoi_dung SET password=%s WHERE id=%s",
            (generate_password_hash(mk_moi), session['user_id'])
        )
        conn.commit()
        flash('Đổi mật khẩu thành công!', 'success')
    except Exception:
        conn.rollback()
        current_app.logger.exception('Lỗi khi đổi mật khẩu')
        flash('Có lỗi xảy ra khi đổi mật khẩu. Vui lòng thử lại.', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('auth.profile'))


@auth_bp.route('/profile/avatar', methods=['POST'])
def cap_nhat_avatar():
    """Đổi ảnh đại diện.

    Tên file lưu xuống đĩa do server tự đặt (u<id>_<chuỗi ngẫu nhiên>.<đuôi>),
    KHÔNG lấy theo tên người dùng tải lên — tên gốc có thể chứa "../" để ghi đè
    file khác trong hệ thống.
    """
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    file = request.files.get('avatar')
    if not file or not file.filename:
        flash('Vui lòng chọn một ảnh.', 'warning')
        return redirect(url_for('auth.profile'))

    head = file.stream.read(12)
    file.stream.seek(0)
    loai = _loai_anh(head)
    if not loai:
        flash('Ảnh không hợp lệ! Chỉ chấp nhận PNG, JPG, GIF hoặc WEBP.', 'danger')
        return redirect(url_for('auth.profile'))

    # Ghi ảnh xuống đĩa. Bọc try: thư mục không tồn tại, đĩa đầy, hoặc tiến trình
    # web không có quyền ghi vào static/avatars đều ném OSError — để nó bay ra
    # ngoài thì người dùng nhận nguyên trang lỗi 500 mà không biết vì sao.
    thu_muc  = _avatar_dir()
    ten_file = f"u{session['user_id']}_{uuid.uuid4().hex[:8]}.{loai}"
    duong_dan = os.path.join(thu_muc, ten_file)
    try:
        os.makedirs(thu_muc, exist_ok=True)
        file.save(duong_dan)
    except OSError:
        current_app.logger.exception('Không ghi được ảnh đại diện vào %s', thu_muc)
        flash('Máy chủ không lưu được ảnh (thư mục ảnh không ghi được). '
              'Vui lòng báo quản trị viên.', 'danger')
        return redirect(url_for('auth.profile'))

    # Phải lưu xuống đĩa rồi mới đo được dung lượng thật, nên quá cỡ thì xóa
    # ngay file vừa ghi.
    if os.path.getsize(duong_dan) > AVATAR_MAX_MB * 1024 * 1024:
        try: os.remove(duong_dan)
        except OSError: pass
        flash(f'Ảnh quá lớn! Tối đa {AVATAR_MAX_MB}MB.', 'warning')
        return redirect(url_for('auth.profile'))

    rel = f"avatars/{ten_file}"   # đường dẫn tương đối bên trong /static
    # get_db_connection() phải nằm TRONG try: DB sập thì đây cũng ném lỗi, mà
    # trước đây nó đứng ngoài nên lại thành 500 lần nữa.
    conn = cursor = None
    try:
        conn   = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT avatar FROM nguoi_dung WHERE id=%s", (session['user_id'],))
        row = cursor.fetchone()
        anh_cu = row['avatar'] if row else None

        cursor.execute("UPDATE nguoi_dung SET avatar=%s WHERE id=%s",
                       (rel, session['user_id']))
        conn.commit()
        session['avatar'] = rel

        # Dọn ảnh cũ. Mỗi lần đổi avatar sinh một tên file mới, không xóa thì
        # thư mục static/avatars phình mãi bằng những ảnh không ai còn dùng.
        if anh_cu and anh_cu != rel:
            try:
                cu = os.path.join(current_app.root_path, 'static',
                                  anh_cu.replace('/', os.sep))
                if os.path.exists(cu):
                    os.remove(cu)
            except OSError:
                pass
        flash('Đã cập nhật ảnh đại diện!', 'success')
    except Exception:
        # conn/cursor có thể còn là None nếu chính get_db_connection() ném lỗi.
        # Gọi thẳng conn.rollback() lúc đó lại ném AttributeError NGAY TRONG khối
        # except -> lỗi gốc bị che mất và người dùng vẫn lãnh trang 500.
        if conn is not None:
            try: conn.rollback()
            except Exception: pass
        current_app.logger.exception('Lỗi khi cập nhật ảnh đại diện')
        try: os.remove(duong_dan)
        except OSError: pass
        flash('Có lỗi xảy ra khi lưu ảnh. Vui lòng thử lại.', 'danger')
    finally:
        if cursor is not None:
            try: cursor.close()
            except Exception: pass
        if conn is not None:
            try: conn.close()
            except Exception: pass
    return redirect(url_for('auth.profile'))