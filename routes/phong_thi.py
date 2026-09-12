"""
PHÍA GIÁO VIÊN: tạo phòng thi, coi thi, chấm và tổng kết.

Đối xứng với thi_sinh.py — file kia là góc nhìn của người đi thi, file này là
góc nhìn của người tổ chức thi.

Các nhóm việc trong file (theo thứ tự xuất hiện):
  1. Tạo phòng            — chọn đề (được nhiều mã đề), đặt giờ, đặt mật khẩu.
  2. Điểm danh theo lớp   — đối chiếu MSSV trong lớp với người thực sự vào thi,
                            để biết ai vắng.
  3. Trang quản lý phòng  — bảng điều khiển lúc đang coi thi.
  4. Duyệt / từ chối      — xử lý hàng chờ thí sinh xin vào.
  5. Đóng / xóa phòng.
  6. Lịch sử & phân tích  — sau khi thi xong: bảng điểm, phổ điểm, câu nào cả
                            lớp làm sai.
  7. Giám sát webcam      — xem hình trực tiếp (phần realtime nằm ở realtime.py).

MỘT PHÒNG CÓ THỂ CÓ NHIỀU MÃ ĐỀ. Đây là điều dễ quên nhất khi đọc file này:
quan hệ phòng–đề nằm ở bảng phong_thi_de_thi (nhiều-nhiều), còn cột
phong_thi.de_thi_id chỉ là mã đề "đại diện" giữ lại cho các phòng đời cũ. Muốn
lấy đủ đề của một phòng thì luôn dùng `_bo_de_cua_phong`, đừng đọc thẳng cột đó.
"""
import os
import re
import io
import random
import string
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request, Response,
                   redirect, session, flash, url_for, jsonify, current_app)
from database import get_db_connection
from decorators import login_required, giao_vien_required

phong_thi_bp = Blueprint('phong_thi', __name__)

# Địa chỉ công khai để dựng link mời vào thi. Chạy sau ngrok/dev tunnel thì phải
# đặt biến này, vì lúc đó host mà Flask nhìn thấy là localhost — link sinh ra sẽ
# chỉ mở được trên chính máy chủ. Để trống thì lấy theo host của request.
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')


def _ma_de_ngan(ten_de_thi):
    """Rút gọn 'Tên (... - Mã đề A02)' -> 'A02' để hiển thị nhãn mã đề."""
    if not ten_de_thi:
        return '—'
    m = re.search(r'Mã đề\s*([A-Za-z0-9]+)', ten_de_thi)
    return m.group(1) if m else (ten_de_thi[:18] + ('…' if len(ten_de_thi) > 18 else ''))


def _dong_neu_het_gio(cursor, conn, phong):
    """Quá giờ kết thúc thì tự đóng phòng. Sửa dict `phong` tại chỗ.

    Hệ thống KHÔNG có tiến trình nền chạy định kỳ. Thay vào đó, mỗi lần có ai
    chạm vào phòng thì tiện thể kiểm tra luôn xem nó hết giờ chưa. Cách này đủ
    dùng và không phải nuôi thêm một dịch vụ chạy ngầm — đổi lại, phòng hết giờ
    mà không ai ngó tới thì trạng thái trong CSDL vẫn là "đang thi" cho tới lần
    truy cập kế tiếp.
    """
    if not phong or phong.get('trang_thai') == 'da_dong':
        return phong
    kt = phong.get('thoi_gian_ket_thuc')
    if kt and isinstance(kt, datetime) and datetime.now() > kt:
        cursor.execute(
            "UPDATE phong_thi SET trang_thai='da_dong' WHERE id=%s",
            (phong['id'],)
        )
        conn.commit()
        phong['trang_thai'] = 'da_dong'
    return phong


def generate_ma_phong():
    """Mã phòng ngẫu nhiên kiểu STEWIE-A3F9K2 (bản cũ, giữ lại để tương thích)."""
    chars = string.ascii_uppercase + string.digits
    return 'STEWIE-' + ''.join(random.choices(chars, k=6))


def sinh_ma_phong_duy_nhat(cursor):
    """Mã phòng dễ đọc: "STEWIE - Phòng 7", đánh số tăng dần.

    Lấy số lớn nhất đang có rồi +1, thay vì đếm số phòng. Đếm thì sau khi xóa
    một phòng, số cũ sẽ được cấp lại — mà mã phòng chính là địa chỉ link
    /thi/<mã> nên trùng mã là link cũ dẫn nhầm sang phòng mới.
    """
    cursor.execute("SELECT ma_phong FROM phong_thi")
    max_n = 0
    for r in cursor.fetchall():
        mp = r['ma_phong'] if isinstance(r, dict) else r[0]
        m = re.match(r'^STEWIE - Phòng (\d+)$', mp or '')
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f'STEWIE - Phòng {max_n + 1}'


def _bo_de_cua_phong(cursor, phong_id, phong_de_thi_id=None):
    """Danh sách mã đề của một phòng — LUÔN dùng hàm này, đừng đọc thẳng cột.

    Nguồn thật là bảng phong_thi_de_thi. Phòng tạo từ trước khi có tính năng
    nhiều mã đề thì bảng đó rỗng, nên rơi về cột de_thi_id đại diện.
    """
    cursor.execute(
        "SELECT de_thi_id FROM phong_thi_de_thi WHERE phong_thi_id=%s ORDER BY de_thi_id",
        (phong_id,)
    )
    ids = [r['de_thi_id'] for r in cursor.fetchall()]
    if not ids and phong_de_thi_id:
        ids = [phong_de_thi_id]
    return ids


