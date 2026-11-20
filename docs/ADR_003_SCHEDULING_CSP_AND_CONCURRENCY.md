# ADR-003: Desain Modul Jadwal & Ruangan — CSP Hard Constraints, Optimasi Deteksi Bentrok, dan Kontrol Konkurensi

- **Status:** Diterima
- **Tanggal:** 20 November 2026
- **Modul:** Modul Jadwal & Ruangan (Minggu 7 — Bagian 1)

---

## 1. Algoritma Deteksi Bentrok: Grouping + Sort-and-Sweep vs Interval Tree

### Konteks
Dokumen tugas (Step 1) menyebut optimasi *Interval Tree* untuk pemeriksaan irisan jam pada `detect_schedule_conflict`. Sementara itu, skala data kampus per semester berkisar **~600 jadwal** (sekitar 6–8 jadwal per ruangan per hari setelah dikelompokkan berdasarkan hari dan ruangan/dosen).

### Pengukuran Nyata (Benchmark Lokal, $N = 600$ jadwal, $179.700$ pasangan)
| Algoritma | Kompleksitas | Waktu Eksekusi | Jumlah Konflik | Dependensi Tambahan |
|---|---|---:|---:|---|
| Naif (`itertools.combinations`) | $O(n^2)$ | **13,70 ms** | 432 | Tidak ada |
| **Grouping + Sort-and-Sweep (dipilih)** | $O(n \log n + k)$ | **2,57 ms** | 432 | Tidak ada |
| Interval Tree (`intervaltree`) | $O(n \log n + k)$ | *(ditunda)* | — | Pustaka eksternal baru |

### Keputusan
1. Memakai **Grouping (`(hari, ruangan_kode)` dan `(hari, dosen_id)`) + Sort-and-Sweep** sebagai algoritma produksi (`detect_schedule_conflict`), yang sudah **5,3x lebih cepat** daripada versi naif dan selesai dalam **~2,57 ms** untuk 600 jadwal tanpa menambah dependensi eksternal.
2. Mempertahankan versi naif (`detect_schedule_conflict_naive`) sebagai *reference oracle* untuk *differential testing* dengan Hypothesis (memastikan hasil `Counter` kedua versi selalu identik 100%).
3. **Menunda Interval Tree** sampai skala berubah drastis (mis. reservasi ruangan lintas tahun atau *solver* otomatis yang mengevaluasi ribuan kandidat slot per detik pada Minggu 8).

### Konsekuensi
- **Kelebihan:** Nol dependensi tambahan, mudah diaudit, sangat cepat untuk skala kampus, dan kebenarannya dijamin oleh properti *differential testing*.
- **Penamaan konflik:** Tipe `time_overlap` dari prompt awal **tidak dipakai** sebagai konflik karena dua kelas pada jam sama di ruangan dan dosen berbeda adalah kondisi normal. Hanya **2 tipe resmi** yang dilaporkan: `room_conflict` dan `lecturer_conflict`.

---

## 2. Aturan Interval Setengah Terbuka & Tipe Waktu `datetime.time`

### Keputusan
- Setiap jadwal dimodelkan sebagai interval setengah terbuka `[jam_mulai, jam_selesai)`.
- Dua jadwal pada hari yang sama bertumpuk jika dan hanya jika:
  ```python
  a.hari == b.hari and a.jam_mulai < b.jam_selesai and b.jam_mulai < a.jam_selesai
  ```
- Penggunaan operator ketat `<` (dan `>` saat menyapu jadwal aktif) memastikan **kuliah bersambung** (mis. `08:00–09:40` dan `09:40–11:20` pada blok 50 menit/SKS) **tidak dianggap bentrok**.
- Seluruh kolom dan field jam bertipe `Time` (`datetime.time`), bukan `str`, guna mencegah bug perbandingan leksikografis (`"9:00" > "10:00"`).

---

## 3. Capacity Check: `max(kelas.kuota, jumlah_terdaftar) <= ruangan.kapasitas`

