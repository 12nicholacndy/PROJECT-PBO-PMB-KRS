# AI Usage Documentation — Materi 1 (Minggu 1 & Minggu 2: Desain UML SEVIMA-Mini)

- **Mata Kuliah:** Pemrograman Berorientasi Objek (AI-First Approach)
- **Materi:** Materi 1 — Minggu 1 (Class Diagram) & Minggu 2 (Refinements, Use Case, Sequence Diagram, SOLID & Design Patterns)
- **Project:** SEVIMA-Mini (Sistem Informasi Akademik)
- **AI Tools:** Gemini / Antigravity Assistant + Mermaid v11 Renderer (validasi render & ekspor visual)
- **Kepatuhan Privasi (AI-First Development Charter):** Seluruh prompt di bawah ini hanya menggunakan entitas domain & data fiktif; **tidak ada data pribadi, kredensial, maupun informasi rahasia** yang dimasukkan ke dalam prompt.

---

# BAGIAN A — MINGGU 1: Eksplorasi Domain, Prompt Engineering & Validasi Class Diagram

## 1. Enam Iterasi Prompt Terbaik Minggu 1 (5 Unsur: Role, Context, Component, Constraint, Format)

### Prompt 1 — Eksplorasi Domain (Analisis Kata Benda & Kata Kerja)
> **Prompt:**  
> "Anda adalah senior software architect. Bantu saya melakukan analisis domain untuk sistem akademik kampus kecil (SEVIMA-Mini) yang mencakup modul PMB, KRS (maksimal 24 SKS & cek prasyarat), Jadwal, Nilai/Transkrip, dan Pembayaran SPP. Identifikasi kandidat class utama (kata benda yang punya data & perilaku), atribut kunci, dan method utama (kata kerja). Pisahkan atribut sederhana seperti NIM agar tidak dijadikan class sendiri. Output dalam bentuk tabel ringkas."
- **Tujuan:** Menentukan kandidat class sebelum membuat diagram agar AI tidak menebak entitas di luar requirement (seperti `Kantin` atau `Gedung`).
- **Hasil & Evaluasi:** Menghasilkan hierarki `User` (4 turunan) serta entitas inti `Course`, `Enrollment`, `Schedule`, `Grade`, `Payment`, dan `ProgramStudi`.

### Prompt 2 — Draft Awal Class Diagram (Titik Awal Lembar Tugas)
> **Prompt:**  
> "Anda adalah arsitek sistem. Buat class diagram UML untuk sistem akademik dengan komponen berikut: User sebagai base class (Mahasiswa, Dosen, Admin, CalonMahasiswa), Course (kode, nama, SKS, prasyarat), Enrollment yang menghubungkan Mahasiswa dan Course, Schedule (hari, jam, ruangan), dan Grade untuk nilai mahasiswa. Terapkan prinsip SOLID. Gunakan composition untuk relasi kuat dan aggregation untuk relasi lemah. Output dalam format Mermaid."
- **Tujuan:** Membangun draft v1 (`diagrams/class-diagram.mmd`, commit `cabcd25`) sebagai pembanding.
- **Temuan Kritis:** Draft v1 memiliki 5 kelemahan: (1) class `Payment` belum ada, (2) visibility modifier (`+`, `-`, `#`) belum tercantum, (3) kardinalitas (`1`, `0..*`) tidak ditulis, (4) `Grade` terhubung langsung ke `Mahasiswa` alih-alih `Enrollment`, dan (5) `Mahasiswa` menjadi *God Object* (mengurus validasi KRS, cetak PDF, sekaligus kirim email).

