"""
PHÍA THÍ SINH: vào phòng, làm bài, nộp bài, xem lại kết quả.

Toàn bộ hành trình của một thí sinh:

    vào phòng (mã + mật khẩu)
        -> ghi danh, có thể phải CHỜ giáo viên duyệt
        -> làm bài (đề được trộn riêng cho từng người)
        -> nộp bài, chấm điểm ngay
        -> xem lại bài (nếu giáo viên cho phép)

BA ĐIỀU QUAN TRỌNG NHẤT CỦA FILE NÀY:

1. Thí sinh KHÔNG cần đăng nhập. Vậy làm sao biết ai được xem bài của ai?
   Lúc ghi danh, id của thí sinh được cất vào session đã ký của Flask
   (`thi_sinh_ids`). Về sau chỉ đúng trình duyệt đó mới mở được bài làm và kết
   quả mang id ấy. Không có lớp này thì chỉ cần đổi số trên URL là đọc được bài
   người khác — lỗi IDOR kinh điển.

2. Đề được TRỘN RIÊNG cho từng thí sinh, nhưng phải trộn ra kết quả GIỐNG HỆT
   nhau ở cả ba thời điểm: lúc làm bài, lúc chấm, và lúc xem lại. Xem phần
   `_sap_xep_cau_hoi` để biết cách làm.

3. Phòng có gán lớp thì trở thành "phòng giới hạn": phải đăng nhập, và MSSV
   ngoài danh sách lớp sẽ phải chờ giáo viên duyệt tay.
"""
import random
from datetime import datetime, timedelta
from flask import (Blueprint, render_template, request,
                   redirect, flash, jsonify, session, url_for)
from database import get_db_connection
from extensions import limiter

thi_sinh_bp = Blueprint('thi_sinh', __name__)


def _bo_de_phong(cursor, phong_id, phong_de_thi_id):
    """Danh sách mã đề của một phòng — một phòng có thể có nhiều mã đề.

    Phòng tạo từ trước khi hệ thống hỗ trợ nhiều đề chưa có dòng nào trong bảng
    phong_thi_de_thi, nên rơi về mã đề đại diện lưu ngay trên bản ghi phòng.
    """
    cursor.execute(
        "SELECT de_thi_id FROM phong_thi_de_thi WHERE phong_thi_id=%s",
        (phong_id,)
    )
    ids = [r['de_thi_id'] for r in cursor.fetchall()]
    if not ids and phong_de_thi_id:
        ids = [phong_de_thi_id]
    return ids


# ==========================================
# PHÒNG GIỚI HẠN LỚP (danh sách lớp = điều kiện dự thi)
#
# Giáo viên gán lớp cho phòng thì phòng đó "khóa" lại:
#   - Bắt buộc đăng nhập, không nhận khách vãng lai. Vì khách tự gõ MSSV, mà
#     gõ MSSV thì gõ mã của ai chẳng được.
#   - MSSV có trong danh sách lớp -> vào thi bình thường.
#   - MSSV lạ -> KHÔNG đuổi thẳng, mà đẩy vào hàng chờ giáo viên duyệt. Sinh
#     viên học lại hay thi bù là chuyện có thật, chặn cứng sẽ oan cho họ.
#
# Phòng không gán lớp nào thì mở cho tất cả.
# ==========================================
def _chuan_hoa_mssv(s):
    """Chuẩn hóa MSSV để đối chiếu: bỏ khoảng trắng + viết hoa."""
    return (s or '').strip().upper()


def _phong_gioi_han_lop(cursor, phong_id):
    """True nếu phòng có gán ít nhất một lớp (=> giới hạn theo danh sách)."""
    cursor.execute(
        "SELECT 1 FROM phong_thi_lop WHERE phong_thi_id=%s LIMIT 1", (phong_id,)
    )
    return cursor.fetchone() is not None


def _roster_mssv(cursor, phong_id):
    """Tập MSSV (đã chuẩn hóa) của mọi SV thuộc các lớp đã gán cho phòng."""
    cursor.execute("""
        SELECT sv.ma_so_sv FROM phong_thi_lop ptl
        JOIN lop_sinh_vien sv ON sv.lop_id = ptl.lop_id
        WHERE ptl.phong_thi_id=%s
    """, (phong_id,))
    return {_chuan_hoa_mssv(r['ma_so_sv']) for r in cursor.fetchall()
            if (r['ma_so_sv'] or '').strip()}


# ==========================================
# QUYỀN XEM BÀI CỦA THÍ SINH (chống lỗi IDOR)
#
# Thí sinh không có tài khoản, nên không thể hỏi "user này là ai". Thay vào đó,
# lúc ghi danh ta ghi id của họ vào session đã ký của Flask. Về sau, mọi trang
# hiện bài làm / kết quả đều phải hỏi `_co_quyen_xem` trước.
#
# Session của Flask được ký bằng SECRET_KEY nên người dùng không tự thêm id lạ
# vào đó được. Bỏ lớp kiểm tra này thì /ket-qua/124 mở ra bài của người khác.
# ==========================================
def _ghi_nho_thi_sinh(thi_sinh_id):
    """Đánh dấu: trình duyệt này chính là chủ của lượt thi vừa tạo."""
    ids = session.get('thi_sinh_ids', [])
    if thi_sinh_id not in ids:
        ids.append(thi_sinh_id)
        session['thi_sinh_ids'] = ids


def _co_quyen_xem(thi_sinh_id):
    """Trình duyệt hiện tại có được xem lượt thi này không? Admin thì xem tất."""
    if session.get('role') == 'admin':
        return True
    return thi_sinh_id in session.get('thi_sinh_ids', [])


