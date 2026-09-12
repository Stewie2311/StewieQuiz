"""
Migration: cho phép 1 PHÒNG THI dùng NHIỀU MÃ ĐỀ, mỗi học sinh được
phát ngẫu nhiên 1 mã đề.

Thêm:
  1. Bảng `phong_thi_de_thi` (phong_thi_id, de_thi_id) — tập mã đề của phòng.
  2. Cột `thi_sinh.de_thi_id` — mã đề đã PHÁT cho học sinh đó (NULL = chưa vào thi).

Backfill:
  - Mỗi phòng cũ -> thêm 1 dòng (phong_thi_id, de_thi_id hiện tại) vào bảng mới.
  - Mỗi thí sinh cũ -> gán de_thi_id = de_thi_id của phòng (để xem lại bài vẫn đúng).

Chạy 1 lần: py -3.11 migrate_multi_de_phong.py  (an toàn chạy lại nhiều lần)
"""
import sys
# Cho phép chạy từ thư mục con: đưa gốc dự án vào sys.path
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_connection

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def cot_da_ton_tai(cursor, bang, cot):
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
    """, (bang, cot))
    return cursor.fetchone()[0] > 0


def bang_da_ton_tai(cursor, bang):
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
    """, (bang,))
    return cursor.fetchone()[0] > 0


def main():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1) Bảng liên kết phòng <-> nhiều mã đề
        if not bang_da_ton_tai(cursor, 'phong_thi_de_thi'):
            cursor.execute("""
                CREATE TABLE phong_thi_de_thi (
                    phong_thi_id INT NOT NULL,
                    de_thi_id    INT NOT NULL,
                    PRIMARY KEY (phong_thi_id, de_thi_id),
                    KEY idx_ptdt_de (de_thi_id),
                    CONSTRAINT fk_ptdt_phong FOREIGN KEY (phong_thi_id)
                        REFERENCES phong_thi(id) ON DELETE CASCADE,
                    CONSTRAINT fk_ptdt_de FOREIGN KEY (de_thi_id)
                        REFERENCES de_thi(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
            """)
            print("Da tao bang phong_thi_de_thi")
        else:
            print("Bang phong_thi_de_thi da ton tai - bo qua")

        # Backfill: mỗi phòng hiện có -> 1 dòng mã đề đại diện
        cursor.execute("""
            INSERT IGNORE INTO phong_thi_de_thi (phong_thi_id, de_thi_id)
            SELECT id, de_thi_id FROM phong_thi WHERE de_thi_id IS NOT NULL
        """)
        print("Backfill phong_thi_de_thi: %d dong" % cursor.rowcount)

        # 2) Cột thi_sinh.de_thi_id (mã đề đã phát cho học sinh)
        if not cot_da_ton_tai(cursor, 'thi_sinh', 'de_thi_id'):
            cursor.execute(
                "ALTER TABLE thi_sinh ADD COLUMN de_thi_id INT NULL AFTER phong_thi_id"
            )
            cursor.execute(
                "ALTER TABLE thi_sinh ADD CONSTRAINT fk_thisinh_de "
                "FOREIGN KEY (de_thi_id) REFERENCES de_thi(id) ON DELETE SET NULL"
            )
            print("Da them cot thi_sinh.de_thi_id (+ FK)")
        else:
            print("Cot thi_sinh.de_thi_id da ton tai - bo qua")

        # Backfill: thí sinh cũ -> gán mã đề của phòng họ
        cursor.execute("""
            UPDATE thi_sinh ts JOIN phong_thi p ON ts.phong_thi_id=p.id
            SET ts.de_thi_id = p.de_thi_id
            WHERE ts.de_thi_id IS NULL AND p.de_thi_id IS NOT NULL
        """)
        print("Backfill thi_sinh.de_thi_id: %d dong" % cursor.rowcount)

        conn.commit()
        print("Hoan tat migration.")
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
