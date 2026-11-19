from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import KRS
from app.schemas import (
    AddCourseRequest,
    ApproveKRSRequest,
    KRSDetailResponse,
    KRSLogResponse,
    KRSRecommendRequest,
    KRSRecommendResponse,
    KRSResponse,
    KRSValidateResponse,
    MataKuliahResponse,
    NotifikasiResponse,
    ReviseKRSRequest,
    SubmitKRSRequest,
)
from app.services.krs_manager import KRSManager
from app.services.krs_recommender import recommend_krs_schedule

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
        version=krs.version,
        catatan_revisi=krs.catatan_revisi,
        submitted_at=krs.submitted_at,
        approved_at=krs.approved_at,
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
    return KRSManager(db).list_matakuliah()


@router.post(
    "/courses",
    response_model=KRSResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Menambahkan mata kuliah ke KRS mahasiswa",
    responses={
        400: {"description": "Gagal validasi KRS (duplikat, SKS > 24, prasyarat, atau jadwal bentrok)"},
        404: {"description": "NIM mahasiswa atau mata kuliah tidak ditemukan"},
        409: {"description": "KRS terkunci (submitted/approved) sehingga tidak dapat diubah"},
    },
)
def add_course(data: AddCourseRequest, db: Session = Depends(get_db)):
    krs = KRSManager(db).add_course(
        data.nim,
        data.kode_mk,
        data.semester,
        dosen_pa_id=data.dosen_pa_id,
    )
    return _to_krs_response(krs)


@router.delete(
    "/{nim}/{semester}/courses/{kode_mk}",
    response_model=KRSResponse,
    status_code=status.HTTP_200_OK,
    summary="Menghapus mata kuliah dari KRS mahasiswa",
    responses={
        404: {"description": "KRS atau mata kuliah tidak ditemukan di dalam KRS"},
        409: {"description": "KRS terkunci (submitted/approved) sehingga tidak dapat diubah"},
    },
)
def remove_course(
    nim: str,
    semester: str,
    kode_mk: str,
    db: Session = Depends(get_db),
):
    krs = KRSManager(db).remove_course(nim, kode_mk, semester)
    return _to_krs_response(krs)


@router.get(
    "/{nim}/notifikasi",
    response_model=list[NotifikasiResponse],
    status_code=status.HTTP_200_OK,
    summary="Melihat daftar notifikasi mahasiswa",
    responses={404: {"description": "Mahasiswa tidak ditemukan"}},
)
def get_notifikasi(nim: str, db: Session = Depends(get_db)):
    return KRSManager(db).get_notifications(nim)


@router.get(
    "/{nim}/{semester}",
    response_model=KRSResponse,
    status_code=status.HTTP_200_OK,
    summary="Melihat detail KRS mahasiswa pada semester tertentu",
    responses={404: {"description": "KRS tidak ditemukan"}},
)
def get_krs(nim: str, semester: str, db: Session = Depends(get_db)):
    krs = KRSManager(db).get_krs(nim, semester)
    return _to_krs_response(krs)


@router.get(
    "/{nim}/{semester}/validate",
    response_model=KRSValidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Memvalidasi seluruh aturan KRS dan mengumpulkan semua kesalahan sekaligus",
    responses={404: {"description": "KRS tidak ditemukan"}},
)
def validate_krs(nim: str, semester: str, db: Session = Depends(get_db)):
    return KRSManager(db).validate_krs(nim, semester)


@router.post(
    "/{nim}/{semester}/submit",
    response_model=KRSResponse,
    status_code=status.HTTP_200_OK,
    summary="Mengajukan KRS (DRAFT/REVISION -> SUBMITTED)",
    responses={
        400: {"description": "KRS kosong atau tidak lolos validasi"},
        403: {"description": "Mahasiswa tidak berwenang mengajukan KRS ini"},
        404: {"description": "KRS tidak ditemukan"},
        409: {"description": "Transisi status tidak diizinkan"},
    },
)
def submit_krs(
    nim: str,
    semester: str,
    body: SubmitKRSRequest | None = Body(default=None),
    db: Session = Depends(get_db),
):
    requester_nim = body.requester_nim if body is not None else None
    krs = KRSManager(db).submit_krs(nim, semester, requester_nim=requester_nim)
    return _to_krs_response(krs)


@router.post(
    "/{nim}/{semester}/approve",
    response_model=KRSResponse,
    status_code=status.HTTP_200_OK,
    summary="Menyetujui KRS oleh Dosen PA (SUBMITTED -> APPROVED) dalam satu transaksi",
    responses={
        403: {"description": "Bukan Dosen PA yang berwenang"},
        404: {"description": "KRS tidak ditemukan"},
        409: {"description": "Transisi status tidak diizinkan atau versi KRS sudah berubah"},
    },
)
def approve_krs(
    nim: str,
    semester: str,
    body: ApproveKRSRequest,
    db: Session = Depends(get_db),
):
    krs = KRSManager(db).approve_krs(
        nim,
        semester,
        dosen_pa_id=body.dosen_pa_id,
        expected_version=body.version,
    )
    return _to_krs_response(krs)


@router.post(
    "/{nim}/{semester}/revise",
    response_model=KRSResponse,
    status_code=status.HTTP_200_OK,
    summary="Mengembalikan KRS untuk revisi oleh Dosen PA (SUBMITTED -> REVISION)",
    responses={
        400: {"description": "Catatan revisi kosong"},
        403: {"description": "Bukan Dosen PA yang berwenang"},
        404: {"description": "KRS tidak ditemukan"},
        409: {"description": "Transisi status tidak diizinkan atau versi KRS sudah berubah"},
    },
)
def revise_krs(
    nim: str,
    semester: str,
    body: ReviseKRSRequest,
    db: Session = Depends(get_db),
):
    krs = KRSManager(db).revise_krs(
        nim,
        semester,
        dosen_pa_id=body.dosen_pa_id,
        catatan=body.catatan,
        expected_version=body.version,
    )
    return _to_krs_response(krs)


@router.get(
    "/{nim}/{semester}/logs",
    response_model=list[KRSLogResponse],
    status_code=status.HTTP_200_OK,
    summary="Melihat riwayat audit perubahan status KRS",
    responses={404: {"description": "KRS tidak ditemukan"}},
)
def get_krs_logs(nim: str, semester: str, db: Session = Depends(get_db)):
    return KRSManager(db).get_logs(nim, semester)


@router.post(
    "/{nim}/{semester}/recommend",
    response_model=KRSRecommendResponse,
    status_code=status.HTTP_200_OK,
    summary="Challenge Opsional (+10%): Rekomendasi jadwal KRS optimal (CSP Backtracking)",
    responses={
        400: {"description": "Input rekomendasi tidak valid"},
        404: {"description": "Mahasiswa atau mata kuliah tidak ditemukan"},
    },
)
def recommend_krs(
    nim: str,
    semester: str,
    body: KRSRecommendRequest,
    db: Session = Depends(get_db),
):
    return recommend_krs_schedule(
        db=db,
        nim=nim,
        semester=semester,
        wajib_codes=body.wajib_codes,
        pilihan_codes=body.pilihan_codes,
        max_sks=body.max_sks,
    )
