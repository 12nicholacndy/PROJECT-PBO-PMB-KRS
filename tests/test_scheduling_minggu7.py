from collections import Counter
from datetime import time

import pytest
from hypothesis import given, settings, strategies as st
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import (
    CalonMahasiswa,
    Dosen,
    Jadwal,
    KRS,
    KRSDetail,
    KRSStatus,
    Kelas,
    MataKuliah,
    Ruangan,
    StatusPMB,
)
from app.seed_jadwal import seed_jadwal_minggu7
from app.services.conflict_detector import (
    Conflict,
    JadwalSlot,
    detect_schedule_conflict,
    detect_schedule_conflict_naive,
    pasangan,
)


def _jam(teks: str) -> time:
    hh, mm = teks.split(":")
    return time(int(hh), int(mm))


def J(
    id_: int,
    hari: int,
    mulai: str,
    selesai: str,
    ruangan: str,
    dosen: int,
) -> JadwalSlot:
    """Helper singkat pembuat JadwalSlot dari teks jam untuk readability test."""
    return JadwalSlot(
        id=id_,
        hari=hari,
        jam_mulai=_jam(mulai),
        jam_selesai=_jam(selesai),
        ruangan_kode=ruangan,
        dosen_id=dosen,
    )


# ---------------------------------------------------------------------------
# 1. Example-Based Tests — Penjaga 5 Bug AI Umum (Parametrized)
# ---------------------------------------------------------------------------

JADWAL_DASAR = J(1, 1, "08:00", "09:40", "A101", 1)


@pytest.mark.parametrize(
    ("nama_kasus", "jadwal_kedua", "expected"),
    [
        (
            "room_conflict_same_room_diff_lecturer",
            J(2, 1, "08:30", "10:10", "A101", 2),
            {Conflict(type="room_conflict", affected=(1, 2))},
        ),
        (
            "lecturer_conflict_same_lecturer_diff_room",
            J(2, 1, "09:00", "10:40", "B201", 1),
            {Conflict(type="lecturer_conflict", affected=(1, 2))},
        ),
        (
            "back_to_back_0940_is_not_conflict",
            J(2, 1, "09:40", "11:20", "A101", 1),
            set(),
        ),
        (
            "different_day_same_room_and_lecturer_is_not_conflict",
            J(2, 3, "08:00", "09:40", "A101", 1),
            set(),
        ),
        (
            "nested_same_room_and_same_lecturer_reports_both",
            J(2, 1, "08:15", "09:15", "A101", 1),
            {
                Conflict(type="room_conflict", affected=(1, 2)),
                Conflict(type="lecturer_conflict", affected=(1, 2)),
            },
        ),
        (
            "time_overlap_only_diff_room_diff_lecturer_is_not_conflict",
            J(2, 1, "08:00", "09:40", "B201", 2),
            set(),
        ),
        (
            "time_comparison_not_string_09_before_10",
            J(2, 1, "09:00", "10:00", "A101", 2),
            {Conflict(type="room_conflict", affected=(1, 2))},
        ),
    ],
)
def test_conflict_detector_both_versions_match_expected(
    nama_kasus, jadwal_kedua, expected
):
    jadwals = [JADWAL_DASAR, jadwal_kedua]
    hasil_naif = detect_schedule_conflict_naive(jadwals)
    hasil_sweep = detect_schedule_conflict(jadwals)

    assert set(hasil_naif) == expected, f"Naif gagal pada kasus {nama_kasus}"
    assert set(hasil_sweep) == expected, f"Sweep gagal pada kasus {nama_kasus}"
    assert len(hasil_naif) == len(expected)
    assert len(hasil_sweep) == len(expected)


def test_pasangan_normalizes_order():
    assert pasangan(5, 2) == (2, 5)
    assert pasangan(2, 5) == (2, 5)


# ---------------------------------------------------------------------------
# 2. Property-Based Testing (Hypothesis) — 2 Properti Inti Minggu 7
# ---------------------------------------------------------------------------

