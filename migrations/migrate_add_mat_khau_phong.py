"""
Migration: TÁCH mã phòng và mật khẩu thành 2 trường riêng.
- Thêm cột phong_thi.mat_khau (mật khẩu vào phòng, tùy chọn, ĐƯỢC PHÉP trùng).
- ma_phong giữ vai trò MÃ ĐỊA CHỈ duy nhất (dùng trong link /thi/<ma_phong>).
- Với phòng CŨ: trước đây ma_phong đóng luôn vai mật khẩu -> copy ma_phong
  sang mat_khau để các phòng cũ giữ NGUYÊN hành vi (vẫn cần mã đó để vào).

Chạy 1 lần: py -3.11 migrate_add_mat_khau_phong.py
An toàn để chạy lại nhiều lần (kiểm tra cột đã tồn tại chưa).
"""
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')  # tránh lỗi cp1252 khi in tiếng Việt
except Exception:
    pass
# Cho phép chạy từ thư mục con: đưa gốc dự án vào sys.path
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_connection


def cot_da_ton_tai(cursor, ten_bang, ten_cot):
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s AND COLUMN_NAME = %s
    """, (ten_bang, ten_cot))
    return cursor.fetchone()[0] > 0


def main():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if not cot_da_ton_tai(cursor, 'phong_thi', 'mat_khau'):
            cursor.execute(
                "ALTER TABLE phong_thi ADD COLUMN mat_khau VARCHAR(255) NULL AFTER ma_phong"
            )
            print("Đã thêm cột phong_thi.mat_khau")
            # Phòng cũ: ma_phong từng là mật khẩu -> giữ nguyên hành vi.
            cursor.execute(
                "UPDATE phong_thi SET mat_khau = ma_phong WHERE mat_khau IS NULL"
            )
            print(f"Đã sao chép mã phòng cũ sang mật khẩu cho {cursor.rowcount} phòng")
        else:
            print("Cột phong_thi.mat_khau đã tồn tại — bỏ qua")

        conn.commit()
        print("Hoàn tất migration.")
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
