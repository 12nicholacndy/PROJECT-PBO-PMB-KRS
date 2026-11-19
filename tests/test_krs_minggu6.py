from datetime import time
from itertools import product

import pytest
from hypothesis import given, settings, strategies as st
from sqlalchemy import select

from app.models import (
    KRS,
    KRSAktor,
    KRSDetail,
    KRSLog,
    KRSStatus,
    MataKuliah,
    Notifikasi,
)
from app.services.krs_errors import KRSTransitionError
from app.services.krs_manager import KRSManager
from app.services.krs_validators import (
    ConflictValidator,
    KRSContext,
    MAX_SKS,
    SKSValidator,
)
from tests.test_krs_minggu5 import _create_approved_student, _seed_sample_courses


# =============================================================================
# 1. UNIT TEST STATE MACHINE EXHAUSTIVE (4 x 4 x 2 = 32 KOMBINASI)
# =============================================================================

# Ditulis ulang secara manual dari spesifikasi tugas (bukan impor dari tabel kode).
SPEC_LEGAL_TRANSITIONS = {
    (KRSStatus.DRAFT, KRSStatus.SUBMITTED, KRSAktor.MAHASISWA),
    (KRSStatus.SUBMITTED, KRSStatus.APPROVED, KRSAktor.DOSEN_PA),
    (KRSStatus.SUBMITTED, KRSStatus.REVISION, KRSAktor.DOSEN_PA),
    (KRSStatus.REVISION, KRSStatus.SUBMITTED, KRSAktor.MAHASISWA),
}

ALL_COMBINATIONS = list(product(KRSStatus, KRSStatus, KRSAktor))
ILLEGAL_TRANSITIONS = [
    combo for combo in ALL_COMBINATIONS if combo not in SPEC_LEGAL_TRANSITIONS
]


@pytest.mark.parametrize(
    "source,target,aktor", sorted(SPEC_LEGAL_TRANSITIONS, key=str)
)
def test_state_machine_all_4_legal_transitions_succeed(source, target, aktor):
    krs = KRS(nim="20260010001", semester="20251", status=source)
    krs.transition(target, aktor)
    assert krs.status == target


@pytest.mark.parametrize("source,target,aktor", ILLEGAL_TRANSITIONS)
def test_state_machine_all_28_illegal_transitions_raise_and_preserve_state(
    source, target, aktor
):
    assert len(ILLEGAL_TRANSITIONS) == 28
    krs = KRS(nim="20260010001", semester="20251", status=source)
    with pytest.raises(KRSTransitionError):
        krs.transition(target, aktor)
    # Pastikan status tidak berubah sedikit pun saat transisi ilegal ditolak.
    assert krs.status == source


# =============================================================================
# 2. PROPERTY-BASED TESTING DENGAN HYPOTHESIS (TANPA DATABASE)
# =============================================================================


@settings(max_examples=1000, deadline=None)
@given(sks_list=st.lists(st.integers(min_value=1, max_value=4), max_size=40))
def test_property_total_sks_never_exceeds_24(sks_list):
    """1000 kombinasi acak: total SKS diterima tidak pernah > 24 dan setiap
    penolakan harus beralasan (total + sks_baru > 24).
    """
    validator = SKSValidator(max_sks=MAX_SKS)
    accepted: list[MataKuliah] = []

    for idx, sks in enumerate(sks_list):
        candidate = MataKuliah(
            kode_mk=f"MK{idx:03d}",
            nama=f"Course {idx}",
            sks=sks,
            hari=(idx % 6) + 1,
            jam_mulai=time(8, 0),
            jam_selesai=time(10, 0),
        )
        current_total = sum(mk.sks for mk in accepted)
        ctx = KRSContext(
            nim="20260010001",
            semester="20251",
            existing_courses=accepted,
            target_course=candidate,
            db=None,  # type: ignore[arg-type]
        )
        result = validator.check(ctx)

        if result.ok:
            accepted.append(candidate)
            # Properti 1: total SKS yang diterima tidak pernah melebihi 24.
            assert sum(mk.sks for mk in accepted) <= MAX_SKS
        else:
            # Properti 2: penolakan wajib beralasan (tidak boleh menolak sembarangan).
            assert current_total + sks > MAX_SKS


def _course_hour_slots(mk: MataKuliah) -> set[tuple[int, int]]:
    """Oracle independen (bukan jadwal_bentrok): ubah MK menjadi himpunan slot jam."""
    return {
        (mk.hari, hour)
        for hour in range(mk.jam_mulai.hour, mk.jam_selesai.hour)
    }


