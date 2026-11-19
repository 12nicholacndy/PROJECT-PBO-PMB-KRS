from datetime import time

import pytest
from sqlalchemy import text

from app.models import KRS, KRSDetail, KRSStatus, MataKuliah
from app.services.krs_manager import KRSManager, KRSValidationError
from app.services.krs_validators import (
    KRSContext,
    ValidationResult,
    Validator,
    build_prasyarat_belum_lulus_query,
    jadwal_bentrok,
)
from tests.conftest import daftar


def _seed_sample_courses(db) -> dict[str, MataKuliah]:
    courses = [
        MataKuliah(
            kode_mk="IF101",
            nama="Algoritma",
            sks=3,
            hari=1,
            jam_mulai=time(8, 0),
            jam_selesai=time(10, 30),
        ),
        MataKuliah(
            kode_mk="IF102",
            nama="Kalkulus I",
            sks=3,
            hari=2,
            jam_mulai=time(8, 0),
            jam_selesai=time(10, 30),
        ),
        MataKuliah(
            kode_mk="IF201",
            nama="Struktur Data",
            sks=3,
            hari=1,
            jam_mulai=time(10, 30),  # Bersambung tepat setelah IF101 (tidak bentrok)
            jam_selesai=time(13, 0),
        ),
        MataKuliah(
            kode_mk="IF202",
            nama="Basis Data",
            sks=3,
            hari=1,
            jam_mulai=time(8, 0),
            jam_selesai=time(10, 30),
        ),
        MataKuliah(
            kode_mk="IF203",
            nama="Statistika",
            sks=3,
            hari=1,
            jam_mulai=time(9, 0),  # Bentrok sebagian dengan Basis Data (08:00-10:30)
            jam_selesai=time(11, 0),
        ),
        MataKuliah(
            kode_mk="IF204",
            nama="Kalkulus II",
            sks=3,
            hari=3,
            jam_mulai=time(8, 0),
            jam_selesai=time(10, 30),
        ),
    ]
    db.add_all(courses)
    db.flush()

    # Struktur Data butuh Algoritma; Kalkulus II butuh Kalkulus I
    by_code = {mk.kode_mk: mk for mk in courses}
    by_code["IF201"].prasyarat.append(by_code["IF101"])
    by_code["IF204"].prasyarat.append(by_code["IF102"])
    db.commit()
    return by_code


def _create_approved_student(client, email="budi@example.com") -> str:
    pendaftar = daftar(client, email=email)
    approved = client.put(f"/api/pmb/approve/{pendaftar['id']}")
    assert approved.status_code == 200
    return approved.json()["nim"]


# --- 1. Verifikasi arah relasi self-referencing ---


def test_self_referencing_prerequisite_direction(db):
    courses = _seed_sample_courses(db)
    db.refresh(courses["IF201"])
    db.refresh(courses["IF101"])

    assert [mk.kode_mk for mk in courses["IF201"].prasyarat] == ["IF101"]
    assert courses["IF101"].prasyarat == []


# --- 2. Unit test rumus irisan interval jadwal ---


def test_jadwal_bentrok_edge_cases():
    mk_a = MataKuliah(
        kode_mk="A",
        nama="Basis Data",
        sks=3,
        hari=1,
        jam_mulai=time(8, 0),
        jam_selesai=time(10, 30),
    )
    mk_partial = MataKuliah(
        kode_mk="B",
        nama="Statistika",
        sks=3,
        hari=1,
        jam_mulai=time(9, 0),
        jam_selesai=time(11, 0),
    )
    mk_nested = MataKuliah(
        kode_mk="C",
        nama="Etika",
        sks=2,
        hari=1,
        jam_mulai=time(8, 30),
        jam_selesai=time(9, 30),
    )
    mk_exact = MataKuliah(
        kode_mk="D",
        nama="PBO",
        sks=3,
        hari=1,
        jam_mulai=time(8, 0),
        jam_selesai=time(10, 30),
    )
    mk_adjacent = MataKuliah(
        kode_mk="E",
        nama="Jaringan",
        sks=3,
        hari=1,
        jam_mulai=time(10, 30),
        jam_selesai=time(12, 0),
    )
    mk_diff_day = MataKuliah(
        kode_mk="F",
        nama="AI",
        sks=3,
        hari=2,
        jam_mulai=time(8, 0),
        jam_selesai=time(10, 30),
    )

    assert jadwal_bentrok(mk_a, mk_partial) is True
    assert jadwal_bentrok(mk_a, mk_nested) is True
    assert jadwal_bentrok(mk_a, mk_exact) is True
    assert jadwal_bentrok(mk_a, mk_adjacent) is False
    assert jadwal_bentrok(mk_a, mk_diff_day) is False


# --- 3. Integration tests: PMB -> KRS add/get/remove ---


