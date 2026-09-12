---
name: lop-management-progress
description: TIẾN ĐỘ tính năng Quản lý lớp & sinh viên (đang làm dở) - đọc trước khi làm tiếp
metadata:
  type: project
---

Tính năng "Quản lý lớp & sinh viên" — bắt đầu 2026-06-21. User ĐÃ ĐỒNG Ý làm. Phạm vi đề tài: trắc nghiệm A/B/C/D. Rủi ro đã thống nhất: chỉ "chặn nhầm" (không hỏng dữ liệu/bài thi), user tự test.

## THIẾT KẾ CHỐT
**Bảng DB mới** (script `migrate_add_lop.py`): 
- `lop`(id, ten_lop, user_id FK users, ngay_tao)
- `lop_sinh_vien`(id, lop_id FK lop, ma_so_sv, ho_ten, email, ngay_them; UNIQUE(lop_id, ma_so_sv))

**Import Excel = preview-trước-khi-lưu** (theo ý user): có nút "Tải file mẫu" + dấu "?" hướng dẫn cột (MSSV/Họ tên/Email, không phụ thuộc thứ tự) + bảng xem trước đánh dấu lỗi từng dòng (thiếu MSSV/họ tên, trùng trong file, đã có trong lớp) → chỉ lưu dòng hợp lệ.

## TRẠNG THÁI (cập nhật khi làm)
### Giai đoạn 1 — Quản lý danh sách — ✅ XONG (2026-06-21), đã verify (compile+import+parse OK)
- [x] `migrate_add_lop.py` — ĐÃ CHẠY THÀNH CÔNG 2026-06-21, bảng `lop` & `lop_sinh_vien` đã tồn tại trong DB `taocauhoiai`. (Đã sửa print bỏ emoji vì console cp1252.)
- [x] `routes/lop.py` blueprint `lop_bp` (9 route: list/tao/chi tiết/xoa/them_sv/xoa_sv/mau_excel/import_preview/import_save)
- [x] đăng ký blueprint trong app.py + import
- [x] templates `lop_hoc.html` + `lop_chi_tiet.html` (modal import preview)
- [x] nav: mục "Lớp học" (top-level nhánh GV/Admin, trước dropdown Phòng thi)
- [x] verify tĩnh OK. CHƯA test chạy thật (cần DB + đăng nhập GV).

### Giai đoạn 2 — Gắn vào phòng thi — ✅ XONG (điểm danh trước, GIỚI HẠN DỰ THI sau)
- KHÔNG dùng cột `phong_thi.lop_id`; dùng bảng nhiều-nhiều `phong_thi_lop` (migrate_add_phong_lop.py). 1 phòng gán nhiều lớp.
- Điểm danh: `_diem_danh_data()` trong routes/phong_thi.py đối chiếu MSSV roster vs thi_sinh; tab "Điểm danh" ở quan_ly_phong (có/vắng/đã nộp + xuất Excel + nhóm "ngoài danh sách").

### Giai đoạn 2b — GIỚI HẠN DỰ THI theo lớp — ✅ XONG (2026-06-24), CHƯA test chạy thật (DB off)
User chốt: khóa đối chiếu = **bắt buộc đăng nhập + MSSV** (MSSV lấy từ tài khoản, KHÔNG xét email/tên → người trùng MSSV khác email vẫn vào vì MSSV từ account, chống mạo danh vì không tự khai). Người ngoài danh sách = **cho vào nhưng ép chờ GV duyệt**.
- **Quy ước: phòng CÓ gán ≥1 lớp (`phong_thi_lop`) = "phòng giới hạn lớp"** (không thêm cột cờ). Gán lúc tạo phòng HOẶC qua gan_lop ở quản lý phòng → đều bật giới hạn. ⚠️ Phòng cũ đã gán lớp (trước đây chỉ để điểm danh) NAY thành giới hạn.
- routes/thi_sinh.py: helper `_chuan_hoa_mssv` (trim+upper), `_phong_gioi_han_lop`, `_roster_mssv`. `_ghi_danh_vao_phong(..., da_dang_nhap)`: phòng giới hạn + chưa đăng nhập → màn lỗi "phải đăng nhập"; MSSV ngoài roster → trang_thai='cho_duyet'. Sửa gate hiển thị cho_duyet.html dùng `trang_thai_ts=='cho_duyet'` (KHÔNG còn dùng phong['can_duyet'] — fix bug ép chờ duyệt mà phòng auto sẽ lọt vào thi). Chặn khách vãng lai sớm trong `tham_gia_phong`.
- routes/phong_thi.py `tao_phong`: GET nạp `lop_list`; POST đọc `lop_ids` → INSERT IGNORE phong_thi_lop (kiểm quyền sở hữu lớp).
- tao_phong.html: khối "Giới hạn theo lớp (tùy chọn)" #lopPicker checkbox name=lop_ids; bỏ trống = mở tự do.

## GHI CHÚ KỸ THUẬT
- CSRF: form HTML kèm {{ csrf_token() }}; fetch (import) gửi header `X-CSRFToken` lấy từ `<meta name="csrf-token">`.
- decorators: dùng `giao_vien_required` (cho giao_vien + admin). Lọc theo session user_id.
- openpyxl đã cài. Đọc file: `io.BytesIO(f.read())` rồi load_workbook(read_only).
- MSSV kiểu số trong Excel: chuyển float->int->str (bỏ '.0').

Liên quan: [[analytics-and-shuffle]], [[realtime-proctoring]], [[multi-de-phong-thi]].