@st.composite
def jadwal_list_strategy(draw):
    """
    Membangkitkan daftar jadwal acak di ruang sempit (Senin-Rabu, jam 07-16,
    durasi 1-3 jam, 2 ruangan, 3 dosen) supaya bentrok sering terjadi.
    """
    n = draw(st.integers(min_value=0, max_value=20))
    items: list[JadwalSlot] = []
    for idx in range(1, n + 1):
        hari = draw(st.integers(min_value=1, max_value=3))
        mulai_jam = draw(st.integers(min_value=7, max_value=16))
        durasi = draw(st.integers(min_value=1, max_value=3))
        ruangan = draw(st.sampled_from(["A101", "B201"]))
        dosen = draw(st.sampled_from([1, 2, 3]))
        items.append(
            JadwalSlot(
                id=idx,
                hari=hari,
                jam_mulai=time(mulai_jam, 0),
                jam_selesai=time(mulai_jam + durasi, 0),
                ruangan_kode=ruangan,
                dosen_id=dosen,
            )
        )
    return items


@settings(max_examples=200, deadline=None)
@given(kandidat=jadwal_list_strategy())
def test_property_accepted_schedules_never_conflict(kandidat):
    """
    Properti 1 (No-Conflict Invariant):
    Simulasi admin memasukkan jadwal satu per satu — hanya diterima jika detektor
    tidak menemukan konflik. Diverifikasi secara independen dengan memecah setiap
    jadwal menjadi slot 1 jam per ruangan & per dosen lalu menghitungnya dengan Counter.
    """
    diterima: list[JadwalSlot] = []
    for j in kandidat:
        if not detect_schedule_conflict([*diterima, j]):
            diterima.append(j)

    slot_ruangan: Counter[tuple[int, str, int]] = Counter()
    slot_dosen: Counter[tuple[int, int, int]] = Counter()

    for j in diterima:
        for jam in range(j.jam_mulai.hour, j.jam_selesai.hour):
            slot_ruangan[(j.hari, j.ruangan_kode, jam)] += 1
            slot_dosen[(j.hari, j.dosen_id, jam)] += 1

    assert all(count <= 1 for count in slot_ruangan.values())
    assert all(count <= 1 for count in slot_dosen.values())


@settings(max_examples=200, deadline=None)
@given(jadwals=jadwal_list_strategy())
def test_property_sweep_matches_naive_exact_multiset(jadwals):
    """
    Properti 2 (Differential Testing):
    Versi Grouping + Sort-and-Sweep harus menghasilkan konflik yang persis sama
    dengan versi Naif termasuk jumlah kemunculannya (menangkap pasangan ganda).
    """
    hasil_naif = detect_schedule_conflict_naive(jadwals)
    hasil_sweep = detect_schedule_conflict(jadwals)
    assert Counter(hasil_sweep) == Counter(hasil_naif)


# ---------------------------------------------------------------------------
# 3. Fixture & Integration / API Tests untuk SchedulingService & Router
# ---------------------------------------------------------------------------

@pytest.fixture()
def jadwal_env(db):
    """Menyiapkan ruangan, dosen, mata kuliah, dan kelas untuk pengujian API Minggu 7."""
    db.add_all([
        Ruangan(kode="A101", nama="Ruang A101", kapasitas=40),
        Ruangan(kode="A102", nama="Ruang A102", kapasitas=30),
        Ruangan(kode="B201", nama="Ruang B201", kapasitas=50),
        Dosen(id=1, nidn="0101018501", nama="Dr. Andi Wijaya"),
        Dosen(id=2, nidn="0102028602", nama="Dr. Rina Kusuma"),
        MataKuliah(
            kode_mk="IF101",
            nama="Basis Data",
            sks=3,
            hari=1,
            jam_mulai=time(8, 0),
            jam_selesai=time(9, 40),
        ),
        MataKuliah(
            kode_mk="IF102",
            nama="Pemrograman Berorientasi Objek",
            sks=3,
            hari=1,
            jam_mulai=time(9, 40),
            jam_selesai=time(11, 20),
        ),
    ])
    db.flush()
    db.add_all([
        Kelas(id=1, kode_mk="IF101", nama_kelas="A", kuota=40, semester="20261"),
        Kelas(id=2, kode_mk="IF101", nama_kelas="B", kuota=30, semester="20261"),
        Kelas(id=3, kode_mk="IF102", nama_kelas="A", kuota=50, semester="20261"),
    ])
    db.commit()
    return db


ADMIN_HEADERS = {"X-Role": "admin_akademik"}


def test_api_create_success_and_back_to_back_allowed(client, jadwal_env):
    # Kapasitas tepat 40 <= 40 di A101 -> harus diterima (201)
    res1 = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "08:00:00",
            "jam_selesai": "09:40:00",
        },
    )
    assert res1.status_code == 201
    assert res1.json()["ruangan_kode"] == "A101"

    # Kuliah bersambung tepat 09:40 di ruangan & dosen sama -> harus diterima (201)
    res2 = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 2,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "09:40:00",
            "jam_selesai": "11:20:00",
        },
    )
    assert res2.status_code == 201


