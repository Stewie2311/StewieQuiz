"""
Tác vụ dọn dẹp định kỳ — chạy theo lịch (Task Scheduler trên Windows / cron trên Linux).

    py -3.11 cleanup.py

Việc thực hiện:
  1. Đóng các phòng thi đã quá giờ kết thúc (trang_thai -> 'da_dong').
  2. XÓA ảnh webcam giám sát của phòng đã đóng, hoặc ảnh cũ hơn N ngày
     (giữ lại số liệu thống kê, chỉ gỡ ảnh) — phục vụ chính sách bảo mật.
  3. Xóa file PDF tạm còn sót trong thư mục uploads/ (cũ hơn 1 ngày).
  4. Xóa tài liệu trong Kho (uploads/tailieu/<user_id>/) cũ hơn N ngày,
     kèm file .json cache chương của nó — để kho không phình mãi.

Cấu hình qua biến môi trường:
    PROCTOR_RETENTION_DAYS  (mặc định 7)  — số ngày giữ ảnh giám sát
    UPLOAD_TMP_HOURS        (mặc định 24) — số giờ giữ file PDF tạm
    TAILIEU_RETENTION_DAYS  (mặc định 60) — số ngày giữ tài liệu trong Kho
                                            (đặt 0 = giữ vĩnh viễn, không xóa)
"""
import os
import sys
import time

# File này nằm trong scripts/ nhưng phải import database.py ở THƯ MỤC GỐC và dọn
# thư mục uploads/ cũng ở gốc. Thêm gốc vào sys.path + neo đường dẫn TUYỆT ĐỐI để
# chạy đúng dù gọi từ thư mục nào (cron, Task Scheduler, hay chạy tay).
_GOC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GOC not in sys.path:
    sys.path.insert(0, _GOC)

from database import get_db_connection

PROCTOR_RETENTION_DAYS = int(os.getenv('PROCTOR_RETENTION_DAYS', 7))
UPLOAD_TMP_HOURS       = int(os.getenv('UPLOAD_TMP_HOURS', 24))
TAILIEU_RETENTION_DAYS = int(os.getenv('TAILIEU_RETENTION_DAYS', 60))
UPLOAD_FOLDER          = os.path.join(_GOC, 'uploads')
TAILIEU_FOLDER         = os.path.join(UPLOAD_FOLDER, 'tailieu')


def dong_phong_het_gio(cursor):
    cursor.execute("""
        UPDATE phong_thi
        SET trang_thai = 'da_dong'
        WHERE trang_thai <> 'da_dong'
          AND thoi_gian_ket_thuc IS NOT NULL
          AND thoi_gian_ket_thuc < NOW()
    """)
    return cursor.rowcount


def xoa_anh_giam_sat(cursor):
    # Gỡ ảnh của phòng đã đóng HOẶC ảnh cũ hơn N ngày (giữ lại dòng thống kê)
    cursor.execute("""
        UPDATE giam_sat g
        JOIN thi_sinh t  ON g.thi_sinh_id = t.id
        JOIN phong_thi p ON t.phong_thi_id = p.id
        SET g.anh_webcam = NULL
        WHERE g.anh_webcam IS NOT NULL
          AND ( p.trang_thai = 'da_dong'
                OR g.cap_nhat < (NOW() - INTERVAL %s DAY) )
    """, (PROCTOR_RETENTION_DAYS,))
    return cursor.rowcount


def don_file_pdf_tam():
    if not os.path.isdir(UPLOAD_FOLDER):
        return 0
    nguong = time.time() - UPLOAD_TMP_HOURS * 3600
    da_xoa = 0
    for ten in os.listdir(UPLOAD_FOLDER):
        duong_dan = os.path.join(UPLOAD_FOLDER, ten)
        try:
            if os.path.isfile(duong_dan) and os.path.getmtime(duong_dan) < nguong:
                os.remove(duong_dan)
                da_xoa += 1
        except OSError:
            pass
    return da_xoa


def don_tai_lieu_cu():
    """Xóa tài liệu PDF trong Kho (uploads/tailieu/<user_id>/) cũ hơn
    TAILIEU_RETENTION_DAYS ngày, kèm file .json cache chương đi cùng.
    Đặt TAILIEU_RETENTION_DAYS=0 để giữ vĩnh viễn (bỏ qua bước này)."""
    if TAILIEU_RETENTION_DAYS <= 0 or not os.path.isdir(TAILIEU_FOLDER):
        return 0
    nguong = time.time() - TAILIEU_RETENTION_DAYS * 86400
    da_xoa = 0
    for user_dir in os.listdir(TAILIEU_FOLDER):
        thu_muc = os.path.join(TAILIEU_FOLDER, user_dir)
        if not os.path.isdir(thu_muc):
            continue
        for ten in os.listdir(thu_muc):
            if ten.endswith('.json'):
                continue  # cache .json được xóa kèm theo PDF của nó (bên dưới)
            duong_dan = os.path.join(thu_muc, ten)
            try:
                if os.path.isfile(duong_dan) and os.path.getmtime(duong_dan) < nguong:
                    os.remove(duong_dan)
                    da_xoa += 1
                    js = duong_dan + '.json'
                    if os.path.exists(js):
                        os.remove(js)
            except OSError:
                pass
    return da_xoa


def main():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        so_phong = dong_phong_het_gio(cursor)
        so_anh   = xoa_anh_giam_sat(cursor)
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    so_file = don_file_pdf_tam()
    so_tl   = don_tai_lieu_cu()
    print(f"[cleanup] Đóng {so_phong} phòng hết giờ | "
          f"Gỡ {so_anh} ảnh giám sát | Xóa {so_file} file PDF tạm | "
          f"Xóa {so_tl} tài liệu kho cũ (> {TAILIEU_RETENTION_DAYS} ngày).")


if __name__ == '__main__':
    main()
