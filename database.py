"""
Kết nối MySQL qua CONNECTION POOL.

Thay vì mở một kết nối mới cho mỗi request (chậm, dễ cạn kết nối khi đông
người), ta tạo sẵn một "bể" kết nối và tái sử dụng. Mỗi lần gọi
`get_db_connection()` lấy một kết nối khỏi bể; khi gọi `.close()` (như code
hiện tại vẫn làm) kết nối được TRẢ LẠI bể chứ không thực sự đóng.

Nếu bể tạm cạn (rò rỉ do quên close ở đâu đó / tải cao đột biến), hàm tự
rơi về kết nối trực tiếp để app KHÔNG bị lỗi 500 — đánh đổi an toàn.
"""
import os
import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv

load_dotenv()

_DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'user':     os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'taocauhoiai'),
    'charset':  'utf8mb4',           # hỗ trợ đầy đủ tiếng Việt + emoji
    'collation': 'utf8mb4_unicode_ci',
}

_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', 10))

# Tạo pool 1 lần khi import. Nếu MySQL chưa sẵn sàng lúc khởi động, không
# làm app crash — sẽ thử lại bằng kết nối trực tiếp khi có request đầu tiên.
try:
    _pool = pooling.MySQLConnectionPool(
        pool_name='taocauhoiai_pool',
        pool_size=_POOL_SIZE,
        pool_reset_session=True,
        **_DB_CONFIG,
    )
except mysql.connector.Error:
    _pool = None


def get_db_connection():
    """Trả về một kết nối MySQL. Gọi `.close()` để trả lại bể như bình thường."""
    if _pool is not None:
        try:
            return _pool.get_connection()
        except mysql.connector.errors.PoolError:
            # Bể tạm cạn -> mở kết nối trực tiếp để không chặn người dùng.
            pass
    return mysql.connector.connect(**_DB_CONFIG)
