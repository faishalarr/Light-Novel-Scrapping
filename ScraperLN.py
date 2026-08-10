import io
import os
import re
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ==========================================
# KONFIGURASI
# ==========================================
# MODE 1 (OTOMATIS PER-VOLUME) — PRIORITAS UTAMA:
#   Taruh link halaman UTAMA/INDEX novel di file PAGE_URLS_FILE
#   (default: "PageUrls.txt"), SATU LINK PER BARIS. Boleh lebih dari
#   satu novel sekaligus — tiap baris = 1 novel, masing-masing bakal
#   otomatis kepecah jadi 1 PDF per volume ("Volume 1", "Volume 2", dst
#   yang ketemu di halaman index-nya).
#
#   Baris kosong atau yang diawali '#' diabaikan (bisa buat catatan).
#
#   Contoh isi PageUrls.txt:
#   https://kaoritranslation.blogspot.com/2025/12/zenmetsu-end-wo-shinimonogurui-de.html
#   https://kaoritranslation.blogspot.com/2025/01/novel-lain-index.html
PAGE_URLS_FILE = "PageUrls.txt"

# MODE 2 (MANUAL, SATU PDF GABUNGAN) — FALLBACK:
#   Dipakai HANYA kalau PAGE_URLS_FILE tidak ditemukan / tidak ada.
#   Baca semua link chapter dari file urls.txt, satu link per baris,
#   digabung jadi satu PDF (perilaku script versi lama).
URLS_FILE = "urls.txt"

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
# FONT UNICODE (WAJIB, biar karakter aneh gak jadi "??")
# ==========================================
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


class NovelPDF(FPDF):
    def footer(self):
        if self.page_no() > 1:
            self.set_y(-15)
            self.set_font("DejaVu", '', 9)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"{self.page_no()}", align='R')


# ==========================================
# HELPER UMUM
# ==========================================
def clean_unicode(text):
    if not text:
        return ""
    replacements = {
        '\u00a0': ' ',   # non-breaking space
        '\u200b': '',    # zero-width space
    }
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
            return io.BytesIO(img_res.content)
    except Exception as e:
        print(f"    ⚠️ Gagal mengunduh gambar ({src_url[:30]}...): {e}")
    return None


def title_from_slug(url):
    """Judul fallback dari slug URL (dipakai di mode manual urls.txt)."""
    path = urlparse(url).path
    slug = os.path.basename(path)
    slug = re.sub(r'\.html?$', '', slug, flags=re.IGNORECASE)
    slug = re.sub(r'_\d+$', '', slug)
    words = [w for w in slug.split('-') if w]
    return ' '.join(w.capitalize() for w in words)


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


