# PostIndo

PostIndo mengubah lampiran tarif kiriman domestik dan internasional Indonesia menjadi basis data terstruktur dan situs statis yang ringan. Situs menyediakan indeks tarif **dari** setiap wilayah/kantor dan **ke** setiap wilayah/kantor, serta tarif ke negara tujuan luar negeri, tanpa API, pelacak, font eksternal, atau JavaScript framework di peramban.

> **Bukan situs resmi Pos Indonesia atau pemerintah.** Data ini merupakan penyajian ulang Keputusan Menteri Komunikasi dan Informatika Nomor 222 Tahun 2022. Periksa status dan tarif yang berlaku pada sumber resmi sebelum mengandalkannya.

## Cakupan data

- Lampiran domestik berbasis teks pada halaman PDF 4–20.197.
- 603 wilayah/kantor (`KAB/KOTA/KEC`) dan 205 KPRK.
- 363.609 rute berarah (603 × 603), termasuk rute menuju wilayah yang sama.
- Lima kelompok berat surat, kartu pos, sekogram, M-Bag, paket 2–3 kg, dan tambahan setiap kg paket.
- Nilai tarif disimpan sebagai bilangan bulat rupiah. `sekogram` bernilai `0` di basis data dan ditampilkan sebagai **Bebas Biaya**.
- Lampiran internasional hasil pindai pada halaman PDF 20.198–20.208, dengan 236 baris negara tujuan.
- Delapan kelompok berat surat/barang cetakan/bungkusan kecil, kartu pos, sekogram sampai 7 kg, M-Bag sampai 30 kg, dan paket pos internasional.
- Tarif internasional selain paket disimpan sebagai integer rupiah; tarif paket disimpan sebagai integer sen dolar AS agar tidak memakai floating point. Tanda `-` pada sumber disimpan sebagai `NULL`.

Nama wilayah/kantor dan negara dipertahankan sebagaimana tertulis pada keputusan, termasuk nama historis, ejaan, dan kode negara yang berulang. Halaman rumus 20.209–20.210 tidak dimasukkan sebagai baris tarif.

## Menjalankan situs

Prasyaratnya adalah Node.js 24 dan npm.

```sh
npm ci
npm run dev
```

Perintah pemeriksaan lokal:

```sh
npm run check
npm test
npm run build
python scripts/check_dist.py --dist dist
```

Build Astro membaca `data/postindo.sqlite` secara read-only melalui `node:sqlite`. Basis data dan CSV tidak disalin ke `dist/`; seluruh halaman yang diterbitkan adalah HTML, CSS, dan JavaScript statis.

## Regenerasi basis data

