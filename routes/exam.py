"""
SINH CÂU HỎI BẰNG AI + TRANG CHỦ + THƯ VIỆN ĐỀ.

File lớn nhất dự án. Chuỗi xử lý chính, từ file PDF tới câu hỏi:

    PDF tải lên
      -> tách chương (bên dưới)   — tìm đúng phần nội dung bài giảng
      -> gọi AI sinh câu hỏi      — thử lần lượt nhiều nhà cung cấp
      -> chuẩn hóa đáp án
      -> trả về cho giáo viên xem, sửa, rồi lưu vào thư viện

Lưu ý: khâu sinh câu hỏi ở đây KHÔNG kiểm duyệt chất lượng. Việc thẩm định (đủ
trường, không phải câu hỏi yếu, không trùng lặp) chỉ diễn ra khi giáo viên đẩy
câu hỏi vào NGÂN HÀNG CHUNG — nằm ở routes/ngan_hang.py, dùng routes/kiem_duyet.py.

BỐN PHẦN CỦA FILE (theo thứ tự):
  1. ĐỌC PDF & TÁCH CHƯƠNG — phần rối nhất, và cũng là phần quyết định chất
     lượng câu hỏi. Đưa cả quyển giáo trình cho AI thì nó sẽ hỏi về mục lục,
     lời nói đầu, tài liệu tham khảo. Phải cắt đúng phần nội dung trước.
  2. GỌI AI          — nhiều nhà cung cấp xếp hàng dự phòng cho nhau.
  3. TRANG CHỦ       — dashboard, khác nhau giữa học sinh và giáo viên.
  4. THƯ VIỆN ĐỀ     — lưu, sửa, xóa, xuất Excel các bộ câu hỏi và đề thi.

Hai cạm bẫy tiếng Việt đã gặp và đã xử lý, được ghi ngay tại chỗ bên dưới:
re.IGNORECASE không đáng tin với chữ Ư/Ơ, và font PDF cũ mã hóa sai chữ "ư".
"""
import os
import re
import json
import time
import random
import requests
import uuid
import unicodedata
from datetime import datetime
from flask import (Blueprint, jsonify, render_template, request,
                   redirect, session, flash, url_for, current_app,
                   send_from_directory, abort)
from werkzeug.utils import secure_filename
import pdfplumber
from database import get_db_connection
from decorators import login_required, giao_vien_required

exam_bp = Blueprint('exam', __name__)


# ==========================================
# PHẦN 1: ĐỌC PDF VÀ TÁCH CHƯƠNG
# ==========================================

# Regex nhận diện tiêu đề chương và các từ tương đương (phần, bài, mục…).
# QUAN TRỌNG: text PHẢI được .lower() trước khi khớp — KHÔNG dùng re.IGNORECASE
# cho ký tự tiếng Việt (Ư/ư, Ơ/ơ) vì Python re có thể không case-fold đúng
# với Latin Extended-B, dẫn đến "CHƯƠNG VI" không khớp dù đã bật IGNORECASE.
# Hỗ trợ: chương / chuong, phần / phan, bài / bai, mục / muc, chapter, part.
# Số chương: chữ số Ả-rập (1, 01) hoặc số La Mã (i, iv, vi, xii…).
# Lookahead (?=\W|$): token La Mã phải kết thúc ở ranh giới từ, tránh ăn nhầm
# (ví dụ "chương việc" không bị nhận là chương "vi").
_CHUONG_RE = re.compile(
    r'(?:chương|chuong|phần|phan|bài|bai|mục|muc|chapter|part)'
    r'\s*(\d+|[ivxlcdm]+)(?=\W|$)',
)

# Pattern nhận dạng dòng bảng mục lục (TOC): kết thúc bằng nhiều dấu chấm + số trang
_TOC_LINE_RE = re.compile(r'[.\s]{5,}\d+\s*$')

# Từ tiếng Việt đầu câu thân bài (phân biệt "Chương 2 chúng ta sẽ..." vs heading thật)
_SENTENCE_STARTERS = frozenset({
    'chúng', 'sẽ', 'ta', 'có', 'đã', 'đến', 'trong', 'về', 'của', 'và',
    'hay', 'này', 'đó', 'cũng', 'mà', 'là', 'một', 'các', 'bao', 'thực',
    'vì', 'do', 'khi', 'sau', 'trước', 'tuy', 'nên', 'nếu', 'được', 'theo',
    'thì', 'hơn', 'được', 'giúp', 'tìm', 'học', 'giới', 'trình', 'bày',
})

_ROMAN_VALUES = {'I': 1, 'V': 5, 'X': 10, 'L': 50,
                 'C': 100, 'D': 500, 'M': 1000}


def _roman_to_int(s):
    """Quy đổi số La Mã -> int. Trả về 0 nếu không hợp lệ."""
    s = s.upper()
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN_VALUES.get(ch)
        if v is None:
            return 0
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


def _chuong_so(token):
    """Token chương (chữ số hoặc số La Mã) -> int. Trả 0 nếu không đọc được."""
    token = token.strip()
    return int(token) if token.isdigit() else _roman_to_int(token)


# Bảng sửa ký tự bị mã hóa sai bởi font PDF cũ (VnTime, ABC...).
# Một số font map "Ư/ư" sang U+01A2/U+01A3 (LATIN LETTER OI) thay vì
# U+01AF/U+01B0 (LATIN LETTER U WITH HORN). Hai ký tự này hoàn toàn khác
# nhau nên NFC normalize lẫn re.IGNORECASE đều không giúp được — phải thay
# thủ công trước khi so khớp.
_FONT_FIX = str.maketrans({
    'Ƣ': 'Ư',  # Ƣ  →  Ư  (CAPITAL LETTER OI → U WITH HORN)
    'ƣ': 'ư',  # ƣ  →  ư  (SMALL LETTER OI   → U WITH HORN)
})

# ==========================================
# GIỚI HẠN GỌI AI
#
# Mỗi lượt gọi AI là tiền thật. Không chặn thì một người bấm nhầm vài chục lần
# là hết hạn mức của cả hệ thống. Bộ đếm nằm ở bảng api_usage, theo từng tài
# khoản và từng ngày.
# ==========================================
_GIOI_HAN_LAN_NGAY = 3      # số lượt tạo câu hỏi mỗi tài khoản mỗi ngày
_GIOI_HAN_CAU_LAN  = 50     # số câu tối đa xin AI trong một lượt
_GIOI_HAN_KY_TU    = 60000  # độ dài tối đa của văn bản dán vào, tránh vượt cửa sổ token


def _kiem_tra_gioi_han(user_id):
    """Còn lượt gọi AI hôm nay không? Trả về (ok, số lượt đã dùng, lời nhắn)."""
    from datetime import date
    conn = get_db_connection()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT so_lan FROM api_usage WHERE user_id=%s AND ngay=%s",
            (user_id, date.today())
        )
        row = cur.fetchone()
        so_da_dung = row['so_lan'] if row else 0
        if so_da_dung >= _GIOI_HAN_LAN_NGAY:
            return False, so_da_dung, (
                f'Bạn đã sử dụng hết {_GIOI_HAN_LAN_NGAY} lượt tạo câu hỏi '
                f'trong hôm nay. Giới hạn được đặt lại lúc 00:00 ngày mai.'
            )
        return True, so_da_dung, ''
    finally:
        cur.close()
        conn.close()


