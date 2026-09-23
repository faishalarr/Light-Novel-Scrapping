import io
import os
import re
import html
import json
import math
import time
import datetime
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ==========================================
# CARA PAKAI
# ==========================================
# Taruh link KE SALAH SATU CHAPTER (chapter mana saja, bebas) dari tiap
# series yang mau di-download di file "VeiSeriesUrls.txt", satu link per
# baris. Script otomatis menemukan SEMUA chapter dalam series itu lewat
# data JSON yang sudah tertanam di halaman (tidak perlu buka halaman index
# terpisah), lalu mengelompokkannya per Volume dan membuat 1 PDF per
# volume.
#
# Contoh isi VeiSeriesUrls.txt:
# https://veinovel.com/series/hey-best-friend-wanna-kiss-again-today/chapter/v1c5
SERIES_URLS_FILE = "VeiSeriesUrls.txt"

SKIP_EXISTING_PDF = True

# Nama situs yang ditampilkan di footer tiap halaman (band kanan bawah).
SITE_NAME = "LalaNovel"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'
}

# Session requests global -- cookie login (kalau ada) otomatis nempel
# di semua request (fetch halaman, chapter, gambar).
SESSION = requests.Session()
# Track status login: True setelah login_veinovel() berhasil.
IS_LOGGED_IN = False

# Nama family internal yang dipakai di semua pdf.set_font()/add_font().
# Cuma label -- ganti font (mis. ke serif) TIDAK perlu ubah pemanggilan
# set_font() di tempat lain, cukup ganti FONT_REGULAR/FONT_BOLD di bawah.
FONT_FAMILY = "NovelFont"

