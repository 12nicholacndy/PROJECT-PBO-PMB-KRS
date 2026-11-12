from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import RegisterRequest, RegisterResponse
from app.services.pmb_service import (
    EmailSudahTerdaftar,
    ReferensiTidakValid,
    register_pendaftar,
)

router = APIRouter(prefix="/api/pmb", tags=["PMB"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mendaftarkan calon mahasiswa baru",
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
