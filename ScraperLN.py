import io
import os
import re
import time
import datetime
import requests
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
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
# Mendukung TIGA jenis situs, dideteksi otomatis (bukan hardcode domain,
# kecuali AgungX):
#   - Situs Blogger (mis. kaoritranslation.blogspot.com, dst)
#   - agungxnovel.my.id  (mis. https://agungxnovel.my.id/novel/<slug>)
#   - Situs bertema Madara/WordPress (mis. archtranslation.com/manga/<slug>/,
#     dan situs lain apapun yang pakai tema Madara — dideteksi otomatis
#     lewat meta generator halamannya, jadi gak perlu didaftar manual)
#
# Baris kosong atau yang diawali '#' diabaikan (bisa buat catatan).
#
# Contoh isi PageUrls.txt:
# https://kaoritranslation.blogspot.com/2025/12/zenmetsu-end-wo-shinimonogurui-de.html
# https://agungxnovel.my.id/novel/kimi-no-gachi
# https://archtranslation.com/manga/kuruna-megami-sama-to-issho-ni-sundara/
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

# Domain-domain yang dianggap "agungxnovel-style" (bukan Blogger).
AGUNGX_DOMAINS = ('agungxnovel.my.id',)

# Batas aman auto-crawl "Next" buat mode Madara, biar gak infinite loop
# kalau ada bug/redirect aneh.
MADARA_MAX_CHAPTERS = 3000

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
        log(f"   ⚠️ Gambar status {img_res.status_code} ({src_url[:50]}...)", "WARN")
    except Exception as e:
        log(f"   ⚠️ Gagal mengunduh gambar ({src_url[:50]}...): {e}", "WARN")
    STATS["gambar_gagal"] += 1
    return None


def is_agungx(url_or_domain):
    domain = urlparse(url_or_domain).netloc or url_or_domain
    return any(d in domain for d in AGUNGX_DOMAINS)


def is_madara(soup):
    """Deteksi tema Madara (dipakai banyak situs manga/novel WordPress,
    bukan cuma satu domain tertentu) lewat meta generator-nya."""
    gen = soup.find('meta', attrs={'name': 'generator'})
    if gen and gen.get('content') and 'madara' in gen['content'].lower():
        return True
    return False


def get_og_image(soup):
    og_image = soup.find('meta', attrs={'property': 'og:image'})
    if og_image and og_image.get('content'):
        return og_image['content']
    img = soup.find('img')
    return img.get('src') if img else None


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
        log("   ℹ️ Tidak ada penanda 'Volume N', coba anggap 1 volume tunggal...", "WARN")
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
        href = a.get('href')
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
                if a and a.get('href'):
                    chap_list.append((urljoin(base_url, a['href']), a.get_text(strip=True)))
            chap_list.reverse()  # AJAX: terbaru -> terlama
            volumes[vol_num] = chap_list
        else:
            a = li.find('a')
            if a and a.get('href'):
                flat_chapters.append((urljoin(base_url, a['href']), a.get_text(strip=True)))

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
        next_href = next_link.get('href') if next_link else None
        current_url = urljoin(base, next_href) if next_href else None

    return volumes


def _get_volumes_from_toc_madara_next_crawl(toc_url, soup, story_title):
    read_first = _find_link_by_text(soup, [r'read\s*first'])
    if read_first is None or not read_first.get('href'):
        raise RuntimeError(
            "Tidak menemukan tombol 'Read First' di halaman index Madara."
        )

    domain = urlparse(toc_url).netloc
    scheme = urlparse(toc_url).scheme
    base = f"{scheme}://{domain}"
    start_url = urljoin(base, read_first['href'])

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
# DISPATCHER: MODE OTOMATIS
# ==========================================
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
    elif is_madara(soup):
        log("   🔎 Terdeteksi sebagai situs bertema Madara, crawl via Prev/Next...")
        story_title, volumes = get_volumes_from_toc_madara(toc_url, soup)
    else:
        story_title, volumes = get_volumes_from_toc_blogger(toc_url, soup)

    log(f"   📌 Judul terdeteksi: {story_title}")
    for v in sorted(volumes):
        log(f"   ✅ Volume {v}: {len(volumes[v])} link ditemukan")

    return story_title, volumes