def _ghi_nhan_luot_dung(user_id):
    """Cộng 1 vào bộ đếm hôm nay. CHỈ gọi sau khi AI đã trả kết quả thành công.

    Trừ lượt trước khi gọi thì người dùng mất lượt oan mỗi lần nhà cung cấp AI
    lỗi hoặc quá tải — chuyện xảy ra thường xuyên.
    """
    from datetime import date
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO api_usage (user_id, ngay, so_lan) VALUES (%s, %s, 1)
            ON DUPLICATE KEY UPDATE so_lan = so_lan + 1
        """, (user_id, date.today()))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _doc_text_pdf(filepath):
    """Trích toàn bộ text từ PDF, chuẩn hóa Unicode NFC và sửa font sai."""
    extracted_text = ""
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                extracted_text += text + "\n"
    text = unicodedata.normalize('NFC', extracted_text)
    return text.translate(_FONT_FIX)


def _la_dong_heading(line):
    """Kiểm tra một dòng text có phải tiêu đề chương thật hay không.

    Lọc bỏ 3 loại nhiễu:
    1. Dòng bảng mục lục (TOC): kết thúc bằng "...... 15"
    2. Dòng thân bài: từ khóa chương nằm giữa câu hoặc theo sau là câu văn
    3. Dòng quá dài: heading thực tế luôn ngắn gọn (≤ 80 ký tự)
    """
    s = line.strip()
    if not s:
        return False
    if _TOC_LINE_RE.search(s):      # lọc TOC
        return False
    lower = s.lower()
    m = _CHUONG_RE.search(lower)
    if not m:
        return False
    if m.start() > 10:              # từ khóa phải ở gần đầu dòng
        return False
    if len(s) > 80:                 # heading không quá dài
        return False
    # Từ liền sau số chương: nếu là từ đầu câu thân bài → bỏ qua
    after = lower[m.end():].strip()
    if after:
        next_word = after.split()[0].rstrip(':.,;') if after.split() else ''
        if next_word in _SENTENCE_STARTERS:
            return False
    return True


# Tiêu đề các phần PHỤ nằm SAU nội dung bài giảng. Chương CUỐI phải dừng ở đây,
# nếu không nó nuốt luôn "Tài liệu tham khảo", "Phụ lục" -> AI lấy đó ra hỏi.
_PHAN_PHU_CUOI_RE = re.compile(
    r'^\s*('
    r'tài\s*liệu\s*tham\s*khảo'
    r'|phụ\s*lục'
    r'|danh\s*mục\s*tài\s*liệu'
    r'|references|bibliography|appendix|index'
    r')\b'
)

# Một chương THẬT luôn có thân bài phía sau. Dòng "Chương 1: Tổng quan" nằm trong
# BẢNG MỤC LỤC thì ngay sau nó là dòng mục lục kế tiếp -> thân bài rỗng. Dùng độ
# dài tối thiểu này để loại các heading giả trong mục lục (kể cả mục lục KHÔNG có
# dấu chấm dẫn trang, thứ mà _TOC_LINE_RE không bắt được).
_THAN_BAI_TOI_THIEU = 200   # ký tự


def _xay_dict_chuong(extracted_text):
    """Xây dựng dict {so_chuong: noi_dung} từ văn bản PDF đã trích xuất.

    Nội dung mỗi chương BẮT ĐẦU ở tiêu đề chương đó và KẾT THÚC ở tiêu đề chương
    kế tiếp — hoặc ở phần phụ cuối tài liệu (tài liệu tham khảo, phụ lục) nếu đó
    là chương cuối. Nhờ vậy:
      • Phần đầu (bìa, lời nói đầu, lời cảm ơn, mục lục) nằm TRƯỚC chương 1 -> bị bỏ.
      • Phần cuối (tài liệu tham khảo, phụ lục) -> bị bỏ.
      • Chương không được chọn -> không lọt vào chương khác.
    """
    lines = extracted_text.splitlines(keepends=True)
    # Vị trí ký tự tích lũy của từng dòng
    char_pos = []
    pos = 0
    for ln in lines:
        char_pos.append(pos)
        pos += len(ln)

    # 1) MỌI dòng trông như tiêu đề chương (chưa khử trùng số chương)
    ung_vien = []                       # [(char_start, so_chuong)]
    for idx, line in enumerate(lines):
        if not _la_dong_heading(line):
            continue
        m = _CHUONG_RE.search(line.lower())
        if not m:
            continue
        so = _chuong_so(m.group(1))
        if so > 0:
            ung_vien.append((char_pos[idx], so))

    # 2) Biên KẾT THÚC của phần bài giảng = tiêu đề phần phụ cuối đầu tiên nằm SAU
    #    chương đầu tiên. Heading phần phụ luôn ngắn; câu văn thân bài có nhắc tới
    #    "tài liệu tham khảo" thì dài hơn nhiều -> chặn độ dài để khỏi cắt nhầm.
    ket = len(extracted_text)
    if ung_vien:
        for idx, line in enumerate(lines):
            s = line.strip().lower()
            if (len(s) <= 60 and _PHAN_PHU_CUOI_RE.match(s)
                    and char_pos[idx] > ung_vien[0][0]):
                ket = char_pos[idx]
                break
    ung_vien = [(p, so) for p, so in ung_vien if p < ket]

    # 3) Bỏ heading GIẢ (dòng trong bảng mục lục): không có thân bài phía sau
    thuc = []
    for i, (start, so) in enumerate(ung_vien):
        end = ung_vien[i + 1][0] if i + 1 < len(ung_vien) else ket
        if end - start >= _THAN_BAI_TOI_THIEU:
            thuc.append((start, so))

    # 4) Cắt nội dung; số chương lặp lại (tiêu đề chạy ở đầu trang) -> giữ lần đầu
    dict_chapters = {}
    for i, (start, so) in enumerate(thuc):
        end = thuc[i + 1][0] if i + 1 < len(thuc) else ket
        if str(so) in dict_chapters:
            continue
        dict_chapters[str(so)] = extracted_text[start:end].strip()

    return dict_chapters


def _noi_dung_bai_giang(extracted_text, dict_chapters):
    """Văn bản dùng cho khối 'Toàn bộ nội dung'.

    Nếu tài liệu CÓ chương -> chỉ ghép nội dung các chương, nhờ đó bìa, lời nói đầu,
    mục lục, tài liệu tham khảo, phụ lục KHÔNG lọt vào prompt. Trước đây khối này
    gửi nguyên văn bản thô -> AI sinh ra câu hỏi kiểu "nhóm biên soạn mong nhận
    được ý kiến đóng góp về điều gì?".
    Tài liệu KHÔNG chia chương -> đành dùng nguyên văn (không có mốc để cắt).
    """
    if not dict_chapters:
        return extracted_text
    return '\n\n'.join(
        dict_chapters[k]
        for k in sorted(dict_chapters, key=lambda x: int(x) if x.isdigit() else 0)
    )


def _phat_hien_chuong_pdf(filepath):
    """Phát hiện danh sách số chương trong PDF dùng pdfplumber + heuristic.

    Lọc 3 nguồn nhiễu: bảng mục lục (TOC), câu tham chiếu thân bài,
    dòng quá dài. Chỉ giữ lần đầu xuất hiện của mỗi số chương.
    """
    extracted_text = _doc_text_pdf(filepath)
    dict_ch = _xay_dict_chuong(extracted_text)
    return sorted(int(k) for k in dict_ch if k.isdigit())

# ==========================================
# PHẦN 2: GỌI AI
#
# Ba nhà cung cấp xếp thành hàng dự phòng. Hết người này mới tới người kia:
#
#   1. Claude chính hãng — dùng khi có key. Có prompt caching nên rẻ nhất khi
#      sinh lại câu hỏi từ cùng một file PDF.
#   2. Gemini            — miễn phí, dùng khi không có key Claude.
#   3. Proxy Claude      — chốt chặn cuối, khi hai đường trên đều hỏng.
#
# Xếp hàng như vậy vì API của model ngôn ngữ hay quá tải (lỗi 503) và trả lỗi
# tạm thời. Chỉ dựa vào một nhà cung cấp thì chức năng chính của cả hệ thống
# chết theo họ. MỌI KEY đọc từ .env, không bao giờ viết thẳng vào mã nguồn.
# ==========================================
API_KEY    = os.getenv('GEMINI_API_KEY')
GEMINI_MODELS = [
    "gemini-2.5-flash",        # ưu tiên: chính xác hơn
    "gemini-3.1-flash-lite",   # dự phòng: nhẹ, hầu như luôn còn hạn mức
]

def _gemini_url(model):
    return ("https://generativelanguage.googleapis.com"
            "/v1beta/models/" + model + ":generateContent")

CLAUDE_OFFICIAL_KEY   = os.getenv('CLAUDE_OFFICIAL_KEY')
CLAUDE_OFFICIAL_MODEL = os.getenv('CLAUDE_OFFICIAL_MODEL', 'claude-haiku-4-5')
CLAUDE_OFFICIAL_URL   = 'https://api.anthropic.com/v1/messages'

CLAUDE_PROXY_KEY   = os.getenv('CLAUDE_PROXY_KEY')
CLAUDE_PROXY_URL   = os.getenv('CLAUDE_PROXY_URL', 'https://taphoaapi.info.vn/v1/messages')
CLAUDE_PROXY_MODEL = os.getenv('CLAUDE_PROXY_MODEL', 'claude-haiku-4-5')

def _parse_anthropic_sse(raw):
    """Gom câu trả lời từ luồng SSE mà proxy trả về.

    Chỉ nhặt các mảnh 'text_delta'. Model có thể phát cả khối 'thinking' (phần
    tự lập luận nội bộ) — trộn nó vào câu trả lời thì JSON hỏng ngay.
    """
    parts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith('data:'):
            continue
        payload = line[5:].strip()
        if not payload or payload == '[DONE]':
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if obj.get('type') == 'content_block_delta':
            delta = obj.get('delta') or {}
            if delta.get('type') == 'text_delta':
                parts.append(delta.get('text', ''))
    return ''.join(parts).strip()

def _call_claude_official(parts):
    """Gọi Claude chính hãng, tận dụng prompt caching để tiết kiệm chi phí.

    Nội dung chia làm ba khối, khác nhau ở chỗ có được cache hay không:

      system  — bộ luật ra đề, không bao giờ đổi     -> cache.
      pdf     — nội dung tài liệu, rất lớn (20–40 nghìn token) -> cache.
      yêu cầu — số câu, độ khó, chương nào  -> KHÔNG cache, vì mỗi lần một khác.

    Cái lợi nằm ở khối PDF: giáo viên hiếm khi sinh câu hỏi một lần rồi thôi,
    họ sinh 20 câu chương 3, xem, rồi xin thêm 20 câu chương 4 từ CÙNG file đó.
    Lần thứ hai trở đi, phần PDF đọc từ cache và rẻ hơn khoảng 90%.
    """
    headers = {
        'x-api-key'        : CLAUDE_OFFICIAL_KEY,
        'anthropic-version': '2023-06-01',
        'anthropic-beta'   : 'prompt-caching-2024-07-31',
        'Content-Type'     : 'application/json',
    }
    body = {
        'model'     : CLAUDE_OFFICIAL_MODEL,
        'max_tokens': 8192,
        'system'    : [{'type': 'text', 'text': _lay_system_prompt(),
                        'cache_control': {'type': 'ephemeral'}}],
        'messages'  : [{
            'role'   : 'user',
            'content': [
                {'type': 'text', 'text': parts.get('pdf', ''),
                 'cache_control': {'type': 'ephemeral'}},
                # Khối yêu cầu phải nằm SAU khối PDF. Cache hoạt động theo tiền
                # tố: mọi thứ đứng trước điểm đánh dấu mới được dùng lại. Đặt
                # khối hay thay đổi lên trước là vô hiệu hóa toàn bộ cache.
                {'type': 'text', 'text': parts.get('requirements', '')},
            ]
        }]
    }
    resp = requests.post(CLAUDE_OFFICIAL_URL, headers=headers, json=body, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get('usage', {})
    cr = usage.get('cache_read_input_tokens', 0)
    cw = usage.get('cache_creation_input_tokens', 0)
    if cr or cw:
        saved = round(cr * 0.9 / 1000, 2)
        print(f"[Cache] write={cw} | read={cr} (tiet kiem ~{saved}K token chi phi)")
    try:
        text = data['content'][0]['text'].strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError('Claude chính hãng trả về cấu trúc không hợp lệ')
    if not text:
        raise RuntimeError('Claude chính hãng trả về rỗng')
    return text

def _call_claude_proxy(prompt):
    """Gọi proxy Claude — chốt chặn cuối khi mọi đường khác đã hỏng."""
    headers = {
        'x-api-key'        : CLAUDE_PROXY_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Type'     : 'application/json',
    }
    body = {
        'model'     : CLAUDE_PROXY_MODEL,
        # Nới rộng vì hạn mức này tính CẢ phần model tự lập luận lẫn JSON trả về.
        'max_tokens': 16000,
        'messages'  : [{'role': 'user', 'content': prompt}],
    }
    resp = requests.post(CLAUDE_PROXY_URL, headers=headers, json=body, timeout=300)
    resp.raise_for_status()
    # Proxy không khai báo charset trong header, nên requests đoán bừa là
    # Latin-1 và mọi dấu tiếng Việt biến thành ký tự rác. Ép UTF-8.
    resp.encoding = 'utf-8'
    text = _parse_anthropic_sse(resp.text)
    if not text:
        raise RuntimeError('proxy Claude trả về rỗng')
    return text

def _trich_xuat_json_list(clean):
    """Đọc mảng JSON câu hỏi từ text AI trả về, CỨU được cả khi bị cắt cụt.

    Model chạm trần token giữa chừng là chuyện thường, và khi đó JSON thiếu dấu
    đóng ngoặc, json.loads() ném lỗi và mất trắng cả mẻ. Ở đây ta lùi về dấu '}'
    hoàn chỉnh cuối cùng rồi tự đóng mảng lại — xin được 20 câu mà chỉ nhận về
    17 câu nguyên vẹn vẫn hơn là nhận 0 câu.
    """
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    start = clean.find('[')
    if start == -1:
        raise ValueError('AI không trả về mảng JSON hợp lệ')
    frag = clean[start:]
    last = frag.rfind('}')
    if last != -1:
        try:
            return json.loads(frag[:last + 1] + ']')
        except json.JSONDecodeError:
            pass
    raise ValueError('Không phân tích được JSON từ AI (có thể bị cắt cụt do quá dài)')


# ---------- Kho tài liệu PDF của người dùng ----------
# File PDF được giữ lại trên đĩa để giáo viên sinh thêm câu hỏi từ cùng tài liệu
# mà không phải tải lên lại. Cố tình KHÔNG lưu vào cơ sở dữ liệu — nhét vài chục
# MB PDF vào MySQL là cách nhanh nhất làm phình cơ sở dữ liệu và chậm mọi thứ.
#
# Mỗi người một thư mục con riêng. Việc này còn có tác dụng phụ quan trọng:
# scripts/cleanup.py chỉ dọn file nằm TRỰC TIẾP trong uploads/, không đụng vào
# thư mục con, nên kho tài liệu không bị nó xóa nhầm.
UPLOAD_FOLDER = 'uploads'
TAILIEU_FOLDER = os.path.join(UPLOAD_FOLDER, 'tailieu')


def _thu_muc_tai_lieu(user_id):
    """Đường dẫn kho tài liệu của 1 user (tạo nếu chưa có)."""
    path = os.path.join(TAILIEU_FOLDER, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _luu_tai_lieu_vinh_vien(filepath, filename, user_id, chapters=None):
    """Chép file PDF vừa xử lý vào kho lâu dài của người dùng.

    Ghi kèm một file .json nhỏ chứa danh sách chương đã dò được. Dò chương phải
    đọc và phân tích cả quyển PDF, khá chậm; lần sau mở lại tài liệu này chỉ cần
    đọc file json là có ngay.

    Mọi lỗi ở đây đều NUỐT. Đây là việc phụ; không lưu được tài liệu thì cũng
    không có lý do gì làm hỏng việc chính là sinh câu hỏi cho người dùng.
    """
    try:
        import shutil
        ten_goc = filename.split('_', 1)[1] if '_' in filename else filename
        ten_goc = secure_filename(ten_goc) or 'tailieu.pdf'
        moc  = datetime.now().strftime('%Y%m%d%H%M%S')
        dich = os.path.join(_thu_muc_tai_lieu(user_id), f"{moc}_{ten_goc}")
        if not os.path.exists(dich):
            shutil.copy2(filepath, dich)
        if chapters:
            try:
                with open(dich + '.json', 'w', encoding='utf-8') as f:
                    json.dump({'chapters': chapters}, f, ensure_ascii=False)
            except OSError:
                pass
    except Exception:
        current_app.logger.exception('Không lưu được tài liệu PDF vào kho')


def _doc_kich_thuoc(so_byte):
    """Đổi số byte sang chuỗi dễ đọc (KB/MB)."""
    kb = so_byte / 1024
    return f"{kb:.0f} KB" if kb < 1024 else f"{kb/1024:.1f} MB"


def _liet_ke_tai_lieu(user_id):
    """Liệt kê tài liệu trong kho của một người, mới nhất lên đầu.

    Đọc thẳng từ thư mục, không hỏi cơ sở dữ liệu — thư mục CHÍNH LÀ nguồn sự
    thật duy nhất, nên không bao giờ có chuyện DB bảo có mà file thì đã mất.
    """
    thu_muc = os.path.join(TAILIEU_FOLDER, str(user_id))
    ket_qua = []
    if not os.path.isdir(thu_muc):
        return ket_qua
    for ten in os.listdir(thu_muc):
        dd = os.path.join(thu_muc, ten)
        if not os.path.isfile(dd) or not ten.lower().endswith('.pdf'):
            continue
        try:
            st = os.stat(dd)
        except OSError:
            continue
        # Cắt tiền tố thời gian (14 chữ số) khỏi tên hiển thị. Tên trên đĩa vẫn
        # giữ nguyên tiền tố để hai file cùng tên không đè lên nhau.
        dau = ten.split('_', 1)[0]
        ten_hien = ten.split('_', 1)[1] if ('_' in ten and dau.isdigit()) else ten
        ket_qua.append({
            'fname'          : ten,
            'ten'            : ten_hien,
            'kich_thuoc_byte': st.st_size,
            'kich_thuoc_str' : _doc_kich_thuoc(st.st_size),
            'ngay'           : datetime.fromtimestamp(st.st_mtime),
        })
    ket_qua.sort(key=lambda x: x['ngay'], reverse=True)
    return ket_qua


# ===== MAPPING BLOOM =====
BLOOM_MAP = {
    'Dễ'        : 'Nhớ (Remember) và Hiểu (Understand)',
    'Trung bình': 'Vận dụng (Apply) và Phân tích (Analyze)',
    'Khó'       : 'Đánh giá (Evaluate) và Sáng tạo (Create)'
}

BLOOM_DESC = {
    'Dễ': """
    • Nhớ (Remember): nhận biết, liệt kê, định nghĩa,
      gọi tên, xác định, nhớ lại kiến thức cơ bản
    • Hiểu (Understand): giải thích, mô tả, phân biệt,
      tóm tắt, diễn đạt lại bằng ngôn ngữ khác""",

    'Trung bình': """
    • Vận dụng (Apply): áp dụng công thức/quy tắc vào
      tình huống cụ thể, giải bài tập, thực hiện quy trình
    • Phân tích (Analyze): so sánh, phân loại, chỉ ra
      mối quan hệ, tìm nguyên nhân, chia nhỏ vấn đề""",

    'Khó': """
    • Đánh giá (Evaluate): nhận xét ưu nhược điểm,
      lựa chọn giải pháp tốt nhất, phê bình, biện hộ
    • Sáng tạo (Create): thiết kế, đề xuất giải pháp mới,
      xây dựng mô hình, tích hợp kiến thức để tạo cái mới"""
}

# Bộ luật ra đề gửi cho AI. Nội dung này KHÔNG đổi giữa các lần gọi, nhờ vậy nó
# nằm yên trong cache của nhà cung cấp và gần như không tốn phí từ lần thứ hai.
#
# Năm quy tắc dưới đây là kết quả của việc quan sát AI làm sai: nó bịa kiến thức
# ngoài tài liệu, hỏi đi hỏi lại một ý, và dồn đáp án đúng vào phương án A.
# Nhưng prompt chỉ là lời nhắc, KHÔNG phải bảo đảm — AI vẫn vi phạm đều đặn, nên
# giáo viên luôn phải tự xem lại trước khi lưu. Cửa chặn tự động duy nhất nằm ở
# routes/ngan_hang.py, lúc đẩy câu vào ngân hàng chung.
_SYSTEM_PROMPT = (
    "Bạn là một hệ thống trích xuất dữ liệu và tạo câu hỏi trắc nghiệm tự động.\n"
    "Trả lời TRỰC TIẾP bằng JSON, không thêm giải thích hay văn bản ngoài JSON.\n"
    "=== 5 QUY TẮC QUAN TRỌNG ===\n"
    "1. CHỐNG BỊA ĐẶT: Chỉ sử dụng thông tin trong tài liệu gốc.\n"
    "2. CHỐNG LẶP LẠI: Mỗi câu hỏi phải khai thác kiến thức KHÁC NHAU.\n"
    "3. NGẮN GỌN: Câu hỏi và đáp án phải súc tích.\n"
    "4. ĐÁP ÁN PHẢI CHÍNH XÁC: Phương án đúng phải đúng kiến thức và bám sát tài liệu, "
    "gán đúng chữ cái. TUYỆT ĐỐI không luôn chọn 'A'. 3 phương án còn lại sai nhưng hợp lý.\n"
    "5. MAPPING CHUẨN: Đặt phương án đúng vào đúng vị trí và gán CHÍNH XÁC chữ cái "
    '"A", "B", "C", hoặc "D" vào trường "dap_an_dung".'
)

# ---------- Prompt admin tự chỉnh được ----------
# Admin sửa prompt qua giao diện, bản sửa ghi ra file JSON. KHÔNG ghi đè mã
# nguồn và KHÔNG vào cơ sở dữ liệu: xóa file đi là mọi thứ trở về mặc định,
# nên prompt hỏng không bao giờ là sự cố không cứu được.
def _prompt_file():
    os.makedirs(current_app.instance_path, exist_ok=True)
    return os.path.join(current_app.instance_path, 'ai_prompt.json')


def _lay_system_prompt():
    """Prompt đang có hiệu lực: bản admin chỉnh nếu có, không thì bản mặc định."""
    try:
        if os.path.isfile(_prompt_file()):
            with open(_prompt_file(), encoding='utf-8') as f:
                txt = ((json.load(f) or {}).get('system_prompt') or '').strip()
            if txt:
                return txt
    except (OSError, ValueError):
        pass
    return _SYSTEM_PROMPT


def _luu_system_prompt(text):
    """Lưu system prompt admin chỉnh. Trả (True, None) hoặc (False, lý_do_lỗi)."""
    text = (text or '').strip()
    if not text:
        return False, 'Nội dung prompt không được để trống.'
    if len(text) > 20000:
        return False, 'Prompt quá dài (tối đa 20.000 ký tự).'
    try:
        with open(_prompt_file(), 'w', encoding='utf-8') as f:
            json.dump({'system_prompt': text}, f, ensure_ascii=False, indent=2)
        return True, None
    except OSError as e:
        return False, f'Không ghi được file cấu hình: {e}'


def _khoi_phuc_system_prompt():
    """Xóa file cấu hình -> quay về prompt mặc định trong code."""
    try:
        if os.path.isfile(_prompt_file()):
            os.remove(_prompt_file())
    except OSError:
        pass


def _prompt_dang_tuy_chinh():
    """True nếu đang dùng prompt admin chỉnh (có file), False nếu dùng mặc định."""
    return os.path.isfile(_prompt_file())


def call_gemini(parts):
    """Cửa vào duy nhất để gọi AI. Tự chuyển sang nhà cung cấp khác khi lỗi.

    Tên hàm giữ lại từ hồi chỉ có Gemini; giờ nó điều phối cả ba đường.

    Nhận `parts` dạng {'pdf': ..., 'requirements': ...} — tách đôi như vậy để
    Claude chính hãng cache riêng được khối PDF. Gemini và proxy không có
    caching nên chỉ cần nối thành một chuỗi phẳng.
    """
    if CLAUDE_OFFICIAL_KEY:
        try:
            return _call_claude_official(parts)
        except Exception as e:
            print("[AI] Claude chính hãng lỗi, thử Gemini:", e)

    flat = _lay_system_prompt() + '\n' + parts.get('pdf', '') + '\n' + parts.get('requirements', '')

    if API_KEY:
        try:
            return _call_gemini_rest(flat)
        except Exception as e:
            print("[AI] Gemini lỗi, thử proxy Claude dự phòng:", e)

    if CLAUDE_PROXY_KEY:
        return _call_claude_proxy(flat)
    raise RuntimeError(
        "Chưa cấu hình CLAUDE_OFFICIAL_KEY, GEMINI_API_KEY, hoặc CLAUDE_PROXY_KEY trong .env"
    )

def _call_gemini_rest(prompt):
    """Gọi Gemini, thử lần lượt từng model trong GEMINI_MODELS cho tới khi được."""
    if not API_KEY:
        raise RuntimeError("Chưa cấu hình GEMINI_API_KEY")
    headers = {'Content-Type': 'application/json'}
    params  = {'key': API_KEY}
    body    = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            # Sát 0: đây là việc trích xuất kiến thức từ tài liệu, không phải
            # việc sáng tác. Model càng "sáng tạo" thì càng bịa ra thứ không có.
            "temperature"    : 0.1,

            # Nới rất rộng vì Gemini 2.5 là model biết "suy nghĩ", và token nó
            # dùng để suy nghĩ ĐƯỢC TÍNH VÀO hạn mức này. Để 8192 thì với tài
            # liệu dài, phần suy nghĩ ngốn gần hết ngân sách, JSON câu hỏi bị
            # cắt cụt còn một hai câu (finishReason = MAX_TOKENS) — triệu chứng
            # là "xin 30 câu mà chỉ nhận được 2 câu".
            "maxOutputTokens": 32768,

            "responseMimeType": "application/json",

            # Tắt hẳn phần suy nghĩ: việc này không cần lập luận dài, tắt đi thì
            # toàn bộ token dồn cho câu hỏi và trả về nhanh hơn. Model nào không
            # hiểu trường này sẽ trả 400, và vòng lặp dưới tự chuyển model khác.
            "thinkingConfig" : {"thinkingBudget": 0},
        }
    }

    last_err = None
    # Hai vòng lồng nhau: mỗi model thử lại tối đa 2 lần, hết thì sang model kế
    # tiếp. Chờ giãn dần giữa các lần (1.5s rồi 3s) để không dội thêm vào một
    # dịch vụ vốn đang quá tải.
    for model in GEMINI_MODELS:
        for attempt in range(2):
            try:
                resp = requests.post(
                    _gemini_url(model),
                    headers=headers, params=params, json=body, timeout=120
                )
                # 429/500/503 là lỗi TẠM THỜI (quá tải, hết hạn mức tức thời) —
                # chờ rồi thử lại. Các mã lỗi khác là lỗi thật, không đáng thử lại.
                if resp.status_code in (429, 500, 503):
                    last_err = "%s %s (model %s)" % (resp.status_code, resp.reason, model)
                    time.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                cand = (data.get('candidates') or [{}])[0]
                # In cảnh báo khi phản hồi bị cắt vì hết token. Đây là manh mối
                # số một khi người dùng báo "sao tạo ra ít câu quá".
                if cand.get('finishReason') == 'MAX_TOKENS':
                    print("[Gemini] CANH BAO: phan hoi bi cat do MAX_TOKENS (model %s) "
                          "-> tang maxOutputTokens hoac giam so cau yeu cau" % model)
                try:
                    return cand['content']['parts'][0]['text']
                except (KeyError, IndexError, TypeError):
                    # Trả 200 nhưng nội dung rỗng/sai cấu trúc: thử lại cũng vô
                    # ích, bỏ luôn model này.
                    last_err = "phản hồi không hợp lệ từ model %s" % model
                    break
            except requests.exceptions.RequestException as e:
                last_err = "%s (model %s)" % (e, model)
                time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(
        "AI đang quá tải, vui lòng thử lại sau ít phút. (Chi tiết: %s)" % last_err
    )


def _luu_pdf_an_toan(file):
    """Nhận file PDF tải lên, kiểm tra rồi lưu. (filepath, None) hoặc (None, lỗi).

    Hai lớp bảo vệ, cả hai đều cần:
      - Đọc 4 byte đầu tìm chữ ký '%PDF'. Đuôi .pdf không chứng minh được gì:
        đổi tên virus.exe thành tailieu.pdf mất đúng một giây.
      - Đặt lại tên file bằng chuỗi ngẫu nhiên. Giữ tên gốc thì hai người tải
        cùng tên "baigiang.pdf" sẽ ghi đè lên nhau, và tên chứa "../" còn có thể
        ghi ra ngoài thư mục uploads.
    """
    if not file or not file.filename:
        return None, 'Vui lòng chọn file PDF!'
    if not file.filename.lower().endswith('.pdf'):
        return None, 'Chỉ chấp nhận file định dạng .pdf!'
    head = file.stream.read(5)
    file.stream.seek(0)
    if head[:4] != b'%PDF':
        return None, 'File không phải PDF hợp lệ!'

    safe = secure_filename(file.filename) or 'tailieu.pdf'
    unique = f"{uuid.uuid4().hex[:8]}_{safe}"
    filepath = os.path.join(UPLOAD_FOLDER, unique)
    file.save(filepath)
    return filepath, None


# ==========================================
# PHẦN 3: TRANG CHỦ
#
# Một route, hai trang hoàn toàn khác nhau tùy vai trò:
#   Học sinh — phòng đang mở để vào thi, và lịch sử bài đã làm.
#   Giáo viên — thư viện đề, phòng của mình, phòng toàn trường, số liệu nhanh.
# ==========================================
_THU_VN = ['Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy', 'Chủ Nhật']


def _today_str():
    """Ngày tháng tiếng Việt cho lời chào, ví dụ 'Thứ Tư, 25/06/2026'."""
    now = datetime.now()
    return f"{_THU_VN[now.weekday()]}, {now.strftime('%d/%m/%Y')}"


@exam_bp.route('/')
@login_required
def index():
    """Trang chủ. Rẽ hai nhánh hoàn toàn tách biệt theo vai trò người dùng."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    user_role = session.get('role', 'hoc_sinh')

    # ----------------------------------------------------
    # NHÁNH HỌC SINH
    # ----------------------------------------------------
    if user_role == 'hoc_sinh':
        cursor.execute("""
            SELECT p.id, p.ten_phong, p.ma_phong, p.trang_thai,
                   (p.mat_khau IS NOT NULL AND p.mat_khau <> '') AS co_mat_khau,
                   p.thoi_gian_mo_phong, p.thoi_gian_bat_dau, p.thoi_luong_lam_bai,
                   m.ten_hoc_phan AS ten_mon, u.ho_ten AS ten_gv
            FROM phong_thi p
            JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
            JOIN hoc_phan m ON d.hoc_phan_id = m.id
            JOIN nguoi_dung u ON p.user_id = u.id
            WHERE p.trang_thai IN ('chuan_bi', 'dang_thi', 'open', 'Đang mở')
              AND (p.thoi_gian_mo_phong IS NULL OR p.thoi_gian_mo_phong <= NOW())
              AND (p.thoi_gian_ket_thuc IS NULL OR p.thoi_gian_ket_thuc > NOW())
            ORDER BY p.thoi_gian_mo_phong DESC
        """)
        phong_dang_mo = cursor.fetchall()
        
        for p in phong_dang_mo:
            if p.get('thoi_gian_mo_phong'):
                try: p['thoi_gian_mo_phong_str'] = p['thoi_gian_mo_phong'].strftime('%H:%M - %d/%m/%Y')
                except: p['thoi_gian_mo_phong_str'] = str(p['thoi_gian_mo_phong'])
            else: p['thoi_gian_mo_phong_str'] = "Chưa thiết lập"
                
            if p.get('thoi_gian_bat_dau'):
                try: p['thoi_gian_bat_dau_str'] = p['thoi_gian_bat_dau'].strftime('%H:%M - %d/%m/%Y')
                except: p['thoi_gian_bat_dau_str'] = str(p['thoi_gian_bat_dau'])
            else: p['thoi_gian_bat_dau_str'] = "Chưa thiết lập"

        # Lịch sử bài đã thi. Tìm theo MSSV hoặc email, KHÔNG theo user_id —
        # bảng thi_sinh không giữ user_id, vì thí sinh vốn không cần tài khoản.
        # Cách khớp này phải trùng khớp với lúc ghi danh trong thi_sinh.py.
        dinh_danh = (session.get('ma_so_sv') or session.get('username') or '').strip()
        email_hs  = (session.get('email') or '').strip()
        cursor.execute("""
            SELECT ts.id, ts.diem, ts.so_cau_dung, ts.tong_so_cau,
                   ts.thoi_gian_nop, ts.lan_thi,
                   p.ten_phong, p.trang_thai AS phong_trang_thai,
                   p.thoi_gian_ket_thuc, d.ten_de_thi, m.ten_hoc_phan AS ten_mon,
                   u.ho_ten AS ten_gv
            FROM thi_sinh ts
            JOIN phong_thi p ON ts.phong_thi_id = p.id
            JOIN de_thi d    ON d.id = COALESCE(ts.de_thi_id, (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id))
            JOIN hoc_phan m  ON d.hoc_phan_id = m.id
            JOIN nguoi_dung u     ON p.user_id = u.id
            WHERE ts.da_nop_bai = 1
              AND ( (%s <> '' AND ts.ma_so_sv = %s)
                 OR (%s <> '' AND ts.email = %s) )
            ORDER BY ts.thoi_gian_nop DESC
        """, (dinh_danh, dinh_danh, email_hs, email_hs))
        lich_su_thi = cursor.fetchall()

        now = datetime.now()
        for b in lich_su_thi:
            # Cờ này quyết định nút "Xem lại bài" có hiện hay không. Phòng chưa
            # kết thúc mà cho xem là mở đường lộ đáp án cho ca thi sau.
            b['da_ket_thuc'] = bool(
                b['phong_trang_thai'] == 'da_dong'
                or (b['thoi_gian_ket_thuc'] and now > b['thoi_gian_ket_thuc'])
            )
            b['nop_str'] = (b['thoi_gian_nop'].strftime('%H:%M %d/%m/%Y')
                            if b['thoi_gian_nop'] else '—')
            b['meta'] = _tach_meta_de(b.get('ten_de_thi'))

        # Chọn tab ngay ở server. Để JavaScript chọn sau khi trang tải xong thì
        # người dùng thấy tab Trang chủ lóe lên rồi mới nhảy sang tab đúng.
        active_tab = request.args.get('tab', 'home')
        if active_tab not in ('home', 'student-room', 'student-history'):
            active_tab = 'home'

        _diem = [b['diem'] for b in lich_su_thi if b.get('diem') is not None]
        dash_diem_tb = round(sum(_diem) / len(_diem), 1) if _diem else None

        cursor.close()
        conn.close()
        return render_template('index.html', phong_dang_mo=phong_dang_mo,
                               lich_su_thi=lich_su_thi, user_role=user_role,
                               active_tab=active_tab, today_str=_today_str(),
                               dash_so_phong=len(phong_dang_mo),
                               dash_so_bai=len(lich_su_thi),
                               dash_diem_tb=dash_diem_tb)

    # ----------------------------------------------------
    # NHÁNH GIÁO VIÊN / ADMIN
    # ----------------------------------------------------
    else:
        # Thư viện cá nhân chứa hai thứ khác hẳn nhau, phân biệt bằng cột `loai`:
        #   'cau_hoi' — bộ câu hỏi thô do AI sinh từ PDF, chưa phải đề.
        #   'de_thi'  — đề hoàn chỉnh, có cấu trúc và thời lượng, mở phòng thi được.
        cursor.execute("""
            SELECT d.id, d.ten_de_thi, d.tong_so_cau, d.tong_diem,
                   d.ngay_tao, d.loai, d.nguon, m.ten_hoc_phan AS ten_mon
            FROM de_thi d
            JOIN hoc_phan m ON d.hoc_phan_id = m.id
            WHERE d.user_id = %s ORDER BY d.ngay_tao DESC
        """, (session['user_id'],))
        tat_ca = cursor.fetchall()
        exams      = [e for e in tat_ca if e.get('loai') != 'de_thi']
        de_thi_list = [e for e in tat_ca if e.get('loai') == 'de_thi']
        for de in de_thi_list:
            de['meta'] = _tach_meta_de(de.get('ten_de_thi'))

        # Đếm số câu Dễ / Trung bình / Khó cho mỗi bộ câu hỏi. Gom trong MỘT
        # truy vấn GROUP BY rồi phân phát vào từng bộ, thay vì bắn một câu đếm
        # cho mỗi bộ (giáo viên có 30 bộ là thành 30 truy vấn cho một lần mở trang).
        for e in exams:
            e['dk_de'] = e['dk_tb'] = e['dk_kho'] = 0
        exam_ids = [e['id'] for e in exams]
        if exam_ids:
            placeholders = ','.join(['%s'] * len(exam_ids))
            cursor.execute(
                "SELECT de_thi_id, do_kho, COUNT(*) AS sl FROM cau_hoi "
                "WHERE de_thi_id IN (%s) GROUP BY de_thi_id, do_kho" % placeholders,
                tuple(exam_ids))
            _dk_index = {e['id']: e for e in exams}
            for r in cursor.fetchall():
                e = _dk_index.get(r['de_thi_id'])
                if not e:
                    continue
                # Cột do_kho là chuỗi tự do, dữ liệu cũ đủ kiểu: "Khó", "kho",
                # "TB", "Trung bình", "trung binh"... nên phải dò chứ không so
                # bằng. Không nhận ra thì xếp vào Trung bình.
                dk = (r['do_kho'] or '').strip().lower()
                sl = r['sl'] or 0
                if 'kh' in dk:
                    e['dk_kho'] += sl
                elif 'trung' in dk or 'tb' in dk or 'binh' in dk or 'bình' in dk:
                    e['dk_tb'] += sl
                elif dk.startswith('d') or 'dễ' in dk:
                    e['dk_de'] += sl
                else:
                    e['dk_tb'] += sl

        # ----- Phòng thi của tôi -----
        cursor.execute("""
            SELECT p.id, p.ten_phong, p.ma_phong, p.trang_thai,
                   (p.mat_khau IS NOT NULL AND p.mat_khau <> '') AS co_mat_khau,
                   p.thoi_gian_bat_dau, p.thoi_luong_lam_bai,
                   d.ten_de_thi, m.ten_hoc_phan AS ten_mon,
                   COUNT(ts.id) AS tong_thi_sinh,
                   SUM(CASE WHEN ts.da_nop_bai=1 THEN 1 ELSE 0 END) AS da_nop,
                   AVG(CASE WHEN ts.da_nop_bai=1 THEN ts.diem END)  AS diem_tb
            FROM phong_thi p
            JOIN de_thi d  ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
            JOIN hoc_phan m ON d.hoc_phan_id = m.id
            LEFT JOIN thi_sinh ts ON ts.phong_thi_id = p.id
            WHERE p.user_id = %s GROUP BY p.id ORDER BY p.ngay_tao DESC
        """, (session['user_id'],))
        phong_this = cursor.fetchall()

        for _p in phong_this:
            try:
                _p['meta'] = _tach_meta_de(_p.get('ten_de_thi'))
            except Exception:
                # Meta chỉ để hiển thị cho đẹp. Một tên đề lạ làm hàm tách vấp
                # thì cũng không đáng để cả danh sách phòng biến mất.
                _p['meta'] = {}

        # ----- Phòng thi toàn trường -----
        cursor.execute("""
            SELECT p.id, p.ten_phong, p.ma_phong, p.trang_thai,
                   (p.mat_khau IS NOT NULL AND p.mat_khau <> '') AS co_mat_khau,
                   p.thoi_gian_mo_phong, p.thoi_gian_bat_dau, p.thoi_luong_lam_bai,
                   m.ten_hoc_phan AS ten_mon, u.ho_ten AS ten_gv
            FROM phong_thi p
            JOIN de_thi d ON d.id = (SELECT MIN(de_thi_id) FROM phong_thi_de_thi WHERE phong_thi_id=p.id)
            JOIN hoc_phan m ON d.hoc_phan_id = m.id
            JOIN nguoi_dung u ON p.user_id = u.id
            WHERE p.trang_thai IN ('chuan_bi', 'dang_thi', 'open', 'Đang mở')
              AND (p.thoi_gian_mo_phong IS NULL OR p.thoi_gian_mo_phong <= NOW())
              AND (p.thoi_gian_ket_thuc IS NULL OR p.thoi_gian_ket_thuc > NOW())
            ORDER BY p.thoi_gian_mo_phong DESC
        """)
        phong_dang_mo_he_thong = cursor.fetchall()
        
        for p in phong_dang_mo_he_thong:
            if p.get('thoi_gian_mo_phong'):
                try: p['thoi_gian_mo_phong_str'] = p['thoi_gian_mo_phong'].strftime('%H:%M - %d/%m/%Y')
                except: p['thoi_gian_mo_phong_str'] = str(p['thoi_gian_mo_phong'])
            else: p['thoi_gian_mo_phong_str'] = "Chưa thiết lập"
                
            if p.get('thoi_gian_bat_dau'):
                try: p['thoi_gian_bat_dau_str'] = p['thoi_gian_bat_dau'].strftime('%H:%M - %d/%m/%Y')
                except: p['thoi_gian_bat_dau_str'] = str(p['thoi_gian_bat_dau'])
            else: p['thoi_gian_bat_dau_str'] = "Chưa thiết lập"

        cursor.execute("SELECT COUNT(*) AS c FROM lop WHERE user_id=%s", (session['user_id'],))
        so_lop = (cursor.fetchone() or {}).get('c', 0)

        # Các ô số liệu trên dashboard. Tính ở server từ dữ liệu đã lấy sẵn, để
        # template chỉ việc in ra chứ không phải lặp và đếm trong Jinja.
        dash_so_de    = len(de_thi_list)
        dash_so_cau   = sum((e.get('tong_so_cau') or 0) for e in exams)
        dash_so_phong = sum(1 for p in phong_this if p.get('trang_thai') != 'da_dong')
        phong_gan_day = phong_this[:5]

        active_tab = request.args.get('tab', 'home')
        if active_tab not in ('home', 'exam', 'library', 'room'):
            active_tab = 'home'

        cursor.close()
        conn.close()

        # Số lượt AI đã dùng hôm nay, để hiện thanh "còn 2/3 lượt".
        from datetime import date as _date
        conn2   = get_db_connection()
        cursor2 = conn2.cursor(dictionary=True)
        cursor2.execute(
            "SELECT so_lan FROM api_usage WHERE user_id=%s AND ngay=%s",
            (session['user_id'], _date.today())
        )
        row_usage = cursor2.fetchone()
        cursor2.close()
        conn2.close()
        so_luot_da_dung = row_usage['so_lan'] if row_usage else 0

        tai_lieu = _liet_ke_tai_lieu(session['user_id'])

        return render_template('index.html', exams=exams, de_thi_list=de_thi_list,
                               phong_this=phong_this,
                               phong_dang_mo_he_thong=phong_dang_mo_he_thong,
                               user_role=user_role, active_tab=active_tab,
                               so_luot_da_dung=so_luot_da_dung,
                               tai_lieu=tai_lieu,
                               gioi_han_luot=_GIOI_HAN_LAN_NGAY,
                               # Đẩy giới hạn xuống giao diện thay vì để JavaScript
                               # tự ghi số. Hai nơi giữ hai con số riêng thì sớm
                               # muộn cũng lệch nhau, và người dùng nhận thông báo
                               # "vượt giới hạn" cho một con số mà giao diện vừa
                               # bảo là hợp lệ.
                               gioi_han_cau=_GIOI_HAN_CAU_LAN,
                               today_str=_today_str(),
                               dash_so_de=dash_so_de, dash_so_cau=dash_so_cau,
                               dash_so_phong=dash_so_phong, dash_so_lop=so_lop,
                               phong_gan_day=phong_gan_day)


