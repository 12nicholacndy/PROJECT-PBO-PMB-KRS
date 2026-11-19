# Sistem Informasi Akademik Mini — Modul PMB (Minggu 3–4) & Modul KRS (Minggu 5–6)

Project ini mengimplementasikan modul Penerimaan Mahasiswa Baru (PMB) secara utuh untuk Minggu 3 & 4, serta **Modul Kartu Rencana Studi (KRS) lengkap (Minggu 5 & 6)** ditambah **Challenge Opsional (+10%): KRS Recommendation CSP Backtracking**:

### Ruang Lingkup Modul PMB (Minggu 3–4)
- Tabel `calon_mahasiswa`, `program_studi`, `jalur_masuk`, dan `nim_sequence` (SQLAlchemy 2.0 `Mapped` + `mapped_column`).
- Primary key, foreign key, unique constraint, check constraint, enum status (`pending`, `approved`, `rejected`), dan nilai default.
- Migrasi database menggunakan Alembic (`0001_create_pmb` → `bd324e9e0e78` → `89b36c3f41cc` → `c6d2d3fb9b6d`).
- Endpoint lengkap:
  - `POST /api/pmb/register` — mendaftarkan pendaftar baru dengan status `pending` (201, 400, 409).
  - `PUT /api/pmb/approve/{pendaftar_id}` — menyetujui pendaftar dan menghasilkan NIM secara idempoten (200, 400, 404, 409).
  - `PUT /api/pmb/reject/{pendaftar_id}` — menolak pendaftar berstatus `pending` (200, 404, 409).
  - `GET /api/pmb/status/{pendaftar_id}` — mengecek status dan NIM pendaftar tanpa mengekspos data pribadi sensitif (200, 404).
  - `GET /api/pmb/stats` — statistik jumlah pendaftar per status, program studi, dan jalur masuk menggunakan SQL `GROUP BY` + `OUTER JOIN` (200).

### Ruang Lingkup Modul KRS (Minggu 5 & 6)
- **Skema Database & Migrasi Alembic (`89b36c3f41cc`, `c6d2d3fb9b6d`):**
  - `matakuliah`, `prerequisite` (self-referencing M:N), `krs` (termasuk `version`, `submitted_at`, `approved_at`, `updated_at`, `catatan_revisi`), `krs_detail`, `krs_log` (audit trail), dan `notifikasi`.
- **Chain of Responsibility (`app/services/krs_validators.py`):**
  - `DuplicateValidator` → `SKSValidator` (`MAX_SKS = 24`) → `PrerequisiteValidator` (query `NOT EXISTS` + bobot nilai ≥ C) → `ConflictValidator` (irisan interval jadwal).
- **State Machine & Otorisasi 2 Lapis (`app/models.py`, `app/services/krs_manager.py`, `app/services/krs_errors.py`):**
  - Tabel transisi `KRS_TRANSITIONS` + enkapsulasi `krs.cek_transisi()` & `krs.transition()` (`DRAFT → SUBMITTED`, `SUBMITTED → APPROVED/REVISION`, `REVISION → SUBMITTED`; tidak bisa skip `DRAFT → APPROVED`).
  - Otorisasi 2 lapis: peran (`mahasiswa` vs `dosen_pa`) dan kepemilikan (`krs.dosen_pa_id == dosen_pa_id` → **403 Forbidden** jika bukan PA-nya).
  - Transaksi atomik pada `submit_krs`, `approve_krs`, dan `revise_krs` (status + timestamp + `krs_log` + `notifikasi` dalam 1 transaksi; rollback penuh jika gagal) + *pessimistic locking* (`with_for_update()`) & *optimistic locking* (`version` → **409 Conflict**).
  - Polymorphic exception handler tunggal `@app.exception_handler(KRSError)` di `app/main.py`.
- **Challenge Opsional (+10%) — KRS Recommendation (`app/services/krs_recommender.py`):**
  - CSP Backtracking memprioritaskan MK wajib > MK pilihan, tanpa bentrok, SKS ≤ 24, dengan guardrail validasi ulang melalui rantai validator resmi.
- **Endpoint KRS (`app/routers/krs.py`):**
  - `GET /api/krs/matakuliah`
  - `POST /api/krs/courses` & `DELETE /api/krs/{nim}/{semester}/courses/{kode_mk}`
  - `GET /api/krs/{nim}/{semester}` & `GET /api/krs/{nim}/{semester}/validate`
  - `POST /api/krs/{nim}/{semester}/submit`
  - `POST /api/krs/{nim}/{semester}/approve`
  - `POST /api/krs/{nim}/{semester}/revise`
  - `GET /api/krs/{nim}/{semester}/logs` & `GET /api/krs/{nim}/notifikasi`
  - `POST /api/krs/{nim}/{semester}/recommend` *(Challenge +10%)*
- **ADR & Bukti Indeks:** `docs/ADR_001_KRS_VALIDATION_DESIGN.md` dan `docs/ADR_002_KRS_STATE_MACHINE_AND_CONCURRENCY.md`.
- **Testing & Property-Based Testing:** `pytest` + `hypothesis` (1000 kombinasi acak SKS + 300 kombinasi jadwal acak dengan oracle independen + 32 kombinasi state machine).

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

Hasil verifikasi terakhir: **93 passed**, total coverage **97.14%** (melebihi syarat minimum 80%).
