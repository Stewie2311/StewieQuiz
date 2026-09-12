"""
Migration: chuẩn hóa lại 3 quan hệ dữ liệu (chạy 1 lần, an toàn chạy lại nhiều lần).

1) PHÒNG THI ↔ ĐỀ THI: bỏ cột dư thừa `phong_thi.de_thi_id` (+ FK, + index).
   Quan hệ 1 phòng ↔ nhiều mã đề chỉ còn giữ ở BẢNG TRUNG GIAN `phong_thi_de_thi`.

2) HỢP NHẤT MÔN HỌC → HỌC PHẦN:
   - Đổi tên cột `de_thi.mon_hoc_id`            -> `de_thi.hoc_phan_id`
   - Đổi tên cột `ngan_hang_cau_hoi.mon_hoc_id` -> `ngan_hang_cau_hoi.hoc_phan_id`
   - FK của 2 cột trên trỏ về `hoc_phan(id)`.
   - XÓA hẳn bảng `mon_hoc`.
   (Nếu còn dữ liệu cũ: map mon_hoc.id -> hoc_phan.id theo tên môn khớp
    ten_mon = ten_hoc_phan; ID không khớp được set NULL để không vỡ FK.)

3) NGÂN HÀNG DÙNG CHUNG: xóa cột `ngan_hang_cau_hoi.de_thi_id` (+ FK) — kho câu
   hỏi dùng chung không được trỏ về một đề thi cụ thể.

Chạy: py -3.11 migrations/migrate_refactor_relations.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_connection

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# ---------- Trợ giúp kiểm tra (idempotent) ----------
def cot_ton_tai(cur, bang, cot):
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
    """, (bang, cot))
    return cur.fetchone()[0] > 0


def bang_ton_tai(cur, bang):
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
    """, (bang,))
    return cur.fetchone()[0] > 0


def fk_tren_cot(cur, bang, cot):
    """Danh sách TÊN các FOREIGN KEY đặt trên (bang.cot)."""
    cur.execute("""
        SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
          AND REFERENCED_TABLE_NAME IS NOT NULL
    """, (bang, cot))
    return [r[0] for r in cur.fetchall()]


def bo_moi_fk(cur, bang, cot):
    for ten in fk_tren_cot(cur, bang, cot):
        cur.execute(f"ALTER TABLE `{bang}` DROP FOREIGN KEY `{ten}`")
        print(f"  - Đã bỏ FK {ten} trên {bang}.{cot}")


def index_ton_tai(cur, bang, ten_index):
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
    """, (bang, ten_index))
    return cur.fetchone()[0] > 0


def bo_index(cur, bang, ten_index):
    if index_ton_tai(cur, bang, ten_index):
        cur.execute(f"ALTER TABLE `{bang}` DROP INDEX `{ten_index}`")
        print(f"  - Đã bỏ index {ten_index} trên {bang}")


