# Sistem PMB — Implementasi Minggu 3

Project ini mengimplementasikan ruang lingkup Minggu 3:
- Tabel `calon_mahasiswa`, `program_studi`, dan `jalur_masuk`.
- SQLAlchemy 2.0 dengan `Mapped` dan `mapped_column`.
- Primary key, foreign key, unique constraint, check constraint, dan nilai default.
- Validasi request menggunakan Pydantic.
- Migrasi database menggunakan Alembic.
- Endpoint `POST /api/pmb/register` dengan status awal `pending`.
- Email duplikat ditolak dengan HTTP 409.
- Input tidak valid dan referensi yang tidak ditemukan ditolak dengan HTTP 400.
- Dokumentasi API tersedia melalui Swagger di `/docs`.

## 1. Persiapan (Windows / PowerShell)

Buka folder project ini di VS Code, lalu jalankan:

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
```

## 2. Migrasi database

Untuk membuat atau memperbarui skema database:

```powershell
alembic upgrade head
alembic current --verbose
```

Jangan menjalankan `alembic downgrade -1` pada database yang berisi data penting karena migrasi awal menghapus tabel PMB.

Jika dosen meminta bukti pengujian downgrade, lakukan hanya pada salinan project/database khusus pengujian yang tidak berisi data penting:
1. Salin seluruh folder project ke folder sementara.
2. Dari folder sementara, aktifkan virtual environment dan pasang requirements.
3. Jalankan `alembic downgrade -1`.
4. Pastikan tabel PMB terhapus pada database salinan.
5. Jalankan `alembic upgrade head` dan pastikan tabel terbentuk kembali.

Jangan lakukan uji downgrade pada database project utama.

## 3. Jalankan API

```powershell
python -m uvicorn app.main:app --reload
```

Buka `http://127.0.0.1:8000/docs` untuk melihat Swagger.

## 4. Data referensi

Endpoint register membutuhkan `program_studi_id` dan `jalur_masuk_id` yang sudah tersedia. Untuk menambahkan data referensi pada database pengembangan, jalankan Python:

```powershell
python
```

Kemudian:

```python
from app.database import SessionLocal
from app.models import ProgramStudi, JalurMasuk

db = SessionLocal()
db.add_all([
    ProgramStudi(kode="001", nama="Sistem Informasi", fakultas="Fakultas Ilmu Komputer"),
    ProgramStudi(kode="002", nama="Teknik Informatika", fakultas="Fakultas Ilmu Komputer"),
    JalurMasuk(nama="SNBP"),
    JalurMasuk(nama="SNBT"),
    JalurMasuk(nama="Mandiri"),
])
db.commit()
db.close()
exit()
```

Jalankan seed hanya jika data referensi belum tersedia agar tidak menimbulkan duplikasi.

## 5. Uji endpoint register

Di Swagger, pilih `POST /api/pmb/register` dan gunakan contoh:

```json
{
  "nama_lengkap": "Budi Santoso",
  "email": "budi@example.com",
  "no_hp": "081234567890",
  "tanggal_lahir": "2007-04-12",
  "alamat": "Cibinong, Bogor",
  "program_studi_id": 1,
  "jalur_masuk_id": 1
}
```

Hasil yang diharapkan:
- Pendaftaran berhasil: HTTP 201 dan status `pending`.
- Email duplikat: HTTP 409.
- Input tidak valid: HTTP 400.
- Program studi atau jalur masuk tidak ditemukan: HTTP 400.

## 6. Jalankan test otomatis

```powershell
python -m pytest -v
```

Hasil pengujian yang tercatat sebelum paket diperbaiki adalah 6 test passed. Jalankan kembali perintah di atas setelah mengekstrak paket final untuk memverifikasi lingkungan lokal.

## 7. Git dan kontribusi

Jika instruksi dosen meminta bukti commit Git, jalankan dari folder project utama:

```powershell
git init
git add .
git commit -m "Implement PMB week 3 with AI assistance"
```

Jika Git meminta identitas, atur nama dan email Git milik sendiri terlebih dahulu. Pastikan commit berhasil dengan:

```powershell
git log -1 --oneline
```

Riwayat Git tidak disertakan dalam ZIP ini; simpan bukti commit atau repository sesuai cara pengumpulan yang diminta dosen.

## 8. AI Usage Log

Ringkasan bantuan AI dan pemeriksaan yang dilakukan dicatat pada `AI_USAGE_LOG.md`. Periksa kembali dan sesuaikan catatan tersebut jika ada bantuan atau pengujian tambahan yang dilakukan setelah tanggal yang tercantum.

## Catatan ruang lingkup

- Status PMB yang disediakan adalah `pending`, `approved`, dan `rejected`. Endpoint register pada Minggu 3 membuat pendaftar dengan status `pending`.
- Kolom NIM nullable dan unik disiapkan untuk proses minggu berikutnya; generator NIM dan proses approval tidak termasuk endpoint register Minggu 3.
- Validasi pola nomor HP dilakukan di aplikasi menggunakan Pydantic.
