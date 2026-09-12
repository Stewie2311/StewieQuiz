"""
Migration: thêm cột `nguon` vào bảng de_thi để biết đề thi lấy câu hỏi từ đâu
('ca_nhan' = Câu hỏi của tôi, 'ngan_hang' = Ngân hàng câu hỏi).
Đề tạo TRƯỚC migration này sẽ để NULL -> hiển thị "Không rõ".

Chạy 1 lần: py -3.11 migrate_add_de_nguon.py
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
        if not cot_da_ton_tai(cursor, 'de_thi', 'nguon'):
            cursor.execute(
                "ALTER TABLE de_thi ADD COLUMN nguon VARCHAR(20) DEFAULT NULL"
            )
            print("[OK] Da them cot de_thi.nguon")
        else:
            print("[SKIP] Cot de_thi.nguon da ton tai")

        conn.commit()
        print("[DONE] Hoan tat migration.")
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