def main():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # =========================================================
        # 2) ĐỔI TÊN CỘT mon_hoc_id -> hoc_phan_id + FK về hoc_phan
        #    (làm trước để có thể DROP bảng mon_hoc ở cuối)
        # =========================================================
        if not bang_ton_tai(cur, 'hoc_phan'):
            raise SystemExit(
                "Chưa có bảng hoc_phan. Hãy chạy import_hoc_phan_cntt.py trước "
                "khi refactor (mon_hoc_id sẽ trỏ về hoc_phan)."
            )

        for bang in ('de_thi', 'ngan_hang_cau_hoi'):
            # a) Bỏ FK cũ trỏ về mon_hoc (nếu còn)
            bo_moi_fk(cur, bang, 'mon_hoc_id')
            bo_moi_fk(cur, bang, 'hoc_phan_id')  # phòng khi chạy lại giữa chừng

            # b) Map ID cũ (mon_hoc.id -> hoc_phan.id theo tên) TRƯỚC khi đổi FK,
            #    để dữ liệu cũ (nếu có) không vỡ ràng buộc.
            if cot_ton_tai(cur, bang, 'mon_hoc_id') and bang_ton_tai(cur, 'mon_hoc'):
                cur.execute(f"""
                    UPDATE `{bang}` t
                    JOIN mon_hoc mh ON mh.id = t.mon_hoc_id
                    JOIN hoc_phan hp ON LOWER(hp.ten_hoc_phan) = LOWER(mh.ten_mon)
                    SET t.mon_hoc_id = hp.id
                """)
                if cur.rowcount:
                    print(f"  - {bang}: map {cur.rowcount} dòng mon_hoc.id -> hoc_phan.id theo tên")
                # ID nào không map được -> NULL để khỏi vỡ FK (chỉ với cột nullable)
                if bang == 'de_thi':
                    cur.execute(f"""
                        UPDATE `{bang}` t
                        LEFT JOIN hoc_phan hp ON hp.id = t.mon_hoc_id
                        SET t.mon_hoc_id = NULL
                        WHERE t.mon_hoc_id IS NOT NULL AND hp.id IS NULL
                    """)

            # c) Đổi tên cột (nếu chưa đổi)
            if cot_ton_tai(cur, bang, 'mon_hoc_id') and not cot_ton_tai(cur, bang, 'hoc_phan_id'):
                if bang == 'de_thi':
                    cur.execute("ALTER TABLE de_thi CHANGE COLUMN mon_hoc_id hoc_phan_id INT(11) DEFAULT NULL")
                else:
                    cur.execute("ALTER TABLE ngan_hang_cau_hoi CHANGE COLUMN mon_hoc_id hoc_phan_id INT(11) NOT NULL")
                print(f"  - {bang}: đổi tên cột mon_hoc_id -> hoc_phan_id")
                # Bỏ index cũ tên mon_hoc_id (MariaDB không có RENAME INDEX);
                # index tên hoc_phan_id sẽ được thêm ở bước (d) bên dưới.
                bo_index(cur, bang, 'mon_hoc_id')

            # d) Thêm FK mới trỏ về hoc_phan(id) (nếu chưa có)
            if cot_ton_tai(cur, bang, 'hoc_phan_id') and not fk_tren_cot(cur, bang, 'hoc_phan_id'):
                if not index_ton_tai(cur, bang, 'hoc_phan_id'):
                    cur.execute(f"ALTER TABLE `{bang}` ADD INDEX `hoc_phan_id` (`hoc_phan_id`)")
                ten_fk = f"fk_{bang}_hoc_phan"
                cur.execute(
                    f"ALTER TABLE `{bang}` ADD CONSTRAINT `{ten_fk}` "
                    f"FOREIGN KEY (`hoc_phan_id`) REFERENCES `hoc_phan`(`id`)"
                )
                print(f"  - {bang}: thêm FK hoc_phan_id -> hoc_phan(id)")

        # =========================================================
        # 3) NGÂN HÀNG DÙNG CHUNG: bỏ cột de_thi_id (+ FK)
        # =========================================================
        bo_moi_fk(cur, 'ngan_hang_cau_hoi', 'de_thi_id')
        if cot_ton_tai(cur, 'ngan_hang_cau_hoi', 'de_thi_id'):
            bo_index(cur, 'ngan_hang_cau_hoi', 'de_thi_id')
            cur.execute("ALTER TABLE ngan_hang_cau_hoi DROP COLUMN de_thi_id")
            print("  - ngan_hang_cau_hoi: đã xóa cột de_thi_id")

        # =========================================================
        # 1) PHÒNG THI: bỏ cột de_thi_id dư thừa (+ FK, + index)
        #    An toàn: bảo đảm mọi phòng đều có mã đề trong bảng trung gian
        #    trước khi bỏ cột đại diện.
        # =========================================================
        if cot_ton_tai(cur, 'phong_thi', 'de_thi_id'):
            cur.execute("""
                INSERT IGNORE INTO phong_thi_de_thi (phong_thi_id, de_thi_id)
                SELECT id, de_thi_id FROM phong_thi WHERE de_thi_id IS NOT NULL
            """)
            if cur.rowcount:
                print(f"  - Backfill phong_thi_de_thi từ mã đề đại diện: {cur.rowcount} dòng")
            bo_moi_fk(cur, 'phong_thi', 'de_thi_id')
            bo_index(cur, 'phong_thi', 'idx_phong_thi_dethi')
            cur.execute("ALTER TABLE phong_thi DROP COLUMN de_thi_id")
            print("  - phong_thi: đã xóa cột de_thi_id (giữ bảng phong_thi_de_thi)")

        # =========================================================
        # 2b) XÓA HẲN BẢNG mon_hoc
        # =========================================================
        if bang_ton_tai(cur, 'mon_hoc'):
            cur.execute("DROP TABLE mon_hoc")
            print("  - Đã xóa bảng mon_hoc")

        conn.commit()
        print("Hoàn tất migration chuẩn hóa quan hệ.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
