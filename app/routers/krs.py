from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import KRS
from app.schemas import (
    AddCourseRequest,
    KRSDetailResponse,
    KRSResponse,
    KRSValidateResponse,
    MataKuliahResponse,
)
from app.services.krs_manager import (
    KRSManager,
    KRSNotFoundError,
    KRSStatusError,
    KRSValidationError,
)

router = APIRouter(prefix="/api/krs", tags=["KRS"])


def _to_krs_response(krs: KRS) -> KRSResponse:
    courses = [
        KRSDetailResponse(
            kode_mk=detail.matakuliah.kode_mk,
            nama=detail.matakuliah.nama,
            sks=detail.matakuliah.sks,
            hari=detail.matakuliah.hari,
            jam_mulai=detail.matakuliah.jam_mulai,
            jam_selesai=detail.matakuliah.jam_selesai,
            nilai=detail.nilai,
        )
        for detail in sorted(krs.details, key=lambda d: d.kode_mk)
    ]
    return KRSResponse(
        id=krs.id,
        nim=krs.nim,
        semester=krs.semester,
        status=krs.status.value,
        dosen_pa_id=krs.dosen_pa_id,
        total_sks=sum(course.sks for course in courses),
        courses=courses,
    )


@router.get(
    "/matakuliah",
    response_model=list[MataKuliahResponse],
    status_code=status.HTTP_200_OK,
    summary="Daftar seluruh mata kuliah yang tersedia",
)
def list_matakuliah(db: Session = Depends(get_db)):
    manager = KRSManager(db)
    return manager.list_matakuliah()


@router.post(
    "/courses",
    response_model=KRSResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Menambahkan mata kuliah ke KRS mahasiswa",
    responses={
        400: {"description": "Gagal validasi KRS (duplikat, SKS > 24, prasyarat, atau jadwal bentrok)"},
        404: {"description": "NIM mahasiswa atau mata kuliah tidak ditemukan"},
        409: {"description": "Status KRS tidak mengizinkan perubahan"},
    },
)
def add_course(data: AddCourseRequest, db: Session = Depends(get_db)):
    manager = KRSManager(db)
    try:
        krs = manager.add_course(data.nim, data.kode_mk, data.semester)
        return _to_krs_response(krs)
    except KRSNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except KRSStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except KRSValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete(
    "/{nim}/{semester}/courses/{kode_mk}",
    response_model=KRSResponse,
    status_code=status.HTTP_200_OK,
    summary="Menghapus mata kuliah dari KRS mahasiswa",
    responses={
        404: {"description": "KRS atau mata kuliah tidak ditemukan di dalam KRS"},
        409: {"description": "Status KRS tidak mengizinkan perubahan"},
    },
)
def remove_course(
    nim: str,
    semester: str,
    kode_mk: str,
    db: Session = Depends(get_db),
):
    manager = KRSManager(db)
    try:
        krs = manager.remove_course(nim, kode_mk, semester)
        return _to_krs_response(krs)
    except KRSNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except KRSStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get(
    "/{nim}/{semester}",
    response_model=KRSResponse,
    status_code=status.HTTP_200_OK,
    summary="Melihat detail KRS mahasiswa pada semester tertentu",
    responses={
        404: {"description": "KRS tidak ditemukan"},
    },
)
def get_krs(nim: str, semester: str, db: Session = Depends(get_db)):
    manager = KRSManager(db)
    try:
        krs = manager.get_krs(nim, semester)
        return _to_krs_response(krs)
    except KRSNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/{nim}/{semester}/validate",
    response_model=KRSValidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Memvalidasi seluruh aturan KRS dan mengumpulkan semua kesalahan sekaligus",
    responses={
        404: {"description": "KRS tidak ditemukan"},
    },
)
def validate_krs(nim: str, semester: str, db: Session = Depends(get_db)):
    manager = KRSManager(db)
    try:
        return manager.validate_krs(nim, semester)
    except KRSNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