def test_add_get_and_remove_course_flow(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    # Katalog mata kuliah tersedia
    catalog = client.get("/api/krs/matakuliah")
    assert catalog.status_code == 200
    assert len(catalog.json()) == 6

    # Tambah Algoritma (3 SKS)
    res_add = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101"},
    )
    assert res_add.status_code == 201
    body = res_add.json()
    assert body["status"] == "draft"
    assert body["total_sks"] == 3
    assert [c["kode_mk"] for c in body["courses"]] == ["IF101"]

    # Ambil KRS
    res_get = client.get(f"/api/krs/{nim}/20251")
    assert res_get.status_code == 200
    assert res_get.json()["total_sks"] == 3

    # Hapus Algoritma
    res_del = client.delete(f"/api/krs/{nim}/20251/courses/IF101")
    assert res_del.status_code == 200
    assert res_del.json()["total_sks"] == 0
    assert res_del.json()["courses"] == []


def test_add_course_unknown_nim_or_course_returns_404(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    res_nim = client.post(
        "/api/krs/courses",
        json={"nim": "99999999999", "semester": "20251", "kode_mk": "IF101"},
    )
    assert res_nim.status_code == 404

    res_mk = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "NOPE99"},
    )
    assert res_mk.status_code == 404


def test_remove_course_not_found_returns_404(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    assert client.delete(f"/api/krs/{nim}/20251/courses/IF101").status_code == 404

    client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101"},
    )
    assert client.delete(f"/api/krs/{nim}/20251/courses/IF102").status_code == 404


# --- 4. Duplicate, SKS (exact 24 vs >24), Conflict ---


def test_duplicate_enrollment_rejected(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    first = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101"},
    )
    assert first.status_code == 201

    dup = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101"},
    )
    assert dup.status_code == 400
    assert "sudah ada di KRS" in dup.json()["detail"]


def test_sks_limit_exact_24_allowed_and_over_24_rejected(client, db):
    # Buat 6 MK @ 4 SKS (total 24 SKS) di hari berbeda + 1 MK 1 SKS
    for i in range(1, 7):
        db.add(
            MataKuliah(
                kode_mk=f"MK{i:02d}",
                nama=f"Mata Kuliah {i}",
                sks=4,
                hari=i,
                jam_mulai=time(8, 0),
                jam_selesai=time(10, 0),
            )
        )
    db.add(
        MataKuliah(
            kode_mk="MK07",
            nama="Mata Kuliah Tambahan",
            sks=1,
            hari=6,
            jam_mulai=time(13, 0),
            jam_selesai=time(14, 0),
        )
    )
    db.commit()

    nim = _create_approved_student(client)

    for i in range(1, 7):
        res = client.post(
            "/api/krs/courses",
            json={"nim": nim, "semester": "20251", "kode_mk": f"MK{i:02d}"},
        )
        assert res.status_code == 201

    assert client.get(f"/api/krs/{nim}/20251").json()["total_sks"] == 24

    # Tambah 1 SKS lagi (jadi 25 > 24) harus ditolak dan menyebut angka
    over = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "MK07"},
    )
    assert over.status_code == 400
    assert "25" in over.json()["detail"]
    assert "24" in over.json()["detail"]


def test_schedule_conflict_rejected_and_adjacent_allowed(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    # Basis Data: Senin 08:00-10:30
    assert (
        client.post(
            "/api/krs/courses",
            json={"nim": nim, "semester": "20251", "kode_mk": "IF202"},
        ).status_code
        == 201
    )

    # Statistika: Senin 09:00-11:00 -> bentrok sebagian!
    conflict = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF203"},
    )
    assert conflict.status_code == 400
    assert "Jadwal bentrok" in conflict.json()["detail"]
    assert "Statistika" in conflict.json()["detail"]
    assert "Basis Data" in conflict.json()["detail"]

    # Luluskan Algoritma di semester sebelumnya agar bisa ambil Struktur Data (Senin 10:30-13:00)
    krs_lama = KRS(nim=nim, semester="20242", status=KRSStatus.APPROVED)
    db.add(krs_lama)
    db.flush()
    db.add(KRSDetail(krs_id=krs_lama.id, kode_mk="IF101", nilai="B"))
    db.commit()

    # Struktur Data bersambung tepat jam 10:30 -> TIDAK bentrok
    adjacent = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF201"},
    )
    assert adjacent.status_code == 201


# --- 5. Prerequisite rules (belum ambil, nilai D/E, semester sama, lulus >= C) ---


