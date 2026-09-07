"""
Standalone scraper: Scrape Novel dari Blogger RSS feed.
Fitur: cover full, daftar isi, halaman sumber, penomoran, retry gambar.
Approach: bikin chapter pages (dengan gambar) via FPDF, lalu bikin
halaman TOC terpisah (tanpa gambar biar font gak corrupt), lalu merge
dengan PyMuPDF.
"""
import os, re, sys, io, time, math, tempfile
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests
from bs4 import BeautifulSoup
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from ScraperLN import (HEADERS, FONT_REGULAR, FONT_BOLD, FONT_DIR,
                       sanitize_filename, clean_unicode, log, fetch_image,
                       truncate_for_toc)

BLOG_URL = "https://kazuxnovel.blogspot.com"
LABEL    = "Konbini Goto"
STORY_TITLE = "Konbini Goutou kara Tasuketa Jimi Tenin ga, Onaji Class no Ubude Kawaii Gal datta"
RESULT_DIR  = "Result"

MAX_IMG_RETRY = 3

JUNK_KEYWORDS = [
    'tl :', 'ed :', '─────', '════', '-----', 'next chapter',
    'previous chapter', 'support kami', 'follow channel', 'baca juga',
    'facebook', 'twitter', 'whatsapp', 'telegram', 'share',
    'discord', 'komentar', 'donasi',
]

# ── 1. Fetch RSS ──────────────────────────────────────────────────────
def fetch_all_rss_chapters(label):
    base = f"{BLOG_URL}/feeds/posts/default/-/{label}?alt=json&max-results=50"
    all_entries = []
    start = 1
    while True:
        url = f"{base}&start-index={start}" if start > 1 else base
        res = requests.get(url, headers=HEADERS, timeout=20)
        data = res.json()
        entries = data.get('feed', {}).get('entry', [])
        if not entries:
            break
        for e in entries:
            title = e.get('title', {}).get('$t', '').strip()
            if not re.search(
                r'volume|chapter|prolog|epilog|afterword|ilustrasi|illustration|'
                r'short.?story|kata.?penutup|bonus|extra|prologue|epilogue',
                title, re.IGNORECASE
            ):
                continue
            href = ''
            for link in e.get('link', []):
                if link.get('rel') == 'alternate':
                    href = link.get('href', '')
                    break
            content = e.get('content', {}).get('$t', '')
            if href and content:
                all_entries.append((title, href, content))
        if len(entries) < 50:
            break
        start += 50
    return all_entries

# ── 2. Parse RSS content → elements ───────────────────────────────────
def parse_rss_content(content_html, referer_url):
    soup = BeautifulSoup(content_html, 'html.parser')
    elements = []
    for tag in soup.find_all(['p', 'img', 'h1', 'h2', 'h3', 'h4', 'h5']):
        if tag.name == 'img':
            src = tag.get('src')
            if src:
                elements.append({'type': 'img', 'src': src, 'referer': referer_url})
        elif tag.name in ('h1', 'h2', 'h3', 'h4', 'h5'):
            text = tag.get_text(strip=True)
            if text:
                elements.append({'type': 'text', 'value': text})
        else:
            text = tag.get_text(' ', strip=True)
            text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if not text or len(text) < 3:
                continue
            if any(j in text.lower() for j in JUNK_KEYWORDS):
                continue
            if re.match(r'^(tl|ed)\s*:', text, re.IGNORECASE):
                continue
            if not elements or elements[-1].get('value') != text:
                elements.append({'type': 'text', 'value': text})
    return elements

