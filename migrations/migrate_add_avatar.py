"""
Migration: thêm cột avatar (đường dẫn ảnh đại diện) vào bảng users.
Chạy 1 lần: py -3.11 migrate_add_avatar.py
An toàn để chạy lại nhiều lần (kiểm tra cột đã tồn tại chưa).
"""
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
        if not cot_da_ton_tai(cursor, 'nguoi_dung', 'avatar'):
            cursor.execute(
                "ALTER TABLE nguoi_dung ADD COLUMN avatar VARCHAR(255) NULL AFTER lop"
            )
            print("Đã thêm cột nguoi_dung.avatar")
        else:
            print("Cột nguoi_dung.avatar đã tồn tại — bỏ qua")

        conn.commit()
        print("Hoàn tất migration.")
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
