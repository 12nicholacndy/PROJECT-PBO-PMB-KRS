# ADR-004: Desain Observer Pattern, Transactional Outbox, Integrasi KRS, AI Rescheduler Guardrail, dan Ekspor iCalendar

- **Status:** Diterima
- **Tanggal:** 20 November 2026
- **Modul:** Modul Jadwal & Ruangan (Minggu 8 — Bagian 2)

---

## 1. Kontrak Observer: `typing.Protocol` vs `abc.ABC`, serta Observer vs Pub/Sub

### Keputusan
1. **Memakai `typing.Protocol`** untuk kontrak `ScheduleObserver`, `NotificationChannel`, `ParticipantRepository`, dan `LLMClient` (`app/services/schedule_observers.py`, `app/services/ai_rescheduler.py`).
   - **Alasan:** *Structural subtyping* tidak memaksa pewarisan kelas dasar, sehingga test double ringan (`SpyObserver`, `BrokenObserver`, `FakeLLMClient`) dapat dibuat tanpa *boilerplate* pewarisan dan tetap diperiksa secara statis oleh type checker.
2. **Memakai Observer Pattern in-process** untuk kebutuhan SEVIMA Mini saat ini, bukan langsung memasang message broker (Redis/RabbitMQ/Kafka), sambil mencatat perbedaannya:
   - Pada **Observer**, `ScheduleSubject` menyimpan referensi langsung ke daftar observer dan memanggil `update(event)` sinkron di proses yang sama.
   - Pada **Pub/Sub**, penerbit dan pelanggan dipisahkan oleh *event bus / message broker* berbasis topik sehingga keduanya tidak saling mengenal dan dapat berjalan asinkron lintas proses.
   - Tabel **`outbox_events`** yang kita tambahkan menjadi langkah fondasi menuju Pub/Sub karena event disimpan secara tahan lama di database hingga diproses dispatcher/worker.

---

## 2. Transactional Outbox (`outbox_events`) vs Tabel `notifikasi` (Minggu 6)

### Konteks
Pada Minggu 6, tabel `notifikasi` telah dibuat dan diuji sebagai kotak masuk pesan mahasiswa (`GET /api/krs/{nim}/notifikasi`). Di Minggu 8, dibutuhkan tabel outbox tahan lama dengan kolom `event_id` (unik), `payload` (JSON), `status` (`pending`/`sent`/`failed`), dan `attempts`.

### Keputusan
- **Mempertahankan tabel `notifikasi`** sebagai kotak masuk mahasiswa (menjaga kompatibilitas 100% dengan Modul KRS Minggu 6 dan tetap mengisinya saat KRS diinvalidasi).
- **Menambahkan tabel terpisah `outbox_events`** melalui migrasi Alembic `e8b4c9d1023a` untuk menjamin pola *Transactional Outbox* pada perubahan jadwal (`SCHEDULE_CHANGED`, `SCHEDULE_DELETED`).

### Pencegahan 4 Bug Klasik Observer (Checklist Audit)
1. **Notify sebelum commit:** `OutboxEvent` ditulis dalam transaksi yang sama dengan `UPDATE/DELETE jadwal` + invalidasi KRS, lalu `OutboxDispatcher.run()` dipanggil **hanya setelah `db.commit()`**.
2. **Satu observer gagal membungkam lainnya:** `ScheduleSubject.notify()` mengiterasi salinan `list(self._observers)` dan membungkus tiap `observer.update(event)` dengan `try/except`, mencatat exception, dan mengembalikan nama observer gagal.
3. **Memory leak akibat `attach()` di endpoint:** `init_default_schedule_subject()` dipanggil sekali pada FastAPI `lifespan` (`app/main.py`); `attach()` juga menolak duplikasi observer.
4. **Notifikasi ganda saat retry:** setiap kanal (`LogChannel`: `email`, `sms`, `push`) memeriksa kunci idempotensi `{event_id}:{role}:{id}:{channel}` sebelum menulis log (*at-least-once delivery + deduplication*), dengan batas retry 5 kali sebelum ditandai `failed`.

---

## 3. Integrasi KRS: Menambah `KRSAktor.SISTEM` (Prinsip Satu Pintu)

### Keputusan
- Tidak pernah mengubah kolom `krs.status` secara langsung dari `SchedulingService`.
- Menambahkan anggota enum baru `KRSAktor.SISTEM = "sistem"` beserta 2 transisi resmi di `KRS_TRANSITIONS` (`app/models.py`):
  - `(KRSStatus.SUBMITTED, KRSAktor.SISTEM) -> {KRSStatus.REVISION}`
  - `(KRSStatus.APPROVED, KRSAktor.SISTEM) -> {KRSStatus.REVISION}`
