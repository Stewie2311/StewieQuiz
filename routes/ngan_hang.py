"""
NGÂN HÀNG CÂU HỎI dùng chung của cả trường.

Hai nguồn câu hỏi song song, và đừng lẫn hai cái này:
  - `ngan_hang_cau_hoi` : kho CHUNG, ai cũng tra cứu và lấy về ra đề được.
  - `cau_hoi`           : câu hỏi thuộc riêng một đề thi của một giáo viên.
Giao diện gọi chúng là nguồn "ngân hàng" và "cá nhân"; tham số `nguon_filter`
quyết định lấy bên nào.

Việc chính của file:
  1. Phát hiện câu hỏi TRÙNG LẶP trước khi cho vào kho (phần ngay bên dưới).
  2. Trang tra cứu + API JSON để lọc theo môn/chương/độ khó.
  3. Thêm câu (lẻ hoặc cả đề) vào kho, và xóa câu khỏi kho.

Mọi câu muốn vào kho đều phải qua hai cửa: `kiem_duyet` (đủ trường, không phải
câu hỏi yếu) rồi mới tới `kiem_tra_trung_lap`.
"""
import difflib
import unicodedata

from flask import (Blueprint, render_template, request, # type: ignore
                   session, jsonify, current_app)
from database import get_db_connection
from decorators import login_required
from routes import kiem_duyet

ngan_hang_bp = Blueprint('ngan_hang', __name__)


# ==========================================
# PHÁT HIỆN CÂU HỎI TRÙNG LẶP
#
# Chạy được ở hai chế độ, tự chọn tùy máy chủ có gì:
#
#   Tốt nhất — sentence-transformers: so theo NGHĨA, nên bắt được cả câu diễn
#   đạt khác chữ nhưng cùng ý. Bù lại nó kéo theo PyTorch (2-3GB) và phải tải
#   model về từ HuggingFace.
#
#   Dự phòng — so theo tập từ: nhẹ, không cần cài gì thêm, dĩ nhiên kém tinh
#   hơn nhưng chức năng vẫn chạy.
#
# Model được import LƯỜI (bên trong hàm, không phải đầu file). Nếu import ở đầu
# file thì torch bị nạp ngay lúc khởi động ứng dụng, VPS nhỏ hết sạch RAM dù
# người dùng chưa hề đụng tới chức năng này.
# ==========================================
_model      = None
_model_loi  = False     # đã thử nạp và hỏng -> đừng thử lại ở mỗi câu hỏi


def _get_model():
    """Nạp model đúng một lần. None = máy chủ không có sentence-transformers."""
    global _model, _model_loi
    if _model is None and not _model_loi:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        except Exception:
            _model_loi = True
            current_app.logger.warning(
                'Không nạp được sentence-transformers -> dùng so khớp chuỗi '
                '(difflib) để phát hiện câu hỏi trùng lặp.'
            )
    return _model