# ==========================================
# PHẦN 4: THƯ VIỆN — xuất Excel, kho tài liệu, tạo/sửa/xóa đề
# ==========================================
@exam_bp.route('/thu_vien/de/<int:de_thi_id>/excel')
@giao_vien_required
def thu_vien_de_excel(de_thi_id):
    """Xuất câu hỏi của một bộ/một đề ra Excel.

    Câu SELECT lọc kèm `user_id`, nên đổi số trên URL cũng không moi được đề
    của giáo viên khác.
    """
    from routes.excel_utils import xuat_xlsx
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT d.ten_de_thi, m.ten_hoc_phan AS ten_mon
        FROM de_thi d JOIN hoc_phan m ON d.hoc_phan_id = m.id
        WHERE d.id = %s AND d.user_id = %s
    """, (de_thi_id, session['user_id']))
    de = cursor.fetchone()
    if not de:
        cursor.close(); conn.close()
        flash('Không tìm thấy hoặc không có quyền tải!', 'danger')
        return redirect('/?tab=library')
    cursor.execute("""
        SELECT chuong, do_kho, bloom, noi_dung,
               cau_a, cau_b, cau_c, cau_d, dap_an_dung, giai_thich
        FROM cau_hoi WHERE de_thi_id = %s ORDER BY id
    """, (de_thi_id,))
    data = cursor.fetchall()
    cursor.close(); conn.close()
    rows = [[i, r['chuong'] or '', r['do_kho'] or '', r['bloom'] or '', r['noi_dung'],
             r['cau_a'], r['cau_b'], r['cau_c'], r['cau_d'],
             (r['dap_an_dung'] or '').upper(), r['giai_thich'] or '']
            for i, r in enumerate(data, 1)]
    return xuat_xlsx(
        'Cau hoi', de['ten_de_thi'] or 'Câu hỏi',
        [f"Môn: {de['ten_mon']} | Tổng số câu: {len(rows)}"],
        ['STT', 'Chương', 'Độ khó', 'Bloom', 'Nội dung',
         'A', 'B', 'C', 'D', 'Đáp án', 'Giải thích'],
        rows, [6, 9, 12, 12, 55, 22, 22, 22, 22, 8, 40],
        f'de_{de_thi_id}.xlsx')


# ---------- Kho tài liệu PDF ----------
# Cả ba route dưới đây đều nhận tên file từ URL, nên đều phải phòng cùng một
# đòn: người dùng gửi lên "../../../etc/passwd" hoặc đường dẫn tới thư mục của
# giáo viên khác. Cách chặn: lấy basename, cho qua secure_filename, rồi mới ghép
# vào thư mục riêng của chính người đang đăng nhập.
@exam_bp.route('/tai_lieu/<path:fname>')
@giao_vien_required
def tai_lieu_tai_xuong(fname):
    """Tải một file trong kho tài liệu của chính mình về máy."""
    ten     = secure_filename(os.path.basename(fname))
    thu_muc = os.path.join(TAILIEU_FOLDER, str(session['user_id']))
    if not ten or not os.path.isfile(os.path.join(thu_muc, ten)):
        abort(404)
    # Tên file khi tải về (bỏ tiền tố thời gian cho gọn)
    dau = ten.split('_', 1)[0]
    ten_tai = ten.split('_', 1)[1] if ('_' in ten and dau.isdigit()) else ten
    return send_from_directory(thu_muc, ten, as_attachment=True,
                               download_name=ten_tai)


@exam_bp.route('/tai_lieu/xoa/<path:fname>', methods=['POST'])
@giao_vien_required
def tai_lieu_xoa(fname):
    """Xóa 1 file PDF (kèm file cache chương .json) khỏi kho của chính user."""
    ten       = secure_filename(os.path.basename(fname))
    duong_dan = os.path.join(TAILIEU_FOLDER, str(session['user_id']), ten)
    try:
        if ten and os.path.isfile(duong_dan):
            os.remove(duong_dan)
            # Xóa nốt file cache chương đi kèm, không thì nó nằm lại làm rác.
            try:
                if os.path.isfile(duong_dan + '.json'):
                    os.remove(duong_dan + '.json')
            except OSError:
                pass
            return jsonify({'success': True})
    except OSError:
        pass
    return jsonify({'success': False, 'message': 'Không xóa được tài liệu.'}), 400


@exam_bp.route('/tai_lieu/dung_lai/<path:fname>')
@giao_vien_required
def tai_lieu_dung_lai(fname):
    """Dùng lại một tài liệu có sẵn trong kho, khỏi phải tải lên lần nữa.

    Ghi đường dẫn file vào session để /upload biết dùng file nào, rồi trả về
    danh sách chương để giao diện nhảy thẳng sang bước chọn chương.

    Chương lấy từ file cache .json nếu có. Không có thì dò một lần rồi ghi cache
    — dò chương phải đọc và phân tích cả quyển PDF, đợi mỗi lần thì rất lâu.
    """
    ten       = secure_filename(os.path.basename(fname))
    thu_muc   = os.path.join(TAILIEU_FOLDER, str(session['user_id']))
    duong_dan = os.path.join(thu_muc, ten)
    if not ten or not os.path.isfile(duong_dan):
        return jsonify({'error': 'Không tìm thấy tài liệu.'}), 404

    session['pdf_filepath'] = duong_dan
    session['pdf_filename'] = ten

    chapters = None
    side = duong_dan + '.json'
    if os.path.isfile(side):
        try:
            with open(side, encoding='utf-8') as f:
                chapters = (json.load(f) or {}).get('chapters')
        except (OSError, ValueError):
            chapters = None
    if not chapters:
        try:
            chapters = _phat_hien_chuong_pdf(duong_dan)
            with open(side, 'w', encoding='utf-8') as f:
                json.dump({'chapters': chapters}, f, ensure_ascii=False)
        except Exception:
            chapters = []

    dau      = ten.split('_', 1)[0]
    ten_hien = ten.split('_', 1)[1] if ('_' in ten and dau.isdigit()) else ten
    return jsonify({'chapters': chapters, 'total': len(chapters), 'ten': ten_hien})


@exam_bp.route('/detect_chapters', methods=['POST'])
@giao_vien_required
def detect_chapters():
    """Bước 1 của luồng tạo câu hỏi: xem nguồn nội dung có chia chương không.

    Văn bản DÁN luôn coi là một khối, không tách chương — người ta dán một đoạn
    ngắn, tách chương ở đó là vô nghĩa.

    File PDF thì đem đi dò. Dò ra chương thì giao diện cho chọn chương nào ra
    bao nhiêu câu; không ra chương nào (đề cương, slide rời) thì cũng coi là một
    khối như văn bản dán.
    """
    noi_dung_text = (request.form.get('noi_dung_text') or '').strip()

    # ===== Nguồn = văn bản DÁN: 1 khối thống nhất, bỏ qua dò chương =====
    if noi_dung_text:
        # Không lưu file -> xoá nguồn PDF cũ trong session (để /upload dùng đúng nguồn).
        session.pop('pdf_filepath', None)
        session.pop('pdf_filename', None)
        return jsonify({'chapters': [], 'total': 0, 'single_block': True})

    # ===== Nguồn = file PDF: vẫn dò chương =====
    if 'pdf_file' not in request.files:
        return jsonify({'error': 'Chưa có file PDF hoặc nội dung dán'}), 400
    filepath, loi = _luu_pdf_an_toan(request.files['pdf_file'])
    if loi:
        return jsonify({'error': loi}), 400
    session['pdf_filepath'] = filepath
    session['pdf_filename'] = os.path.basename(filepath)
    try:
        source_text = _doc_text_pdf(filepath)
    except Exception:
        current_app.logger.exception('Lỗi khi đọc PDF')
        return jsonify({'error': 'Không đọc được file PDF. Vui lòng thử file khác.'}), 500

    if not source_text or not source_text.strip():
        return jsonify({'error': 'Không trích xuất được nội dung. Hãy thử nguồn khác.'}), 400

    try:
        dict_ch  = _xay_dict_chuong(source_text)
        chapters = sorted(int(k) for k in dict_ch if k.isdigit())
    except Exception:
        current_app.logger.exception('Lỗi khi dò chương')
        chapters = []

    if chapters:
        return jsonify({'chapters': chapters, 'total': len(chapters), 'single_block': False})
    # PDF không có chương -> 1 khối "Toàn bộ nội dung"
    return jsonify({'chapters': [], 'total': 0, 'single_block': True})


# ==========================================
# SINH CÂU HỎI — hàm quan trọng nhất file
#
# Các bước, đều nằm gọn trong hàm dưới đây:
#   1. Xác định nguồn (văn bản dán hay PDF), kiểm tra hạn mức gọi AI.
#   2. Cắt lấy nội dung đúng những chương giáo viên đã chọn.
#   3. Dựng prompt: mỗi chương bao nhiêu câu, độ khó nào, mức Bloom nào.
#   4. Gọi AI (call_gemini tự lo việc đổi nhà cung cấp khi lỗi).
#   5. Chuẩn hóa đáp án đúng về một chữ cái A/B/C/D.
#   6. Trả về trang result.html cho giáo viên xem và sửa.
#
# AI hay trả về ÍT CÂU HƠN yêu cầu (tài liệu ngắn, hoặc bị cắt vì hết token).
# Hệ thống KHÔNG tự xin bù — chỉ báo cho giáo viên biết nhận được bao nhiêu câu,
# rồi để họ quyết định có tạo thêm lượt nữa hay không.
# ==========================================
@exam_bp.route('/upload', methods=['POST'])
@giao_vien_required
def upload_file():
    """Sinh câu hỏi từ PDF hoặc văn bản dán, rồi trả về danh sách đã kiểm duyệt."""
    noi_dung_text = (request.form.get('noi_dung_text') or '').strip()

    # Văn bản dán được ưu tiên hơn file PDF. Có cả hai thì lấy văn bản, vì đó là
    # thứ người dùng vừa gõ vào, còn PDF có thể chỉ là file cũ còn sót trong session.
    if noi_dung_text:
        filepath = None
        filename = 'noi_dung_dan'
        extracted_text = noi_dung_text[:_GIOI_HAN_KY_TU]
    else:
        filepath = session.get('pdf_filepath')
        filename = session.get('pdf_filename')
        if not filepath or not os.path.exists(filepath):
            if 'pdf_file' not in request.files:
                return "Lỗi: Không có nguồn nội dung!", 400
            filepath, loi = _luu_pdf_an_toan(request.files['pdf_file'])
            if loi:
                return loi, 400
            filename = os.path.basename(filepath)
        extracted_text = None

    # Admin không bị trừ lượt — cần chạy thử và hỗ trợ người dùng.
    if session.get('role') != 'admin':
        ok, so_da_dung, msg_loi = _kiem_tra_gioi_han(session['user_id'])
        if not ok:
            flash(msg_loi, 'warning')
            return redirect(url_for('exam.index'))

    try:
        if extracted_text is None:
            extracted_text = _doc_text_pdf(filepath)
        dict_chapters = _xay_dict_chuong(extracted_text)

        list_chuong_so = request.form.getlist('chuong_so[]')
        list_de        = request.form.getlist('de[]')
        list_tb        = request.form.getlist('tb[]')
        list_kho       = request.form.getlist('kho[]')

        noi_dung_nguon   = ""
        yeu_cau_chi_tiet = ""
        tong_so_cau      = 0
        bloom_phan_bo    = ""

        for i, c_num in enumerate(list_chuong_so):
            c_num  = c_num.strip()
            so_de  = int(list_de[i]  or 0)
            so_tb  = int(list_tb[i]  or 0)
            so_kho = int(list_kho[i] or 0)
            if so_de + so_tb + so_kho == 0:
                continue
            if c_num == '0':
                # Chương "0" là khối Toàn bộ nội dung. Vẫn đi qua
                # _noi_dung_bai_giang chứ không lấy văn bản thô: hàm đó cắt bỏ
                # bìa, lời nói đầu, mục lục, tài liệu tham khảo — những phần AI
                # rất thích lôi ra hỏi mà chẳng kiểm tra kiến thức gì.
                nhan = "Toàn bộ nội dung"
                noi_dung_nguon += (
                    "\n--- TOÀN BỘ NỘI DUNG ---\n"
                    f"{_noi_dung_bai_giang(extracted_text, dict_chapters)}\n"
                )
            else:
                nhan = f"Chương {c_num}"
                if c_num in dict_chapters:
                    noi_dung_nguon += (
                        f"\n--- CHƯƠNG {c_num} ---\n"
                        f"{dict_chapters[c_num]}\n"
                    )
            yeu_cau_chi_tiet += (
                f"- {nhan}: "
                f"{so_de} câu Dễ, "
                f"{so_tb} câu Trung bình, "
                f"{so_kho} câu Khó\n"
            )
            if so_de > 0:
                bloom_phan_bo += f"  + {so_de} câu {nhan} → Bloom cấp 1-2 (Nhớ/Hiểu)\n"
            if so_tb > 0:
                bloom_phan_bo += f"  + {so_tb} câu {nhan} → Bloom cấp 3-4 (Vận dụng/Phân tích)\n"
            if so_kho > 0:
                bloom_phan_bo += f"  + {so_kho} câu {nhan} → Bloom cấp 5-6 (Đánh giá/Sáng tạo)\n"
            tong_so_cau += so_de + so_tb + so_kho

        if not noi_dung_nguon:
            return "Chưa chọn nội dung để tạo câu hỏi!", 400

        if session.get('role') != 'admin' and tong_so_cau > _GIOI_HAN_CAU_LAN:
            flash(
                f'Mỗi lần tạo tối đa {_GIOI_HAN_CAU_LAN} câu hỏi '
                f'(bạn yêu cầu {tong_so_cau} câu). '
                f'Hãy giảm số câu ở từng chương và thử lại.',
                'warning'
            )
            return redirect(url_for('exam.index'))

        do_phuc_tap = request.form.get('do_phuc_tap', 'ngan_gon')

        # Giải thích là BẮT BUỘC với mọi câu, không phải tùy chọn. Không có nó,
        # giáo viên chẳng có cách nào kiểm tra đáp án AI chọn là đúng hay sai —
        # mà AI chọn sai đáp án thì thường xuyên. Về sau, câu thiếu giải thích
        # cũng sẽ bị chặn không cho vào ngân hàng chung.
        them_giai_thich = (
            ',\n    "giai_thich": "1-2 câu nêu VÌ SAO đáp án đúng, căn cứ trực tiếp '
            'vào nội dung tài liệu ở trên"'
        )

        _van_phong = {
            'ngan_gon':   "Diễn đạt NGẮN GỌN, SÚC TÍCH: câu hỏi và đáp án đi thẳng vào trọng tâm, ít chữ.",
            'chuyen_sau': "Diễn đạt CHUYÊN SÂU, HỌC THUẬT: dùng thuật ngữ chính xác, đào sâu bản chất kiến thức.",
            'de_hieu':    "Diễn đạt DỄ HIỂU, GẦN GŨI: câu chữ đời thường, tránh từ ngữ rối rắm, người mới cũng đọc hiểu được.",
        }
        phong_cach_yc = _van_phong.get(do_phuc_tap, _van_phong['ngan_gon'])

        # Tách prompt làm hai khối: tài liệu (lớn, lặp lại) và yêu cầu (nhỏ, mỗi
        # lần một khác). Chia như vậy để Claude cache được khối tài liệu — xem
        # _call_claude_official ở trên.
        pdf_block = f"=== TÀI LIỆU THAM KHẢO GỐC ===\n{noi_dung_nguon}"

        req_block = f"""=== YÊU CẦU TẠO CÂU HỎI ===