def _dinh_danh_hoc_sinh():
    """Chuỗi dùng để tìm lại các lượt thi của học sinh đang đăng nhập.

    Phải khớp CHÍNH XÁC với thứ được ghi vào cột ma_so_sv lúc ghi danh (MSSV
    nếu có khai, không thì username). Lệch nhau là học sinh mở lịch sử thi ra
    thấy trống trơn dù vừa thi xong.
    """
    return (session.get('ma_so_sv') or session.get('username') or '').strip()


# ==========================================
# TRỘN ĐỀ RIÊNG CHO TỪNG THÍ SINH
#
# Mỗi người một thứ tự câu hỏi và một thứ tự đáp án khác nhau, để hai người
# ngồi cạnh nhau không nhìn bài được. Nhưng việc trộn phải TÁI LẬP ĐƯỢC: cùng
# một thí sinh, gọi lại lúc nào cũng phải ra đúng thứ tự cũ.
#
# Vì sao? Đề được trộn ở ba thời điểm khác nhau, trong ba request khác nhau:
#   1. lúc mở đề ra làm bài,
#   2. lúc chấm điểm sau khi nộp,
#   3. lúc mở lại bài để xem đáp án.
# Thí sinh bấm chọn "C". Nếu lúc chấm mà thứ tự đáp án trộn khác đi, thì "C"
# lúc chấm không còn là "C" mà thí sinh đã nhìn thấy — chấm sai toàn bộ.
#
# Cách bảo đảm: KHÔNG dùng random toàn cục, mà tạo bộ sinh số ngẫu nhiên riêng
# gieo bằng thi_sinh_id (và thêm id câu hỏi khi trộn đáp án). Cùng hạt giống
# thì luôn ra cùng một kết quả, dù chạy ở request nào, máy nào.
#
# Nhân thi_sinh_id với 1.000.000 rồi cộng id câu hỏi để hai câu khác nhau của
# cùng một thí sinh nhận hạt giống khác nhau (nếu chỉ cộng, thí sinh 2 câu 5 và
# thí sinh 5 câu 2 sẽ trùng hạt giống và trộn giống hệt nhau).
#
# Sau khi gọi, mỗi câu hỏi có thêm `dap_an_dung_hien_tai`: chữ cái của đáp án
# đúng THEO THỨ TỰ ĐANG HIỂN THỊ. Đây mới là chữ cái dùng để chấm, không phải
# `dap_an_dung` gốc trong CSDL.
# ==========================================
def _sap_xep_cau_hoi(questions, thi_sinh_id, tron_cau_hoi, tron_dap_an):
    """Trộn câu hỏi/đáp án theo hạt giống cố định của thí sinh (sửa tại chỗ)."""
    if tron_cau_hoi:
        random.Random(thi_sinh_id).shuffle(questions)

    for q in questions:
        # Ghi nhớ NỘI DUNG của đáp án đúng trước khi trộn. Sau khi trộn, chữ cái
        # thay đổi nhưng nội dung thì không — đó là mỏ neo để tìm lại nó.
        goc = (q['dap_an_dung'] or 'A').strip().upper()
        if goc not in ('A', 'B', 'C', 'D'):
            goc = 'A'
        dap_dung_nd = q['cau_' + goc.lower()]

        if tron_dap_an:
            opts = [q['cau_a'], q['cau_b'], q['cau_c'], q['cau_d']]
            random.Random(thi_sinh_id * 1_000_000 + q['id']).shuffle(opts)
            q['cau_a'], q['cau_b'], q['cau_c'], q['cau_d'] = opts

        # Đáp án đúng giờ nằm ở chữ cái nào? Dò lại theo nội dung đã ghi nhớ.
        q['dap_an_dung_hien_tai'] = goc
        for letter in ('A', 'B', 'C', 'D'):
            if q['cau_' + letter.lower()] == dap_dung_nd:
                q['dap_an_dung_hien_tai'] = letter
                break
    return questions