def test_api_create_room_conflict_returns_409_with_detail(client, jadwal_env):
    res1 = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "08:00:00",
            "jam_selesai": "09:40:00",
        },
    )
    jadwal1_id = res1.json()["id"]

    res2 = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 2,
            "ruangan_kode": "A101",
            "dosen_id": 2,
            "hari": 1,
            "jam_mulai": "08:30:00",
            "jam_selesai": "10:10:00",
        },
    )
    assert res2.status_code == 409
    body = res2.json()
    assert any(
        c["type"] == "room_conflict" and jadwal1_id in c["affected"]
        for c in body["conflicts"]
    )


def test_api_create_lecturer_and_both_conflicts_return_409(client, jadwal_env):
    res1 = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "08:00:00",
            "jam_selesai": "09:40:00",
        },
    )
    jadwal1_id = res1.json()["id"]

    # Bentrok dosen saja (ruangan berbeda)
    res_dosen = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 2,
            "ruangan_kode": "B201",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "09:00:00",
            "jam_selesai": "10:40:00",
        },
    )
    assert res_dosen.status_code == 409
    assert res_dosen.json()["conflicts"] == [
        {"type": "lecturer_conflict", "affected": [jadwal1_id]}
    ]

    # Bentrok ruangan DAN dosen sekaligus
    res_both = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 2,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "08:15:00",
            "jam_selesai": "09:15:00",
        },
    )
    assert res_both.status_code == 409
    types = {c["type"] for c in res_both.json()["conflicts"]}
    assert types == {"room_conflict", "lecturer_conflict"}


def test_api_capacity_check_kuota_and_enrolled(client, jadwal_env):
    # Kelas 3 berkuota 50 dimasukkan ke A101 (kapasitas 40) walau enrolled == 0 -> 409
    res_kuota = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 3,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 2,
            "jam_mulai": "08:00:00",
            "jam_selesai": "09:40:00",
        },
    )
    assert res_kuota.status_code == 409
    detail = res_kuota.json()["detail"]
    assert "A101" in detail
    assert "40" in detail
    assert "50" in detail

    # Jika kelas 2 (kuota 30) ternyata punya 31 mahasiswa terdaftar di krs_detail -> ditolak di A102 (kapasitas 30)
    db = jadwal_env
    for i in range(1, 32):
        nim = f"2026001{i:04d}"
        mhs = CalonMahasiswa(
            nama_lengkap=f"Mahasiswa Dummy {i}",
            email=f"mhs{i}@example.com",
            no_hp="081234567890",
            tanggal_lahir=__import__("datetime").date(2007, 1, 1),
            alamat="Bogor",
            program_studi_id=1,
            jalur_masuk_id=1,
            status=StatusPMB.APPROVED,
            nim=nim,
        )
        db.add(mhs)
        db.flush()
        krs = KRS(nim=nim, semester="20261", status=KRSStatus.APPROVED)
        db.add(krs)
        db.flush()
        db.add(KRSDetail(krs_id=krs.id, kode_mk="IF101", kelas_id=2))
    db.commit()

    res_enrolled = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 2,
            "ruangan_kode": "A102",
            "dosen_id": 2,
            "hari": 2,
            "jam_mulai": "10:00:00",
            "jam_selesai": "11:40:00",
        },
    )
    assert res_enrolled.status_code == 409
    assert "31" in res_enrolled.json()["detail"]


def test_api_update_shift_15_minutes_no_self_conflict_and_delete(client, jadwal_env):
    res = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "08:00:00",
            "jam_selesai": "09:40:00",
        },
    )
    jadwal_id = res.json()["id"]

    # Menggeser jadwal sendiri 15 menit (08:15-09:55) TIDAK boleh dianggap bentrok dengan diri sendiri
    res_update = client.put(
        f"/api/jadwal/{jadwal_id}",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "08:15:00",
            "jam_selesai": "09:55:00",
        },
    )
    assert res_update.status_code == 200
    assert res_update.json()["jam_mulai"] == "08:15:00"

    # Daftar jadwal memuat jadwal yang diperbarui
    res_list = client.get("/api/jadwal")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    # Delete jadwal berhasil
    res_del = client.delete(f"/api/jadwal/{jadwal_id}", headers=ADMIN_HEADERS)
    assert res_del.status_code == 200
    assert res_del.json()["deleted_id"] == jadwal_id

    # Delete ulang -> 404
    assert (
        client.delete(
            f"/api/jadwal/{jadwal_id}", headers=ADMIN_HEADERS
        ).status_code
        == 404
    )


