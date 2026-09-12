# Đưa Stewie Quiz lên VPS Ubuntu — stewiequiz.io.vn

Làm lần lượt từ trên xuống. Ước tính 30–45 phút.
Ký hiệu: `$` = gõ trên VPS (qua SSH/PuTTY). WinSCP chỉ dùng để **chép file lên**.

---

## 0. Trỏ tên miền về VPS (làm TRƯỚC, vì DNS cần thời gian lan truyền)

Vào trang quản lý tên miền `stewiequiz.io.vn`, tạo 2 bản ghi:

| Loại | Tên | Giá trị |
|------|-----|---------|
| A    | `@`   | `<IP VPS của bạn>` |
| A    | `www` | `<IP VPS của bạn>` |

Kiểm tra (đợi vài phút tới vài giờ):

```bash
$ ping stewiequiz.io.vn      # phải ra đúng IP VPS
```

**Chưa trỏ xong DNS thì bước xin SSL (mục 6) sẽ thất bại.**

---

## 1. Cài phần mềm nền

```bash
$ sudo apt update && sudo apt upgrade -y
$ sudo apt install -y python3.11 python3.11-venv python3-pip \
                      mysql-server nginx certbot python3-certbot-nginx git
```

---

## 2. Tạo database

```bash
$ sudo mysql
```

Trong dấu nhắc MySQL (đổi `MatKhauManh!123` thành mật khẩu của bạn):

```sql
CREATE DATABASE taocauhoiai CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'stewie'@'localhost' IDENTIFIED BY 'MatKhauManh!123';
GRANT ALL PRIVILEGES ON taocauhoiai.* TO 'stewie'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Xuất DB từ máy Windows (XAMPP) rồi chép lên bằng WinSCP:

```bat
REM Trên máy bạn (Windows), trong thư mục xampp\mysql\bin:
mysqldump -u root taocauhoiai > taocauhoiai.sql
```

Chép `taocauhoiai.sql` lên `/tmp/` bằng WinSCP, rồi nạp vào:

```bash
$ mysql -u stewie -p taocauhoiai < /tmp/taocauhoiai.sql
```

---

## 3. Chép mã nguồn lên VPS

Dùng WinSCP chép **toàn bộ thư mục dự án** lên `/var/www/stewiequiz`.

**KHÔNG chép các thứ sau** (rác, nặng, hoặc sai môi trường):
`venv/`, `__pycache__/`, `logs/`, `uploads/`, `.git/`

Sau khi chép xong:

```bash
$ sudo chown -R $USER:$USER /var/www/stewiequiz
$ cd /var/www/stewiequiz
$ python3.11 -m venv venv
$ ./venv/bin/pip install --upgrade pip
$ ./venv/bin/pip install -r requirements.txt
```

> **Về chức năng "phát hiện câu hỏi trùng lặp":** dòng `sentence-transformers`
> trong `requirements.txt` đang bị comment vì nó kéo theo PyTorch (~2–3GB) và tải
> model từ HuggingFace. VPS **dưới 4GB RAM thì cứ để nguyên** — hệ thống tự dùng
> so khớp chuỗi thay thế, mọi thứ khác chạy bình thường. VPS ≥ 4GB RAM và muốn
> bản AI thì bỏ dấu `#` ở dòng đó rồi cài lại.

---

## 4. Tạo file `.env` cho production

```bash
$ cd /var/www/stewiequiz
$ cp .env.example .env
$ nano .env
```

Bắt buộc đặt đúng các giá trị sau:

```ini
FLASK_ENV=production

# Sinh khóa mới, KHÔNG dùng lại khóa của máy dev:
#   python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=<dán chuỗi vừa sinh>

SESSION_COOKIE_SECURE=True          # đã có HTTPS -> bắt buộc True
PUBLIC_BASE_URL=https://stewiequiz.io.vn
CORS_ORIGINS=https://stewiequiz.io.vn

DB_HOST=localhost
DB_USER=stewie
DB_PASSWORD=MatKhauManh!123
DB_NAME=taocauhoiai

# Giữ nguyên các key AI / email / GIAO_VIEN_CODE như file .env cũ của bạn
```

Khóa quyền đọc file `.env` (nó chứa API key và mật khẩu DB):

```bash
$ chmod 600 .env
```

Cấp quyền cho tiến trình web ghi vào thư mục upload/log:

