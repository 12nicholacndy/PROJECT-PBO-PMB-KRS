from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers.krs import router as krs_router
from app.routers.pmb import router as pmb_router

app = FastAPI(
    title="Sistem Informasi Akademik Mini (PMB & KRS)",
    description="API Modul PMB (Minggu 3 & 4) dan Modul KRS (Minggu 5)",
    version="1.2.0",
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


@app.get("/", tags=["Informasi"])
def home():
    return {"message": "Sistem PMB berjalan"}


app.include_router(pmb_router)
app.include_router(krs_router)