# ==========================================
# TẠO PHÒNG THI
#
# Hai lối vào cùng một hàm:
#   /tao_phong            — từ tab Quản lý phòng, chưa chọn đề nào.
#   /tao_phong/<exam_id>  — từ một đề trong Thư viện, đề đó được tích sẵn.
# ==========================================
@phong_thi_bp.route('/tao_phong', methods=['GET', 'POST'])
@phong_thi_bp.route('/tao_phong/<int:exam_id>', methods=['GET'])
@giao_vien_required
def tao_phong(exam_id=None):
    """Hiện form tạo phòng (GET) hoặc tạo phòng thật (POST).

    Bốn điều kiện để một phòng hợp lệ, kiểm tra hết ở nhánh POST:
      - Chọn ít nhất một đề.
      - Đề phải thuộc loại 'de_thi', không phải bộ câu hỏi trong thư viện.
      - Đề phải là của mình (admin thì miễn).
      - Nhiều mã đề trong cùng phòng thì phải CÙNG MỘT MÔN.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Thư viện có hai loại bản ghi: 'cau_hoi' (bộ câu hỏi rời do AI sinh) và
    # 'de_thi' (đề đã đóng gói, có cấu trúc và thời lượng). Chỉ loại sau mới mở
    # phòng thi được.
    if session.get('role') == 'admin':
        cursor.execute("""
            SELECT d.id, d.ten_de_thi, d.tong_so_cau, d.loai,
                   d.hoc_phan_id AS mon_hoc_id, d.thoi_gian, m.ten_hoc_phan AS ten_mon
            FROM de_thi d JOIN hoc_phan m ON d.hoc_phan_id=m.id
            WHERE d.loai='de_thi'
            ORDER BY d.ngay_tao DESC
        """)
    else:
        cursor.execute("""
            SELECT d.id, d.ten_de_thi, d.tong_so_cau, d.loai,
                   d.hoc_phan_id AS mon_hoc_id, d.thoi_gian, m.ten_hoc_phan AS ten_mon
            FROM de_thi d JOIN hoc_phan m ON d.hoc_phan_id=m.id
            WHERE d.user_id=%s AND d.loai='de_thi'
            ORDER BY d.ngay_tao DESC
        """, (session['user_id'],))
    de_thi_list = cursor.fetchall()

    if request.method == 'POST':
        raw_ids = request.form.getlist('de_thi_ids')
        ids = [int(x) for x in raw_ids if x.isdigit()]
        if not ids:
            flash('Vui lòng chọn ít nhất một đề thi cho phòng!', 'warning')
            cursor.close(); conn.close()
            return redirect(url_for('phong_thi.tao_phong'))

        # Đọc lại các đề TỪ CSDL để kiểm tra, không tin danh sách id gửi lên.
        # Người dùng sửa được HTML để tích cả đề của giáo viên khác.
        fmt = ','.join(['%s'] * len(ids))
        cursor.execute(
            f"SELECT id, hoc_phan_id AS mon_hoc_id, user_id, loai FROM de_thi WHERE id IN ({fmt})", ids
        )
        rows = cursor.fetchall()
        if len(rows) != len(set(ids)):
            flash('Một số đề không tồn tại!', 'danger')
            cursor.close(); conn.close()
            return redirect(url_for('phong_thi.tao_phong'))
        if any(r['loai'] != 'de_thi' for r in rows):
            flash('Chỉ được chọn từ Đề thi, không dùng thư viện Câu hỏi để mở phòng!', 'warning')
            cursor.close(); conn.close()
            return redirect(url_for('phong_thi.tao_phong'))
        if session.get('role') != 'admin' and any(r['user_id'] != session['user_id'] for r in rows):
            flash('Bạn không có quyền dùng một số đề đã chọn!', 'danger')
            cursor.close(); conn.close()
            return redirect(url_for('phong_thi.tao_phong'))
        # Trộn đề của hai môn vào một phòng thì thí sinh bốc trúng mã đề nào là
        # thi môn đó — vô lý, nên chặn.
        if len({r['mon_hoc_id'] for r in rows}) > 1:
            flash('Các mã đề trong cùng một phòng phải thuộc CÙNG một môn học!', 'warning')
            cursor.close(); conn.close()
            return redirect(url_for('phong_thi.tao_phong'))

        try:
            # Mã phòng do hệ thống sinh, giáo viên không phải nghĩ ra. Form đã
            # hiện sẵn một mã, nhưng vẫn kiểm lại phòng khác có chiếm mất chưa
            # (giáo viên mở form rồi để đó, lát sau mới bấm lưu).
            ma_phong = request.form.get('ma_phong', '').strip()
            if ma_phong:
                cursor.execute("SELECT 1 FROM phong_thi WHERE ma_phong=%s", (ma_phong,))
                if cursor.fetchone():
                    ma_phong = ''
            if not ma_phong:
                ma_phong = sinh_ma_phong_duy_nhat(cursor)
            # Mật khẩu là lớp bảo vệ TÙY CHỌN, hoàn toàn tách khỏi mã phòng và
            # được phép trùng giữa các phòng. Bỏ trống thì phòng vào tự do.
            mat_khau = (request.form.get('mat_khau') or '').strip() or None

            thoi_luong = int(request.form.get('thoi_luong_lam_bai', 45))
            tg_bat_dau = request.form.get('thoi_gian_bat_dau') or None

            # Giờ kết thúc không nhập tay mà suy ra: bắt đầu + thời lượng. Để
            # giáo viên nhập cả hai thì sớm muộn cũng có phòng "thi 45 phút
            # nhưng đóng sau 20 phút".
            tg_ket_thuc = None
            if tg_bat_dau:
                try:
                    bd = datetime.strptime(tg_bat_dau[:16], '%Y-%m-%dT%H:%M')
                    tg_ket_thuc = (bd + timedelta(minutes=thoi_luong)
                                   ).strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    tg_ket_thuc = None

            cursor.execute("""
                INSERT INTO phong_thi
                (user_id, ten_phong, ma_phong, mat_khau, mo_ta,
                 thoi_gian_mo_phong, thoi_gian_bat_dau, thoi_gian_ket_thuc, thoi_luong_lam_bai,
                 so_lan_thi_toi_da, tron_cau_hoi, tron_dap_an,
                 hien_thi_dap_an, can_duyet)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                session['user_id'],
                request.form.get('ten_phong'),
                ma_phong,
                mat_khau,
                request.form.get('mo_ta', ''),
                request.form.get('thoi_gian_mo_phong') or None,
                tg_bat_dau,
                tg_ket_thuc,
                thoi_luong,
                int(request.form.get('so_lan_thi_toi_da', 1)),
                1 if request.form.get('tron_cau_hoi')    else 0,
                1 if request.form.get('tron_dap_an')     else 0,
                int(request.form.get('hien_thi_dap_an', 1)),
                1 if request.form.get('can_duyet') == '1' else 0,
            ))
            phong_id = cursor.lastrowid

            cursor.executemany(
                "INSERT INTO phong_thi_de_thi (phong_thi_id, de_thi_id) VALUES (%s,%s)",
                [(phong_id, de_id) for de_id in ids]
            )

            # Gán lớp cho phòng là TÙY CHỌN, nhưng hệ quả thì lớn: phòng lập tức
            # trở thành "phòng giới hạn" — bắt buộc đăng nhập, và ai không có
            # tên trong lớp thì phải chờ duyệt. Xem thi_sinh.py để biết chi tiết.
            raw_lop = request.form.getlist('lop_ids')
            lop_ids = [int(x) for x in raw_lop if x.isdigit()]
            if lop_ids:
                fmt_l = ','.join(['%s'] * len(lop_ids))
                # Lớp là tài nguyên dùng chung: giáo viên gán được cả lớp của
                # đồng nghiệp (dạy thay, coi thi hộ là chuyện thường).
                cursor.execute(
                    f"SELECT id FROM lop WHERE id IN ({fmt_l})",
                    lop_ids
                )
                lop_hop_le = [r['id'] for r in cursor.fetchall()]
                if lop_hop_le:
                    cursor.executemany(
                        "INSERT IGNORE INTO phong_thi_lop (phong_thi_id, lop_id) VALUES (%s,%s)",
                        [(phong_id, lid) for lid in lop_hop_le]
                    )
            conn.commit()
            so_de = len(ids)
            flash(f'Tạo phòng thành công! Mã phòng: {ma_phong}'
                  + (f' · {so_de} mã đề (phát ngẫu nhiên cho mỗi HS)' if so_de > 1 else ''),
                  'success')
            cursor.close(); conn.close()
            return redirect(f'/quan_ly_phong/{phong_id}')

        except Exception as e:
            conn.rollback()
            flash(f'Lỗi: {str(e)}', 'danger')
            cursor.close(); conn.close()
            return redirect(url_for('phong_thi.tao_phong'))

    # ----- Nhánh GET: chuẩn bị dữ liệu cho form -----
    cursor.execute("""
        SELECT l.id, l.ten_lop,
               (SELECT COUNT(*) FROM lop_sinh_vien sv WHERE sv.lop_id=l.id) AS si_so
        FROM lop l ORDER BY l.ten_lop
    """)
    lop_list = cursor.fetchall()

    # Sinh sẵn mã phòng để hiện (và cho bấm sao chép link) ngay trên form, trước
    # cả khi giáo viên bấm lưu.
    ma_phong_moi = sinh_ma_phong_duy_nhat(cursor)

    cursor.close()
    conn.close()
    preselect_ids = [exam_id] if exam_id else []

    if request.args.get('partial'):
        return render_template('tao_phong_inner.html',
                               de_thi_list=de_thi_list,
                               lop_list=lop_list,
                               preselect_ids=preselect_ids,
                               ma_phong_moi=ma_phong_moi)

    return render_template('tao_phong.html',
                           de_thi_list=de_thi_list,
                           lop_list=lop_list,
                           preselect_ids=preselect_ids,
                           ma_phong_moi=ma_phong_moi)


# ==========================================
# ĐIỂM DANH
#
# Ghép hai danh sách lại với nhau bằng MSSV:
#   danh sách lớp (ai LẼ RA phải thi) × thí sinh trong phòng (ai THỰC SỰ đã vào)
#
# Kết quả cho ra hai nhóm mà giáo viên cần thấy:
#   - Sinh viên có trong lớp mà không thấy vào phòng  -> VẮNG.
#   - Người vào phòng mà không có trong lớp nào       -> "ngoài danh sách"
#     (thi lại, học ghép, hoặc gõ nhầm MSSV).
# ==========================================
_DD_NHAN = {
    'da_nop'   : ('Đã nộp',     'badge bg-success'),
    'dang_thi' : ('Đang thi',   'badge bg-warning text-dark'),
    'cho_duyet': ('Chờ duyệt',  'badge text-white', '#d97706'),
    'tu_choi'  : ('Bị từ chối', 'badge bg-secondary'),
    'co_mat'   : ('Có mặt',     'badge bg-info text-dark'),
    'vang'     : ('Vắng',       'badge bg-danger'),
}