- Tổng số lượng mục tiêu: {tong_so_cau} câu.
- LƯU Ý: Nếu tài liệu quá ngắn không đủ tạo {tong_so_cau} câu, hãy tạo SỐ LƯỢNG ÍT HƠN. Không bịa đặt, không lặp câu hỏi.
- Phân bổ:
{yeu_cau_chi_tiet}
{bloom_phan_bo}
- Văn phong: {phong_cach_yc}

=== CHỈ HỎI NỘI DUNG BÀI GIẢNG ===
- CHỈ được tạo câu hỏi từ kiến thức trong phần nội dung ở trên.
- TUYỆT ĐỐI KHÔNG hỏi về bản thân tài liệu: tên chương, tiêu đề mục, số thứ tự chương,
  bố cục, mục lục, lời nói đầu, lời giới thiệu, lời cảm ơn, nhóm biên soạn, tác giả,
  nhà xuất bản, tài liệu tham khảo, phụ lục.
  VÍ DỤ BỊ CẤM: "Chương 2 có tiêu đề là gì?", "Nhóm biên soạn mong nhận được điều gì?".
- Mỗi câu phải kiểm tra KIẾN THỨC THẬT và nêu rõ đối tượng được hỏi (không hỏi chung
  chung kiểu "theo tài liệu, đâu là phát biểu đúng?").