### Prompt 3 — Melengkapi Komponen Wajib (`Payment`, Visibility & Kardinalitas)
> **Prompt:**  
> "Perbaiki class diagram Mermaid sistem akademik SEVIMA-Mini sebelumnya dengan aturan ketat:
> 1. Tambahkan class `Payment` (tagihan SPP per semester) yang berelasi 1-ke-banyak dari `Mahasiswa`, serta `ProgramStudi`.
> 2. Tandai `User` sebagai `<<abstract>>` dengan method abstrak `+tampilkanDashboard()*`.
> 3. Wajib beri visibility modifier pada SEMUA atribut dan method: buat seluruh data sensitif private (`-passwordHash`, `-ipk`, `-nim`), data yang diwariskan protected (`#id`, `#username`, `#email`), dan method akses public (`+`).
> 4. Tulis kardinalitas eksplisit (`1`, `0..1`, `1..*`, `0..*`) di kedua ujung setiap relasi.
> Output hanya kode Mermaid `classDiagram` valid."
- **Tujuan:** Menegakkan *encapsulation*, kelengkapan 5 class inti, dan kardinalitas yang jelas.

### Prompt 4 — Validasi Relasi UML (Inheritance, Composition, Aggregation, Self-Association)
> **Prompt:**  
> "Review seluruh relasi pada class diagram Mermaid SEVIMA-Mini berikut dan perbaiki kesalahan semantik UML:
> - Pastikan inheritance hanya dipakai untuk hubungan *is-a* (`User <|-- Mahasiswa/Dosen/Admin/CalonMahasiswa`).
> - Gunakan **composition** (`*--`) hanya bila bagian ikut mati saat wadah dihapus: `Course *-- Schedule`, `KRS *-- Enrollment`, dan `Enrollment *-- Grade`.
> - Gunakan **aggregation** (`o--`) bila bagian tetap hidup saat wadah dihapus: `ProgramStudi o-- Dosen` dan `ProgramStudi o-- Mahasiswa`.
> - Tambahkan self-association `Course --> Course` untuk relasi prasyarat mata kuliah.
> - Hubungkan `CalonMahasiswa ..> Mahasiswa` (konversi saat lulus PMB), bukan inheritance antar keduanya."
- **Tujuan:** Memastikan AI tidak menukar *composition* dengan *aggregation* dan memodelkan prasyarat serta konversi `CalonMahasiswa` secara benar.

### Prompt 5 — Audit SOLID Awal & Refactor *God Object* (SRP + DIP)
> **Prompt:**  
> "Audit class diagram SEVIMA-Mini berikut terhadap prinsip SOLID, khususnya Single Responsibility Principle (SRP) dan Dependency Inversion Principle (DIP). Identifikasi method di class `Mahasiswa` yang melanggar SRP (validasi KRS, cetak transkrip, kirim notifikasi), lalu pisahkan ke class service yang fokus (`KRSManager`, interface `KRSValidator`, `TranscriptService`, `NotificationService`). Pastikan tidak ada class dengan lebih dari 10 method dan tidak ada *circular dependency*."
- **Tujuan:** Menghilangkan *God Object* awal pada `Mahasiswa` (commit `de11f67`).

### Prompt 6 — Review Akhir & Penyusunan ADR Minggu 1
> **Prompt:**  
> "Berdasarkan class diagram final SEVIMA-Mini, bantu susun kerangka Architecture Decision Record (ADR) yang menjelaskan secara ringkas alasan dan trade-off untuk 5 keputusan desain: (1) pemisahan `KRS` dan `Enrollment`, (2) relasi `Grade` ke `Enrollment` bukan `Mahasiswa`, (3) konversi objek `CalonMahasiswa` ke `Mahasiswa`, (4) pemecahan *God Object* ke lapisan service, dan (5) penggunaan design pattern (Chain of Responsibility, Factory/Generator, Observer)."
- **Tujuan:** Menyusun dokumentasi keputusan arsitektur awal di `docs/ADR-architecture-decisions.pdf`.

---

## 2. Perbandingan Hasil AI Awal vs Revisi Manual — Minggu 1 (Before–After)

- **Bukti Visual:** [before-class-god-object.png](../diagrams/screenshots/before-class-god-object.png) → [after-class-solid-final.png](../diagrams/screenshots/after-class-solid-final.png)

