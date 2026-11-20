from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    AISuggestionItemResponse,
    AISuggestionListResponse,
    ConflictItemResponse,
    ConflictListResponse,
    DeleteJadwalResponse,
    JadwalRequest,
    JadwalResponse,
    NotificationLogResponse,
    OutboxAuditResponse,
    OutboxEventResponse,
)
from app.services.ai_rescheduler import (
    AIRescheduler,
    LLMClient,
    build_llm_client_from_env,
)
from app.services.ical_exporter import export_jadwals_to_ics
from app.services.schedule_observers import get_default_channels
from app.services.scheduling_service import JadwalData, SchedulingService

router = APIRouter(prefix="/api/jadwal", tags=["Jadwal & Ruangan"])


def require_admin_akademik(
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> str:
    """Dependensi otorisasi peran: hanya admin_akademik yang boleh mengakses endpoint admin."""
    if (x_role or "").strip().lower() != "admin_akademik":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak: hanya admin_akademik yang diizinkan",
        )
    return "admin_akademik"


def get_llm_client() -> LLMClient:
    """Dependensi LLMClient (dapat di-override dengan FakeLLMClient saat pengujian)."""
    return build_llm_client_from_env()


def _to_jadwal_data(payload: JadwalRequest) -> JadwalData:
    return JadwalData(
        kelas_id=payload.kelas_id,
        ruangan_kode=payload.ruangan_kode,
        dosen_id=payload.dosen_id,
        hari=payload.hari,
        jam_mulai=payload.jam_mulai,
        jam_selesai=payload.jam_selesai,
    )


@router.get(
    "/conflicts",
    response_model=ConflictListResponse,
    status_code=status.HTTP_200_OK,
    summary="Daftar seluruh bentrok jadwal (ruangan & dosen) di database (Admin)",
    responses={403: {"description": "Bukan admin_akademik"}},
)
def get_conflicts(
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin_akademik),
):
    service = SchedulingService(db)
    conflicts = service.detect_all_conflicts()
    return ConflictListResponse(
        total=len(conflicts),
        conflicts=[
            ConflictItemResponse(type=c.type, affected=list(c.affected))
            for c in conflicts
        ],
    )


@router.get(
    "/outbox",
    response_model=OutboxAuditResponse,
    status_code=status.HTTP_200_OK,
    summary="Audit Transactional Outbox dan log simulasi 3 kanal notifikasi (Admin)",
    responses={403: {"description": "Bukan admin_akademik"}},
)
def get_outbox_audit(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin_akademik),
):
    service = SchedulingService(db)
    events = service.list_outbox_events(limit=limit)
    recent_logs: list[NotificationLogResponse] = []
    for channel in get_default_channels():
        for rec in channel.records[-limit:]:
            recent_logs.append(NotificationLogResponse(**rec))
    return OutboxAuditResponse(
        events=[OutboxEventResponse.model_validate(e) for e in events],
        recent_channel_logs=recent_logs[-limit:],
    )


