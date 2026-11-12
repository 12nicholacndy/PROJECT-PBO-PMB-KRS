# AI Usage Log

Log penggunaan AI untuk membantu pengerjaan project Sistem PMB Minggu 3.

| Tanggal | Bagian | Ringkasan bantuan / instruksi | Pemeriksaan yang dilakukan |
|---|---|---|---|
| 11 November 2026 | Struktur project | Meminta bantuan menyusun struktur folder FastAPI, pemisahan router, service, model, schema, database, dan test. | Memeriksa struktur folder project di VS Code. |
| 11 November 2026 | Database dan ORM | Meminta bantuan menyusun model SQLAlchemy 2.0 untuk tabel `calon_mahasiswa`, `program_studi`, dan `jalur_masuk`, termasuk primary key, foreign key, unique, check constraint, dan nilai default status. | Memeriksa tabel dan data SQLite serta mencocokkan model dengan migrasi. |
| 11 November 2026 | Migrasi database | Meminta bantuan membuat konfigurasi dan revision Alembic untuk membuat serta menghapus tabel PMB. | Menjalankan pemeriksaan status Alembic dan `alembic check`; status revisi yang diperiksa adalah `0001_create_pmb` dan tidak ada operasi migrasi baru yang terdeteksi. |
| 11 November 2026 | API pendaftaran | Meminta bantuan membuat endpoint `POST /api/pmb/register`, validasi request, pemeriksaan email duplikat, pemeriksaan referensi, dan penanganan status HTTP. | Membuka Swagger `/docs` dan mencoba request register. Respons sukses yang tercatat adalah HTTP 201; email yang sudah terdaftar menghasilkan HTTP 409. |
| 11 November 2026 | Pengujian otomatis | Meminta bantuan menyusun test untuk pendaftaran berhasil, email duplikat, nomor HP tidak valid, email tidak valid, dan referensi yang tidak ditemukan. | Menjalankan `python -m pytest -v`; hasil yang tercatat: 6 passed, 1 warning. |
| 12 November 2026 | Pemeriksaan paket ZIP | Meminta bantuan memeriksa isi ZIP, memastikan file aplikasi, migrasi, test, konfigurasi, README, requirements, dan database ikut terkemas. | Mengekstrak ZIP ke folder pemeriksaan, menjalankan 6 test, dan menjalankan aplikasi dari hasil ekstraksi. |

## Catatan penggunaan AI

AI digunakan sebagai alat bantu untuk menyusun dan meninjau kode, dokumentasi, serta langkah pengujian. Hasil bantuan tidak dianggap benar secara otomatis; project diperiksa kembali melalui terminal, Swagger, SQLite, dan pytest. Pemilik project tetap bertanggung jawab memahami kode dan memastikan kesesuaiannya dengan instruksi dosen.

Riwayat Git harus dibuat pada repository project oleh pemilik project. File ZIP ini tidak mengklaim menyertakan riwayat commit Git.
