from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Web UI (Admin & Dosen)"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
CONFLICT_TEMPLATE_PATH = TEMPLATES_DIR / "conflict_resolver.html"
GRADE_TEMPLATE_PATH = TEMPLATES_DIR / "grade_management.html"


@router.get(
    "/admin/conflict-resolver",
    response_class=HTMLResponse,
    summary="Dashboard Admin Conflict Resolver (HTML + JS memanggil API Jadwal)",
)
def conflict_resolver_dashboard() -> HTMLResponse:
    return HTMLResponse(content=CONFLICT_TEMPLATE_PATH.read_text(encoding="utf-8"))


@router.get(
    "/dosen/grades",
    response_class=HTMLResponse,
    summary="Portal Form Web Input & Edit Nilai Dosen + Preview/Download Transkrip PDF",
)
def dosen_grade_portal() -> HTMLResponse:
    return HTMLResponse(content=GRADE_TEMPLATE_PATH.read_text(encoding="utf-8"))