# ==========================================
# GHI DANH VÀO PHÒNG
#
# Trái tim của luồng vào thi. Dùng chung cho cả khách vãng lai (điền form) lẫn
# học sinh đã đăng nhập (vào thẳng).
#
# Điểm khó là xử lý người QUAY LẠI phòng — bấm Back, tải lại trang, hay cố tình
# vào lần nữa để thi thêm lượt. Nên trước khi tạo bản ghi mới, hàm tra lại mọi
# lượt cũ của MSSV này trong phòng và xử theo thứ tự:
#
#   đã thi đủ số lượt   -> chặn
#   đang làm dở         -> đưa về đúng bài đang làm, KHÔNG tạo bản ghi mới
#   đã bị từ chối       -> chặn hẳn
#   đang chờ duyệt      -> đưa về màn hình chờ, KHÔNG tạo bản ghi mới
#   chưa có gì          -> giờ mới thực sự tạo lượt thi mới
#
# Thứ tự này quan trọng. Bỏ nhánh "đang làm dở" thì mỗi lần F5 lại sinh một bài
# thi mới. Bỏ nhánh "đã bị từ chối" thì nút Từ chối của giáo viên thành vô
# nghĩa: người bị từ chối chỉ cần vào lại là có ngay lượt chờ duyệt mới.
#
# Hàm tự đóng conn/cursor ở MỌI nhánh trả về.
# ==========================================
def _ghi_danh_vao_phong(conn, cursor, phong, ma_phong,
                        ho_ten, ma_so_sv, email, lop, da_dang_nhap=False):
    """Ghi danh thí sinh vào phòng; trả về thẳng trang tiếp theo (redirect/render)."""
    trang_thai_ts = 'cho_duyet' if phong['can_duyet'] else 'da_duyet'

    if _phong_gioi_han_lop(cursor, phong['id']):
        if not da_dang_nhap:
            cursor.close(); conn.close()
            return render_template('loi_phong_thi.html',
                loi='Phòng thi này giới hạn theo danh sách lớp. '
                    'Vui lòng ĐĂNG NHẬP bằng tài khoản học sinh để dự thi.')
        # Không có tên trong danh sách lớp: cho vào hàng chờ chứ không đuổi.
        if _chuan_hoa_mssv(ma_so_sv) not in _roster_mssv(cursor, phong['id']):
            trang_thai_ts = 'cho_duyet'

    if not ma_so_sv:
        flash('Vui lòng nhập Mã số sinh viên!', 'danger')
        cursor.close(); conn.close()
        return render_template('tham_gia_phong.html', phong=phong,
                               yeu_cau_mat_khau=bool((phong.get('mat_khau') or '').strip()),
                               da_dang_nhap=da_dang_nhap)

    # Lấy MỌI lượt cũ của MSSV này trong phòng, rồi lần lượt xét 4 nhánh
    # (xem sơ đồ ở đầu phần). Chỉ đếm lượt ĐÃ NỘP là đã dùng — bài đang làm dở
    # chưa tính, nếu không thí sinh mất mạng giữa chừng sẽ bị tính oan một lượt.
    so_lan_toi_da = phong.get('so_lan_thi_toi_da') or 1
    cursor.execute("""
        SELECT id, da_nop_bai, trang_thai
        FROM thi_sinh
        WHERE phong_thi_id=%s AND ma_so_sv=%s
        ORDER BY id DESC
    """, (phong['id'], ma_so_sv))
    cac_lan = cursor.fetchall()
    so_lan_da_nop = sum(1 for x in cac_lan if x['da_nop_bai'])

    if so_lan_da_nop >= so_lan_toi_da:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
            loi=f'Mã số sinh viên "{ma_so_sv}" đã thi đủ '
                f'{so_lan_toi_da} lần cho phòng này. Không thể thi lại!')

    # Còn bài làm dở -> trả họ về đúng bài đó.
    dang_lam = next(
        (x for x in cac_lan
         if not x['da_nop_bai'] and x['trang_thai'] == 'da_duyet'),
        None
    )
    if dang_lam:
        _ghi_nho_thi_sinh(dang_lam['id'])
        cursor.close(); conn.close()
        return redirect(f'/lam_bai/{dang_lam["id"]}')

    # Đã bị từ chối -> chặn hẳn (nếu không, nút "Từ chối" của giáo viên vô nghĩa).
    if any(x['trang_thai'] == 'bi_tu_choi' and not x['da_nop_bai'] for x in cac_lan):
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
            loi='Giáo viên đã từ chối yêu cầu dự thi của bạn ở phòng này. '
                'Vui lòng liên hệ giáo viên nếu bạn cho rằng đây là nhầm lẫn.')

    # Đang xếp hàng chờ duyệt -> về lại màn hình chờ.
    cho_duyet_cu = next(
        (x for x in cac_lan if x['trang_thai'] == 'cho_duyet'), None
    )
    if cho_duyet_cu:
        _ghi_nho_thi_sinh(cho_duyet_cu['id'])
        cursor.close(); conn.close()
        return render_template('cho_duyet.html',
            ho_ten=ho_ten, ma_phong=ma_phong,
            thi_sinh_id=cho_duyet_cu['id'])

    try:
        cursor.execute("""
            INSERT INTO thi_sinh
            (phong_thi_id, ho_ten, ma_so_sv, email,
             lop, trang_thai, tong_so_cau, lan_thi)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (phong['id'], ho_ten, ma_so_sv, email,
              lop, trang_thai_ts, phong['tong_so_cau'],
              so_lan_da_nop + 1))
        conn.commit()
        thi_sinh_id = cursor.lastrowid
        cursor.close(); conn.close()

        # Gắn lượt thi vừa tạo vào trình duyệt này, để về sau họ mở được bài của
        # mình mà không cần tài khoản (xem phần chống IDOR ở trên).
        _ghi_nho_thi_sinh(thi_sinh_id)

        if trang_thai_ts == 'cho_duyet':
            return render_template('cho_duyet.html',
                ho_ten=ho_ten, ma_phong=ma_phong,
                thi_sinh_id=thi_sinh_id)
        return redirect(f'/lam_bai/{thi_sinh_id}')

    except Exception as e:
        conn.rollback()
        cursor.close(); conn.close()
        flash(f'Lỗi đăng ký: {str(e)}', 'danger')
        return render_template('tham_gia_phong.html', phong=phong)


# ==========================================
# VÀO PHÒNG THI
#
# Có hai đường vào, cả hai đều dồn về `_ghi_danh_vao_phong` ở trên:
#   /vao-phong   (POST) — học sinh đã đăng nhập, chọn phòng từ danh sách.
#   /thi/<mã>    (GET/POST) — vào bằng link/mã phòng, dùng cho cả khách vãng lai.
#
# Phòng có ba mốc thời gian, đừng lẫn:
#   thoi_gian_mo_phong  — thí sinh bắt đầu được vào NGỒI CHỜ.
#   thoi_gian_bat_dau   — giờ tính giờ làm bài.
#   thoi_gian_ket_thuc  — hết giờ, phòng đóng.
# ==========================================
GIOI_HAN_VAO_TRE_PHUT = 10   # vào trễ quá 10 phút kể từ giờ bắt đầu thì không nhận

def _phong_chua_vao_duoc(phong):
    """Phòng này có đang cho vào không? Trả về chuỗi lỗi, hoặc None nếu vào được."""
    now = datetime.now()
    if (phong['trang_thai'] == 'da_dong' or
            (phong['thoi_gian_ket_thuc'] and now > phong['thoi_gian_ket_thuc'])):
        return 'Phòng thi đã đóng!'
    if (phong['thoi_gian_mo_phong'] and now < phong['thoi_gian_mo_phong']):
        return ('Phòng chưa mở! Phòng sẽ mở lúc '
                f'{phong["thoi_gian_mo_phong"].strftime("%H:%M %d/%m/%Y")}')
    # Vào trễ quá hạn thì từ chối. Thông báo nêu rõ trễ bao lâu và giờ bắt đầu
    # là mấy giờ, để thí sinh biết mình nhầm hay phòng sai giờ mà còn khiếu nại.
    if phong['thoi_gian_bat_dau'] and now > phong['thoi_gian_bat_dau']:
        so_phut_tre = int((now - phong['thoi_gian_bat_dau']).total_seconds() // 60)
        if so_phut_tre > GIOI_HAN_VAO_TRE_PHUT:
            gio_bat_dau = phong['thoi_gian_bat_dau'].strftime('%H:%M %d/%m/%Y')
            if so_phut_tre < 60:
                mo_ta_tre = f'{so_phut_tre} phút'
            else:
                gio = so_phut_tre // 60
                phut = so_phut_tre % 60
                mo_ta_tre = f'{gio} giờ {phut} phút' if phut else f'{gio} giờ'
            return (f'Bạn đã vào trễ {mo_ta_tre} so với giờ bắt đầu '
                    f'({gio_bat_dau}). Phòng thi chỉ cho phép vào trong '
                    f'{GIOI_HAN_VAO_TRE_PHUT} phút đầu sau khi bắt đầu.')
    return None


@thi_sinh_bp.route('/vao-phong', methods=['POST'])
@limiter.limit("20 per minute")   # chặn dò mật khẩu phòng bằng script
def vao_phong_theo_id():
    """Vào phòng đã chọn từ danh sách (đường dành cho học sinh đã đăng nhập).

    Mật khẩu phòng là một trường RIÊNG, không phải mã phòng. Mã phòng thì công
    khai (nằm trên link), còn mật khẩu là thứ giáo viên đọc cho lớp lúc vào thi.
    Phòng bỏ trống mật khẩu thì vào tự do.
    """
    phong_id = (request.form.get('phong_id') or '').strip()
    mat_khau = (request.form.get('mat_khau') or '').strip()
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
        return render_template('loi_phong_thi.html', loi='Phòng thi không tồn tại!')
    # Mật khẩu phải được kiểm ở SERVER. Giao diện có ẩn ô nhập hay khóa nút thì
    # cũng vô nghĩa — người ta gửi thẳng POST tới đây được.
    mk_phong = (phong.get('mat_khau') or '').strip()
    if mk_phong and mk_phong != mat_khau:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html', loi='Mật khẩu phòng không đúng!')
    loi = _phong_chua_vao_duoc(phong)
    if loi:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html', loi=loi)
    # Lấy thẳng thông tin từ tài khoản, không hỏi lại — họ đã đăng nhập rồi.
    ho_ten   = (session.get('ho_ten') or '').strip()
    ma_so_sv = (session.get('ma_so_sv') or session.get('username') or '').strip()
    email    = (session.get('email') or '').strip()
    lop      = (session.get('lop') or '').strip()
    return _ghi_danh_vao_phong(conn, cursor, phong, phong['ma_phong'],
                               ho_ten, ma_so_sv, email, lop,
                               da_dang_nhap=(session.get('role') == 'hoc_sinh'))


@thi_sinh_bp.route('/thi/<ma_phong>', methods=['GET', 'POST'])
@limiter.limit("20 per minute", methods=['POST'])
def tham_gia_phong(ma_phong):
    """Vào phòng bằng mã (link chia sẻ). Nhận cả khách vãng lai lẫn người đã đăng nhập."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    # Mã phòng KHÔNG duy nhất — giáo viên hay dùng lại mã cũ cho kỳ thi mới. Nên
    # khi nhiều phòng trùng mã, ưu tiên phòng chưa đóng và mới nhất, để link cũ
    # luôn dẫn tới phòng còn hiệu lực chứ không rơi vào phòng năm ngoái.
    cursor.execute("""
        SELECT p.*, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS de_thi_id,
               d.ten_de_thi, d.tong_so_cau, m.ten_hoc_phan AS ten_mon
        FROM phong_thi p
        JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
        JOIN hoc_phan m ON d.hoc_phan_id=m.id
        WHERE p.ma_phong=%s
        ORDER BY (p.trang_thai='da_dong') ASC, p.id DESC
        LIMIT 1
    """, (ma_phong,))
    phong = cursor.fetchone()

    if not phong:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
                               loi='Mã phòng thi không tồn tại!')

    loi = _phong_chua_vao_duoc(phong)
    if loi:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html', loi=loi)

    # Chặn khách vãng lai ở phòng giới hạn lớp NGAY TỪ ĐÂY, trước cả màn hình
    # điền form — để họ không kịp gõ MSSV của người khác vào.
    if session.get('role') != 'hoc_sinh' and _phong_gioi_han_lop(cursor, phong['id']):
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
            loi='Phòng thi này giới hạn theo danh sách lớp. '
                'Vui lòng ĐĂNG NHẬP bằng tài khoản học sinh để dự thi.')

    mk_phong = (phong.get('mat_khau') or '').strip()
    da_dn    = (session.get('role') == 'hoc_sinh')

    def _lay_thong_tin_hs():
        """Thông tin thí sinh lấy từ tài khoản đang đăng nhập.

        Học sinh chưa khai MSSV thì lấy username thay thế. Bắt buộc phải có một
        định danh nào đó, vì toàn bộ cơ chế đếm số lượt thi dựa vào cột ma_so_sv
        — để trống là họ thi lại được vô số lần.
        """
        return (
            (session.get('ho_ten') or '').strip(),
            (session.get('ma_so_sv') or session.get('username') or '').strip(),
            (session.get('email') or '').strip(),
            (session.get('lop') or '').strip(),
        )

    # POST = đã bấm nút vào thi: kiểm mật khẩu rồi ghi danh.
    if request.method == 'POST':
        if mk_phong and (request.form.get('mat_khau') or '').strip() != mk_phong:
            cursor.close(); conn.close()
            return render_template('tham_gia_phong.html', phong=phong,
                                   yeu_cau_mat_khau=True, da_dang_nhap=da_dn,
                                   loi_mat_khau='Mật khẩu phòng không đúng!')
        if da_dn:
            ho_ten, ma_so_sv, email, lop = _lay_thong_tin_hs()
            return _ghi_danh_vao_phong(conn, cursor, phong, ma_phong,
                                       ho_ten, ma_so_sv, email, lop,
                                       da_dang_nhap=True)
        ho_ten   = request.form.get('ho_ten', '').strip()
        ma_so_sv = request.form.get('ma_so_sv', '').strip()
        email    = request.form.get('email', '').strip()
        lop      = request.form.get('lop', '').strip()
        return _ghi_danh_vao_phong(conn, cursor, phong, ma_phong,
                                   ho_ten, ma_so_sv, email, lop)

    # Đã đăng nhập và phòng không đặt mật khẩu: chẳng có gì để hỏi, vào luôn.
    if da_dn and not mk_phong:
        ho_ten, ma_so_sv, email, lop = _lay_thong_tin_hs()
        return _ghi_danh_vao_phong(conn, cursor, phong, ma_phong,
                                   ho_ten, ma_so_sv, email, lop,
                                   da_dang_nhap=True)

    # Còn lại thì hiện form: khách vãng lai khai họ tên/MSSV, người đã đăng nhập
    # chỉ phải gõ mật khẩu phòng.
    cursor.close()
    conn.close()
    return render_template('tham_gia_phong.html', phong=phong,
                           yeu_cau_mat_khau=bool(mk_phong), da_dang_nhap=da_dn)