schedule_strategy = st.lists(
    st.tuples(
        st.integers(min_value=1, max_value=3),  # Sengaja 3 hari agar bentrok sering terjadi
        st.integers(min_value=7, max_value=16),  # Jam mulai 07:00 - 16:00
        st.integers(min_value=1, max_value=3),  # Durasi 1 - 3 jam
    ),
    max_size=25,
)


@settings(max_examples=300, deadline=None)
@given(schedules=schedule_strategy)
def test_property_accepted_courses_never_conflict(schedules):
    """Properti jadwal: tidak ada dua MK diterima yang saling bentrok,
    diverifikasi dengan oracle himpunan slot jam yang terpisah dari validator.
    """
    validator = ConflictValidator()
    accepted: list[MataKuliah] = []
    accepted_slots: set[tuple[int, int]] = set()

    for idx, (hari, start_h, duration) in enumerate(schedules):
        candidate = MataKuliah(
            kode_mk=f"SC{idx:03d}",
            nama=f"Jadwal {idx}",
            sks=2,
            hari=hari,
            jam_mulai=time(start_h, 0),
            jam_selesai=time(start_h + duration, 0),
        )
        candidate_slots = _course_hour_slots(candidate)
        ctx = KRSContext(
            nim="20260010001",
            semester="20251",
            existing_courses=accepted,
            target_course=candidate,
            db=None,  # type: ignore[arg-type]
        )
        result = validator.check(ctx)

        if result.ok:
            accepted.append(candidate)
            accepted_slots |= candidate_slots
            # Jumlah total slot semua MK == ukuran gabungan set slot (tidak ada tumpang tindih).
            assert sum(len(_course_hour_slots(mk)) for mk in accepted) == len(
                accepted_slots
            )
        else:
            # Jika ditolak, harus benar-benar beririsan dengan slot yang sudah terpakai.
            assert len(candidate_slots & accepted_slots) > 0


# =============================================================================
# 3. INTEGRATION & API TESTS: LIFECYCLE, OTORISASI, OPTIMISTIC LOCK, ROLLBACK
# =============================================================================


def test_full_krs_lifecycle_draft_submit_revise_resubmit_approve(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)
    dosen_pa_id = 101

    # 1. Tambah MK -> KRS lahir berstatus draft dengan dosen PA 101
    res_add = client.post(
        "/api/krs/courses",
        json={
            "nim": nim,
            "semester": "20251",
            "kode_mk": "IF101",
            "dosen_pa_id": dosen_pa_id,
        },
    )
    assert res_add.status_code == 201
    assert res_add.json()["status"] == "draft"
    v_after_add = res_add.json()["version"]

    # 2. Mahasiswa submit (DRAFT -> SUBMITTED)
    res_sub = client.post(f"/api/krs/{nim}/20251/submit")
    assert res_sub.status_code == 200
    assert res_sub.json()["status"] == "submitted"
    assert res_sub.json()["submitted_at"] is not None
    v_submitted = res_sub.json()["version"]
    assert v_submitted > v_after_add

    # 3. Dosen PA minta revisi (SUBMITTED -> REVISION)
    res_rev = client.post(
        f"/api/krs/{nim}/20251/revise",
        json={
            "dosen_pa_id": dosen_pa_id,
            "catatan": "Tambahkan Kalkulus I sebagai mata kuliah wajib semester ini",
            "version": v_submitted,
        },
    )
    assert res_rev.status_code == 200
    assert res_rev.json()["status"] == "revision"
    assert "Kalkulus I" in res_rev.json()["catatan_revisi"]

    # 4. Saat REVISION, mahasiswa boleh menambah MK lagi
    res_add2 = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF102"},
    )
    assert res_add2.status_code == 201
    assert res_add2.json()["total_sks"] == 6

    # 5. Mahasiswa submit ulang (REVISION -> SUBMITTED)
    res_sub2 = client.post(f"/api/krs/{nim}/20251/submit")
    assert res_sub2.status_code == 200
    v_resubmitted = res_sub2.json()["version"]

    # 6. Dosen PA menyetujui (SUBMITTED -> APPROVED)
    res_app = client.post(
        f"/api/krs/{nim}/20251/approve",
        json={"dosen_pa_id": dosen_pa_id, "version": v_resubmitted},
    )
    assert res_app.status_code == 200
    assert res_app.json()["status"] == "approved"
    assert res_app.json()["approved_at"] is not None

    # 7. Audit log & notifikasi tercatat lengkap
    logs = client.get(f"/api/krs/{nim}/20251/logs").json()
    assert [log["aksi"] for log in logs] == ["submit", "revise", "submit", "approve"]

    notifs = client.get(f"/api/krs/{nim}/notifikasi").json()
    assert len(notifs) == 2
    assert "Revisi" in notifs[0]["judul"]
    assert "Disetujui" in notifs[1]["judul"]


