from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    AcademicSummaryResponse,
    GradeHistoryItemResponse,
    GradeInputRequest,
    GradeInputResponse,
    GradeItemResponse,
    GradeUpdateRequest,
    GradeUpdateResponse,
    IPKResponse,
    IPSResponse,
    StudentOptionResponse,
    TranscriptResponse,
)
from app.services.gpa_calculator import (
    calculate_ipk,
    calculate_ips,
    get_transcript,
)
from app.services.grade_errors import GradeForbiddenError
from app.services.grade_service import GradeService
from app.services.transcript_pdf import (
    generate_transcript_pdf,
    render_transcript_html,
)

router = APIRouter(prefix="/api/grades", tags=["Nilai & Transkrip"])

_service = GradeService()
_ALLOWED_GRADE_ROLES = {"dosen", "dosen_pa", "admin_akademik"}


def _require_grader_role(
    x_role: str = Header(default="dosen", alias="X-Role"),
) -> str:
    role = x_role.strip().lower()
    if role not in _ALLOWED_GRADE_ROLES:
        raise GradeForbiddenError(
            f"Peran '{x_role}' tidak diizinkan menginput atau mengubah nilai "
            f"(hanya dosen atau admin_akademik)"
        )
    return role


@router.get(
    "/students",
    response_model=list[StudentOptionResponse],
    summary="Daftar mahasiswa berstatus approved beserta ringkasan IPK",
)
def list_students(db: Session = Depends(get_db)):
    return [
        StudentOptionResponse(**asdict(item))
        for item in _service.list_approved_students(db)
    ]


@router.post(
    "",
    response_model=GradeInputResponse,
    status_code=201,
    summary="Input nilai mahasiswa per mata kuliah (validasi presensi >= 75% & auto-update IPK)",
)
def create_grade(
    payload: GradeInputRequest,
    _: str = Depends(_require_grader_role),
    db: Session = Depends(get_db),
):
    grade, summary = _service.input_grade(
        db,
        nim=payload.nim,
        kode_mk=payload.kode_mk,
        semester=payload.semester,
        nilai_huruf=payload.nilai_huruf,
        presensi_persen=payload.presensi_persen,
        created_by=payload.created_by,
    )
    return GradeInputResponse(
        grade=GradeItemResponse(**asdict(grade)),
        summary=AcademicSummaryResponse(**asdict(summary)),
    )


@router.put(
    "/{grade_id}",
    response_model=GradeUpdateResponse,
    summary="Edit nilai mahasiswa dengan trigger audit trail otomatis ke grade_history",
)
def update_grade(
    grade_id: int,
    payload: GradeUpdateRequest,
    _: str = Depends(_require_grader_role),
    db: Session = Depends(get_db),
):
    grade, summary, history = _service.update_grade(
        db,
        grade_id=grade_id,
        new_nilai_huruf=payload.nilai_huruf,
        changed_by=payload.changed_by,
        reason=payload.reason,
        presensi_persen=payload.presensi_persen,
    )
    return GradeUpdateResponse(
        grade=GradeItemResponse(**asdict(grade)),
        summary=AcademicSummaryResponse(**asdict(summary)),
        history=GradeHistoryItemResponse(**asdict(history)),
    )


@router.get(
    "",
    response_model=list[GradeItemResponse],
    summary="Daftar nilai mahasiswa (filter opsional berdasarkan NIM dan semester)",
)
def list_grades(
    nim: str | None = Query(default=None),
    semester: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return [
        GradeItemResponse(**asdict(item))
        for item in _service.list_grades(db, nim=nim, semester=semester)
    ]


@router.get(
    "/{nim}/ips/{semester}",
    response_model=IPSResponse,
    summary="Hitung Indeks Prestasi Semester (IPS) mahasiswa",
)
def get_ips(nim: str, semester: str, db: Session = Depends(get_db)):
    ips = calculate_ips(db, nim, semester)
    return IPSResponse(nim=nim, semester=semester, ips=ips)


@router.get(
    "/{nim}/ipk",
    response_model=IPKResponse,
    summary="Hitung Indeks Prestasi Kumulatif (IPK), Total SKS Lulus, & Predikat",
)
def get_ipk(nim: str, db: Session = Depends(get_db)):
    ipk = calculate_ipk(db, nim)
    summary = _service.get_academic_summary(db, nim)
    return IPKResponse(
        nim=nim,
        ipk=ipk,
        total_sks_lulus=summary.total_sks_lulus,
        predikat=summary.predikat,
        updated_at=summary.updated_at,
    )


@router.get(
    "/{nim}/history",
    response_model=list[GradeHistoryItemResponse],
    summary="Lihat riwayat perubahan nilai (audit trail grade_history) per mahasiswa",
)
def get_grade_history(nim: str, db: Session = Depends(get_db)):
    return [
        GradeHistoryItemResponse(**asdict(item))
        for item in _service.get_grade_history(db, nim)
    ]


@router.get(
    "/{nim}/transcript",
    response_model=TranscriptResponse,
    summary="Ambil catatan akademik lengkap (transkrip) mahasiswa dalam format JSON",
)
def get_transcript_json(nim: str, db: Session = Depends(get_db)):
    transcript = get_transcript(db, nim)
    return TranscriptResponse(**asdict(transcript))


@router.get(
    "/{nim}/transcript/html",
    response_class=HTMLResponse,
    summary="Preview Transkrip Akademik dalam format HTML + CSS profesional",
)
def get_transcript_html(nim: str, db: Session = Depends(get_db)):
    transcript = get_transcript(db, nim)
    return HTMLResponse(content=render_transcript_html(transcript))


@router.get(
    "/{nim}/transcript/pdf",
    summary="Generate dan download Transkrip Akademik resmi dalam format PDF",
)
def get_transcript_pdf(nim: str, db: Session = Depends(get_db)):
    transcript = get_transcript(db, nim)
    pdf_bytes = generate_transcript_pdf(transcript)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="transkrip_{nim}.pdf"'
        },
    )