| No | Aspek Desain | Hasil AI Awal (Draft v1, `cabcd25`) | Revisi Manual (Final) | Alasan Revisi Diperlukan |
|---|---|---|---|---|
| 1 | **Kelengkapan Class Inti** | Hanya membuat 4 class inti (`Course`, `Enrollment`, `Schedule`, `Grade`); **`Payment` hilang** | Menambahkan class `Payment`, `ProgramStudi`, dan `KRS` | `Payment` wajib ada di deliverable (35%) karena termasuk 5 class inti sistem akademik (modul SPP). |
| 2 | **Encapsulation & Visibility** | Semua atribut tanpa modifier (terbaca publik), termasuk `password` dan `ipk` | Memakai `-passwordHash`, `-ipk`, `#id`, `#username`, `#email`, serta method `+` | Menjaga enkapsulasi: `password` tidak boleh terbaca kelas lain dan `ipk` hanya boleh berubah lewat perhitungan nilai yang sah. |
| 3 | **Relasi `Grade`** | `Mahasiswa --> Grade` (langsung ke mahasiswa) | `Enrollment "1" *-- "0..1" Grade` (**composition**) | Nilai selalu melekat pada satu mahasiswa di satu mata kuliah dan satu semester tertentu; jika `Enrollment` dihapus, `Grade` tidak lagi bermakna. |
| 4 | **Struktur KRS vs Enrollment** | Hanya satu tabel `Enrollment` menyimpan status KRS | Memecah menjadi `KRS "1" *-- "1..*" Enrollment` | Yang disetujui Dosen PA adalah satu kartu KRS utuh per semester, bukan setengah *approved* dan setengah *draft* per mata kuliah. |
| 5 | **Composition vs Aggregation** | Semua relasi memakai garis asosiasi biasa `-->` tanpa kardinalitas | Membedakan `*--` (`Course*--Schedule`, `KRS*--Enrollment`, `Enrollment*--Grade`), `o--` (`ProgramStudi o-- Dosen/Mahasiswa`), dan self-association `Course --> Course` | Menunjukkan siklus hidup objek secara akurat dan mencegah kesalahan kardinalitas many-to-many. |
| 6 | **SOLID / Anti God Object** | `Mahasiswa` memiliki `validasiKRS()`, `cetakTranskripPDF()`, `kirimEmailTagihan()` | Dipecah menjadi service modular (`PMBService`, `KRSManager`, `KRSValidator`, `TranscriptService`, `NotificationService`, dll.) | Memenuhi SRP & OCP: perubahan format transkrip atau aturan SKS tidak memaksa perubahan class `Mahasiswa`. |

---

## 3. Catatan OPAR — Minggu 1
- **Observe:** Draft awal class diagram yang dihasilkan AI belum menyertakan `Payment`, tidak menuliskan visibility modifier dan kardinalitas, menempatkan `Grade` langsung ke `Mahasiswa`, serta menumpuk terlalu banyak tanggung jawab pada class `Mahasiswa` (*God Object*).
- **Plan:** Mengiterasikan prompt dengan 5 unsur (Role, Context, Component, Constraint, Format), memvalidasi relasi dengan uji *is-a* dan uji siklus hidup, memeriksa *circular dependency*, serta menerapkan SRP/DIP.
- **Act:** Menyusun `diagrams/class-diagram.mmd` secara bertahap (`cabcd25` → `de11f67`), mendokumentasikan 6 iterasi prompt, dan menyusun ADR awal.
- **Reflect:** Diagram berbasis teks (Mermaid) memudahkan pelacakan diff per commit di GitHub. Validasi kritis terhadap output AI terbukti krusial karena AI dapat terlihat meyakinkan meski melewatkan entitas wajib (`Payment`) dan melanggar enkapsulasi.

---

