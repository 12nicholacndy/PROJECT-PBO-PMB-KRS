from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    ConflictItemResponse,
    ConflictListResponse,
    DeleteJadwalResponse,
    JadwalRequest,
    JadwalResponse,
)
from app.services.scheduling_service import JadwalData, SchedulingService

router = APIRouter(prefix="/api/jadwal", tags=["Jadwal & Ruangan"])


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
    summary="Daftar seluruh bentrok jadwal (ruangan & dosen) di database",
)
def get_conflicts(db: Session = Depends(get_db)):
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
    "",
    response_model=list[JadwalResponse],
    status_code=status.HTTP_200_OK,
    summary="Menampilkan daftar seluruh jadwal kuliah",
)
def list_jadwal(db: Session = Depends(get_db)):
    service = SchedulingService(db)
    return service.list_jadwal()


@router.post(
    "",
    response_model=JadwalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Membuat jadwal kuliah baru dengan validasi bentrok 3D & kapasitas ruangan",
    responses={
        400: {"description": "Format input tidak valid (mis. jam_selesai <= jam_mulai)"},
        404: {"description": "Kelas, ruangan, atau dosen tidak ditemukan"},
        409: {"description": "Jadwal bentrok (ruangan/dosen) atau kapasitas ruangan tidak mencukupi"},
    },
)
def create_jadwal(payload: JadwalRequest, db: Session = Depends(get_db)):
    service = SchedulingService(db)
    return service.create(_to_jadwal_data(payload))


@router.put(
    "/{jadwal_id}",
    response_model=JadwalResponse,
    status_code=status.HTTP_200_OK,
    summary="Memperbarui jadwal kuliah dengan validasi bentrok 3D & kapasitas ruangan",
    responses={
        400: {"description": "Format input tidak valid"},
        404: {"description": "Jadwal, kelas, ruangan, atau dosen tidak ditemukan"},
        409: {"description": "Jadwal bentrok (ruangan/dosen) atau kapasitas ruangan tidak mencukupi"},
    },
)
def update_jadwal(
    jadwal_id: int, payload: JadwalRequest, db: Session = Depends(get_db)
):
    service = SchedulingService(db)
    return service.update(jadwal_id, _to_jadwal_data(payload))


@router.delete(
    "/{jadwal_id}",
    response_model=DeleteJadwalResponse,
    status_code=status.HTTP_200_OK,
    summary="Menghapus jadwal kuliah dalam satu transaksi",
    responses={
        404: {"description": "Jadwal tidak ditemukan"},
    },
)
def delete_jadwal(jadwal_id: int, db: Session = Depends(get_db)):
    service = SchedulingService(db)
    service.delete(jadwal_id)
    return DeleteJadwalResponse(
        deleted_id=jadwal_id,
        message=f"Jadwal id={jadwal_id} berhasil dihapus",
    )