def _diem_danh_data(cursor, phong_id):
    """Dựng bảng điểm danh cho một phòng.

    Trả về dict gồm: các lớp đã gán, `roster` (từng sinh viên trong lớp kèm
    trạng thái đã nộp / đang thi / vắng...), số liệu tổng hợp, và `ngoai_ds`
    (người vào thi nhưng không thuộc lớp nào đã gán).
    """
    cursor.execute("""
        SELECT l.id, l.ten_lop,
               (SELECT COUNT(*) FROM lop_sinh_vien sv WHERE sv.lop_id=l.id) AS si_so
        FROM phong_thi_lop ptl
        JOIN lop l ON ptl.lop_id = l.id
        WHERE ptl.phong_thi_id=%s
        ORDER BY l.ten_lop
    """, (phong_id,))
    lop_da_gan = cursor.fetchall()
    lop_ids = [l['id'] for l in lop_da_gan]

    # Dựng bảng tra MSSV -> thí sinh, để bước sau dò từng người trong lớp mà
    # không phải quét lại cả danh sách thí sinh mỗi lần.
    cursor.execute("""
        SELECT id, ho_ten, ma_so_sv, trang_thai, da_nop_bai, diem
        FROM thi_sinh WHERE phong_thi_id=%s
    """, (phong_id,))
    ts_rows = cursor.fetchall()
    ts_theo_mssv = {}
    for t in ts_rows:
        key = (t['ma_so_sv'] or '').strip()
        if key:
            ts_theo_mssv[key] = t

    roster = []
    da_khop_ids = set()
    if lop_ids:
        fmt = ','.join(['%s'] * len(lop_ids))
        cursor.execute(f"""
            SELECT sv.ma_so_sv, sv.ho_ten, l.ten_lop
            FROM lop_sinh_vien sv JOIN lop l ON sv.lop_id=l.id
            WHERE sv.lop_id IN ({fmt})
            ORDER BY l.ten_lop, sv.ma_so_sv
        """, lop_ids)
        for sv in cursor.fetchall():
            mssv = (sv['ma_so_sv'] or '').strip()
            ts = ts_theo_mssv.get(mssv)
            if not ts:
                tt = 'vang'
            elif ts['da_nop_bai']:
                tt = 'da_nop'
            elif ts['trang_thai'] == 'da_duyet':
                tt = 'dang_thi'
            elif ts['trang_thai'] == 'cho_duyet':
                tt = 'cho_duyet'
            elif ts['trang_thai'] == 'bi_tu_choi':
                tt = 'tu_choi'
            else:
                tt = 'co_mat'
            if ts:
                da_khop_ids.add(ts['id'])
            nhan = _DD_NHAN[tt]
            roster.append({
                'ma_so_sv': mssv, 'ho_ten': sv['ho_ten'], 'ten_lop': sv['ten_lop'],
                'trang_thai': tt, 'nhan': nhan[0], 'badge_class': nhan[1],
                'badge_color': nhan[2] if len(nhan) > 2 else None,
                'co_mat': tt != 'vang',
                'diem': ts['diem'] if ts and ts['da_nop_bai'] else None,
            })

    # Ai vào thi mà không khớp được với sinh viên nào trong lớp. Không coi là
    # gian lận — thường là sinh viên thi lại, học ghép, hoặc gõ sai MSSV. Giáo
    # viên nhìn danh sách này và tự quyết.
    ngoai_ds = [{
        'ho_ten': t['ho_ten'], 'ma_so_sv': t['ma_so_sv'] or '',
        'da_nop': bool(t['da_nop_bai']),
        'diem': t['diem'] if t['da_nop_bai'] else None,
    } for t in ts_rows if t['id'] not in da_khop_ids]

    tong = len(roster)
    co_mat = sum(1 for r in roster if r['co_mat'])
    da_nop = sum(1 for r in roster if r['trang_thai'] == 'da_nop')
    return {
        'lop_da_gan': lop_da_gan,
        'roster': roster,
        'ngoai_ds': ngoai_ds,
        'tong': tong, 'co_mat': co_mat, 'vang': tong - co_mat, 'da_nop': da_nop,
    }


# ==========================================
# TRANG QUẢN LÝ PHÒNG (bảng điều khiển lúc đang coi thi)
# ==========================================
@phong_thi_bp.route('/quan_ly_phong/<int:phong_id>')
@login_required
def quan_ly_phong(phong_id):
    """Trang giáo viên mở trong lúc coi thi.

    Gom mọi thứ cần theo dõi vào một chỗ: danh sách thí sinh và mã đề từng
    người, hàng chờ duyệt, bảng điểm danh, số liệu nhanh, và link mời vào phòng.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT p.*, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS de_thi_id,
               d.ten_de_thi, d.tong_so_cau, m.ten_hoc_phan AS ten_mon
        FROM phong_thi p
        JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
        JOIN hoc_phan m ON d.hoc_phan_id=m.id
        WHERE p.id=%s
    """, (phong_id,))
    phong = cursor.fetchone()

    if not phong:
        flash('Không tìm thấy!', 'danger')
        cursor.close(); conn.close()
        return redirect('/')
    if (session.get('role') != 'admin' and
            phong['user_id'] != session['user_id']):
        flash('Bạn không có quyền!', 'danger')
        cursor.close(); conn.close()
        return redirect('/')

    # Gọi trước khi định dạng giờ thành chuỗi, vì hàm này có thể đổi trang thái
    # phòng thành 'da_dong' và giao diện cần thấy trạng thái mới nhất.
    _dong_neu_het_gio(cursor, conn, phong)

    cursor.execute("""
        SELECT d.id, d.ten_de_thi FROM phong_thi_de_thi ptd
        JOIN de_thi d ON ptd.de_thi_id = d.id
        WHERE ptd.phong_thi_id=%s ORDER BY d.ten_de_thi
    """, (phong_id,))
    bo_de = cursor.fetchall()
    if not bo_de:
        bo_de = [{'id': phong['de_thi_id'], 'ten_de_thi': phong['ten_de_thi']}]
    for d in bo_de:
        d['ma_de'] = _ma_de_ngan(d['ten_de_thi'])

    # Kèm mã đề ĐÃ PHÁT cho từng thí sinh — giáo viên cần biết ai đang làm đề
    # nào khi có thắc mắc hoặc khiếu nại về câu hỏi.
    cursor.execute("""
        SELECT ts.*, d.ten_de_thi AS de_assigned
        FROM thi_sinh ts
        LEFT JOIN de_thi d ON ts.de_thi_id = d.id
        WHERE ts.phong_thi_id=%s ORDER BY ts.ngay_tao DESC
    """, (phong_id,))
    thi_sinhs = cursor.fetchall()
    for t in thi_sinhs:
        t['ma_de'] = _ma_de_ngan(t.get('de_assigned')) if t.get('de_thi_id') else None

    # Tách riêng danh sách thí sinh đang CHỜ DUYỆT
    cho_duyet_list = [t for t in thi_sinhs if t['trang_thai'] == 'cho_duyet']

    cursor.execute("""
        SELECT
            COUNT(*) as tong,
            SUM(CASE WHEN trang_thai='cho_duyet' THEN 1 ELSE 0 END) as cho_duyet,
            SUM(CASE WHEN trang_thai='da_duyet'  THEN 1 ELSE 0 END) as da_duyet,
            SUM(CASE WHEN da_nop_bai=1           THEN 1 ELSE 0 END) as da_nop,
            AVG(CASE WHEN da_nop_bai=1 THEN diem END) as diem_tb
        FROM thi_sinh WHERE phong_thi_id=%s
    """, (phong_id,))
    stats = cursor.fetchone()

    diem_danh = _diem_danh_data(cursor, phong_id)
    da_gan_ids = {l['id'] for l in diem_danh['lop_da_gan']}
    # Bỏ các lớp đã gán ra khỏi ô "gán thêm lớp", để không hiện lựa chọn thừa.
    cursor.execute("""
        SELECT l.id, l.ten_lop,
               (SELECT COUNT(*) FROM lop_sinh_vien sv WHERE sv.lop_id=l.id) AS si_so
        FROM lop l ORDER BY l.ten_lop
    """)
    lop_co_the_gan = [l for l in cursor.fetchall() if l['id'] not in da_gan_ids]

    cursor.close()
    conn.close()

    # Đổi datetime thành chuỗi hiển thị. Bọc try vì các phòng cũ có thể đã lưu
    # sẵn chuỗi ở cột này — gọi .strftime() lên chuỗi sẽ ném AttributeError.
    if phong.get('thoi_gian_mo_phong'):
        try:
            phong['thoi_gian_mo_phong'] = phong['thoi_gian_mo_phong'].strftime('%H:%M - %d/%m/%Y')
        except AttributeError:
            pass

    if phong.get('thoi_gian_bat_dau'):
        try:
            phong['thoi_gian_bat_dau'] = phong['thoi_gian_bat_dau'].strftime('%H:%M - %d/%m/%Y')
        except AttributeError:
            pass

    base_url = PUBLIC_BASE_URL or request.host_url.rstrip('/')
    link_tham_gia = base_url + f'/thi/{phong["ma_phong"]}'
    # ?frag=1 -> dựng trang bằng layout rỗng (không navbar/footer), dùng khi
    # giao diện nạp trang này bằng AJAX vào trong một tab có sẵn.
    layout = 'layouts/_bare.html' if request.args.get('frag') else 'base.html'
    return render_template('quan_ly_phong.html',
                           phong=phong, thi_sinhs=thi_sinhs,
                           cho_duyet_list=cho_duyet_list, bo_de=bo_de,
                           stats=stats, link_tham_gia=link_tham_gia,
                           diem_danh=diem_danh, lop_co_the_gan=lop_co_the_gan,
                           layout=layout)


# ==========================================
# GÁN / BỎ LỚP KHỎI PHÒNG
#
# Nhớ: gán lớp đầu tiên vào phòng cũng đồng thời KHÓA phòng lại (bắt đăng nhập,
# người ngoài lớp phải chờ duyệt). Bỏ hết lớp thì phòng mở lại như cũ.
# ==========================================
def _phong_owner(cursor, phong_id):
    """Kiểm tra quyền sửa phòng. (user_id, None) hoặc (None, 'notfound'|'forbidden')."""
    cursor.execute("SELECT user_id FROM phong_thi WHERE id=%s", (phong_id,))
    p = cursor.fetchone()
    if not p:
        return None, 'notfound'
    if session.get('role') != 'admin' and p['user_id'] != session['user_id']:
        return None, 'forbidden'
    return p['user_id'], None


