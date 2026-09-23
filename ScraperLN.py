import io
import os
import re
import html
import time
import math
import datetime
import tempfile
import requests
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup, NavigableString
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ==========================================
# KONFIGURASI
# ==========================================

# MODE 1 (OTOMATIS PER-VOLUME) — PRIORITAS UTAMA:
# Taruh link halaman UTAMA/INDEX novel di file PAGE_URLS_FILE
# (default: "PageUrls.txt"), SATU LINK PER BARIS. Boleh lebih dari
# satu novel sekaligus — tiap baris = 1 novel, masing-masing bakal
# otomatis kepecah jadi 1 PDF per volume ("Volume 1", "Volume 2", dst
# yang ketemu di halaman index-nya).
#
# Mendukung EMPAT jenis situs, dideteksi otomatis (bukan hardcode domain,
# kecuali AgungX):
#   - Situs Blogger (mis. kaoritranslation.blogspot.com, dst)
#   - agungxnovel.my.id  (mis. https://agungxnovel.my.id/novel/<slug>)
#   - Situs bertema Madara/WordPress (mis. archtranslation.com/manga/<slug>/,
#     dan situs lain apapun yang pakai tema Madara — dideteksi otomatis
#     lewat meta generator halamannya, jadi gak perlu didaftar manual)
#   - Blog WordPress.com biasa (mis. cclawtranslations.home.blog, dan blog
#     *.home.blog / *.wordpress.com lain — juga dideteksi lewat meta
#     generator, bukan hardcode domain)
#
# Baris kosong atau yang diawali '#' diabaikan (bisa buat catatan).
#
# Contoh isi PageUrls.txt:
# https://kaoritranslation.blogspot.com/2025/12/zenmetsu-end-wo-shinimonogurui-de.html
# https://agungxnovel.my.id/novel/kimi-no-gachi
# https://archtranslation.com/manga/kuruna-megami-sama-to-issho-ni-sundara/
# https://cclawtranslations.home.blog/kibishii-onna-joushi-ga-koukousei-ni-modottara-ore-ni-dere-dere-suru-riyuu-ryoukataomoi-no-yaronaoshi-koukousei-seikatsu-toc/
PAGE_URLS_FILE = "PageUrls.txt"

# MODE 2 (MANUAL, SATU PDF GABUNGAN) — FALLBACK TERAKHIR:
# Dipakai HANYA kalau PAGE_URLS_FILE dan STARTURL_FILE dua-duanya kosong /
# gak ada. Baca semua link chapter dari file urls.txt, satu link per baris,
# digabung jadi satu PDF (perilaku script versi lama).
URLS_FILE = "urls.txt"

# MODE 3 (MANUAL START, KHUSUS MADARA) — buat kalau daftar chapter lewat
# AJAX di-block situsnya (butuh nonce, biasanya ditandai respons "0" +
# status 400) DAN kamu udah tau link chapter PERTAMA yang mau dimulai
# (mis. awal Volume 2). Taruh link itu di STARTURL_FILE, SATU LINK PER
# BARIS. Script bakal mulai dari situ terus ngikutin tombol "Next" sampai
# habis, dikelompokin otomatis per Volume (dari heading
# "... - Volume N - ..." di tiap halaman chapter), lalu tetap
# di-generate jadi PDF per volume seperti mode index otomatis.
#
# Mode ini jalan BARENGAN dengan PageUrls.txt (bukan gantiin), jadi bisa
# dipakai berdua sekaligus.
#
# Format baris (bagian judul custom opsional, pisah pakai '|'):
#   <url_chapter_awal>
#   <url_chapter_awal>|Judul Novel Custom
#
# Catatan: mode ini bakal COBA nebak halaman utama novelnya sendiri
# (dari pola URL '/manga/<slug>/') buat ambil cover & judul yang lebih
# akurat. Kalau gagal ditebak/situsnya beda pola, PDF tetap dibuat tapi
# tanpa halaman sampul.
STARTURL_FILE = "StartUrls.txt"

# SKIP_EXISTING_PDF:
# True -> kalau file PDF output-nya SUDAH ADA di folder Result/, volume
# itu dilewati total (gak fetch index/chapter sama sekali).
# Cocok buat lanjutin proses yang sempat berhenti/putus di tengah.
# False -> selalu scraping ulang & timpa PDF yang sudah ada.
SKIP_EXISTING_PDF = True

# ==========================================
# PDF DARK THEME
# ==========================================
# Format PDF bergaya gelap (background hitam, teks putih) dengan
# header page number & footer nama platform + judul chapter.
SITE_NAME = "LalaNovel"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'
}

junk_keywords = [
    'if you are not comfortable', 'jika kalian tidak nyaman',
    'related posts', 'trakteer', 'ko-fi', 'discord',
    'previous chapter', 'next chapter', 'toc', 'table of contents',
    # Titik dua sengaja DIHAPUS dari kata-kata ini: kadang labelnya
    # ("Penerjemah", "Proffreader") kepisah dari nama penerjemahnya
    # jadi tag terpisah di HTML, jadi teksnya cuma "Penerjemah" doang
    # tanpa ": Nama" di belakangnya.
    'penerjemah', 'proffreader', 'proofreader', 'editor:', 'translator',
    'dengarkan', 'menit baca',
    # Junk spesifik Blogger (Lintas Ninja Translation & sejenisnya) yang
    # nempel di akhir hampir tiap bab: TL Note Admin, blok "Baca juga:",
    # label "Tags:", tombol nav Blogger ("Lebih lama"/"Lebih baru" yg
    # ke-render jadi "Sebelumnya"/"Selanjutnya"), dll. Full-match (regex
    # anchor) di-handle di bawah oleh _JUNK_BLOGGER_PATTERNS, sisanya
    # pakai substring match di sini.
    'whatsapp.com/channel', 'tl note:', 'tl note ',
    'baca juga:', 'baca juga di', 'silakan cek',
    'sebelumnya', 'selanjutnya', 'lebih lama', 'lebih baru',
    'postingan komentar', 'berkomentar di kolom',
    'follow channel whatsapp',
    # Baris "• Jaku-Chara Tomozaki-kun ..." & "Facebook Twitter" di blok
    # "Baca juga:" + share button Blogger sering nongol sebagai baris
    # sendiri dengan karakter aneh (\xa0, &nbsp;) di depan, jadi regex
    # anchor ^ di _JUNK_BLOGGER_PATTERNS gak selalu match. Substring di
    # sini sebagai jaring pengaman tambahan.
    'jaku-chara tomozaki-kun', 'facebook twitter',
    'support kami:', 'dukung kami:',
    # Symbol-symbol yang sering nongol di blogspot LN
    '★★★', '★★', '☆☆☆', '☆☆', '★★★★', '★★★★★',
    '★', '☆',
]

# Pola regex full-match buat blok junk Blogger yang sering multi-baris
# (TL Note Admin, blok "Tags:", dsb.). Dipakai setelah substring check
# di junk_keywords biar blok TL Note sepanjang berapa baris pun ke-skip
# utuh, bukan kepotong setengah.
_JUNK_BLOGGER_PATTERNS = [
    re.compile(r'^tl\s*note\s*:.*', re.IGNORECASE | re.DOTALL),
    re.compile(r'^\s*tags?\s*:?\s*$', re.IGNORECASE),
    re.compile(r'^\s*tags?\s*:\s*[\w\s,]+$', re.IGNORECASE),
    re.compile(r'^\s*facebook\s+twitter\s*$', re.IGNORECASE),
    re.compile(r'^\s*facebook\s*$', re.IGNORECASE),
    re.compile(r'^\s*twitter\s*$', re.IGNORECASE),
    re.compile(r'^\s*[\u2022\-\*\xa0\s]*jaku-chara[^\n]*$', re.IGNORECASE),
    re.compile(r'^\s*whatsapp\s*$', re.IGNORECASE),
    re.compile(r'^\s*pinterest\s*$', re.IGNORECASE),
    re.compile(r'^\s*reddit\s*$', re.IGNORECASE),
    re.compile(r'^\s*linkedin\s*$', re.IGNORECASE),
    re.compile(r'^\s*tumblr\s*$', re.IGNORECASE),
    re.compile(r'^\s*telegram\s*$', re.IGNORECASE),
    re.compile(r'^\s*email\s*$', re.IGNORECASE),
    re.compile(r'^\s*tampilkan\s+selengkapnya\s*$', re.IGNORECASE),
    re.compile(r'^\s*show\s+more\s*$', re.IGNORECASE),
    re.compile(r'^\s*share\s*$', re.IGNORECASE),
    re.compile(r'^\s*related\s+posts?\s*$', re.IGNORECASE),
    re.compile(r'^\s*posting\s+komentar\s*$', re.IGNORECASE),
    re.compile(r'^\s*komentar\s*$', re.IGNORECASE),
    re.compile(r'^\s*←?\s*sebelumnya\s+daftar\s+isi\s+selanjutnya\s*→?\s*$', re.IGNORECASE),
    re.compile(r'^\s*←\s*sebelumnya\s*$', re.IGNORECASE),
    re.compile(r'^\s*selanjutnya\s*→\s*$', re.IGNORECASE),
    re.compile(r'^\s*baca\s+juga\s*:.*', re.IGNORECASE | re.DOTALL),
    re.compile(r'^\s*baca\s+juga\s+dalam\s+bahasa\s+lain\s*:.*', re.IGNORECASE | re.DOTALL),
    re.compile(r'^\s*admin\s+kembali.*', re.IGNORECASE | re.DOTALL),
    re.compile(r'^\s*jangan\s+lupa\s+berkomentar.*', re.IGNORECASE | re.DOTALL),
    re.compile(r'^\s*nantikan\s+terus\s+terjemahan.*', re.IGNORECASE | re.DOTALL),
    re.compile(r'^\s*selamat\s+(pagi|siang|sore|malam|berlibur).*', re.IGNORECASE),
]

# Domain-domain yang dianggap "agungxnovel-style" (bukan Blogger).
AGUNGX_DOMAINS = ('agungxnovel.my.id',)

# Domain-domain "kdtnovels-style" -- situs custom (bukan tema Madara
# standar) yang halaman index-nya ("/series/<slug>/") sudah nampilin
# SEMUA link chapter langsung di HTML dengan label "Vol. X Ch. Y ..."
# (gak perlu AJAX kayak Madara).
KDTNOVELS_DOMAINS = ('kdtnovels.net',)

# Domain-domain Luminare Translations (Yarnovel theme)
LUMINARE_DOMAINS = ('luminaretranslations.com',)

# Domain-domain WorldNovel (Next.js + REST API)
WORLDNOVEL_DOMAINS = ('worldnovel.my.id',)

# Domain-domain StorySeedling (Laravel + Livewire + font obfuscation)
STORYSEEDLING_DOMAINS = ('storyseedling.com',)

# Batas aman auto-crawl "Next" buat mode Madara, biar gak infinite loop
# kalau ada bug/redirect aneh.
MADARA_MAX_CHAPTERS = 3000

# ==========================================
# FONT UNICODE (WAJIB, biar karakter aneh gak jadi "??")
# ==========================================
# Nama family internal yang dipakai di semua pdf.set_font()/add_font()
# di bawah. Ini cuma label, gak perlu sama dengan nama asli font-nya --
# jadi ganti font (misal ke serif) TIDAK perlu ubah pemanggilan
# set_font() di tempat lain, cukup ganti FONT_REGULAR/FONT_BOLD di
# bawah ini supaya nunjuk ke file .ttf font serif pilihanmu.
FONT_FAMILY = "NovelFont"

