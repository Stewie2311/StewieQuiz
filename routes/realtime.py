"""
Sự kiện realtime cho giám sát thi bằng VIDEO TRỰC TIẾP (Cách B: chuyển tiếp khung
hình qua Socket.IO — không lưu vào DB).

Luồng:
  - Giáo viên mở trang giám sát -> emit 'gv_join' {phong_id} -> vào "phòng" gv_<id>.
  - Học sinh đang thi -> emit 'hs_frame' {phong_id, ts_id, image} mỗi ~2 giây.
  - Server chuyển tiếp khung hình tới tất cả giáo viên trong gv_<phong_id>
    qua sự kiện 'frame'. Ảnh KHÔNG được lưu lại -> nhẹ máy chủ.
"""
from flask import session
from flask_socketio import join_room, emit
import socketio

from database import get_db_connection


def _la_chu_phong(phong_id, user_id):
    """True nếu user_id chính là giáo viên đã tạo phòng này."""
    conn = cursor = None
    try:
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM phong_thi WHERE id=%s", (phong_id,))
        row = cursor.fetchone()
        return bool(row) and row[0] == user_id
    except Exception:
        return False
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


def _la_thi_sinh_hop_le(phong_id, ts_id):
    try:
        phong_id = int(phong_id)
        ts_id = int(ts_id)
    except (TypeError, ValueError):
        return False

    # Browser này phải chính là browser đã ghi danh lượt thi đó
    ids = session.get('thi_sinh_ids', [])
    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        return False

    if ts_id not in ids:
        return False

    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 1
            FROM thi_sinh
            WHERE id=%s
              AND phong_thi_id=%s
              AND trang_thai='da_duyet'
              AND da_nop_bai=0
        """, (ts_id, phong_id))

        return cursor.fetchone() is not None

    except Exception:
        return False

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def register_socketio(socketio):

    @socketio.on('gv_join')
    def _gv_join(data):
        # Chỉ cho giáo viên/admin đã đăng nhập xem luồng giám sát (bảo vệ riêng tư).
        if session.get('role') not in ('admin', 'giao_vien'):
            return
        if not isinstance(data, dict):
            return
        phong_id = data.get('phong_id')
        if phong_id is None:
            return
        # Giáo viên CHỈ được xem webcam phòng do CHÍNH MÌNH tạo. Thiếu bước này,
        # bất kỳ giáo viên nào cũng join được gv_<id> của phòng người khác và
        # xem webcam sinh viên lớp khác. Admin xem được tất cả.
        if (session.get('role') != 'admin'
                and not _la_chu_phong(phong_id, session.get('user_id'))):
            return
        join_room(f'gv_{phong_id}')

@socketio.on('hs_frame')
def _hs_frame(data):
    if not isinstance(data, dict):
        return

    phong_id = data.get('phong_id')
    ts_id = data.get('ts_id')
    img = data.get('image')

    if phong_id is None or ts_id is None:
        return

    if not _la_thi_sinh_hop_le(phong_id, ts_id):
        return

    if (not isinstance(img, str)
            or not img.startswith('data:image')
            or len(img) > 400_000):
        return

    emit(
        'frame',
        {'ts_id': ts_id, 'image': img},
        room=f'gv_{phong_id}'
    )