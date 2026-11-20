from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Admin Conflict Resolver UI"])

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "conflict_resolver.html"


@router.get(
    "/admin/conflict-resolver",
    response_class=HTMLResponse,
    summary="Dashboard Admin Conflict Resolver (HTML + JS memanggil API Jadwal)",
)
def conflict_resolver_dashboard() -> HTMLResponse:
    return HTMLResponse(content=TEMPLATE_PATH.read_text(encoding="utf-8"))
