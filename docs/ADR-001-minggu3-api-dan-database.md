# ADR-001: Keputusan Teknis Implementasi PMB Minggu 3

- **Status**: Accepted
- **Tanggal**: 20 November 2026
- **Lingkup**: Modul Penerimaan Mahasiswa Baru (PMB) — Skema Database, Migrasi Alembic, dan Endpoint `POST /api/pmb/register`

## 1. Status Code Error Validasi Input: HTTP `400` vs Default FastAPI `422`

### Konteks
Secara bawaan, FastAPI mengembalikan HTTP `422 Unprocessable Entity` ketika validasi Pydantic (`RequestValidationError`) gagal. Sementara itu, kontrak pengujian pada dokumen tugas mengharapkan HTTP `400 Bad Request` untuk input tidak valid seperti format nomor HP Indonesia yang salah atau format email tidak valid.

### Keputusan
Memasang global exception handler untuk `RequestValidationError` di `app/main.py` agar seluruh error validasi input dikembalikan secara konsisten sebagai **HTTP `400 Bad Request`** dengan payload JSON yang ramah (`detail` dan daftar `errors` per field), tanpa membocorkan stack trace atau pesan internal database.

### Konsekuensi
- **Positif**: Konsisten dengan kontrak pengujian tugas (`400` untuk input/referensi tidak valid, `409` untuk konflik email duplikat, `201` untuk pendaftaran baru berhasil).
- **Negatif**: Menyimpang dari perilaku default FastAPI (`422`), sehingga didokumentasikan secara eksplisit di ADR ini.

## 2. Database Pengembangan SQLite + `PRAGMA foreign_keys=ON` & Validasi Dua Lapis

### Konteks
Tahap pengembangan menggunakan SQLite karena ringan (satu file tanpa server). Namun, SQLite memiliki dua keterbatasan bawaan:
1. Enforcement `FOREIGN KEY` tidak aktif secara default pada setiap koneksi.
2. SQLite tidak memiliki fungsi regex bawaan untuk validasi pola nomor HP.

### Keputusan
- Mengaktifkan `PRAGMA foreign_keys=ON` melalui SQLAlchemy event listener `connect` di `app/database.py` dan saat migrasi online di `alembic/env.py`.
- Menerapkan validasi dua lapis:
  - **Lapis aplikasi (Pydantic)**: Memvalidasi format email (`EmailStr`), panjang nama/alamat non-spasi, serta pola nomor seluler Indonesia (`^(?:\+62|62|0)8[1-9][0-9]{6,10}$`).
  - **Lapis database (SQLAlchemy 2.0 + Alembic)**: Menegakkan `UNIQUE` (`email`, `nim`, `program_studi.kode`, `jalur_masuk.nama`), `NOT NULL`, `FOREIGN KEY`, `CHECK` (`length(kode) = 3` dan `Enum` status dengan `create_constraint=True`), serta `server_default` untuk `status='pending'` dan `created_at`.

## 3. Pembatasan Field pada Response Pendaftaran (`RegisterResponse`)

### Keputusan
`RegisterResponse` hanya mengembalikan field yang diperlukan (`id`, `nama_lengkap`, `email`, `no_hp`, `program_studi_id`, `jalur_masuk_id`, `status`, `nim`, `created_at`) dan **tidak mengembalikan** `alamat` maupun `tanggal_lahir` guna meminimalkan paparan data pribadi pada respons API.

## 4. Arsitektur Berlapis & SQLAlchemy 2.0 + Alembic Rollback

### Keputusan
- Memisahkan tanggung jawab ke `app/models.py` (ORM), `app/schemas.py` (validasi I/O), `app/services/pmb_service.py` (aturan bisnis), `app/routers/pmb.py` (HTTP), dan `app/database.py` (koneksi & DI `get_db`).
- Menggunakan sintaks SQLAlchemy 2.0 (`DeclarativeBase`, `Mapped`, `mapped_column`) dan migrasi Alembic dengan fungsi `upgrade()` serta `downgrade()` yang menghapus tabel anak (`calon_mahasiswa`) terlebih dahulu sebelum tabel induk (`jalur_masuk`, `program_studi`).
