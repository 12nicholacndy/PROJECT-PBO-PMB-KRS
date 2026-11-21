from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers.admin_ui import router as admin_ui_router
from app.routers.grades import router as grades_router
from app.routers.jadwal import router as jadwal_router
from app.routers.krs import router as krs_router
from app.routers.pmb import router as pmb_router
from app.services.grade_errors import GradeError
from app.services.krs_errors import KRSError
from app.services.schedule_errors import ScheduleError
from app.services.schedule_observers import init_default_schedule_subject


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Observer dirangkai SEKALI saat aplikasi start untuk mencegah memory leak & notifikasi ganda.
    init_default_schedule_subject()
    yield


app = FastAPI(
    title="Sistem Informasi Akademik Mini (PMB, KRS, Jadwal & Nilai/Transkrip)",
    description=(
        "API Modul PMB (Minggu 3–4), Modul KRS dengan State Machine & CSP (Minggu 5–6), "
        "Modul Jadwal & Ruangan dengan Deteksi Bentrok 3D, Observer/Outbox, AI Rescheduler "
        "& iCalendar (Minggu 7–8), serta Modul Nilai, GPA Calculator, Audit Trail & "
        "Generator Transkrip PDF (Minggu 9–10)"
    ),
    version="2.3.0",
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


@app.get("/", tags=["Informasi"])
def home():
    return {"message": "Sistem Informasi Akademik Mini berjalan"}


app.include_router(pmb_router)
app.include_router(krs_router)
app.include_router(jadwal_router)
app.include_router(grades_router)
app.include_router(admin_ui_router)
