# Sistem PMB — Implementasi Modul PMB (Minggu 3 & 4)

Project ini mengimplementasikan modul Penerimaan Mahasiswa Baru (PMB) secara utuh untuk Minggu 3 dan Minggu 4:
- Tabel `calon_mahasiswa`, `program_studi`, `jalur_masuk`, dan `nim_sequence` (SQLAlchemy 2.0 `Mapped` + `mapped_column`).
- Primary key, foreign key, unique constraint, check constraint, enum status (`pending`, `approved`, `rejected`), dan nilai default.
- Migrasi database menggunakan Alembic (`0001_create_pmb` → `bd324e9e0e78`).
- Endpoint lengkap:
  - `POST /api/pmb/register` — mendaftarkan pendaftar baru dengan status `pending` (201, 400, 409).
  - `PUT /api/pmb/approve/{pendaftar_id}` — menyetujui pendaftar dan menghasilkan NIM secara idempoten (200, 400, 404, 409).
  - `PUT /api/pmb/reject/{pendaftar_id}` — menolak pendaftar berstatus `pending` (200, 404, 409).
  - `GET /api/pmb/status/{pendaftar_id}` — mengecek status dan NIM pendaftar tanpa mengekspos data pribadi sensitif (200, 404).
  - `GET /api/pmb/stats` — statistik jumlah pendaftar per status, program studi, dan jalur masuk menggunakan SQL `GROUP BY` + `OUTER JOIN` (200).
- Dokumentasi API otomatis melalui Swagger UI di `/docs` dan ReDoc di `/redoc`.
- Pengujian otomatis (`pytest` + `pytest-cov`) dengan coverage > 80%.

---

## Keputusan Desain Minggu 4

1. **Format NIM (11 digit angka tanpa strip):** `YYYY` + `kode_prodi (3 digit)` + `nomor_urut (4 digit zero-padded)`, contoh `20260010001`.
   - Alasan: mudah disimpan, diurutkan, dan divalidasi dengan regex `^\d{4}\d{3}\d{4}$`.
   - Nomor urut dihitung ulang per kombinasi `(tahun, kode_prodi)` dan dibatasi maksimal `9999`.
2. **Idempotency pada `PUT /api/pmb/approve/{id}`:**
   - Memanggil approve pada pendaftar yang sudah berstatus `approved` mengembalikan **HTTP 200** dengan NIM dan `approved_at` lama, tanpa memanggil generator NIM dan tanpa menaikkan counter `nim_sequence`.
   - Memanggil approve pada pendaftar berstatus `rejected` ditolak dengan **HTTP 409 Conflict** (status akhir tidak dapat dibalik).
   - Memanggil reject pada pendaftar berstatus `approved` ditolak dengan **HTTP 409 Conflict**.
3. **Penanganan Race Condition & Transaksi:**
   - Pengambilan baris `CalonMahasiswa` dan `NIMSequence` memakai `with_for_update()` (row-level lock di MySQL/PostgreSQL, bukan `threading.Lock` Python agar aman multi-worker).
   - `generate_nim()` hanya menjalankan `db.flush()` dan **tidak** melakukan `commit()` sendiri; seluruh perubahan dikomit sekali di akhir transaksi `approve_pendaftar()`.
   - `UniqueConstraint` pada `calon_mahasiswa.nim` dan primary key gabungan `(tahun, kode_prodi)` pada `nim_sequence` dipadukan dengan retry (maks. 3 kali) pada `IntegrityError` sebagai jaring pengaman.
4. **Limitasi Pengujian Lokal (SQLite vs MySQL):**
   - Pengujian otomatis (`pytest`) memakai SQLite in-memory yang mengabaikan klausa `FOR UPDATE`. Perilaku row-level lock dan uji beban konkuren nyata perlu diverifikasi pada database MySQL/PostgreSQL.

---

## 1. Persiapan (Windows / PowerShell)

```powershell
py -3.12 -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
```

*(Jika menjalankan langsung tanpa `venv`, gunakan `py -3.12 -m ...`.)*

## 2. Migrasi Database

```powershell
py -3.12 -m alembic upgrade head
py -3.12 -m alembic current --verbose
py -3.12 -m alembic check
```

## 3. Jalankan API

```powershell
py -3.12 -m uvicorn app.main:app --reload
```

Buka `http://127.0.0.1:8000/docs` untuk mencoba seluruh endpoint lewat Swagger UI.

## 4. Data Referensi (Seed Awal)

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
```

## 5. Jalankan Test Otomatis & Cek Coverage (Minimal 80%)

```powershell
py -3.12 -m pytest --cov=app --cov-report=term-missing --cov-fail-under=80 -v
```

Hasil verifikasi terakhir: **32 passed**, total coverage **95.09%** (melebihi syarat minimum 80%).
