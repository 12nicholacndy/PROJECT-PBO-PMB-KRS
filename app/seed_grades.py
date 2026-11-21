"""Seed data demo untuk Modul Nilai & Transkrip (Minggu 9–10).

Menjalankan secara idempoten:
    py -3.12 -m app.seed_grades
"""
from datetime import date, time

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    CalonMahasiswa,
    Grade,
    JalurMasuk,
    MataKuliah,
    ProgramStudi,
    StatusPMB,
)
from app.services.grade_service import GradeService
from app.services.pmb_service import approve_pendaftar


def seed_grades_demo() -> None:
    db = SessionLocal()
    service = GradeService()
    try:
        # 1. Pastikan referensi Prodi & Jalur Masuk tersedia
        if db.get(ProgramStudi, 1) is None:
            db.add_all([
                ProgramStudi(
                    id=1,
                    kode="001",
                    nama="Sistem Informasi",
                    fakultas="Fakultas Ilmu Komputer",
                ),
                ProgramStudi(
                    id=2,
                    kode="002",
                    nama="Teknik Informatika",
                    fakultas="Fakultas Ilmu Komputer",
                ),
            ])
        if db.get(JalurMasuk, 1) is None:
            db.add_all([
                JalurMasuk(id=1, nama="SNBP"),
                JalurMasuk(id=2, nama="SNBT"),
                JalurMasuk(id=3, nama="Mandiri"),
            ])
        db.commit()

        # 2. Pastikan Mata Kuliah tersedia
        mk_specs = [
            ("IF101", "Algoritma dan Pemrograman", 3, 1, time(8, 0), time(10, 30)),
            ("IF102", "Struktur Data", 4, 2, time(8, 0), time(11, 20)),
            ("IF103", "Basis Data", 3, 3, time(9, 0), time(11, 30)),
            ("IF104", "Pemrograman Berorientasi Objek", 3, 4, time(10, 0), time(12, 30)),
            ("IF105", "Etika Profesi", 2, 5, time(8, 0), time(9, 40)),
        ]
        for kode, nama, sks, hari, mulai, selesai in mk_specs:
            if db.get(MataKuliah, kode) is None:
                db.add(
                    MataKuliah(
                        kode_mk=kode,
                        nama=nama,
                        sks=sks,
                        hari=hari,
                        jam_mulai=mulai,
                        jam_selesai=selesai,
                    )
                )
        db.commit()

        # 3. Pastikan mahasiswa demo tersedia & berstatus approved (memiliki NIM)
        mhs = db.scalar(
            select(CalonMahasiswa).where(
                CalonMahasiswa.email == "nadia.transkrip@example.com"
            )
        )
        if mhs is None:
            mhs = CalonMahasiswa(
                nama_lengkap="Nadia Kusuma",
                email="nadia.transkrip@example.com",
                no_hp="081298765432",
                tanggal_lahir=date(2006, 8, 17),
                alamat="Depok, Jawa Barat",
                program_studi_id=2,
                jalur_masuk_id=1,
                status=StatusPMB.PENDING,
            )
            db.add(mhs)
            db.commit()
            db.refresh(mhs)
        if mhs.status != StatusPMB.APPROVED or not mhs.nim:
            mhs = approve_pendaftar(db, mhs.id)

        assert mhs.nim is not None
        nim = mhs.nim

        # 4. Seed nilai lintas semester + contoh MK diulang & 1 contoh audit trail
        demo_grades = [
            ("IF101", "20251", "D", 85.0),
            ("IF102", "20251", "A", 95.0),
            ("IF103", "20251", "E", 80.0),
            ("IF101", "20252", "A", 92.0),  # MK diulang -> nilai tertinggi A dipakai
            ("IF103", "20252", "B", 88.0),  # MK lulus setelah sebelumnya E
            ("IF104", "20252", "B", 90.0),
        ]
        created_if104_id: int | None = None
        for kode_mk, sem, huruf, presensi in demo_grades:
            existing = db.scalar(
                select(Grade).where(
                    Grade.nim == nim,
                    Grade.kode_mk == kode_mk,
                    Grade.semester == sem,
                )
            )
            if existing is None:
                gv, _ = service.input_grade(
                    db,
                    nim=nim,
                    kode_mk=kode_mk,
                    semester=sem,
                    nilai_huruf=huruf,
                    presensi_persen=presensi,
                    created_by="Dr. Budi Hartono",
                )
                if kode_mk == "IF104" and sem == "20252":
                    created_if104_id = gv.id

        # Contoh 1 perubahan nilai agar tabel grade_history langsung terisi
        if created_if104_id is not None:
            service.update_grade(
                db,
                grade_id=created_if104_id,
                new_nilai_huruf="A",
                changed_by="Dr. Budi Hartono",
                reason="Penambahan nilai proyek akhir PBO",
            )

        summary = service.get_academic_summary(db, nim)
        print(
            f"Seed Modul Nilai selesai untuk NIM {nim} ({mhs.nama_lengkap}) -> "
            f"SKS Lulus: {summary.total_sks_lulus}, IPK: {summary.ipk:.2f}, "
            f"Predikat: {summary.predikat}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed_grades_demo()
