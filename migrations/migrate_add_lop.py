"""
Tạo 2 bảng cho tính năng Quản lý lớp & sinh viên (Giai đoạn 1).
Chạy 1 lần:  py -3.11 migrate_add_lop.py
An toàn: chỉ TẠO bảng mới (IF NOT EXISTS), không đụng dữ liệu cũ.
"""
# Cho phép chạy từ thư mục con: đưa gốc dự án vào sys.path
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_connection

DDL = [
    """
    CREATE TABLE IF NOT EXISTS lop (
        id        INT AUTO_INCREMENT PRIMARY KEY,
        ten_lop   VARCHAR(255) NOT NULL,
        user_id   INT NOT NULL,
        ngay_tao  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_lop_user (user_id),
        CONSTRAINT fk_lop_user FOREIGN KEY (user_id)
            REFERENCES nguoi_dung(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS lop_sinh_vien (
        id        INT AUTO_INCREMENT PRIMARY KEY,
        lop_id    INT NOT NULL,
        ma_so_sv  VARCHAR(50) NOT NULL,
        ho_ten    VARCHAR(255) NOT NULL,
        email     VARCHAR(255) DEFAULT NULL,
        ngay_them TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_lop_mssv (lop_id, ma_so_sv),
        CONSTRAINT fk_lopsv_lop FOREIGN KEY (lop_id)
            REFERENCES lop(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
]


def main():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for ddl in DDL:
            cur.execute(ddl)
        conn.commit()
        print("[OK] Da tao bang 'lop' & 'lop_sinh_vien' (hoac da ton tai san).")
    except Exception as e:
        conn.rollback()
        print("[LOI]", e)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