@phong_thi_bp.route('/quan_ly_phong/<int:phong_id>/gan_lop', methods=['POST'])
@giao_vien_required
def gan_lop(phong_id):
    """Gán một lớp vào phòng để điểm danh (và giới hạn dự thi)."""
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    try:
        owner, err = _phong_owner(cursor, phong_id)
        if err:
            flash('Không tìm thấy phòng!' if err == 'notfound' else 'Không có quyền!', 'danger')
            return redirect('/?tab=room')
        lop_id = request.form.get('lop_id', '')
        if not lop_id.isdigit():
            flash('Vui lòng chọn lớp để gán!', 'warning')
            return redirect(f'/quan_ly_phong/{phong_id}?tab=diemdanh')
        lop_id = int(lop_id)
        cursor.execute("SELECT ten_lop FROM lop WHERE id=%s", (lop_id,))
        lop = cursor.fetchone()
        if not lop:
            flash('Lớp không hợp lệ!', 'danger')
            return redirect(f'/quan_ly_phong/{phong_id}?tab=diemdanh')
        cursor.execute("""INSERT IGNORE INTO phong_thi_lop (phong_thi_id, lop_id)
                          VALUES (%s,%s)""", (phong_id, lop_id))
        conn.commit()
        flash(f'Đã gán lớp "{lop["ten_lop"]}" vào phòng để điểm danh.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Lỗi khi gán lớp: {str(e)}', 'danger')
    finally:
        cursor.close(); conn.close()
    return redirect(f'/quan_ly_phong/{phong_id}?tab=diemdanh')


@phong_thi_bp.route('/quan_ly_phong/<int:phong_id>/bo_lop/<int:lop_id>', methods=['POST'])
@giao_vien_required
def bo_lop(phong_id, lop_id):
    """Bỏ một lớp khỏi phòng. Bỏ hết lớp thì phòng trở lại mở tự do."""
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    try:
        owner, err = _phong_owner(cursor, phong_id)
        if err:
            flash('Không tìm thấy phòng!' if err == 'notfound' else 'Không có quyền!', 'danger')
            return redirect('/?tab=room')
        cursor.execute("DELETE FROM phong_thi_lop WHERE phong_thi_id=%s AND lop_id=%s",
                       (phong_id, lop_id))
        conn.commit()
        flash('Đã bỏ gán lớp khỏi phòng.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Lỗi: {str(e)}', 'danger')
    finally:
        cursor.close(); conn.close()
    return redirect(f'/quan_ly_phong/{phong_id}?tab=diemdanh')


@phong_thi_bp.route('/quan_ly_phong/<int:phong_id>/xoa_thi_sinh/<int:thi_sinh_id>', methods=['POST'])
@giao_vien_required
def xoa_thi_sinh(phong_id, thi_sinh_id):
    """Xóa 1 thí sinh khỏi phòng (bai_lam & giam_sat tự xóa theo ON DELETE CASCADE)."""
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    try:
        owner, err = _phong_owner(cursor, phong_id)
        if err:
            flash('Không tìm thấy phòng!' if err == 'notfound' else 'Không có quyền!', 'danger')
            return redirect('/?tab=room')
        cursor.execute("DELETE FROM thi_sinh WHERE id=%s AND phong_thi_id=%s",
                       (thi_sinh_id, phong_id))
        conn.commit()
        if cursor.rowcount:
            flash('Đã xóa thí sinh khỏi phòng.', 'success')
        else:
            flash('Không tìm thấy thí sinh trong phòng này.', 'warning')
    except Exception as e:
        conn.rollback()
        flash(f'Lỗi khi xóa thí sinh: {str(e)}', 'danger')
    finally:
        cursor.close(); conn.close()
    return redirect(f'/quan_ly_phong/{phong_id}')


@phong_thi_bp.route('/quan_ly_phong/<int:phong_id>/diem_danh/excel')
@login_required
def diem_danh_excel(phong_id):
    """Xuất bảng điểm danh ra Excel để nộp cho khoa/phòng đào tạo."""
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    phong, err = _phong_co_quyen(cursor, phong_id)
    if err:
        return _err_redirect(conn, cursor, err)
    dd = _diem_danh_data(cursor, phong_id)
    cursor.close(); conn.close()
    rows = [[i, r['ma_so_sv'], r['ho_ten'], r['ten_lop'], r['nhan'],
             float(r['diem']) if r['diem'] is not None else '']
            for i, r in enumerate(dd['roster'], start=1)]
    return _xuat_xlsx(
        'Diem danh',
        f'ĐIỂM DANH - {phong["ten_phong"]}',
        [f'Môn: {phong["ten_mon"]} | Sĩ số: {dd["tong"]} | Có mặt: {dd["co_mat"]} | '
         f'Vắng: {dd["vang"]} | Đã nộp: {dd["da_nop"]}'],
        ['STT', 'MSSV', 'Họ và tên', 'Lớp', 'Trạng thái', 'Điểm'],
        rows, [6, 16, 26, 18, 14, 9],
        f'diem_danh_phong_{phong_id}.xlsx')


# ==========================================
# HÀNG CHỜ DUYỆT
#
# Thí sinh rơi vào hàng chờ khi phòng bật "cần duyệt", hoặc khi họ không có tên
# trong lớp đã gán. Có hai bộ route làm cùng một việc:
#   duyet_thi_sinh — bấm nút, tải lại trang. Dùng khi JavaScript không chạy.
#   duyet_ajax     — bấm nút, không tải lại trang. Đường dùng thường ngày.
#   cho_duyet_data — trả JSON danh sách đang chờ; trang quản lý hỏi lại liên
#                    tục để hàng chờ tự hiện thêm người mới.
# ==========================================
@phong_thi_bp.route('/duyet_thi_sinh/<int:thi_sinh_id>/<action>', methods=['POST'])
@login_required
def duyet_thi_sinh(thi_sinh_id, action):
    """Duyệt hoặc từ chối một thí sinh, rồi quay lại trang quản lý phòng."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # JOIN sang phong_thi để lấy luôn chủ phòng: quyền duyệt thuộc về người
        # TẠO PHÒNG, không phải bất kỳ giáo viên nào biết id thí sinh.
        cursor.execute("""
            SELECT ts.*, p.user_id as gv_id, p.id as phong_id
            FROM thi_sinh ts
            JOIN phong_thi p ON ts.phong_thi_id=p.id
            WHERE ts.id=%s
        """, (thi_sinh_id,))
        ts = cursor.fetchone()

        if not ts:
            flash('Không tìm thấy!', 'danger')
            return redirect('/')
        if (session.get('role') != 'admin' and
                ts['gv_id'] != session['user_id']):
            flash('Bạn không có quyền!', 'danger')
            return redirect('/')

        if action == 'duyet':
            cursor.execute(
                "UPDATE thi_sinh SET trang_thai='da_duyet' WHERE id=%s",
                (thi_sinh_id,)
            )
            flash(f'✅ Đã duyệt: {ts["ho_ten"]}', 'success')
        elif action == 'tu_choi':
            cursor.execute(
                "UPDATE thi_sinh SET trang_thai='bi_tu_choi' WHERE id=%s",
                (thi_sinh_id,)
            )
            flash(f'❌ Đã từ chối: {ts["ho_ten"]}', 'warning')

        conn.commit()
        return redirect(f'/quan_ly_phong/{ts["phong_id"]}')

    except Exception as e:
        flash(f'Lỗi: {str(e)}', 'danger')
        return redirect('/')
    finally:
        cursor.close()
        conn.close()


@phong_thi_bp.route('/quan_ly_phong/<int:phong_id>/cho_duyet_data')
@login_required
def cho_duyet_data(phong_id):
    """Danh sách thí sinh đang chờ duyệt (JSON).

    Trang quản lý phòng gọi lại đường này vài giây một lần, nên giáo viên thấy
    người mới xin vào ngay mà không phải bấm F5 giữa lúc coi thi.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT user_id FROM phong_thi WHERE id=%s", (phong_id,)
        )
        phong = cursor.fetchone()
        if not phong:
            return jsonify({'ok': False}), 404
        if (session.get('role') != 'admin' and
                phong['user_id'] != session['user_id']):
            return jsonify({'ok': False}), 403

        cursor.execute("""
            SELECT id, ho_ten, ma_so_sv, lop, email, ngay_tao
            FROM thi_sinh
            WHERE phong_thi_id=%s AND trang_thai='cho_duyet'
            ORDER BY ngay_tao ASC
        """, (phong_id,))
        rows = cursor.fetchall()
        for r in rows:
            gv = r.pop('ngay_tao', None)
            try:
                r['gio_vao'] = gv.strftime('%H:%M') if gv else ''
            except AttributeError:
                r['gio_vao'] = str(gv) if gv else ''
    finally:
        cursor.close()
        conn.close()
    return jsonify({'ok': True, 'cho_duyet': rows})


