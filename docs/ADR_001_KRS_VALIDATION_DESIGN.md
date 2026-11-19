# ADR-001: Desain Rantai Validasi KRS, Aturan Jadwal, dan Query Prasyarat

- **Status:** Diterima
- **Tanggal:** 19 November 2026
- **Modul:** Modul KRS (Minggu 5 — Bagian 1)

---

## 1. Perilaku Rantai Validasi: Fail-Fast vs Collect-All

### Konteks
Dokumen tugas meminta rantai validasi (*Chain of Responsibility*) berhenti saat menemukan kegagalan pertama (*fail-fast*). Di sisi lain, untuk pemeriksaan KRS secara utuh sebelum diajukan ke dosen PA (`validate_krs`), mahasiswa akan lebih terbantu jika seluruh kesalahan tampil sekaligus dalam satu respons.

### Alternatif
1. **Selalu fail-fast** di semua method: sederhana, tetapi memaksa mahasiswa memperbaiki kesalahan satu per satu saat mengecek seluruh KRS.
2. **Selalu collect-all** di semua method: ramah di pemeriksaan menyeluruh, tetapi boros query prasyarat saat menambah satu mata kuliah yang sudah jelas duplikat.
3. **Hybrid (dipilih):**
   - `KRSManager.add_course()` menjalankan rantai validator **fail-fast** sesuai spesifikasi dokumen tugas.
   - `KRSManager.validate_krs()` memeriksa seluruh KRS secara menyeluruh dan **mengumpulkan semua error sekaligus**.

### Konsekuensi
- Penambahan satu mata kuliah (`add_course`) cepat karena pemeriksaan mahal seperti query prasyarat tidak dijalankan jika duplikasi atau batas SKS sudah gagal.
- Pemeriksaan menyeluruh (`validate_krs`) tetap ergonomis bagi mahasiswa karena menampilkan seluruh masalah dalam satu panggilan API.

---

## 2. Urutan Validator di Rantai Default

### Keputusan
Urutan default di `build_default_validators()`:
1. `DuplicateValidator`
2. `SKSValidator` (`MAX_SKS = 24`, tepat 24 SKS diizinkan)
3. `PrerequisiteValidator`
4. `ConflictValidator`

### Alasan
- Jika mahasiswa tidak sengaja menambah mata kuliah yang sudah ada di KRS, pesan yang tepat adalah *"sudah ada di KRS"*, bukan *"Total SKS melebihi batas"*.
- `DuplicateValidator` dan `SKSValidator` bekerja di memori tanpa query database tambahan, sehingga diletakkan sebelum `PrerequisiteValidator`.

---

## 3. Deteksi Bentrok Jadwal (Irisan Interval)

### Keputusan
Dua jadwal dianggap bentrok jika dan hanya jika:
```python
a.hari == b.hari and a.jam_mulai < b.jam_selesai and b.jam_mulai < a.jam_selesai
```

### Konsekuensi
- Menangkap bentrok sebagian (mis. 08:00–10:30 vs 09:00–11:00), jadwal bersarang (08:00–12:00 vs 09:00–10:00), dan jadwal identik.
- Jadwal **bersambung tepat di batas** (selesai 11:30 lalu mulai 11:30) **tidak dianggap bentrok** karena memakai `<` bukan `<=`.

---

## 4. Perbandingan Nilai Prasyarat Berbasis Bobot Angka & Bukti Indeks (`EXPLAIN`)

### Keputusan
- Nilai huruf dipetakan ke bobot numerik (`SKALA_NILAI`: `A=4.0` hingga `C=2.0`, `D=1.0`, `E=0.0`) dan diterjemahkan ke daftar nilai lulus eksplisit `('A', 'A-', 'B+', 'B', 'B-', 'C+', 'C')` alih-alih perbandingan string `nilai >= 'C'` (yang secara alfabet keliru karena `'A' < 'C'` dan `'D' > 'C'`).
- Query prasyarat menggunakan pola `NOT EXISTS` dengan filter `krs.semester < semester_aktif` (format 5 karakter seperti `"20251"` sehingga urutan leksikografis identik dengan urutan kronologis).

### Bukti `EXPLAIN QUERY PLAN` (SQLite)
Query SQL yang dihasilkan:
```sql
SELECT matakuliah.kode_mk, matakuliah.nama, matakuliah.sks, matakuliah.hari, matakuliah.jam_mulai, matakuliah.jam_selesai
FROM matakuliah
JOIN prerequisite ON matakuliah.kode_mk = prerequisite.kode_prasyarat
WHERE prerequisite.kode_mk = 'IF201'
  AND NOT EXISTS (
    SELECT krs_detail.id
    FROM krs_detail
    JOIN krs ON krs.id = krs_detail.krs_id
    WHERE krs.nim = '20260010001'
      AND krs.semester < '20251'
      AND krs_detail.kode_mk = prerequisite.kode_prasyarat
      AND krs_detail.nilai IN ('A', 'A-', 'B+', 'B', 'B-', 'C+', 'C')
  )
ORDER BY matakuliah.kode_mk;
```

Hasil `EXPLAIN QUERY PLAN`:
```text
(5, 0, 56, 'SEARCH prerequisite USING COVERING INDEX sqlite_autoindex_prerequisite_1 (kode_mk=?)')
(10, 0, 0, 'CORRELATED SCALAR SUBQUERY 1')
(16, 10, 47, 'SEARCH krs USING COVERING INDEX sqlite_autoindex_krs_1 (nim=? AND semester<?)')
(21, 10, 47, 'SEARCH krs_detail USING INDEX sqlite_autoindex_krs_detail_1 (krs_id=? AND kode_mk=?)')
(62, 0, 46, 'SEARCH matakuliah USING INDEX sqlite_autoindex_matakuliah_1 (kode_mk=?)')
(76, 0, 0, 'USE TEMP B-TREE FOR ORDER BY')
```

Seluruh pencarian tabel menggunakan `SEARCH ... USING INDEX / COVERING INDEX` (tanpa full table `SCAN`) berkat indeks otomatis dari:
- Primary key gabungan `prerequisite(kode_mk, kode_prasyarat)`
- Unique constraint `krs(nim, semester)` (kolom kesamaan `nim` di depan, kolom rentang `semester` di belakang)
- Unique constraint `krs_detail(krs_id, kode_mk)`
- Primary key `matakuliah(kode_mk)`

---

## 5. Konsistensi Transaksi & Limitasi Pengujian Lokal

- `KRSManager.add_course()` dan `remove_course()` mengunci baris `krs` dengan `with_for_update()` dan menjalankan validasi serta penulisan `krs_detail` dalam satu transaksi tunggal (commit sekali di akhir), ditambah jaring pengaman `UniqueConstraint("krs_id", "kode_mk")` untuk mencegah race condition SKS > 24 dan double enrollment.
- **Limitasi:** SQLite mengabaikan klausa `FOR UPDATE`, sehingga uji penguncian konkuren di bawah beban tetap perlu diverifikasi pada MySQL/PostgreSQL.
