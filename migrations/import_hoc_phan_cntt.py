# -*- coding: utf-8 -*-
"""
Đọc 'Danh sach hoc phan.xlsx', lọc các học phần thuộc ngành Công nghệ thông tin
(Khoa Thông tin Và Truyền Thông, loại bỏ báo chí / truyền thông đa phương tiện /
thiết kế đồ họa / marketing / nhân văn), rồi nạp vào bảng `hoc_phan` trong MySQL.

Chạy: python import_hoc_phan_cntt.py
"""
import os
import io
import openpyxl
import mysql.connector
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(ROOT_DIR, '.env'))

XLSX = os.path.join(
    ROOT_DIR,
    'Anh',
    'Danh sach hoc phan.xlsx'
)
KHOA_CNTT = "Thông tin Và Truyền Thông"

# Từ khóa loại trừ -> môn KHÔNG thuộc CNTT (báo chí / truyền thông SX / thiết kế / marketing / nhân văn)
EXCLUDE_KW = [
    # Báo chí
    "báo chí", "phóng sự", "phát thanh", "truyền hình", "phỏng vấn", "ghi hình",
    "phim tài liệu", "ký sự", "tác phẩm và thể loại",
    # Sản xuất truyền thông / media
    "biên tập", "sản xuất video", "sản xuất phim", "sản xuất chương trình",
    "sản xuất podcast", "podcast", "kỹ xảo", "dựng hình", "quay phim", "chụp hình",
    "kịch bản", "ấn phẩm", "dẫn chương trình", "đối thoại truyền hình",
    "xây dựng nội dung truyền thông", "nội dung truyền thông",
    "khủng hoảng truyền thông", "quản lý dự án truyền thông",
    "sáng tạo truyền thông", "truyền thông quốc tế", "truyền thông marketing",
    "dư luận xã hội", "xuất bản",
    # Thiết kế đồ họa / quảng cáo / marketing
    "đồ họa", "corel", "photoshop", "canva", "quảng cáo", "marketing",
    "thương hiệu", "tổ chức sự kiện", "thiết kế tương tác đa phương tiện",
    # Nhân văn / đại cương không thuộc CNTT
    "mỹ học", "xã hội học", "logic học", "sinh hoạt lớp",
]


def la_cntt(ten: str) -> bool:
    t = (ten or "").lower()
    for kw in EXCLUDE_KW:
        if kw in t:
            return False
    return True


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def main():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb.active

    kept, excluded = [], []
    seen = set()
    for r in ws.iter_rows(min_row=5, values_only=True):
        # cols: 0 stt,1 ma,2 ten,3 ten_rut,4 tc,5 ma_bm,6 ten_bm,7 ma_dv,8 ten_dv
        ma, ten = r[1], r[2]
        if ma is None or ten is None:
            continue
        if KHOA_CNTT not in str(r[8]):
            continue
        ten = str(ten).strip()
        if not la_cntt(ten):
            excluded.append((str(ma).strip().upper(), ten))
            continue
        ma_u = str(ma).strip().upper()
        if ma_u in seen:           # tránh trùng mã (dữ liệu nguồn đôi khi lặp)
            continue
        seen.add(ma_u)
        kept.append({
            "ma": ma_u,
            "ten": ten,
            "ten_rut": (str(r[3]).strip() if r[3] is not None else None),
            "tc": to_int(r[4]),
        })

    # Ghi file review (UTF-8) để người dùng kiểm tra
    buf = io.StringIO()
    buf.write("=== GIU LAI (CNTT) : %d mon ===\n" % len(kept))
    for i, k in enumerate(kept, 1):
        buf.write("%3d | %-14s | %2s tc | %s\n" % (i, k["ma"], k["tc"], k["ten"]))
    buf.write("\n=== LOAI BO (khong thuoc CNTT) : %d mon ===\n" % len(excluded))
    for i, (ma, ten) in enumerate(excluded, 1):
        buf.write("%3d | %-14s | %s\n" % (i, ma, ten))
    review_file = os.path.join(ROOT_DIR, '_hoc_phan_review.txt')

    with open(review_file, 'w', encoding='utf-8') as f:
        f.write(buf.getvalue())

    # ----- Nạp vào MySQL -----
    conn = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "taocauhoiai"),
        charset="utf8mb4",
    )
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `hoc_phan` (
          `id` INT(11) NOT NULL AUTO_INCREMENT,
          `ma_hoc_phan` VARCHAR(50) NOT NULL,
          `ten_hoc_phan` VARCHAR(255) NOT NULL,
          `ten_rut_gon` VARCHAR(255) DEFAULT NULL,
          `so_tin_chi` INT(11) DEFAULT NULL,
          `nganh` VARCHAR(50) NOT NULL DEFAULT 'CNTT',
          `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_ma_hoc_phan` (`ma_hoc_phan`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """)
    rows = [(k["ma"], k["ten"], k["ten_rut"], k["tc"]) for k in kept]
    cur.executemany("""
        INSERT INTO `hoc_phan` (ma_hoc_phan, ten_hoc_phan, ten_rut_gon, so_tin_chi)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            ten_hoc_phan = VALUES(ten_hoc_phan),
            ten_rut_gon  = VALUES(ten_rut_gon),
            so_tin_chi   = VALUES(so_tin_chi)
    """, rows)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM `hoc_phan`")
    total = cur.fetchone()[0]
    cur.close()
    conn.close()

    print("Giu lai (CNTT):", len(kept))
    print("Loai bo        :", len(excluded))
    print("Tong trong bang hoc_phan:", total)
    print("File review: _hoc_phan_review.txt")


if __name__ == "__main__":
    main()