- KRS berstatus `DRAFT` dan `REVISION` dibiarkan tetap seperti semula karena mahasiswa masih bebas mengubahnya dan validator akan mengecek ulang saat submit.
- `KRSInvalidator.invalidate_for_kelas()` berjalan **tanpa `db.commit()`** agar berpartisipasi dalam transaksi atomik milik `SchedulingService`.
- Pengujian menyeluruh state machine di `tests/test_krs_minggu6.py` diperbarui dari 32 kombinasi (4 sah, 28 ilegal) menjadi **48 kombinasi (6 sah, 42 ilegal)**.

---

## 4. AI Rescheduling Assistant: Slot Dihitung Kode + 4 Lapis Guardrail + Satu Pintu Apply

### Keputusan
1. **Daftar slot tersedia dihitung kode sendiri** (`find_available_slots()` di `app/services/ai_rescheduler.py`) dengan mengecek kapasitas dan memanggil `detect_schedule_conflict()` Minggu 7 — AI hanya memilih dan memberi alasan.
2. **Abstraksi `LLMClient`:** mendukung tier gratis Google AI Studio (`GEMINI_API_KEY`) dan OpenRouter (`OPENROUTER_API_KEY`) dengan temperature `0.1`, fallback lokal deterministik bila API key belum disetel agar demo offline tetap lancar, serta `FakeLLMClient` untuk seluruh automated test.
3. **4 Lapis Saringan Wajib (`filter_ai_suggestions`):**
   - **Saringan 1 (Parsing):** ekstraksi substring antara `[` pertama dan `]` terakhir; JSON rusak menghasilkan `[]` tanpa crash.
   - **Saringan 2 (Skema Pydantic):** validasi `SaranJadwal` per item (`hari` 1..6, `jam_mulai < jam_selesai`); item rusak dibuang satu per satu.
   - **Saringan 3 (Ruangan & Kapasitas DB):** ruangan wajib ada di tabel `ruangan` dan `kapasitas >= max(kuota, terdaftar)`.
   - **Saringan 4 (Deteksi Bentrok 3D):** bungkus kandidat sebagai `JadwalSlot(id=-1, ...)` dan jalankan `detect_schedule_conflict()`; jika `-1` muncul di konflik mana pun, saran dibuang. Maksimal 3 saran yang lolos.
4. **Tombol Terapkan (Conflict Resolver UI):** langsung memanggil `PUT /api/jadwal/{id}` (`SchedulingService.update()`) dengan header peran `X-Role: admin_akademik`. Jika slot keburu diambil admin lain di antara waktu saran diminta dan tombol diklik, server mengembalikan `409 Conflict` dan UI meminta admin memuat ulang saran.

---

## 5. Ekspor iCalendar (`.ics` / RFC 5545)

### Keputusan
- Menambahkan kolom `jadwal.sequence` (default `0`, naik `+1` setiap `update()`) pada migrasi `e8b4c9d1023a`.
- `export_jadwals_to_ics()` menghasilkan teks `text/calendar` dengan:
  - Pemisah baris ketat **CRLF** (`\r\n`).
  - `UID` stabil (`jadwal-{id}@sevima-mini`) agar impor ulang memperbarui event lama, bukan membuat duplikat.
  - `DTSTART;TZID=Asia/Jakarta` & `DTEND;TZID=Asia/Jakarta` dihitung dari tanggal awal semester + `jadwal.hari`.
  - `RRULE:FREQ=WEEKLY;UNTIL=YYYYMMDDTHHMMSSZ` dengan nilai `UNTIL` wajib berformat **UTC (`Z`)**.
  - `SEQUENCE:{jadwal.sequence}` yang mencerminkan revisi terbaru jadwal.

---

## 6. Limitasi Jujur & Risiko
1. **SQLite & `SKIP LOCKED`:** pengujian otomatis berjalan di SQLite yang mengabaikan klausa `FOR UPDATE SKIP LOCKED`; perilaku konkuren antar banyak worker `OutboxDispatcher` tetap perlu diverifikasi pada PostgreSQL/MySQL.
2. **Idempotency key di memori `LogChannel`:** untuk simulasi log dalam satu proses, `sent_keys` disimpan di memori `LogChannel`. Pada produksi dengan beberapa worker atau restart proses, kunci idempotensi perlu disimpan di tabel DB/Redis tersendiri.
3. **Otorisasi Admin Akademik:** pemeriksa peran `require_admin_akademik` sementara membaca header `X-Role: admin_akademik` karena modul autentikasi/JWT belum masuk cakupan Minggu 8.
