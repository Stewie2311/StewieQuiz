"""
Migration: bổ sung các cột phục vụ KIỂM DUYỆT câu hỏi do AI sinh ra.

    py -3.11 migrations/migrate_kiem_duyet_cau_hoi.py

Vì sao cần:
  • ngan_hang_cau_hoi ĐÃ có `bloom` và `trang_thai`, nhưng câu lệnh INSERT lại
    BỎ SÓT `bloom` -> 100% câu trong ngân hàng đang có bloom = NULL.
  • Bảng KHÔNG có `giai_thich`: không lưu được lý do đáp án đúng, giảng viên
    không kiểm chứng được câu hỏi.
  • Chưa có chỗ ghi `nguon_tao` (AI hay giảng viên tự soạn) và `canh_bao`
    (các lỗi hệ thống phát hiện được) để lọc/soát về sau.

An toàn: chỉ THÊM cột, không sửa/xóa dữ liệu cũ. Chạy lại nhiều lần không sao
(cột đã tồn tại thì bỏ qua).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db_connection   # noqa: E402


# (bảng, tên cột, định nghĩa)
COT_CAN_THEM = [
    ('ngan_hang_cau_hoi', 'giai_thich',
     "TEXT NULL COMMENT 'Vì sao đáp án đúng — bắt buộc với câu do AI tạo'"),
    ('ngan_hang_cau_hoi', 'nguon_tao',
     "VARCHAR(20) NULL DEFAULT 'AI' COMMENT 'AI | giao_vien'"),
    ('ngan_hang_cau_hoi', 'canh_bao',
     "TEXT NULL COMMENT 'Các mã cảnh báo hệ thống phát hiện, cách nhau bởi dấu phẩy'"),
    # cau_hoi đã có bloom + giai_thich; chỉ thiếu nguồn tạo
    ('cau_hoi', 'nguon_tao',
     "VARCHAR(20) NULL DEFAULT 'AI' COMMENT 'AI | giao_vien'"),
]


def cot_da_co(cur, bang, cot):
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s AND COLUMN_NAME = %s
    """, (bang, cot))
    return cur.fetchone()[0] > 0


def main():
    conn = get_db_connection()
    cur = conn.cursor()
    them, bo_qua = 0, 0
    try:
        for bang, cot, dinh_nghia in COT_CAN_THEM:
            if cot_da_co(cur, bang, cot):
                print(f'  [bỏ qua] {bang}.{cot} đã tồn tại')
                bo_qua += 1
                continue
            cur.execute(f'ALTER TABLE {bang} ADD COLUMN {cot} {dinh_nghia}')
            print(f'  [THÊM]   {bang}.{cot}')
            them += 1

        # Câu đã có sẵn trong ngân hàng: đánh dấu nguồn AI + trạng thái chờ duyệt
        cur.execute("""
            UPDATE ngan_hang_cau_hoi
               SET nguon_tao = 'AI'
             WHERE nguon_tao IS NULL
        """)
        conn.commit()
        print(f'\nXong: thêm {them} cột, bỏ qua {bo_qua} cột đã có.')
    except Exception as e:
        conn.rollback()
        print('LỖI:', e)
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