# ==========================================
# MÀN HÌNH CHỜ (trang cho_duyet.html hỏi lại endpoint này mỗi vài giây)
# ==========================================
@thi_sinh_bp.route('/kiem_tra_duyet/<int:thi_sinh_id>')
def kiem_tra_duyet(thi_sinh_id):
    """Thí sinh đang ngồi chờ: đã được duyệt chưa, tới giờ thi chưa?

    Trang chờ gọi lại đường này liên tục. Nhờ vậy khi giáo viên bấm Duyệt, hoặc
    khi đồng hồ chạy tới giờ thi, đề tự bật ra mà thí sinh không phải F5.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT ts.trang_thai, ts.da_nop_bai,
               p.thoi_gian_bat_dau, p.thoi_luong_lam_bai,
               p.trang_thai AS phong_trang_thai
        FROM thi_sinh ts
        JOIN phong_thi p ON ts.phong_thi_id=p.id
        WHERE ts.id=%s
    """, (thi_sinh_id,))
    ts = cursor.fetchone()
    cursor.close()
    conn.close()

    if not ts:
        return jsonify({'trang_thai': 'not_found'}), 404

    # Trả về SỐ GIÂY còn lại, không trả mốc giờ. Đồng hồ máy thí sinh có thể sai
    # vài phút; đếm ngược theo số giây do server tính thì ai cũng như ai.
    giay_den_gio = 0
    if ts['thoi_gian_bat_dau']:
        delta = (ts['thoi_gian_bat_dau'] - datetime.now()).total_seconds()
        giay_den_gio = max(0, int(delta))

    return jsonify({
        'trang_thai'   : ts['trang_thai'],
        'da_nop_bai'   : int(ts['da_nop_bai'] or 0),
        'phong_dong'   : ts['phong_trang_thai'] == 'da_dong',
        'giay_den_gio' : giay_den_gio,
        'thoi_luong'   : ts['thoi_luong_lam_bai'],
        'da_bat_dau'   : giay_den_gio <= 0,
    })