=== GIẢI THÍCH (BẮT BUỘC MỖI CÂU) ===
- Trường "giai_thich": 1-2 câu, nêu VÌ SAO đáp án đúng là đúng, CĂN CỨ TRỰC TIẾP vào
  nội dung tài liệu ở trên.
- KHÔNG dùng kiến thức ngoài tài liệu. KHÔNG giải thích vì sao các phương án khác sai.
  KHÔNG nhắc tới độ khó hay mức Bloom trong phần giải thích.
=== ĐỊNH DẠNG JSON ===
[
  {{"cau_hoi": "...", "cau_a": "...", "cau_b": "...", "cau_c": "...", "cau_d": "...", "dap_an_dung": "A", "do_kho": "...", "bloom": "...", "chuong": 1{them_giai_thich}}}
]"""

        text_response = call_gemini({'pdf': pdf_block, 'requirements': req_block})

        clean_json = (text_response.strip()
                      .removeprefix('```json')
                      .removeprefix('```')
                      .removesuffix('```')
                      .strip())
        danh_sach = _trich_xuat_json_list(clean_json)

        def _chuan_hoa_dap_an(q):
            """Ép đáp án đúng về đúng một chữ cái A/B/C/D.

            AI trả về trường này rất tùy hứng: "a", "B)", "C.", hoặc thậm chí
            chép nguyên văn nội dung phương án. Không nắn lại thì hệ thống hiểu
            sai và cả lớp bị chấm sai câu đó.
            """
            raw = str(q.get('dap_an_dung', '') or '').strip()
            up  = raw.upper()
            # Dạng chuẩn: chữ cái đứng đầu, sau nó không phải chữ nữa ("B", "B)", "B.").
            if up[:1] in ('A', 'B', 'C', 'D') and (len(up) == 1 or not up[1].isalpha()):
                return up[0]
            # AI chép nguyên nội dung phương án -> dò xem nó khớp phương án nào.
            for letter in ('A', 'B', 'C', 'D'):
                opt = str(q.get('cau_' + letter.lower(), '') or '').strip()
                if opt and opt.lower() == raw.lower():
                    return letter
            return up[0] if up[:1] in ('A', 'B', 'C', 'D') else 'A'

        for q in danh_sach:
            q['dap_an_dung'] = _chuan_hoa_dap_an(q)

        # Nói thẳng cho giáo viên biết nhận được bao nhiêu câu so với yêu cầu.
        # Im lặng trả về ít câu hơn thì họ tưởng hệ thống hỏng.
        so_cau_thuc_te = len(danh_sach)
        if so_cau_thuc_te < tong_so_cau:
            flash(f'⚠️ Tài liệu ngắn! Bạn yêu cầu {tong_so_cau} câu, nhưng AI chỉ trích xuất được {so_cau_thuc_te} câu chất lượng dựa trên tài liệu.', 'warning')
        else:
            flash(f'✅ Tạo thành công {so_cau_thuc_te} câu hỏi .', 'success')

        # Chỉ trừ lượt Ở ĐÂY — sau khi AI đã trả kết quả. Trừ sớm hơn thì mỗi
        # lần nhà cung cấp AI lỗi là người dùng mất oan một lượt.
        if session.get('role') != 'admin':
            _ghi_nhan_luot_dung(session['user_id'])
            ok, so_da_dung, _ = _kiem_tra_gioi_han(session['user_id'])
            con_lai = _GIOI_HAN_LAN_NGAY - so_da_dung
            if con_lai > 0:
                flash(f'Hôm nay bạn còn {con_lai} lượt tạo câu hỏi.', 'info')
            else:
                flash('Bạn đã dùng hết lượt tạo câu hỏi hôm nay. Giới hạn đặt lại lúc 00:00.', 'info')

        session.pop('pdf_filepath', None)
        session.pop('pdf_filename', None)

        # So đường dẫn tuyệt đối để biết file này đến từ đâu:
        #   đã nằm trong kho  -> giáo viên bấm "dùng lại", không đụng vào nữa.
        #   là file tạm mới up -> chép vào kho (kèm cache chương) rồi xóa bản tạm.
        _abs_fp  = os.path.abspath(filepath or '')
        _abs_kho = os.path.abspath(TAILIEU_FOLDER)
        if filepath and not _abs_fp.startswith(_abs_kho):
            _chuong_cache = sorted(int(k) for k in dict_chapters if k.isdigit())
            _luu_tai_lieu_vinh_vien(filepath, filename or os.path.basename(filepath),
                                    session['user_id'], _chuong_cache)
            try:
                if filepath and os.path.exists(filepath):
                    os.remove(filepath)
            except OSError:
                pass

        conn_mon   = get_db_connection()
        cursor_mon = conn_mon.cursor(dictionary=True)
        cursor_mon.execute("SELECT id, ten_hoc_phan AS ten_mon FROM hoc_phan ORDER BY ten_hoc_phan")
        mon_hocs = cursor_mon.fetchall()
        # Kèm mã học phần để giao diện tự điền mã khi chọn tên môn.
        try:
            cursor_mon.execute("""
                SELECT ma_hoc_phan, ten_hoc_phan FROM hoc_phan ORDER BY ten_hoc_phan
            """)
            hoc_phan = cursor_mon.fetchall()
        except Exception:
            hoc_phan = []
        cursor_mon.close()
        conn_mon.close()

        return render_template('result.html',
                               cau_hoi_list=danh_sach,
                               tong_so_cau=so_cau_thuc_te,
                               mon_hocs=mon_hocs,
                               hoc_phan=hoc_phan)

    except Exception:
        current_app.logger.exception('Lỗi khi tạo câu hỏi từ PDF')
        flash('Đã xảy ra lỗi khi xử lý tài liệu. Vui lòng thử lại với file PDF khác '
              'hoặc sau ít phút.', 'danger')
        return redirect(url_for('exam.index'))

# ==========================================
# LƯU ĐỀ THI
# ==========================================
@exam_bp.route('/save_exam', methods=['POST'])
@giao_vien_required
def save_exam():
    """Lưu bộ câu hỏi AI vừa sinh vào thư viện cá nhân (loai = 'cau_hoi').

    Học phần BẮT BUỘC phải khớp một môn có sẵn trong danh mục chuẩn. Cho tự gõ
    tên môn mới thì chỉ vài tháng là danh mục đầy những "Lập trình Web",
    "lap trinh web", "LTW" — và mọi thống kê theo môn thành vô nghĩa.
    """
    mon_id_selected = request.form.get('mon_hoc_id')
    ten_mon_input   = (request.form.get('ten_mon_hoc')
                       or request.form.get('ten_mon_moi')
                       or '').strip()

    ten_de  = request.form.get('ten_de_thi',  'Đề thi AI')
    tong_so = request.form.get('tong_so_cau', 0)
    count   = int(request.form.get('so_luong_thuc_te', 0))

    conn   = get_db_connection()
    cursor = conn.cursor()
    try:
        hoc_phan_id = None

        if mon_id_selected and mon_id_selected.isdigit():
            hoc_phan_id = int(mon_id_selected)
        else:
            if not ten_mon_input:
                flash('❌ Vui lòng chọn học phần!', 'danger')
                return redirect(url_for('exam.index'))

            # Tên gõ vào phải khớp một môn trong danh mục, nếu không thì từ chối.
            cursor.execute(
                "SELECT id FROM hoc_phan WHERE LOWER(ten_hoc_phan)=LOWER(%s) LIMIT 1",
                (ten_mon_input,)
            )
            row_hp = cursor.fetchone()
            if row_hp:
                hoc_phan_id = row_hp[0]
            else:
                flash('❌ Vui lòng chọn học phần đúng trong danh sách gợi ý '
                      '(không tự nhập tên môn mới).', 'warning')
                return redirect(url_for('exam.index'))

        cursor.execute(
            """INSERT INTO de_thi
               (hoc_phan_id, ten_de_thi, tong_so_cau, user_id, loai)
               VALUES (%s,%s,%s,%s,'cau_hoi')""",
            (hoc_phan_id, ten_de, tong_so, session['user_id'])
        )
        de_id = cursor.lastrowid

        # Nhớ lưu CẢ bloom lẫn giai_thich. Bỏ sót hai cột này thì công sức AI
        # sinh ra giải thích bị vứt đi, và về sau câu hỏi không đủ điều kiện để
        # đưa vào ngân hàng chung (ngân hàng bắt buộc phải có giải thích).
        sql = """INSERT INTO cau_hoi
                 (de_thi_id, chuong, do_kho, bloom, noi_dung,
                  cau_a, cau_b, cau_c, cau_d, dap_an_dung, giai_thich, nguon_tao)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'AI')"""

        # 4. LƯU TỪNG CÂU HỎI
        so_da_luu = 0
        for i in range(1, count + 1):
            # Giảng viên bấm "Loại bỏ" -> KHÔNG lưu câu này. Trước đây form vẫn gửi
            # cờ loai_bo lên nhưng server không đọc -> câu đã loại vẫn chui vào đề.
            if request.form.get(f'loai_bo_{i}', '0') == '1':
                continue

            noi_dung = (request.form.get(f'noi_dung_sync_{i}') or request.form.get(f'noi_dung_{i}', ''))
            cau_a    = (request.form.get(f'cau_a_sync_{i}') or request.form.get(f'cau_a_{i}', ''))
            cau_b    = (request.form.get(f'cau_b_sync_{i}') or request.form.get(f'cau_b_{i}', ''))
            cau_c    = (request.form.get(f'cau_c_sync_{i}') or request.form.get(f'cau_c_{i}', ''))
            cau_d    = (request.form.get(f'cau_d_sync_{i}') or request.form.get(f'cau_d_{i}', ''))
            dap_an   = (request.form.get(f'dap_an_sync_{i}') or request.form.get(f'dap_an_{i}', 'A'))
            chuong_raw = request.form.get(f'chuong_{i}', '').strip()
            so_ch = _chuong_so(chuong_raw) if chuong_raw else 0
            chuong = str(so_ch) if so_ch > 0 else chuong_raw
            do_kho     = request.form.get(f'do_kho_{i}', '')
            bloom      = (request.form.get(f'bloom_{i}', '') or '').strip()
            giai_thich = (request.form.get(f'giai_thich_{i}', '') or '').strip()

            cursor.execute(sql, (
                de_id, chuong, do_kho, bloom, noi_dung,
                cau_a, cau_b, cau_c, cau_d, dap_an, giai_thich
            ))
            so_da_luu += 1

        # Số câu THẬT SỰ lưu (đã trừ câu bị loại bỏ) mới là tổng số câu của đề.
        cursor.execute("UPDATE de_thi SET tong_so_cau=%s WHERE id=%s",
                       (so_da_luu, de_id))

        conn.commit()
        flash('✅ Đã lưu đề thi thành công!', 'success')
        return redirect(url_for('exam.index'))

    except Exception as e:
        conn.rollback()
        flash(f'❌ Lỗi lưu DB: {str(e)}', 'danger')
        return redirect(url_for('exam.index'))
    finally:
        cursor.close()
        conn.close()


# ==========================================
# XEM ĐỀ THI
# ==========================================
@exam_bp.route('/view_exam/<int:exam_id>')
@giao_vien_required
def view_exam(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT d.*, m.ten_hoc_phan AS ten_mon FROM de_thi d
        JOIN hoc_phan m ON d.hoc_phan_id = m.id
        WHERE d.id = %s
    """, (exam_id,))
    exam = cursor.fetchone()

    if not exam:
        flash('Không tìm thấy đề thi!', 'danger')
        return redirect('/')
    if (session.get('role') != 'admin' and
            exam['user_id'] != session['user_id']):
        flash('Bạn không có quyền!', 'danger')
        return redirect('/')

    cursor.execute(
        "SELECT * FROM cau_hoi WHERE de_thi_id=%s ORDER BY id",
        (exam_id,)
    )
    questions = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template(
        'view_exam.html', de_thi=exam, cau_hois=questions
    )


