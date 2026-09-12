"""
Các decorator kiểm soát truy cập DÙNG CHUNG cho toàn bộ Blueprint.

Trước đây mỗi file route tự định nghĩa lại `login_required` (lặp code, dễ
sai khác nhau). Gom về một chỗ để dễ bảo trì và thống nhất hành vi.
"""
from functools import wraps
from flask import session, redirect, url_for, flash


def login_required(f):
    """Bắt buộc đã đăng nhập (có user_id trong session)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Vui lòng đăng nhập!', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def giao_vien_required(f):
    """Chỉ cho phép Giáo viên hoặc Admin (dùng cho tạo đề, tạo phòng...)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Vui lòng đăng nhập!', 'warning')
            return redirect(url_for('auth.login'))
        if session.get('role') not in ('giao_vien', 'admin'):
            flash('Chức năng này chỉ dành cho giáo viên!', 'danger')
            return redirect(url_for('exam.index'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Chỉ cho phép Admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        if session.get('role') != 'admin':
            flash('Bạn không có quyền truy cập!', 'danger')
            return redirect(url_for('exam.index'))
        return f(*args, **kwargs)
    return decorated
