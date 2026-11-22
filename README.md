# Sistem Informasi Akademik Mini — Modul PMB (Minggu 3–4), KRS (Minggu 5–6), Jadwal/Ruangan (Minggu 7–8), Nilai/Transkrip (Minggu 9–10) & Keuangan SPP (Minggu 11)

Project ini mengimplementasikan modul Penerimaan Mahasiswa Baru (PMB) secara utuh untuk Minggu 3 & 4, **Modul Kartu Rencana Studi (KRS) lengkap (Minggu 5 & 6)** ditambah **Challenge Opsional (+10%): KRS Recommendation CSP Backtracking**, **Modul Jadwal & Ruangan lengkap (Minggu 7 & 8)**, **Modul Nilai & Transkrip lengkap (Minggu 9–10)**, serta **Modul Keuangan / SPP lengkap (Minggu 11)** mencakup skema tagihan & pembayaran (`billing`, `payment`, `payment_method`), state management (`unpaid`, `partial`, `paid`, `overdue`), cron job APScheduler otomatis tiap semester & reminder harian, perhitungan denda keterlambatan, pemblokiran KRS otomatis bagi penunggak SPP, simulasi Payment Webhook ber-signature HMAC-SHA256 + email kwitansi, Dashboard Keuangan real-time, dan AI Financial Report JSON:

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

### Ruang Lingkup Modul Jadwal & Ruangan (Minggu 7)
- **Model Domain Baru & Migrasi Alembic (`d7a1e2f3045b`):**
  - `ruangan` (`kode` PK, `kapasitas`), `dosen` (`nidn` unik, `nama`), `kelas` (kelas paralel MK per semester, `kuota`), `jadwal` (`CheckConstraint` `jam_mulai < jam_selesai` & `hari BETWEEN 1 AND 6`, 2 composite index `ix_jadwal_hari_ruangan` & `ix_jadwal_hari_dosen`), serta kolom `krs_detail.kelas_id` untuk penghitungan peserta terdaftar.
- **Detektor Bentrok 3 Dimensi (`app/services/conflict_detector.py`):**
  - Interval setengah terbuka `[jam_mulai, jam_selesai)` bertipe `datetime.time` (kuliah bersambung `09:40` sah, hanya melaporkan `room_conflict` & `lecturer_conflict`).
  - Dua versi: Naif (`itertools.combinations`, $O(n^2)$) sebagai *reference oracle* dan Grouping + Sort-and-Sweep ($O(n \log n + k)$ — **2,57 ms** vs **13,70 ms** untuk 600 jadwal).
- **`SchedulingService` & Error Polimorfik (`app/services/scheduling_service.py`, `app/services/schedule_errors.py`):**
  - Pessimistic locking urutan tetap `Ruangan -> Dosen` (`with_for_update()`), pencarian bentrok di SQL sebelum `db.add()` (anti autoflush self-conflict; `update` mengecualikan ID sendiri), dan capacity check `max(kelas.kuota, terdaftar) <= ruangan.kapasitas`.
  - Handler polimorfik tunggal `@app.exception_handler(ScheduleError)` dengan `exc.body()` (`404` / `409` lengkap daftar `conflicts`).
- **Endpoint Jadwal (`app/routers/jadwal.py`):**
  - `POST /api/jadwal` — buat jadwal baru (`201`, `400`, `404`, `409`)
  - `PUT /api/jadwal/{jadwal_id}` — ubah jadwal (`200`, `400`, `404`, `409`)
  - `DELETE /api/jadwal/{jadwal_id}` — hapus jadwal dalam 1 transaksi (`200`, `404`)
  - `GET /api/jadwal/conflicts` — daftar seluruh bentrok eksisting (siap untuk dashboard admin Minggu 8)
  - `GET /api/jadwal` — daftar seluruh jadwal kuliah
