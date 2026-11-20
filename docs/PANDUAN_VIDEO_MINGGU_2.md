# Panduan Rekaman Video Tutorial YouTube & Persiapan Viva — Minggu 2

- **Durasi ideal video:** **7–10 menit** (rekam layar + suara jelas + kamera wajah seperti video tutorial).
- **Tema Minggu 2:** *Refinements & Skenario* — Use Case Diagram, Sequence Diagram (PMB & Validasi KRS dengan *Alternate Flow*), Audit SOLID (*God Object* → 7 Service), Design Pattern (*Factory* & *Observer*), ADR, dan Dokumentasi AI.

---

## Alur Presentasi Step-by-Step di Video

### 1. Pembukaan & Ringkasan Minggu 1 → Minggu 2 (±45 detik)
- Perkenalkan diri dan sampaikan tujuan Minggu 2: menghidupkan Class Diagram Minggu 1 (*denah rumah*) menjadi interaksi nyata (*aktivitas penghuni*) lewat **Use Case Diagram** dan **Sequence Diagram**, lalu mengauditnya dengan **SOLID**, **Factory**, dan **Observer**, serta mendokumentasikan alasan desain di **ADR** dan **AI Usage Log**.

### 2. Demo Prompt Engineering & Use Case Diagram (±2 menit)
- Buka [prompts/ai-usage-log.md](../prompts/ai-usage-log.md) (bagian Minggu 2 — Prompt 1).
- Tunjukkan **Before vs After** ([before-use-case-draft.png](../diagrams/screenshots/before-use-case-draft.png) vs [after-use-case-final.png](../diagrams/screenshots/after-use-case-final.png) dan [diagrams/use-case-diagram.mmd](../diagrams/use-case-diagram.mmd)):
  - Jelaskan 4 elemen: **Aktor** (peran di luar kotak, termasuk aktor non-manusia `Sistem PMB`, serta pemisahan peran `Dosen PA` & `Dosen Pengampu`), **Use Case** (kata kerja + objek, hapus `"Klik Tombol Simpan"` karena itu detail UI), **System Boundary** (`SEVIMA-Mini`), dan **Asosiasi**.
  - **Tunjuk dan tekankan arah panah `<<include>>` vs `<<extend>>`:**
    - `Isi KRS` `--<<include>>-->` `Cek Prasyarat`, `Cek Batas SKS`, `Cek Jadwal Bentrok` (wajib jalan; panah dari **base → included**).
    - `Ajukan Revisi KRS` `--<<extend>>-->` `Approve KRS` (hanya jika ditolak Dosen PA; panah **terbalik dari extension → base**).
    - `Bayar Denda Keterlambatan` `--<<extend>>-->` `Bayar SPP` (hanya jika lewat jatuh tempo).

### 3. Demo Sequence Diagram Skenario 1 — PMB (±2 menit)
- Tunjukkan **Before vs After** ([before-sequence-pmb-draft.png](../diagrams/screenshots/before-sequence-pmb-draft.png) vs [after-sequence-pmb-final.png](../diagrams/screenshots/after-sequence-pmb-final.png) dan [diagrams/sequence-pmb.mmd](../diagrams/sequence-pmb.mmd)).
- Jelaskan dua tahap:
  1. **Pendaftaran:** `CalonMahasiswa` → `PMBService` → validasi input → simpan `PENDING` di `PMBRepository` → return nomor pendaftaran (plus cabang `alt` jika data invalid/duplikat).
  2. **Verifikasi & Kelulusan:** `Admin` memverifikasi → `PMBService` delegasi ke **`UserFactory`** untuk membuat `Mahasiswa` + NIM unik → simpan ke `PMBRepository` → kirim pesan **asinkron (`-)`)** ke `NotificationService` agar `Admin` tidak menunggu email terkirim → `NotificationService` kirim lewat `NotificationChannel` (dengan `alt` jika berkas tidak lengkap/ditolak, dan nested `alt` jika pengiriman email gagal → *log & retry*).

### 4. Demo Sequence Diagram Skenario 2 — Validasi KRS (±2 menit)
- Tunjukkan **Before vs After** ([before-sequence-krs-draft.png](../diagrams/screenshots/before-sequence-krs-draft.png) vs [after-sequence-krs-final.png](../diagrams/screenshots/after-sequence-krs-final.png) dan [diagrams/sequence-krs.mmd](../diagrams/sequence-krs.mmd)).
- Jelaskan elemen kunci:
  - Pemecahan *God participant* `SistemAkademik` menjadi `KRSManager`, `KRSValidator`, `AcademicRepository`, dan `NotificationService` (sinkron 100% dengan Class Diagram).
  - Fragmen **`loop`** untuk memeriksa prasyarat setiap mata kuliah satu per satu lewat `AcademicRepository`.
  - **Self-call** pada `KRSValidator` untuk menghitung total SKS (maks. 24) dan mengecek jadwal bentrok.
  - Praktik baik: kumpulkan **semua error sekaligus** + pesan error spesifik & *actionable*.
  - Fragmen **`alt` 4 cabang:** (1) Prasyarat belum lulus, (2) Total SKS > 24, (3) Jadwal bentrok, (4) Semua lolos → simpan `PENDING_APPROVAL` → notifikasi **asinkron** ke `Dosen PA`.

