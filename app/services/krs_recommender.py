from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CalonMahasiswa, MataKuliah
from app.services.krs_errors import KRSNotFoundError, KRSValidationError
from app.services.krs_validators import (
    KRSContext,
    MAX_SKS,
    PrerequisiteValidator,
    build_default_validators,
    chain_validators,
    jadwal_bentrok,
)


@dataclass(frozen=True)
class CourseCandidate:
    course: MataKuliah
    is_wajib: bool


def recommend_krs_schedule(
    db: Session,
    nim: str,
    semester: str,
    wajib_codes: list[str],
    pilihan_codes: list[str] | None = None,
    max_sks: int = MAX_SKS,
) -> dict:
    """Challenge Opsional (+10%): Rekomendasi jadwal KRS optimal berbasis CSP Backtracking.

    Aturan & Guardrail:
    - Total SKS tidak pernah melebihi batas (min(max_sks, MAX_SKS = 24)).
    - Prasyarat dicek lewat PrerequisiteValidator resmi (tidak menebak).
    - Prioritas: jumlah MK wajib terpilih > total SKS wajib > total SKS keseluruhan.
    - Tidak boleh ada jadwal bentrok (rumus irisan interval).
    - Hasil rekomendasi tetap diverifikasi ulang lewat rantai validator resmi
      sebelum dikembalikan.
    """
    if max_sks < 1 or max_sks > MAX_SKS:
        raise KRSValidationError(
            f"Target SKS maksimal harus antara 1 dan {MAX_SKS}"
        )

    mahasiswa = db.scalar(
        select(CalonMahasiswa.id).where(CalonMahasiswa.nim == nim)
    )
    if mahasiswa is None:
        raise KRSNotFoundError(
            f"Mahasiswa dengan NIM {nim} tidak ditemukan atau belum disetujui"
        )

    pilihan_codes = pilihan_codes or []
    wajib_set = list(dict.fromkeys(wajib_codes))
    pilihan_set = [
        kode for kode in dict.fromkeys(pilihan_codes) if kode not in wajib_set
    ]
    all_codes = wajib_set + pilihan_set

    if not all_codes:
        raise KRSValidationError(
            "Daftar mata kuliah wajib atau pilihan tidak boleh kosong"
        )

    found_courses = {
        mk.kode_mk: mk
        for mk in db.scalars(
            select(MataKuliah).where(MataKuliah.kode_mk.in_(all_codes))
        ).all()
    }

    missing_codes = [kode for kode in all_codes if kode not in found_courses]
    if missing_codes:
        raise KRSNotFoundError(
            f"Mata kuliah tidak ditemukan: {', '.join(missing_codes)}"
        )

    # Langkah 1: Filter kandidat dengan PrerequisiteValidator resmi.
    prereq_validator = PrerequisiteValidator()
    eligible: list[CourseCandidate] = []
    skipped: list[dict[str, str]] = []

    for kode in all_codes:
        mk = found_courses[kode]
        ctx = KRSContext(
            nim=nim,
            semester=semester,
            existing_courses=[],
            target_course=mk,
            db=db,
        )
        res = prereq_validator.check(ctx)
        if not res.ok:
            skipped.append(
                {
                    "kode_mk": mk.kode_mk,
                    "nama": mk.nama,
                    "alasan": res.message,
                }
            )
            continue
        eligible.append(
            CourseCandidate(course=mk, is_wajib=kode in wajib_set)
        )

    # Urutkan wajib di depan lalu SKS terbesar agar pruning backtracking cepat.
    eligible.sort(
        key=lambda c: (not c.is_wajib, -c.course.sks, c.course.kode_mk)
    )

    best_selection: list[CourseCandidate] = []
    best_score: tuple[int, int, int] = (-1, -1, -1)

    def backtrack(
        index: int,
        chosen: list[CourseCandidate],
        wajib_count: int,
        wajib_sks: int,
        total_sks: int,
    ) -> None:
        nonlocal best_selection, best_score

        score = (wajib_count, wajib_sks, total_sks)
        if score > best_score:
            best_score = score
            best_selection = list(chosen)

        if index >= len(eligible):
            return

        # Upper-bound pruning untuk menjaga performa pada kombinasi besar.
        rem_wajib = sum(1 for c in eligible[index:] if c.is_wajib)
        if wajib_count + rem_wajib < best_score[0]:
            return

        candidate = eligible[index]
        mk = candidate.course

        # Cabang 1: coba ambil kandidat jika SKS muat dan tidak bentrok.
        if total_sks + mk.sks <= max_sks and all(
            not jadwal_bentrok(mk, existing.course) for existing in chosen
        ):
            chosen.append(candidate)
            backtrack(
                index + 1,
                chosen,
                wajib_count + (1 if candidate.is_wajib else 0),
                wajib_sks + (mk.sks if candidate.is_wajib else 0),
                total_sks + mk.sks,
            )
            chosen.pop()

        # Cabang 2: lewati kandidat ini.
        backtrack(index + 1, chosen, wajib_count, wajib_sks, total_sks)

    backtrack(0, [], 0, 0, 0)

    # Langkah 2 (Guardrail): Validasi ulang setiap MK terpilih lewat rantai validator resmi!
    chain = chain_validators(build_default_validators())
    validated_courses: list[MataKuliah] = []
    for item in best_selection:
        if chain is not None:
            ctx = KRSContext(
                nim=nim,
                semester=semester,
                existing_courses=validated_courses,
                target_course=item.course,
                db=db,
            )
            check = chain.validate(ctx)
            if not check.ok:
                raise KRSValidationError(
                    f"Guardrail validasi gagal pada rekomendasi: {check.message}"
                )
        validated_courses.append(item.course)

    chosen_codes = {item.course.kode_mk for item in best_selection}
    for candidate in eligible:
        mk = candidate.course
        if mk.kode_mk in chosen_codes:
            continue
        conflicting = [
            sel.course.nama
            for sel in best_selection
            if jadwal_bentrok(mk, sel.course)
        ]
        if conflicting:
            alasan = f"Bentrok jadwal dengan {', '.join(conflicting)}"
        else:
            alasan = f"Melebihi batas maksimal {max_sks} SKS"
        skipped.append(
            {
                "kode_mk": mk.kode_mk,
                "nama": mk.nama,
                "alasan": alasan,
            }
        )

    return {
        "nim": nim,
        "semester": semester,
        "max_sks": max_sks,
        "total_sks": sum(mk.sks for mk in validated_courses),
        "wajib_terpilih": [
            item.course.kode_mk for item in best_selection if item.is_wajib
        ],
        "pilihan_terpilih": [
            item.course.kode_mk for item in best_selection if not item.is_wajib
        ],
        "recommended_courses": validated_courses,
        "skipped_courses": skipped,
        "validated": True,
    }
