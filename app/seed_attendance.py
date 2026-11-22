"""Seed data demo realistis untuk Modul Presensi QR Code (Minggu 12).

Jalankan:
    py -3.12 -m app.seed_attendance
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceSessionStatus,
    AttendanceStatus,
    CalonMahasiswa,
    Dosen,
    Jadwal,
    JalurMasuk,
    KRS,
    KRSDetail,
    KRSStatus,
    Kelas,
    MataKuliah,
    ProgramStudi,
    Ruangan,
    StatusPMB,
)


def seed_attendance_demo() -> dict[str, int]:
    """Menyiapkan data mahasiswa, jadwal Senin 07:00 & Rabu 10:00, serta 4 sesi presensi
    untuk mendemonstrasikan report <75%, export Excel, dan AI Attendance Insights.
    """
    db = SessionLocal()
    try:
        prodi = db.scalar(select(ProgramStudi).where(ProgramStudi.kode == "002"))
        if prodi is None:
            prodi = ProgramStudi(
                kode="002",
                nama="Teknik Informatika",
                fakultas="Fakultas Teknik",
            )
            db.add(prodi)
            db.flush()

        jalur = db.scalar(select(JalurMasuk).where(JalurMasuk.nama == "SNBT"))
        if jalur is None:
            jalur = JalurMasuk(nama="SNBT")
            db.add(jalur)
            db.flush()

        students_spec = [
            ("20260020101", "Andi Pratama", "andi.presensi@campus.ac.id"),
            ("20260020102", "Budi Santoso", "budi.presensi@campus.ac.id"),
            ("20260020103", "Citra Dewi", "citra.presensi@campus.ac.id"),
            ("20260020104", "Dina Kartika", "dina.presensi@campus.ac.id"),
            ("20260020105", "Eko Wijaya", "eko.presensi@campus.ac.id"),
            ("20260020106", "Fajar Nugroho", "fajar.presensi@campus.ac.id"),
        ]
        for nim, nama, email in students_spec:
            existing = db.scalar(
                select(CalonMahasiswa).where(CalonMahasiswa.nim == nim)
            )
            if existing is None:
                db.add(
                    CalonMahasiswa(
                        nama_lengkap=nama,
                        email=email,
                        no_hp="081234567890",
                        tanggal_lahir=date(2006, 5, 10),
                        alamat="Kampus Terpadu",
                        program_studi_id=prodi.id,
                        jalur_masuk_id=jalur.id,
                        status=StatusPMB.APPROVED,
                        nim=nim,
                        approved_at=datetime(2026, 8, 1, 9, 0, 0),
                    )
                )
        db.flush()

        mk = db.scalar(select(MataKuliah).where(MataKuliah.kode_mk == "IF101"))
        if mk is None:
            mk = MataKuliah(
                kode_mk="IF101",
                nama="Algoritma & Pemrograman",
                sks=3,
                hari=1,
                jam_mulai=time(7, 0),
                jam_selesai=time(9, 30),
            )
            db.add(mk)
            db.flush()

        ruang = db.scalar(select(Ruangan).where(Ruangan.kode == "LAB-A1"))
        if ruang is None:
            ruang = Ruangan(kode="LAB-A1", nama="Lab Komputer A1", kapasitas=40)
            db.add(ruang)
            db.flush()

        dosen = db.scalar(select(Dosen).where(Dosen.nidn == "0412088501"))
        if dosen is None:
            dosen = Dosen(nidn="0412088501", nama="Dr. Hendra Kusuma")
            db.add(dosen)
            db.flush()

        semester = "20261"
        kelas = db.scalar(
            select(Kelas).where(
                Kelas.kode_mk == "IF101",
                Kelas.nama_kelas == "A",
                Kelas.semester == semester,
            )
        )
        if kelas is None:
            kelas = Kelas(
                kode_mk="IF101",
                nama_kelas="A",
                kuota=35,
                semester=semester,
            )
            db.add(kelas)
            db.flush()

        jadwal = db.scalar(
            select(Jadwal).where(
                Jadwal.kelas_id == kelas.id,
                Jadwal.hari == 1,
                Jadwal.jam_mulai == time(7, 0),
            )
        )
        if jadwal is None:
            jadwal = Jadwal(
                kelas_id=kelas.id,
                ruangan_kode=ruang.kode,
                dosen_id=dosen.id,
                hari=1,  # Senin 07:00
                jam_mulai=time(7, 0),
                jam_selesai=time(9, 30),
            )
            db.add(jadwal)
            db.flush()

        for nim, _, _ in students_spec:
            krs = db.scalar(
                select(KRS).where(KRS.nim == nim, KRS.semester == semester)
            )
            if krs is None:
                krs = KRS(nim=nim, semester=semester, status=KRSStatus.APPROVED)
                db.add(krs)
                db.flush()
            det = db.scalar(
                select(KRSDetail).where(
                    KRSDetail.krs_id == krs.id, KRSDetail.kode_mk == "IF101"
                )
            )
            if det is None:
                db.add(KRSDetail(krs_id=krs.id, kode_mk="IF101", kelas_id=kelas.id))
        db.flush()

        # 4 pertemuan hari Senin pagi (07:00)
        mondays = [
            date(2026, 11, 2),
            date(2026, 11, 9),
            date(2026, 11, 16),
            date(2026, 11, 23),
        ]
        # Pola kehadiran: Andi 100%, Budi 25% (sering absent Senin pagi),
        # Citra 50%, Dina 50%, Eko 50%, Fajar 100% -> 4 mahasiswa <75%, absen rate Senin 07:00 = 45.8%
        pattern: dict[str, list[AttendanceStatus]] = {
            "20260020101": [
                AttendanceStatus.HADIR,
                AttendanceStatus.HADIR,
                AttendanceStatus.HADIR,
                AttendanceStatus.HADIR,
            ],
            "20260020102": [
                AttendanceStatus.HADIR,
                AttendanceStatus.ALPHA,
                AttendanceStatus.ALPHA,
                AttendanceStatus.ALPHA,
            ],
            "20260020103": [
                AttendanceStatus.HADIR,
                AttendanceStatus.TERLAMBAT,
                AttendanceStatus.ALPHA,
                AttendanceStatus.ALPHA,
            ],
            "20260020104": [
                AttendanceStatus.ALPHA,
                AttendanceStatus.HADIR,
                AttendanceStatus.ALPHA,
                AttendanceStatus.TERLAMBAT,
            ],
            "20260020105": [
                AttendanceStatus.ALPHA,
                AttendanceStatus.ALPHA,
                AttendanceStatus.HADIR,
                AttendanceStatus.HADIR,
            ],
            "20260020106": [
                AttendanceStatus.HADIR,
                AttendanceStatus.TERLAMBAT,
                AttendanceStatus.HADIR,
                AttendanceStatus.HADIR,
            ],
        }

        created_sessions = 0
        created_records = 0
        for idx, sdate in enumerate(mondays, start=1):
            sess = db.scalar(
                select(AttendanceSession).where(
                    AttendanceSession.jadwal_id == jadwal.id,
                    AttendanceSession.session_date == sdate,
                )
            )
            start_dt = datetime.combine(sdate, jadwal.jam_mulai)
            if sess is None:
                sess = AttendanceSession(
                    jadwal_id=jadwal.id,
                    session_date=sdate,
                    pertemuan_ke=idx,
                    active_jti=uuid.uuid4().hex,
                    session_started_at=start_dt,
                    qr_generated_at=start_dt,
                    qr_expires_at=start_dt + timedelta(minutes=15),
                    status=AttendanceSessionStatus.CLOSED,
                    latitude=-6.3649,
                    longitude=106.8286,
                    radius_meters=150.0,
                    require_location=False,
                    created_by="Dr. Hendra Kusuma",
                )
                db.add(sess)
                db.flush()
                created_sessions += 1

            for nim, statuses in pattern.items():
                rec = db.scalar(
                    select(AttendanceRecord).where(
                        AttendanceRecord.session_id == sess.id,
                        AttendanceRecord.nim == nim,
                    )
                )
                if rec is None:
                    st = statuses[idx - 1]
                    offset = 5 if st == AttendanceStatus.HADIR else (20 if st == AttendanceStatus.TERLAMBAT else 35)
                    db.add(
                        AttendanceRecord(
                            session_id=sess.id,
                            jadwal_id=jadwal.id,
                            nim=nim,
                            status=st,
                            scanned_at=start_dt + timedelta(minutes=offset),
                            token_jti=sess.active_jti if st != AttendanceStatus.ALPHA else None,
                        )
                    )
                    created_records += 1

        db.commit()
        return {
            "jadwal_id": jadwal.id,
            "created_sessions": created_sessions,
            "created_records": created_records,
        }
    finally:
        db.close()


if __name__ == "__main__":
    summary = seed_attendance_demo()
    print(f"Seed presensi QR selesai: {summary}")