# BAGIAN B — MINGGU 2: Refinements, Use Case, Sequence Diagram, SOLID & Design Patterns

## 4. Enam Prompt Terbaik Minggu 2 (Termasuk Prompt Perbaikan & Self-Critique)

### Prompt 7 — Use Case Diagram + Penjelasan Relasi `<<include>>` & `<<extend>>`
> **Prompt:**  
> "Anda adalah software architect UML. Buatkan Use Case Diagram untuk sistem akademik SEVIMA-Mini dalam format Mermaid `flowchart LR` dengan System Boundary `SEVIMA-Mini`. Aktor minimal yang wajib ada di luar boundary: `Calon Mahasiswa`, `Mahasiswa`, `Dosen PA`, `Dosen Pengampu`, `Admin Akademik`, dan aktor eksternal `Sistem PMB`. Sertakan use case pendaftaran PMB (`Daftar sebagai Calon Mahasiswa`, `Verifikasi Berkas PMB`, `Tetapkan Kelulusan & Generate NIM`), KRS (`Isi KRS`, `Approve KRS`, `Ajukan Revisi KRS`), Nilai/Transkrip (`Input Nilai`, `Generate Transkrip`), dan Keuangan (`Bayar SPP`, `Bayar Denda Keterlambatan`). Gunakan kata kerja + objek (hindari detail UI seperti 'klik tombol simpan'). Tandai relasi `<<include>>` (panah base -> included) dan `<<extend>>` (panah extension -> base), lalu jelaskan alasan setiap relasi `<<include>>` dan `<<extend>>` yang Anda buat."
- **Alasan Dipilih:** Menghasilkan perbaikan langsung dari Draft v1 (`65b54c6`) yang sebelumnya membalik arah panah `<<extend>>`, melupakan aktor `Sistem PMB`, dan memasukkan detail antarmuka `"Klik Tombol Simpan KRS"`.
- **Hasil & Validasi Manual:** Semua arah panah diverifikasi dengan uji *"jika langkah ini dihilangkan, apakah use case dasar masih lengkap?"* → `Isi KRS` `--<<include>>-->` `Cek Prasyarat / Cek SKS / Cek Jadwal Bentrok` (wajib), sedangkan `Ajukan Revisi KRS` `--<<extend>>-->` `Approve KRS` dan `Bayar Denda Keterlambatan` `--<<extend>>-->` `Bayar SPP` (opsional, panah terbalik). Commit final: `59231dd`.

### Prompt 8 — Sequence Diagram PMB dengan `UserFactory`, Asinkron & Alternate Flow
> **Prompt:**  
> "Buatkan Sequence Diagram Mermaid untuk skenario Pendaftaran Mahasiswa Baru (PMB) di SEVIMA-Mini menggunakan nama participant yang persis sama dengan Class Diagram kita: `CalonMahasiswa`, `Admin`, `PMBService`, `UserFactory`, `PMBRepository`, `NotificationService`, dan `NotificationChannel`. Bagi menjadi 2 tahap: (1) Pendaftaran online + validasi input, dan (2) Verifikasi berkas oleh Admin lalu pembuatan objek `Mahasiswa` beserta NIM melalui `UserFactory`. Gunakan pesan asinkron (`-)`) ke `NotificationService` agar Admin tidak menunggu email terkirim. Wajib tambahkan fragmen `alt` untuk: formulir tidak valid/duplikat, berkas tidak lengkap/ditolak, dan pengiriman email gagal (log error + jadwalkan retry)."
- **Alasan Dipilih:** Memperbaiki Draft v1 (`65b54c6`) yang menumpuk seluruh proses ke satu participant `SistemAkademik` (*God Object*), tidak memakai `UserFactory`, dan hanya menggambarkan *happy path*.