# ==========================================
# LÀM BÀI VÀ NỘP BÀI
#
# Một route lo cả hai việc:
#   GET  = mở đề ra làm bài.
#   POST = nộp bài, chấm điểm ngay tại chỗ.
#
# Trước khi cho vào, hàm dựng một loạt cửa kiểm tra theo thứ tự: có tồn tại
# không -> có phải bài của trình duyệt này không -> đã được duyệt chưa -> nộp
# rồi hay chưa -> tới giờ thi chưa.
# ==========================================
@thi_sinh_bp.route('/lam_bai/<int:thi_sinh_id>', methods=['GET', 'POST'])
def lam_bai(thi_sinh_id):
    """Mở đề (GET) hoặc nhận bài nộp và chấm điểm (POST)."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT ts.*, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS phong_de_thi_id,
               p.thoi_luong_lam_bai,
               p.tron_cau_hoi, p.tron_dap_an, p.hien_thi_dap_an,
               p.ma_phong, p.thoi_gian_bat_dau, p.thoi_gian_ket_thuc,
               p.trang_thai AS phong_trang_thai, d.tong_diem
        FROM thi_sinh ts
        JOIN phong_thi p ON ts.phong_thi_id=p.id
        JOIN de_thi d ON d.id = COALESCE(ts.de_thi_id, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id))
        WHERE ts.id=%s
    """, (thi_sinh_id,))
    thi_sinh = cursor.fetchone()

    if not thi_sinh:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
                               loi='Không tìm thấy thí sinh!')
    if not _co_quyen_xem(thi_sinh_id):
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
                               loi='Bạn không có quyền truy cập bài thi này!')
    if thi_sinh['trang_thai'] != 'da_duyet':
        cursor.close(); conn.close()
        return render_template('cho_duyet.html',
            ho_ten=thi_sinh['ho_ten'],
            ma_phong=thi_sinh['ma_phong'],
            thi_sinh_id=thi_sinh_id)
    if thi_sinh['da_nop_bai']:
        cursor.close(); conn.close()
        return redirect(f'/ket_qua_thi_sinh/{thi_sinh_id}')

    # Được duyệt rồi nhưng chưa tới giờ: cho ngồi màn hình chờ, đề tự bật khi
    # đồng hồ điểm giờ. Nếu để vào sớm thì thí sinh có thêm thời gian đọc đề.
    now = datetime.now()
    if (thi_sinh['thoi_gian_bat_dau'] and
            now < thi_sinh['thoi_gian_bat_dau']):
        cursor.close(); conn.close()
        return render_template('cho_duyet.html',
            ho_ten=thi_sinh['ho_ten'],
            ma_phong=thi_sinh['ma_phong'],
            thi_sinh_id=thi_sinh_id)

    # Phát mã đề. Phòng có thể có nhiều mã đề, mỗi thí sinh bốc ngẫu nhiên một
    # mã. Bốc XONG THÌ GHI NGAY vào DB và từ đó dùng cố định — nếu bốc lại ở mỗi
    # request, thí sinh chỉ cần F5 là đổi sang đề khác, và bài nộp sẽ được chấm
    # bằng đáp án của một đề mà họ chưa hề nhìn thấy.
    if not thi_sinh.get('de_thi_id'):
        bo_de = _bo_de_phong(cursor, thi_sinh['phong_thi_id'],
                             thi_sinh['phong_de_thi_id'])
        de_phat = random.choice(bo_de) if bo_de else thi_sinh['phong_de_thi_id']
        cursor.execute("UPDATE thi_sinh SET de_thi_id=%s WHERE id=%s",
                       (de_phat, thi_sinh_id))
        conn.commit()
        thi_sinh['de_thi_id'] = de_phat

    cursor.execute(
        "SELECT * FROM cau_hoi WHERE de_thi_id=%s",
        (thi_sinh['de_thi_id'],)
    )
    questions = cursor.fetchall()

    # Trộn đề. Chạy ở CẢ GET lẫn POST và cho ra kết quả y hệt nhau — đó là điều
    # kiện sống còn để chấm đúng (xem phần trộn đề ở đầu file).
    _sap_xep_cau_hoi(questions, thi_sinh_id,
                     thi_sinh['tron_cau_hoi'], thi_sinh['tron_dap_an'])

    # Mốc "vào phòng" chỉ ghi đúng MỘT LẦN. Ghi đè ở mỗi lần mở trang thì thí
    # sinh chỉ việc F5 là đồng hồ làm bài được làm mới, thi mãi không hết giờ.
    now = datetime.now()
    if not thi_sinh['thoi_gian_vao']:
        cursor.execute(
            "UPDATE thi_sinh SET thoi_gian_vao=NOW() WHERE id=%s",
            (thi_sinh_id,)
        )
        conn.commit()
        vao_time = now
    else:
        vao_time = thi_sinh['thoi_gian_vao']

    # ---------- NỘP BÀI ----------
    # Ba hàng rào chặn nộp muộn, và cả ba đều cần thiết vì chúng bịt các lỗ khác
    # nhau. Xem giải thích ở hàng rào thứ ba.
    if request.method == 'POST':
        if thi_sinh['phong_trang_thai'] == 'da_dong':
            cursor.close(); conn.close()
            return render_template('loi_phong_thi.html',
                                   loi='Phòng thi đã đóng, không thể nộp bài!')
        if (thi_sinh.get('thoi_gian_ket_thuc') and
                now > thi_sinh['thoi_gian_ket_thuc']):
            cursor.close(); conn.close()
            return render_template('loi_phong_thi.html',
                                   loi='Đã hết thời gian làm bài!')
        # Hàng rào quan trọng nhất: đồng hồ RIÊNG của từng thí sinh, tính từ lúc
        # họ mở đề. Hai hàng rào trên không cứu được trường hợp phòng không đặt
        # giờ kết thúc (cột đó là NULL, mọi so sánh đều trượt). Khi ấy đồng hồ
        # đếm ngược chỉ tồn tại trong trình duyệt — thí sinh mở đề, để đó ăn cơm
        # vài tiếng rồi quay lại nộp vẫn được nhận, nếu thiếu dòng này.
        #
        # Cộng 60 giây khoan dung cho độ trễ đường truyền, để người bấm nộp đúng
        # giây cuối không bị đánh trượt oan.
        han_nop = vao_time + timedelta(minutes=thi_sinh['thoi_luong_lam_bai'],
                                       seconds=60)
        if now > han_nop:
            cursor.close(); conn.close()
            return render_template('loi_phong_thi.html',
                                   loi='Đã hết thời gian làm bài!')
        try:
            so_cau_dung = 0
            tong_so     = len(questions)

            for q in questions:
                q_id = q['id']
                # Chấm điểm trong KHÔNG GIAN ĐÃ TRỘN, không phải theo đáp án gốc.
                # Thí sinh nhìn thấy đề đã trộn và bấm "C" trên đề đó, nên phải so
                # với `dap_an_dung_hien_tai` (chữ cái sau khi trộn), chứ không phải
                # `dap_an_dung` trong CSDL. Lẫn hai cái này là sai điểm cả phòng.
                dap_chon = request.form.get(f'cau_{q_id}', '').strip().upper()

                dap_dung   = q['dap_an_dung_hien_tai']
                is_correct = 1 if dap_chon and dap_chon == dap_dung else 0
                if is_correct:
                    so_cau_dung += 1

                cursor.execute("""
                    INSERT INTO bai_lam
                    (thi_sinh_id, cau_hoi_id,
                     dap_an_chon, is_correct)
                    VALUES (%s,%s,%s,%s)
                """, (thi_sinh_id, q_id, dap_chon, is_correct))

            tong_diem = float(thi_sinh.get('tong_diem') or 10)
            diem = round(so_cau_dung / tong_so * tong_diem, 2) if tong_so > 0 else 0
            cursor.execute("""
                UPDATE thi_sinh
                SET da_nop_bai=1, thoi_gian_nop=NOW(),
                    so_cau_dung=%s, tong_so_cau=%s, diem=%s
                WHERE id=%s
            """, (so_cau_dung, tong_so, diem, thi_sinh_id))
            conn.commit()
            cursor.close(); conn.close()
            return redirect(f'/ket_qua_thi_sinh/{thi_sinh_id}')

        except Exception as e:
            conn.rollback()
            flash(f'Lỗi nộp bài: {str(e)}', 'danger')

    cursor.close()
    conn.close()

    # Thời gian còn lại do SERVER tính, dựa trên mốc vào phòng đã lưu trong DB.
    # Nhờ vậy F5 không làm đồng hồ chạy lại từ đầu.
    tong_giay = thi_sinh['thoi_luong_lam_bai'] * 60
    con_lai   = int(tong_giay - (now - vao_time).total_seconds())
    # Nhưng cũng không được vượt quá giờ đóng phòng: thí sinh vào muộn 5 phút
    # trước giờ đóng thì chỉ còn 5 phút, không phải trọn thời lượng làm bài.
    if thi_sinh.get('thoi_gian_ket_thuc'):
        den_dong_phong = int((thi_sinh['thoi_gian_ket_thuc'] - now).total_seconds())
        con_lai = min(con_lai, den_dong_phong)
    con_lai = max(con_lai, 0)

    return render_template('lam_bai.html',
        thi_sinh=thi_sinh,
        questions=questions,
        thoi_gian_con_lai=con_lai)