# ==========================================
# XUẤT ĐỀ RA FILE WORD / PDF
#
# Dựng lại bản in đúng mẫu đề thi của trường, lấy dữ liệu TỪ CƠ SỞ DỮ LIỆU chứ
# không phải từ màn hình tạo đề. Nhờ vậy giáo viên in lại được đề cũ bất cứ lúc
# nào, không cần dựng lại đề từ đầu.
#
# Toàn bộ HTML và CSS bản in nằm ngay dưới đây. Phần CSS phải giữ khớp với
# templates/tao_de_thi.html — hai nơi cùng in ra một mẫu, lệch nhau thì bản xem
# trước và bản tải về trông khác nhau.
# ==========================================
_TRUONG = 'TRƯỜNG ĐẠI HỌC KIÊN GIANG'
_DON_VI = 'KHOA THÔNG TIN TRUYỀN THÔNG'   # đơn vị quản lý học phần, in dưới tên trường
_TITLE_MAP = {
    'Cuối kỳ': 'ĐỀ THI KẾT THÚC HỌC PHẦN',
    'Giữa kỳ': 'ĐỀ THI GIỮA KỲ',
    'Kiểm tra thường xuyên': 'ĐỀ KIỂM TRA THƯỜNG XUYÊN',
}

_DE_PRINT_CSS = """
    @page{size:A4;margin:2cm;}
    body{font-family:'Times New Roman',serif;font-size:13pt;color:#000;max-width:740px;margin:0 auto;line-height:1.45;}
    .p-mau{text-align:right;font-style:italic;font-size:11pt;margin-bottom:4px;}
    .p-toptable{width:100%;border-collapse:collapse;border:none;margin-bottom:10px;}
    .p-toptable td{vertical-align:top;padding:2px 4px;border:none;}
    /* Tên trường: KHÔNG in đậm, cỡ chữ nhỏ hơn phần còn lại, và white-space:nowrap
       để luôn nằm gọn trên MỘT hàng. Trước đây in đậm 13pt nên "TRƯỜNG ĐẠI HỌC
       KIÊN GIANG" tràn ra hai dòng, đẩy lệch cả khối tiêu đề bên phải. */
    .p-org{font-weight:normal;text-transform:uppercase;text-align:left;
           font-size:11pt;white-space:nowrap;}
    .p-title{text-align:center;font-weight:bold;text-transform:uppercase;}
    /* Tên khoa in đậm theo mẫu đề của trường; nowrap để "KHOA THÔNG TIN TRUYỀN
       THÔNG" không bị bẻ xuống hai dòng làm lệch khối tiêu đề bên phải. */
    .p-donvi{text-align:left;font-size:11pt;font-weight:bold;white-space:nowrap;}
    .p-hk{text-align:center;}
    .p-made{text-align:left;font-weight:bold;margin:6px 0;}
    .p-hp{text-align:left;font-weight:bold;margin:2px 0;}
    .p-htt,.p-tg{text-align:center;font-weight:bold;margin:2px 0;}
    .p-ghichu{text-align:left;margin:2px 0;}
    .p-noidung{text-align:center;font-weight:bold;margin:14px 0 12px;}
    .pq{margin-bottom:12px;} .pq-q{font-weight:bold;margin-bottom:3px;}
    .pq-opts{padding-left:18px;}
    .pq-opts .optbl{width:100%;border-collapse:collapse;}
    .pq-opts .optbl td{vertical-align:top;padding:1px 10px 1px 0;}
    .pq-opts .optbl-4 td{width:25%;} .pq-opts .optbl-2 td{width:50%;}
    .pq-opt{margin:1px 0;}
    .p-foot{text-align:center;font-style:italic;margin-top:16px;}
    .p-het{text-align:center;font-weight:bold;}
    .p-sign{width:100%;border-collapse:collapse;margin-top:36px;}
    .p-sign td{width:50%;text-align:center;vertical-align:top;padding:2px 4px;}
    .p-sign b{font-weight:bold;} .p-sign span{font-style:italic;font-size:0.9em;}"""


def _tach_meta_de(ten_de_thi):
    """Tách thông tin hành chính được nhúng trong tên đề lúc tạo:
    'Tên (Hình thức · Học kỳ · Năm học · Mã môn XXX) - Mã đề A01'.
    Trả về dict {ten, ma_de, hinh_thuc, hoc_ky, nam_hoc, ma_hp}."""
    ten = (ten_de_thi or '').strip()
    m = re.search(r'Mã đề\s+([A-Za-z0-9]+)', ten)
    ma_de = m.group(1) if m else 'A01'
    base = re.sub(r'\s*-\s*Mã đề\s+[A-Za-z0-9]+\s*$', '', ten).strip()
    hinh_thuc = hoc_ky = nam_hoc = ma_hp = ''
    mm = re.search(r'\(([^()]*)\)\s*$', base)
    if mm:
        for part in (p.strip() for p in mm.group(1).split('·')):
            if not part:
                continue
            if part.startswith('Mã môn'):
                ma_hp = part[len('Mã môn'):].strip()
            elif re.match(r'^\d{4}\s*-\s*\d{4}$', part) or re.match(r'^\d{4}$', part):
                nam_hoc = part
            elif part in _TITLE_MAP:
                hinh_thuc = part
            else:
                hoc_ky = part
        base = base[:mm.start()].strip()
    return {'ten': base, 'ma_de': ma_de, 'hinh_thuc': hinh_thuc,
            'hoc_ky': hoc_ky, 'nam_hoc': nam_hoc, 'ma_hp': ma_hp}


def _opts_html_de(opts):
    """Xếp 4 đáp án theo độ dài (port của optsHtml ở wizard): ngắn -> 1 hàng 4 cột,
    vừa -> 2x2, dài -> mỗi đáp án 1 dòng."""
    import html as _html
    cell = lambda l, t: f"{l}. {_html.escape(str(t or ''))}"
    maxlen = max((len(str(t or '')) for _, t in opts), default=0)
    if maxlen <= 12:
        tds = ''.join(f"<td>{cell(l, t)}</td>" for l, t in opts)
        return f'<table class="optbl optbl-4"><tr>{tds}</tr></table>'
    if maxlen <= 40:
        return ('<table class="optbl optbl-2">'
                f'<tr><td>{cell(*opts[0])}</td><td>{cell(*opts[1])}</td></tr>'
                f'<tr><td>{cell(*opts[2])}</td><td>{cell(*opts[3])}</td></tr></table>')
    return ''.join(f'<div class="pq-opt">{cell(l, t)}</div>' for l, t in opts)