def test_prerequisite_rules_and_grade_weight_check(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    # 1. Belum pernah ambil Algoritma -> gagal
    res1 = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF201"},
    )
    assert res1.status_code == 400
    assert res1.json()["detail"] == "Prasyarat tidak terpenuhi: Algoritma"

    # 2. Pernah ambil di semester sebelumnya dengan nilai D -> tetap gagal!
    # (Menguji agar tidak terjebak bug string 'D' >= 'C')
    krs_20241 = KRS(nim=nim, semester="20241", status=KRSStatus.APPROVED)
    db.add(krs_20241)
    db.flush()
    db.add(KRSDetail(krs_id=krs_20241.id, kode_mk="IF101", nilai="D"))
    db.commit()

    res2 = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF201"},
    )
    assert res2.status_code == 400
    assert "Algoritma" in res2.json()["detail"]

    # 3. Ambil Kalkulus I di semester YANG SAMA (20251) dengan nilai A -> tetap gagal
    # karena prasyarat harus lulus di semester sebelumnya (< semester_aktif).
    krs_20251 = KRS(nim=nim, semester="20251", status=KRSStatus.DRAFT)
    db.add(krs_20251)
    db.flush()
    db.add(KRSDetail(krs_id=krs_20251.id, kode_mk="IF102", nilai="A"))
    db.commit()

    res3 = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF204"},
    )
    assert res3.status_code == 400
    assert "Kalkulus I" in res3.json()["detail"]

    # 4. Lulus Algoritma di semester sebelumnya (20242) dengan nilai C -> LOLOS!
    krs_20242 = KRS(nim=nim, semester="20242", status=KRSStatus.APPROVED)
    db.add(krs_20242)
    db.flush()
    db.add(KRSDetail(krs_id=krs_20242.id, kode_mk="IF101", nilai="C"))
    db.commit()

    res4 = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF201"},
    )
    assert res4.status_code == 201


# --- 6. Status guard (draft/revision editable; submitted/approved locked) ---


@pytest.mark.parametrize("locked_status", [KRSStatus.SUBMITTED, KRSStatus.APPROVED])
def test_locked_krs_cannot_be_modified(client, db, locked_status):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    krs = KRS(nim=nim, semester="20251", status=locked_status)
    db.add(krs)
    db.flush()
    db.add(KRSDetail(krs_id=krs.id, kode_mk="IF101"))
    db.commit()

    res_add = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF102"},
    )
    res_del = client.delete(f"/api/krs/{nim}/20251/courses/IF101")

    assert res_add.status_code == 409
    assert res_del.status_code == 409


def test_revision_krs_remains_editable(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    db.add(KRS(nim=nim, semester="20251", status=KRSStatus.REVISION))
    db.commit()

    res = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101"},
    )
    assert res.status_code == 201
    assert res.json()["status"] == "revision"


# --- 7. validate_krs mengumpulkan semua kesalahan sekaligus ---


def test_validate_krs_collects_all_errors(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    # Sisipkan data bermasalah langsung ke DB untuk menguji validate_krs menyeluruh:
    # - IF201 (Struktur Data, Senin 10:30-13:00 -> butuh Algoritma belum lulus & bentrok IF203)
    # - IF202 (Basis Data, Senin 08:00-10:30) & IF203 (Statistika, Senin 09:00-11:00 -> bentrok)
    krs = KRS(nim=nim, semester="20251", status=KRSStatus.DRAFT)
    db.add(krs)
    db.flush()
    db.add_all([
        KRSDetail(krs_id=krs.id, kode_mk="IF201"),
        KRSDetail(krs_id=krs.id, kode_mk="IF202"),
        KRSDetail(krs_id=krs.id, kode_mk="IF203"),
    ])
    db.commit()

    res = client.get(f"/api/krs/{nim}/20251/validate")
    assert res.status_code == 200
    body = res.json()

    assert body["valid"] is False
    assert body["total_sks"] == 9
    assert len(body["errors"]) == 3
    assert any("Prasyarat tidak terpenuhi: Algoritma" in err for err in body["errors"])
    assert any("Basis Data" in err and "Statistika" in err for err in body["errors"])
    assert any("Struktur Data" in err and "Statistika" in err for err in body["errors"])


def test_validate_krs_not_found_returns_404(client):
    assert client.get("/api/krs/99999999999/20251/validate").status_code == 404
    assert client.get("/api/krs/99999999999/20251").status_code == 404


# --- 8. Dependency Injection / OCP test dengan validator palsu ---


class _TunggakanSPPValidator(Validator):
    def check(self, ctx: KRSContext) -> ValidationResult:
        return ValidationResult(
            ok=False,
            message="Mahasiswa masih memiliki tunggakan SPP",
        )


def test_krs_manager_accepts_custom_injected_validator(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    manager = KRSManager(db, validators=[_TunggakanSPPValidator()])
    with pytest.raises(KRSValidationError, match="tunggakan SPP"):
        manager.add_course(nim, "IF101", "20251")


# --- 9. Verifikasi indeks dengan EXPLAIN QUERY PLAN ---


def test_prerequisite_query_uses_indexes(db):
    stmt = build_prasyarat_belum_lulus_query("20260010001", "20251", "IF201")
    compiled = stmt.compile(
        dialect=db.bind.dialect,
        compile_kwargs={"literal_binds": True},
    )
    plan_rows = db.execute(text(f"EXPLAIN QUERY PLAN {compiled}")).fetchall()
    plan_text = "\n".join(str(row) for row in plan_rows)

    # Pastikan SQLite memakai SEARCH + INDEX (bukan SCAN full table).
    assert "USING INDEX" in plan_text or "USING COVERING INDEX" in plan_text
    assert "SCAN" not in plan_text