@phong_thi_bp.route('/duyet_ajax/<int:thi_sinh_id>/<action>', methods=['POST'])
@login_required
def duyet_ajax(thi_sinh_id, action):
    """Duyệt/từ chối không tải lại trang — đường dùng thường ngày lúc coi thi."""
    if action not in ('duyet', 'tu_choi'):
        return jsonify({'ok': False}), 400

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT ts.ho_ten, p.user_id AS gv_id
            FROM thi_sinh ts
            JOIN phong_thi p ON ts.phong_thi_id=p.id
            WHERE ts.id=%s
        """, (thi_sinh_id,))
        ts = cursor.fetchone()
        if not ts:
            return jsonify({'ok': False}), 404
        if (session.get('role') != 'admin' and
                ts['gv_id'] != session['user_id']):
            return jsonify({'ok': False}), 403

        moi = 'da_duyet' if action == 'duyet' else 'bi_tu_choi'
        cursor.execute(
            "UPDATE thi_sinh SET trang_thai=%s WHERE id=%s",
            (moi, thi_sinh_id)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        current_app.logger.exception('Lỗi khi duyệt thí sinh (AJAX)')
        return jsonify({'ok': False, 'loi': 'Có lỗi xảy ra, vui lòng thử lại.'}), 500
    finally:
        cursor.close()
        conn.close()
    return jsonify({'ok': True, 'ho_ten': ts['ho_ten']})


# ==========================================
# ĐÓNG / XÓA PHÒNG
#
# Khác nhau hoàn toàn:
#   Đóng — dừng nhận thí sinh và dừng nộp bài, nhưng dữ liệu còn nguyên. Đây là
#          việc làm khi hết giờ thi. Đóng xong thí sinh mới được xem lại bài.
#   Xóa  — bay sạch cả phòng lẫn bài làm của thí sinh, không lấy lại được.
# ==========================================
@phong_thi_bp.route('/dong_phong/<int:phong_id>', methods=['POST'])
@login_required
def dong_phong(phong_id):
    """Đóng phòng: hết nhận người, hết nộp bài. Dữ liệu vẫn còn."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM phong_thi WHERE id=%s", (phong_id,)
        )
        phong = cursor.fetchone()
        if (session.get('role') != 'admin' and
                phong['user_id'] != session['user_id']):
            flash('Không có quyền!', 'danger')
            return redirect('/')
        cursor.execute(
            "UPDATE phong_thi SET trang_thai='da_dong' WHERE id=%s",
            (phong_id,)
        )
        conn.commit()
        flash('🔒 Đã đóng phòng thi!', 'success')
    except Exception as e:
        flash(f'Lỗi: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect(f'/quan_ly_phong/{phong_id}')


@phong_thi_bp.route('/xoa_phong/<int:phong_id>', methods=['POST'])
@login_required
def xoa_phong(phong_id):
    """Xóa phòng cùng toàn bộ thí sinh và bài làm. KHÔNG khôi phục được."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT ten_phong, user_id FROM phong_thi WHERE id=%s", (phong_id,)
        )
        phong = cursor.fetchone()
        if not phong:
            flash('Không tìm thấy phòng thi!', 'danger')
            return redirect('/?tab=room')
        if (session.get('role') != 'admin' and
                phong['user_id'] != session['user_id']):
            flash('Bạn không có quyền xóa phòng thi này!', 'danger')
            return redirect('/?tab=room')

        # Xóa từ dưới lên: bài làm -> thí sinh -> phòng. Ngược thứ tự này là vấp
        # ràng buộc khóa ngoại và câu lệnh sẽ hỏng giữa chừng.
        cursor.execute("""
            DELETE FROM bai_lam
            WHERE thi_sinh_id IN (
                SELECT id FROM thi_sinh WHERE phong_thi_id=%s
            )
        """, (phong_id,))
        cursor.execute(
            "DELETE FROM thi_sinh WHERE phong_thi_id=%s", (phong_id,)
        )
        cursor.execute(
            "DELETE FROM phong_thi WHERE id=%s", (phong_id,)
        )
        conn.commit()
        flash(f'Đã xóa phòng thi: {phong["ten_phong"]}', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Lỗi khi xóa phòng: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()
    return redirect('/?tab=room')


# ==========================================
# LỊCH SỬ & PHÂN TÍCH (sau khi thi xong)
#
# Ba trang, đi từ tổng đến chi tiết:
#   /lich_su_phong        — danh sách các phòng đã đóng.
#   /lich_su_phong/<id>   — bảng điểm một phòng, xếp hạng, phân loại giỏi/khá...
#   /phan_tich_phong/<id> — soi từng câu: câu nào cả lớp làm sai, đáp án nào bị
#                           chọn nhầm nhiều nhất. Dùng để biết nên dạy lại phần
#                           nào, hoặc phát hiện câu hỏi ra sai/tối nghĩa.
# ==========================================
@phong_thi_bp.route('/lich_su_phong')
@login_required
def danh_sach_lich_su_phong():
    """Danh sách các phòng ĐÃ ĐÓNG. Admin thấy tất cả, giáo viên chỉ thấy phòng mình."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if session.get('role') == 'admin':
        cursor.execute("""
            SELECT p.*, d.ten_de_thi, m.ten_hoc_phan AS ten_mon,
                   u.ho_ten AS ten_gv,
                   (SELECT COUNT(*) FROM thi_sinh ts WHERE ts.phong_thi_id = p.id) AS tong_thi_sinh,
                   (SELECT COUNT(*) FROM thi_sinh ts WHERE ts.phong_thi_id = p.id AND ts.da_nop_bai = 1) AS da_nop,
                   (SELECT AVG(ts.diem) FROM thi_sinh ts WHERE ts.phong_thi_id = p.id AND ts.da_nop_bai = 1) AS diem_tb
            FROM phong_thi p
            JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id = p.id)
            JOIN hoc_phan m ON d.hoc_phan_id = m.id
            JOIN nguoi_dung u ON p.user_id = u.id
            WHERE p.trang_thai = 'da_dong'
            ORDER BY p.thoi_gian_bat_dau DESC
        """)
    else:
        cursor.execute("""
            SELECT p.*, d.ten_de_thi, m.ten_hoc_phan AS ten_mon,
                   u.ho_ten AS ten_gv,
                   (SELECT COUNT(*) FROM thi_sinh ts WHERE ts.phong_thi_id = p.id) AS tong_thi_sinh,
                   (SELECT COUNT(*) FROM thi_sinh ts WHERE ts.phong_thi_id = p.id AND ts.da_nop_bai = 1) AS da_nop,
                   (SELECT AVG(ts.diem) FROM thi_sinh ts WHERE ts.phong_thi_id = p.id AND ts.da_nop_bai = 1) AS diem_tb
            FROM phong_thi p
            JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id = p.id)
            JOIN hoc_phan m ON d.hoc_phan_id = m.id
            JOIN nguoi_dung u ON p.user_id = u.id
            WHERE p.trang_thai = 'da_dong' AND p.user_id = %s
            ORDER BY p.thoi_gian_bat_dau DESC
        """, (session['user_id'],))

    phong_list = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('danh_sach_lich_su_phong.html', phong_list=phong_list,
                           active_tab='lich_su_phong')


@phong_thi_bp.route('/lich_su_phong/<int:phong_id>')
@login_required
def lich_su_phong(phong_id):
    """Bảng điểm tổng kết của một phòng: xếp hạng và phân loại học lực.

    Thang phân loại: xuất sắc >= 8.5, giỏi 7–8.5, khá 5.5–7, trung bình 4–5.5,
    yếu < 4. Mọi con số chỉ tính trên bài ĐÃ NỘP; người vắng hoặc bỏ dở không
    được đưa vào điểm trung bình, nếu không cả lớp bị kéo tụt vì mấy bài bỏ trống.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT p.*, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS de_thi_id,
               d.ten_de_thi, d.tong_so_cau, m.ten_hoc_phan AS ten_mon
        FROM phong_thi p
        JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
        JOIN hoc_phan m ON d.hoc_phan_id=m.id
        WHERE p.id=%s
    """, (phong_id,))
    phong = cursor.fetchone()

    if not phong:
        flash('Không tìm thấy!', 'danger')
        cursor.close(); conn.close()
        return redirect('/')
    if (session.get('role') != 'admin' and
            phong['user_id'] != session['user_id']):
        flash('Không có quyền!', 'danger')
        cursor.close(); conn.close()
        return redirect('/')

    cursor.execute("""
        SELECT ts.*,
               TIMESTAMPDIFF(SECOND, ts.thoi_gian_vao,
                             ts.thoi_gian_nop) AS thoi_gian_lam
        FROM thi_sinh ts
        WHERE ts.phong_thi_id=%s
        ORDER BY ts.diem DESC, ts.thoi_gian_nop ASC
    """, (phong_id,))
    thi_sinhs = cursor.fetchall()

    cursor.execute("""
        SELECT
            COUNT(*)                                                  AS tong,
            SUM(CASE WHEN da_nop_bai=1 THEN 1 ELSE 0 END)                AS da_nop,
            SUM(CASE WHEN trang_thai='cho_duyet' THEN 1 ELSE 0 END)      AS cho_duyet,
            AVG(CASE WHEN da_nop_bai=1 THEN diem END)                    AS diem_tb,
            MAX(CASE WHEN da_nop_bai=1 THEN diem END)                    AS diem_cao,
            MIN(CASE WHEN da_nop_bai=1 THEN diem END)                    AS diem_thap,
            SUM(CASE WHEN da_nop_bai=1 AND diem>=8.5 THEN 1 ELSE 0 END)              AS xuat_sac,
            SUM(CASE WHEN da_nop_bai=1 AND diem>=7   AND diem<8.5 THEN 1 ELSE 0 END) AS gioi,
            SUM(CASE WHEN da_nop_bai=1 AND diem>=5.5 AND diem<7   THEN 1 ELSE 0 END) AS kha,
            SUM(CASE WHEN da_nop_bai=1 AND diem>=4   AND diem<5.5 THEN 1 ELSE 0 END) AS tb,
            SUM(CASE WHEN da_nop_bai=1 AND diem<4    THEN 1 ELSE 0 END)              AS yeu
        FROM thi_sinh WHERE phong_thi_id=%s
    """, (phong_id,))
    raw = cursor.fetchone()

    def to_int(v):   return int(v or 0)
    def to_float(v): return round(float(v), 2) if v else None
    def calc_pct(v, t):
        """Phần trăm cho thanh biểu đồ; sàn 2% để nhóm rất ít người vẫn nhìn thấy."""
        if t <= 0: return 2
        r = round(to_int(v) / t * 100)
        return r if r > 0 else 2

    da_nop = to_int(raw['da_nop'])
    so_xs  = to_int(raw['xuat_sac'])
    so_g   = to_int(raw['gioi'])
    so_k   = to_int(raw['kha'])
    so_tb  = to_int(raw['tb'])
    so_yeu = to_int(raw['yeu'])
    so_dat = so_xs + so_g + so_k

    stats = {
        'tong'      : to_int(raw['tong']),
        'da_nop'    : da_nop,
        'cho_duyet' : to_int(raw['cho_duyet']),
        'diem_tb'   : to_float(raw['diem_tb']),
        'diem_cao'  : to_float(raw['diem_cao']),
        'diem_thap' : to_float(raw['diem_thap']),
        'xuat_sac'  : so_xs, 'gioi': so_g, 'kha': so_k,
        'tb'        : so_tb, 'yeu' : so_yeu,
    }

    # Xếp hạng. Người chưa nộp mang rank 0 và không chiếm chỗ trong bảng xếp
    # hạng — danh sách đã sắp theo điểm giảm dần nên chỉ cần đếm tuần tự.
    rank = 0
    for ts in thi_sinhs:
        if ts['da_nop_bai']:
            rank += 1
            ts['rank'] = rank
        else:
            ts['rank'] = 0

    cursor.close()
    conn.close()

    return render_template('lich_su_phong.html',
        phong=phong, thi_sinhs=thi_sinhs, stats=stats,
        da_nop=da_nop, so_dat=so_dat,
        ti_le_dat=round(so_dat/da_nop*100, 1) if da_nop > 0 else 0,
        so_xs=so_xs,   so_g=so_g,   so_k=so_k,
        so_tb=so_tb,   so_yeu=so_yeu,
        pct_xs=calc_pct(so_xs, da_nop),
        pct_g=calc_pct(so_g,   da_nop),
        pct_k=calc_pct(so_k,   da_nop),
        pct_tb=calc_pct(so_tb, da_nop),
        pct_yeu=calc_pct(so_yeu, da_nop))