def _xay_de_html(de, questions, auto_print=False):
    """Dựng tài liệu HTML 1 mã đề theo MẪU 3.2 (dùng chung cho Word lẫn In/PDF)."""
    import html as _html
    esc = lambda s: _html.escape(str(s if s is not None else ''))
    info = _tach_meta_de(de.get('ten_de_thi'))
    title = _TITLE_MAP.get(info['hinh_thuc'], 'ĐỀ THI KẾT THÚC HỌC PHẦN')
    ten_hp = de.get('ten_mon') or info['ten'] or '………'
    hk_text = ('Học kỳ: ' + esc(info['hoc_ky']) if info['hoc_ky'] else 'Học kỳ: ………') \
        + ' &nbsp; Năm học: ' + (esc(info['nam_hoc']) if info['nam_hoc'] else '……… - ………')
    thoi_gian = de.get('thoi_gian') or '………'
    ma_hp = esc(info['ma_hp']) or '………'

    head = f'''
    <div class="p-mau">Mẫu 3.2</div>
    <table class="p-toptable">
        <tr><td style="width:42%;" class="p-org">{esc(_TRUONG)}</td>
            <td style="width:58%;" class="p-title">{esc(title)}</td></tr>
        <tr><td class="p-donvi">{esc(_DON_VI)}</td>
            <td class="p-hk">{hk_text}</td></tr>
    </table>
    <div class="p-made">Mã đề: {esc(info['ma_de'])}</div>
    <div class="p-hp">Tên học phần: {esc(ten_hp)}&emsp;&emsp;&emsp;Mã học phần: {ma_hp}</div>
    <div class="p-htt">Hình thức thi: Trắc nghiệm khách quan</div>
    <div class="p-tg">Thời gian làm bài: {thoi_gian} phút (không kể thời gian phát đề)</div>
    <div class="p-ghichu">Ghi chú: ………</div>
    <div class="p-noidung">NỘI DUNG ĐỀ THI</div>'''

    body = ''
    for i, q in enumerate(questions, 1):
        opts = [('A', q.get('cau_a')), ('B', q.get('cau_b')),
                ('C', q.get('cau_c')), ('D', q.get('cau_d'))]
        body += (f'<div class="pq"><div class="pq-q">Câu {i}. {esc(q.get("noi_dung"))}</div>'
                 f'<div class="pq-opts">{_opts_html_de(opts)}</div></div>')

    foot = '''<div class="p-foot">Cán bộ coi thi không giải thích gì thêm!</div>
        <div class="p-het">HẾT</div>
        <table class="p-sign">
            <tr><td><b>Cán bộ duyệt đề</b></td><td><b>Giảng viên ra đề</b></td></tr>
            <tr><td><span>(Ký, ghi rõ họ tên)</span></td><td><span>(Ký, ghi rõ họ tên)</span></td></tr>
        </table>'''

    onload = ' onload="window.print()"' if auto_print else ''
    doc_title = esc(info['ten'] or de.get('ten_de_thi') or 'Đề thi')
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{doc_title}</title>'
            f'<style>{_DE_PRINT_CSS}</style></head><body{onload}>'
            f'<div class="paper">{head}{body}{foot}</div></body></html>')


def _ten_file_an_toan(s, default='de_thi'):
    """Chuẩn hóa tên file ASCII (bỏ dấu tiếng Việt) cho header tải file."""
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^A-Za-z0-9_-]+', '_', s).strip('_')
    return s or default


@exam_bp.route('/export_exam/<int:exam_id>')
@giao_vien_required
def export_exam(exam_id):
    """Xuất 1 mã đề ra Word (fmt=word, tải về .doc) hoặc PDF (fmt=pdf, mở trang
    in để Lưu thành PDF). Dữ liệu lấy từ DB, dựng theo MẪU 3.2."""
    fmt = request.args.get('fmt', 'pdf')
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT d.*, m.ten_hoc_phan AS ten_mon FROM de_thi d
        JOIN hoc_phan m ON d.hoc_phan_id = m.id
        WHERE d.id = %s
    """, (exam_id,))
    de = cursor.fetchone()
    if not de:
        cursor.close(); conn.close()
        flash('Không tìm thấy đề thi!', 'danger')
        return redirect('/')
    if session.get('role') != 'admin' and de['user_id'] != session['user_id']:
        cursor.close(); conn.close()
        flash('Bạn không có quyền xuất đề thi này!', 'danger')
        return redirect('/')
    cursor.execute("SELECT * FROM cau_hoi WHERE de_thi_id=%s ORDER BY id", (exam_id,))
    questions = cursor.fetchall()
    cursor.close(); conn.close()

    if fmt == 'word':
        from flask import Response
        html_doc = _xay_de_html(de, questions, auto_print=False)
        fname = _ten_file_an_toan(_tach_meta_de(de['ten_de_thi'])['ten']) + '.doc'
        # BOM ﻿ giúp Word đọc đúng tiếng Việt UTF-8
        return Response('﻿' + html_doc, mimetype='application/msword',
                        headers={'Content-Disposition': f'attachment; filename="{fname}"'})

    # fmt=pdf -> trả trang in, tự gọi window.print() để người dùng "Lưu thành PDF"
    return _xay_de_html(de, questions, auto_print=True)


# ==========================================
# SỬA ĐỀ THI
# ==========================================
@exam_bp.route('/edit_exam/<int:exam_id>', methods=['GET', 'POST'])
@giao_vien_required
def edit_exam(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT d.*, m.ten_hoc_phan AS ten_mon
        FROM de_thi d
        LEFT JOIN hoc_phan m ON d.hoc_phan_id = m.id
        WHERE d.id=%s
    """, (exam_id,))
    exam = cursor.fetchone()

    if not exam:
        flash('Không tìm thấy đề thi!', 'danger')
        return redirect('/')
    if (session.get('role') != 'admin' and
            exam['user_id'] != session['user_id']):
        flash('Bạn không có quyền!', 'danger')
        return redirect('/')

    if request.method == 'POST':
        try:
            # Dựng lại ten_de_thi từ các trường sửa được (giữ nguyên Mã học phần
            # và mọi thông tin còn lại), đúng định dạng metadata nhúng ban đầu.
            base    = (request.form.get('ten_de_thi') or '').strip() or 'Đề thi'
            ma_de   = (request.form.get('ma_de') or '').strip() or 'A01'
            hinh_thuc = (request.form.get('hinh_thuc') or '').strip()
            hoc_ky    = (request.form.get('hoc_ky') or '').strip()
            nam_hoc   = (request.form.get('nam_hoc') or '').strip()
            ma_hp = _tach_meta_de(exam['ten_de_thi'])['ma_hp']   # không cho sửa
            meta = [x for x in (hinh_thuc, hoc_ky, nam_hoc) if x]
            if ma_hp:
                meta.append(f'Mã môn {ma_hp}')
            ten_moi = f"{base} ({' · '.join(meta)})" if meta else base
            ten_moi = f"{ten_moi} - Mã đề {ma_de}"[:250]
            cursor.execute(
                "UPDATE de_thi SET ten_de_thi=%s WHERE id=%s",
                (ten_moi, exam_id)
            )
            cursor.execute(
                "SELECT id FROM cau_hoi WHERE de_thi_id=%s",
                (exam_id,)
            )
            questions = cursor.fetchall()

            for q in questions:
                q_id = q['id']
                cursor.execute("""
                    UPDATE cau_hoi
                    SET noi_dung=%s, cau_a=%s, cau_b=%s,
                        cau_c=%s, cau_d=%s, dap_an_dung=%s
                    WHERE id=%s
                """, (
                    request.form.get(f'noi_dung_{q_id}'),
                    request.form.get(f'cau_a_{q_id}'),
                    request.form.get(f'cau_b_{q_id}'),
                    request.form.get(f'cau_c_{q_id}'),
                    request.form.get(f'cau_d_{q_id}'),
                    request.form.get(f'dap_an_{q_id}'),
                    q_id
                ))
            conn.commit()
            flash('✅ Cập nhật thành công!', 'success')
            cursor.close()
            conn.close()
            return redirect(f'/view_exam/{exam_id}')
        except Exception as e:
            conn.rollback()
            flash(f'❌ Lỗi: {str(e)}', 'danger')
            cursor.close()
            conn.close()
            return redirect(f'/edit_exam/{exam_id}')

    cursor.execute(
        "SELECT * FROM cau_hoi WHERE de_thi_id=%s ORDER BY id",
        (exam_id,)
    )
    questions = cursor.fetchall()
    cursor.close()
    conn.close()
    meta = _tach_meta_de(exam['ten_de_thi'])
    return render_template(
        'edit_exam.html', exam=exam, questions=questions,
        meta=meta, hinh_thuc_opts=list(_TITLE_MAP.keys())
    )


# ==========================================
# XÓA ĐỀ THI (Logic Bảo vệ Dữ liệu Thực tế)
# ==========================================
def _safe_next(default):
    """Trả về tham số `next` nếu là đường dẫn NỘI BỘ an toàn (bắt đầu bằng '/'
    nhưng không phải '//...') để xóa/thao tác xong quay lại đúng tab người dùng
    đang đứng. Tránh open-redirect ra ngoài site.

    Đọc CẢ form lẫn query string: nút xóa gửi `next` bằng POST (hidden input),
    còn link cũ thì gắn vào ?next=... Chỉ đọc một bên là bên kia rơi về mặc định
    và người dùng bị ném về trang chủ thay vì quay lại tab đang đứng.
    """
    nxt = request.form.get('next') or request.args.get('next', '')
    if nxt.startswith('/') and not nxt.startswith('//'):
        return nxt
    return default


