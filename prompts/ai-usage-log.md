# AI Usage Documentation — Minggu 1 (Desain UML Class Diagram SEVIMA-Mini)

- **Mata Kuliah:** Pemrograman Berorientasi Objek (AI-First Approach)
- **Materi:** Materi 1 — Minggu 1 (Eksplorasi Domain, Prompt Engineering & Validasi Class Diagram)
- **Project:** SEVIMA-Mini (Sistem Informasi Akademik)
- **AI Tools:** Gemini / Antigravity Assistant + Mermaid Live Editor (validasi render)

---

## 1. Enam Iterasi Prompt Terbaik (5 Unsur: Role, Context, Component, Constraint, Format)

### Prompt 1 — Eksplorasi Domain (Analisis Kata Benda & Kata Kerja)
> **Prompt:**  
> "Anda adalah senior software architect. Bantu saya melakukan analisis domain untuk sistem akademik kampus kecil (SEVIMA-Mini) yang mencakup modul PMB, KRS (maksimal 24 SKS & cek prasyarat), Jadwal, Nilai/Transkrip, dan Pembayaran SPP. Identifikasi kandidat class utama (kata benda yang punya data & perilaku), atribut kunci, dan method utama (kata kerja). Pisahkan atribut sederhana seperti NIM agar tidak dijadikan class sendiri. Output dalam bentuk tabel ringkas."
- **Tujuan:** Menentukan kandidat class sebelum membuat diagram agar AI tidak menebak entitas di luar requirement (seperti `Kantin` atau `Gedung`).
- **Hasil & Evaluasi:** Menghasilkan hierarki `User` (4 turunan) serta entitas inti `Course`, `Enrollment`, `Schedule`, `Grade`, `Payment`, dan `ProgramStudi`.

### Prompt 2 — Draft Awal Class Diagram (Titik Awal Lembar Tugas)
> **Prompt:**  
> "Anda adalah arsitek sistem. Buat class diagram UML untuk sistem akademik dengan komponen berikut: User sebagai base class (Mahasiswa, Dosen, Admin, CalonMahasiswa), Course (kode, nama, SKS, prasyarat), Enrollment yang menghubungkan Mahasiswa dan Course, Schedule (hari, jam, ruangan), dan Grade untuk nilai mahasiswa. Terapkan prinsip SOLID. Gunakan composition untuk relasi kuat dan aggregation untuk relasi lemah. Output dalam format Mermaid."
- **Tujuan:** Membangun draft v1 (`diagrams/class-diagram.mmd` commit `cabcd25`) sebagai pembanding.
- **Temuan Kritis:** Sesuai catatan dosen, draft v1 masih memiliki 5 kelemahan: (1) class `Payment` belum ada, (2) visibility modifier (`+`, `-`, `#`) belum tercantum, (3) kardinalitas (`1`, `0..*`) tidak ditulis, (4) `Grade` terhubung langsung ke `Mahasiswa` alih-alih `Enrollment`, dan (5) `Mahasiswa` menjadi *God Object* (mengurus validasi KRS, cetak PDF, sekaligus kirim email).

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

### Prompt 5 — Audit SOLID & Refactor *God Object* (SRP + DIP)
> **Prompt:**  
> "Audit class diagram SEVIMA-Mini berikut terhadap prinsip SOLID, khususnya Single Responsibility Principle (SRP) dan Dependency Inversion Principle (DIP). Identifikasi method di class `Mahasiswa` yang melanggar SRP (validasi KRS, cetak transkrip, kirim notifikasi), lalu pisahkan ke class service yang fokus (`KRSManager`, interface `KRSValidator`, `TranscriptService`, `NotificationService`). Pastikan tidak ada class dengan lebih dari 10 method dan tidak ada *circular dependency*."
- **Tujuan:** Menghilangkan *God Object* dan memastikan diagram siap menjadi cetak biru implementasi Minggu 3–16 (termasuk pola *Chain of Responsibility* / *Strategy* pada `KRSValidator`).

### Prompt 6 — Review Akhir & Penyusunan ADR
> **Prompt:**  
> "Berdasarkan class diagram final SEVIMA-Mini, bantu susun kerangka Architecture Decision Record (ADR) yang menjelaskan secara ringkas alasan dan trade-off untuk 5 keputusan desain: (1) pemisahan `KRS` dan `Enrollment`, (2) relasi `Grade` ke `Enrollment` bukan `Mahasiswa`, (3) konversi objek `CalonMahasiswa` ke `Mahasiswa`, (4) pemecahan *God Object* ke lapisan service, dan (5) penggunaan design pattern (Chain of Responsibility, Factory/Generator, Observer)."
- **Tujuan:** Menyusun dokumentasi keputusan arsitektur secara terstruktur untuk laporan `docs/ADR-architecture-decisions.pdf`.

