from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceSessionStatus,
    AttendanceStatus,
    CalonMahasiswa,
    Jadwal,
    KRS,
    KRSDetail,
    Kelas,
    MataKuliah,
    StatusPMB,
)
from app.services.attendance_errors import (
    AttendanceConflictError,
    AttendanceForbiddenError,
    AttendanceNotFoundError,
    AttendanceTokenError,
    AttendanceValidationError,
)
from app.services.attendance_qr import (
    DEFAULT_QR_VALID_MINUTES,
    EARLY_CHECKIN_BUFFER_MINUTES,
    MAX_LATE_WINDOW_MINUTES,
    ON_TIME_WINDOW_MINUTES,
    decode_attendance_token,
    generate_attendance_qr,
    haversine_distance_meters,
    qr_image_to_base64,
    qr_image_to_png_bytes,
)

HARI_NAMA: dict[int, str] = {
    1: "Senin",
    2: "Selasa",
    3: "Rabu",
    4: "Kamis",
    5: "Jumat",
    6: "Sabtu",
    7: "Minggu",
}
ATTENDANCE_WARNING_THRESHOLD = 75.0


@dataclass(frozen=True)
class GeneratedQRResult:
    session_id: int
    schedule_id: int
    kode_mk: str
    nama_mk: str
    nama_kelas: str
    semester: str
    hari: int
    nama_hari: str
    jam_mulai: str
    jam_selesai: str
    ruangan_kode: str
    dosen_nama: str
    session_date: date
    pertemuan_ke: int
    active_jti: str
    session_started_at: datetime
    qr_generated_at: datetime
    qr_expires_at: datetime
    valid_minutes: int
    token: str
    png_bytes: bytes
    qr_base64: str
    require_location: bool
    latitude: float | None
    longitude: float | None
    radius_meters: float


