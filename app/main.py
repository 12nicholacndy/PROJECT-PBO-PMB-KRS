from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers.krs import router as krs_router
from app.routers.pmb import router as pmb_router
from app.services.krs_errors import KRSError

app = FastAPI(
    title="Sistem Informasi Akademik Mini (PMB & KRS)",
    description="API Modul PMB (Minggu 3–4) dan Modul KRS lengkap dengan State Machine & Rekomendasi CSP (Minggu 5–6)",
    version="2.0.0",
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


@app.get("/", tags=["Informasi"])
def home():
    return {"message": "Sistem PMB berjalan"}


app.include_router(pmb_router)
app.include_router(krs_router)