- **Seed Data dengan Bentrok Disengaja:** `py -3.12 -m app.seed_jadwal` (4 jadwal normal termasuk kuliah bersambung + 1 `room_conflict` + 1 `lecturer_conflict`).
- **ADR & Pengujian:** `docs/ADR_003_SCHEDULING_CSP_AND_CONCURRENCY.md`, 7 kasus parametrized bug-guard, tes API & constraint DB + `EXPLAIN QUERY PLAN`, serta 2 properti Hypothesis (*no-conflict invariant* & *differential testing*).

### Ruang Lingkup Modul Jadwal & Ruangan — Bagian 2 (Minggu 8)
- **Migrasi Alembic (`e8b4c9d1023a`):** tabel `outbox_events` (`event_id` unik, `payload` JSON, `status` `pending`/`sent`/`failed`, `attempts`, `last_error`, `processed_at`) dan kolom `jadwal.sequence` (default `0`, naik setiap `update()`).
- **Observer Pattern + 3 Kanal Simulasi (`app/services/schedule_events.py`, `app/services/schedule_observers.py`):**
  - `ScheduleEvent` (`@dataclass(frozen=True)`) bertipe `schedule_changed` / `schedule_deleted`.
  - `ScheduleSubject` (anti duplikasi `attach`, iterasi salinan, `try/except` per observer) dirangkai **sekali saat aplikasi start** (`lifespan` di `app/main.py`).
  - 3 observer konkret: `StudentObserver`, `LecturerObserver` (memberitahu dosen baru **dan** dosen lama bila diganti), dan `AdminObserver`, masing-masing mengirim lewat 3 `LogChannel` (`email`, `sms`, `push`) dengan kunci idempotensi.
- **Transactional Outbox (`app/services/outbox.py`):**
  - `SchedulingService.update()` & `delete()` menulis perubahan jadwal + invalidasi KRS + baris `outbox_events` dalam **1 transaksi atomik**, lalu menjalankan `OutboxDispatcher.run()` (`with_for_update(skip_locked=True)`, retry hingga 5x → `failed`) **setelah `commit()`**.
- **Integrasi KRS (`app/services/krs_invalidator.py`, `app/models.py`):**
  - Menambah `KRSAktor.SISTEM` + 2 transisi resmi (`SUBMITTED -> REVISION`, `APPROVED -> REVISION`) lewat satu pintu `krs.transition()`; test state machine Minggu 6 diperbarui menjadi **48 kombinasi (6 sah, 42 ilegal)**.
- **AI Rescheduler + 4 Lapis Guardrail & Conflict Resolver UI (`app/services/ai_rescheduler.py`, `app/routers/admin_ui.py`, `app/templates/conflict_resolver.html`):**
  - Daftar slot tersedia dihitung di kode memakai `detect_schedule_conflict()` Minggu 7; abstraksi `LLMClient` (Gemini Flash / OpenRouter via env var, fallback offline deterministik, serta `FakeLLMClient` untuk test).
  - 4 lapis saringan: Parsing JSON → Skema Pydantic `SaranJadwal` → Ruangan & kapasitas DB → Deteksi bentrok 3D `id=-1` (maks. 3 saran).
  - Dashboard admin di `GET /admin/conflict-resolver` dengan proteksi header `X-Role: admin_akademik` pada endpoint admin (`403` untuk non-admin) dan tombol **Terapkan** satu pintu ke `PUT /api/jadwal/{id}`.
- **Ekspor iCalendar (`app/services/ical_exporter.py`):**
  - `GET /api/jadwal/export.ics` (filter opsional `?nim=`, `?semester=`, `?semester_start=`, `?semester_end=`) berformat RFC 5545 (CRLF `\r\n`, `UID` stabil `jadwal-{id}@sevima-mini`, `RRULE` dengan `UNTIL` UTC `Z`, dan `SEQUENCE` yang naik saat jadwal diubah).
- **Dokumentasi Wajib:** UML Sequence Diagram Mermaid di `docs/SEQUENCE_OBSERVER_PATTERN.md` dan `docs/ADR_004_OBSERVER_OUTBOX_AI_RESCHEDULING.md`.

