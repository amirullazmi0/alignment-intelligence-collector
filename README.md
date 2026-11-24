# Alignment Intelligence Collector

Backend pengumpul regulasi JDIHN: menyusuri pencarian publik JDIHN, mengunduh PDF
lampirannya, lalu mengunggahnya ke Knowledge Library Alignment Intelligence. Berjalan
tanpa browser automation, dengan checkpoint di PostgreSQL.

Aplikasi ini dipakai lewat dua jalur:

- **CLI**, untuk sekali jalan dari terminal.
- **HTTP service**, yang menyediakan API jadwal cron per kementerian dan stream log
  langsung. Antarmukanya ada di repo terpisah `alignment-intelligence-cron`.

## Setup

```powershell
yarn setup
yarn install
Copy-Item .env.example .env
```

Isi `.env`, lalu siapkan databasenya:

```powershell
yarn db:deploy
```

Prisma di sini **hanya pemilik schema dan migration**. Saat runtime, tabelnya dibaca dan
ditulis langsung lewat `asyncpg`; tidak ada Prisma Client yang dipakai. Node hanya
dibutuhkan ketika menjalankan migrate.

Kalau sebelumnya sudah pernah memakai versi SQLite, pindahkan checkpoint lamanya sekali:

```powershell
yarn import-sqlite
```

Tanpa langkah itu, dokumen yang sudah pernah dikumpulkan akan dianggap baru dan diunggah
ulang.

## Menjalankan

### Sebagai service

```powershell
yarn serve
```

Service mendengarkan di `COLLECTOR_SERVICE_PORT` (default 5003) dan sekaligus menjalankan
scheduler cron-nya. Endpoint utamanya:

| Endpoint | Fungsi |
|---|---|
| `GET /health` | status service, panjang antrean, run yang sedang berjalan |
| `GET /v1/ministries` | 49 kementerian beserta penulisan `instansi` milik JDIHN |
| `GET/POST /v1/schedules` | daftar dan buat jadwal cron |
| `PATCH/DELETE /v1/schedules/{id}` | ubah dan hapus jadwal |
| `POST /v1/schedules/{id}/run` | jalankan satu jadwal sekarang |
| `GET /v1/runs` | riwayat run |
| `GET /v1/runs/{id}` | detail run beserta lognya |
| `GET /v1/runs/{id}/events` | stream log langsung (SSE) |

**Satu run pada satu waktu.** Cron tidak menjalankan run secara langsung; ia membuat baris
run lalu menitipkannya ke antrean yang dikerjakan satu per satu. Ini bukan soal kerapian:
JDIHN memutus koneksi kalau diberondong, dan handler log dipasang pada logger paket yang
dipakai bersama sehingga dua run bersamaan akan saling mencampur keluarannya.

### Sebagai CLI

```powershell
yarn backfill:dry                                             # pratinjau, tanpa unggah
.\scripts\run.ps1 -Command backfill -Limit 1                  # satu dokumen saja
yarn backfill
yarn incremental
yarn status
yarn reset-failed
yarn validate
```

Opsi `--ministry` dan `--keywords` tersedia pada `backfill` dan `incremental`. Tanpa
`--ministry`, keyword diambil dari `COLLECTOR_TOPIC`/`COLLECTOR_KEYWORDS` di `.env`.

## Cara kerja pemilihan dokumen

Nama kementerian otomatis menjadi keyword pencarian. Dokumen yang `instansi`-nya cocok
diproses lebih dulu; dokumen tingkat nasional lain yang cocok keyword tetap ikut sampai
`MAX_UPLOADS_PER_RUN` terpenuhi. Peraturan daerah kabupaten/kota dan peraturan
universitas/rektor dibuang oleh filter hierarki.

Kandidat diurutkan menurut hierarki UUD 1945, TAP MPR, UU/Perpu, PP,
Perpres/Keppres/Permen, lalu Perda Provinsi.

### Batasan yang perlu diketahui

- JDIHN membatasi hasil pencarian sekitar **100 dokumen per keyword**, dan halaman
  pencarian yang server-rendered mengabaikan parameter paginasi maupun filter instansi.
  Jadi satu run mengumpulkan puluhan dokumen, bukan seluruh arsip sebuah kementerian.
  Menambah keyword adalah satu-satunya cara memperluas cakupan.
- Angka `skipped_existing` yang besar pada run kedua adalah perilaku normal, bukan
  kegagalan: dokumen yang pernah dikumpulkan tidak diunggah ulang.
- Sebagian besar kegagalan berasal dari JDIH sumber yang mati atau menolak koneksi,
  bukan dari collector. Kegagalan sementara ditandai `FAILED_RETRYABLE` dan otomatis
  dicoba lagi; sebab yang tidak akan membaik sendiri ditandai `FAILED_PERMANENT` dan
  hanya dicoba ulang lewat `yarn reset-failed`.

