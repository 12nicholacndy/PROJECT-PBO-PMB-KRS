# AI Usage Log

Log penggunaan AI untuk membantu pengerjaan project Sistem PMB Minggu 3 dan Minggu 4.

| Tanggal | Minggu | Bagian / Fitur | Ringkasan bantuan / instruksi | Revisi manual & pemeriksaan yang dilakukan |
|---|---|---|---|---|
| 11 November 2026 | Minggu 3 | Struktur project | Meminta bantuan menyusun struktur folder FastAPI, pemisahan router, service, model, schema, database, dan test. | Memeriksa struktur folder project di VS Code. |
| 11 November 2026 | Minggu 3 | Database dan ORM | Meminta bantuan menyusun model SQLAlchemy 2.0 untuk tabel `calon_mahasiswa`, `program_studi`, dan `jalur_masuk`, termasuk primary key, foreign key, unique, check constraint, dan nilai default status. | Memeriksa tabel dan data SQLite serta mencocokkan model dengan migrasi. |
| 11 November 2026 | Minggu 3 | Migrasi database | Meminta bantuan membuat konfigurasi dan revision Alembic untuk membuat serta menghapus tabel PMB. | Menjalankan pemeriksaan status Alembic dan `alembic check`; status revisi `0001_create_pmb` sinkron. |
| 11 November 2026 | Minggu 3 | API pendaftaran | Meminta bantuan membuat endpoint `POST /api/pmb/register`, validasi request, pemeriksaan email duplikat, pemeriksaan referensi, dan penanganan status HTTP. | Membuka Swagger `/docs` dan mencoba request register (201 sukses, 409 duplikat, 400 invalid). |
| 11 November 2026 | Minggu 3 | Pengujian otomatis | Meminta bantuan menyusun test pendaftaran awal. | Menjalankan `pytest -v`; 6 test lulus. |
| 19 November 2026 | Minggu 4 | Tabel counter `nim_sequence` & migrasi | Menambahkan model `NIMSequence` `(tahun, kode_prodi, last_number)` dan migrasi Alembic `bd324e9e0e78`. | Menjalankan `alembic upgrade head` dan `alembic check` (`No new upgrade operations detected`). |
| 19 November 2026 | Minggu 4 | Pemulihan `pmb_service.py`, Approve/Reject, Generator NIM, Status & Stats | Memulihkan `register_pendaftar` yang sempat tertimpa, lalu mengimplementasikan `format_nim`, `generate_nim` (`with_for_update()` + `flush()`), `approve_pendaftar` (idempoten + 1 transaksi + retry `IntegrityError`), `reject_pendaftar`, `get_status_pendaftar`, dan `get_pmb_stats` (SQL `GROUP BY` + `OUTER JOIN`). | Meninjau seluruh aturan transisi status (404/400/409/200), memastikan `StatusResponse` tidak mengekspos data pribadi sensitif, dan mendokumentasikan `responses` Swagger. |
| 19 November 2026 | Minggu 4 | Unit & Integration Test + Coverage | Membuat `tests/conftest.py` (SQLite in-memory `StaticPool` + dependency override) dan `tests/test_pmb_minggu4.py` mencakup unit test `format_nim`, approve berurutan, reset per prodi, idempotency (counter tidak naik), reject, status publik, dan stats. | Menjalankan `py -3.12 -m pytest --cov=app --cov-report=term-missing --cov-fail-under=80 -v`; hasil: **32 passed**, total coverage **95.09%**. |
| 19 November 2026 | Minggu 5 | Skema 4 tabel KRS & Migrasi Alembic | Menambahkan tabel `matakuliah`, `prerequisite` (self-referencing M:N), `krs` (FK ke `calon_mahasiswa.nim`), dan `krs_detail` beserta migrasi Alembic `89b36c3f41cc`. | Menjalankan `alembic upgrade head` dan `alembic check` (`No new upgrade operations detected`), serta memverifikasi arah `primaryjoin`/`secondaryjoin` dengan test khusus. |
| 19 November 2026 | Minggu 5 | Chain of Responsibility, Query `NOT EXISTS` & `KRSManager` | Mengimplementasikan `Validator` (ABC + Template Method), `DuplicateValidator`, `SKSValidator` (`MAX_SKS = 24`), `PrerequisiteValidator` (`NOT EXISTS` + bobot nilai), `ConflictValidator` (irisan interval), `KRSManager` (`with_for_update()`), router `/api/krs/...`, serta `docs/ADR_001_KRS_VALIDATION_DESIGN.md`. | Menjalankan `EXPLAIN QUERY PLAN` (seluruh tabel memakai `USING INDEX / COVERING INDEX`, tanpa full table `SCAN`) dan menjalankan 48 test otomatis (**48 passed**, coverage **96.60%**). |

