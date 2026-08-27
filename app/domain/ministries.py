"""Daftar kementerian Republik Indonesia beserta penulisan `instansi` milik JDIHN.

JDIHN menuliskan pemilik dokumen dengan awalan "JDIH", misalnya
`"JDIH Kementerian Pariwisata"`. Pencocokan dilakukan secara substring atas teks yang
sudah dinormalisasi, jadi awalan itu tidak perlu ikut ditulis di sini.

Alias bukan hiasan. Penyisiran langsung ke JDIHN memperlihatkan tiga sumber
ketidakcocokan yang nyata:

1. Salah ketik "Kementerian" ganda, contohnya
   `"JDIH Kementerian Kementerian Komunikasi dan Digital"`.
2. Ejaan "Pelindungan" (bukan "Perlindungan") untuk Pekerja Migran.
3. Nomenklatur sebelum perombakan kabinet 2024 yang masih melekat pada dokumen lama,
   seperti "Pekerjaan Umum dan Perumahan Rakyat".

Dari 49 entri, 34 sudah terbukti cocok dengan data JDIHN nyata dan nol instansi hasil
penyisiran yang tidak tercakup. Sisanya belum pernah terlihat karena dokumennya jarang.
Perbarui daftar ini setelah kabinet berubah; sinyal bahwa ia usang datang sendiri lewat
WARNING yang ditulis collector ketika sebuah kementerian tidak mendapat satu pun kandidat.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ministry:
    value: str
    label: str
    aliases: tuple[str, ...] = ()


MINISTRIES: tuple[Ministry, ...] = (
    Ministry(
        value="Kementerian Koordinator Bidang Politik dan Keamanan",
        label="Kemenko Politik dan Keamanan",
        aliases=("Kementerian Koordinator Bidang Politik, Hukum, dan Keamanan",),
    ),
    Ministry(
        value=(
            "Kementerian Koordinator Bidang Hukum, Hak Asasi Manusia, Imigrasi, dan Pemasyarakatan"
        ),
        label="Kemenko Hukum, HAM, Imigrasi, dan Pemasyarakatan",
        aliases=("Kementerian Koordinator Bidang Politik, Hukum, dan Keamanan",),
    ),
    Ministry(
        value="Kementerian Koordinator Bidang Perekonomian",
        label="Kemenko Perekonomian",
    ),
    Ministry(
        value="Kementerian Koordinator Bidang Pembangunan Manusia dan Kebudayaan",
        label="Kemenko Pembangunan Manusia dan Kebudayaan",
    ),
    Ministry(
        value="Kementerian Koordinator Bidang Infrastruktur dan Pembangunan Kewilayahan",
        label="Kemenko Infrastruktur dan Pembangunan Kewilayahan",
        aliases=("Kementerian Koordinator Bidang Kemaritiman dan Investasi",),
    ),
    Ministry(
        value="Kementerian Koordinator Bidang Pangan",
        label="Kemenko Pangan",
    ),
    Ministry(
        value="Kementerian Koordinator Bidang Pemberdayaan Masyarakat",
        label="Kemenko Pemberdayaan Masyarakat",
    ),
    Ministry(
        value="Kementerian Sekretariat Negara",
        label="Kementerian Sekretariat Negara",
    ),
    Ministry(
        value="Kementerian Dalam Negeri",
        label="Kementerian Dalam Negeri",
    ),
    Ministry(
        value="Kementerian Luar Negeri",
        label="Kementerian Luar Negeri",
    ),
    Ministry(
        value="Kementerian Pertahanan",
        label="Kementerian Pertahanan",
    ),
    Ministry(
        value="Kementerian Agama",
        label="Kementerian Agama",
    ),
    Ministry(
        value="Kementerian Hukum",
        label="Kementerian Hukum",
        aliases=("Kementerian Hukum dan Hak Asasi Manusia",),
    ),
    Ministry(
        value="Kementerian Hak Asasi Manusia",
        label="Kementerian Hak Asasi Manusia",
        aliases=("Kementerian Hukum dan Hak Asasi Manusia",),
    ),
    Ministry(
        value="Kementerian Imigrasi dan Pemasyarakatan",
        label="Kementerian Imigrasi dan Pemasyarakatan",
    ),
    Ministry(
        value="Kementerian Keuangan",
        label="Kementerian Keuangan",
    ),
    Ministry(
        value="Kementerian Pendidikan Dasar dan Menengah",
        label="Kementerian Pendidikan Dasar dan Menengah",
        aliases=(
            "Kementerian Pendidikan dan Kebudayaan",
            "Kementerian Pendidikan, Kebudayaan, Riset, dan Teknologi",
        ),
    ),
    Ministry(
        value="Kementerian Pendidikan Tinggi, Sains, dan Teknologi",
        label="Kementerian Pendidikan Tinggi, Sains, dan Teknologi",
        aliases=(
            "Kementerian Riset, Teknologi, dan Pendidikan Tinggi",
            "Kementerian Pendidikan, Kebudayaan, Riset, dan Teknologi",
        ),
    ),
    Ministry(
        value="Kementerian Kebudayaan",
        label="Kementerian Kebudayaan",
    ),
    Ministry(
        value="Kementerian Kesehatan",
        label="Kementerian Kesehatan",
    ),
    Ministry(
        value="Kementerian Sosial",
        label="Kementerian Sosial",
    ),
    Ministry(
        value="Kementerian Ketenagakerjaan",
        label="Kementerian Ketenagakerjaan",
        aliases=("Kementerian Tenaga Kerja",),
    ),
    Ministry(
        value="Kementerian Pelindungan Pekerja Migran Indonesia",
        label="Kementerian Pelindungan Pekerja Migran Indonesia",
        aliases=("Kementerian Perlindungan Pekerja Migran Indonesia",),
    ),
    Ministry(
        value="Kementerian Perindustrian",
        label="Kementerian Perindustrian",
    ),
    Ministry(
        value="Kementerian Perdagangan",
        label="Kementerian Perdagangan",
    ),
    Ministry(
        value="Kementerian Energi dan Sumber Daya Mineral",
        label="Kementerian Energi dan Sumber Daya Mineral",
    ),
    Ministry(
        value="Kementerian Pekerjaan Umum",
        label="Kementerian Pekerjaan Umum",
        aliases=("Kementerian Pekerjaan Umum dan Perumahan Rakyat",),
    ),
    Ministry(
        value="Kementerian Perumahan dan Kawasan Permukiman",
        label="Kementerian Perumahan dan Kawasan Permukiman",
        aliases=("Kementerian Pekerjaan Umum dan Perumahan Rakyat",),
    ),
    Ministry(
        value="Kementerian Transmigrasi",
        label="Kementerian Transmigrasi",
        aliases=("Kementerian Desa, Pembangunan Daerah Tertinggal, dan Transmigrasi",),
    ),
    Ministry(
        value="Kementerian Perhubungan",
        label="Kementerian Perhubungan",
    ),
    Ministry(
        value="Kementerian Komunikasi dan Digital",
        label="Kementerian Komunikasi dan Digital",
        aliases=("Kementerian Komunikasi dan Informatika",),
    ),
    Ministry(
        value="Kementerian Pertanian",
        label="Kementerian Pertanian",
    ),
    Ministry(
        value="Kementerian Kehutanan",
        label="Kementerian Kehutanan",
        aliases=("Kementerian Lingkungan Hidup dan Kehutanan",),
    ),
    Ministry(
        value="Kementerian Kelautan dan Perikanan",
        label="Kementerian Kelautan dan Perikanan",
    ),
    Ministry(
        value="Kementerian Lingkungan Hidup",
        label="Kementerian Lingkungan Hidup",
        aliases=("Kementerian Lingkungan Hidup dan Kehutanan",),
    ),
    Ministry(
        value="Kementerian Desa dan Pembangunan Daerah Tertinggal",
        label="Kementerian Desa dan Pembangunan Daerah Tertinggal",
        aliases=("Kementerian Desa, Pembangunan Daerah Tertinggal, dan Transmigrasi",),
    ),
    Ministry(
        value="Kementerian Agraria dan Tata Ruang",
        label="Kementerian Agraria dan Tata Ruang / BPN",
        aliases=("Kementerian Agraria dan Tata Ruang/Badan Pertanahan Nasional",),
    ),
    Ministry(
        value="Kementerian Perencanaan Pembangunan Nasional",
        label="Kementerian PPN / Bappenas",
        aliases=(
            "Kementerian Perencanaan Pembangunan Nasional/Badan Perencanaan Pembangunan Nasional",
        ),
    ),
    Ministry(
        value="Kementerian Pendayagunaan Aparatur Negara dan Reformasi Birokrasi",
        label="Kementerian PANRB",
    ),
    Ministry(
        value="Kementerian Badan Usaha Milik Negara",
        label="Kementerian BUMN",
    ),
    Ministry(
        value="Kementerian Kependudukan dan Pembangunan Keluarga",
        label="Kementerian Kependudukan dan Pembangunan Keluarga",
        aliases=("Badan Kependudukan dan Keluarga Berencana Nasional",),
    ),
    Ministry(
        value="Kementerian Koperasi",
        label="Kementerian Koperasi",
        aliases=("Kementerian Koperasi dan Usaha Kecil dan Menengah",),
    ),
    Ministry(
        value="Kementerian Usaha Mikro, Kecil, dan Menengah",
        label="Kementerian UMKM",
        aliases=("Kementerian Koperasi dan Usaha Kecil dan Menengah",),
    ),
    Ministry(
        value="Kementerian Pariwisata",
        label="Kementerian Pariwisata",
        aliases=("Kementerian Pariwisata dan Ekonomi Kreatif",),
    ),
    Ministry(
        value="Kementerian Ekonomi Kreatif",
        label="Kementerian Ekonomi Kreatif",
        aliases=("Kementerian Pariwisata dan Ekonomi Kreatif",),
    ),
    Ministry(
        value="Kementerian Pemberdayaan Perempuan dan Perlindungan Anak",
        label="Kementerian PPPA",
    ),
    Ministry(
        value="Kementerian Pemuda dan Olahraga",
        label="Kementerian Pemuda dan Olahraga",
    ),
    Ministry(
        value="Kementerian Investasi dan Hilirisasi",
        label="Kementerian Investasi dan Hilirisasi / BKPM",
        aliases=("Kementerian Investasi/Badan Koordinasi Penanaman Modal",),
    ),
    Ministry(
        value="Kementerian Haji dan Umrah",
        label="Kementerian Haji dan Umrah",
    ),
)

MINISTRIES_BY_VALUE: dict[str, Ministry] = {item.value: item for item in MINISTRIES}


def find_ministry(value: str) -> Ministry | None:
    return MINISTRIES_BY_VALUE.get(value.strip())


__all__ = ["MINISTRIES", "Ministry", "find_ministry"]