## Sumber kedua: pasal.id

Setiap jadwal punya `source`: `JDIHN` (bawaan) atau `PASAL_ID`. Untuk CLI:
`--source pasal` (atau `.\scripts\run.ps1 -Source pasal`).

Sumber `PASAL_ID` bekerja begini:

1. Kandidat dicari lewat API publik `https://pasal.id/api/v1/search`, satu pencarian per
   keyword per jenis (UU, Perppu, PP, Perpres, Permen, Perda), 20 hasil masing-masing.
   Untuk jadwal kementerian ditambah pencarian Permen "Peraturan Menteri <nama>".
   Pencarian pasal.id mencocokkan isi pasal, jadi kandidat dianggap cocok dengan keyword
   yang menemukannya (judulnya sendiri sering tidak memuat keyword itu).
2. Untuk jadwal kementerian, pemrakarsa Permen dibaca dari `issuing_body` di
   `/api/v1/laws/{frbr_uri}`, satu permintaan per slug (`permenkes`, `permenkkp`, ...),
   karena hasil search tidak memuatnya dan judul Permen sering tanpa nama menterinya.
3. Detail `/api/v1/laws/{frbr_uri}` memberi `source_url`. Dari sampel, situsnya
   peraturan.go.id, peraturan.bpk.go.id, atau JDIH kementerian (mis. jdih.kemkes.go.id).
   Halaman itu dibaca untuk mencari tautan PDF (unduhan langsung lebih dulu, pratinjau
   `/Read/` dan penjelasan `...pjl.pdf` paling belakang), lalu PDF-nya diunggah dengan
   `sourceName` "<host> (via pasal.id)".

`source_id` dokumen pasal.id diberi awalan `pasal:` (mis. `pasal:akn/id/act/uu/2003/13`)
sehingga tidak bertabrakan dengan ID JDIHN. Regulasi yang sama dari dua sumber ditangkap
Alignment sebagai duplikat lewat hash PDF.

Batasan:

- Wajib `PASAL_API_TOKEN` (gratis, dibuat di <https://pasal.id/akun>). Token ditolak
  menghentikan run.
- API pasal.id hanya memberi metadata; teks pasal tidak tersedia lewat API. Ketentuan
  pasal.id melarang ekstraksi konten otomatis massal tanpa lisensi, jadi collector **tidak
  pernah** membuka halaman atau PDF pasal.id, hanya API-nya.
- Rate limit API: search dan detail masing-masing 60/menit dan 360/jam. Jeda bawaan
  `PASAL_REQUEST_DELAY_SECONDS=1.5`. Satu run kementerian memakai sekitar 7 pencarian per
  keyword plus satu detail per kandidat yang diperiksa.
- Hanya `source_url` HTTPS di domain `.go.id` yang dibuka. Di luar itu, atau halamannya
  tanpa PDF di host yang sama, dokumen dicatat `SKIPPED_NO_PDF`.
- Filter `type` yang tidak dikenal pasal.id diabaikan diam-diam (mis. `PERDA_PROV` memberi
  hasil campuran), jadi daftar jenis di `SEARCH_TYPES` harus memakai kode yang benar.

## Daftar kementerian

Ada di `src/alignment_collector/ministries.py`, berisi 49 kementerian beserta alias
penulisannya. Alias bukan hiasan: JDIHN punya salah ketik "Kementerian" ganda, mengeja
"Pelindungan" untuk Pekerja Migran, dan masih menyimpan nomenklatur sebelum perombakan
kabinet 2024.

Perbarui daftar itu setelah kabinet berubah. Sinyal bahwa ia usang datang sendiri:
collector menulis WARNING ketika sebuah kementerian tidak mendapat satu pun kandidat, dan
`ministry_matched` pada ringkasan run akan nol.

## Safety

- Tidak mengakses endpoint `/search-api/` yang dilarang `robots.txt` JDIHN.
- Pada Windows, request halaman JDIHN memakai `curl.exe` native karena WAF JDIHN memutus
  TLS fingerprint OpenSSL Python; verifikasi sertifikat tetap aktif dan hanya revocation
  lookup Schannel yang dilewati.
- Hanya mengunduh URL HTTPS publik; private, loopback, link-local, dan reserved IP ditolak,
  dan pemeriksaan itu diulang pada setiap redirect.
- PDF dibatasi 50 MB serta divalidasi lewat MIME type dan signature `%PDF-`.
- Tidak menulis ke PostgreSQL Alignment Intelligence maupun ImageKit secara langsung, dan
  tidak menghapus dokumen. Unggahan selalu melewati `POST /api/v1/documents` agar ingestion
  normal tetap berjalan.