def test_api_validation_and_not_found_errors(client, jadwal_env):
    # jam_selesai <= jam_mulai ditolak Pydantic (global handler -> 400)
    res_bad_time = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 1,
            "jam_mulai": "10:00:00",
            "jam_selesai": "09:00:00",
        },
    )
    assert res_bad_time.status_code == 400

    # hari=7 di luar 1..6
    res_bad_day = client.post(
        "/api/jadwal",
        headers=ADMIN_HEADERS,
        json={
            "kelas_id": 1,
            "ruangan_kode": "A101",
            "dosen_id": 1,
            "hari": 7,
            "jam_mulai": "08:00:00",
            "jam_selesai": "09:40:00",
        },
    )
    assert res_bad_day.status_code == 400

    # Kelas / Ruangan / Dosen tidak ada -> 404
    assert (
        client.post(
            "/api/jadwal",
            headers=ADMIN_HEADERS,
            json={
                "kelas_id": 999,
                "ruangan_kode": "A101",
                "dosen_id": 1,
                "hari": 1,
                "jam_mulai": "08:00:00",
                "jam_selesai": "09:40:00",
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/jadwal",
            headers=ADMIN_HEADERS,
            json={
                "kelas_id": 1,
                "ruangan_kode": "Z999",
                "dosen_id": 1,
                "hari": 1,
                "jam_mulai": "08:00:00",
                "jam_selesai": "09:40:00",
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/jadwal",
            headers=ADMIN_HEADERS,
            json={
                "kelas_id": 1,
                "ruangan_kode": "A101",
                "dosen_id": 999,
                "hari": 1,
                "jam_mulai": "08:00:00",
                "jam_selesai": "09:40:00",
            },
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/jadwal/999",
            headers=ADMIN_HEADERS,
            json={
                "kelas_id": 1,
                "ruangan_kode": "A101",
                "dosen_id": 1,
                "hari": 1,
                "jam_mulai": "08:00:00",
                "jam_selesai": "09:40:00",
            },
        ).status_code
        == 404
    )


def test_seed_with_intentional_conflicts_and_get_conflicts_endpoint(client, db):
    seed_result = seed_jadwal_minggu7(db, include_intentional_conflicts=True)
    assert seed_result["seeded"] == 6
    # Memanggil seed ulang bersifat idempoten
    assert seed_jadwal_minggu7(db)["seeded"] == 0

    res = client.get("/api/jadwal/conflicts", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    conflict_set = {
        (c["type"], tuple(c["affected"])) for c in body["conflicts"]
    }
    assert conflict_set == {
        ("room_conflict", (1, 5)),
        ("lecturer_conflict", (4, 6)),
    }


def test_database_constraints_and_composite_indexes(jadwal_env):
    db = jadwal_env

    # CheckConstraint: jam_mulai >= jam_selesai ditolak oleh database
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO jadwal (kelas_id, ruangan_kode, dosen_id, hari, jam_mulai, jam_selesai) "
                "VALUES (1, 'A101', 1, 1, '10:00:00', '09:00:00')"
            )
        )
        db.commit()
    db.rollback()

    # CheckConstraint: hari di luar 1..6 ditolak oleh database
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO jadwal (kelas_id, ruangan_kode, dosen_id, hari, jam_mulai, jam_selesai) "
                "VALUES (1, 'A101', 1, 0, '08:00:00', '09:40:00')"
            )
        )
        db.commit()
    db.rollback()

    # Verifikasi EXPLAIN QUERY PLAN menggunakan composite index (hari, ruangan_kode) & (hari, dosen_id)
    plan_ruangan = " ".join(
        str(row)
        for row in db.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT * FROM jadwal "
                "WHERE hari = 1 AND ruangan_kode = 'A101'"
            )
        ).fetchall()
    )
    plan_dosen = " ".join(
        str(row)
        for row in db.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT * FROM jadwal "
                "WHERE hari = 1 AND dosen_id = 1"
            )
        ).fetchall()
    )
    assert "ix_jadwal_hari_ruangan" in plan_ruangan
    assert "ix_jadwal_hari_dosen" in plan_dosen