def _chuan_hoa(s):
    """Bỏ dấu, hạ chữ thường, gộp khoảng trắng cho dễ so khớp."""
    s = unicodedata.normalize('NFD', (s or '').lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return ' '.join(s.split())


def kiem_tra_trung_lap(noi_dung_moi, mon_hoc_id, nguong=0.82):
    """Tìm các câu trong kho giống câu mới quá `nguong` (0..1).

    Chỉ đối chiếu trong phạm vi CÙNG HỌC PHẦN — hai môn khác nhau có câu giống
    nhau là chuyện bình thường và không nên chặn.

    Trả về (có_trùng, danh_sách_câu_trùng) đã sắp theo độ giống giảm dần.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, noi_dung FROM ngan_hang_cau_hoi WHERE hoc_phan_id=%s",
        (mon_hoc_id,)
    )
    existing = cursor.fetchall()
    cursor.close()
    conn.close()

    if not existing:
        return False, []

    model = _get_model()
    if model is not None:
        texts = [noi_dung_moi] + [c['noi_dung'] for c in existing]
        embeddings = model.encode(texts, convert_to_numpy=True,
                                  normalize_embeddings=True)
        vec_moi      = embeddings[0]
        vec_existing = embeddings[1:]
        # Vector đã chuẩn hóa nên tích vô hướng chính là cosine similarity.
        sims = list(vec_existing @ vec_moi)
    else:
        # So theo TẬP TỪ, không so từng ký tự. Ví dụ kinh điển cho thấy vì sao:
        # "tầng nào chịu trách nhiệm ĐỊNH TUYẾN" và "tầng nào chịu trách nhiệm
        # MÃ HÓA" trùng nhau tới ~90% ký tự, so theo ký tự là xóa oan mất một
        # câu hỏi hoàn toàn khác. Kết quả cũng nằm trong thang 0..1 nên dùng
        # chung ngưỡng với cosine ở nhánh trên.
        sims = [kiem_duyet.do_tuong_dong(noi_dung_moi, c['noi_dung'])
                for c in existing]

    trung_lap = []
    for i, cau in enumerate(existing):
        sim = float(sims[i])
        if sim >= nguong:
            trung_lap.append({
                'id'           : cau['id'],
                'noi_dung'     : cau['noi_dung'],
                'do_tuong_dong': round(sim * 100, 1)
            })
    trung_lap.sort(key=lambda x: x['do_tuong_dong'], reverse=True)
    return len(trung_lap) > 0, trung_lap


# ==========================================
# TRUY VẤN DỮ LIỆU CHO TRANG NGÂN HÀNG
#
# Trang tra cứu và API JSON dùng chung đúng một hàm `_lay_du_lieu` bên dưới, để
# hai đường không bao giờ hiển thị số liệu lệch nhau.
# ==========================================
def _lay_du_lieu(nguon_filter, mon_filter,
                 do_kho_filter, chuong_filter, search,
                 page=0, per_page=0):
    """Lấy danh sách môn, câu hỏi, số liệu thống kê và danh sách chương.

    Giao diện đi theo 2 cấp: chọn môn trước, rồi mới xem câu hỏi trong môn đó.
    Nên khi `mon_filter` còn trống, hàm CỐ TÌNH không truy vấn câu hỏi — kho có
    hàng chục nghìn câu, đổ hết ra là treo trang.

    Trả về: (mon_hocs, cau_hois, stats, chuong_list)
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Danh mục học phần của trường có hơn 300 môn, phần lớn chưa có câu hỏi nào.
    # HAVING lọc bỏ các môn rỗng để danh sách chỉ còn môn thật sự dùng được.
    cursor.execute("""
        SELECT m.id, m.ten_hoc_phan AS ten_mon,
               COUNT(DISTINCT n.id) as so_cau_ngan_hang,
               COUNT(DISTINCT c.id) as so_cau_ca_nhan
        FROM hoc_phan m
        LEFT JOIN ngan_hang_cau_hoi n ON n.hoc_phan_id = m.id
        LEFT JOIN de_thi d ON d.hoc_phan_id = m.id
        LEFT JOIN cau_hoi c ON c.de_thi_id  = d.id
        GROUP BY m.id
        HAVING so_cau_ngan_hang > 0 OR so_cau_ca_nhan > 0
        ORDER BY m.ten_hoc_phan
    """)
    mon_hocs_all = cursor.fetchall()

    # Phân trang môn. Trang HTML truyền page/per_page, còn API JSON gọi với 0
    # để lấy trọn danh sách (modal chọn môn cần đủ, không phân trang).
    if page > 0 and per_page > 0:
        total_mon = len(mon_hocs_all)
        total_pages = max(1, -(-total_mon // per_page))  # -(-a//b) = làm tròn lên
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * per_page
        mon_hocs = mon_hocs_all[start_idx:start_idx + per_page]
    else:
        mon_hocs = mon_hocs_all

    # Số liệu cho từng thẻ môn: phân bố độ khó, số chương, số người đóng góp.
    # Gom một truy vấn GROUP BY rồi tra theo dict, thay vì bắn một câu đếm cho
    # mỗi môn (sẽ thành hàng trăm truy vấn cho một lần mở trang).
    cursor.execute("""
        SELECT hoc_phan_id,
               SUM(CASE WHEN do_kho='Dễ' THEN 1 ELSE 0 END)         AS nh_de,
               SUM(CASE WHEN do_kho='Trung bình' THEN 1 ELSE 0 END) AS nh_tb,
               SUM(CASE WHEN do_kho='Khó' THEN 1 ELSE 0 END)        AS nh_kho,
               COUNT(DISTINCT NULLIF(chuong,'')) AS nh_chuong,
               COUNT(DISTINCT nguoi_tao_id)      AS nh_nguoi
        FROM ngan_hang_cau_hoi
        GROUP BY hoc_phan_id
    """)
    nh_stat = {r['hoc_phan_id']: r for r in cursor.fetchall()}
    for m in mon_hocs:
        s = nh_stat.get(m['id'])
        m['nh_de']     = int(s['nh_de'])     if s else 0
        m['nh_tb']     = int(s['nh_tb'])     if s else 0
        m['nh_kho']    = int(s['nh_kho'])    if s else 0
        m['nh_chuong'] = int(s['nh_chuong']) if s else 0
        m['nh_nguoi']  = int(s['nh_nguoi'])  if s else 0

    cau_hois_nh = []
    cau_hois_ch = []

    # Chưa chọn môn thì dừng ở đây, không đụng tới bảng câu hỏi (xem docstring).
    if mon_filter:
        # --- Nguồn 1: kho chung ---
        if nguon_filter in ('tat_ca', 'ngan_hang'):
            q = """
                SELECT
                    n.id, n.noi_dung, n.cau_a, n.cau_b,
                    n.cau_c, n.cau_d, n.dap_an_dung,
                    n.do_kho, n.chuong,
                    DATE_FORMAT(n.ngay_them,'%%d/%%m/%%Y %%H:%%i')
                        AS ngay_them,
                    n.nguoi_tao_id,
                    m.ten_hoc_phan AS ten_mon,
                    u.ho_ten AS ten_gv,
                    'ngan_hang' AS nguon,
                    NULL AS ten_de_thi
                FROM ngan_hang_cau_hoi n
                JOIN hoc_phan m ON n.hoc_phan_id = m.id
                LEFT JOIN nguoi_dung u ON n.nguoi_tao_id = u.id
                WHERE n.hoc_phan_id = %s
            """
            p = [mon_filter]
            if do_kho_filter:
                q += " AND n.do_kho = %s"
                p.append(do_kho_filter)
            if chuong_filter:
                q += " AND n.chuong = %s"
                p.append(chuong_filter)
            if search:
                q += " AND n.noi_dung LIKE %s"
                p.append(f'%{search}%')
            q += " ORDER BY n.ngay_them DESC"
            cursor.execute(q, p)
            cau_hois_nh = cursor.fetchall()

        # --- Nguồn 2: câu hỏi nằm trong đề thi riêng của giáo viên ---
        # Cột chọn ra cố tình đặt trùng tên với nhánh trên ('nguon', 'ngay_them'...)
        # để hai danh sách nối được vào nhau và template chỉ cần một vòng lặp.
        if nguon_filter in ('tat_ca', 'ca_nhan'):
            q = """
                SELECT
                    c.id, c.noi_dung, c.cau_a, c.cau_b,
                    c.cau_c, c.cau_d, c.dap_an_dung,
                    c.do_kho, c.chuong,
                    DATE_FORMAT(d.ngay_tao,'%%d/%%m/%%Y %%H:%%i')
                        AS ngay_them,
                    d.user_id AS nguoi_tao_id,
                    m.ten_hoc_phan AS ten_mon,
                    u.ho_ten AS ten_gv,
                    'ca_nhan' AS nguon,
                    d.ten_de_thi
                FROM cau_hoi c
                JOIN de_thi d  ON c.de_thi_id  = d.id
                JOIN hoc_phan m ON d.hoc_phan_id = m.id
                LEFT JOIN nguoi_dung u ON d.user_id  = u.id
                WHERE d.hoc_phan_id = %s
            """
            p = [mon_filter]
            if do_kho_filter:
                q += " AND c.do_kho = %s"
                p.append(do_kho_filter)
            if chuong_filter:
                q += " AND c.chuong = %s"
                p.append(chuong_filter)
            if search:
                q += " AND c.noi_dung LIKE %s"
                p.append(f'%{search}%')
            q += " ORDER BY d.ngay_tao DESC"
            cursor.execute(q, p)
            cau_hois_ch = cursor.fetchall()

    # Danh sách chương cho ô lọc: gộp chương từ CẢ HAI nguồn bằng UNION.
    chuong_list = []
    if mon_filter:
        cursor.execute("""
            SELECT DISTINCT chuong FROM ngan_hang_cau_hoi
            WHERE hoc_phan_id = %s AND chuong IS NOT NULL AND chuong != ''
            UNION
            SELECT DISTINCT c.chuong FROM cau_hoi c
            JOIN de_thi d ON c.de_thi_id = d.id
            WHERE d.hoc_phan_id = %s AND c.chuong IS NOT NULL AND c.chuong != ''
        """, (mon_filter, mon_filter))
        raw = [row['chuong'] for row in cursor.fetchall()]

        def _ch_sort(v):
            """Chương số xếp theo giá trị số và đứng trước; chương chữ xếp sau.

            Sắp bằng chuỗi thuần sẽ cho ra 1, 10, 11, 2 — nhìn rất kỳ.
            """
            try:
                return (0, int(v))
            except (ValueError, TypeError):
                return (1, str(v))

        chuong_list = sorted(set(raw), key=_ch_sort)

    # Số liệu cho thanh thống kê ở đầu trang — luôn tính, kể cả khi chưa chọn môn.
    cursor.execute("""
        SELECT
            COUNT(*) AS tong,
            COUNT(DISTINCT hoc_phan_id) AS so_mon,
            SUM(CASE WHEN do_kho='Dễ' THEN 1 ELSE 0 END) AS so_de,
            SUM(CASE WHEN do_kho='Trung bình' THEN 1 ELSE 0 END) AS so_tb,
            SUM(CASE WHEN do_kho='Khó' THEN 1 ELSE 0 END) AS so_kho
        FROM ngan_hang_cau_hoi
    """)
    snh = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) AS tong FROM cau_hoi")
    sch = cursor.fetchone()

    cursor.close()
    conn.close()

    stats = {
        'tong'     : snh['tong'] or 0,
        'so_mon'   : snh['so_mon'] or 0,
        'so_de'    : snh['so_de']  or 0,
        'so_tb'    : snh['so_tb']  or 0,
        'so_kho'   : snh['so_kho'] or 0,
        'ngan_hang': snh['tong']   or 0,
        'ca_nhan'  : sch['tong']   or 0,
    }
    cau_hois = cau_hois_nh + cau_hois_ch
    return mon_hocs, cau_hois, stats, chuong_list

# ==========================================
# TRANG TRA CỨU + API JSON + XUẤT EXCEL
# ==========================================
@ngan_hang_bp.route('/ngan_hang')
@login_required
def ngan_hang():
    """Trang /ngan_hang.

    Có tham số ?partial=1 thì chỉ trả về phần ruột (bộ lọc + danh sách), không
    kèm layout. Tab "Thư viện" dùng đường này để nạp bằng AJAX, người dùng
    chuyển môn mà trang không nhấp nháy tải lại.
    """
    nguon_filter  = request.args.get('nguon',      'ngan_hang')
    mon_filter    = request.args.get('mon_hoc_id', '')
    do_kho_filter = request.args.get('do_kho',     '')
    chuong_filter = request.args.get('chuong',     '')
    search        = request.args.get('search',     '')

    page = request.args.get('page', 1, type=int)
    mon_hocs, cau_hois, stats, chuong_list = _lay_du_lieu(
        nguon_filter, mon_filter,
        do_kho_filter, chuong_filter, search,
        page=page, per_page=20
    )
    # Đếm lại tổng số môn để biết có bao nhiêu trang. `_lay_du_lieu` chỉ trả về
    # các môn của TRANG HIỆN TẠI nên không thể lấy số này từ đó.
    conn2 = get_db_connection()
    cursor2 = conn2.cursor()
    cursor2.execute("""
        SELECT COUNT(*) FROM (
            SELECT m.id
            FROM hoc_phan m
            LEFT JOIN ngan_hang_cau_hoi n ON n.hoc_phan_id = m.id
            LEFT JOIN de_thi d ON d.hoc_phan_id = m.id
            LEFT JOIN cau_hoi c ON c.de_thi_id = d.id
            GROUP BY m.id
            HAVING COUNT(DISTINCT n.id) > 0 OR COUNT(DISTINCT c.id) > 0
        ) t
    """)
    total_mon_real = cursor2.fetchone()[0]
    cursor2.close()
    conn2.close()
    total_pages = max(1, -(-total_mon_real // 20))

    template = 'ngan_hang_inner.html' if request.args.get('partial') else 'ngan_hang.html'
    return render_template(template,
        cau_hois=cau_hois, mon_hocs=mon_hocs, stats=stats,
        mon_filter=mon_filter, do_kho_filter=do_kho_filter,
        chuong_filter=chuong_filter, search=search,
        nguon_filter=nguon_filter,
        chuong_list=chuong_list,
        page=page, total_pages=total_pages
    )


@ngan_hang_bp.route('/ngan_hang/excel')
@login_required
def ngan_hang_excel():
    """Tải kho câu hỏi ra Excel. Có ?mon_hoc_id thì chỉ xuất môn đó."""
    from routes.excel_utils import xuat_xlsx
    mon_id = request.args.get('mon_hoc_id', '')
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    q = """
        SELECT m.ten_hoc_phan AS ten_mon, n.chuong, n.do_kho, n.bloom, n.noi_dung,
               n.cau_a, n.cau_b, n.cau_c, n.cau_d, n.dap_an_dung,
               u.ho_ten AS ten_gv
        FROM ngan_hang_cau_hoi n
        JOIN hoc_phan m ON n.hoc_phan_id = m.id
        LEFT JOIN nguoi_dung u ON n.nguoi_tao_id = u.id
    """
    p = []
    if mon_id.isdigit():
        q += " WHERE n.hoc_phan_id = %s"
        p.append(mon_id)
    q += " ORDER BY m.ten_hoc_phan, n.chuong, n.id"
    cursor.execute(q, p)
    data = cursor.fetchall()
    cursor.close(); conn.close()
    rows = [[i, r['ten_mon'], r['chuong'] or '', r['do_kho'] or '', r['bloom'] or '',
             r['noi_dung'], r['cau_a'], r['cau_b'], r['cau_c'], r['cau_d'],
             (r['dap_an_dung'] or '').upper(), r['ten_gv'] or '']
            for i, r in enumerate(data, 1)]
    return xuat_xlsx(
        'Ngan hang cau hoi', 'NGÂN HÀNG CÂU HỎI',
        [f'Tổng số câu: {len(rows)}'],
        ['STT', 'Môn', 'Chương', 'Độ khó', 'Bloom', 'Nội dung',
         'A', 'B', 'C', 'D', 'Đáp án', 'Người đóng góp'],
        rows, [6, 22, 9, 12, 12, 55, 22, 22, 22, 22, 8, 22],
        'ngan_hang_cau_hoi.xlsx')


@ngan_hang_bp.route('/api/ngan_hang')
@login_required
def api_ngan_hang():
    """Phiên bản JSON của trang trên, cho modal "chọn câu từ ngân hàng" khi ra đề.

    Phải làm sạch dữ liệu trước khi jsonify: MySQL trả về kiểu Decimal và
    datetime, hai kiểu này json không tuần tự hóa được và sẽ ném TypeError.
    """
    import decimal, datetime

    nguon_filter  = request.args.get('nguon',      'ngan_hang')
    mon_filter    = request.args.get('mon_hoc_id', '')
    do_kho_filter = request.args.get('do_kho',     '')
    chuong_filter = request.args.get('chuong',     '')
    search        = request.args.get('search',     '')

    try:
        mon_hocs, cau_hois, stats, _chuong_list = _lay_du_lieu(
            nguon_filter, mon_filter,
            do_kho_filter, chuong_filter, search
        )

        def safe_val(v):
            """Đưa kiểu của MySQL về kiểu mà json hiểu được."""
            if isinstance(v, decimal.Decimal):
                return float(v)
            if isinstance(v, datetime.datetime):
                return v.strftime('%d/%m/%Y %H:%M')
            if isinstance(v, datetime.date):
                return v.strftime('%d/%m/%Y')
            return v

        cau_hois_clean = []
        for c in cau_hois:
            row = {}
            for k, v in c.items():
                row[k] = safe_val(v)
            cau_hois_clean.append(row)

        mon_hocs_clean = []
        for m in mon_hocs:
            mon_hocs_clean.append({
                'id'              : int(m['id']),
                'ten_mon'         : m['ten_mon'] or '',
                'so_cau_ngan_hang': int(m['so_cau_ngan_hang'] or 0),
                'so_cau_ca_nhan'  : int(m['so_cau_ca_nhan'] or 0),
                'nh_de'           : int(m.get('nh_de') or 0),
                'nh_tb'           : int(m.get('nh_tb') or 0),
                'nh_kho'          : int(m.get('nh_kho') or 0),
                'nh_chuong'       : int(m.get('nh_chuong') or 0),
                'nh_nguoi'        : int(m.get('nh_nguoi') or 0),
            })

        stats_clean = {}
        for k, v in stats.items():
            if isinstance(v, decimal.Decimal):
                stats_clean[k] = float(v)
            else:
                stats_clean[k] = int(v) if v is not None else 0

        return jsonify({
            'success'  : True,
            'cau_hois' : cau_hois_clean,
            'mon_hocs' : mon_hocs_clean,
            'stats'    : stats_clean
        })

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print("LỖI API NGAN HANG:", tb)
        return jsonify({
            'success': False,
            'error'  : str(e),
            'detail' : tb
        }), 500


# ==========================================
# GHI VÀO KHO: thêm 1 câu, thêm cả đề, xóa câu
#
# Ba cửa kiểm tra mà mọi câu phải qua, theo đúng thứ tự này:
#   1. du_dieu_kien_luu  — đủ nội dung, 4 đáp án, độ khó, Bloom, giải thích?
#   2. la_cau_hoi_yeu    — có phải câu hỏi rỗng nghĩa kiểu "Chương 3 nói về gì"?
#   3. kiem_tra_trung_lap— đã có câu na ná trong kho chưa?
# ==========================================
@ngan_hang_bp.route('/them_vao_ngan_hang', methods=['POST'])
@login_required
def them_vao_ngan_hang():
    """Thêm MỘT câu hỏi vào kho chung."""
    data       = request.get_json()
    noi_dung   = data.get('noi_dung', '').strip()
    cau_a      = data.get('cau_a', '')
    cau_b      = data.get('cau_b', '')
    cau_c      = data.get('cau_c', '')
    cau_d      = data.get('cau_d', '')
    dap_an     = data.get('dap_an_dung', '')
    do_kho     = data.get('do_kho', '')
    bloom      = data.get('bloom', '')
    giai_thich = data.get('giai_thich', '')
    chuong     = data.get('chuong', '')
    mon_hoc_id = data.get('mon_hoc_id')   # thực chất là hoc_phan_id — tên cũ còn sót

    if not noi_dung or not mon_hoc_id:
        return jsonify({
            'success': False,
            'message': 'Thiếu thông tin!'
        }), 400

    # Kiểm tra lại toàn bộ ở server, dù giao diện đã kiểm rồi. Người ta gọi
    # thẳng API này bằng curl được, không thể tin dữ liệu gửi lên.
    cau = {'noi_dung': noi_dung, 'cau_a': cau_a, 'cau_b': cau_b,
           'cau_c': cau_c, 'cau_d': cau_d, 'dap_an_dung': dap_an,
           'do_kho': do_kho, 'bloom': bloom, 'giai_thich': giai_thich}
    ok, loi = kiem_duyet.du_dieu_kien_luu(cau)
    if not ok:
        return jsonify({'success': False,
                        'message': f'Không thể lưu: {loi}'}), 400
    if kiem_duyet.la_cau_hoi_yeu(noi_dung):
        return jsonify({'success': False,
                        'message': 'Câu hỏi yếu (chỉ hỏi tiêu đề/mục lục hoặc quá '
                                   'chung chung) — không được đưa vào ngân hàng.'}), 400

    do_kho = kiem_duyet.chuan_hoa_do_kho(do_kho)
    bloom  = kiem_duyet.chuan_hoa_bloom(bloom)

    is_trung, trung_lap = kiem_tra_trung_lap(noi_dung, mon_hoc_id)
    if is_trung:
        return jsonify({
            'success'  : False,
            'trung_lap': True,
            'message'  : (
                f'Câu hỏi tương tự đã tồn tại '
                f'({trung_lap[0]["do_tuong_dong"]}% giống)'
            ),
            'chi_tiet' : trung_lap[:3]
        })

    conn   = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO ngan_hang_cau_hoi
            (hoc_phan_id, chuong, do_kho, bloom, giai_thich, noi_dung,
             cau_a, cau_b, cau_c, cau_d, dap_an_dung,
             nguoi_tao_id, nguon_tao, trang_thai)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            mon_hoc_id, chuong, do_kho, bloom, giai_thich, noi_dung,
            cau_a, cau_b, cau_c, cau_d, dap_an.strip().upper()[:1],
            session['user_id'], data.get('nguon_tao') or 'AI',
            # Đánh dấu ĐÃ DUYỆT luôn: chính giảng viên vừa đọc câu này và bấm
            # nút thêm, không cần bắt họ tự duyệt lại thứ mình vừa gửi.
            kiem_duyet.TT_DA_DUYET
        ))
        conn.commit()
        return jsonify({
            'success': True,
            'message': 'Đã thêm vào ngân hàng!',
            'id'     : cursor.lastrowid
        })
    except Exception:
        conn.rollback()
        current_app.logger.exception('Lỗi khi thêm câu hỏi vào ngân hàng')
        return jsonify({'success': False, 'message': 'Có lỗi xảy ra, vui lòng thử lại.'}), 500
    finally:
        cursor.close()
        conn.close()


@ngan_hang_bp.route('/them_de_vao_ngan_hang/<int:de_thi_id>',
                    methods=['POST'])
@login_required
def them_de_vao_ngan_hang(de_thi_id):
    """Đẩy cả một đề thi vào kho chung, câu nào không đạt thì bỏ qua câu đó.

    Cố ý KHÔNG hủy toàn bộ khi có câu hỏng: một đề 40 câu mà 2 câu trùng thì
    vẫn nên nhận 38 câu còn lại. Kết quả trả về đếm riêng ba nhóm (thêm được /
    trùng / bị loại) kèm lý do, để giáo viên biết chính xác chuyện gì đã xảy ra.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT d.*, m.id AS mon_hoc_id, m.ten_hoc_phan AS ten_mon
        FROM de_thi d
        JOIN hoc_phan m ON d.hoc_phan_id = m.id
        WHERE d.id = %s
    """, (de_thi_id,))
    de_thi = cursor.fetchone()

    if not de_thi:
        cursor.close(); conn.close()
        return jsonify({
            'success': False,
            'message': 'Không tìm thấy đề thi!'
        }), 404

    cursor.execute(
        "SELECT * FROM cau_hoi WHERE de_thi_id = %s",
        (de_thi_id,)
    )
    cau_hois = cursor.fetchall()

    them_duoc      = 0
    bi_trung       = 0
    bi_loai        = 0
    chi_tiet_trung = []
    chi_tiet_loai  = []

    for cau in cau_hois:
        # Đề cũ (tạo từ trước khi có bước kiểm duyệt) thường thiếu Bloom.
        # `tham_dinh_mot_cau` tự suy ra Bloom giúp, nhưng thiếu giải thích hay
        # là câu hỏi yếu thì không cứu được — loại thẳng.
        q = dict(cau)
        kiem_duyet.tham_dinh_mot_cau(q)
        ok, loi = kiem_duyet.du_dieu_kien_luu(q)
        if not ok or kiem_duyet.la_cau_hoi_yeu(q['noi_dung']):
            bi_loai += 1
            chi_tiet_loai.append({
                'cau_hoi': (q['noi_dung'] or '')[:80] + '...',
                'ly_do'  : loi or 'Câu hỏi yếu (chỉ hỏi tiêu đề/mục lục hoặc quá chung chung).'
            })
            continue

        is_trung, trung_lap = kiem_tra_trung_lap(
            cau['noi_dung'], de_thi['mon_hoc_id']
        )
        if is_trung:
            bi_trung += 1
            chi_tiet_trung.append({
                'cau_hoi'      : cau['noi_dung'][:80] + '...',
                'trung_voi'    : trung_lap[0]['noi_dung'][:80] + '...',
                'do_tuong_dong': trung_lap[0]['do_tuong_dong']
            })
        else:
            cursor.execute("""
                INSERT INTO ngan_hang_cau_hoi
                (hoc_phan_id, chuong, do_kho, bloom, giai_thich, noi_dung,
                 cau_a, cau_b, cau_c, cau_d, dap_an_dung,
                 nguoi_tao_id, nguon_tao, trang_thai)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                de_thi['mon_hoc_id'],
                q['chuong'], q['do_kho'], q['bloom'], q['giai_thich'],
                q['noi_dung'],
                q['cau_a'], q['cau_b'], q['cau_c'], q['cau_d'],
                q['dap_an_dung'],
                session['user_id'],
                cau.get('nguon_tao') or 'AI',
                kiem_duyet.TT_DA_DUYET
            ))
            them_duoc += 1

    conn.commit()
    cursor.close()
    conn.close()

    msg = f'Thêm {them_duoc} câu thành công. {bi_trung} câu trùng lặp bỏ qua.'
    if bi_loai:
        msg += (f' {bi_loai} câu KHÔNG ĐẠT bị loại (thiếu giải thích/độ khó/Bloom, '
                f'sai đáp án, hoặc câu hỏi yếu).')
    return jsonify({
        'success'       : True,
        'them_duoc'     : them_duoc,
        'bi_trung'      : bi_trung,
        'bi_loai'       : bi_loai,
        'chi_tiet_trung': chi_tiet_trung,
        'chi_tiet_loai' : chi_tiet_loai,
        'message'       : msg
    })


@ngan_hang_bp.route('/xoa_khoi_ngan_hang/<int:cau_id>',
                    methods=['POST'])
@login_required
def xoa_khoi_ngan_hang(cau_id):
    """Xóa một câu khỏi kho.

    Kho là của chung nhưng quyền xóa thì không: chỉ người ĐÓNG GÓP câu đó (hoặc
    admin) mới xóa được, tránh giáo viên xóa nhầm/xóa bừa công sức đồng nghiệp.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT nguoi_tao_id FROM ngan_hang_cau_hoi WHERE id=%s",
            (cau_id,)
        )
        cau = cursor.fetchone()
        if not cau:
            return jsonify({
                'success': False,
                'message': 'Không tìm thấy!'
            }), 404
        if (session.get('role') != 'admin' and
                cau['nguoi_tao_id'] != session['user_id']):
            return jsonify({
                'success': False,
                'message': 'Không có quyền!'
            }), 403
        cursor.execute(
            "DELETE FROM ngan_hang_cau_hoi WHERE id=%s", (cau_id,)
        )
        conn.commit()
        return jsonify({
            'success': True,
            'message': 'Đã xóa!'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        cursor.close()
        conn.close()