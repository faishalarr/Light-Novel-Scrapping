import io
import os
import re
import requests
from bs4 import BeautifulSoup
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ==========================================
# BACA DAFTAR URL DARI FILE TERPISAH
# ==========================================
# Taruh link chapter di file "urls.txt", satu link per baris.
# Baris kosong atau yang diawali '#' akan diabaikan (bisa dipakai untuk
# comment/catatan).
URLS_FILE = "urls.txt"

def load_urls(path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"File '{path}' tidak ditemukan. Buat file '{path}' berisi "
            f"link chapter, satu link per baris."
        )
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    if not urls:
        raise ValueError(f"File '{path}' kosong, tidak ada link yang bisa diproses.")
    return urls

URLS = load_urls(URLS_FILE)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'
}

junk_keywords = [
    'if you are not comfortable', 'jika kalian tidak nyaman',
    'related posts', 'trakteer', 'ko-fi', 'discord',
    'previous chapter', 'next chapter', 'toc', 'table of contents',
    'penerjemah:', 'proffreader:', 'proofreader:', 'dengarkan', 'menit baca'
]

# ==========================================
# 0. FONT UNICODE (FIX PERMANEN "??")
# ==========================================
# Akar masalah "--" jadi "??" adalah font bawaan FPDF (Helvetica) cuma
# support charset latin-1, jadi karakter seperti em dash (—), smart quotes
# ("" ''), dsb selalu berpotensi jadi '?'. Solusinya: pakai font TTF
# Unicode (DejaVu Sans) supaya SEMUA karakter Unicode bisa dirender apa
# adanya.
#
# Taruh DejaVuSans.ttf & DejaVuSans-Bold.ttf SEKALI di folder "fonts/"
# di sebelah script ini (satu kali saja, tidak perlu didownload lagi
# tiap run). Kalau file belum ada, script akan berhenti dan kasih tahu.

FONT_DIR = "fonts"
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

for path in (FONT_REGULAR, FONT_BOLD):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Font '{path}' tidak ditemukan. Taruh DejaVuSans.ttf dan "
            f"DejaVuSans-Bold.ttf di folder '{FONT_DIR}/' sekali saja, "
            f"lalu jalankan lagi."
        )

def clean_unicode(text):
    """Sekarang hanya untuk kerapian kosmetik (bukan lagi untuk 'menyelamatkan'
    karakter dari font terbatas), karena font DejaVu sudah full Unicode."""
    if not text:
        return ""
    replacements = {
        '\u00a0': ' ',   # non-breaking space
        '\u200b': '',    # zero-width space
    }
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    return text

class NovelPDF(FPDF):
    def footer(self):
        if self.page_no() > 1:
            self.set_y(-15)
            self.set_font("DejaVu", '', 9)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"{self.page_no()}", align='R')

pdf = NovelPDF()
pdf.add_font("DejaVu", "", FONT_REGULAR)
pdf.add_font("DejaVu", "B", FONT_BOLD)
pdf.set_margins(left=20, top=20, right=20)
pdf.set_auto_page_break(auto=True, margin=20)

def fetch_url(url):
    """Fetch halaman dan paksa encoding UTF-8 agar tidak mojibake."""
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.encoding = 'utf-8'
    return res

def fetch_image(src_url):
    try:
        img_res = requests.get(src_url, headers=HEADERS, timeout=20)
        if img_res.status_code == 200:
            return io.BytesIO(img_res.content)
    except Exception as e:
        print(f"  ⚠️ Gagal mengunduh gambar ({src_url[:30]}...): {e}")
    return None

chapters_data = []
first_cover_img = None

