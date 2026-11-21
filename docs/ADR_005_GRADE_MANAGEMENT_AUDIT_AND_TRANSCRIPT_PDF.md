# ADR-005: Modul Nilai, Audit Trail Berbasis Database Trigger, Auto-IPK, dan Generator Transkrip PDF (Minggu 9–10)

## Status
Accepted — 2026-11-20

## Konteks
Modul Nilai & Transkrip (Minggu 9–10) menambahkan kemampuan pengelolaan nilai akhir per mata kuliah, perhitungan IPS/IPK otomatis, pelacakan riwayat perubahan nilai (`grade_history`), dan pembuatan Transkrip Akademik resmi berformat PDF yang terintegrasi dengan modul PMB (`calon_mahasiswa`, `program_studi`) dan KRS (`krs`, `krs_detail`, `matakuliah`).

## Keputusan Desain

### 1. Skala Nilai dan Resolusi Batas Lulus (`>= D` vs Prasyarat KRS `>= C`)
- **Permasalahan:** Pada soal PDF, bagian *Grade Management* memakai konversi `A=4.0, B=3.0, dst` (di mana `C=2.0, D=1.0, E=0.0`), sedangkan *Business Rules* menuliskan *"Lulus MK jika nilai >= D (2.0)"* dan *Step 1* menegaskan *"Hanya hitung MK yang sudah lulus (nilai >= D)"*.
- **Keputusan:**
  - `GRADE_SCALE` mendukung penuh huruf standar `A=4.0, B=3.0, C=2.0, D=1.0, E=0.0` serta plus/minus (`A-=3.7, B+=3.3, B-=2.7, C+=2.3`) agar seragam dengan `SKALA_NILAI` di `app/services/krs_validators.py`.
  - Batas lulus untuk **Transkrip & perhitungan IPK** ditetapkan **`nilai_angka >= 1.0` (`>= D`)**, sehingga hanya nilai `E` (`0.0`) yang dikecualikan dari IPK.
  - Batas lulus **prasyarat KRS** di `app/services/krs_validators.py` tetap dipertahankan `>= C (2.0)` agar seluruh kontrak dan 138 tes Minggu 3–8 tetap lulus tanpa regresi.

### 2. Audit Trail Menggunakan Database Trigger (`trg_grades_after_update`)
- **Keputusan:** Setiap perubahan nilai pada `grades` dicatat otomatis ke `grade_history` (`id, grade_id, old_value, new_value, changed_by, changed_at, reason`) oleh **SQLite trigger `AFTER UPDATE OF nilai_huruf ON grades WHEN OLD.nilai_huruf != NEW.nilai_huruf`**.
- **Alasan:** Menjamin audit trail tidak dapat dilewati (*tamper-resistant*) bahkan apabila pembaruan baris `grades` terjadi langsung lewat SQL di luar `GradeService`. Trigger dipasang baik di migrasi Alembic (`f9c5d2e1847b`) maupun lewat SQLAlchemy `DDL` event listener `after_create` agar otomatis aktif di database uji in-memory.

### 3. Perhitungan IPK, MK Diulang, dan Auto-Update Ringkasan Atomik
- **Keputusan:**
  - `calculate_ipk(db, nim)` menyaring MK lulus (`>= D`), mengelompokkan berdasarkan `kode_mk` lintas semester dengan memilih **nilai tertinggi**, dan mengembalikan `0.0` untuk mahasiswa semester awal yang belum memiliki nilai lulus.
  - `calculate_ips(db, nim, semester)` menghitung seluruh MK yang dinilai pada semester tersebut (`Σ(SKS × Nilai) / Σ(SKS)`), atau `0.0` jika semester kosong.
  - Setiap `input_grade()` dan `update_grade()` sekaligus menyinkronkan `krs_detail.nilai` (jika KRS ada) dan memperbarui tabel `student_academic_summary` (`ipk`, `total_sks_lulus`, `predikat`) dalam **satu transaksi atomik**.

### 4. Dual Rendering Transkrip: Template HTML/CSS (Jinja2) + PDF Resmi (ReportLab)
- **Keputusan:**
  - Template HTML dinamis (`app/templates/transcript.html`) dirender melalui **Jinja2** dengan CSS bergaya ijazah resmi (border ganda navy & emas, logo vektor SVG, tabel per semester, ringkasan, dan blok TTD Dekan) pada `GET /api/grades/{nim}/transcript/html`.
  - File PDF unduhan (`GET /api/grades/{nim}/transcript/pdf`) dihasilkan oleh **ReportLab** (`app/services/transcript_pdf.py`) yang menggambar layout resmi A4 (bingkai ganda, emblem logo universitas, judul `TRANSKRIP AKADEMIK`, biodata, tabel per semester, ringkasan SKS/IPK/Predikat, dan TTD Dekan).
- **Alasan:** ReportLab 100% pure Python tanpa ketergantungan native DLL GTK/Pango/Cairo seperti WeasyPrint di Windows, sehingga andal di semua lingkungan praktikum mahasiswa sekaligus tetap menghadirkan template HTML+CSS profesional.

## Konsekuensi
- Penambahan dua dependensi ringan di `requirements.txt`: `reportlab` dan `jinja2`.
- Migrasi Alembic `f9c5d2e1847b` menambah 3 tabel (`grades`, `grade_history`, `student_academic_summary`) dan 1 trigger (`trg_grades_after_update`) yang dapat di-rollback penuh lewat `alembic downgrade`.
