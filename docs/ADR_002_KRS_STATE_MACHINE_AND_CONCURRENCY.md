# ADR-002: Desain State Machine KRS, Otorisasi 2 Lapis, dan Kontrol Konkurensi

- **Status:** Diterima
- **Tanggal:** 19 November 2026
- **Modul:** Modul KRS (Minggu 6 — Bagian 2)

---

## 1. Implementasi State Machine: Tabel Transisi Sendiri vs Library Eksternal

### Konteks
Dokumen tugas menyebut *state management library* pada bagian tools, sementara spesifikasi state machine KRS memiliki **4 state** (`draft`, `submitted`, `approved`, `revision`) dan **4 transisi sah** lintas 2 aktor (`mahasiswa`, `dosen_pa`).

### Alternatif
1. **Memakai library `python-statemachine` atau `transitions`:** memiliki fitur guard, callback, dan diagram otomatis, tetapi menambah dependensi eksternal dan kompleksitas integrasi dengan SQLAlchemy ORM.
2. **Tabel transisi eksplisit (`KRS_TRANSITIONS` dictionary) + method `cek_transisi()` & `transition()` langsung di model `KRS` (dipilih):**
   ```python
   KRS_TRANSITIONS = {
       (KRSStatus.DRAFT, KRSAktor.MAHASISWA): {KRSStatus.SUBMITTED},
       (KRSStatus.SUBMITTED, KRSAktor.DOSEN_PA): {KRSStatus.APPROVED, KRSStatus.REVISION},
       (KRSStatus.REVISION, KRSAktor.MAHASISWA): {KRSStatus.SUBMITTED},
   }
   ```

### Konsekuensi
- **Kelebihan:** nol dependensi tambahan, transisi sangat mudah diaudit & diuji menyeluruh (`4 × 4 × 2 = 32` kombinasi), pesan error jelas dalam Bahasa Indonesia, dan mudah dijelaskan saat Viva.
- **Enkapsulasi:** `krs.transition(target, aktor)` menjadi satu-satunya pintu pengubah `krs.status`; service dan router dilarang mengisi `krs.status = ...` secara langsung.
- **Kapan bermigrasi ke library:** jika state bertambah banyak (mis. status dibatalkan, masa perubahan KRS, atau callback kompleks per transisi).

---

## 2. Otorisasi 2 Lapis & Pemetaan Kode HTTP Polimorfik

### Keputusan
- **Lapis 1 (Peran):** ditegakkan oleh tabel transisi (`mahasiswa` hanya submit; `dosen_pa` hanya approve/revise).
- **Lapis 2 (Kepemilikan):** `KRSManager` memeriksa `krs.dosen_pa_id == dosen_pa_id` (dan `requester_nim == nim` bila disertakan).
- **Hierarki Exception Polimorfik (`app/services/krs_errors.py`):**
  - `KRSValidationError` → **400 Bad Request**
  - `KRSForbiddenError` → **403 Forbidden** (aktor/kepemilikan salah)
  - `KRSNotFoundError` → **404 Not Found**
  - `KRSTransitionError` & `KRSLockedError` → **409 Conflict** (status ilegal, KRS terkunci, atau versi basi)
- Satu `@app.exception_handler(KRSError)` di `app/main.py` menangani seluruh turunan `KRSError` sehingga lapisan service tetap bebas dari ketergantungan HTTP.

---

## 3. Transaksi Atomik & Kontrol Konkurensi (Pessimistic + Optimistic Locking)

### Konteks
Dua risiko utama saat persetujuan KRS:
1. Kegagalan di tengah proses `approve_krs` (status berubah tetapi `krs_log` atau `notifikasi` gagal tersimpan).
2. Race condition *tab ganda / layar basi*: mahasiswa menambah MK saat dosen PA sedang meninjau versi KRS sebelumnya.

### Keputusan
- **Transaksi Atomik:** perubahan status, `approved_at`, pencatatan `krs_log`, dan pencatatan baris `notifikasi` (bukan kirim email langsung di tengah transaksi) dijalankan dalam satu unit kerja dengan `db.commit()` sekali di akhir dan `db.rollback()` pada setiap exception.
- **Pessimistic Locking:** seluruh method mutasi (`add_course`, `remove_course`, `submit_krs`, `approve_krs`, `revise_krs`) mengunci baris `krs` yang sama via `with_for_update()` dan memeriksa status *setelah* kunci diperoleh.
- **Optimistic Locking (`version`):** kolom `krs.version` dinaikkan pada setiap perubahan (termasuk saat `add_course`/`remove_course` mengubah `krs_detail`). Jika dosen PA mengirim `version` yang sudah usang, request ditolak dengan **409 Conflict**.

### Limitasi Jujur
1. `dosen_pa_id` saat ini masih dikirim melalui body request karena modul autentikasi/login belum diimplementasikan.
2. SQLite mengabaikan klausa `FOR UPDATE`; pengujian locking di bawah beban konkuren nyata tetap perlu diverifikasi di MySQL/PostgreSQL.

---

## 4. Challenge Opsional (+10%): Guardrail Rekomendasi KRS (CSP Backtracking)

- Algoritma CSP Backtracking di `app/services/krs_recommender.py` menyusun jadwal tanpa bentrok dengan batas maksimal 24 SKS dan fungsi objektif memprioritaskan `(jumlah_mk_wajib, sks_wajib, total_sks)`.
- **Guardrail non-negotiable:** filter prasyarat memakai `PrerequisiteValidator` resmi dan seluruh hasil rekomendasi diverifikasi ulang melalui rantai validator resmi sebelum dikembalikan ke pengguna.