# ==========================================
# PHÂN TÍCH ĐỀ THI (Item Analysis)
#
# Chấm điểm chất lượng của TỪNG CÂU HỎI, theo hai chỉ số kinh điển trong đo
# lường giáo dục:
#
#   ĐỘ KHÓ (p) = số người làm đúng / số người làm câu đó.
#       p càng cao thì câu càng dễ. p ≈ 0.9 nghĩa là gần như ai cũng làm được;
#       p ≈ 0.2 là câu đánh đố hoặc ra sai.
#
#   ĐỘ PHÂN CÁCH (D) = tỉ lệ đúng của NHÓM GIỎI − tỉ lệ đúng của NHÓM YẾU.
#       Nhóm giỏi là 27% thí sinh điểm cao nhất, nhóm yếu là 27% thấp nhất
#       (tỉ lệ 27% là chuẩn quen dùng: đủ tách hai đầu mà vẫn đủ người để số
#       liệu có nghĩa).
#
#       D cao  -> câu hỏi phân loại tốt: học sinh giỏi làm được, học sinh yếu thì không.
#       D ≈ 0  -> câu không phân loại được ai, dễ như nhau với mọi người.
#       D ÂM   -> ĐÁNG NGỜ: học sinh yếu làm đúng NHIỀU HƠN học sinh giỏi. Gần
#                 như chắc chắn câu đó ra sai đáp án, hoặc câu chữ tối nghĩa
#                 khiến người học kỹ lại hiểu sai. Giáo viên nên xem lại câu này.
#
# Chỉ tính trên bài ĐÃ NỘP, lấy số liệu từ bảng bai_lam (mỗi dòng một câu, có
# cột is_correct).
# ==========================================
def _phong_co_quyen(cursor, phong_id):
    """Lấy phòng + kiểm tra quyền. (phong, None) hoặc (None, 'notfound'|'forbidden')."""
    cursor.execute("""
        SELECT p.*, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS de_thi_id,
               d.ten_de_thi, d.tong_diem, m.ten_hoc_phan AS ten_mon
        FROM phong_thi p
        JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
        JOIN hoc_phan m ON d.hoc_phan_id=m.id
        WHERE p.id=%s
    """, (phong_id,))
    phong = cursor.fetchone()
    if not phong:
        return None, 'notfound'
    if (session.get('role') != 'admin' and
            phong['user_id'] != session['user_id']):
        return None, 'forbidden'
    return phong, None


def _phan_tich_du_lieu(cursor, phong):
    """Tính toàn bộ số liệu phân tích của một phòng (trang web và Excel dùng chung).

    Chỉ chạy ĐÚNG HAI truy vấn — một lấy danh sách thí sinh, một lấy toàn bộ
    lượt trả lời — rồi tính hết trong bộ nhớ. Nếu tính bằng SQL theo từng câu
    thì một đề 40 câu sẽ thành 40+ lượt hỏi cơ sở dữ liệu cho một lần mở trang.
    """
    phong_id  = phong['id']
    tong_diem = float(phong['tong_diem'] or 10)

    cursor.execute("""
        SELECT id, ho_ten, ma_so_sv, diem
        FROM thi_sinh
        WHERE phong_thi_id=%s AND da_nop_bai=1
        ORDER BY diem DESC
    """, (phong_id,))
    ts_list = cursor.fetchall()

    cursor.execute("""
        SELECT b.thi_sinh_id, b.cau_hoi_id, b.is_correct,
               c.noi_dung, c.chuong, c.do_kho
        FROM bai_lam b
        JOIN thi_sinh ts ON b.thi_sinh_id = ts.id
        JOIN cau_hoi   c ON b.cau_hoi_id  = c.id
        WHERE ts.phong_thi_id=%s AND ts.da_nop_bai=1
    """, (phong_id,))
    rows = cursor.fetchall()

    so_nop = len(ts_list)
    diems  = [float(t['diem']) for t in ts_list if t['diem'] is not None]

    overview = {
        'so_nop'    : so_nop,
        'diem_tb'   : round(sum(diems) / len(diems), 2) if diems else 0,
        'diem_cao'  : round(max(diems), 2) if diems else 0,
        'diem_thap' : round(min(diems), 2) if diems else 0,
        'ti_le_dat' : round(sum(1 for d in diems if d >= tong_diem/2) / len(diems) * 100, 1) if diems else 0,
    }

    buckets = [0] * 10
    for d in diems:
        idx = int(d / tong_diem * 10) if tong_diem else 0
        buckets[min(9, max(0, idx))] += 1
    b = tong_diem / 10
    dist_labels = [f'{round(i*b,1)}-{round((i+1)*b,1)}' for i in range(10)]

    def _loai(d):
        r = d / tong_diem * 10
        if r >= 8.5: return 'xuat_sac'
        if r >= 7:   return 'gioi'
        if r >= 5.5: return 'kha'
        if r >= 4:   return 'tb'
        return 'yeu'
    phan_loai = {'xuat_sac': 0, 'gioi': 0, 'kha': 0, 'tb': 0, 'yeu': 0}
    for d in diems:
        phan_loai[_loai(d)] += 1

    # Chia nhóm giỏi / nhóm yếu để tính độ phân cách. ts_list đã sắp theo điểm
    # giảm dần, nên cắt 27% đầu và 27% cuối. max(1, ...) để phòng không rơi vào
    # 0 khi phòng chỉ có vài người — chia cho 0 ở dưới sẽ vỡ.
    nhom    = max(1, round(so_nop * 0.27)) if so_nop else 0
    top_ids = {t['id'] for t in ts_list[:nhom]}
    bot_ids = {t['id'] for t in ts_list[-nhom:]} if nhom else set()

    # Duyệt một lượt qua mọi lượt trả lời, cộng dồn cho từng câu: tổng số lượt,
    # số lượt đúng, và riêng số liệu của hai nhóm giỏi/yếu.
    cau = {}
    for r in rows:
        cid = r['cau_hoi_id']
        if cid not in cau:
            cau[cid] = {'noi_dung': r['noi_dung'], 'chuong': r['chuong'],
                        'do_kho': r['do_kho'], 'so_luot': 0, 'so_dung': 0,
                        'top_n': 0, 'top_dung': 0, 'bot_n': 0, 'bot_dung': 0}
        c = cau[cid]
        c['so_luot'] += 1
        c['so_dung'] += r['is_correct']
        if r['thi_sinh_id'] in top_ids:
            c['top_n'] += 1; c['top_dung'] += r['is_correct']
        if r['thi_sinh_id'] in bot_ids:
            c['bot_n'] += 1; c['bot_dung'] += r['is_correct']

    def _dk(p):
        """Xếp loại độ khó theo tỉ lệ làm đúng p."""
        if p >= 0.75: return ('Dễ', 'dk-de')
        if p >= 0.25: return ('Trung bình', 'dk-tb')
        return ('Khó', 'dk-kho')

    def _pc(d):
        """Xếp loại độ phân cách D. D âm là câu hỏi có vấn đề — xem giải thích ở đầu phần."""
        if d >= 0.4: return ('Rất tốt', 'pc-rtot')
        if d >= 0.3: return ('Tốt', 'pc-tot')
        if d >= 0.2: return ('Tạm được', 'pc-tam')
        if d >= 0:   return ('Kém', 'pc-kem')
        return ('Âm – nên loại', 'pc-am')

    cau_list = []
    for i, (cid, c) in enumerate(cau.items(), start=1):
        p     = c['so_dung'] / c['so_luot'] if c['so_luot'] else 0
        d_top = c['top_dung'] / c['top_n'] if c['top_n'] else 0
        d_bot = c['bot_dung'] / c['bot_n'] if c['bot_n'] else 0
        D = d_top - d_bot
        dk_text, dk_class = _dk(p)
        pc_text, pc_class = _pc(D)
        cau_list.append({
            'stt': i, 'noi_dung': c['noi_dung'], 'chuong': c['chuong'], 'do_kho': c['do_kho'],
            'so_luot': c['so_luot'], 'so_dung': c['so_dung'],
            'p': round(p, 2), 'p_pct': round(p * 100),
            'dk_text': dk_text, 'dk_class': dk_class,
            'D': round(D, 2), 'pc_text': pc_text, 'pc_class': pc_class,
        })

    return {'overview': overview, 'dist_labels': dist_labels, 'dist_counts': buckets,
            'phan_loai': phan_loai, 'cau_list': cau_list, 'nhom': nhom, 'so_nop': so_nop}