# ==========================================
# XEM KẾT QUẢ
#
# Hai đường xem, khác nhau về mức độ chặt:
#   /ket_qua_thi_sinh — hiện ngay sau khi nộp. Chi tiết đáp án chỉ hiện nếu
#                       giáo viên bật `hien_thi_dap_an` cho phòng.
#   /xem_lai_bai      — xem lại về sau, phải đăng nhập, và CHỈ khi phòng đã
#                       kết thúc (nếu không, người thi sớm sẽ chụp màn hình đáp
#                       án gửi cho bạn chưa thi).
# ==========================================
@thi_sinh_bp.route('/ket_qua_thi_sinh/<int:thi_sinh_id>')
def ket_qua_thi_sinh(thi_sinh_id):
    """Trang điểm số hiện ngay sau khi nộp bài."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT ts.*, p.hien_thi_dap_an, p.ten_phong,
               (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS phong_de_thi_id,
               p.tron_cau_hoi, p.tron_dap_an,
               d.ten_de_thi, d.tong_diem, m.ten_hoc_phan AS ten_mon
        FROM thi_sinh ts
        JOIN phong_thi p ON ts.phong_thi_id=p.id
        JOIN de_thi d    ON d.id = COALESCE(ts.de_thi_id, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id))
        JOIN hoc_phan m  ON d.hoc_phan_id=m.id
        WHERE ts.id=%s
    """, (thi_sinh_id,))
    thi_sinh = cursor.fetchone()

    if thi_sinh and not _co_quyen_xem(thi_sinh_id):
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
                               loi='Bạn không có quyền xem kết quả này!')

    bai_lam_detail = []
    if thi_sinh and thi_sinh['hien_thi_dap_an']:
        cursor.execute("""
            SELECT cau_hoi_id, dap_an_chon, is_correct
            FROM bai_lam WHERE thi_sinh_id=%s
        """, (thi_sinh_id,))
        da_chon = {r['cau_hoi_id']: r for r in cursor.fetchall()}

        # Gọi lại đúng hàm trộn với đúng thi_sinh_id để DỰNG LẠI tờ đề y hệt lúc
        # thi. Không làm vậy thì "đáp án bạn chọn: C" sẽ trỏ vào một phương án
        # khác hẳn cái mà thí sinh đã bấm.
        cursor.execute(
            "SELECT * FROM cau_hoi WHERE de_thi_id=%s",
            (thi_sinh['de_thi_id'],)
        )
        questions = cursor.fetchall()
        _sap_xep_cau_hoi(questions, thi_sinh_id,
                         thi_sinh['tron_cau_hoi'], thi_sinh['tron_dap_an'])

        for q in questions:
            ans = da_chon.get(q['id'])
            bai_lam_detail.append({
                'noi_dung'   : q['noi_dung'],
                'cau_a'      : q['cau_a'], 'cau_b': q['cau_b'],
                'cau_c'      : q['cau_c'], 'cau_d': q['cau_d'],
                'dap_an_dung': q['dap_an_dung_hien_tai'],
                'dap_an_chon': (ans['dap_an_chon'] if ans else ''),
                'is_correct' : (ans['is_correct'] if ans else 0),
            })

    cursor.close()
    conn.close()
    return render_template('ket_qua_thi_sinh.html',
                           thi_sinh=thi_sinh,
                           bai_lam_detail=bai_lam_detail)


