"""
Giai đoạn 2 - Quản lý lớp: GÁN LỚP cho PHÒNG THI (để điểm danh).
Tạo bảng liên kết nhiều-nhiều phong_thi <-> lop. KHÔNG chặn luồng vào phòng;
lớp chỉ dùng để đối chiếu điểm danh theo MSSV.

Chạy 1 lần:  py -3.11 migrate_add_phong_lop.py
An toàn: chỉ TẠO bảng mới (IF NOT EXISTS), không đụng dữ liệu cũ.
"""
# Cho phép chạy từ thư mục con: đưa gốc dự án vào sys.path
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_connection

DDL = [
    """
    CREATE TABLE IF NOT EXISTS phong_thi_lop (
        phong_thi_id INT NOT NULL,
        lop_id       INT NOT NULL,
        ngay_gan     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (phong_thi_id, lop_id),
        KEY idx_ptl_lop (lop_id),
        CONSTRAINT fk_ptl_phong FOREIGN KEY (phong_thi_id)
            REFERENCES phong_thi(id) ON DELETE CASCADE,
        CONSTRAINT fk_ptl_lop FOREIGN KEY (lop_id)
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
        print("[OK] Da tao bang 'phong_thi_lop' (hoac da ton tai san).")
    except Exception as e:
        conn.rollback()
        print("[LOI]", e)
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
