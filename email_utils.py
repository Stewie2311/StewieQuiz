"""
Gửi email qua SMTP Gmail. Dùng để gửi mã xác minh (OTP) khi đăng ký.
Cần cấu hình EMAIL_ADDRESS và EMAIL_APP_PASSWORD trong .env
(App Password 16 ký tự, không phải mật khẩu Gmail thường).
"""
import os
import ssl
import smtplib
from email.message import EmailMessage

def _thong_tin_email():
    """Đọc cấu hình email từ biến môi trường tại thời điểm gọi."""
    return (os.getenv('EMAIL_ADDRESS', ''),
            os.getenv('EMAIL_APP_PASSWORD', ''))


def cau_hinh_email_ok():
    """True nếu đã khai báo đủ thông tin email trong .env."""
    addr, pw = _thong_tin_email()
    return bool(addr and pw)


def gui_ma_xac_minh(to_email, ma, ho_ten=''):
    """
    Gửi mã OTP tới to_email. Ném exception nếu gửi thất bại
    (caller tự bắt để báo lỗi cho người dùng).
    """
    email_address, app_password = _thong_tin_email()

    msg = EmailMessage()
    msg['Subject'] = 'Mã xác minh đăng ký - Stewie Quiz'
    msg['From']    = email_address
    msg['To']      = to_email

    ten = ho_ten or 'bạn'
    msg.set_content(
        f'Xin chào {ten},\n\n'
        f'Mã xác minh đăng ký tài khoản Stewie Quiz của bạn là: {ma}\n'
        f'Mã có hiệu lực trong 10 phút.\n\n'
        f'Nếu bạn không yêu cầu đăng ký, hãy bỏ qua email này.'
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
        server.login(email_address, app_password)
        server.send_message(msg)


def gui_ma_dat_lai_mat_khau(to_email, ma, ho_ten=''):
    """Gửi mã OTP để ĐẶT LẠI mật khẩu. Ném exception nếu gửi thất bại."""
    email_address, app_password = _thong_tin_email()

    msg = EmailMessage()
    msg['Subject'] = 'Mã đặt lại mật khẩu - Stewie Quiz'
    msg['From']    = email_address
    msg['To']      = to_email

    ten = ho_ten or 'bạn'
    msg.set_content(
        f'Xin chào {ten},\n\n'
        f'Mã đặt lại mật khẩu tài khoản Stewie Quiz của bạn là: {ma}\n'
        f'Mã có hiệu lực trong 10 phút.\n\n'
        f'Nếu bạn KHÔNG yêu cầu đặt lại mật khẩu, hãy bỏ qua email này — '
        f'tài khoản của bạn vẫn an toàn.'
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
        server.login(email_address, app_password)
        server.send_message(msg)