### Prompt 9 — Sequence Diagram Validasi KRS (`loop`, Self-Call, `alt` 4 Cabang & Collect-All-Errors)
> **Prompt:**  
> "Buatkan Sequence Diagram Mermaid untuk skenario Validasi & Submit KRS di SEVIMA-Mini dengan participant yang sinkron dengan Class Diagram: `Mahasiswa`, `KRSManager`, `KRSValidator`, `AcademicRepository`, `NotificationService`, dan `Dosen PA`. Langkah: (1) `Mahasiswa` memanggil `submitKRS()`, (2) `KRSManager` mendelegasikan ke `KRSValidator`, (3) gunakan fragmen `loop` untuk memeriksa prasyarat setiap mata kuliah melalui `AcademicRepository`, (4) gunakan self-call untuk menghitung total SKS (maksimal 24) dan memeriksa jadwal bentrok, (5) tambahkan fragmen `alt` dengan 4 cabang (prasyarat belum lulus, total SKS > 24, jadwal bentrok, dan semua lolos -> simpan `PENDING_APPROVAL` lalu kirim notifikasi asinkron ke Dosen PA). Pastikan pesan error spesifik & actionable serta validator mengumpulkan semua kesalahan sekaligus."
- **Alasan Dipilih:** Menjawab langsung aturan larangan dokumen tugas (*"Sequence diagram tanpa alternate flow wajib dihindari"*) dan menambahkan pesan error yang dapat ditindaklanjuti mahasiswa. Commit final: `7f80af8`.

### Prompt 10 — Self-Critique AI (5 Kelemahan Terbesar Diurutkan Berdasarkan Risiko)
> **Prompt:**  
> "Bertindaklah sebagai reviewer arsitektur senior yang kritis. Audit seluruh diagram UML SEVIMA-Mini saat ini (`class-diagram.mmd`, `use-case-diagram.mmd`, `sequence-pmb.mmd`, `sequence-krs.mmd`). Identifikasi **5 kelemahan terbesar** diurutkan dari yang paling berisiko terhadap implementasi Minggu 3–8, khususnya terkait sinkronisasi nama kelas antar diagram, potensi God Object, dan pelanggaran prinsip SOLID."
- **Temuan Kritis & Keputusan Manual:**
  1. **Tidak sinkron antar diagram (Risiko Tertinggi):** `PMBService`, `UserFactory`, `PMBRepository`, `AcademicRepository`, dan `NotificationChannel` muncul di Sequence Diagram tetapi belum ada di `class-diagram.mmd` → **Diterima & segera disinkronkan.**
  2. **Belum ada ISP (Interface Segregation):** `Mahasiswa` dan `Dosen` belum dibatasi interface peran yang terpisah → **Diterima; dibuat `PengisiKRS`, `PenyetujuKRS`, `Penilai`.**
  3. **DIP pada Notifikasi belum eksplisit:** `NotificationService` belum bergantung pada abstraksi `NotificationChannel` → **Diterima; ditambah interface `NotificationChannel` (`EmailChannel`, `WhatsAppChannel`).**
  4. **OCP Validasi KRS belum menunjukkan strategi aturan konkret:** Ditambah interface `ValidationRule` dengan `PrasyaratRule`, `BatasSKSRule`, `JadwalBentrokRule` sehingga aturan masa depan (`CekTunggakanRule`) tinggal ditambah tanpa ubah `KRSValidator`.
  5. **Observer Pattern belum eksplisit di Class Diagram:** Ditambah `NotificationSubscriber` & `AuditLogSubscriber` yang berlangganan event pada `NotificationService`.

