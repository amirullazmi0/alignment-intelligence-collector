# Alignment Intelligence Collector

Python CLI untuk menemukan peraturan bertema di JDIHN dan mengunggah PDF-nya ke
Knowledge Library Alignment Intelligence. Collector berjalan melalui HTTP tanpa
browser automation dan menyimpan checkpoint lokal di SQLite.

## Setup Windows

```powershell
yarn setup
Copy-Item .env.example .env
```

Isi `ALIGNMENT_EMAIL` dan `ALIGNMENT_PASSWORD` sendiri di `.env`. File tersebut
diabaikan Git dan secret tidak pernah ditampilkan di log. Login collector memakai
mekanisme sesi yang sama dengan dashboard; untuk penggunaan rutin gunakan akun
collector khusus agar refresh token akun browser tidak saling menggantikan.

Tema dipilih melalui environment:

```env
COLLECTOR_TOPIC=pendidikan
COLLECTOR_KEYWORDS=pendidikan,sekolah,guru,dosen,universitas
```

`COLLECTOR_TOPIC` otomatis menjadi keyword. Tambahkan sinonim pada
`COLLECTOR_KEYWORDS` untuk memperluas cakupan. MVP hanya mengambil kategori
`peraturan` yang memiliki lampiran PDF.

## Menjalankan

Mulai dengan preview yang tidak login, download, atau upload:

```powershell
yarn backfill:dry
```

Lakukan smoke upload satu dokumen:

```powershell
.\scripts\run.ps1 -Command backfill -Limit 1
```

Command lain:

```powershell
yarn backfill
yarn incremental
yarn incremental:dry
yarn status
yarn check
```

`backfill` dan `incremental` mengulang targeted search publik untuk setiap keyword.
JDIHN membatasi hasil pencarian, sehingga collector ini tidak mengklaim menyalin
seluruh arsip. Source ID yang sudah selesai dilewati berdasarkan
`.data/collector.sqlite3`; source lama yang berubah ditandai `SOURCE_CHANGED` dan
tidak diunggah otomatis.

## Safety

- Tidak mengakses endpoint `/search-api/` yang dilarang oleh robots.txt JDIHN.
- Hanya mengunduh URL HTTPS publik; private, loopback, link-local, dan reserved IP ditolak.
- PDF dibatasi 50 MB serta divalidasi melalui MIME type dan signature `%PDF-`.
- Tidak menulis ke PostgreSQL atau ImageKit secara langsung dan tidak menghapus dokumen.
- Upload selalu melewati `POST /api/v1/documents` agar ingestion normal tetap berjalan.

