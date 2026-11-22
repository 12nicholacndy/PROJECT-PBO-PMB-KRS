from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    AIAttendanceAnalyzeRequest,
    AIAttendanceInsightsResponse,
    AttendanceCloseSessionResponse,
    AttendanceQRSessionRequest,
    AttendanceQRSessionResponse,
    AttendanceScanRequest,
    AttendanceScanResponse,
    AttendanceSessionItemResponse,
    CourseAttendanceReportResponse,
    StudentAttendanceReportResponse,
)
from app.services.ai_attendance_analyzer import (
    analyze_attendance_from_db,
    analyze_attendance_records,
)
from app.services.attendance_errors import AttendanceForbiddenError
from app.services.attendance_excel import export_course_attendance_excel
from app.services.attendance_qr import get_scan_rate_limiter
from app.services.attendance_service import AttendanceService

router = APIRouter(prefix="/attendance", tags=["Presensi (QR Code)"])

_service = AttendanceService()
_ALLOWED_LECTURER_ROLES = {"dosen", "dosen_pa", "admin_akademik"}


def _require_lecturer_role(
    x_role: str = Header(default="dosen", alias="X-Role"),
) -> str:
    role = x_role.strip().lower()
    if role not in _ALLOWED_LECTURER_ROLES:
        raise AttendanceForbiddenError(
            f"Peran '{x_role}' tidak diizinkan mengelola sesi QR atau laporan dosen "
            f"(hanya dosen atau admin_akademik)"
        )
    return role