# ── 3. Sort chapters per volume ────────────────────────────────────────
def parse_volume_chapter(entries):
    volumes = {}
    for title, href, content in entries:
        vol = 1
        m = re.search(r'(?:volume|vol\.?|v)\s*(\d+)', title, re.IGNORECASE)
        if m:
            vol = int(m.group(1))
        else:
            m = re.search(r'V(\d+)', href)
            if m:
                vol = int(m.group(1))

        chap = 0.0
        m2 = re.search(r'chapter\s*(\d+(?:\.\d+)?)', title, re.IGNORECASE)
        if m2:
            chap = float(m2.group(1))
        elif re.search(r'prolog', title, re.IGNORECASE):
            chap = -2
        elif re.search(r'epilog', title, re.IGNORECASE):
            chap = 998
        elif re.search(r'afterword|kata.?penutup', title, re.IGNORECASE):
            chap = 999
        elif re.search(r'ilustrasi|illustration', title, re.IGNORECASE):
            chap = -3

        clean = re.sub(r'^.+?(?:volume|vol\.?|v)\s*\d+\s*', '', title, flags=re.IGNORECASE).strip()
        clean = re.sub(r'\s*Bahasa Indonesia\s*$', '', clean, flags=re.IGNORECASE).strip()
        if not clean:
            clean = title

        volumes.setdefault(vol, []).append((chap, href, clean, content))

    for v in volumes:
        volumes[v].sort(key=lambda t: (t[0], t[2]))
    return volumes

# ── 4. Helpers ─────────────────────────────────────────────────────────
def download_image(src, referer=None):
    raw = None
    for attempt in range(1, MAX_IMG_RETRY + 1):
        try:
            img_data = fetch_image(src, referer=referer)
            if img_data:
                raw = img_data.read() if hasattr(img_data, 'read') else img_data
                if raw and len(raw) > 100:
                    return raw
        except Exception:
            pass
        if attempt < MAX_IMG_RETRY:
            time.sleep(2 * attempt)
    return None


def save_temp_image(raw_bytes):
    header = raw_bytes[:4]
    if header == b'RIFF':
        ext = '.webp'
    elif header[:3] == b'\xff\xd8\xff':
        ext = '.jpg'
    elif header == b'\x89PNG':
        ext = '.png'
    else:
        ext = '.bin'
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp.write(raw_bytes)
    tmp.close()
    return tmp.name


def _extract_cover_from_illustration(chapters):
    for chap_num, href, label, content in chapters:
        if re.search(r'ilustrasi|illustration', label, re.IGNORECASE):
            soup = BeautifulSoup(content, 'html.parser')
            img = soup.find('img')
            if img and img.get('src'):
                return img['src'], label
    return None, None