FONT_DIR = "fonts"
FONT_REGULAR = os.path.join(FONT_DIR, "NovelSerif-Regular.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "NovelSerif-Bold.ttf")

for _path in (FONT_REGULAR, FONT_BOLD):
    if not os.path.exists(_path):
        raise FileNotFoundError(
            f"Font '{_path}' tidak ditemukan. Taruh file .ttf regular "
            f"dan bold-nya di folder '{FONT_DIR}/' dengan nama persis "
            f"'{os.path.basename(FONT_REGULAR)}' dan "
            f"'{os.path.basename(FONT_BOLD)}' (atau edit FONT_REGULAR/"
            f"FONT_BOLD di atas biar cocok sama nama file font kamu), "
            f"lalu jalankan lagi."
        )

OUTPUT_DIR = "Result"
os.makedirs(OUTPUT_DIR, exist_ok=True)

LOGS_DIR = "Logs"
os.makedirs(LOGS_DIR, exist_ok=True)

_LOG_TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = os.path.join(LOGS_DIR, f"log_{_LOG_TS}.txt")

STATS = {
    "series_ok": 0, "series_gagal": 0,
    "volume_ok": 0, "volume_skip": 0,
    "chapter_ok": 0, "chapter_gagal": 0, "chapter_premium_skip": 0,
    "gambar_ok": 0, "gambar_gagal": 0,
    "errors": [],
}


def log(msg, level="INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class NovelPDF(FPDF):
    _chapter_title = ""
    _image_only_pages = set()
    _next_page_is_image = False

    def add_page(self, *args, **kwargs):
        super().add_page(*args, **kwargs)
        if self._next_page_is_image:
            self._image_only_pages.add(self.page_no())
            self._next_page_is_image = False
        else:
            self.set_page_background((0, 0, 0))
        self.set_text_color(255, 255, 255)

    def header(self):
        if self.page_no() <= 1:
            return
        if self._next_page_is_image or self.page_no() in self._image_only_pages:
            return
        self.set_y(5.1)
        self.set_font(FONT_FAMILY, "", 16)
        self.set_text_color(255, 255, 255)
        self.cell(0, 6, f"Page | {self.page_no()}", align='R')
        self.set_draw_color(217, 217, 217)
        self.set_line_width(0.17)
        self.line(self.l_margin, 11.85, self.w - self.r_margin, 11.85)
        self.set_y(self.t_margin)

    def _fit_text_to_width(self, text, max_width, ellipsis="..."):
        """Potong `text` (kalau perlu) supaya muat di `max_width` (mm)
        pas dirender dengan font yang lagi aktif, ditambah '...' di
        akhir kalau emang kepotong."""
        if self.get_string_width(text) <= max_width:
            return text
        ellipsis_w = self.get_string_width(ellipsis)
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = text[:mid].rstrip()
            if self.get_string_width(candidate) + ellipsis_w <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return (text[:lo].rstrip() + ellipsis) if lo > 0 else ellipsis

    def _fit_title_lines(self, text, max_width, max_font=14, min_font=8, font_style="B"):
        """Cari ukuran font terbesar yang bikin `text` muat dalam SATU
        baris selebar `max_width`. Kalau di font terkecil pun tetap
        kepanjangan, dipecah jadi 2 baris (greedy per kata).

        CATATAN PENTING #1: sengaja cuma manggil set_font() SEDIKIT
        mungkin kali (bukan loop nyoba tiap ukuran satu-satu dari
        max_font turun ke min_font). Lebar teks TrueType itu linear
        terhadap ukuran font, jadi ukuran yang pas bisa dihitung
        langsung dari SATU pengukuran + rasio, tanpa perlu banyak
        pemanggilan set_font().

        CATATAN PENTING #2: `font_style` default sekarang "B" (Bold),
        biar sama kayak ScraperLN.py. Versi SEBELUMNYA sengaja pakai
        regular ("") di sini karena manggil set_font(..., "B", ...) dari
        DALAM footer() -- di ukuran berapa pun -- kebukti bikin fpdf2
        salah nge-render teks di halaman DAFTAR ISI yang diisi belakangan
        lewat teknik "mundur" (pdf.page = N ke halaman yang udah ada)
        jadi karakter acak (isinya sendiri tetap benar, cuma glyph yang
        salah). Kalau itu muncul lagi di PDF hasil script ini, ganti balik
        font_style di sini (dan 2 pemanggilan set_font Bold di footer())
        ke regular "" -- itu obatnya."""
        if not text:
            self.set_font(FONT_FAMILY, font_style, max_font)
            return max_font, [""]

        self.set_font(FONT_FAMILY, font_style, max_font)
        w_at_max = self.get_string_width(text)
        if w_at_max <= max_width:
            return max_font, [text]

        # Skala linear: lebar teks di ukuran X = w_at_max * (X / max_font).
        # Cari X terbesar (dibulatkan ke bawah) yang muat, dari situ
        # cuma perlu verifikasi di SATU ukuran (bukan loop semua ukuran).
        ideal_size = int((max_width / w_at_max) * max_font)
        size = max(min_font, min(max_font, ideal_size))
        self.set_font(FONT_FAMILY, font_style, size)
        # Kalau perkiraan meleset dikit (pembulatan/kerning), turunin 1pt
        # sampai muat atau mentok min_font -- biasanya cuma butuh 0-1x
        # percobaan tambahan, bukan loop penuh max_font..min_font.
        while size > min_font and self.get_string_width(text) > max_width:
            size -= 1
            self.set_font(FONT_FAMILY, font_style, size)
        if self.get_string_width(text) <= max_width:
            return size, [text]

        # Gak muat 1 baris walau udah di font terkecil -> pecah jadi 2 baris.
        words = text.split(' ')
        line1 = ""
        i = 0
        while i < len(words):
            candidate = (line1 + " " + words[i]).strip()
            if self.get_string_width(candidate) <= max_width:
                line1 = candidate
                i += 1
            else:
                break
        if i == 0:
            line1 = self._fit_text_to_width(words[0], max_width)
            i = 1
        line2 = " ".join(words[i:]).strip()
        if not line2:
            return min_font, [line1]
        line2 = self._fit_text_to_width(line2, max_width)
        return min_font, [line1, line2]

    def footer(self):
        if self.page_no() <= 1:
            return
        if self._next_page_is_image or self.page_no() in self._image_only_pages:
            return
        usable_w = self.w - self.l_margin - self.r_margin
        half_w = usable_w / 2
        title_max_w = half_w - 2
        title_text = clean_unicode(self._chapter_title)
        # NOTE: style Bold di sini (dipakai biar sama kayak ScraperLN.py).
        # PERINGATAN dari versi sebelumnya: manggil set_font(..., "B", ...)
        # dari DALAM footer() pernah kebukti bikin fpdf2 salah nge-render
        # teks di halaman DAFTAR ISI yang diisi belakangan lewat teknik
        # "mundur" (pdf.page = N ke halaman yang udah ada) -- jadi karakter
        # acak (isinya tetap benar, cuma glyph salah). Kalau nanti muncul
        # lagi masalah itu di PDF hasil script ini, itu penyebabnya --
        # baliknya ganti "B" jadi "" di 3 baris set_font bawah.
        font_size, title_lines = self._fit_title_lines(title_text, title_max_w)
        line_h = 5.0 if len(title_lines) == 1 else 4.0
        band_top = 280.5
        band_h = max(5.5, line_h * len(title_lines) + 1.5)
        self.set_fill_color(211, 211, 211)
        self.rect(self.l_margin, band_top, usable_w, band_h, 'F')
        self.set_text_color(0, 0, 0)
        self.set_font(FONT_FAMILY, "B", font_size)
        text_y = band_top + (band_h - line_h * len(title_lines)) / 2
        for i, line in enumerate(title_lines):
            self.set_xy(self.l_margin, text_y + i * line_h)
            self.cell(half_w, line_h, line, align='L')
        self.set_font(FONT_FAMILY, "B", 14)
        self.set_xy(self.l_margin + half_w, band_top)
        self.cell(half_w, band_h, SITE_NAME, align='R')
        self.set_text_color(255, 255, 255)

    def chapter_title(self, title):
        self.set_font(FONT_FAMILY, "B", 26)
        self.set_text_color(255, 255, 255)
        self.set_x(self.l_margin)
        self.multi_cell(0, 10, clean_unicode(title), align='C')
        self.ln(6)

    def _first_line_indent_prefix(self, indent_mm=12.7):
        """Fpdf gak punya first-line-indent bawaan; set_x nge-indent
        SEMUA baris paragraf, bukan cuma baris pertama. Trik: tempel
        spasi di depan teks paragraf secukupnya biar lebar visualnya
        kira-kira sama dengan `indent_mm` (default 0.5 inch)."""
        space_w = self.get_string_width(" ")
        if space_w <= 0:
            return "    "
        n = max(1, round(indent_mm / space_w))
        return " " * n


# Karakter CJK punctuation yang kadang nongol di teks novel sebagai
# tanda kutip pesan teks (chat/SMS): 『...』 (white corner brackets) dan
# 「...」 (raised corner brackets). Glyph karakter-karakter ini biasanya
# TIDAK ada di font Latin/serif (termasuk DejaVuSans), jadi kalau
# diterusin mentah ke fpdf2 bakal di-skip senyap dan gak nongol di PDF.
# Ganti ke ASCII bracket [ ] yang hampir pasti ada di font apa pun.
_CJK_PUNCT_TO_ASCII = {
    '\u300e': '[',  # 『 -> [
    '\u300f': ']',  # 』 -> ]
    '\u300c': '[',  # 「 -> [
    '\u300d': ']',  # 」 -> ]
}


def clean_unicode(text):
    if not text:
        return ""
    replacements = {'\u00a0': ' ', '\u200b': ''}
    replacements.update(_CJK_PUNCT_TO_ASCII)
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    return text


def sanitize_filename(name):
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name or "Novel"


# Batas percobaan ulang buat fetch halaman/chapter yang gagal sesaat
# (mis. 404/5xx transien atau koneksi putus gara-gara request beruntun
# terlalu cepat). Jeda antar percobaan makin lama tiap kali gagal.
MAX_RETRY = 3


def fetch_url(url):
    res = SESSION.get(url, headers=HEADERS, timeout=20)
    res.encoding = 'utf-8'
    return res


def fetch_url_with_retry(url, max_retry=MAX_RETRY):
    """Sama seperti fetch_url, tapi coba ulang sampai `max_retry` kali
    kalau koneksi gagal atau status bukan 200, dengan jeda yang makin
    lama tiap percobaan. Balikin (response, error_message); response
    None kalau semua percobaan gagal."""
    res = None
    last_error = None
    for attempt in range(1, max_retry + 1):
        try:
            res = fetch_url(url)
        except Exception as e:
            last_error = f"koneksi gagal: {e}"
            res = None
        else:
            if res.status_code == 200:
                return res, None
            last_error = f"status {res.status_code}"
            res = None
        if attempt < max_retry:
            log(f"    ↻ Percobaan {attempt} gagal ({last_error}), coba lagi...", "WARN")
            time.sleep(2 * attempt)
    return None, last_error


def fetch_image(src_url, referer=None):
    """Unduh gambar. `referer` opsional buat CDN yang nolak request
    tanpa header Referer yang cocok (proteksi hotlink) -> balikin 403
    meski URL-nya valid."""
    headers = HEADERS
    if referer:
        headers = dict(HEADERS)
        headers['Referer'] = referer
    try:
        img_res = SESSION.get(src_url, headers=headers, timeout=20)
        if img_res.status_code == 200:
            STATS["gambar_ok"] += 1
            return io.BytesIO(img_res.content)
        log(f"    ⚠️ Gambar status {img_res.status_code} ({src_url[:50]}...)", "WARN")
    except Exception as e:
        log(f"    ⚠️ Gagal mengunduh gambar ({src_url[:50]}...): {e}", "WARN")
    STATS["gambar_gagal"] += 1
    return None


def to_pdf_safe_image(img_data):
    """fpdf2 gak support format WebP. Kalau gambarnya WebP (dideteksi
    dari magic bytes 'RIFF'), convert ke JPEG dulu pakai Pillow sebelum
    dikembalikan. Format lain (PNG/JPEG) dikembalikan apa adanya."""
    try:
        img_data.seek(0)
        header = img_data.read(4)
        img_data.seek(0)
        if header[:4] == b'RIFF':
            from PIL import Image as _PIL
            _img = _PIL.open(img_data)
            buf = io.BytesIO()
            _img.convert('RGB').save(buf, format='JPEG', quality=90)
            buf.seek(0)
            return buf
    except Exception as e:
        log(f"    ⚠️ Gagal convert gambar WebP->JPEG: {e}", "WARN")
        img_data.seek(0)
    return img_data


def extract_inertia_data(page_html):
    """VeiNovel pakai Inertia.js (Laravel+Vue): SELURUH data halaman,
    termasuk teks lengkap chapter, sudah tertanam sebagai JSON di dalam
    atribut data-page="..." pada <div id="app">. Fungsi ini menariknya
    keluar dan mem-parsing jadi dict Python."""
    m = re.search(r'<div id="app" data-page="(.*?)"></div>', page_html, re.DOTALL)
    if not m:
        raise RuntimeError("Tidak menemukan data-page (struktur halaman mungkin berubah).")
    raw = html.unescape(m.group(1))
    return json.loads(raw)


def parse_chapter_content(content_html):
    """Ubah HTML isi chapter (<p>...</p>, <img .../>) jadi list elemen
    teks & gambar, siap dipakai builder PDF."""
    soup = BeautifulSoup(content_html, 'html.parser')
    elements = []
    for tag in soup.find_all(['p', 'img']):
        if tag.name == 'img':
            src = tag.get('src')
            if src:
                elements.append({'type': 'img', 'src': src})
        else:
            text = tag.get_text(strip=True)
            if not text:
                continue
            elements.append({'type': 'text', 'value': text})
    return elements


def truncate_for_toc(pdf, text, max_width):
    if pdf.get_string_width(text) <= max_width:
        return text
    ellipsis = "..."
    while text and pdf.get_string_width(text + ellipsis) > max_width:
        text = text[:-1]
    text = text.rstrip()
    return (text + ellipsis) if text else ellipsis


def get_series_and_chapters(entry_url):
    """Dari SATU URL chapter mana saja, ambil info series + daftar
    LENGKAP semua chapter (grouped nanti per volume)."""
    log(f"📖 Membaca: {entry_url}")
    res, err = fetch_url_with_retry(entry_url)
    if res is None:
        raise RuntimeError(f"Gagal membuka halaman setelah {MAX_RETRY}x percobaan ({err}).")

    data = extract_inertia_data(res.text)
    props = data.get('props', {})
    series = props.get('series')
    chapter = props.get('chapter')
    all_chapters = props.get('allChapters')  # sejajar dgn 'chapter', bukan di dalamnya

    if not series or not all_chapters:
        raise RuntimeError("Struktur data series/allChapters tidak ditemukan di halaman ini.")

    return series, all_chapters


# ==========================================
# BUILD PDF UNTUK SATU VOLUME
# ==========================================
def build_pdf_for_volume(series, chapters_meta, output_path):
    """chapters_meta: list of dict {chapter_link, chapter_number, title, is_premium}
    (semuanya dari volume yang sama, sudah terurut)."""
    if SKIP_EXISTING_PDF and os.path.exists(output_path):
        log(f"  ⏭️ Dilewati (PDF sudah ada): {output_path}")
        STATS["volume_skip"] += 1
        return

    t_start = time.time()
    slug = series['slug']

    pdf = NovelPDF()
    pdf.add_font(FONT_FAMILY, "", FONT_REGULAR)
    pdf.add_font(FONT_FAMILY, "B", FONT_BOLD)
    pdf.set_margins(left=25.4, top=25.4, right=25.4)
    pdf.set_auto_page_break(auto=True, margin=25.4)
    pdf.set_page_background((0, 0, 0))  # dark theme: semua halaman background hitam

    chapters_data = []

    for cm in chapters_meta:
        if cm.get('is_premium') and not IS_LOGGED_IN:
            log(f"  🔒 Dilewati (chapter premium/berbayar): {cm.get('title')}")
            STATS["chapter_premium_skip"] += 1
            continue

        chapter_url = f"https://veinovel.com/series/{slug}/chapter/{cm['chapter_link']}"
        t_ch = time.time()
        log(f"  Scraping: {chapter_url}")
        res, err = fetch_url_with_retry(chapter_url)
        if res is None:
            log(f"    ⚠️ Gagal ambil chapter setelah {MAX_RETRY}x percobaan ({err}), dilewati.", "WARN")
            STATS["chapter_gagal"] += 1
            STATS["errors"].append(f"{chapter_url} -> {err}")
            continue
        try:
            data = extract_inertia_data(res.text)
        except Exception as e:
            log(f"    ⚠️ Gagal ambil chapter ({e}), dilewati.", "WARN")
            STATS["chapter_gagal"] += 1
            STATS["errors"].append(f"{chapter_url} -> {e}")
            continue

        ch = data.get('props', {}).get('chapter', {})
        content_html = ch.get('content', '')
        elements = parse_chapter_content(content_html)
        title = ch.get('title') or cm.get('title') or cm['chapter_link']

        link_id = pdf.add_link()
        chapters_data.append({
            'title': title,
            'elements': elements,
            'link_id': link_id,
            'page_number': None,
        })

        word_count = sum(len(e['value'].split()) for e in elements if e['type'] == 'text')
        img_count = sum(1 for e in elements if e['type'] == 'img')
        STATS["chapter_ok"] += 1
        log(f"    ✅ \"{title}\" — {word_count} kata, {img_count} gambar ({time.time()-t_ch:.1f}s)")

    if not chapters_data:
        log("  ⚠️ Tidak ada bab yang berhasil di-scrape, PDF dilewati.", "WARN")
        return

    # --- HALAMAN COVER ---
    cover_url = series.get('cover_url')
    if cover_url:
        img_data = fetch_image(cover_url)
        if img_data:
            try:
                img_data = to_pdf_safe_image(img_data)
                from PIL import Image as _PIL2
                img_data.seek(0)
                _img_check = _PIL2.open(img_data)
                _img_w, _img_h = _img_check.size
                img_data.seek(0)
                PAGE_W = 210.0
                PAGE_H = PAGE_W * _img_h / _img_w
                pdf._next_page_is_image = True
                pdf.add_page(format=(PAGE_W, PAGE_H))
                pdf.image(img_data, x=0, y=0, w=PAGE_W, h=PAGE_H)
            except Exception as e:
                log(f"    ⚠️ Gagal render cover: {e}", "WARN")

    # --- HALAMAN JUDUL (judul novel + source) ---
    story_title = series.get('title', 'Novel')
    pdf.add_page()
    pdf._chapter_title = ""
    pdf.set_font(FONT_FAMILY, 'B', 20)
    pdf.set_text_color(255, 255, 255)
    pdf.ln(60)
    pdf.multi_cell(0, 12, clean_unicode(story_title), align='C')
    pdf.ln(20)
    pdf.set_font(FONT_FAMILY, '', 10)
    pdf.set_text_color(180, 180, 180)
    pdf.cell(0, 8, "Source: veinovel.com", align='C')
    pdf.set_text_color(255, 255, 255)

    # --- DAFTAR ISI (multi-halaman) ---
    TOC_ROW_HEIGHT = 9.5
    TOC_PAGE_BOTTOM_Y = 297 - 20
    TOC_FIRST_PAGE_START_Y = 45
    TOC_OTHER_PAGE_START_Y = 20
    toc_capacity_first = max(1, int((TOC_PAGE_BOTTOM_Y - TOC_FIRST_PAGE_START_Y) // TOC_ROW_HEIGHT) - 1)
    toc_capacity_other = max(1, int((TOC_PAGE_BOTTOM_Y - TOC_OTHER_PAGE_START_Y) // TOC_ROW_HEIGHT) - 1)

    num_chapters = len(chapters_data)
    if num_chapters <= toc_capacity_first:
        toc_pages_needed = 1
    else:
        toc_pages_needed = 1 + math.ceil(
            (num_chapters - toc_capacity_first) / toc_capacity_other
        )

    pdf.add_page()
    pdf._chapter_title = ""
    pdf.set_font(FONT_FAMILY, 'B', 18)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 15, "DAFTAR ISI", align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_line_width(0.6)
    pdf.line(pdf.get_x(), pdf.get_y(), 190, pdf.get_y())
    pdf.ln(10)
    toc_start_page = pdf.page_no()

    for _ in range(toc_pages_needed - 1):
        pdf.add_page()
    toc_page_numbers = list(range(toc_start_page, toc_start_page + toc_pages_needed))

    # --- ISI CHAPTER ---
    for ch in chapters_data:
        pdf.add_page()
        ch['page_number'] = pdf.page_no()
        pdf.set_link(ch['link_id'], page=ch['page_number'])

        clean_title = clean_unicode(ch['title'])
        pdf.start_section(clean_title)
        # Footer cukup nama series-nya aja (BUKAN ch['title'] chapter),
        # sama kayak ScraperLN.py.
        pdf._chapter_title = clean_unicode(series.get('title', '')) or clean_title
        pdf.chapter_title(clean_title)

        pdf.set_font(FONT_FAMILY, size=17)
        pdf.set_text_color(255, 255, 255)
        is_first_paragraph = True
        for elem in ch['elements']:
            if elem['type'] == 'img':
                img_data = fetch_image(elem['src'])
                if img_data:
                    try:
                        img_data = to_pdf_safe_image(img_data)
                        from PIL import Image as _PIL2
                        img_data.seek(0)
                        _img_check = _PIL2.open(img_data)
                        _img_w, _img_h = _img_check.size
                        img_data.seek(0)
                        PAGE_W = 210.0
                        PAGE_H = PAGE_W * _img_h / _img_w
                        pdf._next_page_is_image = True
                        pdf.add_page(format=(PAGE_W, PAGE_H))
                        pdf.image(img_data, x=0, y=0, w=PAGE_W, h=PAGE_H)
                    except Exception as e:
                        log(f"    ⚠️ Gagal render gambar: {e}", "WARN")
            elif elem['type'] == 'text':
                clean_text = clean_unicode(elem['value'])
                pdf.set_font(FONT_FAMILY, size=17)
                pdf.set_text_color(255, 255, 255)
                pdf.set_x(pdf.l_margin)
                # Paragraf pertama di chapter: rata kiri tanpa indent.
                # Paragraf berikutnya: baris pertama di-indent 0.5"
                # (fpdf gak punya first-line-indent bawaan, jadi pakai
                # spasi buatan di depan teks -- lihat
                # _first_line_indent_prefix).
                if is_first_paragraph:
                    body_text = clean_text
                    is_first_paragraph = False
                else:
                    body_text = pdf._first_line_indent_prefix() + clean_text
                # align='L' (bukan 'J'): biar spasi indentasi buatan di
                # depan paragraf gak ikut diregangkan oleh mesin justify
                # FPDF pada baris yang bukan baris terakhir paragraf.
                pdf.multi_cell(0, 6.9, body_text, align='L')
                pdf.ln(2)

    # --- ISI DAFTAR ISI ---
    last_page_number = pdf.page_no()  # halaman terakhir (bab terakhir), direstore di bawah
    entry_idx = 0
    for page_i, page_num in enumerate(toc_page_numbers):
        pdf.page = page_num
        if page_i == 0:
            pdf.set_y(TOC_FIRST_PAGE_START_Y)
            capacity = toc_capacity_first
        else:
            pdf.set_y(TOC_OTHER_PAGE_START_Y)
            capacity = toc_capacity_other
        pdf.set_font(FONT_FAMILY, size=11)

        for _ in range(capacity):
            if entry_idx >= num_chapters:
                break
            ch = chapters_data[entry_idx]
            entry_idx += 1
            clean_ch_title = clean_unicode(ch['title'])
            pdf.set_text_color(120, 170, 255)
            toc_text = f"{entry_idx}. {clean_ch_title}"
            toc_text = truncate_for_toc(pdf, toc_text, 138)
            pdf.cell(145, 8, toc_text, link=ch['link_id'])
            pdf.set_text_color(190, 190, 190)
            pdf.cell(0, 8, f"Hal. {ch['page_number']}", align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT, link=ch['link_id'])
            pdf.ln(1.5)

        if entry_idx >= num_chapters:
            break

    # PENTING: loop di atas "mundur" ke halaman TOC (pdf.page = page_num)
    # buat nulis entrinya, dan gak pernah add_page() lagi setelahnya.
    # Kalau pointer halaman dibiarkan nyangkut di TOC pas pdf.output()
    # dipanggil, fpdf2 nge-finalize (dan manggil footer()) utk halaman
    # TOC itu SEKALI LAGI, bukan buat halaman bab terakhir -- akibatnya
    # halaman bab terakhir gak pernah kebagian footer sama sekali.
    # Restore pointer ke halaman terakhir sebelum output() biar footer
    # bab terakhir ikut ke-render.
    pdf.page = last_page_number

    pdf.output(output_path)
    STATS["volume_ok"] += 1
    log(f"  ✅ Tersimpan: {output_path} ({len(chapters_data)} bab, {time.time()-t_start:.1f}s)")


# ==========================================
# MAIN
# ==========================================
def load_urls(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"File '{path}' tidak ditemukan.")
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls


def print_summary(t_start_total):
    elapsed = time.time() - t_start_total
    log("\n" + "=" * 50)
    log("📊 RINGKASAN")
    log("=" * 50)
    log(f"Series berhasil       : {STATS['series_ok']}")
    log(f"Series gagal          : {STATS['series_gagal']}")
    log(f"Volume/PDF selesai    : {STATS['volume_ok']}")
    log(f"Volume dilewati       : {STATS['volume_skip']} (PDF sudah ada)")
    log(f"Chapter berhasil      : {STATS['chapter_ok']}")
    log(f"Chapter gagal         : {STATS['chapter_gagal']}")
    log(f"Chapter premium/skip  : {STATS['chapter_premium_skip']}")
    log(f"Gambar berhasil       : {STATS['gambar_ok']}")
    log(f"Gambar gagal          : {STATS['gambar_gagal']}")
    log(f"Total waktu           : {elapsed:.1f}s")
    if STATS["errors"]:
        log(f"\n⚠️ Detail {len(STATS['errors'])} error:")
        for e in STATS["errors"]:
            log(f"  - {e}")
    log(f"\n📝 Log lengkap: {LOG_FILE}")


def login_veinovel():
    """Login ke VeiNovel pakai credential dari env var VEI_EMAIL & VEI_PASSWORD.
    Laravel/Inertia flow:
      1. GET /auth -> ambil CSRF token dari meta tag
      2. POST /login -> kirim email, password, _token
    Set IS_LOGGED_IN = True kalau berhasil."""
    global IS_LOGGED_IN
    email = os.environ.get("VEI_EMAIL", "").strip()
    password = os.environ.get("VEI_PASSWORD", "").strip()
    if not email or not password:
        log("ℹ️ VEI_EMAIL / VEI_PASSWORD gak di-set, skip login.", "WARN")
        return False

    log(f"🔑 Mencoba login sebagai {email}...")
    try:
        # Step 1: GET /auth (halaman login) buat ambil CSRF token dari meta tag
        login_page = SESSION.get("https://veinovel.com/auth", headers=HEADERS, timeout=20)
        if login_page.status_code != 200:
            log(f"   ⚠️ Gagal buka halaman login (status {login_page.status_code}).", "WARN")
            return False

        # Ambil CSRF token dari <meta name="csrf-token" content="...">
        m = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', login_page.text)
        if not m:
            log("   ⚠️ Gak nemu CSRF token di halaman login.", "WARN")
            return False
        csrf_token = m.group(1)

        # Ambil Inertia asset "version" dari atribut data-page="{...json...}"
        # yang ditanam Laravel di halaman. WAJIB dikirim balik sebagai header
        # X-Inertia-Version di POST /login -- kalau nggak ada/nggak cocok,
        # middleware Inertia langsung balas 409 Conflict TANPA sempat cek
        # email/password sama sekali (itu penyebab paling umum status 409
        # di endpoint ini, beda dari 422 yang emang salah kredensial).
        inertia_version = None
        m_page = re.search(r'data-page="([^"]+)"', login_page.text)
        if m_page:
            try:
                page_data = json.loads(html.unescape(m_page.group(1)))
                inertia_version = page_data.get("version")
            except Exception:
                pass
        if not inertia_version:
            log("   ⚠️ Gak nemu Inertia version di halaman login (lanjut tanpa itu, mungkin 409).", "WARN")

        # Step 2: POST /login (login.attempt route)
        login_data = {
            "_token": csrf_token,
            "email": email,
            "password": password,
        }
        headers_post = dict(HEADERS)
        headers_post["X-Requested-With"] = "XMLHttpRequest"
        headers_post["X-CSRF-TOKEN"] = csrf_token
        headers_post["X-Inertia"] = "true"
        if inertia_version:
            headers_post["X-Inertia-Version"] = inertia_version
        headers_post["Accept"] = "text/html, application/xhtml+xml"
        headers_post["Origin"] = "https://veinovel.com"
        headers_post["Referer"] = "https://veinovel.com/auth"

        # Laravel Inertia juga butuh X-XSRF-TOKEN dari cookie
        # Cookie-nya URL-encoded (base64 -> urlencode), harus di-decode dulu
        xsrf = SESSION.cookies.get("XSRF-TOKEN", "")
        if xsrf:
            from urllib.parse import unquote
            headers_post["X-XSRF-TOKEN"] = unquote(xsrf)

        res = SESSION.post("https://veinovel.com/login", data=login_data, headers=headers_post, timeout=30)

        if res.status_code in (200, 204, 302):
            # Verifikasi: cek kalau cookie session berubah / ada user
            cookies = SESSION.cookies.get_dict()
            if "veinovel_session" in cookies or any("session" in k.lower() for k in cookies):
                IS_LOGGED_IN = True
                log("✅ Login berhasil!")
                return True

        log(f"   ⚠️ Login gagal (status {res.status_code}).", "WARN")
        if res.status_code == 422:
            log("   ⚠️ Kemungkinan email/password salah.", "WARN")
        elif res.status_code == 409:
            log("   ⚠️ 409 = Inertia asset-version mismatch (X-Inertia-Version salah/kosong), "
                "BUKAN berarti email/password salah. Kalau masih 409 walau version udah "
                "dikirim, kemungkinan situs update versi asset tepat pas request jalan -- "
                "coba ulang.", "WARN")
    except Exception as e:
        log(f"   ⚠️ Error login: {e}", "WARN")

    IS_LOGGED_IN = False
    return False


if __name__ == "__main__":
    t_start_total = time.time()

    # Auto-login kalau env var tersedia
    if os.environ.get("VEI_EMAIL") or os.environ.get("VEI_PASSWORD"):
        login_veinovel()
    else:
        log("ℹ️ Gak login -- chapter premium bakal dilewati.", "WARN")

    entry_urls = load_urls(SERIES_URLS_FILE)
    log(f"📄 {len(entry_urls)} series ditemukan di '{SERIES_URLS_FILE}'")

    for idx, entry_url in enumerate(entry_urls, start=1):
        log(f"\n########## [{idx}/{len(entry_urls)}] {entry_url} ##########")
        try:
            series, all_chapters = get_series_and_chapters(entry_url)
        except Exception as e:
            log(f"  ⚠️ Dilewati, gagal baca series: {e}", "ERROR")
            STATS["series_gagal"] += 1
            STATS["errors"].append(f"{entry_url} -> {e}")
            continue

        safe_title = sanitize_filename(series['title'])
        log(f"📚 Series: {series['title']} ({len(all_chapters)} chapter total)")

        # Kelompokkan chapter per volume, urut sesuai chapter_number
        volumes = {}
        for cm in sorted(all_chapters, key=lambda c: c.get('chapter_number', 0)):
            vol = cm.get('volume', 1)
            volumes.setdefault(vol, []).append(cm)

        for vol_num in sorted(volumes):
            log(f"\n=== Volume {vol_num} ({len(volumes[vol_num])} bab) ===")
            output_name = os.path.join(OUTPUT_DIR, f"{safe_title} Vol {vol_num}_[{SITE_NAME}].pdf")
            build_pdf_for_volume(series, volumes[vol_num], output_name)

        STATS["series_ok"] += 1

    log("\n✅ SEMUA SERIES SELESAI DIPROSES!")
    print_summary(t_start_total)