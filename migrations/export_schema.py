"""
Sinh file schema.sql (cấu trúc các bảng) từ database đang chạy.
Chạy: py -3.11 export_schema.py   ->  ghi đè schema.sql
Giúp đưa cấu trúc DB vào git để tái tạo trên môi trường khác.
"""
import io
# Cho phép chạy từ thư mục con: đưa gốc dự án vào sys.path
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_db_connection


def main():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES")
    tables = [r[0] for r in cursor.fetchall()]

    out = io.StringIO()
    out.write("-- Schema Stewie Quiz — sinh tự động bởi export_schema.py\n")
    out.write("-- KHÔNG chứa dữ liệu, chỉ cấu trúc bảng.\n")
    out.write("SET FOREIGN_KEY_CHECKS=0;\n\n")
    for t in tables:
        cursor.execute(f"SHOW CREATE TABLE `{t}`")
        ddl = cursor.fetchone()[1]
        out.write(f"-- ----- Bảng {t} -----\n")
        out.write(f"DROP TABLE IF EXISTS `{t}`;\n")
        out.write(ddl + ";\n\n")
    out.write("SET FOREIGN_KEY_CHECKS=1;\n")

    cursor.close()
    conn.close()

    with open('schema.sql', 'w', encoding='utf-8') as f:
        f.write(out.getvalue())
    print(f"Đã ghi schema.sql ({len(tables)} bảng).")


if __name__ == '__main__':
    main()
