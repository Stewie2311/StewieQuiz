---
name: analytics-and-shuffle
description: Trang Phân tích đề thi (item analysis) + tính năng trộn câu/đáp án
metadata:
  type: project
---

Bổ sung 2025... (2026-06-21) để bài khóa luận "xịn" hơn. Phạm vi đề tài CHỈ: tạo câu hỏi/đề thi/phòng thi, chỉ dạng trắc nghiệm A/B/C/D (KHÔNG làm tự luận hay dạng câu hỏi khác).

**1. Trộn câu hỏi & đáp án (chống gian lận):**
- Backend ĐÃ CÓ SẴN từ trước: cột `phong_thi.tron_cau_hoi`/`tron_dap_an`, hàm `_sap_xep_cau_hoi()` trong `routes/thi_sinh.py` (seed = thi_sinh_id, nhất quán khi làm bài/chấm/xem lại).
- **CẬP NHẬT 2026-06-21: ĐÃ GỠ UI trộn khỏi `tao_phong.html`** (user thấy trùng — việc trộn đã làm ở bước Tạo đề wizard tạo mã đề A01/A02). Backend/cột/hàm GIỮ nguyên; form phòng không gửi checkbox nữa nên `request.form.get('tron_*')` = None → INSERT 0 (không trộn lại đè lên đề). Trộn ở wizard = mã đề cố định (in giấy/nhiều mã); trộn ở phòng = per-thí-sinh lúc thi — 2 tầng khác nhau, để online chỉ cần 1.

**2. Phân tích đề thi (Item Analysis) — tính năng MỚI:**
- Route `phong_thi.phan_tich_phong` (`/phan_tich_phong/<id>`) + template `templates/phan_tich_phong.html`.
- Dữ liệu từ bảng `bai_lam (thi_sinh_id, cau_hoi_id, is_correct)` (chỉ thí sinh da_nop_bai=1).
- Tính: độ khó p = số đúng/số lượt; độ phân cách D = %đúng nhóm 27% điểm cao − nhóm 27% điểm thấp (chia nhóm bằng Python). Phổ điểm 10 khoảng + phân loại học lực.
- Biểu đồ bằng Chart.js (CDN, trong extra_js). KPI + bảng có badge màu (dk-*/pc-* trong CSS template).
- Lối vào: nút "Phân tích" ở bảng "Phòng thi của tôi" (room-section.html) và trang `lich_su_phong.html`.

**3. Dashboard tổng quan giáo viên — ĐÃ GỠ BỎ (2026-06-21, user không thích).**
- Đã xóa: route `phong_thi.tong_quan`, file `templates/tong_quan.html`, mục "Tổng quan" trên navbar. Nếu cần làm lại: gộp theo user_id (KPI + Chart.js + top phòng).

**4. Xuất Excel + In/PDF (2026-06-21):**
- Route `phong_thi.lich_su_phong_excel` (`/lich_su_phong/<id>/excel`) và `phong_thi.phan_tich_phong_excel` (`/phan_tich_phong/<id>/excel`). Helper chung trong phong_thi.py: `_phong_co_quyen()`, `_phan_tich_du_lieu()` (đã refactor route phân tích dùng helper này), `_xuat_xlsx()`. Dùng openpyxl (đã cài). Nút "Tải Excel" + "In/PDF" (window.print + @media print) ở lich_su_phong & phan_tich_phong.

**5. Lưu nháp tự động + điều hướng câu hỏi (trong lam_bai.html, client-side):**
- localStorage key `lambai_<thi_sinh_id>` (biến JS `LB_KEY`): lưu đáp án đã chọn + cờ "xem lại"; khôi phục sau F5/mất mạng; xóa khi nộp (đã chèn removeItem trước mọi form.submit()).
- Panel "Danh sách câu" (lưới số câu, màu = đã/chưa trả lời, chấm cam = đánh dấu) + nút cờ trên mỗi câu. Toàn bộ client-side, không đụng server.

**ĐÃ CÓ SẴN, KHÔNG cần làm:** hẹn giờ tự mở/đóng phòng + đếm ngược phòng chờ (cho_duyet.html + /kiem_tra_duyet trả giay_den_gio/da_bat_dau).

**CHỜ QUYẾT ĐỊNH:** thay snapshot webcam bằng live video. Flask-SocketIO+eventlet ĐÃ CÀI nhưng CHƯA khởi tạo trong app.py (app chạy app.run/waitress đồng bộ) -> cần đổi runner nếu làm realtime.

Liên quan: [[multi-de-phong-thi]], [[navbar-menu-structure]], [[professional-hardening]].