def _xuat_xlsx(sheet_title, tieu_de, info_lines, heads, rows, widths, fname):
    """Dựng file .xlsx trong bộ nhớ và trả về Response tải xuống."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook(); ws = wb.active; ws.title = sheet_title[:31]
    ws.append([tieu_de])
    for line in info_lines:
        ws.append([line])
    ws.append([])
    ws.append(heads)
    hrow = ws.max_row
    fill = PatternFill('solid', fgColor='2563EB')
    font = Font(bold=True, color='FFFFFF')
    for col in range(1, len(heads) + 1):
        cell = ws.cell(row=hrow, column=col)
        cell.fill = fill; cell.font = font
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return Response(buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename={fname}'})


def _err_redirect(conn, cursor, err):
    cursor.close(); conn.close()
    flash('Không tìm thấy phòng thi!' if err == 'notfound' else 'Không có quyền!', 'danger')
    return redirect('/')


def _thong_ke_lop(cursor, phong_id, tong_diem):
    """Bảng điểm theo DANH SÁCH LỚP, tính cả người vắng.

    Khác `_phan_tich_du_lieu` ở chỗ đó: bảng này lấy danh sách lớp làm gốc, ai
    không đi thi vẫn có một dòng và nhận điểm 0. Đây mới là bảng điểm nộp được
    cho khoa — chỉ liệt kê người đã thi thì sinh viên vắng biến mất khỏi bảng.
    """
    cursor.execute("""
        SELECT ts.id, ts.ho_ten, ts.ma_so_sv, ts.lop, ts.diem, ts.da_nop_bai, ts.trang_thai,
               ts.so_cau_dung, ts.tong_so_cau, ts.de_thi_id,
               TIMESTAMPDIFF(SECOND, ts.thoi_gian_vao, ts.thoi_gian_nop) AS thoi_gian_lam,
               d.ten_de_thi AS de_assigned,
               g.so_lan_roi_tab AS so_lan_roi
        FROM thi_sinh ts
        LEFT JOIN de_thi d ON ts.de_thi_id = d.id
        LEFT JOIN giam_sat g ON g.thi_sinh_id = ts.id
        WHERE ts.phong_thi_id=%s
    """, (phong_id,))
    takers = cursor.fetchall()
    by_mssv = {}
    for t in takers:
        key = (t['ma_so_sv'] or '').strip()
        if key:
            by_mssv[key] = t

    # Danh sách lớp đã gán cho phòng + toàn bộ SV theo danh sách lớp
    cursor.execute("""
        SELECT l.ten_lop, sv.ma_so_sv, sv.ho_ten
        FROM phong_thi_lop ptl
        JOIN lop l ON ptl.lop_id = l.id
        JOIN lop_sinh_vien sv ON sv.lop_id = l.id
        WHERE ptl.phong_thi_id=%s
        ORDER BY l.ten_lop, sv.ho_ten
    """, (phong_id,))
    roster_rows = cursor.fetchall()

    def _make_row(ho_ten, mssv, ten_lop, t):
        """Dựng 1 dòng SV. t=None nghĩa là VẮNG (mọi điểm để 0)."""
        if t is None:
            return {'ho_ten': ho_ten, 'ma_so_sv': mssv, 'lop': ten_lop,
                    'ma_de': '—', 'tg_lam': '—', 'so_cau_dung': 0, 'tong_so_cau': None,
                    'diem': 0.0, 'da_nop_bai': False, 'trang_thai': 'vang', 'canh_bao': False}
        sec = t['thoi_gian_lam']
        tg = f'{sec // 60}p {sec % 60}s' if (t['da_nop_bai'] and sec is not None and sec >= 0) else '—'
        diem = float(t['diem']) if t['diem'] is not None else 0.0
        tt = 'da_nop' if t['da_nop_bai'] else 'co_mat'
        return {'ho_ten': ho_ten, 'ma_so_sv': mssv, 'lop': ten_lop,
                'ma_de': _ma_de_ngan(t['de_assigned']) if t['de_thi_id'] else '—',
                'tg_lam': tg, 'so_cau_dung': t['so_cau_dung'], 'tong_so_cau': t['tong_so_cau'],
                'diem': diem, 'da_nop_bai': bool(t['da_nop_bai']),
                'trang_thai': tt, 'canh_bao': (t['so_lan_roi'] or 0) > 0}

    # Nhóm SV theo lớp (ưu tiên danh sách lớp; nếu phòng chưa gán lớp thì gom theo lớp SV tự khai)
    lop_map = {}
    if roster_rows:
        for r in roster_rows:
            mssv = (r['ma_so_sv'] or '').strip()
            lop_map.setdefault(r['ten_lop'], []).append(
                _make_row(r['ho_ten'], mssv, r['ten_lop'], by_mssv.get(mssv)))
    else:
        for t in takers:
            ten_lop = (t['lop'] or '').strip() or 'Chưa phân lớp'
            lop_map.setdefault(ten_lop, []).append(
                _make_row(t['ho_ten'], t['ma_so_sv'], ten_lop, t))

    lop_list = [{'ten_lop': k, 'hoc_sinh': v, 'si_so': len(v)}
                for k, v in lop_map.items()]
    lop_list.sort(key=lambda l: l['ten_lop'])
    all_hs = [hs for l in lop_list for hs in l['hoc_sinh']]

    # Thống kê tóm tắt + phân loại học lực (giỏi / khá / trung bình) cho sơ đồ tròn
    gioi = kha = tb = 0
    for hs in all_hs:
        if hs['da_nop_bai'] and hs['diem'] is not None:
            r = hs['diem'] / tong_diem * 10
            if r >= 8:     gioi += 1
            elif r >= 6.5: kha += 1
            else:          tb += 1

    stats = {
        'tong_sv'  : len(all_hs),
        'vang'     : sum(1 for hs in all_hs if hs['trang_thai'] == 'vang'),
        'canh_bao' : sum(1 for hs in all_hs if hs['canh_bao']),
        'gioi'     : gioi, 'kha': kha, 'tb': tb,
    }
    return {'lop_list': lop_list, 'all_hs': all_hs, 'stats': stats}


# Nhãn trạng thái dạng chữ (dùng cho Excel)
_TT_NHAN = {'vang': 'Vắng', 'da_nop': 'Đã nộp', 'co_mat': 'Có mặt'}


@phong_thi_bp.route('/phan_tich_phong/<int:phong_id>')
@login_required
def phan_tich_phong(phong_id):
    """Trang thống kê kết quả theo lớp (kèm cả sinh viên vắng)."""
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    phong, err = _phong_co_quyen(cursor, phong_id)
    if err:
        return _err_redirect(conn, cursor, err)
    tong_diem = float(phong['tong_diem'] or 10)
    data = _thong_ke_lop(cursor, phong_id, tong_diem)
    cursor.close(); conn.close()

    return render_template('phan_tich_phong.html',
        phong=phong, tong_diem=tong_diem, dat_nguong=tong_diem / 2,
        lop_list=data['lop_list'], all_hs=data['all_hs'], stats=data['stats'])


@phong_thi_bp.route('/phan_tich_phong/<int:phong_id>/excel')
@login_required
def phan_tich_phong_excel(phong_id):
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    phong, err = _phong_co_quyen(cursor, phong_id)
    if err:
        return _err_redirect(conn, cursor, err)
    tong_diem = float(phong['tong_diem'] or 10)
    data = _thong_ke_lop(cursor, phong_id, tong_diem)
    cursor.close(); conn.close()

    rows = []
    for i, hs in enumerate(data['all_hs'], start=1):
        if hs['da_nop_bai']:
            so_dung = f"{hs['so_cau_dung']}/{hs['tong_so_cau']}"
        elif hs['trang_thai'] == 'vang':
            so_dung = '0'
        else:
            so_dung = ''
        diem = hs['diem'] if (hs['da_nop_bai'] or hs['trang_thai'] == 'vang') else ''
        tt = _TT_NHAN.get(hs['trang_thai'], '')
        if hs['canh_bao']:
            tt += ' (Vi phạm)'
        rows.append([i, hs['ho_ten'], hs['ma_so_sv'] or '', hs['lop'],
                     hs['ma_de'] if hs['ma_de'] != '—' else '',
                     hs['tg_lam'] if hs['tg_lam'] != '—' else '',
                     so_dung, diem, tt])

    st = data['stats']
    return _xuat_xlsx(
        'Bang diem theo lop',
        f'BẢNG ĐIỂM THEO LỚP - {phong["ten_phong"]}',
        [f'Môn: {phong["ten_mon"]} | Tổng SV: {st["tong_sv"]} | Vắng: {st["vang"]} | Vi phạm: {st["canh_bao"]}'],
        ['STT', 'Họ và tên', 'Mã số sinh viên', 'Lớp học', 'Mã đề',
         'Thời gian làm bài', 'Số câu đúng', 'Điểm số', 'Trạng thái'],
        rows, [6, 26, 18, 14, 10, 16, 12, 10, 16],
        f'bang_diem_theo_lop_{phong_id}.xlsx')


@phong_thi_bp.route('/lich_su_phong/<int:phong_id>/excel')
@login_required
def lich_su_phong_excel(phong_id):
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    phong, err = _phong_co_quyen(cursor, phong_id)
    if err:
        return _err_redirect(conn, cursor, err)
    cursor.execute("""
        SELECT ho_ten, ma_so_sv, lop, so_cau_dung, tong_so_cau, diem, thoi_gian_nop
        FROM thi_sinh
        WHERE phong_thi_id=%s AND da_nop_bai=1
        ORDER BY diem DESC, thoi_gian_nop ASC
    """, (phong_id,))
    ts = cursor.fetchall()
    cursor.close(); conn.close()
    rows = []
    for i, t in enumerate(ts, start=1):
        nop = t['thoi_gian_nop'].strftime('%d/%m/%Y %H:%M') if t.get('thoi_gian_nop') else ''
        rows.append([i, t['ho_ten'], t['ma_so_sv'] or '', t['lop'] or '',
                     t['so_cau_dung'], t['tong_so_cau'], float(t['diem']) if t['diem'] is not None else '', nop])
    return _xuat_xlsx(
        'Ket qua phong',
        f'KẾT QUẢ PHÒNG THI - {phong["ten_phong"]}',
        [f'Môn: {phong["ten_mon"]} | Đề: {phong["ten_de_thi"]} | Số bài nộp: {len(ts)}'],
        ['STT', 'Họ và tên', 'MSSV', 'Lớp', 'Số câu đúng', 'Tổng câu', 'Điểm', 'Thời gian nộp'],
        rows, [6, 26, 14, 12, 12, 10, 9, 18],
        f'ket_qua_phong_{phong_id}.xlsx')


@phong_thi_bp.route('/xem_ket_qua_phong/<int:phong_id>')
@login_required
def xem_ket_qua_phong(phong_id):
    """Bảng xếp hạng đơn giản: ai điểm cao nhất, ai nộp sớm nhất.

    Xếp theo điểm giảm dần rồi tới thời gian nộp tăng dần — hai người bằng điểm
    thì người nộp trước đứng trên.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT p.*, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS de_thi_id,
               d.ten_de_thi, m.ten_hoc_phan AS ten_mon
        FROM phong_thi p
        JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
        JOIN hoc_phan m ON d.hoc_phan_id=m.id
        WHERE p.id=%s
    """, (phong_id,))
    phong = cursor.fetchone()

    if not phong:
        flash('Không tìm thấy!', 'danger')
        cursor.close(); conn.close()
        return redirect('/')
    if (session.get('role') != 'admin' and
            phong['user_id'] != session['user_id']):
        flash('Không có quyền!', 'danger')
        cursor.close(); conn.close()
        return redirect('/')

    cursor.execute("""
        SELECT * FROM thi_sinh
        WHERE phong_thi_id=%s AND da_nop_bai=1
        ORDER BY diem DESC, thoi_gian_nop ASC
    """, (phong_id,))
    ket_qua = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('ket_qua_phong.html',
                           phong=phong, ket_qua=ket_qua)


# ==========================================
# GIÁM SÁT PHÒNG THI
#
# Hai nguồn dữ liệu, đừng nhầm:
#   - HÌNH WEBCAM đi qua Socket.IO và KHÔNG hề lưu lại (xem realtime.py). Ảnh
#     chỉ được chuyển thẳng từ máy thí sinh tới màn hình giáo viên rồi bỏ.
#   - SỐ LẦN RỜI TAB nằm ở bảng giam_sat, và trang này hỏi lại qua giam_sat_data
#     vài giây một lần.
# ==========================================
@phong_thi_bp.route('/giam_sat_phong/<int:phong_id>')
@login_required
def giam_sat_phong(phong_id):
    """Màn hình coi thi trực tiếp: lưới webcam + cảnh báo rời tab."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT p.*, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS de_thi_id,
               d.ten_de_thi, d.tong_so_cau, m.ten_hoc_phan AS ten_mon
        FROM phong_thi p
        JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
        JOIN hoc_phan m ON d.hoc_phan_id=m.id
        WHERE p.id=%s
    """, (phong_id,))
    phong = cursor.fetchone()

    if not phong:
        cursor.close(); conn.close()
        flash('Không tìm thấy phòng thi!', 'danger')
        return redirect('/?tab=room')
    if (session.get('role') != 'admin' and
            phong['user_id'] != session['user_id']):
        cursor.close(); conn.close()
        flash('Bạn không có quyền giám sát phòng thi này!', 'danger')
        return redirect('/?tab=room')

    cursor.execute("""
        SELECT d.id, d.ten_de_thi FROM phong_thi_de_thi ptd
        JOIN de_thi d ON ptd.de_thi_id = d.id
        WHERE ptd.phong_thi_id=%s ORDER BY d.ten_de_thi
    """, (phong_id,))
    bo_de = cursor.fetchall()
    if not bo_de:
        bo_de = [{'id': phong['de_thi_id'], 'ten_de_thi': phong['ten_de_thi']}]
    for d in bo_de:
        d['ma_de'] = _ma_de_ngan(d['ten_de_thi'])

    cursor.execute("""
        SELECT l.ten_lop FROM phong_thi_lop ptl
        JOIN lop l ON ptl.lop_id = l.id
        WHERE ptl.phong_thi_id=%s ORDER BY l.ten_lop
    """, (phong_id,))
    lop_da_gan = cursor.fetchall()

    cursor.close(); conn.close()

    for k in ('thoi_gian_mo_phong', 'thoi_gian_bat_dau'):
        if phong.get(k):
            try:
                phong[k] = phong[k].strftime('%H:%M - %d/%m/%Y')
            except AttributeError:
                pass

    layout = 'layouts/_bare.html' if request.args.get('frag') else 'base.html'
    return render_template('giam_sat_phong.html', phong=phong,
                           bo_de=bo_de, lop_da_gan=lop_da_gan, layout=layout)


@phong_thi_bp.route('/giam_sat_phong/<int:phong_id>/data')
@login_required
def giam_sat_data(phong_id):
    """Tình trạng thí sinh trong lúc thi (JSON): ai đang làm, ai rời tab mấy lần."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, user_id, trang_thai, thoi_gian_ket_thuc "
            "FROM phong_thi WHERE id=%s", (phong_id,)
        )
        phong = cursor.fetchone()
        if not phong:
            return jsonify({'ok': False}), 404
        if (session.get('role') != 'admin' and
                phong['user_id'] != session['user_id']):
            return jsonify({'ok': False}), 403

        # Tự đóng phòng nếu đã hết giờ
        _dong_neu_het_gio(cursor, conn, phong)

        # Chỉ giám sát thí sinh đã được duyệt vào thi
        # Chỉ lấy DANH SÁCH + trạng thái (nhẹ). Hình webcam đến qua kênh realtime
        # (Socket.IO), không còn đọc ảnh nặng từ DB nữa.
        cursor.execute("""
            SELECT ts.id, ts.ho_ten, ts.ma_so_sv, ts.da_nop_bai,
                   g.so_lan_roi_tab
            FROM thi_sinh ts
            LEFT JOIN giam_sat g ON g.thi_sinh_id = ts.id
            WHERE ts.phong_thi_id=%s AND ts.trang_thai='da_duyet'
            ORDER BY ts.ho_ten
        """, (phong_id,))
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    ds = [{
        'id'         : r['id'],
        'ho_ten'     : r['ho_ten'],
        'ma_so_sv'   : r['ma_so_sv'] or '',
        'da_nop_bai' : int(r['da_nop_bai'] or 0),
        'so_lan_roi' : int(r['so_lan_roi_tab'] or 0),
    } for r in rows]
    return jsonify({'ok': True, 'thi_sinhs': ds})