### 5. Demo Refactor Class Diagram — SOLID, Factory & Observer (±1,5 menit)
- Buka [diagrams/class-diagram.mmd](../diagrams/class-diagram.mmd) dan bandingkan [before-class-god-object.png](../diagrams/screenshots/before-class-god-object.png) vs [after-class-solid-final.png](../diagrams/screenshots/after-class-solid-final.png).
- Tunjuk bukti penerapan 5 prinsip SOLID & 2 Pattern:
  - **S (SRP):** 7 service modular (`PMBService`, `KRSManager`+`KRSValidator`, `JadwalService`, `TranscriptService`, `KeuanganService`, `PresensiService`, `NotificationService`), tidak ada kelas > 6 method.
  - **O (OCP):** `ValidationRule` (*Strategy*) → tambah `CekTunggakanRule` tanpa mengubah `KRSValidator`.
  - **L (LSP):** `getIPK()` hanya di `Mahasiswa`, tidak ditaruh di `User` (agar `CalonMahasiswa` tidak dipaksa punya IPK).
  - **I (ISP):** Interface kecil per peran (`PengisiKRS`, `PenyetujuKRS`, `Penilai`).
  - **D (DIP):** `NotificationService` bergantung pada interface `NotificationChannel` (*Dependency Injection*), sehingga ganti `EmailChannel` ke `WhatsAppChannel` atau *mock* saat unit test tidak mengubah kode service.
  - **Factory (`UserFactory`)** & **Observer (`NotificationSubscriber`)**.

### 6. Tunjukkan ADR PDF, Final UML PDF, AI Usage Log & Commit GitHub (±1 menit)
- Tampilkan [docs/ADR-architecture-decisions.pdf](ADR-architecture-decisions.pdf) (tunjuk 5 bagian: Konteks, Keputusan, Alternatif & Trade-off, Konsekuensi, Pattern).
- Tampilkan [docs/final-uml-diagrams.pdf](final-uml-diagrams.pdf).
- Tampilkan halaman **Commits** di GitHub (`PROJECT-PBO-1`) untuk membuktikan commit bertahap, deskriptif, dan bertanda `[AI-assisted]`.

---

## Contekan Cepat Persiapan Viva (Tanya Jawab Lisan Tanpa AI)

1. **Cara cepat bedakan `<<include>>` vs `<<extend>>`?**
   > Tanyakan: *"Kalau langkah ini dihilangkan, apakah use case dasarnya masih lengkap?"* Kalau **tidak lengkap** = `<<include>>` (panah base → included). Kalau **masih lengkap** dan hanya terjadi pada kondisi tertentu = `<<extend>>` (panah extension → base).
2. **Kenapa notifikasi ke Dosen PA dan email NIM memakai panah asinkron (`-)`)?**
   > Karena pengiriman email/notifikasi bergantung pada jaringan/SMTP eksternal yang bisa lambat atau gagal. Dengan asinkron, `Mahasiswa` dan `Admin` langsung mendapat respons tanpa harus menunggu email benar-benar terkirim.
3. **Kenapa `PMBService` tidak langsung `new Mahasiswa()` sendiri, melainkan lewat `UserFactory`?**
   > Supaya logika pembuatan 4 jenis `User` dan aturan generate NIM unik terpusat di satu tempat (menghindari `if-else` tersebar di banyak file dan memudahkan pengujian).
4. **Apa untungnya Observer Pattern (`NotificationSubscriber`) dan DIP (`NotificationChannel`)?**
   > `KRSManager` cukup mengumumkan event tanpa tahu siapa penerimanya; besok kalau butuh `AuditLogSubscriber` tinggal mendaftar (*OCP*). Dengan `NotificationChannel`, beralih dari email ke WhatsApp atau menyuntikkan *fake notifier* saat unit test di Minggu 5 cukup mengganti implementasi via konstruktor (*DIP*).
5. **Mengapa semua nama participant di Sequence Diagram harus ada di Class Diagram?**
   > Itulah arti **diagram yang sinkron** (*traceable*): setiap objek yang berinteraksi di skenario runtime harus benar-benar terdefinisi secara struktural di cetak biru Class Diagram agar siap diimplementasikan di Minggu 3–8.

---

## Checklist Sebelum Submit ke EdLink
- [ ] Video tutorial sudah di-upload ke YouTube (visibilitas **Public** atau **Unlisted**).
- [ ] Seluruh file terbaru sudah di-`git push` ke GitHub (`PROJECT-PBO-1`).
- [ ] File `docs/ADR-architecture-decisions.pdf` dan `docs/final-uml-diagrams.pdf` sudah dilampirkan di EdLink.
- [ ] Tautan YouTube & tautan GitHub sudah dibagikan di EdLink.