# ==========================================
# MODE OTOMATIS: PARSING HALAMAN INDEX -> PER VOLUME
# ==========================================
def get_story_title(soup):
    h1 = soup.find('h1', class_='post-title') or soup.find('h1')
    title = h1.get_text(strip=True) if h1 else "Novel"
    title = re.sub(r'\[LN\]\s*Bahasa Indonesia', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*~\s*$', '', title)
    return title.strip()


def get_volumes_from_toc(toc_url):
    """Baca halaman index novel, kelompokkan link chapter per 'Volume N'.

    Pola yang dicari: tag <b>/<strong> yang isinya persis "Volume 1",
    "Volume 2", dst sebagai penanda batas volume, lalu tag <b>/<strong>
    berikutnya yang membungkus <a> (mis. **[Chapter 1](url)**) dianggap
    sebagai isi volume tersebut, sampai ketemu penanda "Volume N" lagi.

    Return: (story_title, {1: [(url, label), ...], 2: [...], ...})
    """
    print(f"📖 Membaca halaman index: {toc_url}")
    res = fetch_url(toc_url)
    if res.status_code != 200:
        raise RuntimeError(f"Gagal membuka halaman index (status {res.status_code}).")

    soup = BeautifulSoup(res.text, 'html.parser')
    post_body = soup.find('div', class_=re.compile(r'post-body|entry-content'))
    if not post_body:
        raise RuntimeError("Tidak menemukan konten utama (post-body) di halaman index.")

    story_title = get_story_title(soup)

    volumes = {}
    current_vol = None
    seen = set()

    for tag in post_body.find_all(['b', 'strong']):
        text = tag.get_text(strip=True)

        vol_match = re.match(r'^volume\s*(\d+)\s*$', text, re.IGNORECASE)
        if vol_match:
            current_vol = int(vol_match.group(1))
            volumes.setdefault(current_vol, [])
            continue

        a = tag.find('a')
        if a and current_vol is not None:
            href = a.get('href')
            label = a.get_text(strip=True)
            if href and href not in seen:
                seen.add(href)
                volumes[current_vol].append((href, label))

    if not volumes:
        raise RuntimeError(
            "Tidak menemukan pola 'Volume 1', 'Volume 2', dst di halaman index. "
            "Mungkin struktur halamannya beda, cek manual / pakai mode urls.txt."
        )

    for v in sorted(volumes):
        print(f"  ✅ Volume {v}: {len(volumes[v])} link ditemukan")

    return story_title, volumes


# ==========================================
# SCRAPING + BUILD PDF UNTUK SATU BATCH LINK
# (dipakai baik utk 1 PDF gabungan maupun 1 PDF per volume)
# ==========================================
def build_pdf_for_urls(urls, output_path):
    pdf = NovelPDF()
    pdf.add_font("DejaVu", "", FONT_REGULAR)
    pdf.add_font("DejaVu", "B", FONT_BOLD)
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_auto_page_break(auto=True, margin=20)

    chapters_data = []
    first_cover_img = None

    for index, url in enumerate(urls, start=1):
        print(f"  [{index}/{len(urls)}] Scraping konten bab: {url}")
        res = fetch_url(url)
        if res.status_code != 200:
            print(f"    ⚠️ Gagal ({res.status_code}), dilewati.")
            continue

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
                            print("    📸 Gambar Sampul Utama ditemukan!")
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

    if not chapters_data:
        print("  ⚠️ Tidak ada bab yang berhasil di-scrape, PDF dilewati.")
        return

    # --- HALAMAN COVER ---
    if first_cover_img:
        pdf.add_page()
        pdf.image(first_cover_img, x=0, y=0, w=210, h=297)

    # --- HALAMAN DAFTAR ISI ---
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
    print(f"  ✅ Tersimpan: {output_path}")


# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    if os.path.exists(PAGE_URLS_FILE):
        toc_urls = load_urls(PAGE_URLS_FILE)
        print(f"📄 {len(toc_urls)} halaman index ditemukan di '{PAGE_URLS_FILE}'\n")

        for toc_index, toc_url in enumerate(toc_urls, start=1):
            print(f"\n########## [{toc_index}/{len(toc_urls)}] {toc_url} ##########")
            try:
                story_title, volumes = get_volumes_from_toc(toc_url)
            except Exception as e:
                print(f"  ⚠️ Dilewati, gagal parse halaman index: {e}")
                continue

            safe_story_title = sanitize_filename(story_title)
            print(f"📚 Novel: {story_title} — {len(volumes)} volume terdeteksi\n")

            for vol_num in sorted(volumes):
                urls = [u for u, _label in volumes[vol_num]]
                print(f"\n=== Memproses Volume {vol_num} ({len(urls)} bab) ===")
                output_name = os.path.join(OUTPUT_DIR, f"{safe_story_title} Vol {vol_num}.pdf")
                build_pdf_for_urls(urls, output_name)

        print("\n✅ SEMUA NOVEL & VOLUME SELESAI DIPROSES!")
    else:
        print(f"ℹ️ '{PAGE_URLS_FILE}' tidak ditemukan, pakai mode manual '{URLS_FILE}'.")
        urls = load_urls(URLS_FILE)
        story_title = title_from_slug(urls[0])
        safe_title = sanitize_filename(story_title)
        output_name = os.path.join(OUTPUT_DIR, f"{safe_title}.pdf")
        print(f"\n=== Memproses {len(urls)} bab (mode manual urls.txt) ===")
        build_pdf_for_urls(urls, output_name)