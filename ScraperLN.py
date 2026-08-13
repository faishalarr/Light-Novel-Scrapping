import io
import os
import re
import time
import datetime
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

# SKIP_EXISTING_PDF:
#   True  -> kalau file PDF output-nya SUDAH ADA di folder Result/, volume
#            itu dilewati total (gak fetch index/chapter sama sekali).
#            Cocok buat lanjutin proses yang sempat berhenti/putus di tengah.
#   False -> selalu scraping ulang & timpa PDF yang sudah ada.
SKIP_EXISTING_PDF = True

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
    'penerjemah', 'proffreader', 'proofreader', 'editor:',
    'dengarkan', 'menit baca'
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

# ==========================================
# LOGGING
# ==========================================
_LOG_TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = os.path.join(OUTPUT_DIR, f"log_{_LOG_TS}.txt")

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
        '\u00a0': ' ',
        '\u200b': '',
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
            STATS["gambar_ok"] += 1
            return io.BytesIO(img_res.content)
        log(f"    ⚠️ Gambar status {img_res.status_code} ({src_url[:50]}...)", "WARN")
    except Exception as e:
        log(f"    ⚠️ Gagal mengunduh gambar ({src_url[:50]}...): {e}", "WARN")
    STATS["gambar_gagal"] += 1
    return None


def normalize_img_src(src):
    """Samakan URL gambar Blogger yang sebenarnya SAMA tapi beda ukuran,
    biar deteksi 'gambar cover berulang' gak gagal cuma gara-gara beda
    parameter ukuran. Blogger punya BEBERAPA gaya URL yang beda-beda:

      Gaya 1 (folder ukuran sebelum nama file):
      .../img/b/R29vZ2xl/AVvXsE.../w452-h640/Img_3294_ReadEra.png
      .../img/b/R29vZ2xl/AVvXsE.../s2560/Img_3294_ReadEra.png
      -> nama file 'Img_3294_ReadEra.png' di paling belakang SELALU sama,
         cuma folder ukurannya (w452-h640 / s2560) yang beda.

      Gaya 2 (ukuran nempel pakai '='):
      .../img/a/AVvXsEj...=w640-h426-p-k-no-nu
      .../img/a/AVvXsEj...=s1600
      -> ID sebelum '=' yang jadi patokan.

      Gaya lama (.../s1600/namafile.jpg):
      -> nama file di paling belakang juga sama persis.

    Solusinya disatukan: selalu ambil SEGMEN PALING BELAKANG dari path,
    dan kalau segmen itu masih ada '=' (gaya 2), potong lagi di situ.
    """
    if not src:
        return src
    src = src.split('?')[0]  # buang query string kalau ada
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


