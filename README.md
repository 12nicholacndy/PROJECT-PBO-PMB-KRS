# Sistem PMB — Implementasi Minggu 3

Project ini mengimplementasikan ruang lingkup Minggu 3 mata kuliah Pemrograman Berorientasi Objek (*dari UML ke kode*):
- 3 tabel database: `calon_mahasiswa`, `program_studi`, dan `jalur_masuk`.
- ORM SQLAlchemy 2.0 dengan `DeclarativeBase`, `Mapped`, dan `mapped_column`.
- Constraint dua lapis: `PRIMARY KEY`, `FOREIGN KEY` (`PRAGMA foreign_keys=ON`), `UNIQUE`, `CHECK` (`status` enum & `length(kode) = 3`), `NOT NULL`, `server_default`, serta validasi Pydantic (termasuk regex nomor HP Indonesia).
- Migrasi database menggunakan Alembic dengan `upgrade` dan `downgrade` (rollback) terverifikasi.
- Endpoint `POST /api/pmb/register` dengan status awal `pending` (respons tidak membocorkan `alamat` dan `tanggal_lahir`).
- Error handling konsisten: HTTP `201` (sukses), `409` (email duplikat), `400` (validasi input / referensi tidak valid — keputusan didokumentasikan di [ADR-001](docs/ADR-001-minggu3-api-dan-database.md)).
- Dokumentasi API otomatis melalui Swagger UI di `/docs`.

## 1. Struktur Project

```text
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 0001_create_pmb_tables.py
├── app/
│   ├── database.py          # Koneksi SQLite, PRAGMA foreign_keys=ON, get_db()
│   ├── main.py              # Aplikasi FastAPI & handler RequestValidationError -> 400
│   ├── models.py            # Model SQLAlchemy 2.0 (CalonMahasiswa, ProgramStudi, JalurMasuk)
│   ├── schemas.py           # Skema Pydantic (RegisterRequest, RegisterResponse)
│   ├── routers/
│   │   └── pmb.py           # Endpoint POST /api/pmb/register
│   └── services/
│       └── pmb_service.py   # Aturan bisnis pendaftaran & penanganan IntegrityError
├── docs/
│   └── ADR-001-minggu3-api-dan-database.md
├── tests/
│   └── test_register.py     # Unit test API & constraint database
├── AI_USAGE_LOG.md
├── alembic.ini
└── requirements.txt
```

## 2. Persiapan (Windows / PowerShell)

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
```

## 3. Migrasi Database (Upgrade & Rollback)

Terapkan skema database:

```powershell
python -m alembic upgrade head
python -m alembic current --verbose
python -m alembic check
```

Untuk menguji rollback (`downgrade`) dan pembuatan ulang (`upgrade`) pada database pengembangan/pengujian:

```powershell
python -m alembic downgrade -1
python -m alembic upgrade head
```

> Catatan: `downgrade -1` menghapus `calon_mahasiswa` lebih dulu sebelum `jalur_masuk` dan `program_studi` agar tidak melanggar foreign key.

## 4. Data Referensi (Seed)

Endpoint register membutuhkan `program_studi_id` dan `jalur_masuk_id` yang sudah tersedia. Jalankan seed satu kali pada database lokal:

```python
from app.database import SessionLocal
from app.models import ProgramStudi, JalurMasuk

db = SessionLocal()
db.add_all([
    ProgramStudi(kode="001", nama="Sistem Informasi", fakultas="Fakultas Ilmu Komputer"),
    ProgramStudi(kode="002", nama="Teknik Informatika", fakultas="Fakultas Ilmu Komputer"),
    JalurMasuk(nama="SNBP"),
    JalurMasuk(nama="SNBT"),
    JalurMasuk(nama="Mandiri"),
])
db.commit()
db.close()
```

## 5. Jalankan API & Uji di Swagger

```powershell
python -m uvicorn app.main:app --reload
```

Buka `http://127.0.0.1:8000/docs`, pilih `POST /api/pmb/register`, dan gunakan data dummy:

```json
{
  "nama_lengkap": "Budi Santoso",
  "email": "budi@example.com",
  "no_hp": "081234567890",
  "tanggal_lahir": "2007-04-12",
  "alamat": "Cibinong, Bogor",
  "program_studi_id": 1,
  "jalur_masuk_id": 1
}
```

Hasil yang diharapkan:
- Pendaftaran berhasil: HTTP `201` dengan `status: "pending"` dan `nim: null`.
- Email duplikat (termasuk beda huruf besar/kecil): HTTP `409`.
- Input tidak valid (format email/HP/nama pendek): HTTP `400`.
- `program_studi_id` atau `jalur_masuk_id` tidak ditemukan: HTTP `400`.

## 6. Jalankan Pengujian Otomatis

```powershell
python -m pytest -v
```

## 7. Dokumentasi Tambahan

- **ADR**: [docs/ADR-001-minggu3-api-dan-database.md](docs/ADR-001-minggu3-api-dan-database.md)
- **AI Usage Log & Prompt Terbaik**: [AI_USAGE_LOG.md](AI_USAGE_LOG.md)
