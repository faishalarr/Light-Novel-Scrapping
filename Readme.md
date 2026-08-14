# Light Novel Scrapping

Scrape light novel/manga terjemahan fan dari beberapa situs, lalu otomatis
disusun jadi PDF rapi lengkap dengan cover, daftar isi yang bisa diklik,
dan dipecah per volume.

## Fitur

- **Deteksi situs otomatis** — script otomatis ngenalin jenis situsnya,
  gak perlu setting manual per domain:
  - Situs **Blogger** (mis. `*.blogspot.com`)
  - **AgungX Novel** (`agungxnovel.my.id`)
  - Situs bertema **Madara/WordPress** (mis. `archtranslation.com`, dan
    situs lain apa pun yang pakai tema Madara — dideteksi lewat meta
    generator halamannya)
- **Auto-split per volume** — dari satu link index/daftar isi novel,
  hasilnya otomatis kepecah jadi `Novel Vol 1.pdf`, `Novel Vol 2.pdf`, dst.
- **Mode crawl manual (`StartUrls.txt`)** — buat situs Madara yang daftar
  chapter-nya diproteksi lewat AJAX/nonce, tinggal kasih satu link
  chapter awal, script bakal ngikutin tombol **Next** otomatis.
- **Cover per volume** — cover PDF diambil dari gambar pertama halaman
  awal volume (atau halaman utama novel kalau gak ketemu).
- **Daftar isi otomatis** dengan link yang bisa diklik langsung ke tiap
  bab, judul kepanjangan otomatis dipotong biar rapi.
- **Resume-friendly** — kalau proses berhenti di tengah jalan, tinggal
  jalanin lagi; volume yang PDF-nya udah ada otomatis dilewati.
- **Logging lengkap** — tiap run kesimpen log-nya sendiri di folder
  `Logs/`, terpisah dari hasil PDF di `Result/`.

## Instalasi

1. Pastikan Python 3.9+ terpasang.
2. Install dependency:
   ```bash
   pip install -r Requirements.txt
   ```
3. Download font **DejaVu Sans** (regular & bold), taruh di folder
   `fonts/` dengan nama persis:
   ```
   fonts/DejaVuSans.ttf
   fonts/DejaVuSans-Bold.ttf
   ```
   (Font ini wajib biar karakter unicode/aneh gak jadi kotak "??" di PDF.)

## Cara Pakai

Script bakal jalanin mode-mode di bawah secara **berurutan** — Mode 1 dan
Mode 3 bisa dipakai bareng sekaligus, Mode 2 cuma jadi fallback kalau
dua-duanya kosong.

### Mode 1 — Otomatis per volume (`PageUrls.txt`)

Cara paling gampang. Taruh link halaman **utama/index** novelnya di file
`PageUrls.txt`, satu link per baris. Boleh lebih dari satu novel sekaligus.

```
https://kaoritranslation.blogspot.com/2025/12/zenmetsu-end-wo-shinimonogurui-de.html
https://agungxnovel.my.id/novel/kimi-no-gachi
https://archtranslation.com/manga/kuruna-megami-sama-to-issho-ni-sundara/
```

Setiap link otomatis kepecah jadi PDF per volume ("Volume 1", "Volume 2",
dst yang ketemu di halaman index-nya).

### Mode 2 — Manual, satu PDF gabungan (`urls.txt`)

Fallback terakhir, dipakai **hanya** kalau `PageUrls.txt` dan
`StartUrls.txt` dua-duanya kosong/gak ada. Isi `urls.txt` dengan link
chapter satu per satu, semuanya bakal digabung jadi satu PDF.

```
https://contoh.blogspot.com/2024/01/chapter-1.html
https://contoh.blogspot.com/2024/01/chapter-2.html
```

### Mode 3 — Crawl manual dari chapter awal (`StartUrls.txt`)

Khusus buat situs Madara yang daftar chapter-nya gak bisa diambil lewat
AJAX (biasanya kalau nyoba manggil AJAX-nya balikin respons `"0"` +
status 400 — tandanya diproteksi nonce). Kalau kamu udah tau link chapter
**pertama** yang mau dimulai (mis. awal Volume 2), taruh di
`StartUrls.txt`:

```
https://archtranslation.com/manga/kuruna-megami-sama-to-issho-ni-sundara/volume-2/kuruna-megami-sama-to-issho-ni-sundara-vol-2-ilustrasi/
```

Script bakal mulai dari link itu, ngikutin tombol **Next** terus-terusan
sampai habis, dikelompokin otomatis per Volume, lalu tetap di-generate
jadi PDF per volume seperti Mode 1.

Opsional, kasih judul custom dengan pisahin pakai `|`:

```
https://.../chapter-awal/|Judul Novel Custom
```

## Struktur Folder

```
.
├── ScraperLN.py
├── PageUrls.txt        # Mode 1 (opsional)
├── urls.txt             # Mode 2 (fallback)
├── StartUrls.txt        # Mode 3 (opsional)
├── Requirements.txt
├── fonts/
│   ├── DejaVuSans.ttf
│   └── DejaVuSans-Bold.ttf
├── Result/              # Output PDF ke sini
└── Logs/                # Log tiap run ke sini
```

## Konfigurasi

Beberapa hal bisa diatur langsung di bagian atas `ScraperLN.py`:

| Variabel             | Default            | Keterangan                                                                 |
| --------------------- | ------------------ | --------------------------------------------------------------------------- |
| `PAGE_URLS_FILE`      | `"PageUrls.txt"`   | Path file link index novel (Mode 1)                                        |
| `URLS_FILE`           | `"urls.txt"`       | Path file link chapter manual (Mode 2)                                     |
| `STARTURL_FILE`       | `"StartUrls.txt"`  | Path file link chapter awal buat crawl manual (Mode 3)                     |
| `SKIP_EXISTING_PDF`   | `True`             | `True` = lewati volume yang PDF-nya udah ada. `False` = selalu timpa ulang. |
| `MADARA_MAX_CHAPTERS` | `3000`             | Batas aman jumlah chapter saat auto-crawl "Next", biar gak infinite loop.   |

## Catatan

- Baris kosong atau yang diawali `#` di file `.txt` mana pun bakal
  diabaikan (bisa dipakai buat catatan).
- Kalau ada gambar/chapter yang gagal diambil, script tetap lanjut ke
  bab berikutnya — cek ringkasan & log di akhir run buat detail error.
- Script ini dibuat buat kebutuhan pribadi mengarsipkan hasil terjemahan
  fan (fan translation) yang udah publik dibaca gratis. Hormati kerja
  keras penerjemahnya — jangan disebarluaskan komersial.