@thi_sinh_bp.route('/xem_lai_bai/<int:thi_sinh_id>')
def xem_lai_bai(thi_sinh_id):
    """Học sinh mở lại bài thi cũ từ trang lịch sử.

    Chặt hơn trang kết quả ở hai điểm:
      - Phải ĐĂNG NHẬP. Quyền được xét theo cả ba manh mối: session ghi danh,
        MSSV, và email — vì học sinh có thể đã thi từ máy khác, session cũ mất.
      - Phòng phải KẾT THÚC rồi. Cho xem sớm thì người thi ca đầu chụp màn hình
        đáp án gửi cho ca sau. Admin được miễn để còn hỗ trợ khi có khiếu nại.
    """
    if 'user_id' not in session:
        flash('Vui lòng đăng nhập để xem lại bài thi!', 'warning')
        return redirect(url_for('auth.login'))

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT ts.*, p.ten_phong, p.hien_thi_dap_an,
               (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id) AS phong_de_thi_id,
               p.tron_cau_hoi, p.tron_dap_an,
               p.trang_thai AS phong_trang_thai, p.thoi_gian_ket_thuc,
               d.ten_de_thi, d.tong_diem, m.ten_hoc_phan AS ten_mon, u.ho_ten AS ten_gv
        FROM thi_sinh ts
        JOIN phong_thi p ON ts.phong_thi_id = p.id
        JOIN de_thi d    ON d.id = COALESCE(ts.de_thi_id, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id))
        JOIN hoc_phan m  ON d.hoc_phan_id = m.id
        JOIN nguoi_dung u     ON p.user_id = u.id
        WHERE ts.id=%s
    """, (thi_sinh_id,))
    thi_sinh = cursor.fetchone()

    if not thi_sinh:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
                               loi='Không tìm thấy bài thi!')

    # Ba manh mối nhận chủ nhân bài thi, chỉ cần khớp một: session đã ghi danh,
    # trùng MSSV, hoặc trùng email. Chỉ dựa vào session thì học sinh đổi máy/xóa
    # cookie là mất luôn quyền xem bài của chính mình.
    dinh_danh = _dinh_danh_hoc_sinh()
    email_hs  = (session.get('email') or '').strip()
    la_chu = (
        _co_quyen_xem(thi_sinh_id)
        or (dinh_danh and thi_sinh.get('ma_so_sv') == dinh_danh)
        or (email_hs and (thi_sinh.get('email') or '') == email_hs)
    )
    if not la_chu:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
                               loi='Bạn không có quyền xem lại bài thi này!')

    if not thi_sinh['da_nop_bai']:
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
                               loi='Bài thi này chưa được nộp nên chưa có gì để xem lại.')

    now = datetime.now()
    da_ket_thuc = bool(
        thi_sinh['phong_trang_thai'] == 'da_dong'
        or (thi_sinh['thoi_gian_ket_thuc'] and now > thi_sinh['thoi_gian_ket_thuc'])
    )
    if not da_ket_thuc and session.get('role') != 'admin':
        cursor.close(); conn.close()
        return render_template('loi_phong_thi.html',
            loi='Phòng thi chưa kết thúc. Bạn chỉ có thể xem lại bài làm sau '
                'khi phòng thi đã đóng hoặc đã hết giờ.')

    # Lại dựng lại tờ đề đã trộn, giống hệt trang kết quả ở trên.
    cursor.execute(
        "SELECT cau_hoi_id, dap_an_chon, is_correct FROM bai_lam WHERE thi_sinh_id=%s",
        (thi_sinh_id,)
    )
    da_chon = {r['cau_hoi_id']: r for r in cursor.fetchall()}

    cursor.execute("SELECT * FROM cau_hoi WHERE de_thi_id=%s",
                   (thi_sinh['de_thi_id'],))
    questions = cursor.fetchall()
    _sap_xep_cau_hoi(questions, thi_sinh_id,
                     thi_sinh['tron_cau_hoi'], thi_sinh['tron_dap_an'])

    chi_tiet = []
    for q in questions:
        ans = da_chon.get(q['id'])
        chi_tiet.append({
            'noi_dung'   : q['noi_dung'],
            'cau_a'      : q['cau_a'], 'cau_b': q['cau_b'],
            'cau_c'      : q['cau_c'], 'cau_d': q['cau_d'],
            'dap_an_dung': q['dap_an_dung_hien_tai'],
            'dap_an_chon': (ans['dap_an_chon'] if ans else ''),
            'is_correct' : (ans['is_correct'] if ans else 0),
        })

    cursor.close(); conn.close()
    return render_template('xem_lai_bai.html',
                           thi_sinh=thi_sinh,
                           chi_tiet=chi_tiet,
                           hien_dap_an=bool(thi_sinh['hien_thi_dap_an']))


# ==========================================
# GIÁM SÁT: NHẬN ẢNH WEBCAM TỪ THÍ SINH
# Trình duyệt thí sinh chụp ảnh webcam mỗi vài giây và POST lên đây.
# Chỉ lưu ảnh MỚI NHẤT cho mỗi thí sinh (upsert) -> không phình DB.
# ==========================================
@thi_sinh_bp.route('/giam_sat/upload/<int:thi_sinh_id>', methods=['POST'])
def giam_sat_upload(thi_sinh_id):
    if not _co_quyen_xem(thi_sinh_id):
        return jsonify({'ok': False}), 403
    data = request.get_json(silent=True) or {}
    anh  = data.get('anh', '')
    # Chặn dữ liệu rỗng hoặc quá lớn (ảnh nén ~10-40KB là đủ)
    if not anh.startswith('data:image') or len(anh) > 2_000_000:
        return jsonify({'ok': False}), 400

    conn   = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO giam_sat (thi_sinh_id, anh_webcam)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE anh_webcam=VALUES(anh_webcam)
        """, (thi_sinh_id, anh))
        conn.commit()
    except Exception:
        conn.rollback()
        return jsonify({'ok': False}), 500
    finally:
        cursor.close()
        conn.close()
    return jsonify({'ok': True})


# ==========================================
# GIÁM SÁT: GHI NHẬN THÍ SINH RỜI KHỎI TAB / CỬA SỔ THI
# Trả về tổng số lần đã rời để client quyết định cảnh báo hay tự nộp.
# ==========================================
@thi_sinh_bp.route('/giam_sat/roi_tab/<int:thi_sinh_id>', methods=['POST'])
def giam_sat_roi_tab(thi_sinh_id):
    if not _co_quyen_xem(thi_sinh_id):
        return jsonify({'ok': False}), 403

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    so_lan = 1
    try:
        cursor.execute("""
            INSERT INTO giam_sat (thi_sinh_id, so_lan_roi_tab)
            VALUES (%s, 1)
            ON DUPLICATE KEY UPDATE so_lan_roi_tab = so_lan_roi_tab + 1
        """, (thi_sinh_id,))
        conn.commit()
        cursor.execute(
            "SELECT so_lan_roi_tab FROM giam_sat WHERE thi_sinh_id=%s",
            (thi_sinh_id,)
        )
        row = cursor.fetchone()
        if row:
            so_lan = row['so_lan_roi_tab']
    except Exception:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
    return jsonify({'ok': True, 'so_lan': so_lan})