@router.get(
    "/export.ics",
    status_code=status.HTTP_200_OK,
    summary="Ekspor jadwal kuliah dalam format iCalendar (.ics / RFC 5545)",
    responses={200: {"content": {"text/calendar": {}}}},
)
def export_jadwal_ics(
    nim: str | None = Query(default=None, description="Filter jadwal berdasarkan NIM mahasiswa"),
    semester: str | None = Query(default=None, description="Filter jadwal berdasarkan kode semester"),
    semester_start: date = Query(default=date(2026, 9, 1)),
    semester_end: date = Query(default=date(2026, 12, 20)),
    db: Session = Depends(get_db),
):
    service = SchedulingService(db)
    jadwals = service.list_jadwal(nim=nim, semester=semester)
    ics_content = export_jadwals_to_ics(
        jadwals,
        semester_start=semester_start,
        semester_end=semester_end,
    )
    filename = f"jadwal_{nim}.ics" if nim else "jadwal_kuliah.ics"
    return Response(
        content=ics_content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "",
    response_model=list[JadwalResponse],
    status_code=status.HTTP_200_OK,
    summary="Menampilkan daftar seluruh jadwal kuliah",
)
def list_jadwal(
    nim: str | None = Query(default=None),
    semester: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    service = SchedulingService(db)
    return service.list_jadwal(nim=nim, semester=semester)


@router.post(
    "",
    response_model=JadwalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Membuat jadwal kuliah baru dengan validasi bentrok 3D & kapasitas ruangan (Admin)",
    responses={
        400: {"description": "Format input tidak valid (mis. jam_selesai <= jam_mulai)"},
        403: {"description": "Bukan admin_akademik"},
        404: {"description": "Kelas, ruangan, atau dosen tidak ditemukan"},
        409: {"description": "Jadwal bentrok (ruangan/dosen) atau kapasitas ruangan tidak mencukupi"},
    },
)
def create_jadwal(
    payload: JadwalRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin_akademik),
):
    service = SchedulingService(db)
    return service.create(_to_jadwal_data(payload))


@router.post(
    "/{jadwal_id}/suggest",
    response_model=AISuggestionListResponse,
    status_code=status.HTTP_200_OK,
    summary="Meminta saran jadwal pengganti dari AI Scheduling Assistant (4 lapis guardrail, Admin)",
    responses={
        403: {"description": "Bukan admin_akademik"},
        404: {"description": "Jadwal tidak ditemukan"},
    },
)
def suggest_reschedule(
    jadwal_id: int,
    db: Session = Depends(get_db),
    llm_client: LLMClient = Depends(get_llm_client),
    _admin: str = Depends(require_admin_akademik),
):
    rescheduler = AIRescheduler(db, llm_client)
    target, available_slots, suggestions = rescheduler.suggest_alternatives(jadwal_id)
    return AISuggestionListResponse(
        jadwal_id=target.id,
        kelas_id=target.kelas_id,
        dosen_id=target.dosen_id,
        available_slots_count=len(available_slots),
        suggestions=[
            AISuggestionItemResponse(
                hari=s.hari,
                jam_mulai=s.jam_mulai,
                jam_selesai=s.jam_selesai,
                ruangan_kode=s.ruangan_kode,
                alasan=s.alasan,
            )
            for s in suggestions
        ],
    )


@router.put(
    "/{jadwal_id}",
    response_model=JadwalResponse,
    status_code=status.HTTP_200_OK,
    summary="Memperbarui jadwal kuliah (validasi 3D, invalidasi KRS, outbox & notifikasi, Admin)",
    responses={
        400: {"description": "Format input tidak valid"},
        403: {"description": "Bukan admin_akademik"},
        404: {"description": "Jadwal, kelas, ruangan, atau dosen tidak ditemukan"},
        409: {"description": "Jadwal bentrok (ruangan/dosen) atau kapasitas ruangan tidak mencukupi"},
    },
)
def update_jadwal(
    jadwal_id: int,
    payload: JadwalRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin_akademik),
):
    service = SchedulingService(db)
    return service.update(jadwal_id, _to_jadwal_data(payload))


@router.delete(
    "/{jadwal_id}",
    response_model=DeleteJadwalResponse,
    status_code=status.HTTP_200_OK,
    summary="Menghapus jadwal kuliah (invalidasi KRS, outbox & notifikasi dalam 1 transaksi, Admin)",
    responses={
        403: {"description": "Bukan admin_akademik"},
        404: {"description": "Jadwal tidak ditemukan"},
    },
)
def delete_jadwal(
    jadwal_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(require_admin_akademik),
):
    service = SchedulingService(db)
    service.delete(jadwal_id)
    return DeleteJadwalResponse(
        deleted_id=jadwal_id,
        message=f"Jadwal id={jadwal_id} berhasil dihapus",
    )
