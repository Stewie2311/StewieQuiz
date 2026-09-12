---
name: realtime-proctoring
description: Giám sát thi bằng VIDEO TRỰC TIẾP qua Socket.IO (thay chụp ảnh lưu DB)
metadata:
  type: project
---

Thay giám sát "chụp ảnh mỗi vài giây lưu DB" bằng **stream khung hình realtime qua Socket.IO** (Cách B, 2026-06-21). Lý do chọn B thay WebRTC: chạy được khi HS thi khác mạng (đi qua server), KHÔNG cần STUN/TURN/đăng ký gì.

**Kiến trúc:**
- `extensions.py`: `socketio = SocketIO(async_mode='threading', cors_allowed_origins='*')` — threading để KHÔNG monkey-patch eventlet (tránh xung đột mysql-connector trên Windows).
- `routes/realtime.py`: `register_socketio()` với 2 sự kiện: `gv_join` (GV vào room `gv_<phong_id>`, chỉ cho admin/giao_vien) và `hs_frame` (HS gửi {phong_id, ts_id, image} → server `emit('frame')` tới room đó, KHÔNG lưu DB).
- `app.py`: `socketio.init_app(app)` + `register_socketio(socketio)` trong `_register_extensions`; runner đổi sang `socketio.run(app, ..., allow_unsafe_werkzeug=True)`.
- Quan trọng: Flask-SocketIO threading bọc `app.wsgi_app = _SocketIOMiddleware` → `/socket.io/` đi tắt ở tầng WSGI, KHÔNG qua CSRF/before_request. Không cần exempt gì.
- `lam_bai.html`: nạp socket.io client (CDN 4.7.5); `chupAnh()` đổi từ fetch POST `/giam_sat/upload` sang `gsSocket.emit('hs_frame', ...)`; tần suất 6s→2s. PHONG_ID = `{{ thi_sinh.phong_thi_id }}`.
- `giam_sat_phong.html`: nhận `frame` → đổ vào `<img id="img_<ts_id>">`; online tính theo độ tươi khung hình (<8s); `/data` (poll 4s) chỉ còn lấy danh sách + trạng thái (đã bỏ tải `anh_webcam` nặng trong route `giam_sat_data`).

**Chạy:** dev `python app.py` (có WebSocket nhờ simple-websocket đã cài); prod `python serve.py` (waitress → chạy qua long-polling, vẫn hoạt động).

**CHƯA test trực tiếp được** (cần 2 máy + webcam). **Caveat thật:** webcam (getUserMedia) chỉ chạy trên `https://` hoặc `localhost` → khi deploy cho HS thi ở nhà BẮT BUỘC dùng HTTPS, nếu không camera không bật (áp dụng cả tính năng cũ).

Route `/giam_sat/upload` cũ vẫn còn nhưng không còn được gọi. Liên quan: [[analytics-and-shuffle]], [[professional-hardening]].