FONT_DIR = "fonts"
# Default sekarang nunjuk ke font SERIF unicode (mis. Liberation Serif
# atau Noto Serif), biar hasilnya mirip contoh "Heroine Yandere" (serif),
# bukan sans-serif DejaVuSans yang lama. Taruh file .ttf-nya di folder
# 'fonts/' dengan nama persis di bawah ini (atau ubah nama filenya di
# sini biar cocok sama file yang kamu punya).
FONT_REGULAR = os.path.join(FONT_DIR, "NovelSerif-Regular.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "NovelSerif-Bold.ttf")

# Font CJK OPSIONAL. Definisi + subset generator udah di blok atas
# (baris ~125). Di sini cuma validasi font latin wajib ada + trigger
# pembuatan subset kalau font CJK tersedia.
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

# ==========================================
# LOGGING
# ==========================================
_LOG_TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = os.path.join(LOGS_DIR, f"log_{_LOG_TS}.txt")

STATS = {
    "novel_ok": 0,
    "novel_gagal": 0,
    "volume_ok": 0,
    "volume_skip": 0,
    "chapter_ok": 0,
    "chapter_gagal": 0,
    "gambar_ok": 0,
    "gambar_gagal": 0,
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
    _cover_done = False

    def add_page(self, *args, **kwargs):
        super().add_page(*args, **kwargs)
        self.set_page_background((0, 0, 0))
        self.set_text_color(255, 255, 255)

    def header(self):
        if self.page_no() <= 1:
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
        """Potong `text` (kalau perlu) supaya muat di `max_width` (mm) pas
        dirender dengan font yang lagi aktif, ditambah '...' di akhir kalau
        emang kepotong. Diukur dari lebar asli teksnya (get_string_width),
        BUKAN dari jumlah karakter -- jadi gak overlap/numpuk kalau
        hurufnya lebar, dan gak sia-sia kepotong kepagian kalau hurufnya
        sempit."""
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

    def footer(self):
        if self.page_no() <= 1:
            return
        # Referensi asli pakai font dekoratif "Britannic Bold" khusus buat
        # bar ini (beda dari body font). Kalau kamu taruh file ttf-nya di
        # fonts/ dan add_font sebagai "FooterFont", ganti FONT_FAMILY di
        # bawah ini jadi "FooterFont". Selama itu belum ada, dipakai bold
        # dari FONT_FAMILY biasa sebagai fallback.
        usable_w = self.w - self.l_margin - self.r_margin
        half_w = usable_w / 2
        # Sisain sedikit jarak (2mm) dari batas tengah biar judul gak
        # mepet/numpuk sama SITE_NAME di sebelah kanan.
        title_max_w = half_w - 2
        title_text = clean_unicode(self._chapter_title)
        # Judul PANJANG: coba font makin kecil dulu (14 -> 8) biar tetap 1
        # baris utuh; kalau di ukuran terkecil pun masih kepanjangan, baru
        # dipecah jadi 2 baris (bukan dipotong "...") biar judulnya tetap
        # kebaca lengkap.
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
        # SITE_NAME dibikin center-vertikal ke seluruh tinggi band (yang
        # sekarang bisa lebih tinggi kalau judulnya kepecah 2 baris).
        self.set_font(FONT_FAMILY, "B", 14)
        self.set_xy(self.l_margin + half_w, band_top)
        self.cell(half_w, band_h, SITE_NAME, align='R')
        self.set_text_color(255, 255, 255)

    def _fit_title_lines(self, text, max_width, max_font=14, min_font=8, font_style="B"):
        """Cari ukuran font terbesar (14 turun sampai 8) yang bikin `text`
        muat dalam SATU baris selebar `max_width`. Kalau di font terkecil
        pun tetap kepanjangan buat 1 baris, dipecah jadi 2 baris (per kata,
        greedy) di font terkecil itu -- baris kedua baru dipotong + '...'
        (lewat _fit_text_to_width) kalau ternyata MASIH kepanjangan juga.
        Balikin (font_size, [daftar baris])."""
        for size in range(max_font, min_font - 1, -1):
            self.set_font(FONT_FAMILY, font_style, size)
            if self.get_string_width(text) <= max_width:
                return size, [text]

        # Gak muat 1 baris walau udah di font terkecil -> pecah jadi 2 baris.
        self.set_font(FONT_FAMILY, font_style, min_font)
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
            # Satu kata pertama aja udah kepanjangan sendirian -> paksa
            # muat sebagian kata itu biar baris pertama gak kosong.
            line1 = self._fit_text_to_width(words[0], max_width)
            i = 1
        line2 = " ".join(words[i:]).strip()
        if not line2:
            return min_font, [line1]
        line2 = self._fit_text_to_width(line2, max_width)
        return min_font, [line1, line2]

    def chapter_title(self, title):
        self.set_font(FONT_FAMILY, "B", 26)
        self.set_text_color(255, 255, 255)
        self.set_x(self.l_margin)
        self.multi_cell(0, 10, clean_unicode(title), align='C')
        self.ln(6)

    def _first_line_indent_prefix(self, indent_mm=12.7):
        space_w = self.get_string_width(" ")
        if space_w <= 0:
            return "    "
        n = max(1, round(indent_mm / space_w))
        return " " * n

    def chapter_body(self, text):
        self.set_font(FONT_FAMILY, "", 17)
        self.set_text_color(255, 255, 255)
        paragraphs = text.strip().split("\r\n")
        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue
            self.set_x(self.l_margin)
            if i == 0:
                body_text = clean_unicode(para)
            else:
                body_text = self._first_line_indent_prefix() + clean_unicode(para)
            # align='L' (bukan 'J'): kalau di-justify, spasi indentasi buatan
            # di depan paragraf ikut diregangkan oleh mesin justify FPDF pada
            # baris yang BUKAN baris terakhir paragraf -> indentasi jadi
            # kelihatan beda-beda lebar antar paragraf (paragraf 1 baris vs
            # paragraf yang kepecah jadi >1 baris kena perlakuan beda).
            self.multi_cell(0, 6.9, body_text, align='L')
            self.ln(2)

# ==========================================
# ==========================================
# HELPER UMUM
# ==========================================
def clean_unicode(text):
    if not text:
        return ""
    replacements = {
        '\u00a0': ' ',
        '\u200b': '',
        # CJK punctuation -> ASCII bracket. Glyph 『 』 【 】 「 』 sering
        # gak ada di font Latin/serif biasa (termasuk font default
        # scraper ini), dan fpdf2 di-skip senyap kalau font-nya gak
        # punya glyph -> karakter hilang dari PDF. Replace ke [ ] yang
        # hampir pasti ADA di font apa pun, jadi konteks "tanda kutip
        # pesan teks" masih kebaca buat novel Indo.
    }
    replacements.update(_CJK_PUNCT_TO_ASCII)
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    return text


# Karakter CJK punctuation yang sering dipake di LN terjemahan Kaori
# sebagai tanda kutip pesan teks (chat/SMS): 『...』 (white corner
# brackets) dan 「...」 (raised corner brackets). Glyph karakter-karakter
# ini biasanya TIDAK ada di font Latin/serif (termasuk font default
# scraper ini), jadi kalau diterusin mentah ke fpdf2 bakal di-skip
# senyap dan gak nongol di PDF. Ganti ke ASCII bracket [ ] yang hampir
# pasti ada di font apa pun -- masih kebaca sebagai "tanda kutip pesan"
# dan visual konsisten buat novel Indo yang gak pake tipografi Jepang.
# Penugasan lain untuk karakter punctuation CJK ada di dalam
# clean_unicode() di bawah.
_CJK_PUNCT_TO_ASCII = {
    '\u300e': '[',  # 『 -> [
    '\u300f': ']',  # 』 -> ]
    '\u300c': '[',  # 「 -> [
    '\u300d': ']',  # 」 -> ]
}


def sanitize_filename(name):
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name or "Novel"


def fix_doubled_url(href):
    """Kadang link chapter di halaman index situs sumber (ini murni salah
    input dari sisi ADMIN situsnya, bukan bug di scraper) ke-double persis
    jadi satu string tanpa pemisah, misal:

        https://situs.com/x.htmlhttps://situs.com/x.html

    Fungsi ini mendeteksi pola "URL diikuti pengulangan dirinya sendiri"
    dan motongnya balik jadi satu URL aja. Kalau href-nya normal (gak
    ke-double), dikembalikan apa adanya tanpa diubah."""
    if not href:
        return href

    # Kasus paling umum: seluruh string persis 2x lipat isi yang sama
    # (panjang genap, separuh pertama == separuh kedua).
    n = len(href)
    if n % 2 == 0:
        half = n // 2
        first, second = href[:half], href[half:]
        if first == second and re.match(r'^https?://', first):
            return first

    # Fallback: cari kemunculan KEDUA dari "http://" atau "https://" di
    # tengah string (skip beberapa karakter pertama biar gak nemu balik
    # ke skema di posisi 0), lalu cek apakah bagian sebelum & sesudah titik
    # itu persis sama -> kalau iya, itu tandanya URL-nya beneran ke-double.
    m = re.search(r'https?://', href[8:])
    if m:
        split_at = m.start() + 8
        first_part, second_part = href[:split_at], href[split_at:]
        if first_part == second_part:
            return first_part

    return href


def fetch_url(url):
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.encoding = 'utf-8'
    return res


def fetch_image(src_url, referer=None):
    headers = HEADERS
    if referer:
        # Beberapa CDN (mis. yang dipakai KDTNovels) nolak request gambar
        # kalau gak ada header Referer yang cocok (proteksi hotlink) ->
        # balikin status 403 meski URL-nya valid. Kirim Referer = halaman
        # asal gambar itu ditemukan buat ngakalin ini.
        headers = dict(HEADERS)
        headers['Referer'] = referer
    try:
        img_res = requests.get(src_url, headers=headers, timeout=20)
        if img_res.status_code == 200:
            STATS["gambar_ok"] += 1
            return io.BytesIO(img_res.content)
        log(f"   ⚠️ Gambar status {img_res.status_code} ({src_url[:50]}...)", "WARN")
    except Exception as e:
        log(f"   ⚠️ Gagal mengunduh gambar ({src_url[:50]}...): {e}", "WARN")
    STATS["gambar_gagal"] += 1
    return None


def is_agungx(url_or_domain):
    domain = urlparse(url_or_domain).netloc or url_or_domain
    return any(d in domain for d in AGUNGX_DOMAINS)


def is_kdtnovels(url_or_domain):
    domain = urlparse(url_or_domain).netloc or url_or_domain
    return any(d in domain for d in KDTNOVELS_DOMAINS)


def is_luminare(url_or_domain):
    domain = urlparse(url_or_domain).netloc or url_or_domain
    return any(d in domain for d in LUMINARE_DOMAINS)


def is_worldnovel(url_or_domain):
    domain = urlparse(url_or_domain).netloc or url_or_domain
    return any(d in domain for d in WORLDNOVEL_DOMAINS)


def is_storyseedling(url_or_domain):
    domain = urlparse(url_or_domain).netloc or url_or_domain
    return any(d in domain for d in STORYSEEDLING_DOMAINS)


def is_yarnovel(soup):
    body = soup.find('body')
    if body and body.get('class'):
        return any('yarnovel' in c for c in body.get('class'))
    return False


def is_madara(soup):
    """Deteksi tema Madara (dipakai banyak situs manga/novel WordPress,
    bukan cuma satu domain tertentu) lewat meta generator-nya."""
    gen = soup.find('meta', attrs={'name': 'generator'})
    if gen and gen.get('content') and 'madara' in gen['content'].lower():
        return True
    return False


def is_wpcom(soup):
    """Deteksi blog WordPress.com biasa (mis. *.home.blog) lewat meta
    generator-nya -- bukan Madara, cuma blog polos."""
    gen = soup.find('meta', attrs={'name': 'generator'})
    if gen and gen.get('content') and 'wordpress.com' in gen['content'].lower():
        return True
    return False


def is_generic_wp(soup):
    """Deteksi WordPress self-hosted umum (bukan WP.com, bukan Madara).
    Cek meta generator 'WordPress' tapi bukan 'WordPress.com' dan bukan Madara."""
    gen = soup.find('meta', attrs={'name': 'generator'})
    if gen and gen.get('content'):
        content = gen['content'].lower()
        if 'wordpress' in content and 'wordpress.com' not in content and 'madara' not in content:
            return True
    # Fallback: cek wp-content di link/script
    if soup.find('link', href=re.compile(r'wp-content|wp-includes')):
        return True
    if soup.find('script', src=re.compile(r'wp-content|wp-includes')):
        return True
    return False


def get_og_image(soup):
    og_image = soup.find('meta', attrs={'property': 'og:image'})
    if og_image and og_image.get('content'):
        return og_image['content']
    return None


def is_generic_wp_index(soup):
    """Cek apakah halaman ini adalah halaman index/TOC novel (bukan chapter).
    Biasanya halaman index punya daftar link chapter dengan pola /chapter- atau /volume-"""
    links = soup.find_all('a', href=re.compile(r'/chapter-|/volume-'))
    return len(links) >= 3


def get_volumes_from_toc_generic_wp(toc_url, soup):
    """Parse halaman index novel WordPress self-hosted."""
    # Ambil judul dari h1 atau og:title
    story_title = "Novel"
    h1 = soup.find('h1')
    if h1:
        story_title = h1.get_text(strip=True)
    else:
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.get('content'):
            story_title = og_title['content'].strip()

    # Kumpulkan semua link chapter
    domain = urlparse(toc_url).netloc
    volumes = {}
    current_vol = 1
    seen = set()

    # Cari link chapter dengan pola /chapter- atau /volume-
    for a in soup.find_all('a', href=True):
        href = fix_doubled_url(a.get('href'))
        if not href or href in seen:
            continue
        link_domain = urlparse(href).netloc
        if link_domain and link_domain != domain:
            continue
        # Pola chapter: /chapter-1-, /chapter-2-, /volume-1/chapter-1-
        if not re.search(r'(/chapter-\d+|/volume-\d+/chapter-)', href):
            continue
        
        label = a.get_text(strip=True)
        if not label or len(label) < 3:
            continue
        
        # Coba deteksi volume dari URL
        vol_match = re.search(r'/volume-(\d+)/', href)
        if vol_match:
            current_vol = int(vol_match.group(1))
        else:
            # Coba deteksi dari label
            vol_label = re.search(r'volume\s*(\d+)', label, re.IGNORECASE)
            if vol_label:
                current_vol = int(vol_label.group(1))
        
        seen.add(href)
        volumes.setdefault(current_vol, []).append((href, label))

    if not volumes:
        raise RuntimeError("Tidak menemukan link chapter di halaman index WordPress ini.")

    return story_title, volumes


def scrape_chapter_generic_wp(url, soup):
    """Scrape chapter dari WordPress self-hosted."""
    # Cari konten di entry-content atau post-content atau main
    container = None
    for cls in ['entry-content', 'post-content', 'post-body', 'content', 'article-content']:
        container = soup.find('div', class_=cls)
        if container:
            break
    if not container:
        # Fallback: cari di main atau article
        container = soup.find('main') or soup.find('article')
    if not container:
        # Fallback: cari div dengan class mengandung 'post'
        container = soup.find('div', class_=re.compile(r'post-\d+'))

    chapter_title = "Chapter"
    elements = []

    if container:
        # Ambil judul dari h1
        h1 = soup.find('h1')
        if h1:
            chapter_title = h1.get_text(strip=True)

        # Parse konten
        for tag in container.find_all(['p', 'img']):
            if tag.name == 'img':
                src = tag.get('src')
                if src:
                    elements.append({'type': 'img', 'src': urljoin(url, src), 'referer': url})
            else:
                text = tag.get_text(strip=True)
                if not text:
                    continue
                # Skip nav/junk
                text_lower = text.lower()
                if any(junk in text_lower for junk in junk_keywords):
                    continue
                if re.search(r'(previous chapter|next chapter|table of contents)', text_lower):
                    continue
                if len(text) < 3:
                    continue
                if not elements or elements[-1].get('value') != text:
                    elements.append({'type': 'text', 'value': text})

    return chapter_title, elements


def normalize_img_src(src):
    """Samakan URL gambar Blogger yang sebenarnya SAMA tapi beda ukuran,
    biar deteksi 'gambar cover berulang' gak gagal cuma gara-gara beda
    parameter ukuran. Dipakai khusus untuk mode Blogger."""
    if not src:
        return src
    src = src.split('?')[0]
    last = src.rstrip('/').split('/')[-1]
    if '=' in last:
        last = last.split('=')[0]
    return last.lower()


def title_from_slug(url):
    path = urlparse(url).path
    slug = os.path.basename(path)
    slug = re.sub(r'\.html?$', '', slug, flags=re.IGNORECASE)
    slug = re.sub(r'_\d+$', '', slug)
    words = [w for w in slug.split('-') if w]
    return ' '.join(w.capitalize() for w in words)


def load_urls(path, required=True):
    """required=True -> file kosong/gak ada dianggap error (dipakai utk
    urls.txt sebagai mode fallback terakhir).
    required=False -> file kosong/gak ada cuma balikin list kosong []
    (dipakai utk PageUrls.txt, biar bisa dicek terus fallback ke urls.txt
    tanpa bikin script crash)."""
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(
                f"File '{path}' tidak ditemukan. Buat file '{path}' berisi "
                f"link chapter, satu link per baris."
            )
        return []
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    if not urls and required:
        raise ValueError(f"File '{path}' kosong, tidak ada link yang bisa diproses.")
    return urls


def load_start_entries(path):
    """Baca STARTURL_FILE. Tiap baris: '<url>' atau '<url>|<judul custom>'.
    File gak ada / kosong -> balikin list kosong (bukan error), biar bisa
    dicek terus lanjut ke mode lain tanpa crash."""
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if '|' in line:
                url_part, title_part = line.split('|', 1)
                url_part = url_part.strip()
                title_part = title_part.strip()
                if url_part:
                    entries.append((url_part, title_part or None))
            else:
                entries.append((line, None))
    return entries


# ==========================================
# MODE OTOMATIS (BLOGGER): PARSING INDEX -> PER VOLUME
# ==========================================
def get_story_title_blogger(soup):
    h1 = soup.find('h1', class_='post-title') or soup.find('h1')
    title = h1.get_text(strip=True) if h1 else "Novel"
    title = re.sub(r'\[LN\]\s*Bahasa Indonesia', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\[ENG\]\s*', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*Volume\s*\d+\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*~\s*$', '', title)
    return title.strip()


def _is_bold_chapter_link(a_tag):
    if a_tag.parent is not None and a_tag.parent.name in ('b', 'strong'):
        return True
    if a_tag.find(['b', 'strong'], recursive=False) is not None:
        return True
    return False


def get_volumes_from_toc_blogger(toc_url, soup):
    story_title = get_story_title_blogger(soup)
    post_body = soup.find('div', class_=re.compile(r'post-body|entry-content'))
    if not post_body:
        raise RuntimeError("Tidak menemukan konten utama (post-body) di halaman index.")

    volumes = {}
    current_vol = None
    seen = set()

    # Perluas tag yang di-scan buat penanda "Volume N": beberapa post Kaori
    # TL nulis "Volume 2", "Volume 3" dst sebagai TEKS POLOS (gak dibold,
    # gak pakai heading tag) -- beda sama "Volume 1" yang biasanya di-bold.
    # Kalau cuma discan dari b/strong/h2-h4 doang, heading yang polos ini
    # bakal kelewat -> current_vol nyangkut di volume sebelumnya, dan SEMUA
    # chapter volume berikutnya (termasuk volume-volume sesudahnya lagi)
    # keliru ke-lump jadi satu grup sama volume sebelumnya. Makanya di sini
    # ikut discan tag 'p'/'div' juga, dengan syarat teksnya PERSIS "Volume
    # N" doang (regex full-match), biar gak salah kena paragraf cerita yang
    # kebetulan nyebut kata "volume" di tengah kalimat.
    # Catatan: regex pakai vol\w* (bukan "volume") biar typo umum kayak
    # "Voloume", "Volme", "Voluem" dst tetap ke-detek.
    for tag in post_body.find_all(['b', 'strong', 'h2', 'h3', 'h4', 'p', 'div', 'a']):
        if tag.name != 'a':
            text = tag.get_text(strip=True)
            vol_match = re.match(r'^(?:vol\w*|jilid)\s*(\d+)\s*(?:\s+END)?$', text, re.IGNORECASE)
            if vol_match:
                current_vol = int(vol_match.group(1))
                volumes.setdefault(current_vol, [])
            continue

        href = fix_doubled_url(tag.get('href'))
        if not href or current_vol is None or href in seen:
            continue
        # Sama kayak jalur fallback di bawah: sengaja TIDAK mensyaratkan
        # _is_bold_chapter_link() -- banyak post Kaori TL nulis link
        # chapter-nya polos (gak dibold) di bawah heading "Volume N".
        # Filter keamanannya pakai regex keyword label di bawah.
        label = tag.get_text(strip=True)
        if not re.search(
            r'chapter|bab|prolog|prologue|epilog|epilogue|ilustrasi|illustrasi|illustrations?|'
            r'afterword|extra|bonus|kata penutup|episode|eps\b|part\s*\d',
            label, re.IGNORECASE
        ):
            continue

        seen.add(href)
        volumes[current_vol].append((href, label))

    if not volumes:
        log("   ℹ️ Tidak ada penanda 'Volume N', coba anggap 1 volume tunggal...", "WARN")
        fallback_links = []
        for a in post_body.find_all('a'):
            href = fix_doubled_url(a.get('href'))
            if not href or href in seen:
                continue
            # Catatan: sengaja TIDAK mensyaratkan _is_bold_chapter_link()
            # di sini. Beberapa blog nulis link chapter-nya polos (gak
            # dibold) khususnya kalau novelnya cuma 1 volume / gak punya
            # heading "Volume N" sama sekali. Filter keamanannya cukup
            # dari regex keyword label di bawah + scope ke post_body.
            label = a.get_text(strip=True)
            if re.search(
                r'chapter|bab|prolog|prologue|epilog|epilogue|ilustrasi|illustrasi|illustrations?|'
                r'afterword|extra|bonus|kata penutup|episode|eps\b|part\s*\d',
                label, re.IGNORECASE
            ):
                seen.add(href)
                fallback_links.append((href, label))
        if fallback_links:
            volumes[1] = fallback_links

    if not volumes:
        raise RuntimeError(
            "Tidak menemukan pola 'Volume 1', 'Volume 2', dst maupun daftar "
            "link chapter di halaman index. Mungkin struktur halamannya "
            "beda banget, cek manual / pakai mode urls.txt."
        )

    return story_title, volumes


# ==========================================
# MODE OTOMATIS (AGUNGXNOVEL.MY.ID): PARSING INDEX -> PER VOLUME
# ==========================================
def get_story_title_agungx(soup):
    h1 = soup.find('h1')
    if h1:
        return h1.get_text(strip=True)
    og_title = soup.find('meta', attrs={'property': 'og:title'})
    if og_title and og_title.get('content'):
        return re.split(r'\s*-\s*Baca Novel', og_title['content'])[0].strip()
    return "Novel"


def get_volumes_from_toc_agungx(toc_url, soup):
    story_title = get_story_title_agungx(soup)
    domain = urlparse(toc_url).netloc
    scheme = urlparse(toc_url).scheme

    # Beberapa halaman novel AgungX punya tombol pintasan "Baca Sekarang"
    # yang link-nya SAMA PERSIS ke chapter terbaru (/chapter/<id>), tapi
    # muncul di HTML SEBELUM daftar chapter lengkap dan labelnya gak
    # memuat "Volume N" (cuma judul chapter polos). Kalau dibiarin
    # "kemunculan pertama menang", tombol pintasan ini yang kepake ->
    # chapter-nya gagal ke-detect volume-nya (default Volume 1) padahal
    # aslinya Volume lain, dan nyempil di urutan paling awal.
    #
    # Solusi: per href, simpan entri PERTAMA dulu, tapi kalau nanti
    # ketemu kemunculan LAIN dari href yang sama dan label-nya beneran
    # memuat "Volume N" sementara entri yang tersimpan belum, timpa
    # dengan yang lebih akurat itu.
    entries_by_href = {}  # href -> {'label': str, 'vol_num': int|None}

    # Daftar chapter ditandai lewat link ke /chapter/<id>, labelnya sendiri
    # sudah memuat "Volume N ..." jadi gak perlu ngelacak heading terpisah.
    for a in soup.find_all('a', href=re.compile(r'/chapter/\d+')):
        href = fix_doubled_url(a.get('href'))
        if not href:
            continue
        full_url = urljoin(f"{scheme}://{domain}", href)
        label = a.get_text(strip=True)
        if not label:
            continue

        vol_match = re.search(r'volume\s*(\d+)', label, re.IGNORECASE)
        vol_num = int(vol_match.group(1)) if vol_match else None

        existing = entries_by_href.get(full_url)
        if existing is None:
            entries_by_href[full_url] = {'label': label, 'vol_num': vol_num}
        elif existing['vol_num'] is None and vol_num is not None:
            # Entri lama gak ada info volume-nya, yang baru ada -> timpa.
            entries_by_href[full_url] = {'label': label, 'vol_num': vol_num}

    volumes = {}
    for full_url, info in entries_by_href.items():
        vol_num = info['vol_num'] if info['vol_num'] is not None else 1
        volumes.setdefault(vol_num, []).append((full_url, info['label']))

    # Urutkan tiap volume berdasarkan ID chapter (angka di '/chapter/<id>'),
    # BUKAN urutan kemunculan pertama di HTML. Soalnya tombol pintasan
    # "Baca Sekarang" ke chapter terbaru muncul duluan di HTML (sebelum
    # daftar chapter lengkap) -> kalau dipertahankan urutan kemunculan,
    # chapter itu nyempil di urutan paling awal padahal harusnya paling
    # akhir. ID chapter biasanya naik sesuai urutan terbit/baca, jadi
    # jauh lebih bisa diandalkan buat urutan yang benar.
    def _chapter_id(entry):
        m = re.search(r'/chapter/(\d+)', entry[0])
        return int(m.group(1)) if m else 0

    for vol_num in volumes:
        volumes[vol_num].sort(key=_chapter_id)

    if not volumes:
        raise RuntimeError(
            "Tidak menemukan link chapter (/chapter/<id>) di halaman novel AgungX."
        )

    return story_title, volumes


# ==========================================
# MODE OTOMATIS (MADARA / WORDPRESS TEMA MADARA): PARSING INDEX -> PER VOLUME
# ==========================================
# Madara gak nampilin daftar chapter lengkap di HTML statis awal — itu
# dimuat lewat AJAX ke wp-admin/admin-ajax.php (action=manga_get_chapters)
# setelah halaman kebuka di browser. Di banyak situs endpoint ini
# dilindungi nonce (respons "0" + status 400 kalau dipanggil langsung
# tanpa token), jadi gak selalu bisa ditembak dari sini. STRATEGI UTAMA:
# tetap coba AJAX dulu (kalau situsnya kebetulan gak proteksi nonce).
# FALLBACK: nyusurin manual lewat tombol "Next" di tiap halaman chapter,
# mulai dari "Read First". Kalau "Read First" pun gak ada / kamu udah
# punya link chapter awal sendiri, pakai STARTURL_FILE (mode 3, lihat
# konfigurasi di atas) buat mulai crawl "Next" langsung dari situ.
def _find_link_by_text(soup, patterns):
    """Cari <a> pertama yang teksnya cocok salah satu regex di `patterns`."""
    for a in soup.find_all('a'):
        text = a.get_text(strip=True)
        if not text:
            continue
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return a
    return None


def _get_madara_post_id(soup):
    """Cari ID post WordPress buat manga ini. Madara biasanya nyimpennya di
    atribut data-id elemen holder daftar chapter (mis. #manga-chapters-holder)."""
    holder = soup.find(id=re.compile(r'manga-chapters-holder'))
    if holder and holder.get('data-id'):
        log(f"   🐞 [debug] post id dari #manga-chapters-holder: {holder['data-id']}")
        return holder['data-id']

    # Debug: dump semua elemen yang punya data-id, biar kelihatan kandidat
    # lain kalau strategi utama di atas gak nemu apa-apa.
    all_data_id_els = soup.find_all(attrs={'data-id': True})
    if all_data_id_els:
        log(f"   🐞 [debug] gak nemu #manga-chapters-holder, tapi ada {len(all_data_id_els)} elemen beratribut data-id:")
        for el in all_data_id_els[:10]:
            log(f"      <{el.name} id={el.get('id')!r} class={el.get('class')!r} data-id={el.get('data-id')!r}>")
    else:
        log("   🐞 [debug] gak ada elemen apapun yang punya atribut data-id di halaman ini.")

    el = soup.find(attrs={'data-id': True})
    if el and el.get('data-id') and el['data-id'].isdigit():
        return el['data-id']
    return None


def _fetch_madara_ajax_chapter_list(base_url, post_id):
    ajax_url = urljoin(base_url, "/wp-admin/admin-ajax.php")
    try:
        res = requests.post(
            ajax_url,
            data={"action": "manga_get_chapters", "manga": post_id},
            headers=HEADERS,
            timeout=20,
        )
    except Exception as e:
        log(f"   ⚠️ AJAX Madara gagal koneksi: {e}", "WARN")
        return None
    log(f"   🐞 [debug] AJAX POST {ajax_url} -> status {res.status_code}, panjang respons {len(res.text)} char")
    snippet = res.text.strip().replace('\n', ' ')[:500]
    log(f"   🐞 [debug] cuplikan respons AJAX: {snippet!r}")
    if res.status_code != 200 or not res.text.strip():
        return None
    return BeautifulSoup(res.text, 'html.parser')


def _parse_madara_ajax_chapters(ajax_soup, base_url):
    """Parse hasil AJAX manga_get_chapters. Struktur umumnya:
    <ul class="main version-chap ...">
      <li class="parent has-child"><a>Volume 1</a><ul class="sub-chap-list">
        <li class="wp-manga-chapter"><a href="...">Bab 1</a>...</li>
        ...
      </ul></li>
      ...
    </ul>
    Kalau novelnya gak dibagi per-volume, semua <li> chapter langsung
    jadi anak <ul class="main..."> tanpa sub-list -> dianggap Volume 1.
    Urutan aslinya dari AJAX biasanya TERBARU -> TERLAMA, jadi tiap
    list chapter dibalik supaya urut baca dari awal.
    """
    main_ul = ajax_soup.find('ul', class_=re.compile(r'\bmain\b')) or ajax_soup.find('ul')
    if main_ul is None:
        all_lis = ajax_soup.find_all('li')
        log(f"   🐞 [debug] gak nemu tag <ul> apapun di respons AJAX. Total <li> di respons: {len(all_lis)}")
        return {}

    volumes = {}
    vol_counter = 0
    flat_chapters = []

    for li in main_ul.find_all('li', recursive=False):
        sub_ul = li.find('ul', recursive=False)
        if sub_ul is not None:
            vol_counter += 1
            header_a = li.find('a', recursive=False)
            vol_text = header_a.get_text(strip=True) if header_a else f"Volume {vol_counter}"
            vol_match = re.search(r'volume\s*(\d+)', vol_text, re.IGNORECASE)
            vol_num = int(vol_match.group(1)) if vol_match else vol_counter

            chap_list = []
            for sub_li in sub_ul.find_all('li'):
                a = sub_li.find('a')
                href = fix_doubled_url(a.get('href')) if a else None
                if href:
                    chap_list.append((urljoin(base_url, href), a.get_text(strip=True)))
            chap_list.reverse()  # AJAX: terbaru -> terlama
            volumes[vol_num] = chap_list
        else:
            a = li.find('a')
            href = fix_doubled_url(a.get('href')) if a else None
            if href:
                flat_chapters.append((urljoin(base_url, href), a.get_text(strip=True)))

    if flat_chapters:
        flat_chapters.reverse()
        volumes.setdefault(1, [])
        volumes[1] = flat_chapters + volumes[1]  # jaga-jaga kalau campur

    return volumes


def _madara_crawl_next(start_url):
    """Mulai dari `start_url`, ikutin tombol 'Next' di tiap halaman chapter
    sampai habis (atau ketemu link yang udah dikunjungi / limit tercapai).
    Dipakai baik oleh fallback 'Read First' maupun mode manual
    STARTURL_FILE. Balikin dict {vol_num: [(url, label), ...]}."""
    domain = urlparse(start_url).netloc
    scheme = urlparse(start_url).scheme
    base = f"{scheme}://{domain}"

    volumes = {}
    visited = set()
    current_url = start_url
    count = 0

    while current_url and current_url not in visited and count < MADARA_MAX_CHAPTERS:
        visited.add(current_url)
        count += 1

        try:
            res = fetch_url(current_url)
        except Exception as e:
            log(f"   ⚠️ Gagal ambil chapter saat crawling ({current_url}): {e}", "WARN")
            break
        if res.status_code != 200:
            log(f"   ⚠️ Status {res.status_code} saat crawling ({current_url}), berhenti crawl.", "WARN")
            break

        cur_soup = BeautifulSoup(res.text, 'html.parser')
        cur_h1 = cur_soup.find('h1')
        h1_text = cur_h1.get_text(strip=True) if cur_h1 else ""

        vol_match = re.search(r'volume\s*(\d+)', h1_text, re.IGNORECASE)
        vol_num = int(vol_match.group(1)) if vol_match else 1

        label_match = re.search(r'-\s*volume\s*\d+\s*-\s*(.+)$', h1_text, re.IGNORECASE)
        label = label_match.group(1).strip() if label_match else h1_text

        volumes.setdefault(vol_num, []).append((current_url, label))

        if count % 5 == 0:
            log(f"   🔗 {count} chapter ditemukan lewat crawl Next... (terakhir: {label})")

        next_link = _find_link_by_text(cur_soup, [r'^next$'])
        next_href = fix_doubled_url(next_link.get('href')) if next_link else None
        current_url = urljoin(base, next_href) if next_href else None

    return volumes


def _get_volumes_from_toc_madara_next_crawl(toc_url, soup, story_title):
    read_first = _find_link_by_text(soup, [r'read\s*first'])
    read_first_href = fix_doubled_url(read_first.get('href')) if read_first else None
    if not read_first_href:
        raise RuntimeError(
            "Tidak menemukan tombol 'Read First' di halaman index Madara."
        )

    domain = urlparse(toc_url).netloc
    scheme = urlparse(toc_url).scheme
    base = f"{scheme}://{domain}"
    start_url = urljoin(base, read_first_href)

    volumes = _madara_crawl_next(start_url)
    if not volumes:
        raise RuntimeError("Gagal crawl chapter dari 'Read First' (mode Madara).")

    return story_title, volumes


def guess_story_root_url(chapter_url):
    """Coba tebak URL halaman UTAMA novel dari URL sebuah chapter Madara,
    dengan motong path balik ke pola umum '/manga/<slug>/' (2 segmen
    pertama). Dipakai di mode StartUrls buat nyari cover & judul yang
    lebih akurat, karena mode itu gak pernah mampir ke halaman index.
    Balikin None kalau polanya gak cocok."""
    parsed = urlparse(chapter_url)
    m = re.match(r'^(/[^/]+/[^/]+/)', parsed.path)
    if not m:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{m.group(1)}"


def guess_title_and_cover_from_root(root_url):
    """Ambil judul (H1) & cover (og:image) dari halaman utama novel.
    Balikin (title, cover_url) — salah satu/dua-duanya bisa None kalau
    gagal diambil."""
    try:
        res = fetch_url(root_url)
    except Exception:
        return None, None
    if res.status_code != 200:
        return None, None
    soup = BeautifulSoup(res.text, 'html.parser')
    h1 = soup.find('h1')
    title = h1.get_text(strip=True) if h1 else None
    cover = get_og_image(soup)
    return title, cover


def guess_cover_from_first_chapter(first_chapter_url):
    """Ambil URL gambar PERTAMA di halaman `first_chapter_url` (biasanya
    halaman ilustrasi awal volume). Dipakai buat cover per-volume di mode
    StartUrls, karena og:image novel selalu sama buat semua volume."""
    try:
        res = fetch_url(first_chapter_url)
    except Exception:
        return None
    if res.status_code != 200:
        return None
    soup = BeautifulSoup(res.text, 'html.parser')
    _, elements = scrape_chapter_madara(first_chapter_url, soup)
    for e in elements:
        if e['type'] == 'img':
            return e['src']
    return None


def guess_title_from_madara_chapter(start_url):
    """Coba tebak judul novel dari halaman chapter (dipakai mode
    STARTURL_FILE, karena di situ kita gak lewat halaman index dulu)."""
    try:
        res = fetch_url(start_url)
    except Exception:
        return "Novel"
    if res.status_code != 200:
        return "Novel"
    soup = BeautifulSoup(res.text, 'html.parser')
    h1 = soup.find('h1')
    h1_text = h1.get_text(strip=True) if h1 else "Novel"
    # Buang bagian "- Volume N - label..." di belakang biar dapet judul utama.
    title_only = re.split(r'\s*-\s*volume\s*\d+', h1_text, flags=re.IGNORECASE)[0].strip()
    return title_only or "Novel"


def get_volumes_from_toc_madara(toc_url, soup):
    h1 = soup.find('h1')
    story_title = h1.get_text(strip=True) if h1 else "Novel"

    domain = urlparse(toc_url).netloc
    scheme = urlparse(toc_url).scheme
    base = f"{scheme}://{domain}"

    post_id = _get_madara_post_id(soup)
    if post_id:
        log(f"   🔎 Coba ambil daftar chapter via AJAX Madara (post id {post_id})...")
        ajax_soup = _fetch_madara_ajax_chapter_list(base, post_id)
        if ajax_soup is not None:
            volumes = _parse_madara_ajax_chapters(ajax_soup, base)
            total = sum(len(v) for v in volumes.values())
            if total > 0:
                log(f"   ✅ AJAX berhasil, {total} chapter ditemukan lewat {len(volumes)} volume.")
                return story_title, volumes
        log("   ⚠️ AJAX kosong/gagal diparse (kemungkinan diblok nonce), fallback ke crawl manual via 'Next'...", "WARN")
    else:
        log("   ⚠️ Gak nemu post ID buat AJAX, fallback ke crawl manual via 'Next'...", "WARN")

    return _get_volumes_from_toc_madara_next_crawl(toc_url, soup, story_title)


# ==========================================
# MODE OTOMATIS (KDTNOVELS.NET): PARSING INDEX -> PER VOLUME
# ==========================================
# Halaman index KDTNovels ("/series/<slug>/") nampilin SEMUA link chapter
# langsung di HTML (gak lewat AJAX), dengan teks link berpola
# "Vol. X Ch. Y <Judul> <Tanggal>". Urutan tampilnya TERBARU -> TERLAMA,
# jadi kita gak boleh andalkan urutan tampil -- nomor volume & chapter
# diparse dari teks link itu sendiri lalu diurutkan ulang.
_KDT_VOLCHAP_RE = re.compile(r'Vol\.?\s*(\d+)\s*Ch\.?\s*([\d.]+)', re.IGNORECASE)


# Halaman index KDTNovels ("/series/<slug>/") nampilin SEMUA link chapter
# langsung di HTML (gak lewat AJAX), dengan teks link berpola
# "Vol. X Ch. Y <Judul> <Tanggal>". Urutan tampilnya TERBARU -> TERLAMA,
# jadi kita gak boleh andalkan urutan tampil -- nomor volume & chapter
# diparse dari teks link itu sendiri lalu diurutkan ulang.
_KDT_VOLCHAP_RE = re.compile(r'Vol\.?\s*(\d+)\s*Ch\.?\s*([\d.]+)', re.IGNORECASE)

# Tanggal rilis ("December 11, 2025") nempel LANGSUNG di belakang judul
# chapter tanpa pemisah di HTML-nya (beda <span>/<div> tanpa spasi) ->
# harus dibuang manual, bukan cuma dipisah spasi.
_KDT_DATE_SUFFIX_RE = re.compile(
    r'\s*(January|February|March|April|May|June|July|August|September|'
    r'October|November|December)\s+\d{1,2},\s*\d{4}\s*$',
    re.IGNORECASE
)


def get_story_title_kdtnovels(soup):
    h1 = soup.find('h1')
    if h1:
        return h1.get_text(strip=True)
    og_title = soup.find('meta', attrs={'property': 'og:title'})
    if og_title and og_title.get('content'):
        return re.split(r'\s*-\s*Light Novel', og_title['content'])[0].strip()
    return "Novel"


def get_volumes_from_toc_kdtnovels(toc_url, soup):
    story_title = get_story_title_kdtnovels(soup)
    domain = urlparse(toc_url).netloc

    raw = {}  # href -> (vol_num, chap_num_float, label)
    for a in soup.find_all('a'):
        href = fix_doubled_url(a.get('href'))
        if not href:
            continue
        if not href.startswith(('http://', 'https://')):
            href = urljoin(toc_url, href)
        # Pakai separator spasi: potongan "Vol. X Ch. Y", judul, & tanggal
        # ada di elemen/text-node terpisah tanpa spasi di HTML aslinya,
        # jadi tanpa separator ini bakal nempel jadi satu kata (mis.
        # "Ch. 0.5Illustrations").
        label = a.get_text(" ", strip=True)
        label = re.sub(r'\s+', ' ', label)
        if not label:
            continue
        m = _KDT_VOLCHAP_RE.search(label)
        if m:
            vol_num = int(m.group(1))
            chap_str = m.group(2)
        else:
            vol_from_url = re.search(r'vol[\.\-_ ]?(\d+)', href, re.IGNORECASE)
            chap_from_label = re.search(r'Ch\.?\s*([\d.]+)', label, re.IGNORECASE)
            if not (vol_from_url and chap_from_label):
                continue
            vol_num = int(vol_from_url.group(1))
            chap_str = chap_from_label.group(1)
        try:
            chap_num = float(chap_str)
        except ValueError:
            continue
        link_domain = urlparse(href).netloc
        if link_domain and link_domain != domain:
            continue

        if m:
            clean_label = label[m.end():].strip()
        else:
            chap_label_match = re.search(r'Ch\.?\s*[\d.]+\s*[:\-]?\s*(.*)', label, re.IGNORECASE)
            clean_label = chap_label_match.group(1).strip() if chap_label_match else label
        clean_label = _KDT_DATE_SUFFIX_RE.sub('', clean_label).strip()
        if not clean_label:
            clean_label = label

        # href yang sama bisa nongol dobel (mis. tombol pintasan "First
        # Chapter" / "New Chapter" di atas daftar utama) -- cukup dicatat
        # sekali aja.
        raw.setdefault(href, (vol_num, chap_num, clean_label))

    volumes = {}
    for href, (vol_num, chap_num, label) in raw.items():
        volumes.setdefault(vol_num, []).append((chap_num, href, label))

    for vol_num in volumes:
        volumes[vol_num].sort(key=lambda t: t[0])
        volumes[vol_num] = [(href, label) for _chap_num, href, label in volumes[vol_num]]

    if not volumes:
        raise RuntimeError(
            "Tidak menemukan link chapter berpola 'Vol. X Ch. Y' di halaman "
            "index KDTNovels ini."
        )

    return story_title, volumes


_KDT_ILLUSTRATION_LABEL_RE = re.compile(r'illustrat|ilustrasi', re.IGNORECASE)


def get_kdtnovels_volume_covers(volumes):
    """Buat tiap volume, cari chapter berlabel 'Illustrations'/'Ilustrasi'
    (biasanya Ch. 0.5) lalu ambil gambar PERTAMA di halaman itu sebagai
    cover volume tsb. Beda volume = beda halaman ilustrasi = beda cover,
    gak kayak og:image novel yang sama persis buat semua volume. Balikin
    dict {vol_num: (cover_url, referer_url)} -- referer_url (halaman
    ilustrasinya sendiri) WAJIB dikirim balik pas fetch gambar covernya,
    soalnya CDN gambar situs ini nolak (403) request tanpa Referer yang
    cocok. Volume yang gak ketemu halaman ilustrasinya gak masuk dict
    (nanti fallback ke og:image umum).

    PENTING: dulu ini pakai guess_cover_from_first_chapter() yang parse
    halaman pakai scrape_chapter_madara() -- parser buat tema Madara,
    BUKAN buat KDTNovels. Sekarang pakai scrape_chapter_kdtnovels()
    langsung, yang udah ngerti elemen <span class="kdt-ilus"
    data-ilus="..."> kustom KDTNovels (situs ini gak taruh gambar di
    <img src> biasa)."""
    covers = {}
    for vol_num, entries in volumes.items():
        illus_url = None
        for href, label in entries:
            if _KDT_ILLUSTRATION_LABEL_RE.search(label):
                illus_url = href
                break
        if not illus_url:
            continue
        cover = None
        try:
            res = fetch_url(illus_url)
            if res.status_code == 200:
                soup_illus = BeautifulSoup(res.text, 'html.parser')
                _, illus_elements = scrape_chapter_kdtnovels(illus_url, soup_illus)
                for e in illus_elements:
                    if e['type'] == 'img':
                        cover = e['src']
                        break
        except Exception:
            cover = None
        if cover:
            covers[vol_num] = (cover, illus_url)
            log(f"   📸 Cover Volume {vol_num} diambil dari halaman ilustrasi: {illus_url}")
        else:
            log(f"   ℹ️ Halaman ilustrasi Volume {vol_num} ketemu tapi gak ada gambarnya.", "WARN")
    return covers


JUNK_SECTION_HEADINGS_KDT = re.compile(
    r'recommended series|comment|related\s+(post|chapter)|discord',
    re.IGNORECASE
)

_KDT_TITLE_HEADING_RE = re.compile(
    r'^(chapter|bab|prolog(?:ue)?|epilog(?:ue)?|illustrations?|ilustrasi|'
    r'episode|eps|bonus\s+cerita\s+pendek|extra)\b',
    re.IGNORECASE
)


def scrape_chapter_kdtnovels(url, soup, fallback_label=None):
    """Scrape 1 chapter dari kdtnovels.net. `fallback_label` = label yang
    udah didapat dari halaman index (mis. "Vol. 1 Ch. 1 Judul Bab"),
    dipakai sebagai cadangan judul kalau gak ketemu heading judul di
    dalam isi chapter-nya."""
    container = _find_main_content_container(soup)
    chapter_title_parts = []
    elements = []

    if container is not None:
        for elem in container.find_all(['p', 'img', 'span', 'h1', 'h2', 'h3', 'h4', 'h5']):
            if elem.name in ('h1', 'h2', 'h3', 'h4', 'h5'):
                heading_text = elem.get_text(strip=True)
                if not heading_text:
                    continue
                if JUNK_SECTION_HEADINGS_KDT.search(heading_text) or JUNK_SECTION_HEADINGS.search(heading_text):
                    break
                if _KDT_TITLE_HEADING_RE.match(heading_text):
                    if heading_text not in chapter_title_parts:
                        chapter_title_parts.append(heading_text)
                continue

            if elem.name == 'img':
                src = elem.get('src')
                if src:
                    elements.append({'type': 'img', 'src': urljoin(url, src), 'referer': url})
                continue

            if elem.name == 'span':
                # Elemen kustom KDTNovels buat gambar ilustrasi (mis.
                # halaman "Ch. 0: Illustrations") -- BUKAN <img> biasa,
                # URL gambarnya ditaruh di atribut data-ilus (bukan
                # src), kemungkinan sengaja biar gak gampang di-scrape
                # via selector <img> standar. Isinya cuma path relatif
                # ke proxy gambar situs sendiri ("/img/<base64>"), sama
                # persis kayak pola og:image yang udah kepake di tempat
                # lain -- jadi tinggal di-urljoin, gak perlu decode
                # apa-apa manual.
                cls = elem.get('class') or []
                data_ilus = elem.get('data-ilus')
                if 'kdt-ilus' in cls and data_ilus:
                    elements.append({'type': 'img', 'src': urljoin(url, data_ilus), 'referer': url})
                continue

            # Separator spasi + collapse whitespace: beberapa halaman
            # nulis tiap baris dialog/kalimat dipisah <br> di DALAM
            # satu <p> yang sama (bukan <p> terpisah). Tanpa separator
            # ini, get_text() nyambungin baris-baris itu TANPA spasi.
            text = elem.get_text(' ', strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            if not text:
                continue
            text_lower = text.lower()
            if any(junk in text_lower for junk in junk_keywords):
                continue
            if not elements or elements[-1].get('value') != text:
                elements.append({'type': 'text', 'value': text})

    if chapter_title_parts:
        final_title = " - ".join(chapter_title_parts)
    elif fallback_label:
        final_title = fallback_label
    else:
        h1 = soup.find('h1')
        final_title = h1.get_text(strip=True) if h1 else "Chapter"

    return final_title, elements


# ==========================================
# LUMINARE TRANSLATIONS (YARNOVEL THEME)
# ==========================================
# Situs ini menggunakan WordPress + tema Yarnovel dengan content protection
# di HTML, tapi punya WP REST API yang bisa diakses untuk mengambil daftar
# chapter dan kontennya.

LUMINARE_REST_BASE = "https://{domain}/wp-json/wp/v2"


def _luminare_get_series_id(toc_url):
    """Ambil series_id dari URL halaman index Luminare via WP REST API."""
    parsed = urlparse(toc_url)
    domain = parsed.netloc
    # Ekstrak slug dari URL: /series/<slug>/ atau /series/<slug>/...
    path_parts = [p for p in parsed.path.split('/') if p]
    if len(path_parts) >= 2 and path_parts[0] == 'series':
        slug = path_parts[1]
    else:
        slug = path_parts[-1] if path_parts else ''
    api_url = f"{LUMINARE_REST_BASE.format(domain=domain)}/series?slug={slug}"
    res = fetch_url(api_url)
    data = res.json()
    if data and isinstance(data, list):
        return domain, data[0]['id'], data[0]['title']['rendered']
    raise RuntimeError(f"Gagal menemukan series ID untuk slug '{slug}'")


def _luminare_fetch_all_chapters(domain, series_id):
    """Fetch semua chapter via WP REST API (handle pagination + client-side filter by series_id)."""
    all_chapters = []
    page = 1
    target_series_id = int(series_id)
    while True:
        api_url = (
            f"{LUMINARE_REST_BASE.format(domain=domain)}/chapter"
            f"?per_page=100&page={page}"
        )
        res = fetch_url(api_url)
        data = res.json()
        if not isinstance(data, list) or len(data) == 0:
            break
        for ch in data:
            meta = ch.get('meta', {})
            ch_series_id = meta.get('series_id')
            if ch_series_id is not None and int(ch_series_id) == target_series_id:
                all_chapters.append(ch)

        # Cek header X-WP-TotalPages
        total_pages = int(res.headers.get('X-WP-TotalPages', 1))
        if page >= total_pages:
            break
        page += 1
    return all_chapters


def get_volumes_from_toc_luminare(toc_url, soup):
    """Parse halaman index Luminare via WP REST API."""
    domain, series_id, story_title = _luminare_get_series_id(toc_url)
    log(f"   🔎 Luminare series ID: {series_id}")

    chapters = _luminare_fetch_all_chapters(domain, series_id)
    log(f"   📖 {len(chapters)} chapter ditemukan via REST API")

    volumes = {}
    for ch in chapters:
        meta = ch.get('meta', {})
        group = meta.get('chapter_group', '')
        chap_idx = meta.get('chapter_index', 0)
        chap_title = meta.get('chapter_title', '') or ''
        chap_sub = meta.get('chapter_subtitle', '') or ''
        link = ch.get('link', '')

        if not link:
            continue

        # Parse nomor volume dari chapter_group ("volume-1" -> 1)
        vol_match = re.match(r'volume-(\d+)', group, re.IGNORECASE) if group else None
        if vol_match:
            vol_num = int(vol_match.group(1))
        else:
            vol_num = 1  # default ke volume 1 kalau gak ada info

        label = chap_title.strip()
        if chap_sub.strip():
            label = f"{label} {chap_sub.strip()}" if label else chap_sub.strip()
        if not label:
            label = f"Chapter {chap_idx}"

        volumes.setdefault(vol_num, [])
        volumes[vol_num].append((link, label, chap_idx, ch.get('id', 0)))

    # Sort chapters in each volume by post ID ascending (kronologis: bab awal duluan)
    for vol_num in volumes:
        volumes[vol_num].sort(key=lambda x: x[3])

    # Strip chapter_index dan post_id dari tuples (jaga kompatibilitas dgn build_pdf_for_urls)
    for vol_num in volumes:
        volumes[vol_num] = [(url, label) for url, label, _idx, _id in volumes[vol_num]]

    return story_title, volumes


def scrape_chapter_luminare(url, soup):
    """Scrape chapter Luminare via WP REST API (konten di HTML diproteksi)."""
    # Ambil post ID dari body class: "postid-15056"
    body = soup.find('body')
    post_id = None
    if body:
        body_classes = body.get('class', [])
        for cls in body_classes:
            m = re.match(r'postid-(\d+)', cls)
            if m:
                post_id = int(m.group(1))
                break

    if not post_id:
        # Fallback: coba dari shortlink
        shortlink = soup.find('link', attrs={'rel': 'shortlink'})
        if shortlink and shortlink.get('href'):
            m = re.search(r'\?p=(\d+)', shortlink['href'])
            if m:
                post_id = int(m.group(1))

    if not post_id:
        return "Chapter", []

    # Fetch konten via REST API
    parsed = urlparse(url)
    domain = parsed.netloc
    api_url = f"https://{domain}/wp-json/wp/v2/chapter/{post_id}"
    try:
        res = fetch_url(api_url)
        data = res.json()
    except Exception:
        return "Chapter", []

    # Build judul
    meta = data.get('meta', {})
    chap_title = meta.get('chapter_title', '') or ''
    chap_sub = meta.get('chapter_subtitle', '') or ''
    final_title = chap_title.strip()
    if chap_sub.strip():
        final_title = f"{final_title} {chap_sub.strip()}" if final_title else chap_sub.strip()
    if not final_title:
        final_title = data.get('title', {}).get('rendered', 'Chapter')

    # Parse HTML konten
    content_html = data.get('content', {}).get('rendered', '')
    if not content_html:
        return final_title, []

    content_soup = BeautifulSoup(content_html, 'html.parser')
    elements = []

    for tag in content_soup.find_all(['p', 'img', 'figure', 'h2', 'h3', 'h4', 'h5']):
        if tag.name == 'img':
            # Skip gambar yang sudah diambil dari <figure> induknya
            if tag.find_parent('figure'):
                continue
            src = tag.get('src')
            if src:
                elements.append({'type': 'img', 'src': src, 'referer': url})
        elif tag.name == 'figure':
            # Figure bisa berisi gallery (beberapa <img>)
            for img in tag.find_all('img'):
                src = img.get('src')
                if src:
                    elements.append({'type': 'img', 'src': src, 'referer': url})
        else:
            text = tag.get_text(' ', strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            if not text:
                continue
            text_lower = text.lower()
            if any(junk in text_lower for junk in junk_keywords):
                continue
            if not elements or elements[-1].get('value') != text:
                elements.append({'type': 'text', 'value': text})

    return final_title, elements


# ==========================================
# WORLDNOVEL.MY.ID (Next.js + REST API)
# ==========================================
# worldnovel.my.id adalah situs Next.js yang konten chapter-nya dimuat
# lewat React Server Components (bukan HTML statis). Ada API internal
# yang bisa dipanggil tanpa auth buat ambil SEMUA chapter sekaligus
# beserta konten Markdown-nya:
#   GET /api/novels/{internal_id}/chapters
# Internal ID novel didapat dari data RSC yang ter-embed di halaman index.
#
# Cache buat nyimpen konten chapter dari API, biar scrape_chapter_worldnovel
# gak perlu fetch ulang. Diisi sekali di get_volumes_from_toc_worldnovel,
# dipakai di scrape_chapter_worldnovel.
_WORLDNOVEL_CHAPTER_CACHE = {}


def _worldnovel_extract_novel_internal_id(soup):
    """Ekstrak internal ID novel dari data RSC yang ter-embed di halaman
    index worldnovel.my.id. ID-nya ada di JSON payload dalam tag <script>
    yang berisi 'self.__next_f.push' — cari field 'id' di objek novel
    yang ada di data 'initialData' (novel object)."""
    for script in soup.find_all('script'):
        text = script.string or ''
        if 'self.__next_f.push' not in text:
            continue
        # Cari pola novel:{\\\"id\\\":\\\"cms...\\\"} di dalam payload RSC
        # (escaped JSON di dalam string JavaScript)
        m = re.search(r'novel.*?\\"id\\":\\"(cms[a-z0-9]+)\\"', text)
        if m:
            return m.group(1)
    return None


def get_volumes_from_toc_worldnovel(toc_url, soup):
    """Parse halaman index worldnovel.my.id via REST API internal."""
    novel_internal_id = _worldnovel_extract_novel_internal_id(soup)
    if not novel_internal_id:
        raise RuntimeError(
            "Gagal menemukan internal ID novel di halaman index worldnovel.my.id."
        )

    log(f"   🔎 WorldNovel internal ID: {novel_internal_id}")

    # Ambil judul dari og:title (lebih reliable untuk situs JS-rendered)
    story_title = "Novel"
    og_title = soup.find('meta', attrs={'property': 'og:title'})
    if og_title and og_title.get('content'):
        story_title = og_title['content'].strip()
    if story_title == "Novel":
        h1 = soup.find('h1')
        if h1:
            story_title = h1.get_text(strip=True)

    # Fetch semua chapter via API internal
    api_url = f"https://{urlparse(toc_url).netloc}/api/novels/{novel_internal_id}/chapters"
    try:
        res = fetch_url(api_url)
        data = res.json()
    except Exception as e:
        raise RuntimeError(f"Gagal fetch API chapter WorldNovel: {e}")

    chapters = data.get('chapters', [])
    if not chapters:
        raise RuntimeError("API WorldNovel mengembalikan 0 chapter.")

    log(f"   📖 {len(chapters)} chapter ditemukan via REST API")

    # Isi cache konten chapter & kelompokkan berdasarkan volume
    volumes = {}
    for ch in chapters:
        vol_num = ch.get('volume') or 1
        public_id = ch.get('publicId', '')
        title = ch.get('title', 'Chapter')
        chapter_number = ch.get('chapterNumber', '')
        content = ch.get('content', '')
        reading_order = ch.get('readingOrder', 0)

        # Simpan di cache biar scrape_chapter_worldnovel bisa akses
        _WORLDNOVEL_CHAPTER_CACHE[public_id] = {
            'title': title,
            'content': content,
            'chapter_number': chapter_number,
        }

        # URL dummy — gak dipakai buat fetch, cuma identifier
        dummy_url = f"worldnovel://{public_id}"
        label = title
        if chapter_number:
            label = f"Chapter {chapter_number} - {title}"

        volumes.setdefault(vol_num, []).append((reading_order, dummy_url, label))

    # Urutkan tiap volume berdasarkan readingOrder
    for vol_num in volumes:
        volumes[vol_num].sort(key=lambda t: t[0])
        volumes[vol_num] = [(url, label) for _order, url, label in volumes[vol_num]]

    if not volumes:
        raise RuntimeError("Tidak ada volume ditemukan dari API WorldNovel.")

    return story_title, volumes


def _parse_worldnovel_markdown(content, base_url=None):
    """Konversi konten Markdown dari WorldNovel jadi list elemen
    {'type': 'text', 'value': ...} / {'type': 'img', 'src': ..., 'referer': ...}"""
    elements = []
    if not content:
        return elements

    # Base URL buat convert relative image URLs
    if not base_url:
        base_url = "https://worldnovel.my.id"

    # Split per baris, prosees paragraph & gambar
    lines = content.split('\n')
    text_buffer = []

    for line in lines:
        stripped = line.strip()

        # Markdown image: ![alt](url) — support both relative & absolute
        img_match = re.match(r'!\[.*?\]\(([^)]+)\)', stripped)
        if img_match:
            # Flush text buffer dulu
            if text_buffer:
                combined = ' '.join(text_buffer)
                combined = re.sub(r'\s+', ' ', combined).strip()
                if combined:
                    elements.append({'type': 'text', 'value': combined})
                text_buffer = []
            img_src = img_match.group(1)
            if not img_src.startswith('http'):
                img_src = urljoin(base_url, img_src)
            elements.append({'type': 'img', 'src': img_src, 'referer': base_url})
            continue

        # Baris kosong = pemisah paragraph
        if not stripped:
            if text_buffer:
                combined = ' '.join(text_buffer)
                combined = re.sub(r'\s+', ' ', combined).strip()
                if combined:
                    elements.append({'type': 'text', 'value': combined})
                text_buffer = []
            continue

        # Skip junk keywords
        text_lower = stripped.lower()
        if any(junk in text_lower for junk in junk_keywords):
            continue

        text_buffer.append(stripped)

    # Flush sisa buffer
    if text_buffer:
        combined = ' '.join(text_buffer)
        combined = re.sub(r'\s+', ' ', combined).strip()
        if combined:
            elements.append({'type': 'text', 'value': combined})

    return elements


def scrape_chapter_worldnovel(url, soup):
    """Scrape chapter WorldNovel dari cache yang sudah diisi oleh
    get_volumes_from_toc_worldnovel. URL yang masuk berformat
    'worldnovel://<publicId>'."""
    # Ekstrak public ID dari URL dummy
    public_id = url.replace('worldnovel://', '')

    cached = _WORLDNOVEL_CHAPTER_CACHE.get(public_id)
    if not cached:
        log(f"   ⚠️ Cache miss untuk chapter {public_id}, coba fetch dari API...", "WARN")
        # Fallback: fetch satu chapter dari API
        return "Chapter", []

    title = cached['title']
    chapter_number = cached.get('chapter_number', '')
    content = cached['content']

    final_title = title
    if chapter_number:
        final_title = f"Chapter {chapter_number} - {title}"

    elements = _parse_worldnovel_markdown(content, base_url="https://worldnovel.my.id")
    return final_title, elements


# ==========================================
# STORYSEEDLING.COM (Laravel + font obfuscation)
# ==========================================
# storyseedling.com pakai Laravel + Livewire + Alpine.js. Konten chapter
# di-obfuscate server-side: setiap huruf Latin diganti dgn char dari range
# Kangxi Radicals (U+2F42-U+2F75), lalu di-render pakai custom font
# "Vagre" yang mapping balik ke glyph asli. POST ke /content dgn nonce
# bisa langsung dpt HTML obfuscated-nya (gak perlu solve Turnstile).

def _decode_storyseedling_text(text):
    """Decode obfuscated text: Kangxi Radicals (U+2F42-U+2F75) -> A-Z + a-z.
    Font Vagre mapping 52 Kangxi Radical chars ke 52 huruf Latin
    (26 uppercase + 26 lowercase)."""
    result = []
    for ch in text:
        code = ord(ch)
        if 0x2F42 <= code <= 0x2F75:
            p = code - 0x2F42
            if p < 26:
                result.append(chr(0x41 + p))  # A-Z
            else:
                result.append(chr(0x61 + p - 26))  # a-z
        else:
            result.append(ch)
    return ''.join(result)


def _storyseedling_extract_nonce(soup):
    """Extract nonce dari halaman chapter storyseedling.com.
    Nonce ada di atribut loadChapter('sitekey', 'nonce')."""
    for script in soup.find_all('script'):
        text = script.string or ''
        m = re.search(r"loadChapter\('([^']+)',\s*'([^']+)'\)", text)
        if m:
            return m.group(2)
    # Fallback: cari di seluruh HTML
    m = re.search(r"loadChapter\('([^']+)',\s*'([^']+)'\)", str(soup))
    if m:
        return m.group(2)
    return None


def _storyseedling_fetch_chapters_via_playwright(toc_url):
    """Ambil daftar chapter dari halaman series storyseedling.com pakai Playwright.
    Karena chapter list di-load via JavaScript (Alpine.js), gak bisa pakai
    requests biasa. Balikin list of dict {url, title, volume, chapter}."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright belum terinstall. Jalankan: "
            "pip install playwright && python -m playwright install chromium"
        )

    parsed = urlparse(toc_url)
    series_id_match = re.search(r'/series/(\d+)', parsed.path)
    if not series_id_match:
        raise RuntimeError(f"Gak bisa extract series ID dari URL: {toc_url}")
    series_id = series_id_match.group(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(toc_url, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(5000)

        raw_chapters = page.evaluate('''(seriesId) => {
            const results = [];
            const links = document.querySelectorAll('a[href*="/v"]');
            for (const link of links) {
                const href = link.getAttribute('href') || '';
                const text = link.innerText.trim();
                if (href.includes('/series/' + seriesId + '/v') && text.length > 5) {
                    results.push({href, text});
                }
            }
            return results;
        }''', series_id)

        browser.close()

    # Parse chapters
    chapters = []
    seen_urls = set()
    for raw in raw_chapters:
        href = raw['href']
        text = raw['text']

        # Skip non-chapter links
        if 'Read' == text.strip() or len(text) < 5:
            continue
        # Skip if not a chapter URL pattern
        vol_match = re.search(r'/v(\d+)/([\d.]+)', href)
        if not vol_match:
            continue

        full_url = urljoin(toc_url, href) if not href.startswith('http') else href
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        vol_num = int(vol_match.group(1))
        # Clean title: remove "Vol. N Chapter N - " prefix, decode obfuscation
        clean_title = re.sub(r'Vol\.\s*\d+\s+Chapter\s+[\d.]+\s*[-–]\s*', '', text)
        clean_title = _decode_storyseedling_text(clean_title)
        # Remove extra junk (dates, unicode artifacts)
        clean_title = re.sub(r'\d+\s+years?\s+ago.*', '', clean_title).strip()
        clean_title = re.sub(r'[^\x00-\x7F]+', '', clean_title).strip()

        chapters.append({
            'url': full_url,
            'title': clean_title,
            'volume': vol_num,
        })

    if not chapters:
        raise RuntimeError("Gak ada chapter ditemukan via Playwright.")

    return chapters


def get_volumes_from_toc_storyseedling(toc_url, soup):
    """Parse halaman series storyseedling.com."""
    # Ambil judul dari og:title
    story_title = "Novel"
    og_title = soup.find('meta', attrs={'property': 'og:title'})
    if og_title and og_title.get('content'):
        story_title = html.unescape(og_title['content']).strip()
    if story_title == "Novel":
        h1 = soup.find('h1')
        if h1:
            story_title = h1.get_text(strip=True)

    log(f"   🔎 StorySeedling: ambil chapter list via Playwright...")
    chapters = _storyseedling_fetch_chapters_via_playwright(toc_url)
    log(f"   📖 {len(chapters)} chapter ditemukan")

    # Group by volume
    volumes = {}
    for ch in chapters:
        vol_num = ch['volume']
        label = f"Chapter {ch['title']}" if ch['title'] else "Chapter"
        volumes.setdefault(vol_num, []).append((ch['url'], label))

    if not volumes:
        raise RuntimeError("Gak ada volume ditemukan dari StorySeedling.")

    return story_title, volumes


def scrape_chapter_storyseedling(url, soup):
    """Scrape chapter storyseedling.com via POST /content + decode obfuscation."""
    # Extract nonce dari halaman chapter
    nonce = _storyseedling_extract_nonce(soup)
    if not nonce:
        log("   ⚠️ Gak bisa extract nonce, coba fallback.", "WARN")
        return "Chapter", []

    # POST ke /content endpoint - with retry (10 attempts)
    content_url = f"{url.rstrip('/')}/content"
    post_headers = {
        'User-Agent': HEADERS['User-Agent'],
        'X-Nonce': nonce,
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': url,
    }
    
    content_html = None
    for attempt in range(1, 11):
        try:
            res = requests.post(content_url, headers=post_headers, json={"nonce": ""}, timeout=30)
        except Exception as e:
            log(f"   ⚠️ POST /content gagal (percobaan {attempt}/10): {e}", "WARN")
            if attempt < 10:
                time.sleep(5 * attempt)  # 5s, 10s, 15s...
            continue
        
        if res.status_code == 200:
            content_html = res.text
            break
        elif res.status_code == 400:
            log(f"   ⚠️ POST /content status 400 (percobaan {attempt}/10)", "WARN")
            if attempt < 10:
                time.sleep(8 * attempt)  # 8s, 16s, 24s...
            continue
        else:
            log(f"   ⚠️ POST /content status {res.status_code} (percobaan {attempt}/10)", "WARN")
            if attempt < 10:
                time.sleep(5 * attempt)
            continue

    if not content_html or len(content_html) < 100:
        log(f"   ⚠️ Gagal dapat konten chapter setelah 10x percobaan", "WARN")
        return "Chapter", []

    content_soup = BeautifulSoup(content_html, 'html.parser')

    # Extract title dari halaman chapter
    title = "Chapter"
    h1 = soup.find('h1')
    if h1:
        title = _decode_storyseedling_text(h1.get_text(strip=True))
        title = re.sub(r'^Vol\.\s*\d+\s+', '', title)
        title = re.sub(r'^Chapter\s+[\d.]+\s*[-–]\s*', '', title)

    # Parse elements - storyseedling uses <p> containing <span class="cls..."> with obfuscated text
    # Strategy: iterate <p> tags, extract text from cls spans inside each, decode, clean
    elements = []
    junk_watermarks = [
        'this content is owned by story seedling',
        'if you are reading this on a site other than storyseedling',
    ]

    # Get images
    for img in content_soup.find_all('img'):
        src = img.get('src')
        if src:
            elements.append({'type': 'img', 'src': urljoin(url, src), 'referer': url})

    # Process each <p> tag - each represents a paragraph
    for p_tag in content_soup.find_all('p'):
        # Get all cls spans inside this paragraph
        para_texts = []
        for span in p_tag.find_all('span', class_=re.compile(r'cls[a-f0-9]+')):
            text = span.get_text(strip=True)
            # Filter out noise spans (just cls references)
            if not text or re.match(r'^cls[a-f0-9]+$', text):
                continue
            # Only keep spans with actual obfuscated text (non-cls chars)
            if re.search(r'[^cls\d]', text):
                para_texts.append(text)
        
        if not para_texts:
            continue
        
        # Combine and decode
        para_text = ' '.join(para_texts)
        para_text = _decode_storyseedling_text(para_text)
        para_text = re.sub(r'\s+', ' ', para_text).strip()
        
        # Skip very short or junk paragraphs
        if len(para_text) < 10:
            continue
        para_lower = para_text.lower()
        if any(junk in para_lower for junk in junk_watermarks):
            continue
        if any(junk in para_lower for junk in junk_keywords):
            continue
        
        if not elements or elements[-1].get('value') != para_text:
            elements.append({'type': 'text', 'value': para_text})

    return title, elements
# Blog WordPress.com biasa (mis. *.home.blog) -- ToC-nya cuma heading
# "Volume N" diikuti link chapter POLOS (gak dibungkus bold kayak pola
# Blogger). Link chapter dikenali dari pola PERMALINK TANGGAL bawaan
# WordPress (/yyyy/mm/dd/judul-slug/), bukan dari nama class HTML --
# otomatis nyaring link navbar/footer/share/Discord yang gak relevan.
_WP_DATE_PERMALINK_RE = re.compile(r'/\d{4}/\d{2}/\d{2}/')


def get_volumes_from_toc_wpcom(toc_url, soup):
    h1 = soup.find('h1')
    story_title = h1.get_text(strip=True) if h1 else "Novel"
    domain = urlparse(toc_url).netloc

    volumes = {}
    current_vol = None
    seen = set()

    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'a']):
        if tag.name != 'a':
            text = tag.get_text(strip=True)
            vol_match = re.match(r'^volume\s*(\d+)', text, re.IGNORECASE)
            if vol_match:
                current_vol = int(vol_match.group(1))
                volumes.setdefault(current_vol, [])
            continue

        href = fix_doubled_url(tag.get('href'))
        if not href or current_vol is None or href in seen:
            continue
        link_domain = urlparse(href).netloc
        if link_domain and link_domain != domain:
            continue
        if not _WP_DATE_PERMALINK_RE.search(href):
            continue

        label = tag.get_text(strip=True)
        if not label:
            continue
        seen.add(href)
        volumes[current_vol].append((href, label))

    if not volumes:
        raise RuntimeError(
            "Tidak menemukan link chapter berpola tanggal WordPress "
            "(/yyyy/mm/dd/) di halaman index ini."
        )

    return story_title, volumes


def get_wpcom_volume_covers(soup):
    """Blog WordPress.com kayak CClaw nampilin gambar sampul SENDIRI buat
    tiap volume, persis SEBELUM heading 'Volume N' masing-masing di
    halaman ToC. Fungsi ini nyatet <img> TERAKHIR yang muncul sebelum tiap
    heading itu -> dict {vol_num: url_gambar}. Kalau volume tertentu gak
    punya gambar sebelum headingnya, dia gak masuk dict (nanti fallback ke
    cover umum/og:image di pemanggilnya)."""
    covers = {}
    last_img_src = None
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'img']):
        if tag.name == 'img':
            src = tag.get('src')
            if src:
                last_img_src = src
            continue
        text = tag.get_text(strip=True)
        vol_match = re.match(r'^volume\s*(\d+)', text, re.IGNORECASE)
        if vol_match and last_img_src:
            vol_num = int(vol_match.group(1))
            covers.setdefault(vol_num, last_img_src)
    return covers


# ==========================================
# DISPATCHER: MODE OTOMATIS
# ==========================================
def _is_blogger_label_page(toc_url):
    """True kalau URL-nya halaman label Blogger (mis.
    ...blogspot.com/search/label/<nama>...). Halaman ini bukan ToC
    novel, tapi listing semua post dengan label tertentu -- di-crawl
    via pagination 'Load more posts' (link dengan parameter start=N)."""
    parsed = urlparse(toc_url)
    if not parsed.netloc.endswith('blogspot.com'):
        return False
    return '/search/label/' in parsed.path


def get_volumes_from_toc_blogger_label(toc_url):
    """Crawl halaman label Blogger: kumpulkan SEMUA link post yang
    muncul di semua halaman pagination (link 'Load more posts' / 'Older'
    yang bawa parameter start=N). Balikin (story_title, {1: [(href, label), ...]})
    -- semuanya dilump jadi 1 volume karena Blogger label page gak punya
    heading 'Volume N'."""
    log("   🔎 Halaman label Blogger terdeteksi, crawl pagination...")
    parsed = urlparse(toc_url)
    # Bersihin query string biar crawl dari page 1 (start=1 = default)
    base = urljoin(f"{parsed.scheme}://{parsed.netloc}", parsed.path)

    seen = set()
    all_entries = []  # [(label, href)]
    next_url = base
    page = 0
    MAX_PAGES = 50  # safety cap

    while next_url and page < MAX_PAGES:
        page += 1
        log(f"   📄 Label page {page}: {next_url[:90]}...")
        res = fetch_url(next_url)
        if res.status_code != 200:
            log(f"   ⚠️ Gagal fetch label page {page} (status {res.status_code}), stop.", "WARN")
            break
        soup = BeautifulSoup(res.text, 'html.parser')

        # Setiap post entry di Blogger label page punya <h2> dengan <a>
        # nunjuk ke URL post individual.
        new_on_page = 0
        for h2 in soup.find_all('h2'):
            a = h2.find('a')
            if not a or not a.get('href'):
                continue
            href = fix_doubled_url(a['href'])
            if not href or href in seen:
                continue
            # Filter link post dummy "Older posts"/"Home" dll
            if any(skip in href for skip in ('/search/label', '/search?', '#', 'javascript:')):
                continue
            label = a.get_text(strip=True)
            if not label:
                continue
            seen.add(href)
            all_entries.append((label, href))
            new_on_page += 1

        log(f"      → {new_on_page} post baru (total {len(all_entries)})")

        if new_on_page == 0:
            # Halaman kosong / 'No results found' -> stop
            break

        # Cari link 'Load more posts' / 'Older posts' (Blogger pake
        # parameter start= di URL-nya). Kadang ada beberapa link dengan
        # start=, pilih yang start= terbesar (paling baru / lanjutannya).
        candidates = []
        for a in soup.find_all('a'):
            href = a.get('href') or ''
            if 'start=' not in href:
                continue
            text = a.get_text(strip=True).lower()
            if any(kw in text for kw in ('load more', 'older', 'next', '›', '»')) or 'start=' in href:
                candidates.append(href)
        if not candidates:
            break
        # Ambil yang start= terbesar (pagination lanjutan)
        def start_of(url):
            m = re.search(r'start=(\d+)', url)
            return int(m.group(1)) if m else 0
        next_url = max(candidates, key=start_of)
        if start_of(next_url) == start_of(candidates[0]) and len(candidates) > 1 and start_of(candidates[0]) == 0:
            # Safety: kalau semua start=0, jangan loop
            break

    if not all_entries:
        raise RuntimeError("Halaman label Blogger ini kosong / tidak ada post.")

    # Title dari <title> page atau h1 (Blogger label page biasanya cuma
    # punya <title>). Buang suffix ' - Kaori TL' dll.
    title = ''
    if soup and soup.title and soup.title.string:
        title = soup.title.string.strip()
    else:
        og = soup.find('meta', attrs={'property': 'og:title'}) if soup else None
        if og and og.get('content'):
            title = og['content']
    if not title:
        title = "Novel"
    title = re.split(r'\s*[-–—]\s*', title, maxsplit=1)[0].strip()

    # Sort entries by chapter number yang ke-parse dari label (Chapter 1,
    # Chapter 2, ..., Prologue, Afterword). Entries yang gak ke-parse
    # ditaruh di akhir.
    def sort_key(entry):
        lbl, _ = entry  # entry = (label, href); lbl = element 0
        # Cari pola "Chapter N" atau "Bab N" (N bisa desimal: 4.5, 8.5, dst)
        m = re.search(r'chapter\s*(\d+(?:\.\d+)?)', lbl, re.IGNORECASE) or re.search(r'bab\s*(\d+(?:\.\d+)?)', lbl, re.IGNORECASE)
        if m:
            # Pakai float biar 4.5 < 5, bukan integer (4.5 -> 4 salah)
            return (1, float(m.group(1)), lbl)
        if re.search(r'prolog', lbl, re.IGNORECASE):
            return (0, 0, lbl)
        if re.search(r'illustrasi|illustration', lbl, re.IGNORECASE):
            return (-1, 0, lbl)
        if re.search(r'epilog', lbl, re.IGNORECASE):
            return (2, 0, lbl)
        if re.search(r'afterword', lbl, re.IGNORECASE):
            return (3, 0, lbl)
        return (4, 0, lbl)

    all_entries.sort(key=sort_key)

    return title, {1: [(href, label) for label, href in all_entries]}


def get_volumes_from_toc(toc_url):
    log(f"📖 Membaca halaman index: {toc_url}")
    try:
        res = fetch_url(toc_url)
    except Exception as e:
        raise RuntimeError(f"Gagal koneksi ke halaman index: {e}")

    if res.status_code != 200:
        raise RuntimeError(f"Gagal membuka halaman index (status {res.status_code}).")

    soup = BeautifulSoup(res.text, 'html.parser')

    if is_agungx(toc_url):
        log("   🔎 Terdeteksi sebagai situs AgungX Novel.")
        story_title, volumes = get_volumes_from_toc_agungx(toc_url, soup)
    elif is_kdtnovels(toc_url):
        log("   🔎 Terdeteksi sebagai situs KDTNovels.")
        story_title, volumes = get_volumes_from_toc_kdtnovels(toc_url, soup)
    elif is_luminare(toc_url) or is_yarnovel(soup):
        log("   🔎 Terdeteksi sebagai situs Luminare Translations (Yarnovel theme).")
        story_title, volumes = get_volumes_from_toc_luminare(toc_url, soup)
    elif is_worldnovel(toc_url):
        log("   🔎 Terdeteksi sebagai situs WorldNovel (Next.js + REST API).")
        story_title, volumes = get_volumes_from_toc_worldnovel(toc_url, soup)
    elif is_storyseedling(toc_url):
        log("   🔎 Terdeteksi sebagai situs StorySeedling (Laravel + font obfuscation).")
        story_title, volumes = get_volumes_from_toc_storyseedling(toc_url, soup)
    elif _is_blogger_label_page(toc_url):
        # Halaman label Blogger (gak ada ToC): crawl pagination 'Load more'.
        # soup di atas gak dipake di sini (langsung re-fetch per page di
        # fungsi label-crawl).
        story_title, volumes = get_volumes_from_toc_blogger_label(toc_url)
    elif is_madara(soup):
        log("   🔎 Terdeteksi sebagai situs bertema Madara, crawl via Prev/Next...")
        story_title, volumes = get_volumes_from_toc_madara(toc_url, soup)
    elif is_wpcom(soup):
        log("   🔎 Terdeteksi sebagai blog WordPress.com biasa.")
        story_title, volumes = get_volumes_from_toc_wpcom(toc_url, soup)
    elif is_generic_wp(soup) and is_generic_wp_index(soup):
        log("   🔎 Terdeteksi sebagai WordPress self-hosted (index novel).")
        story_title, volumes = get_volumes_from_toc_generic_wp(toc_url, soup)
    else:
        story_title, volumes = get_volumes_from_toc_blogger(toc_url, soup)

    log(f"   📌 Judul terdeteksi: {story_title}")
    for v in sorted(volumes):
        log(f"   ✅ Volume {v}: {len(volumes[v])} link ditemukan")

    return story_title, volumes


# ==========================================
# SCRAPING SATU CHAPTER: BLOGGER
# ==========================================
def _elem_is_bold_styled(elem, text):
    """True kalau `elem` sendiri tag bold/heading (b/strong/h2/h3), ATAU
    seluruh isinya cuma satu anak <b>/<strong> yang teksnya PAS sama
    dengan `text` (satu baris bold utuh, mis. "<p><b>Chapter 1</b></p>").
    Dipakai buat mastiin paragraf "lanjutan judul" beneran ditulis bold
    (subtitle), bukan paragraf isi cerita biasa yang kebetulan jadi
    paragraf pertama setelah judul."""
    if elem.name in ('b', 'strong', 'h2', 'h3'):
        return True
    bold_children = elem.find_all(['b', 'strong'], recursive=False)
    return len(bold_children) == 1 and bold_children[0].get_text(strip=True) == text


def scrape_chapter_blogger(url, soup, first_cover_key_holder, fallback_label=None):
    post_body = soup.find('div', class_=re.compile(r'post-body|entry-content'))
    chapter_title_parts = []
    # Hitung terpisah berapa kali heading "Chapter N/Prolog/Epilog dst"
    # ketemu (bukan subtitle nyambung) -- dipakai buat deteksi halaman
    # "gabungan beberapa chapter" di bawah.
    num_chapter_headings = 0
    elements = []

    if post_body:
        for elem in post_body.find_all(['p', 'img', 'div', 'b', 'strong', 'h2', 'h3', 'span']):
            # Skip wrapper <div> yang berisi anak <div>/<p> -- hanya proses
            # "leaf" div (yang isinya teks langsung). Tanpa ini, find_all
            # bakal ngambil outer div juga -> get_text() nyambungin semua
            # paragraf jadi satu blok, lalu inner div juga ke-proses satu-
            # satu -> teks ke-gabung/double & berantakan di PDF output.
            if elem.name == 'div' and elem.find(['div', 'p']):
                continue
            # Skip <span> yang di dalam <p> -- p-nya sudah di-scan sendiri,
            # jadi span gak perlu diproses ulang (biar gak double).
            if elem.name == 'span' and elem.find_parent('p'):
                continue
            # Skip <span> Google Docs wrapper (id="docs-internal-guid-...")
            # yang ngebungkus BANYAK <p> di dalamnya. Tanpa ini, satu
            # <span> gede di proses sebagai leaf -> get_text() nyambungin
            # SELURUH chapter jadi 1 block berantakan.
            if elem.name == 'span' and elem.find(['p', 'div']):
                continue
            if elem.name == 'img':
                src = elem.get('src')
                if not src:
                    continue
                img_key = normalize_img_src(src)
                if first_cover_key_holder['img'] is None:
                    log("   📸 Gambar sampul utama ditemukan, mengunduh...")
                    first_cover_key_holder['img'] = fetch_image(src, referer=url)
                    first_cover_key_holder['key'] = img_key
                    continue
                if img_key == first_cover_key_holder['key']:
                    continue
                elements.append({'type': 'img', 'src': src, 'referer': url})
            else:
                # Separator spasi + collapse whitespace: beberapa post
                # Blogger nulis tiap kalimat/paragraf dipisah <br> di
                # DALAM satu <p> yang sama (bukan <p> terpisah). Tanpa
                # separator ini, get_text() nyambungin baris-baris itu
                # TANPA spasi (mis. "hari libur.Saat ini, aku...").
                text = elem.get_text(' ', strip=True)
                # Strip zero-width space (& zero-width joiner, BOM, dll.)
                # yang sering muncul di awal paragraf Blogger dari paste
                # Word/Google Docs -- tanpa strip ini, get_text() masih
                # nge-keep karakter tak terlihat di akhir/awal text & bisa
                # bikin PDF wrap atau output berantakan.
                text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
                text = re.sub(r'\s+', ' ', text).strip()
                if not text:
                    continue
                text_lower = text.lower()
                is_junk = any(junk in text_lower for junk in junk_keywords)
                if is_junk:
                    continue
                # Pola regex full-match buat blok junk Blogger multi-baris
                # (TL Note Admin, "Tags:", baris share button individual,
                # dsb.) -- substring check di atas gak nutup ini karena
                # teksnya bisa panjang & kepecah per baris di HTML.
                if any(pat.search(text) for pat in _JUNK_BLOGGER_PATTERNS):
                    continue
                # Guard panjang teks: judul chapter harusnya pendek (mis.
                # "Epilog" atau "Chapter 5 - Judulnya"). Kalau paragraf
                # yang KEBETULAN diawali kata "epilog"/"chapter"/dst itu
                # panjangnya udah kayak satu chapter penuh (kasus di atas
                # -- satu <p> gede yang gabungin SEMUA isi bab jadi satu),
                # itu bukan judul, biarin jatuh ke isi normal di bawah.
                if len(text) <= 120 and re.match(
                    r'^(chapter|prologue|prolog|epilogue|epilog|bab)\b\s*\d*\b', text_lower
                ):
                    num_chapter_headings += 1
                    if text not in chapter_title_parts:
                        chapter_title_parts.append(text)
                elif (
                    len(chapter_title_parts) == 1
                    and not elements
                    and not text.startswith(('"', '“', '"', "'", '‘', "'"))
                    and _elem_is_bold_styled(elem, text)
                ):
                    if text not in chapter_title_parts:
                        chapter_title_parts.append(text)
                else:
                    if not elements or elements[-1].get('value') != text:
                        elements.append({'type': 'text', 'value': text})

    # Beberapa post Kaori TL menggabungkan BEBERAPA chapter dalam SATU
    # halaman (mis. ToC-nya nulis "Chapter 6 - 10" -> 1 URL isinya chapter
    # 6,7,8,9,10 sekaligus). Kalau direkonstruksi dari heading di dalam
    # halaman (kayak biasanya), semua heading "Chapter N" yang ketemu bakal
    # digabung jadi satu judul yang panjang & rancu (mis. "Chapter 6 ... -
    # Chapter 7: ... - Chapter 10: ..."). Begitu ketemu LEBIH DARI SATU
    # heading chapter dalam satu halaman, itu tanda halamannya gabungan --
    # pakai label dari Daftar Isi (fallback_label, mis. "Chapter 6 - 10")
    # yang udah bersih & akurat, daripada rekonstruksi yang berantakan.
    if num_chapter_headings >= 2 and fallback_label:
        final_title = fallback_label
    elif chapter_title_parts:
        final_title = " - ".join(chapter_title_parts)
    else:
        title_el = soup.find('h1', class_='post-title') or soup.find('h1')
        final_title = title_el.get_text(strip=True) if title_el else (fallback_label or "Chapter")

    if elements and elements[0]['type'] == 'text':
        first_text = elements[0]['value'].strip().lower()
        if first_text in final_title.lower() or any(part.lower() == first_text for part in chapter_title_parts):
            elements.pop(0)

    return final_title, elements


# ==========================================
# SCRAPING SATU CHAPTER: AGUNGXNOVEL.MY.ID
# ==========================================
def scrape_chapter_agungx(url, soup):
    heading = None
    for tag in soup.find_all(['h1', 'h2', 'h3']):
        text = tag.get_text(strip=True)
        if re.match(r'^volume\s*\d+', text, re.IGNORECASE):
            heading = tag
            break
    if heading is None:
        heading = soup.find('h1') or soup.find('h2')

    final_title = heading.get_text(strip=True) if heading else "Chapter"

    elements = []
    if heading is not None:
        for tag in heading.find_all_next():
            if tag.name == 'a':
                link_text = tag.get_text(strip=True).lower()
                if any(k in link_text for k in ('sebelumnya', 'berikutnya', 'selanjutnya', 'kembali')):
                    break
                continue
            if tag.name in ('h1', 'h2', 'h3'):
                heading_text = tag.get_text(strip=True).lower()
                if 'diskusi' in heading_text or 'komentar' in heading_text:
                    break
                continue
            if tag.name == 'img':
                src = tag.get('src')
                if src:
                    src = urljoin(url, src)
                    elements.append({'type': 'img', 'src': src, 'referer': url})
            elif tag.name == 'p':
                text = tag.get_text(strip=True)
                if not text:
                    continue
                text_lower = text.lower()
                if any(junk in text_lower for junk in junk_keywords):
                    continue
                if not elements or elements[-1].get('value') != text:
                    elements.append({'type': 'text', 'value': text})

    return final_title, elements


# ==========================================
# SCRAPING SATU CHAPTER: MADARA
# ==========================================
_WIDGET_ANCESTOR_RE = re.compile(
    r'widget|sidebar|popular|trending|related|similar|latest[-_]?(post|update|release)|'
    r'manga-list|c-related|recommend',
    re.IGNORECASE
)


def _is_inside_widget(el):
    """True kalau `el` (atau salah satu leluhurnya, TIDAK TERMASUK
    <body>/<html>) ada di dalam section widget/sidebar (mis. 'Paling
    Populer', 'Related Manga', 'Latest Update' dst). Container chapter
    asli gak pernah nempel di dalam widget kayak gini, jadi ini dipakai
    buat DISKUALIFIKASI kandidat container yang salah nyasar ke situ.

    PENTING: berhenti pas ketemu <body>/<html>, JANGAN ikut cek class
    di tag itu -- banyak tema WordPress (termasuk Madara) naruh class
    layout umum di <body> kayak 'right-sidebar'/'left-sidebar'/
    'no-sidebar' (penanda ada-gaknya sidebar di LAYOUT halaman, bukan
    widget beneran). Karena <body> adalah leluhur SEMUA elemen di
    halaman, kalau ini ikut dicek, class layout kayak itu bakal
    mendiskualifikasi SEMUA kandidat container sekaligus -> 0 kata, 0
    gambar di semua chapter (bug yang sempat kejadian).

    PENTING #2: `el` SENDIRI juga dicek, bukan cuma leluhurnya. Kalau
    widget "Related"/"Populer"/"Latest Update"-nya SENDIRI yang
    kebetulan punya class ketangkep salah satu pola pencarian (mis.
    'text-left', 'entry-content'), dia harus tetep didiskualifikasi
    walau bukan dia yang jadi LELUHUR siapa-siapa. Tanpa ini, widget
    gede berisi sinopsis banyak novel lain bisa ke-anggep jadi
    'kontainer chapter', bikin jumlah kata meledak (ratusan ribu kata
    buat satu chapter)."""
    for candidate in [el] + list(el.parents):
        if not hasattr(candidate, 'get'):
            continue
        if getattr(candidate, 'name', None) in ('body', 'html'):
            break
        cls = candidate.get('class')
        el_id = candidate.get('id') or ''
        combined = ' '.join(cls) if cls else ''
        combined += ' ' + el_id
        if _WIDGET_ANCESTOR_RE.search(combined):
            return True
    return False


def _find_main_content_container(soup):
    """Cari elemen yang jadi 'badan' chapter. Coba class umum tema
    Madara/Mangabooth dulu (reading-content, text-left, dst) — ini bikin
    halaman yang isinya CUMA gambar (mis. halaman ilustrasi tanpa
    paragraf sama sekali) tetep ketemu kontainernya. Kalau gak nemu,
    fallback ke heuristik lama: div/article dengan <p> ATAU <img> anak
    langsung terbanyak.

    PENTING: tiap kandidat yang ketemu divalidasi dulu -- kalau dia
    (atau leluhurnya) ada di dalam widget/sidebar kayak 'Paling
    Populer'/'Related'/'Latest Update', dia DISKUALIFIKASI dan lanjut
    coba kandidat berikutnya. Class kayak 'text-left'/'post-body' itu
    generik banget dan bisa nyangkut di elemen widget yang gak
    berhubungan sama chapter yang lagi di-scrape -- tanpa validasi ini,
    gambar novel lain di widget itu ketauan ke-anggep punya chapter ini
    (nyasar ke bab yang salah)."""
    for cls_pattern in (
        r'reading-content', r'reader-content', r'text-left', r'c-blog__body',
        r'entry-content', r'chapter-content', r'cha-content', r'post-body',
        r'reading-detail', r'ep-content', r'entry-summary', r'epcontent',
    ):
        for el in soup.find_all(['div', 'article'], class_=re.compile(cls_pattern, re.IGNORECASE)):
            text_el = el.get_text()
            if 'Please enter your username' in text_el or 'Back to Archives' in text_el:
                continue
            # 'span.kdt-ilus' -- elemen kustom KDTNovels buat gambar
            # ilustrasi (BUKAN <img> biasa, lihat catatan di
            # scrape_chapter_kdtnovels). Halaman ilustrasi KDTNovels
            # gak punya <p>/<img> sama sekali, cuma span-span ini, jadi
            # kualifikasinya harus ikut ngecek ini juga.
            if el.find_all('p') or el.find_all('img') or el.find_all('span', class_='kdt-ilus'):
                # Prefer inner/deeper matching container if any exists,
                # to avoid picking up outer wrappers like reading-content-wrap
                for deeper in el.find_all(['div', 'article'], class_=re.compile(cls_pattern, re.IGNORECASE)):
                    if deeper is el:
                        continue
                    if 'Please enter your username' in deeper.get_text() or 'Back to Archives' in deeper.get_text():
                        continue
                    if deeper.find_all('p') or deeper.find_all('img') or deeper.find_all('span', class_='kdt-ilus'):
                        el = deeper
                return el

    best = None
    best_score = 0
    for tag in soup.find_all(['div', 'article']):
        if _is_inside_widget(tag):
            continue
        direct_p = tag.find_all('p', recursive=False)
        direct_img = tag.find_all('img', recursive=False)
        # Kontainer valid kalau punya minimal 2 paragraf langsung, ATAU
        # minimal 2 gambar langsung (buat halaman ilustrasi tanpa teks).
        if len(direct_p) < 1 and len(direct_img) < 1:
            continue
        score = sum(len(p.get_text(strip=True)) for p in direct_p) + len(direct_img) * 80
        if score > 150000:
            continue
        if score > best_score:
            best = tag
            best_score = score
    if best is not None:
        return best

    # Fallback KE-3: situs modern (React/Next.js, dst -- kayak KDTNovels)
    # sering bungkus TIAP <img> di dalam <div>/<figure> wrapper sendiri
    # (buat lazy-load/styling), jadi <img>-nya BUKAN anak langsung dari
    # kontainer utama dan gak kehitung sama fallback di atas yang pakai
    # recursive=False. Ini paling kerasa di halaman yang isinya CUMA
    # galeri gambar (mis. "Illustrations"), tanpa paragraf sama sekali.
    # Di sini kita cari <img> di kedalaman berapa pun (recursive), TAPI
    # kandidat cuma boleh lolos kalau dia SAMA SEKALI gak punya <p>
    # anak langsung -- biar gak nyasar milih wrapper gede/layout utama
    # yang isinya campur macem-macem elemen dari seluruh halaman.
    best = None
    best_score = 0
    for tag in soup.find_all(['div', 'article']):
        if _is_inside_widget(tag):
            continue
        if tag.find_all('p', recursive=False):
            continue
        nested_img = tag.find_all('img')
        if not nested_img:
            continue
        score = len(nested_img) * 80
        if score > 150000:
            continue
        if score > best_score:
            best = tag
            best_score = score
    return best


JUNK_SECTION_HEADINGS = re.compile(
    r'support kami|server discord|paling populer|comments?\s+for\s+chapter|'
    r'light novel discussion|leave a reply|related\s+(post|chapter)|'
    r'discord|donasi|discussion|kami menghargai privasi|iklan adalah|archnovel|'
    r'please enter your username|receive a link to create a new password|back to archives',
    re.IGNORECASE
)

_CHAPTER_TITLE_KEYWORD_RE = re.compile(
    r'^(bab|chapter|prolog|prologue|epilog|epilogue|ilustrasi|illustrations?|'
    r'take|episode|eps|bonus\s+cerita\s+pendek|extra)\b',
    re.IGNORECASE
)

# Kata kunci "label pendek" chapter non-nomor -- dipakai buat motong
# label h1 yang keulangan judul novel di depannya (lihat pemakaiannya
# di scrape_chapter_madara). Beda dari _CHAPTER_TITLE_KEYWORD_RE di
# atas (yang match di AWAL string doang, ^...), regex ini SENGAJA
# nyari di mana AJA di tengah string (gak dianchor ^) justru karena
# tujuannya nemuin titik potong, bukan validasi apakah keseluruhan
# baris itu judul.
_SHORT_LABEL_KEYWORDS_RE = re.compile(
    r'(ilustrasi|illustrations?|episode|eps\b|bonus\s+cerita\s+pendek|extra)',
    re.IGNORECASE
)


def _extract_pure_bold_lines(p_tag):
    """Kalau `p_tag` isinya CUMA tag <b>/<strong> (masing-masing dianggap
    1 "baris"), boleh dipisah <br>, TANPA ada teks polos lain nempel
    langsung di dalamnya -> balikin list teks tiap baris bold itu secara
    berurutan. Ini nangkep kasus beberapa penerjemah nulis judul+subtitle
    chapter dalam SATU <p> yang sama (mis. "<p><b>Take 1</b><br><b>Sub
    Judul</b></p>") alih-alih 2 <p> terpisah kayak biasanya. Balikin None
    kalau paragrafnya BUKAN pola murni bold-doang ini (mis. paragraf isi
    cerita biasa yang cuma kebetulan ada 1-2 kata di-bold di tengah
    kalimat) -> biar tetap diproses lewat jalur teks normal seperti biasa."""
    lines = []
    for child in p_tag.children:
        if isinstance(child, NavigableString):
            if child.strip():
                return None
            continue
        if child.name == 'br':
            continue
        if child.name in ('b', 'strong'):
            text = child.get_text(strip=True)
            if text:
                lines.append(text)
            continue
        return None
    return lines or None


def _flatten_content_container(parent):
    """Ambil elemen isi dari `parent` secara rekursif dengan mempertahankan
    urutan DOM aslinya. Elemen pembungkus (<div>, <p>/<li> yang berisi blok
    atau gambar lain) di-perluas ke anak-anaknya; elemen "daun" (blok teks
    tanpa turunan blok/img, tag <img>, heading) langsung masuk hasil. Ini
    bikin gambar & paragraf yang berselang-seling di dalam nested <p>
    (khas hasil import dari blogspot) urutannya tetep bener."""
    items = []
    for child in parent.find_all(True, recursive=False):
        name = child.name
        if name in ('script', 'style', 'ins', 'button', 'iframe'):
            continue
        if name == 'div':
            # Skip wrapper iklan AdSense (atau disclaimer privasi AdSense
            # yang di-inject situs sebagai banner), BUKAN paragraf cerita
            # yang ngomongin "masalah privasi" karakter. Dulu filter di
            # sini pakai substring "privasi/iklan/archnovel" tapi itu
            # ke-trigger sama dialog biasa (mis. "...ada masalah privasi
            # Kurumi-san..."), jadi 1 paragraf nge-skip jadi SELURUH
            # <div class="text-left"> yg isi 500+ paragraf ke-drop.
            cls = child.get('class') or []
            if any('ad-slot' in c.lower() or 'ad-ins' in c.lower() or 'archnovel' in c.lower() for c in cls):
                continue
        # Kalau masih ada blok/tag penting di dalamnya, turun dulu
        if child.find(['p', 'li', 'div', 'img', 'h2', 'h3', 'h4', 'h5']):
            items.extend(_flatten_content_container(child))
        else:
            items.append(child)
    return items


def scrape_chapter_madara(url, soup):
    # Hapus elemen iklan/disclaimer privasi ArchNovel jika ada di dalam soup
    for adv in soup.find_all(text=re.compile(r'Kami menghargai privasi|iklan adalah satu-satunya', re.IGNORECASE)):
        parent = adv.parent
        if parent:
            parent.decompose()

    container = _find_main_content_container(soup)
    chapter_title_parts = []
    elements = []

    if container is not None:
        targets = _flatten_content_container(container)
        for elem in targets:
            if elem.name == 'div':
                # Skip wrapper iklan/disclaimer (AdSense slot dll.).
                # Penting: filter pakai CLASS AdSense (`ad-slot`/`ad-ins`),
                # BUKAN substring "privasi/iklan" -- substring ke-trigger
                # sama dialog cerita yang nyebut kata "privasi" (mis.
                # "...ada masalah privasi Kurumi-san...") dan skip 1 div =
                # nge-drop ratusan paragraf sekaligus.
                cls = elem.get('class') or []
                if any('ad-slot' in c.lower() or 'ad-ins' in c.lower() or 'archnovel' in c.lower() for c in cls):
                    continue
            if elem.name in ('h2', 'h3', 'h4', 'h5'):
                heading_text = elem.get_text(strip=True)
                if JUNK_SECTION_HEADINGS.search(heading_text):
                    # Ketemu heading widget (Discord, Paling Populer,
                    # Comments, dst) -> berhenti total, jangan lanjut ke
                    # elemen sesudahnya sama sekali.
                    break
                continue

            # Ekstrak <img> yang nempel di dalam <p> (mis. <p><img ...></p>),
            # HANYA kalau paragrafnya gak punya blok turunan (div/p/li).
            # Kalau ada blok turunan (HTML dari blogspot sering NESTED p),
            # elemen ini adalah wrapper; turun rekursif biar urutan teks dan
            # gambar sesuai posisi aslinya di DOM (jangan hog semua gambar ke
            # depan teks).
            nested_block = elem.find(['div', 'p', 'li', 'h2', 'h3', 'h4', 'h5'])
            if elem.name == 'p' and elem.find_all('img') and nested_block:
                sub_targets = _flatten_content_container(elem)
                for sub_elem in sub_targets:
                    sub_text = sub_elem.get_text(' ', strip=True)
                    if sub_text:
                        if (
                            any(junk in sub_text.lower() for junk in junk_keywords)
                            or 'privasi' in sub_text.lower()
                            or 'iklan' in sub_text.lower()
                            or 'password' in sub_text.lower()
                            or 'username or email' in sub_text.lower()
                        ):
                            continue
                        if not elements or elements[-1].get('value') != sub_text:
                            elements.append({'type': 'text', 'value': sub_text})
                continue
            if elem.name == 'p' and elem.find_all('img'):
                for p_img in elem.find_all('img'):
                    src = p_img.get('src')
                    if src:
                        abs_src = urljoin(url, src)
                        parent_a = p_img.find_parent('a')
                        if parent_a and parent_a.get('href'):
                            href_abs = urljoin(url, parent_a['href'])
                            cur_slug = url.split('/manga/')[-1].split('/')[0] if '/manga/' in url else None
                            href_slug = href_abs.split('/manga/')[-1].split('/')[0] if '/manga/' in href_abs else None
                            if cur_slug and href_slug and cur_slug != href_slug:
                                continue
                        elements.append({'type': 'img', 'src': abs_src, 'referer': url})
                # Hapus <img> dari <p> biar teksnya aja yang diproses di bawah
                for p_img in elem.find_all('img'):
                    p_img.decompose()

            if elem.name == 'img':
                src = elem.get('src')
                if src:
                    abs_src = urljoin(url, src)
                    # Jaring pengaman terakhir: kalau <img> ini dibungkus
                    # <a> yang link-nya nunjuk ke MANGA LAIN (slug beda
                    # dari halaman yang lagi diproses), ini hampir pasti
                    # thumbnail widget "Related"/"Paling Populer" yang
                    # entah gimana kebawa masuk container -> skip, biar
                    # gak nyasar jadi gambar milik chapter ini.
                    parent_a = elem.find_parent('a')
                    if parent_a and parent_a.get('href'):
                        href_abs = urljoin(url, parent_a['href'])
                        cur_slug = url.split('/manga/')[-1].split('/')[0] if '/manga/' in url else None
                        href_slug = href_abs.split('/manga/')[-1].split('/')[0] if '/manga/' in href_abs else None
                        if cur_slug and href_slug and cur_slug != href_slug:
                            continue
                    elements.append({'type': 'img', 'src': abs_src, 'referer': url})
                continue

            # Kasus khusus: paragraf yang isinya beberapa baris bold
            # digabung jadi SATU <p> (mis. "Take 1" lalu "Pertunjukan
            # Dimulai" dipisah <br>, bukan 2 <p> terpisah). Kalau
            # dibiarkan lewat jalur teks normal di bawah, dua baris itu
            # bakal ke-gabung tanpa spasi jadi satu string aneh dan gak
            # kedetect sebagai judul sama sekali. Pecah dulu jadi
            # baris-baris terpisah, proses satu-satu pakai logika yang
            # sama kayak baris bold biasa.
            bold_lines = _extract_pure_bold_lines(elem) if elem.name == 'p' else None
            if bold_lines is not None:
                for line in bold_lines:
                    line_lower = line.lower()
                    if any(junk in line_lower for junk in junk_keywords):
                        continue
                    if _CHAPTER_TITLE_KEYWORD_RE.match(line_lower):
                        if line not in chapter_title_parts:
                            chapter_title_parts.append(line)
                        continue
                    if (
                        len(chapter_title_parts) == 1
                        and not elements
                        and not line.startswith(('"', '“', '"', "'", '‘', "'"))
                    ):
                        if line not in chapter_title_parts:
                            chapter_title_parts.append(line)
                        continue
                    if not elements or elements[-1].get('value') != line:
                        elements.append({'type': 'text', 'value': line})
                continue

            # Separator spasi + collapse whitespace: beberapa post
            # (situs Madara/WP) nulis tiap kalimat/baris dialog dipisah
            # <br> di DALAM satu <p> yang sama (bukan <p> terpisah).
            # Tanpa separator ini, get_text() nyambungin baris-baris itu
            # TANPA spasi (mis. "...AnimeRuang klub pecinta anime...",
            # atau dialog beruntun "Sihir Cantik!""Waaah! Ayo...").
            text = elem.get_text(' ', strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            if not text:
                continue
            text_lower = text.lower()
            if (
                any(junk in text_lower for junk in junk_keywords)
                or 'privasi' in text_lower
                or 'iklan' in text_lower
                or 'password' in text_lower
                or 'username or email' in text_lower
            ):
                continue

            # Paragraf yang isinya SATU baris bold utuh (mis. "**Bab 1**")
            # dan cocok pola judul chapter -> dianggap bagian judul, bukan isi.
            bold_children = elem.find_all(['b', 'strong'], recursive=False)
            is_full_bold_line = (
                len(bold_children) == 1
                and bold_children[0].get_text(strip=True) == text
            )
            if is_full_bold_line and _CHAPTER_TITLE_KEYWORD_RE.match(text_lower):
                if text not in chapter_title_parts:
                    chapter_title_parts.append(text)
                continue

            # Baris bold KEDUA persis setelah judul chapter (mis. "Take 4"
            # lalu "Pesta Teh") -> dianggap subtitle, bukan isi cerita.
            # Syarat sama kayak di scrape_chapter_blogger: baris judul
            # utama sudah ketemu (persis 1), belum ada elemen isi lain
            # yang kesimpan, teksnya bukan dialog (gak diawali tanda
            # kutip), dan baris ini sendiri beneran satu baris bold utuh.
            if (
                len(chapter_title_parts) == 1
                and not elements
                and not text.startswith(('"', '“', '"', "'", '‘', "'"))
                and is_full_bold_line
            ):
                if text not in chapter_title_parts:
                    chapter_title_parts.append(text)
                continue

            if not elements or elements[-1].get('value') != text:
                elements.append({'type': 'text', 'value': text})

    if chapter_title_parts:
        final_title = " - ".join(chapter_title_parts)
    else:
        h1 = soup.find('h1')
        h1_text = h1.get_text(strip=True) if h1 else "Chapter"
        label_match = re.search(r'-\s*volume\s*\d+\s*-\s*(.+)$', h1_text, re.IGNORECASE)
        final_title = label_match.group(1).strip() if label_match else h1_text
        # Kadang situs sumber nulis h1-nya dengan judul novel yang
        # KEULANG lagi sebelum label singkatnya, mis. "<Judul Novel
        # Panjang> - Volume 2 - <Judul Novel Panjang> Ilustrasi v2" ->
        # bikin judul entri Daftar Isi kepanjangan banget. Kalau salah
        # satu kata kunci "label pendek" ini ketemu di tengah/akhir
        # label, potong biar cuma mulai dari situ ke depan aja (buang
        # judul novel yang keulang di depannya).
        short_match = _SHORT_LABEL_KEYWORDS_RE.search(final_title)
        if short_match:
            final_title = final_title[short_match.start():].strip()

    return final_title, elements


def strip_novel_prefix_from_title(title, story_title):
    """Beberapa situs (mis. format judul post Blogger) nulis judul chapter
    lengkap dengan nama novel + volume di depannya, misal:
    'Gyaru no Jitensha o Naoshitara Natsuka Reta Volume 1 Chapter 1'.
    Untuk heading di dalam PDF kita cuma mau bagian chapter-nya aja, mis.
    'Chapter 1'. Fungsi ini strip nama novel (story_title) dan embel-embel
    'Volume N' di depannya kalau ketemu; kalau polanya gak cocok, judul
    asli dikembalikan apa adanya (aman, gak maksa)."""
    if not title or not story_title:
        return title
    t = title.strip()
    t = re.sub(r'^\[ENG\]\s*', '', t, flags=re.IGNORECASE)
    st = story_title.strip()
    if not t.lower().startswith(st.lower()):
        return t
    remainder = t[len(st):]
    remainder = re.sub(r'^[\s\-:–—]+', '', remainder)
    remainder = re.sub(r'^Volume\s*\d+\s*', '', remainder, flags=re.IGNORECASE)
    remainder = re.sub(r'^[\s\-:–—]+', '', remainder)
    return remainder if remainder else t


def truncate_for_toc(pdf, text, max_width):
    """Potong `text` (pakai font & ukuran yang lagi aktif di `pdf`) biar
    muat di lebar `max_width` (mm), kasih '...' di ujung kalau kepotong.
    Ini yang nyegah judul kepanjangan numpuk ke kolom nomor halaman di
    daftar isi."""
    if pdf.get_string_width(text) <= max_width:
        return text
    ellipsis = "..."
    while text and pdf.get_string_width(text + ellipsis) > max_width:
        text = text[:-1]
    text = text.rstrip()
    return (text + ellipsis) if text else ellipsis


# ==========================================
# SCRAPING + BUILD PDF UNTUK SATU BATCH LINK
# ==========================================
def build_pdf_for_urls(urls, output_path, cover_image_url=None, url_labels=None, cover_referer=None, source_domain=None, story_title=None):
    if SKIP_EXISTING_PDF and os.path.exists(output_path):
        log(f"   ⏭️ Dilewati (PDF sudah ada): {output_path}")
        STATS["volume_skip"] += 1
        return

    t_start_volume = time.time()

    pdf = NovelPDF()
    pdf.add_font(FONT_FAMILY, "", FONT_REGULAR)
    pdf.add_font(FONT_FAMILY, "B", FONT_BOLD)
    pdf.set_margins(left=25.4, top=25.4, right=25.4)
    pdf.set_auto_page_break(auto=True, margin=25.4)
    pdf.set_page_background((0, 0, 0))  # dark theme: semua halaman background hitam

    chapters_data = []
    first_cover_img = None
    first_cover_key_holder = {'img': None, 'key': None}  # dipakai mode Blogger

    # Untuk AgungX & Madara (mode index), cover diambil sekali dari halaman
    # novel (og:image), bukan dari dalam isi chapter.
    if cover_image_url:
        log("   📸 Mengunduh gambar sampul novel...")
        first_cover_img = fetch_image(cover_image_url, referer=cover_referer)

    for index, url in enumerate(urls, start=1):
        t_start_chapter = time.time()
        log(f"   [{index}/{len(urls)}] Scraping: {url}")

        # WorldNovel: konten sudah ada di cache, skip HTTP fetch
        if url.startswith('worldnovel://'):
            final_title, elements = scrape_chapter_worldnovel(url, None)

            link_id = pdf.add_link()
            chapters_data.append({
                'title': final_title,
                'elements': elements,
                'link_id': link_id,
                'page_number': None
            })

            word_count = sum(len(e['value'].split()) for e in elements if e['type'] == 'text')
            img_count = sum(1 for e in elements if e['type'] == 'img')
            elapsed = time.time() - t_start_chapter
            STATS["chapter_ok"] += 1
            log(f"   ✅ \"{final_title}\" — {word_count} kata, {img_count} gambar ({elapsed:.1f}s)")
            continue

        # Retry beberapa kali sebelum nyerah. Kadang server blog (Blogger
        # dkk) sempat ngasih status error sesaat (mis. 404/5xx) padahal
        # halamannya sebenarnya valid -- biasanya gara-gara request
        # beruntun terlalu cepat ke domain yang sama. Jeda dikit + coba
        # lagi seringnya langsung berhasil.
        MAX_RETRY = 3
        res = None
        last_error = None
        for attempt in range(1, MAX_RETRY + 1):
            try:
                res = fetch_url(url)
            except Exception as e:
                last_error = f"koneksi gagal: {e}"
                res = None
            else:
                if res.status_code == 200:
                    break
                last_error = f"status {res.status_code}"
            if attempt < MAX_RETRY:
                log(f"   ↻ Percobaan {attempt} gagal ({last_error}), coba lagi...", "WARN")
                time.sleep(2 * attempt)

        if res is None:
            log(f"   ⚠️ Gagal koneksi ({last_error}) setelah {MAX_RETRY}x percobaan, dilewati.", "WARN")
            STATS["chapter_gagal"] += 1
            STATS["errors"].append(f"{url} -> koneksi gagal: {last_error}")
            continue

        if res.status_code != 200:
            log(f"   ⚠️ Status {res.status_code} setelah {MAX_RETRY}x percobaan, dilewati.", "WARN")
            STATS["chapter_gagal"] += 1
            STATS["errors"].append(f"{url} -> status {res.status_code}")
            continue

        soup = BeautifulSoup(res.text, 'html.parser')

        if is_agungx(url):
            final_title, elements = scrape_chapter_agungx(url, soup)
        elif is_kdtnovels(url):
            fallback_label = url_labels.get(url) if url_labels else None
            final_title, elements = scrape_chapter_kdtnovels(url, soup, fallback_label=fallback_label)
        elif is_luminare(url) or is_yarnovel(soup):
            final_title, elements = scrape_chapter_luminare(url, soup)
        elif is_storyseedling(url):
            final_title, elements = scrape_chapter_storyseedling(url, soup)
        elif url.startswith('worldnovel://'):
            final_title, elements = scrape_chapter_worldnovel(url, soup)
        elif is_madara(soup) or is_wpcom(soup):
            final_title, elements = scrape_chapter_madara(url, soup)
        elif is_generic_wp(soup):
            final_title, elements = scrape_chapter_generic_wp(url, soup)
        else:
            fallback_label = url_labels.get(url) if url_labels else None
            final_title, elements = scrape_chapter_blogger(url, soup, first_cover_key_holder, fallback_label=fallback_label)
            if first_cover_img is None and first_cover_key_holder['img'] is not None:
                first_cover_img = first_cover_key_holder['img']

        link_id = pdf.add_link()
        chapters_data.append({
            'title': final_title,
            'elements': elements,
            'link_id': link_id,
            'page_number': None
        })

        word_count = sum(len(e['value'].split()) for e in elements if e['type'] == 'text')
        img_count = sum(1 for e in elements if e['type'] == 'img')
        elapsed = time.time() - t_start_chapter
        STATS["chapter_ok"] += 1
        log(f"   ✅ \"{final_title}\" — {word_count} kata, {img_count} gambar ({elapsed:.1f}s)")

    if not chapters_data:
        log("   ⚠️ Tidak ada bab yang berhasil di-scrape, PDF dilewati.", "WARN")
        return

    # COVER PAGE
    cover_drawn = False
    if first_cover_img:
        try:
            import io as _io, tempfile as _tmp
            raw = first_cover_img.read() if hasattr(first_cover_img, 'read') else first_cover_img
            # Convert WebP/PNG to JPEG jika perlu (fpdf2 gak support WebP)
            if raw[:4] == b'RIFF':  # WebP
                try:
                    from PIL import Image as _PIL
                    _img = _PIL.open(io.BytesIO(raw))
                    buf = io.BytesIO()
                    _img.convert('RGB').save(buf, format='JPEG', quality=90)
                    raw = buf.getvalue()
                except Exception:
                    pass
            _tmpf = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            _tmpf.write(raw)
            _tmpf.close()
            pdf.add_page()
            pdf._chapter_title = ""
            pdf.image(_tmpf.name, x=0, y=0, w=210, h=297)
            cover_drawn = True
            try:
                import os as _os
                _os.unlink(_tmpf.name)
            except Exception:
                pass
        except Exception as e:
            log(f"   ⚠️ Cover image gagal render: {e}", "WARN")
            pdf.add_page()
            pdf._chapter_title = ""
    if not cover_drawn:
        pdf.add_page()
        pdf._chapter_title = ""
        if story_title:
            pdf.set_font(FONT_FAMILY, 'B', 22)
            pdf.ln(80)
            pdf.multi_cell(0, 14, clean_unicode(story_title), align='C')
        if source_domain:
            pdf.ln(15)
            pdf.set_font(FONT_FAMILY, '', 10)
            pdf.set_text_color(140, 140, 140)
            pdf.cell(0, 8, f"Source: {source_domain}", align='C')
            pdf.set_text_color(255, 255, 255)
    pdf._cover_done = True

    # SOURCE PAGE
    if story_title or source_domain:
        pdf.add_page()
        pdf.set_page_background((0, 0, 0))
        pdf.set_text_color(255, 255, 255)
        pdf._chapter_title = story_title or ""
        pdf.set_font(FONT_FAMILY, 'B', 20)
        pdf.set_text_color(255, 255, 255)
        pdf.ln(60)
        if story_title:
            pdf.multi_cell(0, 12, clean_unicode(story_title), align='C')
        if source_domain:
            pdf.ln(20)
            pdf.set_font(FONT_FAMILY, '', 10)
            pdf.set_text_color(140, 140, 140)
            pdf.cell(0, 8, f"Source: {source_domain}", align='C')
            pdf.set_text_color(255, 255, 255)

    # ---------- DAFTAR ISI (selalu halaman terpisah) ----------
    # Kapasitas per halaman dihitung dari tinggi baris entri yang FIXED
    # (cell 8mm + ln 1.5mm = 9.5mm, karena truncate_for_toc udah jamin
    # tiap judul selalu 1 baris), dibagi sisa ruang halaman sampai batas
    # bawah (margin bawah 20mm). Halaman TOC pertama mulai dari y=45
    # (di bawah heading "DAFTAR ISI"), halaman TOC lanjutan mulai dari
    # y=20 (margin atas biasa).
    TOC_ROW_HEIGHT = 9.5
    TOC_PAGE_BOTTOM_Y = 297 - 20
    TOC_FIRST_PAGE_START_Y = 45
    TOC_OTHER_PAGE_START_Y = 20
    # -1 baris sebagai margin pengaman: kalau jumlah bab PAS banget sama
    # kapasitas hitungan (mis. 24 bab, kapasitas hitung 24), baris
    # terakhir jatuh persis di batas bawah halaman (page_break_trigger)
    # dan FPDF kadang masih nge-trigger auto page-break di titik situ
    # (nabrak/numpuk ke halaman bab pertama). Margin 1 baris ini bikin
    # perhitungan reservasi halaman TOC gak pernah mepet ke batas.
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
    pdf.cell(0, 15, "DAFTAR ISI", align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_line_width(0.6)
    pdf.line(pdf.get_x(), pdf.get_y(), 190, pdf.get_y())
    pdf.ln(10)
    toc_start_page = pdf.page_no()

    # Reservasi halaman KOSONG tambahan buat DAFTAR ISI kalau bab-nya
    # kebanyakan buat muat 1 halaman -- ini WAJIB dilakukan SEBELUM
    # halaman isi tiap bab ditambahkan, biar nomor halamannya gak
    # tabrakan/ke-timpa pas nanti kita balik ke halaman TOC buat diisi.
    if toc_pages_needed > 1:
        log(f"   ℹ️ Daftar isi butuh {toc_pages_needed} halaman ({num_chapters} bab).")
    for _ in range(toc_pages_needed - 1):
        pdf.add_page()
    toc_page_numbers = list(range(toc_start_page, toc_start_page + toc_pages_needed))

    for ch in chapters_data:
        pdf.add_page()
        # Footer cukup nama novelnya aja (story_title udah bersih, gak ada
        # "Volume N"), BUKAN ch['title'] yang di beberapa situs isinya
        # "Nama Novel Volume N Chapter M" lengkap.
        pdf._chapter_title = clean_unicode(story_title) if story_title else clean_unicode(ch['title'])
        ch['page_number'] = pdf.page_no()
        pdf.set_link(ch['link_id'], page=ch['page_number'])

        # Judul chapter center + garis pemisah. Strip nama novel/volume di
        # depan (kalau ada) biar headingnya cuma "Chapter N", gak perlu
        # nama novelnya lagi (itu sudah ada di footer & halaman sampul).
        chapter_heading = strip_novel_prefix_from_title(clean_unicode(ch['title']), story_title)
        pdf.chapter_title(chapter_heading)

        # Isi chapter
        is_first_paragraph = True
        for elem in ch['elements']:
            if elem['type'] == 'img':
                img_data = fetch_image(elem['src'], referer=elem.get('referer'))
                if img_data:
                    try:
                        pdf.image(img_data, x=25, w=160)
                        pdf.ln(6)
                    except Exception:
                        pass
            elif elem['type'] == 'text':
                clean_text = clean_unicode(elem['value'])
                pdf.set_font(FONT_FAMILY, "", 17)
                pdf.set_text_color(255, 255, 255)
                pdf.set_x(pdf.l_margin)
                # Paragraf pertama di chapter: rata kiri (gak nge-indent).
                # Paragraf berikutnya: baris pertamanya di-indent 0.5" pakai
                # spasi di depan teks (FPDF gak punya first-line-indent
                # bawaan; set_x doang bakal nge-indent SEMUA baris paragraf,
                # bukan cuma baris pertama).
                if is_first_paragraph:
                    body_text = clean_text
                    is_first_paragraph = False
                else:
                    body_text = pdf._first_line_indent_prefix() + clean_text
                # align='L' (bukan 'J'): baris terakhir tiap paragraf gak
                # ikut di-justify oleh FPDF, jadi paragraf pendek (1 baris)
                # vs paragraf panjang (>1 baris) kena perlakuan beda -> spasi
                # indentasi buatan di depan paragraf ikut diregangkan gak
                # rata pas di-justify. Pakai align kiri biar indentasi
                # konsisten di semua paragraf.
                pdf.multi_cell(0, 6.9, body_text, align='L')
                pdf.ln(2)

    # Isi entri DAFTAR ISI ke halaman-halaman yang udah direservasi di
    # atas. PENTING: gak boleh panggil pdf.add_page() di loop ini --
    # semua halaman TOC-nya udah dibikin duluan (toc_page_numbers), jadi
    # kita cuma pindah pointer `pdf.page` ke halaman yang udah ada, gak
    # pernah bikin halaman baru pas posisi lagi "mundur". Kalau sampai
    # add_page() kepanggil di sini pas pdf.page lagi di-rewind, FPDF
    # bakal nyisipin/nimpa halaman berikutnya (bukan nambah di ujung
    # dokumen) -- ini penyebab TOC dulu numpuk ketiban konten bab 1.
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
            clean_ch_title = strip_novel_prefix_from_title(clean_unicode(ch['title']), story_title)
            pdf.set_text_color(100, 180, 255)
            toc_text = f"{entry_idx}. {clean_ch_title}"
            toc_text = truncate_for_toc(pdf, toc_text, 138)
            pdf.cell(145, 8, toc_text, link=ch['link_id'])
            pdf.set_text_color(180, 180, 180)
            pdf.cell(0, 8, f"Hal. {ch['page_number']}", align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT, link=ch['link_id'])
            pdf.ln(1.5)
            pdf.set_font(FONT_FAMILY, size=11)

        if entry_idx >= num_chapters:
            break

    if entry_idx < num_chapters:
        # Jaring pengaman: seharusnya gak kejadian lagi (margin -1 baris
        # di atas), tapi kalau suatu saat masih kurang halaman TOC,
        # mendingan ketauan lewat log daripada entrinya diem-diem hilang
        # dari daftar isi.
        log(f"   ⚠️ {num_chapters - entry_idx} entri daftar isi gak kebagian tempat.", "WARN")

    pdf.output(output_path)
    STATS["volume_ok"] += 1
    elapsed_volume = time.time() - t_start_volume
    log(f"   ✅ Tersimpan: {output_path} ({len(chapters_data)} bab, {elapsed_volume:.1f}s)")


# ==========================================
# MAIN
# ==========================================
def print_summary(t_start_total):
    elapsed_total = time.time() - t_start_total
    log("\n" + "=" * 50)
    log("📊 RINGKASAN")
    log("=" * 50)
    log(f"Novel berhasil      : {STATS['novel_ok']}")
    log(f"Novel gagal         : {STATS['novel_gagal']}")
    log(f"Volume/PDF selesai  : {STATS['volume_ok']}")
    log(f"Volume dilewati     : {STATS['volume_skip']} (PDF sudah ada)")
    log(f"Chapter berhasil    : {STATS['chapter_ok']}")
    log(f"Chapter gagal       : {STATS['chapter_gagal']}")
    log(f"Gambar berhasil     : {STATS['gambar_ok']}")
    log(f"Gambar gagal        : {STATS['gambar_gagal']}")
    log(f"Total waktu         : {elapsed_total:.1f}s")
    if STATS["errors"]:
        log(f"\n⚠️ Detail {len(STATS['errors'])} error/skip:")
        for e in STATS["errors"]:
            log(f"   - {e}")
    log(f"\n📝 Log lengkap disimpan di: {LOG_FILE}")


if __name__ == "__main__":
    t_start_total = time.time()

    # Mode 1: PageUrls.txt (index otomatis). Mode 3: StartUrls.txt (crawl
    # manual dari chapter awal). Dua-duanya optional & bisa dipakai
    # bareng. Mode 2 (urls.txt) cuma jadi fallback kalau dua-duanya
    # kosong.
    toc_urls = load_urls(PAGE_URLS_FILE, required=False)
    start_entries = load_start_entries(STARTURL_FILE)

    did_something = False

    # ---------- MODE 1: PageUrls.txt ----------
    if toc_urls:
        did_something = True
        log(f"📄 {len(toc_urls)} halaman index ditemukan di '{PAGE_URLS_FILE}'")

        for toc_index, toc_url in enumerate(toc_urls, start=1):
            t_start_novel = time.time()
            log(f"\n########## [{toc_index}/{len(toc_urls)}] {toc_url} ##########")

            try:
                story_title, volumes = get_volumes_from_toc(toc_url)
            except Exception as e:
                log(f"   ⚠️ Dilewati, gagal parse halaman index: {e}", "ERROR")
                STATS["novel_gagal"] += 1
                STATS["errors"].append(f"{toc_url} -> {e}")
                continue

            safe_story_title = sanitize_filename(story_title)
            log(f"📚 Novel: {story_title} — {len(volumes)} volume terdeteksi")

            # Untuk AgungX, Madara & WordPress.com, ambil cover novel sekali
            # dari halaman index-nya (og:image), bukan dari gambar pertama
            # tiap chapter. Khusus WordPress.com, tiap volume kadang punya
            # gambar sampulnya sendiri di ToC (mis. CClaw) -> dipakai
            # duluan kalau ketemu, baru fallback ke cover umum di atas.
            # Khusus KDTNovels, og:image novel SAMA buat semua volume, jadi
            # cover per-volume diambil dari gambar pertama halaman
            # "Illustrations" volume masing-masing -> dipakai duluan,
            # fallback ke og:image kalau volume itu gak punya halaman
            # ilustrasi.
            cover_image_url = None
            vol_covers_wpcom = {}
            vol_covers_kdt = {}
            try:
                res_cover = fetch_url(toc_url)
                soup_cover = BeautifulSoup(res_cover.text, 'html.parser')
                if is_agungx(toc_url) or is_kdtnovels(toc_url) or is_madara(soup_cover) or is_wpcom(soup_cover) or is_worldnovel(toc_url) or is_storyseedling(toc_url):
                    cover_image_url = get_og_image(soup_cover)
                elif is_luminare(toc_url) or is_yarnovel(soup_cover):
                    # Ambil og:image dari chapter pertama kalau ada
                    for vol_num in sorted(volumes):
                        if volumes[vol_num]:
                            first_url = volumes[vol_num][0][0]
                            try:
                                r_ch = fetch_url(first_url)
                                s_ch = BeautifulSoup(r_ch.text, 'html.parser')
                                og_ch = s_ch.find('meta', property='og:image')
                                if og_ch and og_ch.get('content'):
                                    cover_image_url = og_ch['content']
                                    break
                            except Exception:
                                pass
                        break
                if is_wpcom(soup_cover):
                    vol_covers_wpcom = get_wpcom_volume_covers(soup_cover)
            except Exception:
                cover_image_url = None

            if is_kdtnovels(toc_url):
                vol_covers_kdt = get_kdtnovels_volume_covers(volumes)

            for vol_num in sorted(volumes):
                urls = [u for u, _label in volumes[vol_num]]
                url_labels = {u: label for u, label in volumes[vol_num]}
                log(f"\n=== Memproses Volume {vol_num} ({len(urls)} bab) ===")
                output_name = os.path.join(OUTPUT_DIR, f"{safe_story_title} Vol {vol_num}_[{SITE_NAME}].pdf")
                if vol_num in vol_covers_kdt:
                    vol_cover_url, vol_cover_referer = vol_covers_kdt[vol_num]
                elif vol_num in vol_covers_wpcom:
                    vol_cover_url, vol_cover_referer = vol_covers_wpcom[vol_num], toc_url
                else:
                    vol_cover_url, vol_cover_referer = cover_image_url, toc_url
                build_pdf_for_urls(
                    urls, output_name, cover_image_url=vol_cover_url,
                    url_labels=url_labels, cover_referer=vol_cover_referer,
                    source_domain=urlparse(toc_url).netloc, story_title=story_title
                )

            STATS["novel_ok"] += 1
            elapsed_novel = time.time() - t_start_novel
            log(f"🏁 Selesai '{story_title}' dalam {elapsed_novel:.1f}s")

        log("\n✅ Semua entri di PageUrls.txt selesai diproses!")

    # ---------- MODE 3: StartUrls.txt (crawl manual dari chapter awal) ----------
    if start_entries:
        did_something = True
        log(f"\n📄 {len(start_entries)} entri start-crawl ditemukan di '{STARTURL_FILE}'")

        for idx, (start_url, custom_title) in enumerate(start_entries, start=1):
            t_start_novel = time.time()
            log(f"\n########## [StartUrls {idx}/{len(start_entries)}] {start_url} ##########")

            root_url = guess_story_root_url(start_url)
            root_title, root_cover_image_url = (None, None)
            if root_url:
                log(f"   🔎 Coba ambil judul & cover dari halaman utama novel: {root_url}")
                root_title, root_cover_image_url = guess_title_and_cover_from_root(root_url)

            story_title = custom_title or root_title or guess_title_from_madara_chapter(start_url)
            safe_story_title = sanitize_filename(story_title)
            log(f"📚 Novel: {story_title} (mode start-url manual)")
            log(f"   🔗 Mulai crawl 'Next' dari: {start_url}")

            volumes = _madara_crawl_next(start_url)
            if not volumes:
                log("   ⚠️ Gagal crawl, tidak ada chapter ditemukan.", "ERROR")
                STATS["novel_gagal"] += 1
                STATS["errors"].append(f"{start_url} -> crawl gagal/kosong")
                continue

            for v in sorted(volumes):
                log(f"   ✅ Volume {v}: {len(volumes[v])} link ditemukan")

            for vol_num in sorted(volumes):
                urls = [u for u, _label in volumes[vol_num]]
                log(f"\n=== Memproses Volume {vol_num} ({len(urls)} bab) ===")

                vol_cover = guess_cover_from_first_chapter(urls[0]) if urls else None
                if vol_cover:
                    vol_cover_referer = urls[0]
                    log("   📸 Cover volume ini diambil dari gambar pertama halaman awalnya.")
                elif root_cover_image_url:
                    vol_cover = root_cover_image_url
                    vol_cover_referer = root_url
                    log("   📸 Gak ada gambar di halaman awal, pakai cover novel dari halaman utama.")
                else:
                    vol_cover_referer = None
                    log("   ℹ️ Cover gak ketemu, PDF bakal dibuat tanpa halaman sampul.", "WARN")

                output_name = os.path.join(OUTPUT_DIR, f"{safe_story_title} Vol {vol_num}_[{SITE_NAME}].pdf")
                build_pdf_for_urls(
                    urls, output_name, cover_image_url=vol_cover, cover_referer=vol_cover_referer,
                    source_domain=urlparse(start_url).netloc, story_title=story_title
                )

            STATS["novel_ok"] += 1
            elapsed_novel = time.time() - t_start_novel
            log(f"🏁 Selesai '{story_title}' dalam {elapsed_novel:.1f}s")

        log("\n✅ Semua entri di StartUrls.txt selesai diproses!")

    # ---------- MODE 2: urls.txt (fallback terakhir) ----------
    if not did_something:
        if os.path.exists(PAGE_URLS_FILE) or os.path.exists(STARTURL_FILE):
            log(f"ℹ️ '{PAGE_URLS_FILE}' / '{STARTURL_FILE}' ada tapi kosong, pakai mode manual '{URLS_FILE}'.")
        else:
            log(f"ℹ️ '{PAGE_URLS_FILE}' & '{STARTURL_FILE}' tidak ditemukan, pakai mode manual '{URLS_FILE}'.")

        urls = load_urls(URLS_FILE, required=True)
        story_title = title_from_slug(urls[0])
        safe_title = sanitize_filename(story_title)
        output_name = os.path.join(OUTPUT_DIR, f"{safe_title}_[{SITE_NAME}].pdf")
        log(f"\n=== Memproses {len(urls)} bab (mode manual urls.txt) ===")
        build_pdf_for_urls(urls, output_name, source_domain=urlparse(urls[0]).netloc, story_title=story_title)
        STATS["novel_ok"] += 1

    print_summary(t_start_total)