### Ruang Lingkup Modul Nilai & Transkrip (Minggu 9–10)
- **Skema Database & Migrasi Alembic (`f9c5d2e1847b`):**
  - Tabel `grades` (`nim`, `kode_mk`, `semester`, `nilai_huruf`, `nilai_angka`, `presensi_persen`, `created_by`, `updated_by`, `change_reason`, `UniqueConstraint(nim, kode_mk, semester)`).
  - Tabel `grade_history` (`id`, `grade_id`, `old_value`, `new_value`, `changed_by`, `changed_at`, `reason`) beserta **SQLite Database Trigger** `trg_grades_after_update` (`AFTER UPDATE OF nilai_huruf ON grades`) yang otomatis merekam setiap perubahan nilai.
  - Tabel `student_academic_summary` (`nim`, `total_sks_lulus`, `ipk`, `predikat`, `updated_at`) yang diperbarui otomatis dalam satu transaksi setiap kali nilai diinput atau diubah.
- **Grade Management & Business Rules (`app/services/grade_service.py`, `app/services/grade_errors.py`):**
  - Input nilai (`A/A-/B+/B/B-/C+/C/D/E` → angka `4.0` s.d. `0.0`), tolak input jika presensi `< 75%` (`400`), proteksi peran `X-Role` (`403` untuk mahasiswa), sinkronisasi otomatis ke `krs_detail.nilai`, dan polymorphic exception handler `@app.exception_handler(GradeError)`.
- **GPA Calculator (`app/services/gpa_calculator.py`):**
  - `calculate_ips(db, nim, semester)` → IP semester (`0.0` jika kosong).
  - `calculate_ipk(db, nim)` → IP kumulatif (hanya MK lulus `>= D`, MK diulang mengambil nilai tertinggi, `0.0` untuk semester awal tanpa nilai).
  - `get_transcript(db, nim)` → catatan akademik lengkap per semester + summary + predikat (`Cum Laude >= 3.50`, `Sangat Memuaskan 3.00–3.49`, `Memuaskan 2.50–2.99`, `Cukup 2.00–2.49`, `Kurang < 2.00`).
- **PDF Generator & Template HTML Resmi (`app/services/transcript_pdf.py`, `app/templates/transcript.html`):**
  - Template HTML/CSS Jinja2 bergaya ijazah resmi + generator PDF **ReportLab** A4 (logo universitas, judul `TRANSKRIP AKADEMIK`, biodata NIM/Nama/Prodi/Angkatan, tabel per semester `[Kode MK | Nama MK | SKS | Nilai | Mutu]`, summary Total SKS/IPK/Predikat, dan blok `TTD Dekan`).
- **Portal Web Form Dosen & Endpoint Nilai (`app/routers/grades.py`, `app/templates/grade_management.html`):**
  - `GET /dosen/grades` — Form web input & edit nilai dosen + live auto-IPK + tabel `grade_history` + tombol preview HTML & download PDF.
  - `POST /api/grades`, `PUT /api/grades/{grade_id}`, `GET /api/grades`, `GET /api/grades/students`
  - `GET /api/grades/{nim}/ips/{semester}`, `GET /api/grades/{nim}/ipk`, `GET /api/grades/{nim}/history`
  - `GET /api/grades/{nim}/transcript`, `GET /api/grades/{nim}/transcript/html`, `GET /api/grades/{nim}/transcript/pdf`
- **Seed Demo & Dokumentasi:** `py -3.12 -m app.seed_grades`, `docs/ADR_005_GRADE_MANAGEMENT_AUDIT_AND_TRANSCRIPT_PDF.md`, serta `tests/test_grades_minggu9.py` (12 kasus IPK + Hypothesis PBT + tes trigger DB + tes PDF).

