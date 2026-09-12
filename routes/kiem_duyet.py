"""
KIỂM DUYỆT CÂU HỎI DO AI SINH RA — module dùng CHUNG cho mọi luồng
(tạo câu hỏi, lưu bộ câu hỏi, thêm vào ngân hàng).

Vì sao cần: prompt dù chặt tới đâu cũng KHÔNG đảm bảo được chất lượng — mô hình
ngôn ngữ vẫn thường xuyên mắc các lỗi sau (đã kiểm chứng trên chính ngân hàng
câu hỏi của hệ thống):
  • Bloom bỏ trống (100% câu trong ngân hàng hiện tại đang NULL).
  • Câu hỏi yếu kiểu "Chương 3 của tài liệu có tiêu đề là gì?" — không kiểm tra
    kiến thức, chỉ hỏi mục lục.
  • Câu gần trùng nhau (4 câu hỏi tiêu đề chương giống nhau tới 82%).
  • Đáp án đúng dồn về A (position bias).
  • Phương án nhiễu trùng nhau / rỗng.
Chỉ có kiểm tra Ở SERVER sau khi AI trả về mới chắc chắn.

Nguyên tắc: KHÔNG xóa lặng lẽ. Câu lỗi được ĐÁNH DẤU cảnh báo + trạng thái để
giảng viên tự quyết, và KHÔNG được tự động lưu vào ngân hàng chính thức.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

# ====== TẬP GIÁ TRỊ HỢP LỆ ======
DAP_AN_HOP_LE = ('A', 'B', 'C', 'D')
DO_KHO_HOP_LE = ('Dễ', 'Trung bình', 'Khó')
BLOOM_HOP_LE  = ('Nhớ', 'Hiểu', 'Vận dụng', 'Phân tích')

# ====== TRẠNG THÁI KIỂM DUYỆT ======
TT_CHO_DUYET   = 'cho_duyet'
TT_CAN_SUA     = 'can_sua'
TT_NGHI_TRUNG  = 'nghi_trung'
TT_DA_DUYET    = 'da_duyet'
TT_BI_TU_CHOI  = 'bi_tu_choi'

# ====== MÃ CẢNH BÁO -> nhãn hiển thị ======
CANH_BAO_NHAN = {
    'thieu_du_lieu' : 'Thiếu dữ liệu',
    'thieu_bloom'   : 'Thiếu Bloom',
    'thieu_do_kho'  : 'Thiếu độ khó',
    'thieu_giai_thich': 'Thiếu giải thích',
    'sai_dap_an'    : 'Nghi sai đáp án',
    'nhieu_trung'   : 'Phương án trùng nhau',
    'cau_yeu'       : 'Câu hỏi yếu',
    'nghi_trung'    : 'Nghi trùng lặp',
    'trung_ngan_hang': 'Trùng câu trong ngân hàng',
}

# Ngưỡng nghi trùng (0..1). Dùng chung cho cả so tập từ lẫn embedding.
NGUONG_TRUNG = 0.82

# Ngưỡng trùng Ý: hai câu CÙNG một đáp án đúng mà phần hỏi còn giống nhau tới mức
# này thì thực chất là MỘT kiến thức được hỏi hai lần bằng cách diễn đạt khác —
# dạng trùng mà ngưỡng chữ nghĩa 0.82 ở trên bỏ lọt hoàn toàn ("TCP đảm bảo tin cậy
# bằng cơ chế nào?" vs "Cơ chế nào giúp TCP truyền tin cậy?" chỉ giống 0.33 chữ
# nghĩa nhưng cùng đáp án "Báo nhận và truyền lại").
# Để THẤP (0.30) là cố ý: thà loại oan 1 câu rồi xin AI câu khác thay (vòng bổ sung
# tự bù, không tốn lượt của người dùng) còn hơn để lọt câu hỏi trùng ý vào đề thi.
NGUONG_TRUNG_Y = 0.30

# Hai đáp án đúng giống nhau tới mức này thì coi là CÙNG một kiến thức.
NGUONG_TRUNG_DAP_AN = 0.80

# Hai đáp án đúng giống nhau DƯỚI mức này thì coi là KHÁC HẲN kiến thức — dù phần
# hỏi có gần y hệt cũng KHÔNG được coi là trùng (cổng HTTP 80 vs cổng HTTPS 443).
NGUONG_KHAC_HAN = 0.50


# ==========================================
# CHUẨN HÓA
# ==========================================
_DO_KHO_MAP = {
    'de': 'Dễ', 'dễ': 'Dễ', 'easy': 'Dễ', '1': 'Dễ',
    'trung binh': 'Trung bình', 'trung bình': 'Trung bình', 'tb': 'Trung bình',
    'medium': 'Trung bình', 'normal': 'Trung bình', '2': 'Trung bình',
    'kho': 'Khó', 'khó': 'Khó', 'hard': 'Khó', 'difficult': 'Khó', '3': 'Khó',
}

_BLOOM_MAP = {
    'nho': 'Nhớ', 'nhớ': 'Nhớ', 'remember': 'Nhớ', 'biet': 'Nhớ', 'biết': 'Nhớ',
    'nhan biet': 'Nhớ', 'nhận biết': 'Nhớ', '1': 'Nhớ',
    'hieu': 'Hiểu', 'hiểu': 'Hiểu', 'understand': 'Hiểu', 'thong hieu': 'Hiểu',
    'thông hiểu': 'Hiểu', '2': 'Hiểu',
    'van dung': 'Vận dụng', 'vận dụng': 'Vận dụng', 'apply': 'Vận dụng',
    'ap dung': 'Vận dụng', 'áp dụng': 'Vận dụng', '3': 'Vận dụng',
    'phan tich': 'Phân tích', 'phân tích': 'Phân tích', 'analyze': 'Phân tích',
    'analyse': 'Phân tích', 'danh gia': 'Phân tích', 'đánh giá': 'Phân tích',
    'evaluate': 'Phân tích', 'sang tao': 'Phân tích', 'create': 'Phân tích',
    '4': 'Phân tích', '5': 'Phân tích', '6': 'Phân tích',
}


def bo_dau(s):
    """Bỏ dấu tiếng Việt + hạ chữ thường (dùng để so khớp, không để hiển thị).

    'đ' PHẢI được thay tay: NFD chỉ tách được dấu thanh/dấu mũ, KHÔNG tách 'đ'
    thành 'd' + dấu gạch. Bỏ sót bước này thì "đâu là phát biểu đúng" ra
    "đau la phat bieu đung" -> mọi mẫu regex viết 'dau/dung' KHÔNG BAO GIỜ khớp
    (câu hỏi yếu lọt lưới), và _BLOOM_MAP tra "đanh gia" cũng không ra.
    """
    s = str(s or '').lower().replace('đ', 'd')
    s = unicodedata.normalize('NFD', s)
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


# Nhớ tạm kết quả chuẩn hóa: khâu sinh câu hỏi so mỗi câu mới với TOÀN BỘ ngân hàng
# (tới 3000 câu) và lặp lại ở từng vòng bổ sung -> cùng một chuỗi bị chuẩn hóa lại
# hàng trăm nghìn lần. Cache khiến mỗi chuỗi chỉ xử lý đúng một lần.
@lru_cache(maxsize=8192)
def _chuan_hoa_text_cached(s):
    return ' '.join(re.sub(r'[^\w\s]', ' ', bo_dau(s)).split())


def chuan_hoa_text(s):
    """Chuẩn hóa để so TRÙNG Y NGUYÊN: bỏ dấu, bỏ dấu câu, gộp khoảng trắng."""
    return _chuan_hoa_text_cached(str(s or ''))


@lru_cache(maxsize=8192)
def _bo_tu_cached(s):
    # frozenset (không phải set): giá trị đã cache KHÔNG được để ai sửa tại chỗ.
    return frozenset(_chuan_hoa_text_cached(s).split())


def _bo_tu(s):
    return _bo_tu_cached(str(s or ''))


def chuan_hoa_do_kho(v):
    """Ép về đúng 3 giá trị hệ thống dùng. Không nhận dạng được -> None (thiếu)."""
    return _DO_KHO_MAP.get(str(v or '').strip().lower())


def chuan_hoa_bloom(v):
    """Ép về đúng 4 mức Bloom. Không nhận dạng được -> None (thiếu)."""
    key = str(v or '').strip().lower()
    if key in _BLOOM_MAP:
        return _BLOOM_MAP[key]
    key = bo_dau(key)
    return _BLOOM_MAP.get(key)


def chuan_hoa_dap_an(q):
    """Ép 'dap_an_dung' về 1 chữ cái A/B/C/D. AI hay trả 'a.', 'B)' hoặc nguyên
    văn nội dung phương án. Không xác định được -> None (để báo lỗi, KHÔNG đoán
    bừa thành 'A' như trước — đoán bừa chính là nguồn gốc của câu sai đáp án)."""
    raw = str(q.get('dap_an_dung', '') or '').strip()
    up = raw.upper()
    if up[:1] in DAP_AN_HOP_LE and (len(up) == 1 or not up[1].isalpha()):
        return up[0]
    # AI trả về nguyên văn nội dung -> dò khớp với từng phương án
    for letter in DAP_AN_HOP_LE:
        opt = str(q.get('cau_' + letter.lower(), '') or '').strip()
        if opt and chuan_hoa_text(opt) == chuan_hoa_text(raw):
            return letter
    return None


# ==========================================
# PHÁT HIỆN CÂU HỎI YẾU
# ==========================================
# Các mẫu câu KHÔNG kiểm tra kiến thức thật — chỉ hỏi mục lục / tiêu đề / nói
# chung chung về tài liệu. Khớp trên chuỗi ĐÃ BỎ DẤU nên không lệ thuộc dấu.
_MAU_CAU_YEU = [
    r'chuong\s*\d+.*(tieu de|ten|goi la|la gi)',   # "Chương 3 ... có tiêu đề là gì"
    r'(tieu de|ten goi).*(chuong|muc|bai|phan)\s*\d*',
    r'tai lieu (nay|tren).*(noi ve|de cap|trinh bay).*gi',
    r'noi dung chinh cua (tai lieu|chuong|bai|muc)',
    r'(muc luc|bo cuc).*(tai lieu|giao trinh)',
    r'tai lieu.*co bao nhieu (chuong|muc|phan|bai)',
    r'chuong\s*\d+\s*(la|co)\s*(chuong|phan|muc)\s*(thu)?\s*\d*',
    r'^theo tai lieu,? dau la phat bieu dung\??$',   # không nêu nội dung cần hỏi
    # --- Hỏi về BỐ CỤC tài liệu thay vì kiến thức trong tài liệu ---
    r'(chuong|muc|phan|bai)\s*\d+.*(trinh bay|de cap|noi ve|gioi thieu).*(gi|nao)',
    r'(chuong|muc|phan|bai)\s*(nao|thu may).*(trinh bay|de cap|noi ve|chua)',
    r'(dau|day) la (chuong|muc|phan) (thu )?\d+',
    r'(hinh|bang|so do|vi du)\s*\d+.*(the hien|minh hoa|cho thay).*gi',
    r'(tai lieu|giao trinh|slide|bai giang).*(gom|chia thanh|co).*\d+.*(chuong|phan|muc)',
    # --- Hỏi chung chung, không nêu rõ đang kiểm tra kiến thức nào ---
    r'^(theo tai lieu|theo noi dung tren|dua vao tai lieu),?\s*(dau la|cau nao|y nao|'
    r'phat bieu nao|nhan dinh nao)\b.{0,25}$',
    r'^(dau la|cau nao|y nao|phat bieu nao|dieu nao)\s*(sau day\s*)?(la\s*)?'
    r'(dung|sai|chinh xac|khong dung)\s*\??$',
    r'(noi dung|kien thuc|van de) (chinh|quan trong|co ban) (nhat )?(cua|trong) '
    r'(chuong|bai|muc|phan|tai lieu)',
    # --- Hỏi về PHẦN PHỤ của giáo trình (lời nói đầu, lời cảm ơn, mục lục, tài
    # liệu tham khảo...). Các phần này đã bị cắt ở khâu đọc PDF, nhưng tài liệu
    # KHÔNG chia chương thì không cắt được phần đầu -> cần lưới thứ hai ở đây.
    r'(nhom|ban) (bien soan|tac gia)',
    r'bien soan (tai lieu|giao trinh|cuon sach)',
    r'loi (noi dau|mo dau|cam on|cam doan|tua|ket)',
    r'phan (gioi thieu|mo dau|loi noi dau)',
    r'(y kien|noi dung) dong gop',
    r'(thieu sot|sai sot).*(tai lieu|giao trinh|nhom)',
    r'(tai lieu|giao trinh) (nay )?(duoc )?(bien soan|xuat ban|phat hanh)',
    r'(tai lieu tham khao|phu luc|danh muc (hinh|bang|tu viet tat))',
    r'(muc tieu|doi tuong) (cua )?(mon hoc|hoc phan|tai lieu|giao trinh)',
]
_RE_CAU_YEU = [re.compile(p) for p in _MAU_CAU_YEU]

# Câu quá ngắn / quá chung chung thì không thể kiểm tra kiến thức thật
_DO_DAI_TOI_THIEU = 20     # ký tự


def la_cau_hoi_yeu(noi_dung):
    """True nếu câu hỏi thuộc dạng YẾU (hỏi tiêu đề/mục lục/chung chung)."""
    txt = chuan_hoa_text(noi_dung)
    if len(str(noi_dung or '').strip()) < _DO_DAI_TOI_THIEU:
        return True
    return any(r.search(txt) for r in _RE_CAU_YEU)


# ==========================================
# SUY LUẬN BLOOM KHI AI BỎ TRỐNG
# ==========================================
_BLOOM_TU_KHOA = [
    # (mức Bloom, các từ khóa nhận dạng — đã bỏ dấu)
    ('Phân tích', ('so sanh', 'phan tich', 'danh gia', 'nguyen nhan', 'vi sao',
                   'tai sao', 'moi quan he', 'anh huong', 'uu nhuoc', 'khac nhau',
                   'khac biet', 'nhan xet', 'ket luan')),
    # KHÔNG dùng các từ quá chung như 'thuc hien', 'giai' — chúng xuất hiện trong cả
    # câu hỏi mức Nhớ/Hiểu ("CPU THỰC HIỆN chức năng nào?") và làm suy luận sai mức.
    ('Vận dụng',  ('tinh toan', 'hay tinh', 'ap dung', 'van dung', 'chuyen doi',
                   'ket qua la', 'gia tri cua', 'bao nhieu', 'cong thuc', 'suy ra')),
    ('Hiểu',      ('giai thich', 'phan biet', 'y nghia', 'ban chat', 'muc dich',
                   'vai tro', 'chuc nang', 'nham', 'de lam gi', 'dung de')),
    ('Nhớ',       ('la gi', 'dinh nghia', 'khai niem', 'gom', 'bao gom', 'thuoc',
                   'ten goi', 'nao sau day', 'liet ke')),
]


def suy_luan_bloom(noi_dung):
    """Gợi ý mức Bloom từ cách đặt câu hỏi khi AI bỏ trống.
    Ưu tiên mức CAO trước (phân tích -> vận dụng -> hiểu -> nhớ) vì từ khóa của
    mức cao đặc trưng hơn. Không nhận ra -> mặc định 'Hiểu' (mức trung tính)."""
    txt = chuan_hoa_text(noi_dung)
    for muc, tu_khoa in _BLOOM_TU_KHOA:
        if any(t in txt for t in tu_khoa):
            return muc
    return 'Hiểu'


# ==========================================
# SO TRÙNG
# ==========================================
def do_tuong_dong(a, b):
    """Độ giống nhau giữa 2 câu hỏi (0..1).

    Dùng TẬP TỪ (Jaccard) chứ KHÔNG so từng ký tự: so ký tự rất dễ xóa oan —
    'tầng nào chịu trách nhiệm ĐỊNH TUYẾN?' và 'tầng nào chịu trách nhiệm MÃ HÓA?'
    giống nhau ~90% ký tự nhưng là 2 câu hỏi KHÁC nhau.
    Kết hợp thêm SequenceMatcher để bắt trường hợp đảo trật tự từ.
    """
    ta, tb = _bo_tu(a), _bo_tu(b)
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, chuan_hoa_text(a), chuan_hoa_text(b)).ratio()
    # Jaccard là chính; seq chỉ được tính khi tập từ ĐÃ khá giống (đảo trật tự)
    return max(jac, seq if jac >= 0.7 else 0.0)


def trung_y_nguyen(a, b):
    """Trùng KHÍT sau khi chuẩn hóa (thường hóa, bỏ dấu câu, gộp khoảng trắng)."""
    return chuan_hoa_text(a) == chuan_hoa_text(b)


def dap_an_noi_dung(q):
    """Nội dung của phương án ĐÚNG (không phải chữ cái). '' nếu không xác định."""
    dap = str(q.get('dap_an_dung') or '').strip().upper()[:1]
    if dap not in DAP_AN_HOP_LE:
        return ''
    return str(q.get('cau_' + dap.lower()) or '').strip()


def trung_nhau(q1, q2, nguong=NGUONG_TRUNG):
    """True nếu 2 câu hỏi kiểm tra CÙNG MỘT kiến thức.

    ĐÁP ÁN ĐÚNG mới là thứ quyết định câu hỏi đang kiểm tra kiến thức gì — không
    phải câu chữ. Nên phải xét CẶP (phần hỏi, đáp án đúng):

      1. Phần hỏi trùng y nguyên -> trùng.
      2. Đáp án đúng NHƯ NHAU (>= NGUONG_TRUNG_DAP_AN) và phần hỏi còn hao hao
         (>= NGUONG_TRUNG_Y) -> TRÙNG Ý: cùng kiến thức, chỉ đổi cách diễn đạt.
         Đây là dạng AI hay mắc khi bị ép tạo thêm câu.
      3. Phần hỏi gần y hệt (>= `nguong`) NHƯNG đáp án đúng khác hẳn -> KHÔNG trùng.
         "Cổng mặc định của HTTP?" (80) và "Cổng mặc định của HTTPS?" (443) giống
         nhau 98% câu chữ mà là hai câu hỏi khác nhau hoàn toàn. Chỉ coi là trùng
         khi đáp án còn giống nhau ít nhiều (>= NGUONG_KHAC_HAN).
    """
    nd1 = str(q1.get('noi_dung') or q1.get('cau_hoi') or '')
    nd2 = str(q2.get('noi_dung') or q2.get('cau_hoi') or '')
    if not nd1 or not nd2:
        return False
    if trung_y_nguyen(nd1, nd2):
        return True

    sim_hoi = do_tuong_dong(nd1, nd2)
    d1, d2  = dap_an_noi_dung(q1), dap_an_noi_dung(q2)
    sim_dap = do_tuong_dong(d1, d2) if (d1 and d2) else 0.0

    if sim_dap >= NGUONG_TRUNG_DAP_AN and sim_hoi >= NGUONG_TRUNG_Y:
        return True
    if sim_hoi >= nguong and sim_dap >= NGUONG_KHAC_HAN:
        return True
    return False


def _co_the_trung(ta, tb):
    """Sàng THÔ trước khi tính độ giống thật (rẻ hơn nhiều lần).

    Jaccard luôn <= (số từ câu ngắn / số từ câu dài), và do_tuong_dong() chỉ dùng
    tới SequenceMatcher (đắt) khi Jaccard >= 0.7. Nên hai câu chênh lệch độ dài
    quá 30% thì KHÔNG THỂ đạt ngưỡng trùng -> bỏ qua ngay, khỏi tính.
    Nhờ vậy khâu so với ngân hàng 3000 câu không phải chạy SequenceMatcher hàng
    trăm nghìn lần.
    """
    if not ta or not tb:
        return False
    ngan, dai = (len(ta), len(tb)) if len(ta) <= len(tb) else (len(tb), len(ta))
    return ngan / dai >= 0.7


# ==========================================
# THẨM ĐỊNH 1 CÂU HỎI
# ==========================================
def tham_dinh_mot_cau(q, tu_dong_bo_sung=True):
    """Chuẩn hóa + kiểm tra 1 câu hỏi (sửa TẠI CHỖ dict q).

    Trả về danh sách mã cảnh báo. Đồng thời gán vào q:
      q['canh_bao']  : list mã cảnh báo
      q['trang_thai']: cho_duyet | can_sua
      q['nguon_tao'] : 'AI'
    tu_dong_bo_sung=True -> tự suy luận Bloom / độ khó khi AI bỏ trống (vẫn giữ
    cảnh báo để giảng viên biết là hệ thống đoán, không phải AI trả về).
    """
    cb = []

    # --- Nội dung & 4 phương án ---
    noi_dung = str(q.get('noi_dung') or q.get('cau_hoi') or '').strip()
    # Giữ đồng bộ CẢ HAI khóa: AI trả về 'cau_hoi', còn DB/template dùng 'noi_dung'.
    q['noi_dung'] = noi_dung
    q['cau_hoi'] = noi_dung
    opts = {}
    for k in ('a', 'b', 'c', 'd'):
        opts[k] = str(q.get('cau_' + k, '') or '').strip()
        q['cau_' + k] = opts[k]

    if not noi_dung or any(not v for v in opts.values()):
        cb.append('thieu_du_lieu')

    # Phương án nhiễu trùng nhau -> học sinh thấy 2 lựa chọn y hệt
    ds_chuan = [chuan_hoa_text(v) for v in opts.values() if v]
    if len(ds_chuan) == 4 and len(set(ds_chuan)) < 4:
        cb.append('nhieu_trung')

    # --- Đáp án đúng phải là A/B/C/D và phải TRỎ VÀO một phương án có nội dung ---
    dap = chuan_hoa_dap_an(q)
    if dap not in DAP_AN_HOP_LE:
        cb.append('sai_dap_an')
        q['dap_an_dung'] = ''
    else:
        q['dap_an_dung'] = dap
        if not opts[dap.lower()]:
            cb.append('sai_dap_an')

    # --- Độ khó ---
    dk = chuan_hoa_do_kho(q.get('do_kho'))
    if not dk:
        cb.append('thieu_do_kho')
        dk = 'Trung bình' if tu_dong_bo_sung else None
    q['do_kho'] = dk

    # --- Bloom ---
    bl = chuan_hoa_bloom(q.get('bloom'))
    if not bl:
        cb.append('thieu_bloom')
        bl = suy_luan_bloom(noi_dung) if tu_dong_bo_sung else None
    q['bloom'] = bl

    # --- Giải thích ---
    gt = str(q.get('giai_thich', '') or '').strip()
    q['giai_thich'] = gt
    if not gt:
        cb.append('thieu_giai_thich')

    # --- Chương: ép về số ---
    m = re.search(r'\d+', str(q.get('chuong', '') or ''))
    q['chuong'] = int(m.group()) if m else 0

    # --- Câu hỏi yếu ---
    if noi_dung and la_cau_hoi_yeu(noi_dung):
        cb.append('cau_yeu')

    q['nguon_tao'] = q.get('nguon_tao') or 'AI'
    q['canh_bao'] = cb
    q['trang_thai'] = TT_CHO_DUYET if not cb else TT_CAN_SUA
    return cb


# ==========================================
# THẨM ĐỊNH CẢ DANH SÁCH (kèm so trùng nội bộ + so với ngân hàng)
# ==========================================
def tham_dinh_danh_sach(danh_sach, cau_ngan_hang=None, nguong=NGUONG_TRUNG):
    """Thẩm định toàn bộ danh sách câu hỏi AI vừa tạo.

    cau_ngan_hang: list dict {'id', 'noi_dung'} các câu ĐÃ CÓ trong ngân hàng
                   (cùng học phần) để phát hiện trùng với kho sẵn có.
    Trả về (danh_sach_da_gan_co, thong_ke).
    Câu trùng Y NGUYÊN với câu trước đó trong CÙNG lô -> bị LOẠI hẳn (vô nghĩa).
    Câu gần trùng -> GIỮ LẠI nhưng đánh dấu 'nghi_trung' + kèm câu đối chiếu.
    """
    cau_ngan_hang = cau_ngan_hang or []
    ket_qua = []
    da_nhan = []          # các câu đã chấp nhận trong lô này

    for q in danh_sach:
        if not isinstance(q, dict):
            continue
        tham_dinh_mot_cau(q)
        nd = q['noi_dung']

        if not nd:
            ket_qua.append(q)
            continue

        # 1) Trùng Y NGUYÊN với câu khác trong cùng lô -> bỏ hẳn
        if any(trung_y_nguyen(nd, x['noi_dung']) for x in da_nhan):
            continue

        # 2) Gần trùng trong cùng lô (kể cả TRÙNG Ý: hỏi lại kiến thức cũ bằng
        #    câu chữ khác nhưng đáp án đúng y hệt)
        for x in da_nhan:
            if trung_nhau(q, x, nguong):
                sim = do_tuong_dong(nd, x['noi_dung'])
                q['canh_bao'].append('nghi_trung')
                q['trang_thai'] = TT_NGHI_TRUNG
                q['trung_voi'] = {
                    'noi_dung': x['noi_dung'],
                    'do_giong': round(sim * 100, 1),
                    'o_dau': 'Trong lô vừa tạo',
                }
                break

        # 3) Trùng với câu ĐÃ CÓ trong ngân hàng
        if 'nghi_trung' not in q['canh_bao']:
            tu_nd = _bo_tu(nd)
            for c in cau_ngan_hang:
                if not _co_the_trung(tu_nd, _bo_tu(c.get('noi_dung', ''))):
                    continue      # sàng thô: chênh lệch độ dài quá lớn -> khỏi tính
                sim = do_tuong_dong(nd, c.get('noi_dung', ''))
                if sim >= nguong:
                    q['canh_bao'].append('trung_ngan_hang')
                    q['trang_thai'] = TT_NGHI_TRUNG
                    q['trung_voi'] = {
                        'noi_dung': c.get('noi_dung', ''),
                        'do_giong': round(sim * 100, 1),
                        'o_dau': 'Đã có trong ngân hàng câu hỏi',
                    }
                    break

        da_nhan.append(q)
        ket_qua.append(q)

    thong_ke = {
        'tong'      : len(ket_qua),
        'dat'       : sum(1 for q in ket_qua if not q['canh_bao']),
        'can_xem'   : sum(1 for q in ket_qua if q['canh_bao']),
        'trung_lap' : len(danh_sach) - len(ket_qua),   # bị loại vì trùng y nguyên
    }
    return ket_qua, thong_ke


# ==========================================
# CỔNG CHẶN TRƯỚC KHI LƯU
# ==========================================
def du_dieu_kien_luu(q):
    """True nếu câu hỏi ĐỦ ĐIỀU KIỆN lưu vào ngân hàng chính thức.

    Đây là hàng rào CUỐI ở server: dù giao diện có gửi lên gì đi nữa, câu thiếu
    trường bắt buộc hoặc sai đáp án đều KHÔNG được lưu.
    """
    noi_dung = str(q.get('noi_dung') or q.get('cau_hoi') or '').strip()
    if not noi_dung:
        return False, 'Thiếu nội dung câu hỏi.'
    for k in ('a', 'b', 'c', 'd'):
        if not str(q.get('cau_' + k) or '').strip():
            return False, f'Thiếu phương án {k.upper()}.'
    # 4 phương án phải KHÁC NHAU: nếu 2 lựa chọn giống hệt thì câu hỏi vô nghĩa
    # (thí sinh thấy 2 đáp án y hệt) và có thể có tới 2 phương án "đúng".
    opts = [chuan_hoa_text(q.get('cau_' + k)) for k in ('a', 'b', 'c', 'd')]
    if len(set(opts)) < 4:
        return False, 'Có hai phương án trùng nội dung nhau.'
    dap = str(q.get('dap_an_dung') or '').strip().upper()
    if dap not in DAP_AN_HOP_LE:
        return False, 'Đáp án đúng phải là A, B, C hoặc D.'
    if la_cau_hoi_yeu(noi_dung):
        return False, ('Câu hỏi yếu: chỉ hỏi tiêu đề/mục lục/số chương hoặc quá '
                       'chung chung, không kiểm tra kiến thức thật.')
    if chuan_hoa_do_kho(q.get('do_kho')) not in DO_KHO_HOP_LE:
        return False, 'Độ khó phải là Dễ, Trung bình hoặc Khó.'
    if chuan_hoa_bloom(q.get('bloom')) not in BLOOM_HOP_LE:
        return False, 'Mức Bloom phải là Nhớ, Hiểu, Vận dụng hoặc Phân tích.'
    if not str(q.get('giai_thich') or '').strip():
        return False, 'Thiếu giải thích đáp án.'
    return True, None


# ==========================================
# LỌC LẤY CÁC CÂU DÙNG ĐƯỢC NGAY
# ==========================================
# Dùng ở khâu SINH câu hỏi: chỉ giữ câu vừa ĐỦ ĐIỀU KIỆN LƯU vừa KHÔNG trùng, để
# vòng bổ sung biết còn thiếu bao nhiêu câu THẬT SỰ dùng được mà xin thêm cho đủ.
# Khác với tham_dinh_danh_sach() (giữ lại câu lỗi + gắn cảnh báo cho giảng viên
# xem): ở đây câu lỗi bị GẠT RA để xin AI tạo câu khác thay thế — người dùng yêu
# cầu 20 câu thì phải nhận đủ 20 câu dùng được, không phải 20 câu rồi mất 11.
LY_DO_LOAI = {
    'khong_dat'      : 'không đạt chuẩn',
    'trung_lo'       : 'trùng nhau',
    'trung_ngan_hang': 'đã có trong ngân hàng',
}


def loc_cau_dung_duoc(danh_sach, cau_ngan_hang=None, nguong=NGUONG_TRUNG,
                      da_nhan=None):
    """Lọc ra các câu DÙNG ĐƯỢC NGAY từ lô AI vừa trả về.

    da_nhan: các câu đã nhận ở vòng trước (câu mới phải không trùng với chúng).
    Trả về (sach, ly_do) — `sach` là danh sách MỚI được nhận thêm, `ly_do` là dict
    đếm số câu bị loại theo từng nguyên nhân (để báo cho giảng viên biết vì sao
    thiếu, thay vì im lặng như trước).
    """
    cau_ngan_hang = cau_ngan_hang or []
    da_nhan = list(da_nhan or [])
    sach, ly_do = [], {}

    for q in danh_sach:
        if not isinstance(q, dict):
            continue
        tham_dinh_mot_cau(q)

        ok, _ = du_dieu_kien_luu(q)
        if not ok:
            ly_do['khong_dat'] = ly_do.get('khong_dat', 0) + 1
            continue

        if any(trung_nhau(q, x, nguong) for x in da_nhan):
            ly_do['trung_lo'] = ly_do.get('trung_lo', 0) + 1
            continue

        nd = q['noi_dung']
        tu_nd = _bo_tu(nd)
        if any(_co_the_trung(tu_nd, _bo_tu(c.get('noi_dung', '')))
               and do_tuong_dong(nd, c.get('noi_dung', '')) >= nguong
               for c in cau_ngan_hang):
            ly_do['trung_ngan_hang'] = ly_do.get('trung_ngan_hang', 0) + 1
            continue

        da_nhan.append(q)
        sach.append(q)

    return sach, ly_do