---

## 2. Perbandingan Hasil AI Awal vs Revisi Manual (Before–After)

| No | Aspek Desain | Hasil AI Awal (Draft v1) | Revisi Manual (Final) | Alasan Revisi Diperlukan |
|---|---|---|---|---|
| 1 | **Kelengkapan Class Inti** | Hanya membuat 4 class inti (`Course`, `Enrollment`, `Schedule`, `Grade`); **`Payment` hilang** | Menambahkan class `Payment`, `ProgramStudi`, dan `KRS` | `Payment` wajib ada di deliverable (35%) karena termasuk 5 class inti sistem akademik (modul SPP). |
| 2 | **Encapsulation & Visibility** | Semua atribut tanpa modifier (terbaca publik), termasuk `password` dan `ipk` | Memakai `-passwordHash`, `-ipk`, `#id`, `#username`, `#email`, serta method `+` | Menjaga enkapsulasi: `password` tidak boleh terbaca kelas lain dan `ipk` hanya boleh berubah lewat perhitungan nilai yang sah. |
| 3 | **Relasi `Grade`** | `Mahasiswa --> Grade` (langsung ke mahasiswa) | `Enrollment "1" *-- "0..1" Grade` (**composition**) | Nilai selalu melekat pada satu mahasiswa di satu mata kuliah dan satu semester tertentu; jika `Enrollment` dihapus, `Grade` tidak lagi bermakna. |
| 4 | **Struktur KRS vs Enrollment** | Hanya satu tabel `Enrollment` menyimpan status KRS | Memecah menjadi `KRS "1" *-- "1..*" Enrollment` | Yang disetujui Dosen PA adalah satu kartu KRS utuh per semester, bukan setengah *approved* dan setengah *draft* per mata kuliah. |
| 5 | **Composition vs Aggregation** | Semua relasi memakai garis asosiasi biasa `-->` tanpa kardinalitas | Membedakan `*--` (`Course*--Schedule`, `KRS*--Enrollment`, `Enrollment*--Grade`), `o--` (`ProgramStudi o-- Dosen/Mahasiswa`), dan self-association `Course --> Course` | Menunjukkan siklus hidup objek secara akurat dan mencegah kesalahan kardinalitas many-to-many. |
| 6 | **SOLID / Anti God Object** | `Mahasiswa` memiliki `validasiKRS()`, `cetakTranskripPDF()`, `kirimEmailTagihan()` | Dipecah menjadi `KRSManager`, `KRSValidator` (`<<interface>>`), `TranscriptService`, dan `NotificationService` | Memenuhi SRP & OCP: perubahan format transkrip atau aturan SKS tidak memaksa perubahan class `Mahasiswa`. |

---

## 3. Catatan OPAR — Minggu 1

- **Observe:** Draft awal class diagram yang dihasilkan AI dari prompt standar belum menyertakan `Payment`, tidak menuliskan visibility modifier dan kardinalitas, menempatkan `Grade` langsung ke `Mahasiswa`, serta menumpuk terlalu banyak tanggung jawab pada class `Mahasiswa` (*God Object*).
- **Plan:** Mengiterasikan prompt secara terstruktur dengan 5 unsur (Role, Context, Component, Constraint, Format), memvalidasi setiap relasi menggunakan uji *is-a*, uji siklus hidup (*composition vs aggregation*), pengecekan *circular dependency*, serta menerapkan SRP/DIP.
- **Act:** Menyusun `diagrams/class-diagram.mmd` secara bertahap (commit draft v1 → commit refactor SOLID final), mendokumentasikan 6 iterasi prompt dan tabel before–after di `prompts/ai-usage-log.md`, serta menyusun `docs/ADR-architecture-decisions.pdf`.
- **Reflect:** Diagram berbasis teks (Mermaid) memudahkan pelacakan diff per commit di GitHub dan langsung selaras dengan implementasi kode pada minggu berikutnya. Validasi kritis terhadap output AI terbukti krusial karena AI terlihat meyakinkan meski melewatkan entitas wajib (`Payment`) dan melanggar enkapsulasi.