# ── 5. Generate PDF (approach: merge chapter PDF + TOC PDF) ────────────
def make_pdf(volumes, story_title, out_dir=RESULT_DIR):
    os.makedirs(out_dir, exist_ok=True)

    try:
        import fitz as pymupdf
        HAS_PYMUPDF = True
    except ImportError:
        HAS_PYMUPDF = False
        log("⚠️ PyMuPDF tidak ada, TOC tanpa page number", "WARN")

    try:
        from PIL import Image as PILImage
        HAS_PIL = True
    except ImportError:
        HAS_PIL = False

    for vol_num in sorted(volumes):
        chapters = volumes[vol_num]
        safe_title = sanitize_filename(story_title)
        out_path = os.path.join(out_dir, f"{safe_title} Vol {vol_num}.pdf")
        if os.path.exists(out_path):
            log(f"⏭️ Dilewati (PDF sudah ada): {out_path}")
            continue

        log(f"\n{'='*50}")
        log(f"=== Memproses Volume {vol_num} ({len(chapters)} bab) ===")
        log(f"{'='*50}")

        # ──────────────────────────────────────────────────────────
        # STEP 1: Bikin halaman CONTENT (cover + source + chapters)
        #         Pake FPDF — gak ada masalah font karena TOC dipisah.
        # ──────────────────────────────────────────────────────────
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.set_margin(20)
        if os.path.exists(FONT_REGULAR):
            pdf.add_font("DejaVu", "", FONT_REGULAR)
            pdf.add_font("DejaVu", "B", FONT_BOLD)

        # Cover full page
        cover_src, cover_label = _extract_cover_from_illustration(chapters)
        cover_page_idx = 0
        if cover_src:
            raw = download_image(cover_src)
            if raw:
                tmp_path = save_temp_image(raw)
                pdf.add_page()
                cover_page_idx = pdf.page_no()
                if HAS_PIL:
                    try:
                        img = PILImage.open(tmp_path)
                        iw, ih = img.size
                        scale = min(210/iw, 297/ih)
                        dw, dh = iw*scale, ih*scale
                        x, y = (210-dw)/2, (297-dh)/2
                        pdf.image(tmp_path, x=x, y=y, w=dw, h=dh)
                        log(f"   📸 Cover: {cover_label[:50]} ({iw}x{ih})")
                    except Exception:
                        pdf.image(tmp_path, w=170, x=20, y=20)
                else:
                    pdf.image(tmp_path, w=170, x=20, y=20)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        # Source page
        pdf.add_page()
        source_page_idx = pdf.page_no()
        pdf.set_font("DejaVu", "B", 20)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(60)
        pdf.multi_cell(0, 12, clean_unicode(story_title), align='C')
        pdf.ln(20)
        pdf.set_font("DejaVu", "", 10)
        pdf.set_text_color(140, 140, 140)
        pdf.cell(0, 8, f"Source: {BLOG_URL}", align='C')
        pdf.ln(8)
        pdf.cell(0, 8, f"Volume {vol_num} — {len(chapters)} chapters", align='C')

        # Placeholder 1 halaman kosong untuk TOC
        toc_placeholder_page = pdf.page_no() + 1
        pdf.add_page()

        # Chapter pages
        chapters_data = []
        for chap_idx, (chap_num, url, label, content) in enumerate(chapters, 1):
            log(f"   [{chap_idx}/{len(chapters)}] {label[:60]}")
            t0 = time.time()

            elements = parse_rss_content(content, url)
            words = sum(len(e['value'].split()) for e in elements if e.get('type') == 'text')
            img_count = sum(1 for e in elements if e.get('type') == 'img')

            pdf.add_page()
            ch_page = pdf.page_no()

            # Chapter title
            pdf.set_font("DejaVu", 'B', 16)
            pdf.set_text_color(0, 0, 0)
            clean_title = clean_unicode(label)
            pdf.multi_cell(0, 8, clean_title, align='L')
            pdf.ln(4)
            y_line = pdf.get_y()
            pdf.set_line_width(0.5)
            pdf.line(pdf.get_x(), y_line, 190, y_line)
            pdf.ln(10)

            # Content
            pdf.set_font("DejaVu", size=11)
            for elem in elements:
                if elem['type'] == 'img':
                    raw = download_image(elem['src'], referer=elem.get('referer'))
                    if raw:
                        try:
                            tmp_path = save_temp_image(raw)
                            pdf.image(tmp_path, w=170)
                            pdf.ln(5)
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                elif elem['type'] == 'text':
                    clean_text = clean_unicode(elem['value'])
                    pdf.multi_cell(0, 6.5, clean_text, align='L')
                    pdf.ln(4)

            elapsed = time.time() - t0
            log(f"   ✅ \"{label[:50]}\" — {words} kata, {img_count} gambar ({elapsed:.1f}s)")
            chapters_data.append({'title': label, 'page_number': ch_page})

        # Simpan content PDF
        content_tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        content_tmp.close()
        pdf.output(content_tmp.name)

        # ──────────────────────────────────────────────────────────
        # STEP 2: Bikin halaman DAFTAR ISI (FPDF murni, tanpa gambar)
        # ──────────────────────────────────────────────────────────
        num_chapters = len(chapters_data)
        TOC_ROW_HEIGHT = 9.5
        TOC_PAGE_BOTTOM_Y = 297 - 20
        TOC_FIRST_PAGE_START_Y = 45
        TOC_OTHER_PAGE_START_Y = 20
        toc_capacity_first = max(1, int((TOC_PAGE_BOTTOM_Y - TOC_FIRST_PAGE_START_Y) // TOC_ROW_HEIGHT) - 1)
        toc_capacity_other = max(1, int((TOC_PAGE_BOTTOM_Y - TOC_OTHER_PAGE_START_Y) // TOC_ROW_HEIGHT) - 1)
        toc_pages_needed = 1 if num_chapters <= toc_capacity_first else (
            1 + math.ceil((num_chapters - toc_capacity_first) / toc_capacity_other)
        )

        toc_pdf = FPDF()
        toc_pdf.set_auto_page_break(auto=False)
        toc_pdf.set_margin(20)
        if os.path.exists(FONT_REGULAR):
            toc_pdf.add_font("DejaVu", "", FONT_REGULAR)
            toc_pdf.add_font("DejaVu", "B", FONT_BOLD)

        for toc_page_i in range(toc_pages_needed):
            toc_pdf.add_page()
            if toc_page_i == 0:
                toc_pdf.set_font("DejaVu", 'B', 18)
                toc_pdf.set_text_color(0, 0, 0)
                toc_pdf.cell(0, 15, "DAFTAR ISI", align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                toc_pdf.set_line_width(0.6)
                toc_pdf.line(toc_pdf.get_x(), toc_pdf.get_y(), 190, toc_pdf.get_y())
                toc_pdf.ln(10)
                capacity = toc_capacity_first
                start_y = TOC_FIRST_PAGE_START_Y
            else:
                toc_pdf.set_y(TOC_OTHER_PAGE_START_Y)
                capacity = toc_capacity_other
                start_y = TOC_OTHER_PAGE_START_Y

            toc_pdf.set_y(start_y)
            toc_pdf.set_font("DejaVu", size=11)

            # Hitung offset entry_idx untuk halaman ini
            if toc_page_i == 0:
                entry_offset = 0
            else:
                entry_offset = toc_capacity_first + (toc_page_i - 1) * toc_capacity_other

            for _ in range(capacity):
                idx = entry_offset + _
                if idx >= num_chapters:
                    break
                ch = chapters_data[idx]
                # Adjust page number: +toc_pages_needed karena halaman TOC
                # disisipkan di depan chapter pages
                adj_page = ch['page_number'] + toc_pages_needed
                clean_ch_title = clean_unicode(ch['title'])
                toc_text = f"{idx+1}. {clean_ch_title}"
                toc_text = truncate_for_toc(toc_pdf, toc_text, 138)
                toc_pdf.set_text_color(30, 80, 160)
                toc_pdf.cell(145, 8, toc_text)
                toc_pdf.set_text_color(100, 100, 100)
                toc_pdf.cell(0, 8, f"Hal. {adj_page}", align='R',
                             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                toc_pdf.ln(1.5)
                toc_pdf.set_font("DejaVu", size=11)

        toc_tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        toc_tmp.close()
        toc_pdf.output(toc_tmp.name)

        # ──────────────────────────────────────────────────────────
        # STEP 3: Merge — sisipkan halaman TOC setelah cover+source
        # ──────────────────────────────────────────────────────────
        if HAS_PYMUPDF:
            doc = pymupdf.open(content_tmp.name)
            toc_doc = pymupdf.open(toc_tmp.name)
            # Sisipkan halaman TOC setelah halaman source (indeks = toc_placeholder_page-1)
            insert_at = toc_placeholder_page - 1  # 0-indexed
            doc.insert_pdf(toc_doc, from_page=0, to_page=len(toc_doc)-1, start_at=insert_at)
            doc.save(out_path)
            doc.close()
            toc_doc.close()
        else:
            # Fallback: simpan content only
            os.replace(content_tmp.name, out_path)

        # Cleanup temp files
        try:
            os.unlink(content_tmp.name)
        except OSError:
            pass
        try:
            os.unlink(toc_tmp.name)
        except OSError:
            pass

        file_size_mb = os.path.getsize(out_path) / (1024 * 1024)
        log(f"✅ Tersimpan: {out_path} ({len(chapters_data)} bab, {file_size_mb:.1f} MB)")

# ── MAIN ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log(f"📡 Fetching chapters dari RSS feed (label: '{LABEL}')...")
    entries = fetch_all_rss_chapters(LABEL)
    log(f"📖 {len(entries)} chapter ditemukan")

    if not entries:
        log("❌ Tidak ada chapter ditemukan!", "ERROR")
        sys.exit(1)

    volumes = parse_volume_chapter(entries)
    for v in sorted(volumes):
        log(f"   Volume {v}: {len(volumes[v])} chapter")

    make_pdf(volumes, STORY_TITLE)
    log("\n🏁 Selesai!")