def test_submit_empty_krs_rejected_400(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101", "dosen_pa_id": 101},
    )
    client.delete(f"/api/krs/{nim}/20251/courses/IF101")

    res = client.post(f"/api/krs/{nim}/20251/submit")
    assert res.status_code == 400
    assert "kosong" in res.json()["detail"].lower()


def test_submit_revalidates_and_blocks_corrupted_krs(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    # Masukkan MK tanpa prasyarat langsung ke DB (simulasi koreksi nilai/data berubah)
    krs = KRS(nim=nim, semester="20251", status=KRSStatus.DRAFT, dosen_pa_id=101)
    db.add(krs)
    db.flush()
    db.add(KRSDetail(krs_id=krs.id, kode_mk="IF201"))
    db.commit()

    res = client.post(f"/api/krs/{nim}/20251/submit")
    assert res.status_code == 400
    assert "Algoritma" in res.json()["detail"]


def test_authorization_wrong_dosen_pa_and_wrong_student_return_403(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101", "dosen_pa_id": 101},
    )

    # Mahasiswa lain mencoba submit KRS milik nim -> 403
    wrong_student = client.post(
        f"/api/krs/{nim}/20251/submit",
        json={"requester_nim": "20260019999"},
    )
    assert wrong_student.status_code == 403

    # Submit sah
    assert client.post(f"/api/krs/{nim}/20251/submit").status_code == 200

    # Dosen PA lain (ID 999 != 101) mencoba approve atau revise -> 403
    wrong_approve = client.post(
        f"/api/krs/{nim}/20251/approve",
        json={"dosen_pa_id": 999},
    )
    wrong_revise = client.post(
        f"/api/krs/{nim}/20251/revise",
        json={"dosen_pa_id": 999, "catatan": "Saya bukan PA-nya"},
    )
    assert wrong_approve.status_code == 403
    assert wrong_revise.status_code == 403


def test_cannot_skip_draft_to_approved_returns_409(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101", "dosen_pa_id": 101},
    )

    res = client.post(
        f"/api/krs/{nim}/20251/approve",
        json={"dosen_pa_id": 101},
    )
    assert res.status_code == 409
    assert "draft -> approved" in res.json()["detail"]


def test_optimistic_locking_rejects_stale_version_409(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    res_add = client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101", "dosen_pa_id": 101},
    )
    stale_version = res_add.json()["version"]

    res_sub = client.post(f"/api/krs/{nim}/20251/submit")
    assert res_sub.status_code == 200
    assert res_sub.json()["version"] != stale_version

    # Dosen PA mengirim versi lama (layar basi) -> ditolak 409
    stale_approve = client.post(
        f"/api/krs/{nim}/20251/approve",
        json={"dosen_pa_id": 101, "version": stale_version},
    )
    assert stale_approve.status_code == 409
    assert "berubah" in stale_approve.json()["detail"].lower()


def test_revise_requires_non_empty_note_400(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101", "dosen_pa_id": 101},
    )
    client.post(f"/api/krs/{nim}/20251/submit")

    res = client.post(
        f"/api/krs/{nim}/20251/revise",
        json={"dosen_pa_id": 101, "catatan": "   "},
    )
    assert res.status_code == 400


def test_approve_transaction_rolls_back_atomically_on_failure(client, db, monkeypatch):
    """Membuktikan atomisitas: jika penulisan notifikasi gagal di tengah transaksi,
    perubahan status KRS dan log ikut di-rollback (tidak setengah jadi).
    """
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    client.post(
        "/api/krs/courses",
        json={"nim": nim, "semester": "20251", "kode_mk": "IF101", "dosen_pa_id": 101},
    )
    client.post(f"/api/krs/{nim}/20251/submit")

    manager = KRSManager(db)

    def _boom(*args, **kwargs):
        raise RuntimeError("Simulasi kegagalan penyimpanan notifikasi")

    monkeypatch.setattr(manager, "_record_notification", _boom)

    with pytest.raises(RuntimeError, match="Simulasi kegagalan"):
        manager.approve_krs(nim, "20251", dosen_pa_id=101)

    # Pastikan semua perubahan dibatalkan: status tetap submitted, approved_at None,
    # tidak ada log 'approve', dan tidak ada notifikasi.
    krs = db.scalars(
        select(KRS).where(KRS.nim == nim, KRS.semester == "20251")
    ).one()
    assert krs.status == KRSStatus.SUBMITTED
    assert krs.approved_at is None

    approve_logs = db.scalars(
        select(KRSLog).where(KRSLog.krs_id == krs.id, KRSLog.aksi == "approve")
    ).all()
    assert approve_logs == []

    notifs = db.scalars(select(Notifikasi).where(Notifikasi.nim == nim)).all()
    assert notifs == []


def test_notifications_unknown_student_returns_404(client):
    assert client.get("/api/krs/99999999999/notifikasi").status_code == 404


# =============================================================================
# 4. CHALLENGE OPSIONAL (+10%): KRS RECOMMENDATION AI (CSP BACKTRACKING)
# =============================================================================


def test_krs_recommender_prioritizes_wajib_over_pilihan_and_checks_prereq(client, db):
    _seed_sample_courses(db)
    # Tambah MK pilihan 4 SKS yang bentrok dengan Basis Data (wajib 3 SKS, Senin 08:00-10:30)
    db.add(
        MataKuliah(
            kode_mk="PIL01",
            nama="Desain Game (Pilihan)",
            sks=4,
            hari=1,
            jam_mulai=time(8, 30),
            jam_selesai=time(11, 30),
        )
    )
    db.add(
        MataKuliah(
            kode_mk="PIL02",
            nama="Cloud Computing (Pilihan)",
            sks=3,
            hari=4,
            jam_mulai=time(13, 0),
            jam_selesai=time(15, 30),
        )
    )
    db.commit()

    nim = _create_approved_student(client)

    # Mahasiswa meminta:
    # - Wajib: IF202 (Basis Data, 3 SKS, Senin 08:00-10:30), IF201 (Struktur Data -> belum lulus Algoritma!)
    # - Pilihan: PIL01 (4 SKS, bentrok Senin), PIL02 (3 SKS, Kamis -> aman)
    res = client.post(
        f"/api/krs/{nim}/20251/recommend",
        json={
            "wajib_codes": ["IF202", "IF201"],
            "pilihan_codes": ["PIL01", "PIL02"],
            "max_sks": 24,
        },
    )
    assert res.status_code == 200
    body = res.json()

    assert body["validated"] is True
    # Wajib IF202 menang atas Pilihan PIL01 meskipun PIL01 punya SKS lebih besar!
    assert body["wajib_terpilih"] == ["IF202"]
    assert body["pilihan_terpilih"] == ["PIL02"]
    assert body["total_sks"] == 6

    skipped_by_code = {item["kode_mk"]: item["alasan"] for item in body["skipped_courses"]}
    assert "Algoritma" in skipped_by_code["IF201"]
    assert "Bentrok jadwal" in skipped_by_code["PIL01"]


def test_krs_recommender_respects_max_sks_and_error_cases(client, db):
    _seed_sample_courses(db)
    nim = _create_approved_student(client)

    res = client.post(
        f"/api/krs/{nim}/20251/recommend",
        json={
            "wajib_codes": ["IF101"],
            "pilihan_codes": ["IF102"],
            "max_sks": 4,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total_sks"] == 3
    assert body["wajib_terpilih"] == ["IF101"]
    assert body["pilihan_terpilih"] == []

    # Daftar kosong -> 400
    assert (
        client.post(
            f"/api/krs/{nim}/20251/recommend",
            json={"wajib_codes": [], "pilihan_codes": []},
        ).status_code
        == 400
    )
    # MK tidak ada -> 404
    assert (
        client.post(
            f"/api/krs/{nim}/20251/recommend",
            json={"wajib_codes": ["NOPE99"]},
        ).status_code
        == 404
    )
    # NIM tidak ada -> 404
    assert (
        client.post(
            "/api/krs/99999999999/20251/recommend",
            json={"wajib_codes": ["IF101"]},
        ).status_code
        == 404
    )