# ==========================================
# 1. SCRAPING DATA DARI WEB
# ==========================================
for index, url in enumerate(URLS, start=1):
    print(f"[{index}/{len(URLS)}] Scraping konten bab: {url}")
    res = fetch_url(url)

    if res.status_code == 200:
        soup = BeautifulSoup(res.text, 'html.parser')
        post_body = soup.find('div', class_=re.compile(r'post-body|entry-content'))

        chapter_title_parts = []
        elements = []

        if post_body:
            for elem in post_body.find_all(['p', 'img', 'div', 'b', 'strong', 'h2', 'h3']):
                if elem.name == 'img':
                    src = elem.get('src')
                    if src:
                        if first_cover_img is None:
                            print("  📸 Gambar Sampul Utama ditemukan!")
                            first_cover_img = fetch_image(src)
                        elements.append({'type': 'img', 'src': src})

                else:
                    text = elem.get_text(strip=True)
                    if not text:
                        continue

                    text_lower = text.lower()
                    is_junk = any(junk in text_lower for junk in junk_keywords)

                    if not is_junk:
                        if re.match(r'^(chapter|prologue|prolog|epilogue|epilog)\s*\d*', text_lower):
                            if text not in chapter_title_parts:
                                chapter_title_parts.append(text)
                        elif len(chapter_title_parts) >= 1 and len(text) < 80 and not elements:
                            if text not in chapter_title_parts:
                                chapter_title_parts.append(text)
                        else:
                            if not elements or elements[-1].get('value') != text:
                                elements.append({'type': 'text', 'value': text})

        if chapter_title_parts:
            final_title = " - ".join(chapter_title_parts)
        else:
            title_el = soup.find('h1', class_='post-title') or soup.find('h1')
            final_title = title_el.get_text(strip=True) if title_el else f"Chapter {index}"

        # Hapus teks judul duplikat di awal paragraf cerita
        if elements and elements[0]['type'] == 'text':
            first_text = elements[0]['value'].strip().lower()
            if first_text in final_title.lower() or any(part.lower() == first_text for part in chapter_title_parts):
                elements.pop(0)

        link_id = pdf.add_link()

        chapters_data.append({
            'title': final_title,
            'elements': elements,
            'link_id': link_id,
            'page_number': None
        })

# ==========================================
# 2. MERAKIT HALAMAN PDF
# ==========================================

# --- HALAMAN 1: COVER FULL PAGE ---
if first_cover_img:
    print("Menyusun Halaman Sampul (Cover)...")
    pdf.add_page()
    pdf.image(first_cover_img, x=0, y=0, w=210, h=297)

# --- HALAMAN 2: DAFTAR ISI ---
print("Menyusun Halaman Daftar Isi...")
pdf.add_page()
pdf.set_font("DejaVu", 'B', 18)
pdf.set_text_color(0, 0, 0)
pdf.cell(0, 15, "DAFTAR ISI", align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.set_line_width(0.6)
pdf.line(pdf.get_x(), pdf.get_y(), 190, pdf.get_y())
pdf.ln(10)

toc_start_page = pdf.page_no()

# --- HALAMAN 3 DST: ISI CHAPTER ---
print("Menyusun Isi Bab...")
for ch in chapters_data:
    pdf.add_page()

    ch['page_number'] = pdf.page_no()
    pdf.set_link(ch['link_id'], page=ch['page_number'])

    clean_title = clean_unicode(ch['title'])

    # Bookmark Native Sidebar (PDF Outlines)
    pdf.start_section(clean_title)

    # Judul Bab
    pdf.set_font("DejaVu", 'B', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(0, 8, clean_title, align='L')
    pdf.ln(4)

    pdf.set_line_width(0.5)
    y_line = pdf.get_y()
    pdf.line(pdf.get_x(), y_line, 190, y_line)
    pdf.ln(10)

    # Isi Cerita
    pdf.set_font("DejaVu", size=11)
    for elem in ch['elements']:
        if elem['type'] == 'img':
            img_data = fetch_image(elem['src'])
            if img_data:
                try:
                    pdf.image(img_data, x=25, w=160)
                    pdf.ln(6)
                except Exception:
                    pass

        elif elem['type'] == 'text':
            clean_text = clean_unicode(elem['value'])
            pdf.multi_cell(0, 6.5, clean_text, align='L')
            pdf.ln(4)

# ==========================================
# 3. MENGISI LINK & NOMOR HALAMAN DI DAFTAR ISI
# ==========================================
print("Menghubungkan Link & Nomor Halaman di Daftar Isi...")
pdf.page = toc_start_page

pdf.set_y(45)
pdf.set_font("DejaVu", size=11)

for i, ch in enumerate(chapters_data, start=1):
    clean_ch_title = clean_unicode(ch['title'])

    pdf.set_text_color(30, 80, 160)
    toc_text = f"{i}. {clean_ch_title}"
    pdf.cell(145, 8, toc_text, link=ch['link_id'])

    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, f"Hal. {ch['page_number']}", align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT, link=ch['link_id'])
    pdf.ln(1.5)

# ==========================================
# 4. SIMPAN PDF
# ==========================================
OUTPUT_DIR = "Result"
os.makedirs(OUTPUT_DIR, exist_ok=True)

output_name = os.path.join(OUTPUT_DIR, "Naze_ka_S_kyuu_V5_Interactive.pdf")
print("\nSedang menyimpan file PDF...")
pdf.output(output_name)
print(f"✅ SELESAI! File tersimpan dengan nama: {output_name}")