### Prompt 11 — Audit Lengkap 5 Prinsip SOLID + Factory & Observer pada Class Diagram
> **Prompt:**  
> "Refactor `diagrams/class-diagram.mmd` SEVIMA-Mini agar 100% sinkron dengan `sequence-pmb.mmd` dan `sequence-krs.mmd` serta mematuhi kelima prinsip SOLID:
> - **SRP:** Pisah layanan ke 7 service modular (`PMBService`, `KRSManager`+`KRSValidator`, `JadwalService`, `TranscriptService`, `KeuanganService`, `PresensiService`, `NotificationService`), maksimal 6 method per kelas.
> - **OCP:** Gunakan `ValidationRule <<interface>>` (*Strategy*) pada `KRSValidator`.
> - **LSP:** Pastikan `getIPK()` tetap hanya di `Mahasiswa` (bukan di `User`).
> - **ISP:** Tambahkan interface peran `PengisiKRS`, `PenyetujuKRS`, `Penilai`.
> - **DIP:** Tambahkan `NotificationChannel <<interface>>`, `PMBRepository <<interface>>`, `AcademicRepository <<interface>>`.
> - **Patterns:** Tambahkan `UserFactory` (Factory Pattern) dan `NotificationSubscriber` + `AuditLogSubscriber` (Observer Pattern).
> Pastikan tidak ada *circular dependency*. Output kode Mermaid `classDiagram` valid."
- **Hasil & Validasi Manual:** Menghasilkan `diagrams/class-diagram.mmd` final (commit `b99fc1c`) yang 100% sinkron dengan semua sequence diagram, lolos render Mermaid v11 tanpa error, dan bebas *circular dependency*.

### Prompt 12 — Penyusunan ADR Minggu 2 (Template 5 Bagian: Konteks, Keputusan, Alternatif & Trade-off, Konsekuensi, Pattern)
> **Prompt:**  
> "Bantu susun pembaruan Architecture Decision Record (ADR) SEVIMA-Mini untuk Minggu 2 dengan tepat 5 bagian per keputusan: (1) Konteks, (2) Keputusan, (3) Alternatif & Trade-off, (4) Konsekuensi positif & negatif, dan (5) Pattern yang diterapkan. Cakup keputusan: pemecahan God Object menjadi 7 service modular (vs tetap 1 kelas vs langsung microservice), penggunaan `UserFactory` untuk PMB & NIM, penggunaan Observer Pattern + DIP `NotificationChannel` untuk notifikasi asinkron, dan strategi `ValidationRule` (OCP) dengan mekanisme *collect-all-errors* pada KRS."
- **Hasil:** Disusun dan diekspor ke [docs/ADR-architecture-decisions.pdf](../docs/ADR-architecture-decisions.pdf).

---

## 5. Perbandingan Hasil AI Awal vs Revisi Manual — Minggu 2 (Before–After)