def _naive_wall_clock(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now().replace(microsecond=0)
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    return dt.replace(microsecond=0)


class AttendanceService:
    """Service utama Modul Presensi berbasis QR Code (Minggu 12)."""

    def _get_jadwal_or_404(self, db: Session, schedule_id: int) -> Jadwal:
        jadwal = db.scalar(
            select(Jadwal)
            .options(
                joinedload(Jadwal.kelas).joinedload(Kelas.matakuliah),
                joinedload(Jadwal.ruangan),
                joinedload(Jadwal.dosen),
            )
            .where(Jadwal.id == schedule_id)
        )
        if jadwal is None:
            raise AttendanceNotFoundError(
                f"Jadwal kuliah dengan ID {schedule_id} tidak ditemukan"
            )
        return jadwal

    def create_or_refresh_qr_session(
        self,
        db: Session,
        schedule_id: int,
        *,
        valid_minutes: int = DEFAULT_QR_VALID_MINUTES,
        session_date: date | None = None,
        now: datetime | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_meters: float = 100.0,
        require_location: bool = False,
        created_by: str = "dosen",
    ) -> GeneratedQRResult:
        """Dosen generate QR baru untuk sesi kuliah tertentu (valid 15 menit, jti unik).

        Jika sesi pada `(schedule_id, session_date)` sudah ada, token lama (`active_jti`)
        otomatis diganti sehingga QR lama tidak dapat dipakai ulang (anti replay attack),
        tanpa mengubah `session_started_at` agar perhitungan `terlambat` tetap akurat.
        """
        if valid_minutes <= 0 or valid_minutes > 60:
            raise AttendanceValidationError(
                "Durasi validitas QR harus antara 1 sampai 60 menit"
            )
        if radius_meters <= 0:
            raise AttendanceValidationError("radius_meters harus lebih besar dari 0")
        if require_location and (latitude is None or longitude is None):
            raise AttendanceValidationError(
                "latitude dan longitude wajib diisi jika require_location=true"
            )

        jadwal = self._get_jadwal_or_404(db, schedule_id)
        wall_now = _naive_wall_clock(now)
        target_date = session_date or wall_now.date()
        new_jti = uuid.uuid4().hex
        expires_wall = wall_now + timedelta(minutes=valid_minutes)
        class_start_wall = datetime.combine(target_date, jadwal.jam_mulai)

        session = db.scalar(
            select(AttendanceSession)
            .where(
                AttendanceSession.jadwal_id == schedule_id,
                AttendanceSession.session_date == target_date,
            )
            .with_for_update()
        )

        if session is None:
            max_pertemuan = db.scalar(
                select(func.coalesce(func.max(AttendanceSession.pertemuan_ke), 0)).where(
                    AttendanceSession.jadwal_id == schedule_id
                )
            )
            session = AttendanceSession(
                jadwal_id=schedule_id,
                session_date=target_date,
                pertemuan_ke=int(max_pertemuan or 0) + 1,
                active_jti=new_jti,
                session_started_at=class_start_wall,
                qr_generated_at=wall_now,
                qr_expires_at=expires_wall,
                status=AttendanceSessionStatus.OPEN,
                latitude=latitude,
                longitude=longitude,
                radius_meters=radius_meters,
                require_location=require_location,
                created_by=created_by,
                updated_at=wall_now,
            )
            db.add(session)
            db.flush()
        else:
            session.active_jti = new_jti
            session.qr_generated_at = wall_now
            session.qr_expires_at = expires_wall
            session.status = AttendanceSessionStatus.OPEN
            session.updated_at = wall_now
            if latitude is not None and longitude is not None:
                session.latitude = latitude
                session.longitude = longitude
            if require_location:
                session.require_location = True
                session.radius_meters = radius_meters
            db.flush()

        qr_img, token = generate_attendance_qr(
            schedule_id=schedule_id,
            valid_minutes=valid_minutes,
            session_id=session.id,
            jti=new_jti,
            session_date=target_date,
            now=now,
        )
        png_bytes = qr_image_to_png_bytes(qr_img)
        qr_b64 = qr_image_to_base64(qr_img)

        db.commit()
        db.refresh(session)

        return GeneratedQRResult(
            session_id=session.id,
            schedule_id=jadwal.id,
            kode_mk=jadwal.kelas.kode_mk,
            nama_mk=jadwal.kelas.matakuliah.nama,
            nama_kelas=jadwal.kelas.nama_kelas,
            semester=jadwal.kelas.semester,
            hari=jadwal.hari,
            nama_hari=HARI_NAMA.get(jadwal.hari, str(jadwal.hari)),
            jam_mulai=jadwal.jam_mulai.strftime("%H:%M"),
            jam_selesai=jadwal.jam_selesai.strftime("%H:%M"),
            ruangan_kode=jadwal.ruangan_kode,
            dosen_nama=jadwal.dosen.nama,
            session_date=session.session_date,
            pertemuan_ke=session.pertemuan_ke,
            active_jti=session.active_jti,
            session_started_at=session.session_started_at,
            qr_generated_at=session.qr_generated_at,
            qr_expires_at=session.qr_expires_at,
            valid_minutes=valid_minutes,
            token=token,
            png_bytes=png_bytes,
            qr_base64=qr_b64,
            require_location=session.require_location,
            latitude=session.latitude,
            longitude=session.longitude,
            radius_meters=session.radius_meters,
        )

    def close_session(
        self,
        db: Session,
        session_id: int,
        *,
        mark_absent: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Menutup sesi presensi & opsional mencatat status `alpha` bagi mahasiswa yang belum scan."""
        session = db.scalar(
            select(AttendanceSession)
            .where(AttendanceSession.id == session_id)
            .with_for_update()
        )
        if session is None:
            raise AttendanceNotFoundError(
                f"Sesi presensi dengan ID {session_id} tidak ditemukan"
            )

        jadwal = self._get_jadwal_or_404(db, session.jadwal_id)
        wall_now = _naive_wall_clock(now)
        session.status = AttendanceSessionStatus.CLOSED
        session.updated_at = wall_now

        marked_alpha = 0
        if mark_absent:
            enrolled_nims = self._get_enrolled_nims_for_jadwal(db, jadwal)
            existing_nims = set(
                db.scalars(
                    select(AttendanceRecord.nim).where(
                        AttendanceRecord.session_id == session.id
                    )
                ).all()
            )
            for nim in sorted(enrolled_nims - existing_nims):
                db.add(
                    AttendanceRecord(
                        session_id=session.id,
                        jadwal_id=jadwal.id,
                        nim=nim,
                        status=AttendanceStatus.ALPHA,
                        scanned_at=wall_now,
                        notes="Tidak melakukan scan hingga sesi ditutup",
                    )
                )
                marked_alpha += 1

        db.commit()
        return {
            "session_id": session.id,
            "schedule_id": session.jadwal_id,
            "status": session.status.value,
            "marked_alpha": marked_alpha,
        }

    def _get_enrolled_nims_for_jadwal(self, db: Session, jadwal: Jadwal) -> set[str]:
        rows = db.execute(
            select(KRS.nim, KRSDetail.kelas_id)
            .join(KRSDetail, KRSDetail.krs_id == KRS.id)
            .where(
                KRS.semester == jadwal.kelas.semester,
                KRSDetail.kode_mk == jadwal.kelas.kode_mk,
            )
        ).all()
        result: set[str] = set()
        for nim, detail_kelas_id in rows:
            if detail_kelas_id is None or detail_kelas_id == jadwal.kelas_id:
                result.add(nim)
        return result

    def _verify_enrollment(self, db: Session, nim: str, jadwal: Jadwal) -> CalonMahasiswa:
        mahasiswa = db.scalar(
            select(CalonMahasiswa).where(CalonMahasiswa.nim == nim)
        )
        if mahasiswa is None:
            raise AttendanceNotFoundError(
                f"Mahasiswa dengan NIM {nim} tidak ditemukan"
            )
        if mahasiswa.status != StatusPMB.APPROVED:
            raise AttendanceForbiddenError(
                f"Mahasiswa dengan NIM {nim} belum berstatus aktif/approved"
            )

        detail = db.scalar(
            select(KRSDetail)
            .join(KRS, KRS.id == KRSDetail.krs_id)
            .where(
                KRS.nim == nim,
                KRS.semester == jadwal.kelas.semester,
                KRSDetail.kode_mk == jadwal.kelas.kode_mk,
            )
        )
        if detail is None:
            raise AttendanceForbiddenError(
                f"Mahasiswa {nim} tidak terdaftar pada mata kuliah "
                f"{jadwal.kelas.kode_mk} ({jadwal.kelas.matakuliah.nama}) semester {jadwal.kelas.semester}"
            )
        if detail.kelas_id is not None and detail.kelas_id != jadwal.kelas_id:
            raise AttendanceForbiddenError(
                f"Mahasiswa {nim} terdaftar di kelas lain, tidak dapat scan QR "
                f"kelas {jadwal.kelas.kode_mk}-{jadwal.kelas.nama_kelas}"
            )
        return mahasiswa

    def record_scan(
        self,
        db: Session,
        *,
        token: str,
        nim: str,
        now: datetime | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict[str, Any]:
        """Memproses scan QR mahasiswa dengan 5 validasi wajib + geolocation opsional.

        Validasi:
        1. Decode JWT -> extract `schedule_id`, cek `exp` (15 menit), dan cek `jti` aktif (anti replay).
        2. Query schedule -> pastikan hari ini memang ada jadwal kuliah sesuai sesi.
        3. Cek enrollment -> pastikan mahasiswa terdaftar pada MK & kelas ini (anti scan kelas orang lain).
        4. Cek geolocation (opsional bila lokasi sesi diaktifkan/dikirim).
        5. Cek duplicate -> pastikan mahasiswa belum scan pada sesi ini.
        6. Hitung status -> `hadir` (<= 15 menit pertama) / `terlambat` (> 15 s.d. 30 menit).
        """
        cleaned_nim = nim.strip()
        if not cleaned_nim:
            raise AttendanceValidationError("NIM mahasiswa wajib diisi")

        # 1. Decode JWT & verifikasi expiration + anti-replay
        claims = decode_attendance_token(token, now=now)
        jadwal = self._get_jadwal_or_404(db, claims.schedule_id)

        session_query = select(AttendanceSession).where(
            AttendanceSession.jadwal_id == claims.schedule_id
        )
        if claims.session_id is not None:
            session_query = session_query.where(
                AttendanceSession.id == claims.session_id
            )
        else:
            session_query = session_query.where(
                AttendanceSession.active_jti == claims.jti
            )
        session = db.scalar(session_query.with_for_update())

        if session is None:
            raise AttendanceTokenError(
                "Sesi presensi untuk token QR ini tidak ditemukan atau sudah tidak berlaku"
            )
        if session.status != AttendanceSessionStatus.OPEN:
            raise AttendanceTokenError(
                "Sesi presensi sudah ditutup oleh dosen; QR tidak lagi diterima"
            )
        if session.active_jti != claims.jti:
            raise AttendanceTokenError(
                "Token QR sudah digantikan oleh token sesi baru (replay attack ditolak)"
            )

        # 2. Query schedule: apakah hari ini ada kuliah?
        scan_dt = _naive_wall_clock(now)
        if scan_dt.date() != session.session_date:
            raise AttendanceValidationError(
                f"Tanggal scan ({scan_dt.date().isoformat()}) tidak sesuai dengan "
                f"tanggal sesi kuliah ({session.session_date.isoformat()})"
            )
        if scan_dt.isoweekday() != jadwal.hari:
            hari_scan = HARI_NAMA.get(scan_dt.isoweekday(), str(scan_dt.isoweekday()))
            hari_jadwal = HARI_NAMA.get(jadwal.hari, str(jadwal.hari))
            raise AttendanceValidationError(
                f"Tidak ada jadwal kuliah {jadwal.kelas.kode_mk} pada hari {hari_scan} "
                f"(jadwal resmi: hari {hari_jadwal})"
            )

        # 3. Cek enrollment mahasiswa pada MK & kelas
        mahasiswa = self._verify_enrollment(db, cleaned_nim, jadwal)

        # 4. Validasi Geolocation (opsional)
        distance_meters: float | None = None
        if session.require_location and (latitude is None or longitude is None):
            raise AttendanceValidationError(
                "Validasi lokasi aktif: koordinat latitude dan longitude wajib dikirim"
            )
        if (
            session.latitude is not None
            and session.longitude is not None
            and latitude is not None
            and longitude is not None
        ):
            distance_meters = haversine_distance_meters(
                session.latitude, session.longitude, latitude, longitude
            )
            if distance_meters > session.radius_meters:
                raise AttendanceValidationError(
                    f"Lokasi Anda berada di luar radius kelas "
                    f"({distance_meters:.1f} m > batas {session.radius_meters:.1f} m)"
                )

        # 5. Check duplicate scan pada sesi yang sama
        existing = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.session_id == session.id,
                AttendanceRecord.nim == cleaned_nim,
            )
        )
        if existing is not None:
            raise AttendanceConflictError(
                f"Mahasiswa {cleaned_nim} sudah melakukan presensi pada sesi ini "
                f"(status: {existing.status.value})"
            )

        # 6. Calculate status: Hadir (<= 15m pertama), Terlambat (15-30m), Tolak (> 30m)
        elapsed_minutes = (
            scan_dt - session.session_started_at
        ).total_seconds() / 60.0
        class_end = datetime.combine(session.session_date, jadwal.jam_selesai)

        if elapsed_minutes < -EARLY_CHECKIN_BUFFER_MINUTES:
            raise AttendanceValidationError(
                f"Presensi belum dibuka. Kuliah dimulai pukul {jadwal.jam_mulai.strftime('%H:%M')}."
            )
        if scan_dt > class_end:
            raise AttendanceValidationError(
                f"Waktu kuliah sudah berakhir pada pukul {jadwal.jam_selesai.strftime('%H:%M')}."
            )
        if elapsed_minutes > MAX_LATE_WINDOW_MINUTES:
            raise AttendanceValidationError(
                f"Window presensi sudah ditutup (> {MAX_LATE_WINDOW_MINUTES} menit sejak kuliah dimulai)"
            )

        if elapsed_minutes <= ON_TIME_WINDOW_MINUTES:
            status = AttendanceStatus.HADIR
            message = (
                f"Presensi berhasil dicatat sebagai HADIR untuk {jadwal.kelas.kode_mk} "
                f"({jadwal.kelas.matakuliah.nama}) kelas {jadwal.kelas.nama_kelas}"
            )
        else:
            status = AttendanceStatus.TERLAMBAT
            message = (
                f"Presensi berhasil dicatat sebagai TERLAMBAT ({int(round(elapsed_minutes))} menit) "
                f"untuk {jadwal.kelas.kode_mk} ({jadwal.kelas.matakuliah.nama}) kelas {jadwal.kelas.nama_kelas}"
            )

        record = AttendanceRecord(
            session_id=session.id,
            jadwal_id=jadwal.id,
            nim=cleaned_nim,
            status=status,
            scanned_at=scan_dt,
            token_jti=claims.jti,
            latitude=latitude,
            longitude=longitude,
            distance_meters=distance_meters,
        )
        db.add(record)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise AttendanceConflictError(
                f"Mahasiswa {cleaned_nim} sudah melakukan presensi pada sesi ini"
            ) from exc

        db.refresh(record)
        return {
            "success": True,
            "status": record.status.value,
            "message": message,
            "record_id": record.id,
            "session_id": session.id,
            "schedule_id": jadwal.id,
            "nim": cleaned_nim,
            "nama_mahasiswa": mahasiswa.nama_lengkap,
            "kode_mk": jadwal.kelas.kode_mk,
            "nama_mk": jadwal.kelas.matakuliah.nama,
            "nama_kelas": jadwal.kelas.nama_kelas,
            "scanned_at": record.scanned_at,
            "distance_meters": record.distance_meters,
        }

    def list_sessions(
        self,
        db: Session,
        *,
        schedule_id: int | None = None,
        kode_mk: str | None = None,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(AttendanceSession)
            .join(Jadwal, Jadwal.id == AttendanceSession.jadwal_id)
            .join(Kelas, Kelas.id == Jadwal.kelas_id)
            .options(
                joinedload(AttendanceSession.jadwal)
                .joinedload(Jadwal.kelas)
                .joinedload(Kelas.matakuliah),
                joinedload(AttendanceSession.jadwal).joinedload(Jadwal.dosen),
                joinedload(AttendanceSession.jadwal).joinedload(Jadwal.ruangan),
                joinedload(AttendanceSession.records),
            )
            .order_by(
                AttendanceSession.session_date.desc(),
                AttendanceSession.id.desc(),
            )
        )
        if schedule_id is not None:
            stmt = stmt.where(AttendanceSession.jadwal_id == schedule_id)
        if kode_mk is not None:
            stmt = stmt.where(Kelas.kode_mk == kode_mk.strip().upper())

        sessions = db.scalars(stmt).unique().all()
        output: list[dict[str, Any]] = []
        for s in sessions:
            hadir = sum(1 for r in s.records if r.status == AttendanceStatus.HADIR)
            terlambat = sum(
                1 for r in s.records if r.status == AttendanceStatus.TERLAMBAT
            )
            alpha = sum(1 for r in s.records if r.status == AttendanceStatus.ALPHA)
            output.append({
                "session_id": s.id,
                "schedule_id": s.jadwal_id,
                "kode_mk": s.jadwal.kelas.kode_mk,
                "nama_mk": s.jadwal.kelas.matakuliah.nama,
                "nama_kelas": s.jadwal.kelas.nama_kelas,
                "semester": s.jadwal.kelas.semester,
                "hari": s.jadwal.hari,
                "nama_hari": HARI_NAMA.get(s.jadwal.hari, str(s.jadwal.hari)),
                "jam_mulai": s.jadwal.jam_mulai.strftime("%H:%M"),
                "jam_selesai": s.jadwal.jam_selesai.strftime("%H:%M"),
                "ruangan_kode": s.jadwal.ruangan_kode,
                "dosen_nama": s.jadwal.dosen.nama,
                "session_date": s.session_date,
                "pertemuan_ke": s.pertemuan_ke,
                "status": s.status.value,
                "qr_expires_at": s.qr_expires_at,
                "hadir_count": hadir,
                "terlambat_count": terlambat,
                "alpha_count": alpha,
                "total_records": len(s.records),
            })
        return output

    def get_student_attendance_report(
        self,
        db: Session,
        nim: str,
        *,
        semester: str | None = None,
    ) -> dict[str, Any]:
        """Laporan presensi per mahasiswa: persentase kehadiran per MK + warning < 75%."""
        cleaned_nim = nim.strip()
        mahasiswa = db.scalar(
            select(CalonMahasiswa).where(CalonMahasiswa.nim == cleaned_nim)
        )
        if mahasiswa is None:
            raise AttendanceNotFoundError(
                f"Mahasiswa dengan NIM {cleaned_nim} tidak ditemukan"
            )

        krs_stmt = (
            select(KRS, KRSDetail, MataKuliah)
            .join(KRSDetail, KRSDetail.krs_id == KRS.id)
            .join(MataKuliah, MataKuliah.kode_mk == KRSDetail.kode_mk)
            .where(KRS.nim == cleaned_nim)
        )
        if semester:
            krs_stmt = krs_stmt.where(KRS.semester == semester)
        enrolled_rows = db.execute(krs_stmt).all()

        courses_summary: list[dict[str, Any]] = []
        warning_courses: list[str] = []

        for krs_obj, detail, mk in enrolled_rows:
            session_stmt = (
                select(AttendanceSession)
                .join(Jadwal, Jadwal.id == AttendanceSession.jadwal_id)
                .join(Kelas, Kelas.id == Jadwal.kelas_id)
                .where(
                    Kelas.kode_mk == mk.kode_mk,
                    Kelas.semester == krs_obj.semester,
                )
            )
            if detail.kelas_id is not None:
                session_stmt = session_stmt.where(Kelas.id == detail.kelas_id)
            sessions = db.scalars(session_stmt).all()
            session_ids = [s.id for s in sessions]
            total_sessions = len(session_ids)

            records_by_session: dict[int, AttendanceRecord] = {}
            if session_ids:
                recs = db.scalars(
                    select(AttendanceRecord).where(
                        AttendanceRecord.nim == cleaned_nim,
                        AttendanceRecord.session_id.in_(session_ids),
                    )
                ).all()
                records_by_session = {r.session_id: r for r in recs}

            hadir = 0
            terlambat = 0
            izin = 0
            alpha = 0
            for sid in session_ids:
                rec = records_by_session.get(sid)
                if rec is None or rec.status == AttendanceStatus.ALPHA:
                    alpha += 1
                elif rec.status == AttendanceStatus.HADIR:
                    hadir += 1
                elif rec.status == AttendanceStatus.TERLAMBAT:
                    terlambat += 1
                elif rec.status == AttendanceStatus.IZIN:
                    izin += 1

            attended = hadir + terlambat
            percentage = (
                round((attended / total_sessions) * 100.0, 2)
                if total_sessions > 0
                else 0.0
            )
            is_warning = (
                total_sessions > 0 and percentage < ATTENDANCE_WARNING_THRESHOLD
            )
            if is_warning:
                warning_courses.append(mk.kode_mk)

            courses_summary.append({
                "kode_mk": mk.kode_mk,
                "nama_mk": mk.nama,
                "semester": krs_obj.semester,
                "kelas_id": detail.kelas_id,
                "total_sessions": total_sessions,
                "hadir": hadir,
                "terlambat": terlambat,
                "izin": izin,
                "alpha": alpha,
                "attended_count": attended,
                "attendance_percentage": percentage,
                "warning": is_warning,
            })

        courses_summary.sort(key=lambda item: (item["semester"], item["kode_mk"]))
        active_courses = [c for c in courses_summary if c["total_sessions"] > 0]
        overall_pct = (
            round(
                sum(c["attendance_percentage"] for c in active_courses)
                / len(active_courses),
                2,
            )
            if active_courses
            else 0.0
        )

        return {
            "nim": cleaned_nim,
            "nama_mahasiswa": mahasiswa.nama_lengkap,
            "semester": semester,
            "overall_attendance_percentage": overall_pct,
            "warning_course_count": len(warning_courses),
            "courses": courses_summary,
        }

    def get_course_attendance_report(
        self,
        db: Session,
        kode_mk: str,
        *,
        semester: str | None = None,
        kelas_id: int | None = None,
    ) -> dict[str, Any]:
        """Laporan presensi per MK: rekap semua mahasiswa + daftar warning (<75%)."""
        normalized_kode = kode_mk.strip().upper()
        mk = db.scalar(
            select(MataKuliah).where(MataKuliah.kode_mk == normalized_kode)
        )
        if mk is None:
            raise AttendanceNotFoundError(
                f"Mata kuliah '{normalized_kode}' tidak ditemukan"
            )

        kelas_stmt = select(Kelas).where(Kelas.kode_mk == normalized_kode)
        if semester:
            kelas_stmt = kelas_stmt.where(Kelas.semester == semester)
        if kelas_id is not None:
            kelas_stmt = kelas_stmt.where(Kelas.id == kelas_id)
        kelas_list = db.scalars(kelas_stmt.order_by(Kelas.semester, Kelas.nama_kelas)).all()
        kelas_ids = [k.id for k in kelas_list]
        kelas_map = {k.id: k for k in kelas_list}

        sessions: list[AttendanceSession] = []
        if kelas_ids:
            sessions = db.scalars(
                select(AttendanceSession)
                .join(Jadwal, Jadwal.id == AttendanceSession.jadwal_id)
                .options(
                    joinedload(AttendanceSession.jadwal).joinedload(Jadwal.kelas),
                )
                .where(Jadwal.kelas_id.in_(kelas_ids))
                .order_by(AttendanceSession.session_date, AttendanceSession.id)
            ).all()

        sessions_by_kelas: dict[int, list[AttendanceSession]] = {}
        for s in sessions:
            sessions_by_kelas.setdefault(s.jadwal.kelas_id, []).append(s)

        # Kumpulkan mahasiswa terdaftar di MK ini
        krs_stmt = (
            select(KRS.nim, KRS.semester, KRSDetail.kelas_id, CalonMahasiswa.nama_lengkap)
            .join(KRSDetail, KRSDetail.krs_id == KRS.id)
            .join(CalonMahasiswa, CalonMahasiswa.nim == KRS.nim)
            .where(KRSDetail.kode_mk == normalized_kode)
        )
        if semester:
            krs_stmt = krs_stmt.where(KRS.semester == semester)
        enrolled_rows = db.execute(krs_stmt).all()

        # Filter berdasarkan kelas_id jika diminta
        students_meta: dict[str, dict[str, Any]] = {}
        for nim_val, sem_val, det_kelas_id, nama_mhs in enrolled_rows:
            if kelas_id is not None and det_kelas_id is not None and det_kelas_id != kelas_id:
                continue
            nama_kelas = (
                kelas_map[det_kelas_id].nama_kelas
                if det_kelas_id in kelas_map
                else (kelas_list[0].nama_kelas if kelas_list else "-")
            )
            students_meta[nim_val] = {
                "nim": nim_val,
                "nama_mahasiswa": nama_mhs,
                "semester": sem_val,
                "kelas_id": det_kelas_id,
                "nama_kelas": nama_kelas,
            }

        session_ids = [s.id for s in sessions]
        all_records: list[AttendanceRecord] = []
        if session_ids:
            all_records = db.scalars(
                select(AttendanceRecord)
                .options(
                    joinedload(AttendanceRecord.mahasiswa),
                    joinedload(AttendanceRecord.session),
                    joinedload(AttendanceRecord.jadwal).joinedload(Jadwal.kelas),
                )
                .where(AttendanceRecord.session_id.in_(session_ids))
                .order_by(AttendanceRecord.scanned_at, AttendanceRecord.id)
            ).all()

        # Pastikan mahasiswa yang tercatat di attendance_records tetap muncul
        for rec in all_records:
            if rec.nim not in students_meta:
                students_meta[rec.nim] = {
                    "nim": rec.nim,
                    "nama_mahasiswa": (
                        rec.mahasiswa.nama_lengkap if rec.mahasiswa else rec.nim
                    ),
                    "semester": rec.jadwal.kelas.semester,
                    "kelas_id": rec.jadwal.kelas_id,
                    "nama_kelas": rec.jadwal.kelas.nama_kelas,
                }

        records_lookup: dict[tuple[str, int], AttendanceRecord] = {
            (r.nim, r.session_id): r for r in all_records
        }

        students_report: list[dict[str, Any]] = []
        warning_students: list[dict[str, Any]] = []

        for nim_val in sorted(students_meta.keys()):
            meta = students_meta[nim_val]
            det_kid = meta["kelas_id"]
            if det_kid is not None and det_kid in sessions_by_kelas:
                relevant_sessions = sessions_by_kelas[det_kid]
            else:
                relevant_sessions = sessions

            total_s = len(relevant_sessions)
            hadir = 0
            terlambat = 0
            izin = 0
            alpha = 0

            for sess in relevant_sessions:
                rec = records_lookup.get((nim_val, sess.id))
                if rec is None or rec.status == AttendanceStatus.ALPHA:
                    alpha += 1
                elif rec.status == AttendanceStatus.HADIR:
                    hadir += 1
                elif rec.status == AttendanceStatus.TERLAMBAT:
                    terlambat += 1
                elif rec.status == AttendanceStatus.IZIN:
                    izin += 1

            attended = hadir + terlambat
            pct = round((attended / total_s) * 100.0, 2) if total_s > 0 else 0.0
            is_warning = total_s > 0 and pct < ATTENDANCE_WARNING_THRESHOLD

            row = {
                "nim": nim_val,
                "nama_mahasiswa": meta["nama_mahasiswa"],
                "nama_kelas": meta["nama_kelas"],
                "total_sessions": total_s,
                "hadir": hadir,
                "terlambat": terlambat,
                "izin": izin,
                "alpha": alpha,
                "attended_count": attended,
                "attendance_percentage": pct,
                "warning": is_warning,
            }
            students_report.append(row)
            if is_warning:
                warning_students.append(row)

        warning_students.sort(key=lambda r: (r["attendance_percentage"], r["nim"]))

        avg_pct = (
            round(
                sum(r["attendance_percentage"] for r in students_report)
                / len(students_report),
                2,
            )
            if students_report
            else 0.0
        )

        detailed_logs = [
            {
                "session_date": r.session.session_date.isoformat(),
                "pertemuan_ke": r.session.pertemuan_ke,
                "nama_kelas": r.jadwal.kelas.nama_kelas,
                "nim": r.nim,
                "nama_mahasiswa": r.mahasiswa.nama_lengkap if r.mahasiswa else r.nim,
                "status": r.status.value,
                "scanned_at": r.scanned_at.strftime("%Y-%m-%d %H:%M:%S"),
                "distance_meters": r.distance_meters,
            }
            for r in all_records
        ]

        return {
            "kode_mk": mk.kode_mk,
            "nama_mk": mk.nama,
            "semester": semester or (kelas_list[0].semester if kelas_list else None),
            "total_sessions": len(sessions),
            "total_students": len(students_report),
            "average_attendance_percentage": avg_pct,
            "warning_threshold": ATTENDANCE_WARNING_THRESHOLD,
            "warning_count": len(warning_students),
            "warning_students": warning_students,
            "students": students_report,
            "detailed_logs": detailed_logs,
        }