# ==========================================
# SCRAPING SATU CHAPTER: BLOGGER
# ==========================================
def scrape_chapter_blogger(url, soup, first_cover_key_holder):
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
                if first_cover_key_holder['img'] is None:
                    log("   📸 Gambar sampul utama ditemukan, mengunduh...")
                    first_cover_key_holder['img'] = fetch_image(src)
                    first_cover_key_holder['key'] = img_key
                    continue
                if img_key == first_cover_key_holder['key']:
                    continue
                elements.append({'type': 'img', 'src': src})
            else:
                text = elem.get_text(strip=True)
                if not text:
                    continue
                text_lower = text.lower()
                is_junk = any(junk in text_lower for junk in junk_keywords)
                if is_junk:
                    continue
                if re.match(r'^(chapter|prologue|prolog|epilogue|epilog)\s*\d*', text_lower):
                    if text not in chapter_title_parts:
                        chapter_title_parts.append(text)
                elif (
                    len(chapter_title_parts) == 1
                    and not elements
                    and not text.startswith(('"', '“', '"', "'", '‘', "'"))
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
        final_title = title_el.get_text(strip=True) if title_el else "Chapter"

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
                    elements.append({'type': 'img', 'src': src})
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
def _find_main_content_container(soup):
    """Cari elemen yang jadi 'badan' chapter. Coba class umum tema
    Madara/Mangabooth dulu (reading-content, text-left, dst) — ini bikin
    halaman yang isinya CUMA gambar (mis. halaman ilustrasi tanpa
    paragraf sama sekali) tetep ketemu kontainernya. Kalau gak nemu,
    fallback ke heuristik lama: div/article dengan <p> ATAU <img> anak
    langsung terbanyak."""
    for cls_pattern in (
        r'reading-content', r'text-left', r'c-blog__body',
        r'entry-content', r'chapter-content', r'cha-content', r'post-body',
    ):
        el = soup.find(['div', 'article'], class_=re.compile(cls_pattern, re.IGNORECASE))
        if el is not None and el.find_all(['p', 'img']):
            return el

    best = None
    best_score = 0
    for tag in soup.find_all(['div', 'article']):
        direct_p = tag.find_all('p', recursive=False)
        direct_img = tag.find_all('img', recursive=False)
        # Kontainer valid kalau punya minimal 2 paragraf langsung, ATAU
        # minimal 2 gambar langsung (buat halaman ilustrasi tanpa teks).
        if len(direct_p) < 2 and len(direct_img) < 2:
            continue
        score = sum(len(p.get_text(strip=True)) for p in direct_p) + len(direct_img) * 80
        if score > best_score:
            best = tag
            best_score = score
    return best


JUNK_SECTION_HEADINGS = re.compile(
    r'support kami|server discord|paling populer|comments?\s+for\s+chapter|'
    r'light novel discussion|leave a reply|related\s+(post|chapter)|'
    r'discord|donasi|discussion',
    re.IGNORECASE
)


def scrape_chapter_madara(url, soup):
    container = _find_main_content_container(soup)
    chapter_title_parts = []
    elements = []

    if container is not None:
        for elem in container.find_all(['p', 'img', 'h2', 'h3', 'h4', 'h5']):
            if elem.name in ('h2', 'h3', 'h4', 'h5'):
                heading_text = elem.get_text(strip=True)
                if JUNK_SECTION_HEADINGS.search(heading_text):
                    # Ketemu heading widget (Discord, Paling Populer,
                    # Comments, dst) -> berhenti total, jangan lanjut ke
                    # elemen sesudahnya sama sekali.
                    break
                continue

            if elem.name == 'img':
                src = elem.get('src')
                if src:
                    elements.append({'type': 'img', 'src': urljoin(url, src)})
                continue

            text = elem.get_text(strip=True)
            if not text:
                continue
            text_lower = text.lower()
            if any(junk in text_lower for junk in junk_keywords):
                continue

            # Paragraf yang isinya SATU baris bold utuh (mis. "**Bab 1**")
            # dan cocok pola judul chapter -> dianggap bagian judul, bukan isi.
            bold_children = elem.find_all(['b', 'strong'], recursive=False)
            is_full_bold_line = (
                len(bold_children) == 1
                and bold_children[0].get_text(strip=True) == text
            )
            if is_full_bold_line and re.match(
                r'^(bab|chapter|prolog|prologue|epilog|epilogue|ilustrasi)\b', text_lower
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

    return final_title, elements


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
def build_pdf_for_urls(urls, output_path, cover_image_url=None):
    if SKIP_EXISTING_PDF and os.path.exists(output_path):
        log(f"   ⏭️ Dilewati (PDF sudah ada): {output_path}")
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
    first_cover_key_holder = {'img': None, 'key': None}  # dipakai mode Blogger

    # Untuk AgungX & Madara (mode index), cover diambil sekali dari halaman
    # novel (og:image), bukan dari dalam isi chapter.
    if cover_image_url:
        log("   📸 Mengunduh gambar sampul novel...")
        first_cover_img = fetch_image(cover_image_url)

    for index, url in enumerate(urls, start=1):
        t_start_chapter = time.time()
        log(f"   [{index}/{len(urls)}] Scraping: {url}")

        try:
            res = fetch_url(url)
        except Exception as e:
            log(f"   ⚠️ Gagal koneksi ({e}), dilewati.", "WARN")
            STATS["chapter_gagal"] += 1
            STATS["errors"].append(f"{url} -> koneksi gagal: {e}")
            continue

        if res.status_code != 200:
            log(f"   ⚠️ Status {res.status_code}, dilewati.", "WARN")
            STATS["chapter_gagal"] += 1
            STATS["errors"].append(f"{url} -> status {res.status_code}")
            continue

        soup = BeautifulSoup(res.text, 'html.parser')

        if is_agungx(url):
            final_title, elements = scrape_chapter_agungx(url, soup)
        elif is_madara(soup):
            final_title, elements = scrape_chapter_madara(url, soup)
        else:
            final_title, elements = scrape_chapter_blogger(url, soup, first_cover_key_holder)
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
        toc_text = truncate_for_toc(pdf, toc_text, 138)
        pdf.cell(145, 8, toc_text, link=ch['link_id'])
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 8, f"Hal. {ch['page_number']}", align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT, link=ch['link_id'])
        pdf.ln(1.5)

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

            # Untuk AgungX & Madara, ambil cover novel sekali dari halaman
            # index-nya (og:image), bukan dari gambar pertama tiap chapter.
            cover_image_url = None
            try:
                res_cover = fetch_url(toc_url)
                soup_cover = BeautifulSoup(res_cover.text, 'html.parser')
                if is_agungx(toc_url) or is_madara(soup_cover):
                    cover_image_url = get_og_image(soup_cover)
            except Exception:
                cover_image_url = None

            for vol_num in sorted(volumes):
                urls = [u for u, _label in volumes[vol_num]]
                log(f"\n=== Memproses Volume {vol_num} ({len(urls)} bab) ===")
                output_name = os.path.join(OUTPUT_DIR, f"{safe_story_title} Vol {vol_num}.pdf")
                build_pdf_for_urls(urls, output_name, cover_image_url=cover_image_url)

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
                    log("   📸 Cover volume ini diambil dari gambar pertama halaman awalnya.")
                elif root_cover_image_url:
                    vol_cover = root_cover_image_url
                    log("   📸 Gak ada gambar di halaman awal, pakai cover novel dari halaman utama.")
                else:
                    log("   ℹ️ Cover gak ketemu, PDF bakal dibuat tanpa halaman sampul.", "WARN")

                output_name = os.path.join(OUTPUT_DIR, f"{safe_story_title} Vol {vol_num}.pdf")
                build_pdf_for_urls(urls, output_name, cover_image_url=vol_cover)

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
        output_name = os.path.join(OUTPUT_DIR, f"{safe_title}.pdf")
        log(f"\n=== Memproses {len(urls)} bab (mode manual urls.txt) ===")
        build_pdf_for_urls(urls, output_name)
        STATS["novel_ok"] += 1

    print_summary(t_start_total)