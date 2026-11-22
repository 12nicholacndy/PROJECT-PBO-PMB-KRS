# ADR-007: Modul Presensi QR Code, JWT Anti-Replay, Geolocation, Rate Limiting & AI Attendance Insights (Minggu 12)

- **Status:** Accepted
- **Tanggal:** 22 November 2026
- **Konteks:** Minggu 12 — Sistem presensi perkuliahan berbasis QR code yang di-generate dosen (valid 15 menit), di-scan mahasiswa dengan validasi jadwal, enrollment kelas, duplikasi, dan geolokasi (opsional), disertai rekap laporan `< 75%`, export Excel `.xlsx`, proteksi rate limiting, serta analisis pola kehadiran AI.

---

## 1. Resolusi Kontradiksi Waktu: QR Valid 15 Menit vs Status `terlambat` (15–30 Menit)

Dalam spesifikasi tugas terdapat dua aturan waktu sekaligus:
1. QR code hanya valid **15 menit** (`exp = iat + 15 menit`).
2. Status presensi mahasiswa dihitung **`hadir`** jika scan dalam **15 menit pertama** dan **`terlambat`** jika scan **setelah 15 menit (maks. 30 menit)**.

**Keputusan Desain:**
- Umur setiap token JWT QR tetap ketat **15 menit** sejak QR di-generate (`qr_expires_at = qr_generated_at + 15 menit`) agar syarat keamanan *QR expiration* tidak pernah dilonggarkan.
- Status `hadir` (≤ 15 menit) vs `terlambat` (> 15 s.d. ≤ 30 menit) dihitung relatif terhadap **`session_started_at` (waktu mulai jadwal kuliah `jadwal.jam_mulai`)**, bukan terhadap `iat` QR terakhir.
- Apabila dosen me-refresh QR di tengah masa awal kuliah (mis. kuliah mulai `08:00`, dosen refresh QR pada `08:12`), QR baru tetap berumur 15 menit (`08:12–08:27`) namun `session_started_at` tetap `08:00`. Mahasiswa yang scan pukul `08:20` menggunakan QR baru tetap valid secara JWT dan tercatat **`terlambat`** (20 menit dari `08:00`), sementara scan lewat pukul `08:30` (> 30 menit) tetap ditolak dengan **HTTP 400**.

---

## 2. Pencegahan Replay Attack dengan `jti` Unik & Tabel `attendance_sessions`

Snippet dasar JWT yang hanya berisi `{schedule_id, iat, exp}` tidak menyimpan status sesi di server sehingga rentan dipakai ulang bila QR diganti atau sesi sudah ditutup.

**Keputusan Desain:**
- Menambahkan tabel `attendance_sessions` dan `attendance_records` melalui migrasi Alembic `b2c3d4e5f6a7`.
- Setiap pembuatan atau refresh QR menghasilkan UUID unik **`jti`** yang disimpan di kolom `attendance_sessions.active_jti` (ber-`UniqueConstraint`) sekaligus disematkan ke payload JWT (`schedule_id`, `session_id`, `jti`, `session_date`, `iat`, `exp`).
- Saat `POST /attendance/scan` dijalankan, sistem memeriksa:
  1. Validitas tanda tangan `HS256` & `exp` JWT (**401** jika expired/rusak),
  2. Status sesi masih `open` (**401** jika sesi sudah ditutup),
  3. `claims.jti == session.active_jti` (**401** jika token lama diputar ulang / replay attack).

---

## 3. Validasi Enrollment Tingkat Kelas Paralel & Geolocation Haversine

- **Anti Scan Kelas Orang Lain (`403 Forbidden`):** Validasi memeriksa `KRS` + `KRSDetail` untuk `(nim, semester, kode_mk)` **dan** mencocokkan `krs_detail.kelas_id == jadwal.kelas_id` (bila `kelas_id` terisi). Dengan demikian, mahasiswa Kelas B pada mata kuliah yang sama tetap ditolak saat mencoba memindai QR Kelas A.
- **Geolocation Validation (Opsional):** Menggunakan rumus **Haversine** (`haversine_distance_meters`) untuk menghitung jarak koordinat mahasiswa terhadap titik kelas (`latitude`, `longitude`, `radius_meters`). Jika `require_location=False` dan mahasiswa tidak mengirim koordinat, kontrak dasar `{ token, nim }` tetap berjalan mulus; jika lokasi diaktifkan atau dikirim dan jarak melewati `radius_meters`, scan ditolak (**400**).

---

## 4. Keamanan Rate Limiting & Export Excel (`.xlsx`)

- **Rate Limiting (`429 Too Many Requests`):** `ScanRateLimiter` berbasis *sliding window* per kunci `(client_ip, nim)` (batas 5 request per 60 detik) dengan header `Retry-After` untuk menahan spam scan tanpa memengaruhi mahasiswa lain.
- **Export Excel (`.xlsx`):** Memakai library pure-Python `openpyxl` menghasilkan workbook 3 sheet (`Rekap Presensi`, `Warning (<75%)`, dan `Log Presensi Detail`) lengkap dengan format persentase dan pewarnaan status.

---

## 5. AI Attendance Insights Deterministik (Anti Halusinasi Angka)

- Seluruh metrik presensi per mahasiswa, daftar warning `< 75%`, serta deteksi pola absen berdasarkan kombinasi `(mahasiswa, hari, periode_waktu)` dan `(kelas, hari, jam_mulai)` dihitung secara **100% deterministik** di `app/services/ai_attendance_analyzer.py`, menghasilkan struktur JSON konsisten dengan field `insights` dan `actions`.
- Mendukung dua sumber data: input array JSON langsung (`POST /attendance/ai-insights`) sesuai Step 3, serta pembacaan langsung dari database (`GET /attendance/ai-insights`). Opsional diperkaya narasi Gemini bila `GEMINI_API_KEY` dikonfigurasi dengan fallback offline deterministik.

---

## Konsekuensi & Limitasi Jujur

- `ScanRateLimiter` saat ini menggunakan struktur memori per proses (`threading.Lock` + `deque`); untuk deployment multi-worker produksi perlu dipindahkan ke Redis (`INCR`/`EXPIRE` atau sliding window Lua).
- Integrasi otomatis persentase presensi riil ke aturan `presensi_persen >= 75%` pada Modul Nilai (Minggu 9) dipertahankan bersifat terpisah (*decoupled*) agar seluruh 195 test Minggu 3–11 tetap lulus tanpa regresi.
