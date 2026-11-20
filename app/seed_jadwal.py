"""
Seed data Minggu 7: ruangan, dosen, kelas paralel, jadwal normal (termasuk kuliah bersambung),
dan 2 bentrok yang disengaja (1 room_conflict & 1 lecturer_conflict) untuk menguji GET /api/jadwal/conflicts.
"""
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Dosen, Jadwal, Kelas, MataKuliah, Ruangan


def seed_jadwal_minggu7(db: Session, *, include_intentional_conflicts: bool = True) -> dict[str, int]:
    """Membuat data referensi & jadwal Minggu 7 secara idempoten jika tabel ruangan masih kosong."""
    if db.scalar(select(Ruangan.kode).limit(1)) is not None:
        return {"seeded": 0}

    ruangans = [
        Ruangan(kode="A101", nama="Ruang Kuliah A101", kapasitas=40),
        Ruangan(kode="A102", nama="Ruang Kuliah A102", kapasitas=30),
        Ruangan(kode="B201", nama="Ruang Kuliah B201", kapasitas=50),
        Ruangan(kode="LAB1", nama="Lab Komputer 1", kapasitas=35),
    ]
    dosens = [
        Dosen(id=1, nidn="0101018501", nama="Dr. Andi Wijaya"),
        Dosen(id=2, nidn="0102028602", nama="Dr. Rina Kusuma"),
        Dosen(id=3, nidn="0103038703", nama="Dr. Budi Hartono"),
    ]
    db.add_all(ruangans + dosens)

    mk_list = [
        ("IF101", "Basis Data", 3, 1, time(8, 0), time(9, 40)),
        ("IF102", "Pemrograman Berorientasi Objek", 3, 1, time(9, 40), time(11, 20)),
        ("IF201", "Statistika Komputasi", 3, 2, time(8, 0), time(9, 40)),
        ("IF202", "Jaringan Komputer", 3, 3, time(10, 0), time(11, 40)),
    ]
    for kode_mk, nama, sks, hari, jam_mulai, jam_selesai in mk_list:
        if db.get(MataKuliah, kode_mk) is None:
            db.add(
                MataKuliah(
                    kode_mk=kode_mk,
                    nama=nama,
                    sks=sks,
                    hari=hari,
                    jam_mulai=jam_mulai,
                    jam_selesai=jam_selesai,
                )
            )
    db.flush()

    kelas_list = [
        Kelas(id=1, kode_mk="IF101", nama_kelas="A", kuota=35, semester="20261"),
        Kelas(id=2, kode_mk="IF101", nama_kelas="B", kuota=30, semester="20261"),
        Kelas(id=3, kode_mk="IF102", nama_kelas="A", kuota=40, semester="20261"),
        Kelas(id=4, kode_mk="IF201", nama_kelas="A", kuota=45, semester="20261"),
        Kelas(id=5, kode_mk="IF202", nama_kelas="A", kuota=30, semester="20261"),
    ]
    db.add_all(kelas_list)
    db.flush()

    jadwals = [
        # Jadwal normal + kuliah bersambung (08:00-09:40 lalu 09:40-11:20 di A101 -> TIDAK bentrok)
        Jadwal(id=1, kelas_id=1, ruangan_kode="A101", dosen_id=1, hari=1, jam_mulai=time(8, 0), jam_selesai=time(9, 40)),
        Jadwal(id=2, kelas_id=3, ruangan_kode="A101", dosen_id=2, hari=1, jam_mulai=time(9, 40), jam_selesai=time(11, 20)),
        # Praktikum kelas 1 pada hari Kamis di LAB1 (1 kelas punya >1 pertemuan/minggu)
        Jadwal(id=3, kelas_id=1, ruangan_kode="LAB1", dosen_id=1, hari=4, jam_mulai=time(13, 0), jam_selesai=time(14, 40)),
        # Kelas besar berkuota 45 di B201 (kapasitas 50)
        Jadwal(id=4, kelas_id=4, ruangan_kode="B201", dosen_id=3, hari=2, jam_mulai=time(8, 0), jam_selesai=time(9, 40)),
    ]

    if include_intentional_conflicts:
        # Bentrok disengaja (simulasi impor data lama untuk menguji GET /api/jadwal/conflicts):
        # 1) Room conflict dengan Jadwal #1: Senin 08:30-09:30 di A101 (dosen berbeda)
        jadwals.append(
            Jadwal(id=5, kelas_id=2, ruangan_kode="A101", dosen_id=3, hari=1, jam_mulai=time(8, 30), jam_selesai=time(9, 30))
        )
        # 2) Lecturer conflict dengan Jadwal #4: Selasa 09:00-10:40 dengan Dosen #3 di ruangan berbeda (A102)
        jadwals.append(
            Jadwal(id=6, kelas_id=5, ruangan_kode="A102", dosen_id=3, hari=2, jam_mulai=time(9, 0), jam_selesai=time(10, 40))
        )

    db.add_all(jadwals)
    db.commit()
    return {"seeded": len(jadwals)}


if __name__ == "__main__":
    session = SessionLocal()
    try:
        result = seed_jadwal_minggu7(session)
        print("Seed Minggu 7 selesai:", result)
    finally:
        session.close()