```bash
$ mkdir -p uploads logs static/avatars
$ sudo chown -R www-data:www-data /var/www/stewiequiz
$ sudo chmod -R 755 /var/www/stewiequiz
$ sudo chmod 600 /var/www/stewiequiz/.env
```

---

## 5. Chạy ứng dụng bằng systemd

```bash
$ sudo cp deploy/stewiequiz.service /etc/systemd/system/
$ sudo systemctl daemon-reload
$ sudo systemctl enable --now stewiequiz
$ sudo systemctl status stewiequiz          # phải thấy "active (running)"
```

Nếu lỗi, xem log:

```bash
$ sudo journalctl -u stewiequiz -n 50 --no-pager
```

---

## 6. Nginx + chứng chỉ SSL

```bash
$ sudo cp deploy/nginx-stewiequiz.conf /etc/nginx/sites-available/stewiequiz
$ sudo ln -s /etc/nginx/sites-available/stewiequiz /etc/nginx/sites-enabled/
$ sudo rm -f /etc/nginx/sites-enabled/default
$ sudo nginx -t && sudo systemctl reload nginx
```

Xin chứng chỉ (miễn phí, tự gia hạn):

```bash
$ sudo certbot --nginx -d stewiequiz.io.vn -d www.stewiequiz.io.vn
```

Chọn **redirect** (chuyển hết HTTP sang HTTPS) khi certbot hỏi.

Mở tường lửa:

```bash
$ sudo ufw allow OpenSSH
$ sudo ufw allow 'Nginx Full'
$ sudo ufw enable
```

---

## 7. Cập nhật Google OAuth

Vào <https://console.cloud.google.com> → **Credentials** → OAuth 2.0 Client ID của bạn,
thêm vào **Authorized redirect URIs**:

```
https://stewiequiz.io.vn/login/google/callback
```

Và thêm vào **Authorized JavaScript origins**:

```
https://stewiequiz.io.vn
```

> Nếu quên bước này, bấm "Đăng nhập bằng Google" sẽ báo `redirect_uri_mismatch`.

---

## 8. Dọn rác định kỳ (ảnh webcam, PDF tạm)

```bash
$ crontab -e
```

Thêm dòng (chạy 3 giờ sáng mỗi ngày):

```cron
0 3 * * * cd /var/www/stewiequiz && ./venv/bin/python scripts/cleanup.py >> logs/cleanup.log 2>&1
```

---

## 9. Kiểm tra sau khi lên

- [ ] `https://stewiequiz.io.vn` mở được, có ổ khóa HTTPS
- [ ] `https://stewiequiz.io.vn/health` trả `{"status":"ok","db":"up"}`
- [ ] Đăng nhập bằng tài khoản thường — vào được
- [ ] Đăng nhập bằng Google — vào được (kiểm tra bước 7)
- [ ] Tải lên 1 file PDF và tạo câu hỏi bằng AI — chạy tới cùng, không lỗi 504
- [ ] Tạo phòng thi → mở link `/thi/<mã>` trên **điện thoại** → camera xin quyền được
- [ ] Mở trang Giám sát trên máy tính → thấy hình webcam học sinh (kiểm tra WebSocket)
- [ ] Xuất Excel một lớp học — tải về được (kiểm tra openpyxl)

---

## Cập nhật code về sau

Chép file mới lên bằng WinSCP, rồi:

```bash
$ cd /var/www/stewiequiz
$ ./venv/bin/pip install -r requirements.txt   # nếu có thêm thư viện
$ sudo systemctl restart stewiequiz
```

## Sự cố thường gặp

| Hiện tượng | Nguyên nhân |
|---|---|
| Đăng nhập xong bị đá về trang login | `SESSION_COOKIE_SECURE=True` nhưng đang vào bằng `http://` — phải dùng `https://` |
| Google OAuth `redirect_uri_mismatch` | Chưa làm bước 7, hoặc nginx thiếu `X-Forwarded-Proto` |
| Màn hình giám sát trống | Nginx thiếu khối `location /socket.io/`, hoặc gunicorn chạy nhiều hơn 1 worker |
| Upload PDF báo lỗi 413 | Nginx thiếu `client_max_body_size 50M` |
| AI tạo câu hỏi báo 504 | Nginx thiếu `proxy_read_timeout 300s` |
| Xuất Excel lỗi 500 | Chưa cài `openpyxl` (`pip install -r requirements.txt`) |