| No | Diagram / Aspek | Hasil AI Awal (Draft v1, `65b54c6`) | Revisi Manual (Final Minggu 2) | Bukti Visual (Before → After) | Alasan Revisi Diperlukan |
|---|---|---|---|---|---|
| 1 | **Use Case — Aktor & Boundary** | Hanya 3 aktor (`Mahasiswa`, `Dosen`, `Admin`); `Sistem PMB` dan peran `Dosen PA` vs `Dosen Pengampu` hilang | 6 aktor berbasis peran di luar boundary: `Calon Mahasiswa`, `Sistem PMB` (`«system»`), `Admin Akademik`, `Mahasiswa`, `Dosen PA`, `Dosen Pengampu` | [before-use-case-draft.png](../diagrams/screenshots/before-use-case-draft.png) → [after-use-case-final.png](../diagrams/screenshots/after-use-case-final.png) | Aktor merepresentasikan **peran** (termasuk sistem eksternal `Sistem PMB`), bukan sekadar nama orang; tugas mewajibkan alur PMB. |
| 2 | **Use Case — Arah `<<extend>>` & Detail UI** | Panah `Approve KRS --<<extend>>--> Ajukan Revisi KRS` **terbalik**; terdapat `"Klik Tombol Simpan KRS"` | Membalik panah menjadi `Ajukan Revisi KRS --<<extend>>--> Approve KRS` & `Bayar Denda --<<extend>>--> Bayar SPP`; menghapus `"Klik Tombol Simpan"` | [before-use-case-draft.png](../diagrams/screenshots/before-use-case-draft.png) → [after-use-case-final.png](../diagrams/screenshots/after-use-case-final.png) | Pada UML, `<<extend>>` selalu mengarah dari **use case tambahan → use case dasar**, dan tombol simpan adalah detail UI, bukan tujuan bisnis. |
| 3 | **Sequence PMB — God Object, Factory & Alt Flow** | Semua logika di satu participant `SistemAkademik`; email sinkron; tanpa `UserFactory`; hanya *happy path* | Dipecah menjadi `PMBService`, `UserFactory` (`generateNIM`), `PMBRepository`, `NotificationService` (asinkron `-)`), `NotificationChannel`, + `alt` formulir invalid, berkas tidak lengkap, & email gagal | [before-sequence-pmb-draft.png](../diagrams/screenshots/before-sequence-pmb-draft.png) → [after-sequence-pmb-final.png](../diagrams/screenshots/after-sequence-pmb-final.png) | Menghilangkan *God Object*, memusatkan pembuatan `Mahasiswa` + NIM via *Factory*, menjaga respons Admin tetap cepat (asinkron), dan menangani kegagalan nyata (*alternate flow*). |
| 4 | **Sequence KRS — Loop, Alt 4 Cabang & Actionable Error** | Satu participant `SistemAkademik`; tanpa `loop`; tanpa `alt` kegagalan validasi; notifikasi Dosen PA sinkron | Dipecah ke `KRSManager`, `KRSValidator`, `AcademicRepository`, `NotificationService`; tambah `loop` per MK, *self-call* SKS & bentrok, `alt` 4 cabang + pesan error spesifik (*collect-all-errors*), dan notifikasi asinkron | [before-sequence-krs-draft.png](../diagrams/screenshots/before-sequence-krs-draft.png) → [after-sequence-krs-final.png](../diagrams/screenshots/after-sequence-krs-final.png) | Sequence diagram tanpa *alternate flow* melanggar rubrik; mengumpulkan semua kesalahan sekaligus mencegah mahasiswa bolak-balik submit di malam terakhir KRS. |
| 5 | **Class Diagram — Sinkronisasi, ISP, DIP, Factory & Observer** | Versi Minggu 1 belum memiliki `PMBService`, `UserFactory`, `PMBRepository`, `AcademicRepository`, `ValidationRule`, interface ISP, `NotificationChannel` (DIP), & `NotificationSubscriber` (Observer) | Melengkapi 7 service SRP, interface ISP (`PengisiKRS`, `PenyetujuKRS`, `Penilai`), Strategy OCP (`ValidationRule`), `UserFactory`, Observer (`NotificationSubscriber`), dan DIP (`NotificationChannel`) tanpa *circular dependency* | [before-class-god-object.png](../diagrams/screenshots/before-class-god-object.png) → [after-class-solid-final.png](../diagrams/screenshots/after-class-solid-final.png) | Memastikan ketiga jenis diagram 100% sinkron (*traceable*) dan seluruh prinsip S-O-L-I-D + 2 design pattern wajib terbukti secara struktural sebelum implementasi. |

---

## 6. AI Usage Log Mingguan (Sesuai AI-First Development Charter)

