from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    ApproveResponse,
    RegisterRequest,
    RegisterResponse,
    StatsResponse,
    StatusResponse,
)
from app.services.pmb_service import (
    EmailSudahTerdaftar,
    NIMTidakValid,
    PendaftarTidakDitemukan,
    ReferensiTidakValid,
    StatusTidakValid,
    approve_pendaftar,
    get_pmb_stats,
    get_status_pendaftar,
    register_pendaftar,
    reject_pendaftar,
)

router = APIRouter(prefix="/api/pmb", tags=["PMB"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mendaftarkan calon mahasiswa baru",
    responses={
        400: {"description": "Input tidak valid atau program studi / jalur masuk tidak ditemukan"},
        409: {"description": "Email sudah terdaftar"},
    },
)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    try:
        return register_pendaftar(db, data)
    except EmailSudahTerdaftar as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ReferensiTidakValid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.put(
    "/approve/{pendaftar_id}",
    response_model=ApproveResponse,
    status_code=status.HTTP_200_OK,
    summary="Menyetujui pendaftar dan menghasilkan NIM (idempoten)",
    responses={
        400: {"description": "Data pendaftar tidak valid untuk pembuatan NIM"},
        404: {"description": "Pendaftar tidak ditemukan"},
        409: {"description": "Pendaftar berstatus rejected tidak dapat disetujui"},
    },
)
def approve(pendaftar_id: int, db: Session = Depends(get_db)):
    try:
        return approve_pendaftar(db, pendaftar_id)
    except PendaftarTidakDitemukan as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except StatusTidakValid as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except NIMTidakValid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.put(
    "/reject/{pendaftar_id}",
    response_model=ApproveResponse,
    status_code=status.HTTP_200_OK,
    summary="Menolak pendaftar berstatus pending (idempoten)",
    responses={
        404: {"description": "Pendaftar tidak ditemukan"},
        409: {"description": "Pendaftar yang sudah disetujui tidak dapat ditolak"},
    },
)
def reject(pendaftar_id: int, db: Session = Depends(get_db)):
    try:
        return reject_pendaftar(db, pendaftar_id)
    except PendaftarTidakDitemukan as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except StatusTidakValid as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get(
    "/status/{pendaftar_id}",
    response_model=StatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Melihat status pendaftaran dan NIM calon mahasiswa",
    responses={
        404: {"description": "Pendaftar tidak ditemukan"},
    },
)
def get_status(pendaftar_id: int, db: Session = Depends(get_db)):
    try:
        return get_status_pendaftar(db, pendaftar_id)
    except PendaftarTidakDitemukan as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/stats",
    response_model=StatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Melihat statistik jumlah pendaftar per status, program studi, dan jalur masuk",
)
def get_stats(db: Session = Depends(get_db)):
    return get_pmb_stats(db)