@router.get(
    "/qr/{schedule_id}",
    summary="Generate QR Code image (PNG) untuk sesi kuliah tertentu (valid 15 menit)",
    responses={
        200: {
            "content": {"image/png": {}},
            "description": "Gambar QR Code PNG dengan token JWT HS256 unik di header X-Attendance-Token",
        },
        403: {"description": "Peran tidak diizinkan (bukan dosen/admin)"},
        404: {"description": "Jadwal tidak ditemukan"},
    },
)
def get_attendance_qr_image(
    schedule_id: int,
    valid_minutes: int = Query(default=15, ge=1, le=60),
    session_date: date | None = Query(default=None),
    generated_at: datetime | None = Query(
        default=None,
        description="Opsional: override waktu generate (ISO datetime) untuk simulasi",
    ),
    latitude: float | None = Query(default=None, ge=-90.0, le=90.0),
    longitude: float | None = Query(default=None, ge=-180.0, le=180.0),
    radius_meters: float = Query(default=100.0, gt=0.0, le=5000.0),
    require_location: bool = Query(default=False),
    _: str = Depends(_require_lecturer_role),
    db: Session = Depends(get_db),
) -> Response:
    result = _service.create_or_refresh_qr_session(
        db,
        schedule_id,
        valid_minutes=valid_minutes,
        session_date=session_date,
        now=generated_at,
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
        require_location=require_location,
    )
    return Response(
        content=result.png_bytes,
        media_type="image/png",
        headers={
            "X-Attendance-Token": result.token,
            "X-Session-Id": str(result.session_id),
            "X-Active-Jti": result.active_jti,
            "X-Expires-At": result.qr_expires_at.isoformat(),
            "X-Session-Date": result.session_date.isoformat(),
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/qr/{schedule_id}/session",
    response_model=AttendanceQRSessionResponse,
    status_code=201,
    summary="Generate atau refresh sesi QR Code dan kembalikan metadata JSON + QR base64",
)
def create_attendance_qr_session(
    schedule_id: int,
    payload: AttendanceQRSessionRequest | None = None,
    generated_at: datetime | None = Query(default=None),
    _: str = Depends(_require_lecturer_role),
    db: Session = Depends(get_db),
):
    body = payload or AttendanceQRSessionRequest()
    result = _service.create_or_refresh_qr_session(
        db,
        schedule_id,
        valid_minutes=body.valid_minutes,
        session_date=body.session_date,
        now=generated_at,
        latitude=body.latitude,
        longitude=body.longitude,
        radius_meters=body.radius_meters,
        require_location=body.require_location,
        created_by=body.created_by,
    )
    return AttendanceQRSessionResponse(
        session_id=result.session_id,
        schedule_id=result.schedule_id,
        kode_mk=result.kode_mk,
        nama_mk=result.nama_mk,
        nama_kelas=result.nama_kelas,
        semester=result.semester,
        hari=result.hari,
        nama_hari=result.nama_hari,
        jam_mulai=result.jam_mulai,
        jam_selesai=result.jam_selesai,
        ruangan_kode=result.ruangan_kode,
        dosen_nama=result.dosen_nama,
        session_date=result.session_date,
        pertemuan_ke=result.pertemuan_ke,
        active_jti=result.active_jti,
        session_started_at=result.session_started_at,
        qr_generated_at=result.qr_generated_at,
        qr_expires_at=result.qr_expires_at,
        valid_minutes=result.valid_minutes,
        token=result.token,
        qr_base64=result.qr_base64,
        require_location=result.require_location,
        latitude=result.latitude,
        longitude=result.longitude,
        radius_meters=result.radius_meters,
    )


@router.get(
    "/sessions",
    response_model=list[AttendanceSessionItemResponse],
    summary="Daftar sesi presensi yang pernah dibuat",
)
def list_attendance_sessions(
    schedule_id: int | None = Query(default=None),
    kode_mk: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return _service.list_sessions(db, schedule_id=schedule_id, kode_mk=kode_mk)


@router.post(
    "/sessions/{session_id}/close",
    response_model=AttendanceCloseSessionResponse,
    summary="Tutup sesi presensi dan tandai mahasiswa yang tidak scan sebagai alpha",
)
def close_attendance_session(
    session_id: int,
    mark_absent: bool = Query(default=True),
    _: str = Depends(_require_lecturer_role),
    db: Session = Depends(get_db),
):
    return _service.close_session(db, session_id, mark_absent=mark_absent)


@router.post(
    "/scan",
    response_model=AttendanceScanResponse,
    summary="Mahasiswa scan QR presensi (validasi JWT, jadwal, enrollment, duplicate, status & rate limit)",
    responses={
        200: {"description": "Presensi berhasil dicatat (hadir atau terlambat)"},
        400: {"description": "Di luar jadwal/window waktu valid atau lokasi di luar radius"},
        401: {"description": "QR expired, token tidak valid, atau replay attack"},
        403: {"description": "Mahasiswa tidak terdaftar pada MK/kelas ini"},
        404: {"description": "Jadwal atau NIM mahasiswa tidak ditemukan"},
        409: {"description": "Mahasiswa sudah melakukan scan pada sesi ini"},
        429: {"description": "Terlalu banyak percobaan scan (rate limit)"},
    },
)
def scan_attendance_qr(
    payload: AttendanceScanRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    client_host = request.client.host if request.client else "unknown"
    rate_key = f"{client_host}:{payload.nim.strip()}"
    get_scan_rate_limiter().check_and_record(rate_key)

    return _service.record_scan(
        db,
        token=payload.token,
        nim=payload.nim,
        now=payload.scanned_at,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )


@router.get(
    "/report/student/{nim}",
    response_model=StudentAttendanceReportResponse,
    summary="Laporan persentase kehadiran per MK untuk satu mahasiswa",
)
def get_student_report(
    nim: str,
    semester: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return _service.get_student_attendance_report(db, nim, semester=semester)


@router.get(
    "/report/course/{kode_mk}",
    response_model=CourseAttendanceReportResponse,
    summary="Laporan kehadiran per MK beserta daftar warning mahasiswa (<75%)",
)
def get_course_report(
    kode_mk: str,
    semester: str | None = Query(default=None),
    kelas_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    report = _service.get_course_attendance_report(
        db, kode_mk, semester=semester, kelas_id=kelas_id
    )
    report.pop("detailed_logs", None)
    return report


@router.get(
    "/report/course/{kode_mk}/export",
    summary="Export laporan presensi mata kuliah ke file Excel (.xlsx) untuk dosen",
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
            "description": "File Excel (.xlsx) berisi Rekap Presensi, Warning (<75%), dan Log Detail",
        }
    },
)
def export_course_report_excel(
    kode_mk: str,
    semester: str | None = Query(default=None),
    kelas_id: int | None = Query(default=None),
    _: str = Depends(_require_lecturer_role),
    db: Session = Depends(get_db),
) -> Response:
    report = _service.get_course_attendance_report(
        db, kode_mk, semester=semester, kelas_id=kelas_id
    )
    xlsx_bytes = export_course_attendance_excel(report)
    filename = f"presensi_{report['kode_mk']}_{report.get('semester') or 'all'}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post(
    "/ai-insights",
    response_model=AIAttendanceInsightsResponse,
    summary="AI Attendance Analyzer dari array JSON attendance_records (structured insights + actions)",
)
def post_ai_attendance_insights(payload: AIAttendanceAnalyzeRequest):
    return analyze_attendance_records(payload.attendance_records)


@router.get(
    "/ai-insights",
    response_model=AIAttendanceInsightsResponse,
    summary="AI Attendance Analyzer langsung dari data presensi di database",
)
def get_ai_attendance_insights(
    kode_mk: str | None = Query(default=None),
    semester: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return analyze_attendance_from_db(db, kode_mk=kode_mk, semester=semester)
