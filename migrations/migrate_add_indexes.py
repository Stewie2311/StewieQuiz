"""
Migration: thêm các INDEX giúp truy vấn nhanh hơn khi dữ liệu lớn.
Chạy 1 lần: py -3.11 migrate_add_indexes.py
An toàn để chạy lại nhiều lần (kiểm tra index đã tồn tại chưa).
"""
# Cho phép chạy từ thư mục con: đưa gốc dự án vào sys.path
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_connection

# (tên_index, bảng, cột)
INDEXES = [
    ('idx_phongthi_trangthai', 'phong_thi', 'trang_thai'),
    ('idx_thisinh_email',      'thi_sinh',  'email'),
    ('idx_thisinh_mssv',       'thi_sinh',  'ma_so_sv'),
    ('idx_giamsat_capnhat',    'giam_sat',  'cap_nhat'),
]


def index_da_ton_tai(cursor, ten_bang, ten_index):
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s AND INDEX_NAME = %s
    """, (ten_bang, ten_index))
    return cursor.fetchone()[0] > 0


def main():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        for ten_index, bang, cot in INDEXES:
            if index_da_ton_tai(cursor, bang, ten_index):
                print(f"Index {ten_index} đã tồn tại — bỏ qua")
                continue
            cursor.execute(f"CREATE INDEX `{ten_index}` ON `{bang}` (`{cot}`)")
            print(f"Đã tạo index {ten_index} trên {bang}({cot})")
        conn.commit()
        print("Hoàn tất migration index.")
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
