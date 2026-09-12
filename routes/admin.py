"""
Trang QUẢN TRỊ (chỉ Admin).

Gồm 4 mảng việc:
  1. Dashboard  — trang /admin và các API JSON trả số liệu thống kê.
                  Phần tính toán nằm ở routes/thong_ke.py, ở đây chỉ bọc lại
                  thành endpoint có kiểm tra quyền.
  2. Prompt AI  — cho admin sửa system prompt dùng khi sinh câu hỏi.
  3. Người dùng — khóa/mở khóa, đổi vai trò, xóa tài khoản.
  4. Đề thi     — xóa đề thi vi phạm.

Mọi route đều bọc @admin_required nên không cần kiểm tra quyền lại bên trong.
"""
from flask import (Blueprint, render_template, request,
                   redirect, flash, jsonify)
from database import get_db_connection
from decorators import admin_required
from routes.exam import (_lay_system_prompt, _luu_system_prompt,
                         _khoi_phuc_system_prompt, _prompt_dang_tuy_chinh,
                         _SYSTEM_PROMPT)
from routes import thong_ke

admin_bp = Blueprint('admin', __name__)


# ==========================================
# ADMIN DASHBOARD
# ==========================================
@admin_bp.route('/admin')
@admin_required
def admin_dashboard():
    """Dựng khung trang /admin.

    Chỉ nạp dữ liệu TĨNH của trang (danh sách người dùng, các tùy chọn cho ô
    lọc, prompt AI hiện hành). Số liệu thống kê nặng được giao diện gọi riêng
    qua các API JSON bên dưới để trang mở lên nhanh.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM nguoi_dung WHERE role!='admin' ORDER BY id DESC"
    )
    users = cursor.fetchall()

    cursor.execute(
        "SELECT id, ho_ten FROM nguoi_dung WHERE role='giao_vien' ORDER BY ho_ten"
    )
    filter_teachers = cursor.fetchall()

    # Chỉ lấy học phần ĐÃ có đề thi hoặc câu hỏi. Trường có hơn 300 học phần,
    # đổ hết vào ô lọc thì người dùng phải cuộn mỏi tay qua toàn môn rỗng.
    cursor.execute("""
        SELECT h.id, h.ma_hoc_phan, h.ten_hoc_phan
        FROM hoc_phan h
        WHERE h.id IN (SELECT DISTINCT hoc_phan_id FROM de_thi WHERE hoc_phan_id IS NOT NULL)
           OR h.id IN (SELECT DISTINCT hoc_phan_id FROM ngan_hang_cau_hoi)
        ORDER BY h.ten_hoc_phan
    """)
    filter_subjects = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template('admin.html',
                           users=users,
                           filter_teachers=filter_teachers,
                           filter_subjects=filter_subjects,
                           system_prompt=_lay_system_prompt(),
                           system_prompt_default=_SYSTEM_PROMPT,
                           prompt_is_custom=_prompt_dang_tuy_chinh())


# ==========================================
# API THỐNG KÊ DASHBOARD (trả JSON)
#
# Trang /admin gọi 4 endpoint này bằng AJAX sau khi khung trang đã hiện, mỗi
# endpoint một khối trên giao diện. Tách nhỏ như vậy để một truy vấn chậm chỉ
# làm chậm đúng khối của nó, thay vì treo cả trang.
# ==========================================
def _dashboard_api(builder, *, extra=None):
    """Khung chung cho 4 endpoint dashboard.

    Lo phần lặp đi lặp lại: mở kết nối, đọc tham số lọc, gọi `builder`, đóng
    kết nối. Bọc try/except để một truy vấn lỗi chỉ khiến khối đó hiện "Lỗi
    tải dữ liệu", không làm vỡ cả trang quản trị.
    """
    conn = cursor = None
    try:
        flt = thong_ke.parse_filters(request.args)
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        data = builder(cursor, flt)
        if extra:
            data = {**data, **extra(cursor, flt)}
        return jsonify({'ok': True, 'data': data})
    except Exception as e:
        # Phải log traceback ở đây: trả JSON lỗi về cho giao diện là hết dấu
        # vết, không có dòng này thì log server trống trơn, không truy được.
        from flask import current_app
        current_app.logger.exception('Lỗi API dashboard thống kê')
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@admin_bp.route('/admin/dashboard/stats')
@admin_required
def dashboard_stats():
    """Các ô số liệu tổng quan ở đầu dashboard (tổng người dùng, đề, phòng...)."""
    return _dashboard_api(thong_ke.get_stats)


@admin_bp.route('/admin/dashboard/charts')
@admin_required
def dashboard_charts():
    """Dữ liệu cho các biểu đồ (phổ điểm, hoạt động theo thời gian...)."""
    return _dashboard_api(thong_ke.get_charts)


@admin_bp.route('/admin/dashboard/recent-activities')
@admin_required
def dashboard_recent_activities():
    """Hai bảng 'hoạt động gần đây' và 'lượt gọi AI gần đây'."""
    def build(cursor, flt):
        return {
            'recentActivities': thong_ke.get_recent_activities(cursor, flt),
            'recentAI':         thong_ke.get_recent_ai(cursor, flt),
        }
    return _dashboard_api(build)


@admin_bp.route('/admin/dashboard/alerts')
@admin_required
def dashboard_alerts():
    """Các cảnh báo cần admin để mắt (phòng thi bất thường, quá hạn mức AI...)."""
    def build(cursor, flt):
        return {'alerts': thong_ke.get_alerts(cursor, flt)}
    return _dashboard_api(build)


# ==========================================
# CẤU HÌNH PROMPT AI
#
# Admin sửa được system prompt dùng để sinh câu hỏi, nhưng bản sửa lưu ra FILE
# JSON chứ không ghi đè mã nguồn — sai prompt thì bấm "khôi phục" là về mặc
# định, không cần sửa code rồi deploy lại.
# ==========================================
@admin_bp.route('/admin/prompt', methods=['POST'])
@admin_required
def luu_prompt():
    """Lưu prompt admin vừa sửa; áp dụng ngay cho lần sinh câu hỏi kế tiếp."""
    ok, loi = _luu_system_prompt(request.form.get('system_prompt', ''))
    if ok:
        flash('Đã lưu prompt AI mới. Các lần tạo câu hỏi tiếp theo sẽ dùng prompt này.', 'success')
    else:
        flash(f'Không lưu được: {loi}', 'danger')
    return redirect('/admin#prompt')


@admin_bp.route('/admin/prompt/reset', methods=['POST'])
@admin_required
def reset_prompt():
    """Xóa prompt tùy chỉnh, quay về prompt mặc định viết sẵn trong code."""
    _khoi_phuc_system_prompt()
    flash('Đã khôi phục prompt AI về bản mặc định trong hệ thống.', 'success')
    return redirect('/admin#prompt')


# ==========================================
# QUẢN LÝ NGƯỜI DÙNG
#
# Ba thao tác dưới đây có hiệu lực NGAY ở request kế tiếp của nạn nhân, nhờ
# hook đồng bộ phiên trong app.py (_register_session_guard) — không phải chờ
# họ tự đăng xuất.
# ==========================================
@admin_bp.route('/admin/toggle_user/<int:user_id>', methods=['POST'])
@admin_required
def toggle_user(user_id):
    """Khóa tài khoản đang mở, hoặc mở lại tài khoản đang khóa."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT trang_thai, ho_ten, role FROM nguoi_dung WHERE id=%s", (user_id,)
        )
        user = cursor.fetchone()
        if not user:
            flash('Không tìm thấy người dùng!', 'danger')
            return redirect('/admin')
        # Cấm khóa admin, kể cả chính mình: hook đồng bộ phiên sẽ đá họ ra ngay
        # request sau, và nếu hệ thống chỉ có đúng 1 admin thì không còn ai vào
        # được trang Quản trị để mở khóa nữa — tự nhốt mình ngoài cửa.
        if user['role'] == 'admin':
            flash('Không thể khóa tài khoản quản trị viên!', 'danger')
            return redirect('/admin#users')

        new_status = 'locked' if user['trang_thai'] == 'active' else 'active'
        cursor.execute(
            "UPDATE nguoi_dung SET trang_thai=%s WHERE id=%s",
            (new_status, user_id)
        )
        conn.commit()
        action = 'Đã khóa' if new_status == 'locked' else 'Đã mở khóa'
        flash(f'{action} tài khoản: {user["ho_ten"]}', 'success')

    except Exception as e:
        flash(f'Lỗi: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect('/admin#users')


@admin_bp.route('/admin/change_role/<int:user_id>', methods=['POST'])
@admin_required
def change_role(user_id):
    """Đổi vai trò giữa Giáo viên và Học sinh.

    Không cho phép phong/hạ vai trò 'admin' qua đường này — câu SELECT lọc sẵn
    `role != 'admin'`, nên tài khoản admin không bao giờ là mục tiêu hợp lệ.
    """
    new_role = request.form.get('role')
    if new_role not in ('giao_vien', 'hoc_sinh'):
        flash('Vai trò không hợp lệ!', 'danger')
        return redirect('/admin#users')

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT ho_ten FROM nguoi_dung WHERE id=%s AND role!='admin'", (user_id,))
        user = cursor.fetchone()
        if not user:
            flash('Không tìm thấy người dùng!', 'danger')
            return redirect('/admin#users')

        cursor.execute("UPDATE nguoi_dung SET role=%s WHERE id=%s", (new_role, user_id))
        conn.commit()
        label = 'Giáo viên' if new_role == 'giao_vien' else 'Học sinh'
        flash(f'Đã đổi vai trò "{user["ho_ten"]}" thành {label}', 'success')
    except Exception as e:
        flash(f'Lỗi: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect('/admin#users')


@admin_bp.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Xóa hẳn một tài khoản (trừ admin). Dữ liệu liên quan xóa theo ràng buộc FK."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT ho_ten, role FROM nguoi_dung WHERE id=%s", (user_id,))
        user = cursor.fetchone()
        if not user or user['role'] == 'admin':
            flash('Không thể xóa tài khoản này!', 'danger')
            return redirect('/admin#users')

        cursor.execute("DELETE FROM nguoi_dung WHERE id=%s", (user_id,))
        conn.commit()
        flash(f'Đã xóa tài khoản: {user["ho_ten"]}', 'success')
    except Exception as e:
        flash(f'Lỗi khi xóa: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect('/admin#users')


# ==========================================
# XÓA ĐỀ THI (quyền admin, dùng khi đề vi phạm)
# ==========================================
@admin_bp.route('/admin/delete_exam/<int:exam_id>', methods=['POST'])
@admin_required
def delete_exam(exam_id):
    """Xóa một đề thi bất kỳ, không cần là người tạo ra nó."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT ten_de_thi FROM de_thi WHERE id=%s", (exam_id,))
        exam = cursor.fetchone()
        if not exam:
            flash('Không tìm thấy đề thi!', 'danger')
            return redirect('/admin#exams')

        cursor.execute("DELETE FROM de_thi WHERE id=%s", (exam_id,))
        conn.commit()
        flash(f'Đã xóa đề thi: {exam["ten_de_thi"]}', 'success')
    except Exception as e:
        flash(f'Lỗi khi xóa đề thi: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect('/admin#exams')


# ==========================================
# MÔN HỌC / HỌC PHẦN
# ==========================================
# Bảng mon_hoc đã được HỢP NHẤT vào danh mục chuẩn `hoc_phan` (nạp từ file Excel
# của trường qua migrations/import_hoc_phan_cntt.py). Học phần là danh mục cố định
# nên KHÔNG còn route thêm/xóa môn thủ công ở đây (tránh tạo dữ liệu môn "rác").
