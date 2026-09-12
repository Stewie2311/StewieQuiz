"""
Migration: thêm cột `loai` vào bảng de_thi để phân biệt
  - 'cau_hoi' : bộ câu hỏi sinh từ PDF (lưu ở tab "Câu hỏi" của thư viện)
  - 'de_thi'  : đề thi đóng gói từ wizard tạo đề (lưu ở tab "Đề thi")

Chạy 1 lần: py -3.11 migrate_add_loai_dethi.py
An toàn để chạy lại nhiều lần (kiểm tra cột đã tồn tại chưa).
"""
import sys
# Cho phép chạy từ thư mục con: đưa gốc dự án vào sys.path
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_connection

# Console Windows mặc định cp1252 không in được tiếng Việt -> ép UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


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
        if not cot_da_ton_tai(cursor, 'de_thi', 'loai'):
            cursor.execute(
                "ALTER TABLE de_thi ADD COLUMN loai VARCHAR(20) "
                "NOT NULL DEFAULT 'cau_hoi' AFTER ten_de_thi"
            )
            print("Da them cot de_thi.loai")
        else:
            print("Cot de_thi.loai da ton tai - bo qua")

        # Backfill dữ liệu cũ (luôn chạy, idempotent): các đề do wizard sinh ra
        # đều có chuỗi ' - Mã đề A' trong tên -> đánh dấu là 'de_thi'.
        cursor.execute(
            "UPDATE de_thi SET loai = 'de_thi' "
            "WHERE loai <> 'de_thi' AND ten_de_thi LIKE %s",
            ('% - Mã đề A%',)
        )
        print("Da danh dau %d de cu la 'de_thi'" % cursor.rowcount)

        conn.commit()
        print("Hoàn tất migration.")
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