### Konteks
Saat jadwal disusun di awal semester, belum ada mahasiswa yang mengisi KRS (`jumlah_terdaftar = 0`). Jika pengecekan hanya membandingkan `jumlah_terdaftar <= ruangan.kapasitas`, kelas berkuota 50 akan lolos masuk ke ruangan berkapasitas 40.

### Keputusan
- Pemeriksaan kapasitas di `SchedulingService.cek_kapasitas()` menggunakan `dibutuhkan = max(kelas.kuota, jumlah_terdaftar)` dan membandingkan **`dibutuhkan <= ruangan.kapasitas`** (operator `<=` sehingga ruangan berkapasitas 40 sah diisi tepat 40 orang).
- Jika melanggar, lempar `RoomCapacityError` (**HTTP 409 Conflict**) dengan pesan lengkap mencantumkan kode ruangan, kapasitas ruangan, kuota, dan jumlah terdaftar.
- Kolom nullable `krs_detail.kelas_id` ditambahkan melalui migrasi Minggu 7 agar penghitungan peserta terdaftar dapat berjalan dan siap diintegrasikan penuh dengan modul KRS pada Minggu 8.

---

## 4. Pemisahan Model Domain & Indeks Komposit

### Keputusan
- Memisahkan entitas `Ruangan` (PK alami `kode`), `Dosen` (`nidn` unik), `Kelas` (kelas paralel per MK & semester), dan `Jadwal` (1 kelas dapat memiliki >1 pertemuan/minggu, mis. teori Senin + praktikum Kamis).
- Menambahkan 2 `CheckConstraint` di database (`jam_mulai < jam_selesai`, `hari BETWEEN 1 AND 6`) dan 2 indeks komposit yang dirancang dari pola query utama:
  - `ix_jadwal_hari_ruangan (hari, ruangan_kode)`
  - `ix_jadwal_hari_dosen (hari, dosen_id)`
- Kolom lama `hari`, `jam_mulai`, `jam_selesai` pada `matakuliah` sementara tetap dipertahankan untuk menjaga kompatibilitas modul KRS Minggu 5–6 hingga integrasi penuh pada Minggu 8.

---

## 5. Kontrol Konkurensi, Urutan Penguncian, & Polimorfisme Error

### Keputusan
- **Urutan `SchedulingService._simpan()`:**
  1. Kunci baris `Ruangan` terlebih dahulu, lalu baris `Dosen` dengan `with_for_update()` (**urutan tetap `Ruangan -> Dosen`** untuk mencegah deadlock saat dua admin membuat jadwal bersamaan).
  2. Cari bentrok langsung di SQL sebelum menambahkan/menyalin perubahan ke session (mencegah SQLAlchemy *autoflush* menemukan objeknya sendiri; pada `update`, `Jadwal.id != jadwal.id` dikecualikan agar geser jam 15 menit tidak dianggap bentrok dengan diri sendiri).
  3. Jalankan `cek_kapasitas()`.
  4. Salin atribut, `db.add()`, lalu `db.commit()` sekali di akhir.
- **Polimorfisme Error (`ScheduleError`):** satu exception handler di `app/main.py` memanggil `exc.body()` sehingga `ScheduleConflictError` (409) otomatis menyertakan daftar `conflicts` terstruktur sementara `ScheduleNotFoundError` (404) dan `RoomCapacityError` (409) mengembalikan `{"detail": ...}`.
- **Konsistensi validasi format:** error validasi Pydantic (`jam_selesai <= jam_mulai`, `hari` di luar 1–6) mengikuti global handler `RequestValidationError -> HTTP 400` yang telah ditetapkan sejak Minggu 3.

### Limitasi Jujur & Risiko
1. **Row-level locking di SQLite:** SQLite mengabaikan klausa `FOR UPDATE`, sehingga pengujian skenario race condition dua admin secara konkuren nyata tetap wajib diverifikasi pada MySQL/PostgreSQL.
2. **Filter semester aktif:** pencarian bentrok saat ini memeriksa seluruh baris pada tabel `jadwal` (belum memfilter berdasarkan `kelas.semester` aktif); pada lingkungan multi-semester historis, query bentrok perlu menyertakan join & filter `Kelas.semester == semester_aktif`.
