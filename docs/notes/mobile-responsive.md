---
name: mobile-responsive
description: Cách tổ chức responsive mobile; responsive.css là file tổng cho mọi trang base.html
metadata:
  type: project
---

Tối ưu giao diện điện thoại (2026-06-21).

- `static/css/responsive.css` là file responsive TỔNG, áp cho mọi trang dùng `base.html`. Bản cũ nhắm class navbar đã chết (`.navbar-center`, `.mobile-toggle`) nên vô dụng — đã viết lại nhắm đúng `.stewie-navbar`, `.data-table`/`.table-wrapper`, `.stat-grid`, `.chapter-row`, `.exam-opts`, hero... với breakpoint 991/768/480. base.html nạp kèm `?v=mobile1` (đổi version khi sửa để phá cache).
- **Bẫy cần nhớ:** `.table-wrapper` trong main.css đặt `overflow:hidden` -> bảng rộng bị CẮT CỤT trên mobile; responsive.css ghi đè `overflow-x:auto` ở ≤768.
- Ghi đè padding `.main-content` của base.html phải dùng selector mạnh hơn (`.main-layout > .main-content`) vì khối `<style>` trong base.html nằm SAU link responsive.css.
- Trang ĐỨNG RIÊNG (có DOCTYPE, không extends base) KHÔNG nhận responsive.css — phải thêm `@media` riêng trong từng file: đã thêm cho `lam_bai.html`, `ket_qua_thi_sinh.html`, `tham_gia_phong.html`. Các trang riêng khác: auth/* (dùng auth.css), cho_duyet, loi_phong_thi, chinh_sach, errors/error.
- `tao_de_thi.html` extends base nhưng tự có `@media (max-width:640px)` thu `.tdt-grid2/.tdt-grid4/.src-grid` về 1 cột.

Liên quan: [[ui-redesign-blue]], [[professional-hardening]].
