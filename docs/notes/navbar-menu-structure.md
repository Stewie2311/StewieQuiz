---
name: navbar-menu-structure
description: Navbar GV/Admin gom 3 dropdown theo luồng Tạo -> Thư viện -> Phòng thi
metadata:
  type: project
---

Cấu trúc menu navbar cho Giáo viên & Admin (chốt 2026-06-21, theo yêu cầu user gom gọn):
gom 8 trang thành **3 dropdown** theo luồng công việc Tạo -> Lưu -> Mở phòng:
- **Tạo ▾** = Tạo câu hỏi (`/?tab=exam`) + Tạo đề thi (`/tao_de_thi`)
- **Thư viện ▾** = Câu hỏi của tôi + Đề thi (lib-subnav, tab library) + Ngân hàng câu hỏi (`/ngan_hang`)
  - Đã CHUẨN HÓA tên (2026-06-21): "Câu hỏi theo môn"->"Câu hỏi của tôi"; bỏ hẳn nhãn "Thư viện chung"/"Thư viện cá nhân", kho dùng chung gọi nhất quán là "Ngân hàng câu hỏi" ở mọi trang (ngan_hang.html, admin, tao_de_thi, library-section, flash exam.py).
- **Phòng thi ▾** = Tạo phòng thi (`/tao_phong`) + Tất cả phòng thi + Phòng thi của tôi (room-subnav)

Triển khai trong `templates/components/navbar.html`:
- Đã GỘP nhánh `{% elif role=='admin' %}` và `{% else %}` (vốn giống hệt) thành 1 nhánh `{% else %}` chung — sửa menu chỉ cần sửa 1 chỗ. Học sinh vẫn nhánh riêng (Phòng Thi / Bài Đã Thi).
- Mỗi dropdown dùng class `stewie-room-hover` (mở khi hover desktop; mobile hiện dạng list tĩnh — xem [[mobile-responsive]]).
- Parent giữ `data-tab` để click parent mở tab tương ứng + tránh href="#" nhảy trang.

Liên quan: [[tao-de-thi-wizard]], [[multi-de-phong-thi]].
