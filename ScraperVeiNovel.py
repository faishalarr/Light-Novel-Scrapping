import io
import os
import re
import html
import json
import time
import datetime
import requests
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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'
}

FONT_DIR = "fonts"
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")

for _path in (FONT_REGULAR, FONT_BOLD):
    if not os.path.exists(_path):
        raise FileNotFoundError(
            f"Font '{_path}' tidak ditemukan. Taruh DejaVuSans.ttf dan "
            f"DejaVuSans-Bold.ttf di folder '{FONT_DIR}/' sekali saja, "
            f"lalu jalankan lagi."
        )

OUTPUT_DIR = "Result"
os.makedirs(OUTPUT_DIR, exist_ok=True)

_LOG_TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = os.path.join(OUTPUT_DIR, f"log_{_LOG_TS}.txt")

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
    def footer(self):
        if self.page_no() > 1:
            self.set_y(-15)
            self.set_font("DejaVu", '', 9)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"{self.page_no()}", align='R')


def clean_unicode(text):
    if not text:
        return ""
    replacements = {'\u00a0': ' ', '\u200b': ''}
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    return text


def sanitize_filename(name):
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name or "Novel"


def fetch_url(url):
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.encoding = 'utf-8'
    return res


def fetch_image(src_url):
    try:
        img_res = requests.get(src_url, headers=HEADERS, timeout=20)
        if img_res.status_code == 200:
            STATS["gambar_ok"] += 1
            return io.BytesIO(img_res.content)
        log(f"    ⚠️ Gambar status {img_res.status_code} ({src_url[:50]}...)", "WARN")
    except Exception as e:
        log(f"    ⚠️ Gagal mengunduh gambar ({src_url[:50]}...): {e}", "WARN")
    STATS["gambar_gagal"] += 1
    return None


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


def get_series_and_chapters(entry_url):
    """Dari SATU URL chapter mana saja, ambil info series + daftar
    LENGKAP semua chapter (grouped nanti per volume)."""
    log(f"📖 Membaca: {entry_url}")
    res = fetch_url(entry_url)
    if res.status_code != 200:
        raise RuntimeError(f"Gagal membuka halaman (status {res.status_code}).")

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
    pdf.add_font("DejaVu", "", FONT_REGULAR)
    pdf.add_font("DejaVu", "B", FONT_BOLD)
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_auto_page_break(auto=True, margin=20)

    chapters_data = []

    for cm in chapters_meta:
        if cm.get('is_premium'):
            log(f"  🔒 Dilewati (chapter premium/berbayar): {cm.get('title')}")
            STATS["chapter_premium_skip"] += 1
            continue

        chapter_url = f"https://veinovel.com/series/{slug}/chapter/{cm['chapter_link']}"
        t_ch = time.time()
        log(f"  Scraping: {chapter_url}")
        try:
            res = fetch_url(chapter_url)
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

    # --- HALAMAN COVER (pakai cover_url dari data series) ---
    cover_url = series.get('cover_url')
    if cover_url:
        img_data = fetch_image(cover_url)
        if img_data:
            pdf.add_page()
            pdf.image(img_data, x=0, y=0, w=210, h=297)

    # --- DAFTAR ISI ---
    pdf.add_page()
    pdf.set_font("DejaVu", 'B', 18)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 15, "DAFTAR ISI", align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_line_width(0.6)
    pdf.line(pdf.get_x(), pdf.get_y(), 190, pdf.get_y())
    pdf.ln(10)
    toc_start_page = pdf.page_no()

    # --- ISI CHAPTER ---
    for ch in chapters_data:
        pdf.add_page()
        ch['page_number'] = pdf.page_no()
        pdf.set_link(ch['link_id'], page=ch['page_number'])

        clean_title = clean_unicode(ch['title'])
        pdf.start_section(clean_title)

        pdf.set_font("DejaVu", 'B', 16)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, 8, clean_title, align='L')
        pdf.ln(4)

        pdf.set_line_width(0.5)
        y_line = pdf.get_y()
        pdf.line(pdf.get_x(), y_line, 190, y_line)
        pdf.ln(10)

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

    # --- ISI LINK & NOMOR HALAMAN DI DAFTAR ISI ---
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


if __name__ == "__main__":
    t_start_total = time.time()
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
            output_name = os.path.join(OUTPUT_DIR, f"{safe_title} Vol {vol_num}.pdf")
            build_pdf_for_volume(series, volumes[vol_num], output_name)

        STATS["series_ok"] += 1

    log("\n✅ SEMUA SERIES SELESAI DIPROSES!")
    print_summary(t_start_total)