| Minggu | Fitur / Artefak | Tool | Prompt Kunci | Commit Output AI | Revisi Manual & Commit Final |
|---|---|---|---|---|---|
| Minggu 1 | Class Diagram Awal (`class-diagram.mmd`) | Gemini / Antigravity | Prompt 1–5 (domain, visibility, kardinalitas, relasi UML, SRP) | `cabcd25` | Tambah `Payment`, perbaiki `Grade`→`Enrollment`, visibility & kardinalitas, pecah God Object (`de11f67`, `8a74d58`, `1edd28b`) |
| Minggu 2 | Use Case Diagram (`use-case-diagram.mmd`) | Gemini / Antigravity + Mermaid v11 | Prompt 7 (aktor peran + `Sistem PMB`, `<<include>>` & `<<extend>>` + alasan) | `65b54c6` | Hapus detail UI `"Klik Tombol Simpan"`, tambah aktor `Sistem PMB` & peran Dosen, balik arah panah `<<extend>>` (`59231dd`) |
| Minggu 2 | Sequence Diagram PMB & KRS (`sequence-pmb.mmd`, `sequence-krs.mmd`) | Gemini / Antigravity + Mermaid v11 | Prompt 8–9 (participant sinkron, `UserFactory`, `loop`, `alt` 4 cabang, async) | `65b54c6` | Pecah `SistemAkademik`, tambah `loop` per MK, `alt` 4 cabang KRS & `alt` berkas/email gagal PMB, ubah notifikasi jadi asinkron (`7f80af8`) |
| Minggu 2 | Sinkronisasi Class Diagram + SOLID & Patterns | Gemini / Antigravity + Mermaid v11 | Prompt 10–11 (*self-critique* 5 kelemahan terbesar & audit S-O-L-I-D) | `b99fc1c` | Tambah 7 service modular, `UserFactory`, `ValidationRule`, interface ISP, DIP `NotificationChannel`, & Observer `NotificationSubscriber` (`b99fc1c`) |
| Minggu 2 | ADR PDF, Final UML PDF & Dokumentasi | Gemini / Antigravity + Chrome Headless | Prompt 12 (ADR 5 bagian: Konteks, Keputusan, Alternatif & Trade-off, Konsekuensi, Pattern) | Commit dokumentasi Minggu 2 | Verifikasi visual seluruh render PNG/PDF, lengkapi screenshot before–after, OPAR, README, dan panduan video Viva |

---

## 7. Catatan OPAR — Minggu 2

- **Observe:** Output pertama AI pada Use Case dan Sequence Diagram mengulangi 4 kesalahan klasik: membalik arah panah `<<extend>>`, memasukkan interaksi antarmuka (`"Klik Tombol Simpan"`) sebagai use case, membungkus seluruh logika ke satu participant `SistemAkademik` (*God Object*), serta hanya menampilkan *happy path* tanpa *alternate flow* dan dengan notifikasi sinkron. Selain itu, muncul ketidaksinkronan antara participant Sequence Diagram dengan kelas di Class Diagram Minggu 1.
- **Plan:** Melakukan iterasi bertahap: (1) perbaiki Use Case dengan uji *include vs extend* dan pemisahan peran aktor, (2) pecah Sequence PMB & KRS ke participant SOLID dengan `loop`, *self-call*, `alt` error handling, dan pesan asinkron, (3) minta AI mengkritik hasilnya sendiri (*5 kelemahan terbesar*), (4) sinkronkan `class-diagram.mmd` dengan seluruh prinsip S-O-L-I-D + *Factory* & *Observer*, dan (5) rekam bukti *before–after*, ADR 5 bagian, serta commit bertahap.
- **Act:** Menghasilkan dan memverifikasi render Mermaid v11 untuk 4 diagram final (`class-diagram.mmd`, `use-case-diagram.mmd`, `sequence-pmb.mmd`, `sequence-krs.mmd`), 8 screenshot *before–after* di `diagrams/screenshots/`, ADR PDF lengkap 5 bagian (`docs/ADR-architecture-decisions.pdf`), kompilasi `docs/final-uml-diagrams.pdf`, serta riwayat commit bertahap dengan label `[AI-assisted]`.
- **Reflect:** Meminta AI mengkritik diagram buatannya sendiri (*self-critique*) terbukti jauh lebih efektif daripada meminta diagram jadi dalam sekali prompt. Sinkronisasi nama kelas antar Class Diagram dan Sequence Diagram membuat desain langsung siap diterjemahkan ke kode PMB (Minggu 3–4) dan KRS (Minggu 5–6) tanpa perombakan arsitektur.
