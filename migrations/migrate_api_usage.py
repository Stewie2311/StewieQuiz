# -*- coding: utf-8 -*-
"""Tạo bảng api_usage để giới hạn số lần gọi AI mỗi 24h theo tài khoản."""
import os
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

import mysql.connector

conn = mysql.connector.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
)
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS api_usage (
        user_id INT NOT NULL,
        ngay    DATE NOT NULL,
        so_lan  INT  NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, ngay),
        CONSTRAINT fk_api_usage_user
            FOREIGN KEY (user_id) REFERENCES nguoi_dung(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
""")
conn.commit()
cur.close()
conn.close()
print("OK — bảng api_usage đã sẵn sàng.")
