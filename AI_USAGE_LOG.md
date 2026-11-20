# AI Usage Log — Modul PMB Minggu 3

Log penggunaan AI, prompt terbaik, dan verifikasi independen untuk pengerjaan tugas Sistem PMB Minggu 3 (dari UML ke kode). Seluruh contoh data pada prompt dan pengujian menggunakan **data dummy** (`@example.com`) sesuai AI-First Charter.

## 1. Ringkasan Aktivitas & Verifikasi

| Tanggal | Bagian | Ringkasan bantuan / instruksi | Pemeriksaan & validasi mandiri |
|---|---|---|---|
| 11 November 2026 | Struktur project | Menyusun struktur folder FastAPI (SRP): `models`, `schemas`, `services`, `routers`, `database`, dan `tests`. | Memeriksa pemisahan tanggung jawab tiap modul dan dependency injection `get_db`. |
| 11 November 2026 | Database & ORM | Menyusun model SQLAlchemy 2.0 (`DeclarativeBase`, `Mapped`, `mapped_column`) untuk `calon_mahasiswa`, `program_studi`, dan `jalur_masuk`. | Memeriksa DDL SQLite (`PRAGMA foreign_keys=ON`, `UNIQUE`, `CHECK` status enum, `no_hp` bertipe `VARCHAR(15)` bukan `INTEGER`, `nim` nullable+unique). |
| 11 November 2026 | Migrasi Alembic | Membuat konfigurasi `alembic/env.py` dan revision `0001_create_pmb` dengan `upgrade()` dan `downgrade()`. | Menguji `alembic check` (`No new upgrade operations detected`), serta menguji siklus `alembic downgrade -1` → tabel terhapus bersih → `alembic upgrade head` → 3 tabel terbentuk kembali pada database salinan. |
| 11 November 2026 | API Pendaftaran | Membuat `POST /api/pmb/register`, validasi Pydantic, pengecekan email duplikat & referensi, serta exception handler `400`. | Menguji melalui Swagger `/docs` dan `TestClient`: `201` (sukses, status `pending`), `409` (email duplikat), `400` (HP/email/referensi tidak valid). |
| 20 November 2026 | Review & Perbaikan Minggu 3 | Mengaudit kesesuaian kode dengan materi Minggu 3: menghapus `alamat`/`tanggal_lahir` dari `RegisterResponse`, menormalisasi email lowercase, memperbaiki validasi spasi, menghilangkan duplikasi `CHECK` constraint, menambah ADR `400` vs `422`, dan menambah test constraint DB. | Menjalankan `python -m pytest -v` (10 passed), `alembic check`, dan uji `downgrade -1` / `upgrade head` pada salinan DB. |

## 2. Prompt Terbaik yang Digunakan

### Prompt 1 — Skema Database SQLAlchemy 2.0 & Migrasi Alembic (Revisi dari Prompt Awal)
> Buatkan model SQLAlchemy 2.0 (`DeclarativeBase`, `Mapped`, `mapped_column`) dan skrip migrasi Alembic (`upgrade` dan `downgrade`) untuk modul PMB dengan 3 tabel terpisah:
> 1. `program_studi` (`id` PK, `kode` String(3) unique dengan `CHECK length(kode) = 3`, `nama`, `fakultas`).
> 2. `jalur_masuk` (`id` PK, `nama` unique untuk SNBP/SNBT/Mandiri).
> 3. `calon_mahasiswa` (`id` PK, `nama_lengkap`, `email` unique + index, `no_hp` String(15) bukan integer, `tanggal_lahir` Date, `alamat`, `program_studi_id` FK, `jalur_masuk_id` FK, `status` Enum `pending`/`approved`/`rejected` dengan `create_constraint=True` dan default `pending`, `nim` String(30) nullable & unique untuk persiapan Minggu 4, `created_at` server default timestamp, `approved_at` nullable).
>
> Sertakan aktivasi `PRAGMA foreign_keys=ON` untuk SQLite, pastikan urutan `downgrade()` menghapus `calon_mahasiswa` lebih dulu sebelum tabel referensi, dan jelaskan constraint mana yang ditegakkan di database vs di aplikasi (Pydantic).

**Mengapa efektif**: Memperbaiki ketidakkonsistenan prompt awal (memisahkan `jalur_masuk` menjadi tabel FK), mengunci gaya SQLAlchemy 2.0 agar AI tidak memakai `Column()` gaya lama, mencegah bug umum (`no_hp` jadi integer, lupa `create_constraint=True` dan `PRAGMA foreign_keys=ON`), serta langsung menyiapkan kolom `nim` dan `downgrade()`.

### Prompt 2 — Endpoint `POST /api/pmb/register` dengan Konteks Arsitektur Berlapis
> Berdasarkan model SQLAlchemy di `app/models.py` (`CalonMahasiswa`, `ProgramStudi`, `JalurMasuk`, `StatusPMB`) dan `get_db()` di `app/database.py`, buatkan implementasi `POST /api/pmb/register` dengan pemisahan layer:
> - `app/schemas.py`: `RegisterRequest` (strip whitespace, validasi `EmailStr`, regex HP Indonesia `^(?:\+62|62|0)8[1-9][0-9]{6,10}$`, ID > 0) dan `RegisterResponse` tanpa membocorkan `alamat` dan `tanggal_lahir`.
> - `app/services/pmb_service.py`: normalisasi email lowercase, cek email duplikat (beserta fallback `IntegrityError` untuk race condition), cek keberadaan `program_studi` dan `jalur_masuk`, lalu simpan dengan status default `pending`.
> - `app/routers/pmb.py` & `app/main.py`: router tipis dengan `Depends(get_db)`, HTTP `201` untuk sukses, `409` untuk email duplikat, `400` untuk referensi tidak valid dan validation error (ganti default `422` FastAPI).
> - Gunakan hanya data dummy (`Budi Santoso`, `budi@example.com`) untuk contoh payload dan unit test pytest.

**Mengapa efektif**: Memberikan konteks file dan struktur folder secara eksplisit sehingga AI menghasilkan kode yang konsisten, bebas kebocoran data sensitif pada respons, dan aman terhadap race condition email ganda.

## 3. Catatan Tanggung Jawab

AI digunakan sebagai rekan kerja untuk menyusun draf kode, dokumentasi, dan kasus uji. Setiap output dibaca, diperbaiki (termasuk menghapus kebocoran field respons, duplikasi constraint, dan celah validasi whitespace), lalu diverifikasi sendiri melalui `pytest`, `alembic`, SQLite, dan Swagger UI.
