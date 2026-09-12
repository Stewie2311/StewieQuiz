"""
Tổng hợp dữ liệu THỐNG KÊ cho trang Admin Dashboard.

Tách riêng phần "đọc số liệu" khỏi route (routes/admin.py) để dễ bảo trì và
kiểm thử. Mọi hàm ở đây chỉ ĐỌC từ MySQL (không ghi), nhận sẵn một cursor
(dictionary=True) do route mở/đóng, và trả về dict/list sạch (đã đổi Decimal
-> float, datetime -> chuỗi hiển thị kiểu Việt Nam) để jsonify không lỗi.

Nguyên tắc:
- KHÔNG tạo dữ liệu giả. Nếu bảng rỗng thì trả 0 / danh sách rỗng, phía giao
  diện tự hiển thị "Chưa có dữ liệu".
- Bộ lọc (thời gian / học phần / giảng viên / trạng thái phòng) áp dụng ở nơi
  có ý nghĩa; số liệu cấu trúc (tổng người dùng, học phần...) là trạng thái
  hiện tại nên không phụ thuộc mốc thời gian.
"""
import re
from datetime import date, datetime
from decimal import Decimal


# ─────────────────────────────────────────────────────────────
# Tiện ích
# ─────────────────────────────────────────────────────────────
def _f(v):
    """Decimal/None -> float an toàn cho JSON."""
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _dt(v):
    """datetime/date -> chuỗi 'dd/MM/YYYY HH:MM' kiểu Việt Nam (hoặc None)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime('%d/%m/%Y %H:%M')
    if isinstance(v, date):
        return v.strftime('%d/%m/%Y')
    return str(v)


def _semester_start():
    """Mốc bắt đầu học kỳ hiện tại: HK1 = 1/7 (tháng 7-12), HK2 = 1/1 (tháng 1-6)."""
    today = date.today()
    return date(today.year, 7, 1) if today.month >= 7 else date(today.year, 1, 1)


def parse_filters(args):
    """Đọc & chuẩn hoá tham số lọc từ query string."""
    def _int(name):
        raw = (args.get(name) or '').strip()
        if raw.isdigit():
            return int(raw)
        return None

    rng = (args.get('range') or 'all').strip()
    if rng not in ('today', '7d', '30d', 'semester', 'all'):
        rng = 'all'

    room_status = (args.get('room_status') or '').strip()
    if room_status not in ('chuan_bi', 'dang_thi', 'da_dong'):
        room_status = ''

    return {
        'range':       rng,
        'subject':     _int('subject'),      # hoc_phan_id
        'teacher':     _int('teacher'),      # nguoi_dung.id (giáo viên)
        'room_status': room_status,
    }


def _range_clause(col, rng):
    """Trả về (fragment_sql, params) cho lọc theo thời gian trên cột `col`."""
    if rng == 'today':
        return f" AND DATE({col}) = CURDATE()", []
    if rng == '7d':
        return f" AND {col} >= (NOW() - INTERVAL 7 DAY)", []
    if rng == '30d':
        return f" AND {col} >= (NOW() - INTERVAL 30 DAY)", []
    if rng == 'semester':
        return f" AND {col} >= %s", [_semester_start()]
    return "", []   # 'all'


def _scalar(cursor, sql, params=None):
    """Chạy 1 câu SELECT trả về 1 giá trị (cột đặt alias 'c')."""
    cursor.execute(sql, params or [])
    row = cursor.fetchone()
    if not row:
        return 0
    # dictionary cursor -> lấy giá trị đầu tiên
    return list(row.values())[0]


# ─────────────────────────────────────────────────────────────
# 1) KPI TỔNG QUAN  (+ bảng top giảng viên / học phần / phòng thi)
# ─────────────────────────────────────────────────────────────
def get_stats(cursor, flt):
    """Các ô số liệu tổng quan + mấy bảng xếp hạng ở đầu dashboard.

    Lưu ý về bộ lọc: những con số mang tính CẤU TRÚC (tổng người dùng, tổng học
    phần, tổng lớp) là ảnh chụp hiện trạng nên KHÔNG áp bộ lọc thời gian — hỏi
    "có bao nhiêu học phần trong 7 ngày qua" là câu hỏi vô nghĩa. Chỉ những con
    số mang tính HOẠT ĐỘNG (câu hỏi thêm mới, đề tạo, lượt thi) mới lọc theo
    khoảng thời gian.
    """
    rng      = flt['range']
    subject  = flt['subject']
    teacher  = flt['teacher']

    total_teachers = _scalar(cursor, "SELECT COUNT(*) c FROM nguoi_dung WHERE role='giao_vien'")
    total_students = _scalar(cursor, "SELECT COUNT(*) c FROM nguoi_dung WHERE role='hoc_sinh'")
    total_users    = total_teachers + total_students

    total_classes = _scalar(cursor, "SELECT COUNT(*) c FROM lop")
    # Sĩ số sinh viên nằm rải ở HAI nơi và không nơi nào đầy đủ: danh sách lớp
    # giáo viên import (lop_sinh_vien), và ô "lớp" trong hồ sơ tài khoản
    # (nguoi_dung.lop). Lấy số lớn hơn để không báo thiếu; cộng lại thì sẽ đếm
    # trùng những em có mặt ở cả hai nơi.
    sv_roster = _scalar(cursor, "SELECT COUNT(*) c FROM lop_sinh_vien")
    sv_account = _scalar(cursor,
        "SELECT COUNT(*) c FROM nguoi_dung "
        "WHERE role='hoc_sinh' AND lop IS NOT NULL AND TRIM(lop) <> ''")
    total_class_students = max(int(sv_roster), int(sv_account))
    classes_with_room = _scalar(cursor, "SELECT COUNT(DISTINCT lop_id) c FROM phong_thi_lop")
    total_subjects = _scalar(cursor, "SELECT COUNT(*) c FROM hoc_phan")

    # ── Ngân hàng câu hỏi (lọc theo học phần / giảng viên nếu có) ──
    sql = "SELECT COUNT(*) c FROM ngan_hang_cau_hoi WHERE 1=1"
    p = []
    if subject:
        sql += " AND hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND nguoi_tao_id=%s"; p.append(teacher)
    r, _p = _range_clause('ngay_them', rng)
    sql += r; p += _p
    total_questions = _scalar(cursor, sql, p)

    # ── Câu hỏi do AI tạo (bảng cau_hoi = câu hỏi sinh ra trong các đề thi) ──
    sql = ("SELECT COUNT(*) c FROM cau_hoi ch "
           "JOIN de_thi d ON ch.de_thi_id=d.id WHERE 1=1")
    p = []
    if subject:
        sql += " AND d.hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND d.user_id=%s"; p.append(teacher)
    r, _p = _range_clause('d.ngay_tao', rng)
    sql += r; p += _p
    ai_questions = _scalar(cursor, sql, p)

    # ── Đề thi ──
    sql = "SELECT COUNT(*) c FROM de_thi WHERE 1=1"
    p = []
    if subject:
        sql += " AND hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND user_id=%s"; p.append(teacher)
    r, _p = _range_clause('ngay_tao', rng)
    sql += r; p += _p
    total_exams = _scalar(cursor, sql, p)

    # ── Phòng thi ──
    sql = "SELECT COUNT(*) c FROM phong_thi WHERE 1=1"
    p = []
    if teacher:
        sql += " AND user_id=%s"; p.append(teacher)
    r, _p = _range_clause('ngay_tao', rng)
    sql += r; p += _p
    total_rooms = _scalar(cursor, sql, p)

    # Phòng đang mở (đang thi) — trạng thái hiện tại, không theo thời gian
    sql = "SELECT COUNT(*) c FROM phong_thi WHERE trang_thai='dang_thi'"
    p = []
    if teacher:
        sql += " AND user_id=%s"; p.append(teacher)
    open_rooms = _scalar(cursor, sql, p)

    # ── Lượt làm bài (đã nộp) + điểm trung bình ──
    sql = ("SELECT COUNT(*) c FROM thi_sinh ts "
           "JOIN de_thi d ON ts.de_thi_id=d.id "
           "WHERE ts.da_nop_bai=1")
    p = []
    if subject:
        sql += " AND d.hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND d.user_id=%s"; p.append(teacher)
    r, _p = _range_clause('ts.thoi_gian_nop', rng)
    sql += r; p += _p
    total_submissions = _scalar(cursor, sql, p)

    sql = ("SELECT ROUND(AVG(ts.diem),2) c FROM thi_sinh ts "
           "JOIN de_thi d ON ts.de_thi_id=d.id "
           "WHERE ts.da_nop_bai=1")
    p = []
    if subject:
        sql += " AND d.hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND d.user_id=%s"; p.append(teacher)
    r, _p = _range_clause('ts.thoi_gian_nop', rng)
    sql += r; p += _p
    average_score = _f(_scalar(cursor, sql, p))

    # ── Lượt gọi AI ──
    # Trong khoảng lọc (mặc định 'all' = tổng cộng); và riêng "hôm nay".
    sql = "SELECT COALESCE(SUM(so_lan),0) c FROM api_usage WHERE 1=1"
    p = []
    if teacher:
        sql += " AND user_id=%s"; p.append(teacher)
    r, _p = _range_clause('ngay', rng)
    sql += r; p += _p
    ai_usage_range = _scalar(cursor, sql, p)

    sql = "SELECT COALESCE(SUM(so_lan),0) c FROM api_usage WHERE ngay=CURDATE()"
    p = []
    if teacher:
        sql += " AND user_id=%s"; p.append(teacher)
    ai_usage_today = _scalar(cursor, sql, p)

    return {
        'totalUsers':          int(total_users),
        'totalTeachers':       int(total_teachers),
        'totalStudents':       int(total_students),
        'totalClasses':        int(total_classes),
        'totalClassStudents':  int(total_class_students),
        'classesWithRoom':     int(classes_with_room),
        'totalSubjects':       int(total_subjects),
        'totalQuestions':      int(total_questions),
        'aiGeneratedQuestions': int(ai_questions),
        'totalExams':          int(total_exams),
        'totalExamRooms':      int(total_rooms),
        'openExamRooms':       int(open_rooms),
        'totalSubmissions':    int(total_submissions),
        'averageScore':        average_score,
        'aiUsageToday':        int(ai_usage_today),
        'aiUsageRange':        int(ai_usage_range),
        'topTeachers':         _top_teachers(cursor, flt),
        'topSubjects':         _top_subjects(cursor, flt),
        'activeExamRooms':     _active_rooms(cursor, flt),
        'aiUsageByUser':       _ai_usage_by_user(cursor, flt),
    }


def _ai_usage_by_user(cursor, flt):
    """Số lượt dùng AI để tạo câu hỏi, theo từng người dùng (kèm tổng để tính tỷ lệ)."""
    sql = """
        SELECT u.ho_ten, u.role, SUM(a.so_lan) AS so_lan
        FROM api_usage a LEFT JOIN nguoi_dung u ON a.user_id=u.id
        WHERE 1=1
    """
    p = []
    if flt['teacher']:
        sql += " AND a.user_id=%s"; p.append(flt['teacher'])
    sql += " GROUP BY u.id, u.ho_ten, u.role ORDER BY so_lan DESC LIMIT 12"
    cursor.execute(sql, p)
    rows = cursor.fetchall()
    total = sum(int(r['so_lan']) for r in rows)
    _role = {'giao_vien': 'Giảng viên', 'hoc_sinh': 'Sinh viên', 'admin': 'Quản trị viên'}
    return {
        'total': int(total),
        'items': [{
            'ho_ten':  r['ho_ten'] or '—',
            'vai_tro': _role.get(r['role'], r['role'] or ''),
            'so_lan':  int(r['so_lan']),
        } for r in rows],
    }


def _top_teachers(cursor, flt):
    """Top giảng viên hoạt động nhiều nhất (câu hỏi / đề thi / phòng thi)."""
    cursor.execute("""
        SELECT u.id, u.ho_ten,
               (SELECT COUNT(*) FROM ngan_hang_cau_hoi n WHERE n.nguoi_tao_id=u.id) AS so_cau,
               (SELECT COUNT(*) FROM de_thi d          WHERE d.user_id=u.id)       AS so_de,
               (SELECT COUNT(*) FROM phong_thi p       WHERE p.user_id=u.id)       AS so_phong
        FROM nguoi_dung u
        WHERE u.role='giao_vien'
        ORDER BY (so_cau + so_de + so_phong) DESC, u.ho_ten
        LIMIT 8
    """)
    return [{
        'ho_ten':  r['ho_ten'],
        'so_cau':  int(r['so_cau']),
        'so_de':   int(r['so_de']),
        'so_phong': int(r['so_phong']),
    } for r in cursor.fetchall()]


def _top_subjects(cursor, flt):
    """Top học phần có nhiều câu hỏi ngân hàng nhất (kèm phân bố độ khó)."""
    cursor.execute("""
        SELECT h.ten_hoc_phan, h.ma_hoc_phan,
               COUNT(*) AS tong,
               SUM(n.do_kho='Dễ')        AS de,
               SUM(n.do_kho='Trung bình') AS tb,
               SUM(n.do_kho='Khó')       AS kho
        FROM ngan_hang_cau_hoi n
        JOIN hoc_phan h ON n.hoc_phan_id=h.id
        GROUP BY h.id, h.ten_hoc_phan, h.ma_hoc_phan
        ORDER BY tong DESC
        LIMIT 8
    """)
    return [{
        'ten':    r['ten_hoc_phan'],
        'ma':     r['ma_hoc_phan'],
        'tong':   int(r['tong'] or 0),
        'de':     int(r['de'] or 0),
        'tb':     int(r['tb'] or 0),
        'kho':    int(r['kho'] or 0),
    } for r in cursor.fetchall()]


def _active_rooms(cursor, flt):
    """Phòng thi đang mở hoặc sắp diễn ra (chưa đóng), kèm số thí sinh."""
    status = flt['room_status']
    sql = """
        SELECT p.id, p.ten_phong, p.ma_phong, p.trang_thai,
               p.thoi_gian_bat_dau, u.ho_ten AS gv,
               (SELECT COUNT(*) FROM thi_sinh ts WHERE ts.phong_thi_id=p.id) AS so_ts
        FROM phong_thi p
        LEFT JOIN nguoi_dung u ON p.user_id=u.id
        WHERE 1=1
    """
    p = []
    if status:
        sql += " AND p.trang_thai=%s"; p.append(status)
    else:
        sql += " AND p.trang_thai IN ('chuan_bi','dang_thi')"
    if flt['teacher']:
        sql += " AND p.user_id=%s"; p.append(flt['teacher'])
    sql += " ORDER BY (p.thoi_gian_bat_dau IS NULL), p.thoi_gian_bat_dau ASC LIMIT 12"
    cursor.execute(sql, p)
    return [{
        'ten_phong': r['ten_phong'],
        'ma_phong':  r['ma_phong'],
        'gv':        r['gv'] or '—',
        'bat_dau':   _dt(r['thoi_gian_bat_dau']) or 'Chưa đặt',
        'trang_thai': r['trang_thai'],
        'so_ts':     int(r['so_ts']),
    } for r in cursor.fetchall()]


def _exams_by_subject(cursor, flt):
    """Số ĐỀ THI theo từng học phần (top). Bổ trợ cho 'đề thi theo tháng':
    cho biết học phần nào được tạo nhiều đề thi nhất. Dữ liệu thật từ de_thi."""
    sql = ("SELECT h.ma_hoc_phan, h.ten_hoc_phan, COUNT(*) c "
           "FROM de_thi d JOIN hoc_phan h ON d.hoc_phan_id=h.id WHERE 1=1")
    p = []
    if flt['subject']:
        sql += " AND d.hoc_phan_id=%s"; p.append(flt['subject'])
    if flt['teacher']:
        sql += " AND d.user_id=%s"; p.append(flt['teacher'])
    sql += " GROUP BY h.id, h.ma_hoc_phan, h.ten_hoc_phan ORDER BY c DESC LIMIT 8"
    cursor.execute(sql, p)
    rows = cursor.fetchall()
    return {
        'labels': [r['ma_hoc_phan'] for r in rows],
        'names':  [r['ten_hoc_phan'] for r in rows],
        'data':   [int(r['c']) for r in rows],
    }


# ─────────────────────────────────────────────────────────────
# 2) BIỂU ĐỒ
# ─────────────────────────────────────────────────────────────
def get_charts(cursor, flt):
    """Dữ liệu cho 8 biểu đồ của dashboard, trả về dưới dạng labels + data.

    Các biểu đồ theo thời gian đều tự "vá" những tháng/ngày không có dữ liệu
    thành 0. SQL chỉ trả về các mốc CÓ bản ghi, vẽ thẳng lên thì đường biểu đồ
    nhảy cóc qua tháng rỗng và trông như thể tháng đó không tồn tại.
    """
    subject = flt['subject']
    teacher = flt['teacher']

    # ── (1) Tăng trưởng người dùng theo tháng (12 tháng gần nhất) ──
    cursor.execute("""
        SELECT DATE_FORMAT(created_at,'%m/%Y') AS nhan,
               DATE_FORMAT(created_at,'%Y-%m') AS sk,
               COUNT(*) c
        FROM nguoi_dung
        WHERE role IN ('giao_vien','hoc_sinh')
          AND created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
        GROUP BY nhan, sk ORDER BY sk
    """)
    rows = cursor.fetchall()
    user_growth = {'labels': [r['nhan'] for r in rows],
                   'data':   [int(r['c']) for r in rows]}

    # ── (2) Lượt gọi AI theo ngày (30 ngày gần nhất) ──
    sql = """
        SELECT DATE_FORMAT(ngay,'%d/%m') AS nhan, ngay, SUM(so_lan) c
        FROM api_usage
        WHERE ngay >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
    """
    p = []
    if teacher:
        sql += " AND user_id=%s"; p.append(teacher)
    sql += " GROUP BY ngay ORDER BY ngay"
    cursor.execute(sql, p)
    rows = cursor.fetchall()
    ai_usage = {'labels': [r['nhan'] for r in rows],
                'data':   [int(r['c']) for r in rows]}

    # ── (3) Phân bố câu hỏi theo độ khó (ngân hàng) ──
    sql = "SELECT do_kho, COUNT(*) c FROM ngan_hang_cau_hoi WHERE 1=1"
    p = []
    if subject:
        sql += " AND hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND nguoi_tao_id=%s"; p.append(teacher)
    sql += " GROUP BY do_kho"
    cursor.execute(sql, p)
    dk = {(r['do_kho'] or '').strip(): int(r['c']) for r in cursor.fetchall()}
    difficulty = {'labels': ['Dễ', 'Trung bình', 'Khó'],
                  'data':   [dk.get('Dễ', 0), dk.get('Trung bình', 0), dk.get('Khó', 0)]}

    # ── (4) Câu hỏi ngân hàng theo chương ──
    # Sắp xếp bằng (ch+0): ép chương về số để 2 đứng trước 10. Xếp theo chuỗi
    # thì thứ tự thành 1, 10, 2. Nhóm "(Chưa rõ)" luôn bị đẩy xuống cuối.
    sql = """SELECT COALESCE(NULLIF(TRIM(chuong),''),'(Chưa rõ)') ch, COUNT(*) c
             FROM ngan_hang_cau_hoi WHERE 1=1"""
    p = []
    if subject:
        sql += " AND hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND nguoi_tao_id=%s"; p.append(teacher)
    sql += " GROUP BY ch ORDER BY (ch='(Chưa rõ)') ASC, (ch+0) ASC, ch ASC"
    cursor.execute(sql, p)
    rows = cursor.fetchall()
    q_chapter = {
        'labels': [(r['ch'] if r['ch'] == '(Chưa rõ)' else f"Chương {r['ch']}") for r in rows],
        'data':   [int(r['c']) for r in rows],
    }

    # ── (5) Đề thi tạo theo tháng (6 tháng gần nhất; tháng không có đề = 0) ──
    _today = date.today()
    _months = []
    _y, _m = _today.year, _today.month
    for _i in range(5, -1, -1):
        mm, yy = _m - _i, _y
        while mm <= 0:
            mm += 12; yy -= 1
        _months.append((yy, mm))
    em_labels = [f"{mm:02d}/{yy}" for (yy, mm) in _months]
    em_keys   = [f"{yy}-{mm:02d}" for (yy, mm) in _months]
    sql = ("SELECT DATE_FORMAT(ngay_tao,'%Y-%m') sk, COUNT(*) c FROM de_thi "
           "WHERE ngay_tao >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)")
    p = []
    if subject:
        sql += " AND hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND user_id=%s"; p.append(teacher)
    sql += " GROUP BY sk"
    cursor.execute(sql, p)
    _em = {r['sk']: int(r['c']) for r in cursor.fetchall()}
    exams_by_month = {'labels': em_labels, 'data': [_em.get(k, 0) for k in em_keys]}

    # ── (5b) Số đề thi theo học phần ──
    exams_by_subject = _exams_by_subject(cursor, flt)

    # ── (6) Trạng thái phòng thi ──
    sql = "SELECT trang_thai, COUNT(*) c FROM phong_thi WHERE 1=1"
    p = []
    if teacher:
        sql += " AND user_id=%s"; p.append(teacher)
    sql += " GROUP BY trang_thai"
    cursor.execute(sql, p)
    rs = {r['trang_thai']: int(r['c']) for r in cursor.fetchall()}
    room_status = {
        'labels': ['Đang mở', 'Sắp diễn ra', 'Đã đóng'],
        'data':   [rs.get('dang_thi', 0), rs.get('chuan_bi', 0), rs.get('da_dong', 0)],
    }

    # ── (7) Phân bố điểm bài làm (histogram 0-2,2-4,4-6,6-8,8-10) ──
    # Chỉ tính bài ĐÃ NỘP. Bài đang làm dở có điểm 0 hoặc NULL, gom vào sẽ dồn
    # một cục giả tạo vào khoảng 0–2 và làm phổ điểm sai lệch hoàn toàn.
    sql = """
        SELECT
          SUM(ts.diem >= 0 AND ts.diem < 2)  b0,
          SUM(ts.diem >= 2 AND ts.diem < 4)  b1,
          SUM(ts.diem >= 4 AND ts.diem < 6)  b2,
          SUM(ts.diem >= 6 AND ts.diem < 8)  b3,
          SUM(ts.diem >= 8 AND ts.diem <= 10) b4
        FROM thi_sinh ts JOIN de_thi d ON ts.de_thi_id=d.id
        WHERE ts.da_nop_bai=1
    """
    p = []
    if subject:
        sql += " AND d.hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND d.user_id=%s"; p.append(teacher)
    cursor.execute(sql, p)
    row = cursor.fetchone() or {}
    score_dist = {
        'labels': ['0–2', '2–4', '4–6', '6–8', '8–10'],
        'data':   [int(row.get(f'b{i}') or 0) for i in range(5)],
    }

    # ── (8) Điểm trung bình theo lớp (các bài đã nộp) ──
    sql = """
        SELECT COALESCE(NULLIF(TRIM(ts.lop),''),'(Chưa rõ)') AS lop,
               ROUND(AVG(ts.diem),2) AS avg_diem,
               COUNT(*) AS so_bai
        FROM thi_sinh ts JOIN de_thi d ON ts.de_thi_id=d.id
        WHERE ts.da_nop_bai=1
    """
    p = []
    if subject:
        sql += " AND d.hoc_phan_id=%s"; p.append(subject)
    if teacher:
        sql += " AND d.user_id=%s"; p.append(teacher)
    sql += " GROUP BY lop ORDER BY avg_diem DESC LIMIT 12"
    cursor.execute(sql, p)
    rows = cursor.fetchall()
    score_by_class = {
        'labels': [r['lop'] for r in rows],
        'data':   [_f(r['avg_diem']) for r in rows],
        'counts': [int(r['so_bai']) for r in rows],
    }

    return {
        'userGrowthByMonth':   user_growth,
        'aiUsageByDate':       ai_usage,
        'questionsByDifficulty': difficulty,
        'questionsByChapter':  q_chapter,
        'examsByMonth':        exams_by_month,
        'examsBySubject':      exams_by_subject,
        'examRoomStatus':      room_status,
        'scoreByClass':        score_by_class,
        'scoreDistribution':   score_dist,
    }


# ─────────────────────────────────────────────────────────────
# 3) HOẠT ĐỘNG GẦN ĐÂY
# ─────────────────────────────────────────────────────────────
def get_recent_activities(cursor, flt, limit=12):
    """Dòng thời gian "ai vừa làm gì" trên toàn hệ thống.

    Hệ thống không có bảng nhật ký riêng, nên hoạt động được dựng lại bằng cách
    hỏi 4 bảng nghiệp vụ (đề thi, phòng thi, bài nộp, câu hỏi mới), gán nhãn
    loại cho từng bản ghi, rồi trộn chung và sắp theo thời gian.
    """
    acts = []

    # Đề thi mới tạo
    cursor.execute("""
        SELECT d.ten_de_thi, d.ngay_tao, u.ho_ten
        FROM de_thi d LEFT JOIN nguoi_dung u ON d.user_id=u.id
        ORDER BY d.ngay_tao DESC LIMIT 8
    """)
    for r in cursor.fetchall():
        acts.append({'type': 'exam', 'icon': 'fa-file-lines', 'color': 'blue',
                        'text': (
                            f"{r['ho_ten'] or 'Giảng viên'} tạo đề thi "
                            f"{r['ten_de_thi'] or 'không tên'}"
                        ),
                     'time': r['ngay_tao'], 'time_str': _dt(r['ngay_tao'])})

    # Phòng thi mới tạo
    cursor.execute("""
        SELECT p.ten_phong, p.ngay_tao, u.ho_ten
        FROM phong_thi p LEFT JOIN nguoi_dung u ON p.user_id=u.id
        ORDER BY p.ngay_tao DESC LIMIT 8
    """)
    for r in cursor.fetchall():
        acts.append({'type': 'room', 'icon': 'fa-door-open', 'color': 'pink',
                     'text': f"<b>{r['ho_ten'] or 'Giảng viên'}</b> tạo phòng thi "
                             f"<b>{r['ten_phong']}</b>",
                     'time': r['ngay_tao'], 'time_str': _dt(r['ngay_tao'])})

    # Bài làm mới nộp
    cursor.execute("""
        SELECT ho_ten, thoi_gian_nop, diem, tong_so_cau, so_cau_dung
        FROM thi_sinh
        WHERE da_nop_bai=1 AND thoi_gian_nop IS NOT NULL
        ORDER BY thoi_gian_nop DESC LIMIT 8
    """)
    for r in cursor.fetchall():
        acts.append({'type': 'submit', 'icon': 'fa-pen-to-square', 'color': 'green',
                     'text': f"<b>{r['ho_ten']}</b> nộp bài "
                             f"(đúng {int(r['so_cau_dung'] or 0)}/{int(r['tong_so_cau'] or 0)}, "
                             f"điểm {_f(r['diem']):.1f})",
                     'time': r['thoi_gian_nop'], 'time_str': _dt(r['thoi_gian_nop'])})

    # Câu hỏi ngân hàng mới thêm
    cursor.execute("""
        SELECT n.ngay_them, u.ho_ten, h.ma_hoc_phan
        FROM ngan_hang_cau_hoi n
        LEFT JOIN nguoi_dung u ON n.nguoi_tao_id=u.id
        LEFT JOIN hoc_phan h ON n.hoc_phan_id=h.id
        ORDER BY n.ngay_them DESC LIMIT 8
    """)
    for r in cursor.fetchall():
        acts.append({'type': 'question', 'icon': 'fa-database', 'color': 'purple',
                     'text': f"<b>{r['ho_ten'] or 'Giảng viên'}</b> thêm câu hỏi vào "
                             f"ngân hàng <b>{r['ma_hoc_phan'] or ''}</b>",
                     'time': r['ngay_them'], 'time_str': _dt(r['ngay_them'])})

    # Trộn 4 nguồn lại và sắp theo thời gian. Phải loại bản ghi có mốc None
    # trước khi sort, nếu không so None với datetime sẽ ném TypeError.
    acts = [a for a in acts if a['time'] is not None]
    acts.sort(key=lambda a: a['time'], reverse=True)
    # Bỏ 'time' (kiểu datetime) sau khi sắp xong; chỉ giữ 'time_str' đã định
    # dạng, vì datetime không đưa thẳng qua jsonify được.
    for a in acts:
        a.pop('time', None)
    return acts[:limit]


def get_recent_ai(cursor, flt, limit=10):
    """Lịch sử dùng AI gần đây (từ bảng api_usage — theo ngày/người dùng)."""
    cursor.execute("""
        SELECT a.ngay, a.so_lan, u.ho_ten, u.role
        FROM api_usage a LEFT JOIN nguoi_dung u ON a.user_id=u.id
        ORDER BY a.ngay DESC, a.so_lan DESC LIMIT %s
    """, [limit])
    return [{
        'nguoi_dung': r['ho_ten'] or '—',
        'hanh_dong':  'Tạo câu hỏi bằng AI',
        'so_lan':     int(r['so_lan']),
        'trang_thai': 'Thành công',
        'thoi_gian':  _dt(r['ngay']),
    } for r in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────
# 4) CẢNH BÁO CẦN XỬ LÝ
# ─────────────────────────────────────────────────────────────
def get_alerts(cursor, flt):
    """Những việc admin nên để mắt tới, hiện thành thẻ cảnh báo trên dashboard.

    Cảnh báo nào đếm được 0 thì không thêm vào danh sách — bảng cảnh báo trống
    nghĩa là mọi thứ bình thường, dễ đọc hơn là một danh sách toàn số 0.
    """
    alerts = []

    # Đếm câu trùng bằng cách so khớp CHÍNH XÁC nội dung (chỉ chuẩn hóa hoa
    # thường và khoảng trắng). Đây là phép đếm rẻ, chạy được ở mỗi lần mở
    # dashboard; việc so trùng theo nghĩa nằm ở kiem_duyet.py và nặng hơn nhiều.
    # SUM(c-1) vì trong một nhóm n câu giống nhau thì chỉ n-1 câu là thừa.
    dup = _scalar(cursor, """
        SELECT COALESCE(SUM(c-1),0) c FROM (
            SELECT COUNT(*) c FROM ngan_hang_cau_hoi
            GROUP BY hoc_phan_id, TRIM(LOWER(noi_dung))
            HAVING COUNT(*) > 1
        ) t
    """)
    if dup:
        alerts.append({'level': 'warning', 'icon': 'fa-clone',
                       'title': 'Câu hỏi trùng nội dung',
                       'desc': f'Phát hiện {dup} câu hỏi có nội dung trùng lặp trong ngân hàng.',
                       'count': int(dup)})

    # Phòng thi đang mở
    opening = _scalar(cursor, "SELECT COUNT(*) c FROM phong_thi WHERE trang_thai='dang_thi'")
    if opening:
        alerts.append({'level': 'info', 'icon': 'fa-door-open',
                       'title': 'Phòng thi đang mở',
                       'desc': f'{opening} phòng thi đang diễn ra.',
                       'count': int(opening)})

    # Phòng thi sắp diễn ra (chuẩn bị + có mốc bắt đầu trong tương lai hoặc chưa mở)
    upcoming = _scalar(cursor, """
        SELECT COUNT(*) c FROM phong_thi
        WHERE trang_thai='chuan_bi'
          AND (thoi_gian_bat_dau IS NULL OR thoi_gian_bat_dau >= NOW())
    """)
    if upcoming:
        alerts.append({'level': 'info', 'icon': 'fa-clock',
                       'title': 'Phòng thi sắp diễn ra',
                       'desc': f'{upcoming} phòng thi đang ở trạng thái chuẩn bị.',
                       'count': int(upcoming)})

    # Tài khoản bị khóa
    locked = _scalar(cursor, "SELECT COUNT(*) c FROM nguoi_dung WHERE trang_thai='locked'")
    if locked:
        alerts.append({'level': 'danger', 'icon': 'fa-user-lock',
                       'title': 'Tài khoản bị khóa',
                       'desc': f'{locked} tài khoản đang bị khóa.',
                       'count': int(locked)})

    return alerts