### Ruang Lingkup Modul Keuangan / SPP (Minggu 11)
- **Payment Schema & Migrasi Alembic (`a1b2c3d4e5f6`):**
  - Tabel `payment_method`, `billing` (`UniqueConstraint(nim, semester)`, `amount`, `late_fee`, `paid_amount`, `due_date`, status `unpaid`/`partial`/`paid`/`overdue`), dan `payment` (`transaction_id` unik, `source` `manual`/`webhook`, `receipt_sent`, `receipt_email`, `raw_payload`).
- **Billing Service, Denda & APScheduler (`app/services/billing_service.py`, `app/services/billing_scheduler.py`):**
  - `generate_billing()` idempoten untuk semua mahasiswa aktif: tarif berdasar prodi (**Teknik Rp 5.000.000**, **Ekonomi Rp 4.000.000**, lainnya Rp 4.500.000), `due_date = semester_start + 14 hari`, status awal `unpaid`.
  - Cron job **APScheduler** (`semester_billing_generator` tiap 1 Feb & 1 Agu; `daily_overdue_reminder` harian 07:00 WIB) terpasang di `lifespan` FastAPI.
  - Perhitungan denda keterlambatan (1% per minggu terlambat, maks. 10% pokok SPP) + transisi ke `overdue` + pengiriman reminder otomatis (simulasi email + tabel `notifikasi`).
  - `process_payment()` dalam transaksi atomik (`with_for_update()`): akumulasi `paid_amount < total` → `partial`, `paid_amount >= total` → `paid`.
- **Integrasi Block KRS (`app/services/krs_validators.py`, `app/services/krs_manager.py`):**
  - `BillingArrearsValidator` & guard `_ensure_no_spp_arrears()` menolak pengisian (`POST /api/krs/courses`) dan pengajuan (`POST /api/krs/{nim}/{semester}/submit`) dengan **HTTP 403 Forbidden** bila mahasiswa memiliki tunggakan (`unpaid`/`partial`/`overdue`); langsung terbuka otomatis setelah lunas (`paid`).
- **Payment Webhook Simulation (`POST /api/finance/webhook/payment`):**
  - Verifikasi signature HMAC-SHA256 (`X-Webhook-Signature`, **401** jika invalid), pemrosesan atomik idempoten per `transaction_id`, pengiriman kwitansi email simulasi, return **200 OK**, serta helper `POST /api/finance/webhook/sign` untuk menghasilkan perintah `curl`.
- **Dashboard Keuangan Real-Time & AI Financial Report (`app/services/finance_dashboard.py`, `app/services/ai_financial_report.py`, `app/templates/finance_dashboard.html`):**
  - `GET /keuangan/dashboard` & `GET /api/finance/dashboard`: ringkasan per mahasiswa (total tagihan, sudah bayar, sisa, status blokir KRS), collection rate per prodi, dan grafik pembayaran per bulan.
  - `GET /api/finance/ai-insights` & `POST /api/finance/ai-insights/csv`: output JSON berisi total revenue bulan ini, collection rate, Top 10 penunggak terbesar, dan rekomendasi reminder tunggakan `> 2 bulan`.
- **Seed Demo & Dokumentasi:** `py -3.12 -m app.seed_finance`, `docs/ADR_006_FINANCE_SPP_WEBHOOK_SCHEDULER_AND_AI_REPORT.md`, serta `tests/test_finance_minggu11.py`.

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

Buka `http://127.0.0.1:8000/docs` untuk mencoba seluruh endpoint lewat Swagger UI, `http://127.0.0.1:8000/admin/conflict-resolver` untuk Conflict Resolver Dashboard Admin, `http://127.0.0.1:8000/dosen/grades` untuk Portal Form Input Nilai & Transkrip PDF Dosen, atau `http://127.0.0.1:8000/keuangan/dashboard` untuk Dashboard Keuangan SPP Real-Time.

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

Hasil verifikasi terakhir: **195 passed**, total coverage **91.85%** (melebihi syarat minimum 80%).