---

## OPAR — Minggu 5 (Validasi Prasyarat, Irisan Jadwal & Transaksi KRS)

- **Observe:** Query prasyarat yang membandingkan nilai huruf sebagai string (`nilai >= 'C'`) keliru secara alfabet (`'A' < 'C'` dan `'D' > 'C'`); pengecekan jadwal bentrok yang hanya memeriksa `jam_mulai` sama melewatkan irisan parsial; dan pengisian KRS bersamaan tanpa lock berpotensi meloloskan total SKS > 24.
- **Plan:**
  1. Memetakan nilai huruf ke bobot numerik (`SKALA_NILAI`) dan memakai query `NOT EXISTS` dengan filter `semester < semester_aktif` dan `nilai IN ('A','A-','B+','B','B-','C+','C')`.
  2. Menerapkan rumus irisan interval `a.hari == b.hari and a.jam_mulai < b.jam_selesai and b.jam_mulai < a.jam_selesai`.
  3. Memisahkan 4 aturan ke dalam pola *Chain of Responsibility* (`Duplicate → SKS → Prerequisite → Conflict`) yang disuntikkan ke `KRSManager`, dengan kunci `with_for_update()` dan 1 transaksi per `add_course()`.
- **Act:** Membangun skema 4 tabel KRS + migrasi Alembic, `app/services/krs_validators.py`, `app/services/krs_manager.py`, `app/routers/krs.py`, `docs/ADR_001_KRS_VALIDATION_DESIGN.md`, dan 16 test baru di `tests/test_krs_minggu5.py`.
- **Reflect:** Seluruh 48 test lulus (coverage 96.60%) dan `EXPLAIN QUERY PLAN` membuktikan seluruh lookup memakai indeks otomatis dari PK/UniqueConstraint. **Limitasi:** SQLite mengabaikan `FOR UPDATE` sehingga penguncian konkuren tetap perlu diuji di MySQL/PostgreSQL; state machine (`submit_krs`, `approve_krs`) dan property-based testing (`hypothesis`) akan dilengkapi pada Minggu 6.

---

## OPAR — Minggu 4 (Generator NIM & Idempotency)

- **Observe:** Saat dua admin menekan approve untuk pendaftar berbeda di program studi yang sama secara bersamaan, pola *read-modify-write* tanpa penguncian database dapat menghasilkan NIM ganda. Selain itu, klik approve berulang pada pendaftar yang sama berisiko membakar nomor urut NIM.
- **Plan:**
  1. Memindahkan penguncian ke level database (bukan `threading.Lock` Python) menggunakan tabel `nim_sequence` dan `SELECT ... FOR UPDATE` (`with_for_update()`).
  2. Menjaga seluruh alur approve (baca pendaftar, naikkan counter via `flush()`, simpan NIM dan `approved_at`) dalam satu transaksi tunggal yang di-commit sekali di akhir, ditambah retry pada `IntegrityError`.
  3. Menangani idempotency di `approve_pendaftar`: jika status sudah `approved`, kembalikan NIM lama tanpa menyentuh counter.
- **Act:** Mengimplementasikan `format_nim`, `generate_nim`, `approve_pendaftar`, `reject_pendaftar`, `get_status_pendaftar`, dan `get_pmb_stats` di `app/services/pmb_service.py`, endpoint terkait di `app/routers/pmb.py`, serta 32 test otomatis dengan coverage 95.09%.
- **Reflect:** Seluruh 32 test lulus di SQLite in-memory (coverage 95.09%) dan membuktikan format 11 digit, urutan per prodi, serta idempotency tanpa kenaikan counter. **Limitasi:** SQLite mengabaikan klausa `FOR UPDATE`, sehingga pengujian race condition di bawah beban konkuren nyata tetap perlu diverifikasi di MySQL/PostgreSQL.

---

## Catatan Penggunaan AI

AI digunakan sebagai alat bantu untuk menyusun, meninjau, dan menguji kode serta dokumentasi. Setiap perubahan diperiksa kembali melalui terminal, Alembic, Swagger, dan `pytest --cov`. Pemilik project tetap bertanggung jawab memahami setiap baris kode untuk keperluan pemeriksaan dan viva.
