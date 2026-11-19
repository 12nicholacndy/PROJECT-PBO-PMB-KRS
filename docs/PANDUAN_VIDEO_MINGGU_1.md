# Panduan Rekaman Video Tutorial YouTube & Persiapan Viva — Minggu 1

Durasi ideal video: **5–8 menit** (rekam layar + suara + kamera wajah seperti tutorial).

---

## Alur Presentasi Step-by-Step di Video

### 1. Pembukaan (±30 detik)
- Perkenalkan diri, mata kuliah Pemrograman Berorientasi Objek, dan tujuan tugas Minggu 1: merancang **Class Diagram UML** untuk sistem akademik **SEVIMA-Mini** (PMB, KRS, Jadwal, Nilai, dan Pembayaran SPP) menggunakan pendekatan *AI-First*.

### 2. Analisis Domain (±1 menit)
- Jelaskan teknik analisis kata benda & kata kerja:
  - **Hierarki User (`<<abstract>>`):** `Mahasiswa`, `Dosen`, `Admin`, `CalonMahasiswa` mewarisi `User` (atribut bersama `#id`, `#username`, `-passwordHash`, `#email` + method `+tampilkanDashboard()*`).
  - **5 Class Inti:** `Course` (self-association prasyarat), `Enrollment` (pemecah many-to-many), `Schedule`, `Grade`, dan `Payment` (+ `ProgramStudi` & `KRS`).

### 3. Demo Prompt Engineering & Iterasi (±2 menit)
- Buka file `prompts/ai-usage-log.md`.
- Tunjukkan 5 unsur prompt yang baik (**Role, Context, Component, Constraint, Format**).
- Tunjukkan perbedaan **Prompt Awal (Draft v1)** vs **Prompt Iterasi (Final)**:
  - Jelaskan 5 kesalahan AI yang berhasil kamu temukan di Draft v1:
    1. Class `Payment` lupa dibuat AI.
    2. Visibility modifier (`+`, `-`, `#`) belum ada (`password` & `ipk` harus `-` private!).
    3. `Grade` salah ditempel ke `Mahasiswa` (seharusnya *composition* ke `Enrollment`).
    4. Semua relasi cuma garis biasa tanpa membedakan *composition* (`*--`) vs *aggregation* (`o--`) dan tanpa kardinalitas.
    5. `Mahasiswa` menjadi *God Object* (mengurus validasi KRS, cetak transkrip, kirim email) → dipecah sesuai **SRP** menjadi `KRSManager`, `KRSValidator`, `TranscriptService`, dan `NotificationService`.

### 4. Demo Render Class Diagram di Mermaid Live Editor / GitHub (±2 menit)
- Buka `diagrams/class-diagram.mmd` (atau tempel ke https://mermaid.live / tampilkan langsung di halaman GitHub).
- Tunjuk dan jelaskan langsung pada diagram:
  - **Inheritance (`<|--`):** uji *is-a* (`Mahasiswa` adalah `User`).
  - **Composition (`*--`):** `Course *-- Schedule`, `KRS *-- Enrollment`, `Enrollment *-- Grade` (jika wadah dihapus, bagian ikut mati).
  - **Aggregation (`o--`):** `ProgramStudi o-- Dosen`, `ProgramStudi o-- Mahasiswa` (jika prodi tutup, dosen/mahasiswa tetap hidup).
  - **Self-Association:** `Course --> Course` (prasyarat mata kuliah).
  - **Tanpa Circular Dependency & tanpa God Object.**

### 5. Tunjukkan ADR PDF & Riwayat Commit Bertahap GitHub (±1 menit)
- Buka `docs/ADR-architecture-decisions.pdf` dan jelaskan singkat 2–3 keputusan utamanya (pemisahan `KRS`/`Enrollment`, `Grade` ke `Enrollment`, dan design patterns: *Chain of Responsibility*, *Factory*, *State Machine*, *Observer*).
- Buka halaman commits GitHub branch `PROJECT-PBO-1` untuk menunjukkan **commit bertahap (incremental)** (Draft v1 → Refactor SOLID → AI Usage Log → ADR PDF & README).

---

## Checklist Sebelum Upload ke EdLink
- [ ] Video tutorial sudah di-upload ke YouTube (pastikan visibilitas **Public** atau **Unlisted**, jangan Private).
- [ ] File `docs/ADR-architecture-decisions.pdf` siap di-upload/dibagikan di EdLink.
- [ ] Link repo GitHub branch `PROJECT-PBO-1` bisa dibuka oleh dosen.
