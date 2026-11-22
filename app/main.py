from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers.admin_ui import router as admin_ui_router
from app.routers.attendance import router as attendance_router
from app.routers.finance import router as finance_router
from app.routers.grades import router as grades_router
from app.routers.jadwal import router as jadwal_router
from app.routers.krs import router as krs_router
from app.routers.pmb import router as pmb_router
from app.services.attendance_errors import AttendanceError
from app.services.billing_scheduler import get_billing_scheduler
from app.services.finance_errors import FinanceError
from app.services.grade_errors import GradeError
from app.services.krs_errors import KRSError
from app.services.schedule_errors import ScheduleError
from app.services.schedule_observers import init_default_schedule_subject


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Observer dirangkai SEKALI saat aplikasi start untuk mencegah memory leak & notifikasi ganda.
    init_default_schedule_subject()
    scheduler = get_billing_scheduler()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(
    title="Sistem Informasi Akademik Mini (PMB, KRS, Jadwal, Nilai, Keuangan SPP & Presensi QR)",
    description=(
        "API Modul PMB (Minggu 3–4), Modul KRS dengan State Machine & CSP (Minggu 5–6), "
        "Modul Jadwal & Ruangan dengan Deteksi Bentrok 3D, Observer/Outbox, AI Rescheduler "
        "& iCalendar (Minggu 7–8), Modul Nilai, GPA Calculator, Audit Trail & "
        "Generator Transkrip PDF (Minggu 9–10), Modul Keuangan SPP, APScheduler, "
        "Payment Webhook, Dashboard Real-Time, Block KRS & AI Financial Report (Minggu 11), "
        "serta Modul Presensi QR Code dengan JWT Time-Based Auth, Geolocation, Rate Limiting, "
        "Export Excel & AI Attendance Insights (Minggu 12)"
    ),
    version="2.5.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    # Materi dosen menyebut keputusan mengubah validasi bawaan FastAPI
    # (422) menjadi 400 agar sesuai kontrak tugas.
    errors = []
    for error in exc.errors():
        errors.append({
            "field": ".".join(str(part) for part in error.get("loc", [])),
            "message": error.get("msg", "Input tidak valid"),
        })
    return JSONResponse(
        status_code=400,
        content={"detail": "Input tidak valid", "errors": errors},
    )


@app.exception_handler(KRSError)
async def krs_exception_handler(request: Request, exc: KRSError):
    # Polimorfisme: satu handler untuk KRSError dan seluruh turunannya
    # (400 validasi, 403 forbidden, 404 not found, 409 transition/locked).
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
    )


@app.exception_handler(ScheduleError)
async def schedule_exception_handler(request: Request, exc: ScheduleError):
    # Polimorfisme: satu handler untuk ScheduleError dan seluruh turunannya
    # (404 not found, 409 conflict dengan detail daftar bentrok, 409 kapasitas ruangan).
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.body(),
    )


@app.exception_handler(GradeError)
async def grade_exception_handler(request: Request, exc: GradeError):
    # Polimorfisme: satu handler untuk GradeError dan seluruh turunannya
    # (400 validasi/presensi <75%, 403 forbidden, 404 not found, 409 duplikat).
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.body(),
    )


@app.exception_handler(FinanceError)
async def finance_exception_handler(request: Request, exc: FinanceError):
    # Polimorfisme: satu handler untuk FinanceError dan seluruh turunannya
    # (400 validasi, 401 webhook signature invalid, 403 forbidden, 404 not found, 409 konflik).
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.body(),
    )


@app.exception_handler(AttendanceError)
async def attendance_exception_handler(request: Request, exc: AttendanceError):
    # Polimorfisme: satu handler untuk AttendanceError dan seluruh turunannya
    # (400 jadwal/lokasi/window, 401 QR expired/replay, 403 bukan kelasnya, 404 not found, 409 duplikat, 429 rate limit).
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.body(),
        headers=exc.headers(),
    )


@app.get("/", tags=["Informasi"])
def home():
    return {"message": "Sistem Informasi Akademik Mini berjalan"}


app.include_router(pmb_router)
app.include_router(krs_router)
app.include_router(jadwal_router)
app.include_router(grades_router)
app.include_router(finance_router)
app.include_router(attendance_router)
app.include_router(admin_ui_router)
