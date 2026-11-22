# ADR-006: Modul Keuangan (SPP), Payment Webhook, APScheduler, Block KRS & AI Financial Insights (Minggu 11)

- **Status:** Accepted
- **Tanggal:** 22 November 2026
- **Konteks:** Minggu 11 — Sistem Manajemen Pembayaran SPP dengan state management (`unpaid`, `partial`, `paid`, `overdue`), cron job otomatis (APScheduler), denda keterlambatan, webhook payment gateway ber-signature, integrasi blokir KRS, dashboard keuangan real-time, dan laporan AI Financial Insights JSON.

---

## 1. Keputusan Skema Database (25%)

Menambahkan 3 tabel baru melalui migrasi Alembic `a1b2c3d4e5f6`:

1. **`payment_method`**: master metode pembayaran (`code` unik, `name`, `category`, `is_active`), otomatis diisi dengan metode default (`va_bca`, `va_bni`, `gateway_va`, `transfer_bank`, `qris`, `ewallet`, `tunai`).
2. **`billing`**:
   - Mengikat `nim` (FK `calon_mahasiswa.nim`) dan `semester` (5 digit) dengan `UniqueConstraint(nim, semester)` agar `generate_billing()` **idempoten** jika cron job terpicu lebih dari sekali.
   - Kolom terpisah `amount` (pokok SPP), `late_fee` (denda keterlambatan), dan `paid_amount` (akumulasi pembayaran sukses) dengan properti turunan `total = amount + late_fee` dan `remaining_amount = max(0, total - paid_amount)`.
   - Status enum ber-constraint: `unpaid`, `partial`, `paid`, `overdue`.
3. **`payment`**:
   - Mencatat setiap transaksi pembayaran (`billing_id` FK `billing.id`, `payment_method_id` FK `payment_method.id`, `transaction_id` **UNIQUE**, `amount`, `status`, `source` `'manual'`/`'webhook'`, `paid_at`, `receipt_sent`, `receipt_email`, `raw_payload` JSON).

**Alasan pemisahan `amount`, `late_fee`, dan `paid_amount`:**
- Pembayaran cicilan (`partial` → `paid`) dievaluasi berdasarkan **akumulasi `paid_amount + amount >= billing.total`**, bukan nominal transaksi terakhir saja.
- Transparansi audit antara pokok SPP dan denda keterlambatan tetap terjaga untuk dashboard dan kwitansi.

---

## 2. Tarif SPP, Denda Keterlambatan & APScheduler (35%)

1. **Tarif per Program Studi:**
   - Program studi/fakultas mengandung *Teknik* → **Rp 5.000.000**.
   - Program studi/fakultas mengandung *Ekonomi / Manajemen / Akuntansi / Bisnis* → **Rp 4.000.000**.
   - Fallback prodi lain (mis. *Sistem Informasi*) → **Rp 4.500.000**.
2. **Jatuh Tempo & Denda:**
   - `due_date = semester_start + 14 hari` (2 minggu), status awal `'unpaid'`.
   - Jika melewati `due_date` dan belum `paid`, status otomatis diperbarui menjadi `'overdue'` dengan denda **1% per minggu keterlambatan** (dibulatkan ke atas per minggu), dibatasi maksimal **10% dari pokok SPP**.
3. **APScheduler Cron Jobs (`app/services/billing_scheduler.py`):**
   - `semester_billing_generator`: `CronTrigger(month="2,8", day=1, hour=0, minute=5)` — otomatis men-generate tagihan SPP awal semester Ganjil & Genap.
   - `daily_overdue_reminder`: `CronTrigger(hour=7, minute=0)` — otomatis mengecek tagihan jatuh tempo, menghitung denda, dan mengirim pengingat (simulasi email + tabel `notifikasi`).
   - Dikelola di `lifespan` FastAPI (`start()` saat startup, `shutdown(wait=False)` saat teardown).

---

## 3. Keamanan & Idempotensi Payment Webhook (15%)

- Endpoint `POST /api/finance/webhook/payment` membaca **raw request body bytes** dan memverifikasi header `X-Webhook-Signature` dengan **HMAC-SHA256** menggunakan `hmac.compare_digest` (constant-time, anti timing attack; mendukung format `sha256=<hex>` maupun `<hex>` polos). Signature hilang atau tidak cocok ditolak dengan **401 Unauthorized**.
- Seluruh proses pembayaran (`process_payment`) berjalan dalam **1 transaksi database atomik** dengan `with_for_update()` pada baris `billing` dan `rollback()` jika terjadi kegagalan.
- `payment.transaction_id` yang unik membuat replay webhook dari payment gateway **idempoten** (mengembalikan `200 OK` dengan `duplicate: true` tanpa menambah `paid_amount` dua kali).
- Pembayaran berhasil otomatis memicu pengiriman **kwitansi via email simulasi** (`EmailNotificationSimulator`) dan mencatat `Notifikasi` di database.

---

## 4. Integrasi Block KRS Tanpa Merusak Modul Sebelumnya

- Menambahkan `BillingArrearsValidator` ke rantai validator KRS (`app/services/krs_validators.py`) serta guard `_ensure_no_spp_arrears()` pada `KRSManager.add_course()`, `KRSManager.submit_krs()`, dan `_validate_courses()`.
- Mahasiswa diblokir (**HTTP 403 Forbidden**) apabila memiliki minimal satu tagihan berstatus `unpaid`, `partial`, atau `overdue`.
- Mahasiswa yang belum memiliki baris tagihan di tabel `billing` (termasuk seluruh skenario test Minggu 5–10) tetap dapat mengisi KRS tanpa regresi. Begitu tagihan dilunasi (`paid`), akses KRS langsung terbuka otomatis.

---

## 5. Dashboard Keuangan Real-Time (20%) & AI Financial Insights (5%)

- `GET /api/finance/dashboard` mengagregasi secara real-time: ringkasan per mahasiswa (total tagihan, sudah bayar, sisa, status blokir KRS), collection rate per program studi, dan grafik pembayaran per bulan, disajikan secara visual pada halaman `GET /keuangan/dashboard` (grafik SVG offline-ready tanpa ketergantungan CDN).
- `GET /api/finance/ai-insights` (dari DB) dan `POST /api/finance/ai-insights/csv` (dari CSV `nim, jumlah, tanggal, status`) menghitung **seluruh metrik numerik secara deterministik** (total revenue bulan ini, collection rate, Top 10 penunggak terbesar, rekomendasi jumlah mahasiswa menunggak > 2 bulan / > 60 hari) agar bebas halusinasi angka, dengan opsi narasi eksekutif Gemini bila `GEMINI_API_KEY` dikonfigurasi dan fallback offline deterministik.

---

## Konsekuensi & Limitasi Jujur

- Pengiriman email kwitansi & reminder menggunakan `EmailNotificationSimulator` + tabel `notifikasi` (bukan SMTP eksternal) agar deterministik, cepat, dan mudah diverifikasi secara lokal.
- Pada SQLite lokal, klausa `SELECT ... FOR UPDATE` diabaikan oleh mesin SQLite; pengujian konkurensi row-level lock nyata tetap disarankan pada PostgreSQL/MySQL.