Dokumen PDF sengaja tidak dilacak Git karena ukurannya sekitar 136 MB. Unduh dokumen dari [entri resmi JDIH Kominfo](https://jdih.komdigi.go.id/produk_hukum/view/id/803/t/keputusan%2Bmenteri%2Bkomunikasi%2Bdan%2Binformatika%2Bnomor%2B222%2Btahun%2B2022), lalu simpan di akar repositori dengan nama:

```text
Kepmen Kominfo No. 222 Tahun 2022 ttg Besaran Tarif LPU (Lengkap).pdf
```

SHA-256 sumber yang diterima secara baku:

```text
811d1fb3ac805f172ea7f2d922cc1915b05c63226023bb9c68fc71a66c39bf3d
```

Siapkan Python 3.10 atau lebih baru dan Tesseract OCR dengan data bahasa `eng` serta `ind`, lalu jalankan ekstraktor:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements.txt
python scripts/extract_rates.py \
  --input "Kepmen Kominfo No. 222 Tahun 2022 ttg Besaran Tarif LPU (Lengkap).pdf" \
  --output-dir data
```

Satu perintah tersebut mengekstrak matriks domestik berbasis teks dan menjalankan OCR untuk tabel internasional. Ekstraksi penuh diperkirakan memerlukan sekitar 24 menit. Ekstraktor memproses PDF lewat subprocess berurutan dalam chunk 1.000 halaman agar pemakaian memori tetap sekitar 400 MB. Hasil chunk domestik yang selesai dan satu hasil OCR internasional yang sudah lolos validasi disimpan di `data/.extract-cache/`. Proses domestik dapat dilanjutkan per chunk; interupsi di tengah OCR 11 halaman internasional mengulang tahap OCR tersebut, yang biasanya memerlukan sekitar dua menit. Ukuran chunk dapat diubah dengan `--chunk-pages`. Jika biner OCR tidak bernama `tesseract`, berikan lokasinya melalui `--tesseract-command`.

Secara baku, checksum PDF wajib cocok. `--allow-unverified-source` hanya ditujukan untuk salinan atau revisi sumber yang sengaja diterima; seluruh pemeriksaan struktur tetap dijalankan. Semua artefak disiapkan dan divalidasi dalam direktori staging. Berkas manifest, sebagai penanda set lengkap, diterbitkan paling akhir agar publikasi yang terputus dapat dideteksi.

Validasi ulang artefak yang sudah dibuat tidak memerlukan PDF:

```sh
python scripts/validate_data.py --data-dir data
python -m unittest discover -s tests/python -v
```

## Artefak dan skema

Lima berkas hasil ekstraksi di bawah ini dilacak Git agar CI dan deployment tidak perlu mengolah ulang PDF:

| Berkas | Isi |
| --- | --- |
| `data/postindo.sqlite` | Basis data utama untuk build Astro |
| `data/locations.csv` | 603 wilayah/kantor dalam urutan sumber |
| `data/rates.csv` | 363.609 rute dalam urutan baris sumber |
| `data/international_rates.csv` | 236 tarif negara tujuan internasional dalam urutan sumber |
| `data/manifest.json` | Versi skema, metadata sumber, jumlah record, ukuran, dan SHA-256 artefak |

SQLite berisi empat tabel publik:

- `metadata(key, value)` menyimpan metadata generasi dan sumber.
- `locations(office_id, source_name, kprk_id, ordinal, slug)` menyimpan ID sebagai teks, termasuk ID berhuruf seperti `B1`.
- `rates(source_row, source_page, origin_id, destination_id, letter_up_to_100g, letter_over_100g_to_250g, letter_over_250g_to_500g, letter_over_500g_to_1000g, letter_over_1000g_to_2000g, postcard, sekogram, m_bag_per_kg, parcel_over_2kg_to_3kg, parcel_each_additional_kg)` menyimpan seluruh tarif sebagai integer rupiah.
- `international_rates(source_row, source_page, source_name, country_code, slug, letter_printed_matter_small_packet_up_to_20g, letter_printed_matter_small_packet_over_20g_to_50g, letter_printed_matter_small_packet_over_50g_to_100g, letter_printed_matter_small_packet_over_100g_to_250g, letter_printed_matter_small_packet_over_250g_to_500g, letter_printed_matter_small_packet_over_500g_to_1000g, letter_printed_matter_small_packet_over_1000g_to_1500g, letter_printed_matter_small_packet_over_1500g_to_2000g, postcard, sekogram_up_to_7kg, m_bag_per_kg_up_to_30kg, parcel_up_to_3kg_usd_cents, parcel_each_additional_kg_usd_cents)` menyimpan satu baris per nomor negara tujuan. Delapan kolom `letter_printed_matter_small_packet_*` mengikuti tepat satu kelompok judul sumber: surat, barang cetakan, dan bungkusan kecil.

Pasangan `(origin_id, destination_id)` adalah primary key tarif domestik dan tersedia indeks yang diawali `destination_id` untuk build halaman tujuan. `international_rates.source_row` adalah primary key karena kode negara tidak unik pada sumber: `SZ` muncul untuk `Eswatini (Swaziland)` dan `Swaziland`. CSV memakai kolom yang sama, UTF-8, aturan quoting RFC 4180, line ending LF, kolom kosong untuk `NULL`, dan urutan sumber deterministik.

## Build dan GitHub Pages

Astro memakai dua variabel lingkungan:

| Variabel | Default deployment | Kegunaan |
| --- | --- | --- |
| `SITE_URL` | `https://<pemilik>.github.io` | Origin kanonis situs |
| `BASE_PATH` | `/<repositori>` | Prefix URL untuk project Pages |

Workflow deployment menghitung keduanya dari `GITHUB_REPOSITORY`. Untuk user/organization Pages, `BASE_PATH` otomatis menjadi `/`. Untuk domain khusus, konfigurasi lebih dahulu domain dan DNS melalui **Settings → Pages**, lalu buat repository variable `SITE_URL` dengan origin domain dan `BASE_PATH` bernilai `/`. Pengguna private organization Pages dengan origin acak juga harus mengisi kedua variabel sesuai URL Pages sebenarnya.

Aktifkan **Settings → Pages → Build and deployment → Source: GitHub Actions**. Pull request dan push menjalankan [workflow CI](.github/workflows/ci.yml). Push ke `main` baru memicu [workflow deployment](.github/workflows/deploy.yml) setelah CI untuk commit yang sama selesai dengan sukses; jalankan ulang CI berbasis push tersebut bila deployment perlu diulang.

CI memverifikasi checksum dan isi artefak, schema Astro, unit test, build penuh, tautan internal, pengujian peramban seluler dengan dan tanpa JavaScript, serta batas berikut:

- build maksimal 8 menit;
- seluruh `dist/` maksimal 300 MiB;
- setiap halaman wilayah/kantor maksimal 50 KiB gzip;
- total CSS maksimal 10 KiB gzip;
- total JavaScript klien maksimal 5 KiB gzip;
- tidak ada sumber daya pihak ketiga atau artefak basis data di hasil publikasi.

Anggaran tersebut menyediakan margin terhadap [batas GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) sebesar 1 GB untuk situs terbit dan timeout deployment 10 menit.

## Batasan penggunaan

PostIndo melaporkan pita berat yang diterbitkan dalam keputusan, bukan menghitung interpolasi berat, mengonversi tarif dolar AS, atau memberikan penawaran harga komersial Pos Indonesia. Lampiran internasional diperoleh melalui dua pembacaan tabel penuh dan pembacaan ulang sel yang tidak sepakat, memakai model bahasa Tesseract `eng` serta `ind`. Koreksi hasil tinjauan hanya berlaku untuk PDF resmi atau lapisan gambar sumber dengan checksum identik. Hasil akhirnya diperiksa terhadap struktur tabel, jangkar sumber, pola ketersediaan layanan, dan checksum seluruh CSV internasional. Untuk metodologi, sumber, serta peringatan yang tampil kepada pengunjung, buka halaman `/tentang/` pada situs hasil build.