# POST, không phải GET: xóa là hành động đổi dữ liệu. Để GET thì chỉ cần dụ giáo
# viên bấm vào một đường link (hoặc nhúng <img src>) là đề thi bay mất — và
# CSRF token cũng không gắn vào link GET được.
@exam_bp.route('/delete_exam/<int:exam_id>', methods=['POST'])
@giao_vien_required
def delete_exam(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # 1. Kiểm tra quyền sở hữu
        cursor.execute("SELECT user_id FROM de_thi WHERE id=%s", (exam_id,))
        exam = cursor.fetchone()
        if not exam:
            flash('Không tìm thấy đề thi!', 'danger')
            return redirect(_safe_next('/'))
        if (session.get('role') != 'admin' and exam['user_id'] != session['user_id']):
            flash('Bạn không có quyền xóa đề thi này!', 'danger')
            return redirect(_safe_next('/'))

        # ==========================================
        # KIỂM TRA LOGIC RÀNG BUỘC (BẢO VỆ DỮ LIỆU)
        # ==========================================
        
        # 2. Kiểm tra xem đề này đã được tạo Phòng thi chưa?
        #    (quan hệ phòng ↔ đề nằm hoàn toàn ở bảng trung gian phong_thi_de_thi)
        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM phong_thi_de_thi WHERE de_thi_id=%s
        """, (exam_id,))
        if cursor.fetchone()['cnt'] > 0:
            flash('⚠️ BẢO VỆ DỮ LIỆU: Không thể xóa! Đề thi này đã được sử dụng để tạo Phòng thi. Cần giữ lại để bảo toàn điểm số của Thí sinh.', 'warning')
            return redirect(_safe_next(url_for('exam.index')))

        # ==========================================
        # NẾU VƯỢT QUA ĐƯỢC CÁC BÀI KIỂM TRA THÌ MỚI XÓA
        # ==========================================

        # 3a. Câu hỏi đã chia sẻ vào Ngân hàng chung là BẢN COPY độc lập (kho dùng
        # chung, không còn trỏ về đề gốc) nên xóa đề KHÔNG ảnh hưởng ngân hàng.

        # 3b. Xóa các câu hỏi gốc trong đề
        cursor.execute("DELETE FROM cau_hoi WHERE de_thi_id=%s", (exam_id,))
        
        # 4. Xóa đề thi
        cursor.execute("DELETE FROM de_thi WHERE id=%s", (exam_id,))
        
        conn.commit()
        flash('🗑️ Đã xóa đề thi! Câu hỏi đã chia sẻ vào Ngân hàng chung (nếu có) vẫn được giữ nguyên.', 'success')

    except Exception as e:
        conn.rollback() 
        flash(f'Lỗi hệ thống: {e}', 'danger')
    finally:
        cursor.close()
        conn.close()
        
    return redirect(_safe_next('/admin' if session.get('role') == 'admin' else '/'))


# ==========================================
# WIZARD TẠO ĐỀ THI
#
# Đóng gói câu hỏi rời thành một ĐỀ THI hoàn chỉnh, qua các bước:
#   chọn nguồn (thư viện cá nhân hay ngân hàng chung)
#     -> chọn môn
#     -> chọn câu (bấm từng câu, hoặc bốc ngẫu nhiên theo số lượng)
#     -> đặt tổng điểm, số mã đề muốn sinh, có trộn câu/đáp án không
#     -> sinh ra bản ghi de_thi (loai='de_thi') + các cau_hoi thuộc về nó
#
# Sinh nhiều mã đề thì mỗi mã là MỘT bản ghi de_thi riêng, có bản sao câu hỏi
# riêng đã trộn sẵn. Tốn chỗ hơn là chỉ lưu một đề rồi trộn lúc thi, nhưng đổi
# lại đề in ra giấy khớp chính xác với đề trên máy — điều bắt buộc khi thi tại
# phòng máy mà vẫn phát đề giấy dự phòng.
# ==========================================
def _ma_tu_ten_de(ten_de_thi, ma_set):
    """Lấy mã học phần từ tên đề dạng 'IT101 - Lập trình Web'.

    Chỉ chấp nhận khi mã đó có thật trong danh mục học phần, tránh nhận nhầm
    một cái tên vô tình có dấu gạch ngang.
    """
    if not ten_de_thi or ' - ' not in ten_de_thi:
        return ''
    prefix = ten_de_thi.split(' - ', 1)[0].strip()
    if prefix and (not ma_set or prefix in ma_set):
        return prefix
    return ''


def _bo_cau_hoi(cursor, nguon, user_id, ma_set=None):
    """Liệt kê các BỘ câu hỏi để chọn làm nguồn ra đề.

    Mỗi bản ghi de_thi là một bộ riêng, KHÔNG gộp các bộ cùng môn lại với nhau.
    Giáo viên thường sinh nhiều bộ cho cùng một môn (mỗi lần một chương), và họ
    cần chọn đúng bộ mình muốn. Vì thế mỗi bộ kèm thêm số câu và ngày tạo — đó
    là cách duy nhất để phân biệt hai bộ cùng tên môn.
    """
    ma_set = ma_set or set()
    if nguon == 'ca_nhan':
        cursor.execute("""
            SELECT d.id, d.ten_de_thi, d.ngay_tao,
                   m.id AS mon_id, m.ten_hoc_phan AS ten_mon,
                   (SELECT COUNT(*) FROM cau_hoi c WHERE c.de_thi_id = d.id) AS so_cau
            FROM de_thi d JOIN hoc_phan m ON m.id = d.hoc_phan_id
            WHERE d.user_id = %s AND d.loai = 'cau_hoi'
              AND EXISTS (SELECT 1 FROM cau_hoi c WHERE c.de_thi_id = d.id)
            ORDER BY m.ten_hoc_phan, d.ngay_tao DESC, d.id DESC
        """, (user_id,))
    else:
        # Ngân hàng chung không gắn câu hỏi với đề thi nào cả, nên ở đây một "bộ"
        # chính là một HỌC PHẦN. Chú ý: cột `id` trả về là hoc_phan_id, không
        # phải de_thi_id như nhánh trên — nơi gọi phải hiểu đúng theo `nguon`.
        cursor.execute("""
            SELECT m.id AS id, m.ten_hoc_phan AS ten_de_thi, NULL AS ngay_tao,
                   m.id AS mon_id, m.ten_hoc_phan AS ten_mon,
                   COUNT(n.id) AS so_cau
            FROM hoc_phan m
            JOIN ngan_hang_cau_hoi n ON n.hoc_phan_id = m.id
            GROUP BY m.id, m.ten_hoc_phan
            ORDER BY m.ten_hoc_phan
        """)
    rows = cursor.fetchall()
    for r in rows:
        r['ma_mon'] = _ma_tu_ten_de(r.get('ten_de_thi'), ma_set)
        dt = r.pop('ngay_tao', None)
        r['ngay_str'] = dt.strftime('%d/%m/%Y %H:%M') if hasattr(dt, 'strftime') else (str(dt) if dt else '')
    return rows


def _tron_dap_an_4(a, b, c, d, dap):
    """Trộn 4 phương án, trả về (a, b, c, d mới, chữ cái đúng mới).

    Ghi nhớ NỘI DUNG của đáp án đúng trước khi trộn rồi dò lại vị trí sau — cùng
    một mẹo với `_sap_xep_cau_hoi` trong thi_sinh.py. Khác ở chỗ: hàm này trộn
    MỘT LẦN rồi lưu hẳn kết quả vào đề, còn hàm kia trộn lại mỗi request theo
    hạt giống cố định.
    """
    correct_content = {'A': a, 'B': b, 'C': c, 'D': d}.get((dap or 'A').strip().upper(), a)
    contents = [a, b, c, d]
    random.shuffle(contents)
    letters = ['A', 'B', 'C', 'D']
    new_dap = 'A'
    for i, ct in enumerate(contents):
        if ct == correct_content:
            new_dap = letters[i]
            break
    return contents[0], contents[1], contents[2], contents[3], new_dap


@exam_bp.route('/tao_de_thi')
@giao_vien_required
def tao_de_thi():
    """Mở trang wizard tạo đề, nạp sẵn danh mục học phần và các bộ câu hỏi."""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    # Bọc try vì bảng hoc_phan có thể chưa được nạp trên một máy cài mới. Thiếu
    # danh mục thì ô chọn môn trống, nhưng trang vẫn mở được thay vì lỗi 500.
    try:
        cursor.execute("""
            SELECT id, ma_hoc_phan, ten_hoc_phan, so_tin_chi
            FROM hoc_phan ORDER BY ten_hoc_phan
        """)
        hoc_phan = cursor.fetchall()
    except Exception:
        hoc_phan = []
    ma_set = {h['ma_hoc_phan'] for h in hoc_phan if h.get('ma_hoc_phan')}
    mon_ca_nhan = _bo_cau_hoi(cursor, 'ca_nhan', session['user_id'], ma_set)
    mon_chung   = _bo_cau_hoi(cursor, 'chung', session['user_id'], ma_set)
    cursor.close()
    conn.close()
    return render_template('tao_de_thi.html',
                           mon_ca_nhan=mon_ca_nhan, mon_chung=mon_chung,
                           hoc_phan=hoc_phan)


@exam_bp.route('/api/cau_hoi_nguon')
@giao_vien_required
def api_cau_hoi_nguon():
    """Câu hỏi trong một bộ đã chọn (JSON), cho giao diện wizard hiển thị.

    Vẫn nhận tham số cũ tên là `mon_id` cho tương thích, nhưng giá trị thực chất
    là id của BỘ chứ không phải môn — tên tham số cũ chưa kịp đổi.
    """
    nguon  = request.args.get('nguon', 'ca_nhan')
    de_thi_id = request.args.get('de_thi_id', '') or request.args.get('mon_id', '')
    if not de_thi_id.isdigit():
        return jsonify({'success': False, 'cau_hois': []})

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if nguon == 'ca_nhan':
        cursor.execute("""
            SELECT c.id, c.noi_dung, c.cau_a, c.cau_b, c.cau_c, c.cau_d,
                   c.dap_an_dung, c.chuong, c.do_kho
            FROM cau_hoi c JOIN de_thi d ON c.de_thi_id = d.id
            WHERE d.user_id = %s AND c.de_thi_id = %s
            ORDER BY c.id DESC
        """, (session['user_id'], de_thi_id))
    else:
        cursor.execute("""
            SELECT id, noi_dung, cau_a, cau_b, cau_c, cau_d,
                   dap_an_dung, chuong, do_kho
            FROM ngan_hang_cau_hoi
            WHERE hoc_phan_id = %s
            ORDER BY id DESC
        """, (de_thi_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({'success': True, 'cau_hois': rows})


@exam_bp.route('/tao_de_thi', methods=['POST'])
@giao_vien_required
def tao_de_thi_post():
    """Sinh đề thi từ bộ câu hỏi đã chọn, tạo `so_luong_de` mã đề.

    Mỗi mã đề là một bản ghi de_thi riêng, kèm bản sao câu hỏi ĐÃ TRỘN SẴN. Đề
    lưu ra sao thì in ra và thi trên máy y như vậy.
    """
    nguon  = request.form.get('nguon', 'ca_nhan')
    mon_id = request.form.get('mon_id', '')      # hoc_phan_id — để lưu đề đúng môn
    bo_id  = request.form.get('bo_id', '')       # id của BỘ câu hỏi nguồn
    che_do = request.form.get('che_do', 'thu_cong')
    ten_de = (request.form.get('ten_de', '').strip() or 'Đề thi')

    # Hình thức thi, học kỳ, năm học, mã môn được NHÉT VÀO TÊN ĐỀ, ví dụ:
    #   "Đề thi (Cuối kỳ · HK1 · 2025-2026 · Mã môn IT101)"
    # Bảng de_thi không có cột riêng cho các thông tin này. Ghép vào tên là cách
    # lưu được mà không phải sửa cấu trúc bảng. Về sau `_tach_meta_de` bóc ngược
    # ra để hiển thị thành từng cột. Xấu, nhưng đang hoạt động — muốn dọn thì
    # phải thêm cột và viết migration cho dữ liệu cũ.
    meta = [request.form.get(k, '').strip()
            for k in ('hinh_thuc', 'hoc_ky', 'nam_hoc')]
    meta = [m for m in meta if m]
    ma_mon = request.form.get('ma_mon', '').strip()
    if ma_mon:
        meta.append(f'Mã môn {ma_mon}')
    if meta:
        ten_de = f"{ten_de} ({' · '.join(meta)})"

    try:    tong_diem = float(request.form.get('tong_diem') or 10)
    except ValueError: tong_diem = 10
    if tong_diem <= 0 or tong_diem > 1000:
        tong_diem = 10

    try:    so_luong_de = int(request.form.get('so_luong_de') or 1)
    except ValueError: so_luong_de = 1
    so_luong_de = max(1, min(so_luong_de, 20))

    # Thời lượng lưu theo đề, để lúc mở phòng thi hệ thống tự điền sẵn.
    try:    thoi_gian = int(request.form.get('thoi_gian') or 60)
    except ValueError: thoi_gian = 60
    thoi_gian = max(1, min(thoi_gian, 600))

    tron_ch = request.form.get('tron_cau_hoi') == '1'
    tron_da = request.form.get('tron_dap_an') == '1'

    # Giao diện đã trộn sẵn các mã đề ở bước "Xem trước" và gửi kèm lên đây. Lưu
    # ĐÚNG những bản đó, không trộn lại ở server — nếu server trộn lại thì đề
    # lưu xuống khác đề giáo viên vừa xem, và họ mất niềm tin vào bản xem trước.
    variants = None
    raw_variants = request.form.get('variants_json', '')
    if raw_variants:
        try:
            parsed = json.loads(raw_variants)
            if isinstance(parsed, list) and parsed:
                variants = parsed
        except (ValueError, TypeError):
            variants = None

    if not mon_id.isdigit() or not bo_id.isdigit():
        flash('Vui lòng chọn bộ câu hỏi!', 'danger')
        return redirect(url_for('exam.tao_de_thi'))

    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # Chỉ lấy câu trong ĐÚNG bộ đã chọn. Vơ hết câu cùng môn ở các bộ khác
        # thì đề ra sẽ có cả câu của chương giáo viên không định kiểm tra.
        if nguon == 'ca_nhan':
            cursor.execute("""
                SELECT c.id, c.noi_dung, c.cau_a, c.cau_b, c.cau_c, c.cau_d,
                       c.dap_an_dung, c.chuong, c.do_kho
                FROM cau_hoi c JOIN de_thi d ON c.de_thi_id = d.id
                WHERE d.user_id = %s AND c.de_thi_id = %s
            """, (session['user_id'], bo_id))
        else:
            cursor.execute("""
                SELECT id, noi_dung, cau_a, cau_b, cau_c, cau_d,
                       dap_an_dung, chuong, do_kho
                FROM ngan_hang_cau_hoi WHERE hoc_phan_id = %s
            """, (bo_id,))
        pool = cursor.fetchall()
        pool_by_id = {q['id']: q for q in pool}

        def _luu_paper(i, ds_items):
            """Lưu 1 mã đề (A01, A02, ...) gồm danh sách câu đã sắp sẵn.
            ds_items: list các tuple (base_row, a, b, c, d, dap)."""
            ten = f'{ten_de} - Mã đề A{i:02d}'[:250]
            cursor.execute("""
                INSERT INTO de_thi (hoc_phan_id, ten_de_thi, tong_so_cau,
                                    user_id, tong_diem, thoi_gian, loai, nguon)
                VALUES (%s,%s,%s,%s,%s,%s,'de_thi',%s)
            """, (mon_id, ten, len(ds_items), session['user_id'], tong_diem, thoi_gian,
                  'ca_nhan' if nguon == 'ca_nhan' else 'ngan_hang'))
            de_id = cursor.lastrowid
            for base, a, b, c, d, dap in ds_items:
                cursor.execute("""
                    INSERT INTO cau_hoi (de_thi_id, chuong, do_kho, noi_dung,
                                         cau_a, cau_b, cau_c, cau_d, dap_an_dung)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (de_id, base.get('chuong'), base.get('do_kho'),
                      base['noi_dung'], a, b, c, d, dap))

        da_tao = 0

        if variants:
            # ===== Lưu đúng các bản đề client đã dựng ở bước Xem trước =====
            for i, variant in enumerate(variants[:so_luong_de], start=1):
                ds_items = []
                for it in (variant or []):
                    try:
                        qid = int(it.get('id'))
                    except (TypeError, ValueError, AttributeError):
                        continue
                    base = pool_by_id.get(qid)
                    if not base:
                        continue
                    a, b = it.get('cau_a'), it.get('cau_b')
                    c, d = it.get('cau_c'), it.get('cau_d')
                    dap  = (it.get('dap_an_dung') or 'A').strip().upper()[:1]

                    # Toàn vẹn dữ liệu: 4 phương án client gửi phải là HOÁN VỊ của
                    # 4 phương án gốc và đáp án đúng phải trỏ vào nội dung đúng gốc.
                    # Nếu lệch (dữ liệu bị can thiệp) -> quay về thứ tự gốc cho an toàn.
                    goc = [base['cau_a'], base['cau_b'], base['cau_c'], base['cau_d']]
                    correct_goc = {'A': base['cau_a'], 'B': base['cau_b'],
                                   'C': base['cau_c'], 'D': base['cau_d']
                                   }.get((base['dap_an_dung'] or 'A').upper(), base['cau_a'])
                    sel_content = {'A': a, 'B': b, 'C': c, 'D': d}.get(dap)
                    if (None in (a, b, c, d) or sorted(map(str, [a, b, c, d])) != sorted(map(str, goc))
                            or dap not in ('A', 'B', 'C', 'D') or sel_content != correct_goc):
                        a, b, c, d = goc
                        dap = (base['dap_an_dung'] or 'A').upper()[:1] or 'A'
                    ds_items.append((base, a, b, c, d, dap))
                if not ds_items:
                    continue
                _luu_paper(i, ds_items)
                da_tao += 1
            if da_tao == 0:
                flash('Không có câu hỏi hợp lệ để tạo đề!', 'warning')
                return redirect(url_for('exam.tao_de_thi'))
        else:
            # ===== Phương án dự phòng: server tự bốc & trộn (giữ tương thích) =====
            chosen = None
            so_cau = 0
            if che_do == 'thu_cong':
                ids = request.form.getlist('selected_ids')
                chosen = [pool_by_id[int(i)] for i in ids
                          if i.isdigit() and int(i) in pool_by_id]
                if not chosen:
                    flash('Bạn chưa chọn câu hỏi nào!', 'warning')
                    return redirect(url_for('exam.tao_de_thi'))
            else:
                try:    so_cau = int(request.form.get('so_cau') or 0)
                except ValueError: so_cau = 0
                if so_cau <= 0:
                    flash('Vui lòng nhập số câu mỗi đề (chế độ ngẫu nhiên)!', 'warning')
                    return redirect(url_for('exam.tao_de_thi'))
                if so_cau > len(pool):
                    flash(f'Môn này chỉ có {len(pool)} câu, không đủ {so_cau} câu mỗi đề!', 'warning')
                    return redirect(url_for('exam.tao_de_thi'))

            for i in range(1, so_luong_de + 1):
                ds = list(chosen) if che_do == 'thu_cong' else random.sample(pool, so_cau)
                if tron_ch:
                    random.shuffle(ds)
                ds_items = []
                for q in ds:
                    a, b, c, d = q['cau_a'], q['cau_b'], q['cau_c'], q['cau_d']
                    dap = q['dap_an_dung'] or 'A'
                    if tron_da:
                        a, b, c, d, dap = _tron_dap_an_4(a, b, c, d, dap)
                    ds_items.append((q, a, b, c, d, dap))
                _luu_paper(i, ds_items)
                da_tao += 1

        conn.commit()
        flash(f'✅ Đã tạo {da_tao} đề thi vào Thư viện!', 'success')
        return redirect(url_for('exam.index') + '?tab=library&sub=dethi')

    except Exception as e:
        conn.rollback()
        current_app.logger.exception('Lỗi khi tạo đề thi')
        flash(f'Lỗi khi tạo đề thi: {e}', 'danger')
        return redirect(url_for('exam.tao_de_thi'))
    finally:
        cursor.close()
        conn.close()