# ==========================================
# MODE OTOMATIS: PARSING HALAMAN INDEX -> PER VOLUME
# ==========================================
def get_story_title(soup):
    h1 = soup.find('h1', class_='post-title') or soup.find('h1')
    title = h1.get_text(strip=True) if h1 else "Novel"
    title = re.sub(r'\[LN\]\s*Bahasa Indonesia', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*Volume\s*\d+\s*$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*~\s*$', '', title)
    return title.strip()


def _is_bold_chapter_link(a_tag):
    if a_tag.parent is not None and a_tag.parent.name in ('b', 'strong'):
        return True
    if a_tag.find(['b', 'strong'], recursive=False) is not None:
        return True
    return False


def get_volumes_from_toc(toc_url):
    log(f"📖 Membaca halaman index: {toc_url}")
    try:
        res = fetch_url(toc_url)
    except Exception as e:
        raise RuntimeError(f"Gagal koneksi ke halaman index: {e}")
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

    for tag in post_body.find_all(['b', 'strong', 'h2', 'h3', 'h4', 'a']):
        if tag.name != 'a':
            text = tag.get_text(strip=True)
            vol_match = re.match(r'^volume\s*(\d+)\s*$', text, re.IGNORECASE)
            if vol_match:
                current_vol = int(vol_match.group(1))
                volumes.setdefault(current_vol, [])
            continue

        href = tag.get('href')
        if not href or current_vol is None or href in seen:
            continue
        if not _is_bold_chapter_link(tag):
            continue
        label = tag.get_text(strip=True)
        seen.add(href)
        volumes[current_vol].append((href, label))

    if not volumes:
        log("  ℹ️ Tidak ada penanda 'Volume N', coba anggap 1 volume tunggal...", "WARN")
        fallback_links = []
        for a in post_body.find_all('a'):
            href = a.get('href')
            if not href or href in seen or not _is_bold_chapter_link(a):
                continue
            label = a.get_text(strip=True)
            if re.search(
                r'chapter|prolog|prologue|epilog|epilogue|illustrasi|afterword|extra|bonus|kata penutup',
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

    log(f"  📌 Judul terdeteksi: {story_title}")
    for v in sorted(volumes):
        log(f"  ✅ Volume {v}: {len(volumes[v])} link ditemukan")

    return story_title, volumes


# ==========================================
# SCRAPING + BUILD PDF UNTUK SATU BATCH LINK
# ==========================================
def build_pdf_for_urls(urls, output_path):
    if SKIP_EXISTING_PDF and os.path.exists(output_path):
        log(f"  ⏭️ Dilewati (PDF sudah ada): {output_path}")
        STATS["volume_skip"] += 1
        return

    t_start_volume = time.time()

    pdf = NovelPDF()
    pdf.add_font("DejaVu", "", FONT_REGULAR)
    pdf.add_font("DejaVu", "B", FONT_BOLD)
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_auto_page_break(auto=True, margin=20)

    chapters_data = []
    first_cover_img = None
    first_cover_key = None  # versi ternormalisasi dari first_cover_src

    for index, url in enumerate(urls, start=1):
        t_start_chapter = time.time()
        log(f"  [{index}/{len(urls)}] Scraping: {url}")
        try:
            res = fetch_url(url)
        except Exception as e:
            log(f"    ⚠️ Gagal koneksi ({e}), dilewati.", "WARN")
            STATS["chapter_gagal"] += 1
            STATS["errors"].append(f"{url} -> koneksi gagal: {e}")
            continue
        if res.status_code != 200:
            log(f"    ⚠️ Status {res.status_code}, dilewati.", "WARN")
            STATS["chapter_gagal"] += 1
            STATS["errors"].append(f"{url} -> status {res.status_code}")
            continue

        soup = BeautifulSoup(res.text, 'html.parser')
        post_body = soup.find('div', class_=re.compile(r'post-body|entry-content'))

        chapter_title_parts = []
        elements = []

        if post_body:
            for elem in post_body.find_all(['p', 'img', 'div', 'b', 'strong', 'h2', 'h3', 'span']):
                if elem.name == 'img':
                    src = elem.get('src')
                    if not src:
                        continue
                    img_key = normalize_img_src(src)

                    if first_cover_img is None:
                        log("    📸 Gambar sampul utama ditemukan, mengunduh...")
                        first_cover_img = fetch_image(src)
                        first_cover_key = img_key
                        # Gambar cover ini dipakai sbg halaman cover PDF,
                        # jangan diulang di isi chapter tempat dia ditemukan.
                        continue
                    if img_key == first_cover_key:
                        # Gambar sama dengan cover utama (dibandingkan versi
                        # ternormalisasi, jadi tetap kena walau ukurannya
                        # beda-beda tiap chapter) -> jangan diulang di isi.
                        continue
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
                        elif (
                            len(chapter_title_parts) == 1
                            and not elements
                            and not text.startswith(('"', '"', '"', "'", "'", "'"))
                        ):
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

        word_count = sum(len(e['value'].split()) for e in elements if e['type'] == 'text')
        img_count = sum(1 for e in elements if e['type'] == 'img')
        elapsed = time.time() - t_start_chapter
        STATS["chapter_ok"] += 1
        log(f"    ✅ \"{final_title}\" — {word_count} kata, {img_count} gambar ({elapsed:.1f}s)")

    if not chapters_data:
        log("  ⚠️ Tidak ada bab yang berhasil di-scrape, PDF dilewati.", "WARN")
        return

    if first_cover_img:
        pdf.add_page()
        pdf.image(first_cover_img, x=0, y=0, w=210, h=297)

    pdf.add_page()
    pdf.set_font("DejaVu", 'B', 18)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 15, "DAFTAR ISI", align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_line_width(0.6)
    pdf.line(pdf.get_x(), pdf.get_y(), 190, pdf.get_y())
    pdf.ln(10)
    toc_start_page = pdf.page_no()

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
    elapsed_volume = time.time() - t_start_volume
    log(f"  ✅ Tersimpan: {output_path} ({len(chapters_data)} bab, {elapsed_volume:.1f}s)")


# ==========================================
# MAIN
# ==========================================
def print_summary(t_start_total):
    elapsed_total = time.time() - t_start_total
    log("\n" + "=" * 50)
    log("📊 RINGKASAN")
    log("=" * 50)
    log(f"Novel berhasil     : {STATS['novel_ok']}")
    log(f"Novel gagal        : {STATS['novel_gagal']}")
    log(f"Volume/PDF selesai : {STATS['volume_ok']}")
    log(f"Volume dilewati    : {STATS['volume_skip']} (PDF sudah ada)")
    log(f"Chapter berhasil   : {STATS['chapter_ok']}")
    log(f"Chapter gagal      : {STATS['chapter_gagal']}")
    log(f"Gambar berhasil    : {STATS['gambar_ok']}")
    log(f"Gambar gagal       : {STATS['gambar_gagal']}")
    log(f"Total waktu        : {elapsed_total:.1f}s")
    if STATS["errors"]:
        log(f"\n⚠️ Detail {len(STATS['errors'])} error/skip:")
        for e in STATS["errors"]:
            log(f"  - {e}")
    log(f"\n📝 Log lengkap disimpan di: {LOG_FILE}")


if __name__ == "__main__":
    t_start_total = time.time()

    # Coba mode otomatis dulu (PageUrls.txt). Kalau file gak ada ATAU ada
    # tapi kosong, otomatis fallback ke mode manual (urls.txt) tanpa crash.
    toc_urls = load_urls(PAGE_URLS_FILE, required=False)

    if toc_urls:
        log(f"📄 {len(toc_urls)} halaman index ditemukan di '{PAGE_URLS_FILE}'")

        for toc_index, toc_url in enumerate(toc_urls, start=1):
            t_start_novel = time.time()
            log(f"\n########## [{toc_index}/{len(toc_urls)}] {toc_url} ##########")
            try:
                story_title, volumes = get_volumes_from_toc(toc_url)
            except Exception as e:
                log(f"  ⚠️ Dilewati, gagal parse halaman index: {e}", "ERROR")
                STATS["novel_gagal"] += 1
                STATS["errors"].append(f"{toc_url} -> {e}")
                continue

            safe_story_title = sanitize_filename(story_title)
            log(f"📚 Novel: {story_title} — {len(volumes)} volume terdeteksi")

            for vol_num in sorted(volumes):
                urls = [u for u, _label in volumes[vol_num]]
                log(f"\n=== Memproses Volume {vol_num} ({len(urls)} bab) ===")
                output_name = os.path.join(OUTPUT_DIR, f"{safe_story_title} Vol {vol_num}.pdf")
                build_pdf_for_urls(urls, output_name)

            STATS["novel_ok"] += 1
            elapsed_novel = time.time() - t_start_novel
            log(f"🏁 Selesai '{story_title}' dalam {elapsed_novel:.1f}s")

        log("\n✅ SEMUA NOVEL & VOLUME SELESAI DIPROSES!")
    else:
        if os.path.exists(PAGE_URLS_FILE):
            log(f"ℹ️ '{PAGE_URLS_FILE}' ada tapi kosong, pakai mode manual '{URLS_FILE}'.")
        else:
            log(f"ℹ️ '{PAGE_URLS_FILE}' tidak ditemukan, pakai mode manual '{URLS_FILE}'.")

        urls = load_urls(URLS_FILE, required=True)
        story_title = title_from_slug(urls[0])
        safe_title = sanitize_filename(story_title)
        output_name = os.path.join(OUTPUT_DIR, f"{safe_title}.pdf")
        log(f"\n=== Memproses {len(urls)} bab (mode manual urls.txt) ===")
        build_pdf_for_urls(urls, output_name)
        STATS["novel_ok"] += 1

    print_summary(t_start_total)