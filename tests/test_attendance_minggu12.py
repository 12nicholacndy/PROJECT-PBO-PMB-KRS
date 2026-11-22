from __future__ import annotations

from datetime import date, datetime, time, timedelta
import io

from hypothesis import given, settings, strategies as st
from openpyxl import load_workbook
import pytest

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
from app.services.ai_attendance_analyzer import analyze_attendance_records
from app.services.attendance_qr import (
    decode_attendance_token,
    generate_attendance_qr,
    haversine_distance_meters,
    reset_scan_rate_limiter,
)


def _seed_base_attendance_world(db):
    """Menyiapkan 4 mahasiswa, 2 MK, 2 kelas paralel IF101 (A & B), dan jadwal Senin 08:00."""
    mhs_list = [
        ("20260010001", "Andi Pratama", "andi@test.id"),
        ("20260010002", "Budi Santoso", "budi@test.id"),
        ("20260010003", "Citra Dewi", "citra@test.id"),
        ("20260010004", "Dodi Kelas B", "dodi@test.id"),
        ("20260010005", "Eka Non-Enrolled", "eka@test.id"),
    ]
    for nim, nama, email in mhs_list:
        db.add(
            CalonMahasiswa(
                nama_lengkap=nama,
                email=email,
                no_hp="081234567890",
                tanggal_lahir=date(2006, 4, 1),
                alamat="Bogor",
                program_studi_id=1,
                jalur_masuk_id=1,
                status=StatusPMB.APPROVED,
                nim=nim,
                approved_at=datetime(2026, 8, 1, 8, 0, 0),
            )
        )

    mk1 = MataKuliah(
        kode_mk="IF101",
        nama="Algoritma & Pemrograman",
        sks=3,
        hari=1,
        jam_mulai=time(8, 0),
        jam_selesai=time(10, 0),
    )
    mk2 = MataKuliah(
        kode_mk="IF202",
        nama="Basis Data",
        sks=3,
        hari=3,
        jam_mulai=time(10, 0),
        jam_selesai=time(12, 0),
    )
    ruang1 = Ruangan(kode="R-101", nama="Ruang Kuliah 101", kapasitas=40)
    ruang2 = Ruangan(kode="R-102", nama="Ruang Kuliah 102", kapasitas=40)
    dosen = Dosen(nidn="0401018501", nama="Dr. Agus Salim")
    db.add_all([mk1, mk2, ruang1, ruang2, dosen])
    db.flush()

    kelas_a = Kelas(kode_mk="IF101", nama_kelas="A", kuota=30, semester="20261")
    kelas_b = Kelas(kode_mk="IF101", nama_kelas="B", kuota=30, semester="20261")
    kelas_db = Kelas(kode_mk="IF202", nama_kelas="A", kuota=30, semester="20261")
    db.add_all([kelas_a, kelas_b, kelas_db])
    db.flush()

    jadwal_a = Jadwal(
        kelas_id=kelas_a.id,
        ruangan_kode="R-101",
        dosen_id=dosen.id,
        hari=1,  # Senin
        jam_mulai=time(8, 0),
        jam_selesai=time(10, 0),
    )
    jadwal_b = Jadwal(
        kelas_id=kelas_b.id,
        ruangan_kode="R-102",
        dosen_id=dosen.id,
        hari=2,  # Selasa
        jam_mulai=time(8, 0),
        jam_selesai=time(10, 0),
    )
    jadwal_db = Jadwal(
        kelas_id=kelas_db.id,
        ruangan_kode="R-101",
        dosen_id=dosen.id,
        hari=3,  # Rabu
        jam_mulai=time(10, 0),
        jam_selesai=time(12, 0),
    )
    db.add_all([jadwal_a, jadwal_b, jadwal_db])
    db.flush()

    # Andi, Budi, Citra di IF101 Kelas A; Dodi di IF101 Kelas B; Eka hanya ambil IF202
    enrollments = [
        ("20260010001", "IF101", kelas_a.id),
        ("20260010002", "IF101", kelas_a.id),
        ("20260010003", "IF101", kelas_a.id),
        ("20260010004", "IF101", kelas_b.id),
        ("20260010005", "IF202", kelas_db.id),
    ]
    for nim, kode_mk, kid in enrollments:
        krs = KRS(nim=nim, semester="20261", status=KRSStatus.APPROVED)
        db.add(krs)
        db.flush()
        db.add(KRSDetail(krs_id=krs.id, kode_mk=kode_mk, kelas_id=kid))

    db.commit()
    return {
        "jadwal_a_id": jadwal_a.id,
        "jadwal_b_id": jadwal_b.id,
        "jadwal_db_id": jadwal_db.id,
        "kelas_a_id": kelas_a.id,
        "kelas_b_id": kelas_b.id,
    }


def test_generate_qr_image_and_jwt_claims(client, db):
    """Step 1: GET /attendance/qr/{schedule_id} mengembalikan PNG valid + JWT 15 menit unik."""
    world = _seed_base_attendance_world(db)
    monday = datetime(2026, 11, 23, 8, 0, 0)  # Senin

    resp = client.get(
        f"/attendance/qr/{world['jadwal_a_id']}",
        params={
            "session_date": "2026-11-23",
            "generated_at": monday.isoformat(),
        },
        headers={"X-Role": "dosen"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    token = resp.headers["x-attendance-token"]
    claims = decode_attendance_token(token, now=monday + timedelta(minutes=5))
    assert claims.schedule_id == world["jadwal_a_id"]
    assert claims.session_id == int(resp.headers["x-session-id"])
    assert (claims.exp - claims.iat) == timedelta(minutes=15)

    # Mahasiswa tidak boleh generate QR (403)
    forbidden = client.get(
        f"/attendance/qr/{world['jadwal_a_id']}",
        headers={"X-Role": "mahasiswa"},
    )
    assert forbidden.status_code == 403

    # Jadwal tidak ada -> 404
    missing = client.get("/attendance/qr/99999", headers={"X-Role": "dosen"})
    assert missing.status_code == 404


def test_scan_hadir_terlambat_and_reject_after_30_minutes(client, db):
    """Step 2: Hadir <=15m, Terlambat 15-30m (dengan refresh QR), ditolak >30m."""
    world = _seed_base_attendance_world(db)
    t_start = datetime(2026, 11, 23, 8, 0, 0)  # Senin 08:00

    # Generate QR pertama pukul 08:00 (valid s.d. 08:15)
    qr1 = client.post(
        f"/attendance/qr/{world['jadwal_a_id']}/session",
        params={"generated_at": t_start.isoformat()},
        json={"valid_minutes": 15, "session_date": "2026-11-23"},
    )
    assert qr1.status_code == 201
    token1 = qr1.json()["token"]
    assert qr1.json()["qr_base64"].startswith("data:image/png;base64,")

    # Andi scan pukul 08:08 (8 menit -> HADIR)
    scan_andi = client.post(
        "/attendance/scan",
        json={
            "token": token1,
            "nim": "20260010001",
            "scanned_at": (t_start + timedelta(minutes=8)).isoformat(),
        },
    )
    assert scan_andi.status_code == 200
    body_andi = scan_andi.json()
    assert body_andi["success"] is True
    assert body_andi["status"] == "hadir"

    # Dosen refresh QR pukul 08:12 (QR baru valid s.d. 08:27; jti lama direvoke)
    qr2 = client.post(
        f"/attendance/qr/{world['jadwal_a_id']}/session",
        params={"generated_at": (t_start + timedelta(minutes=12)).isoformat()},
        json={"valid_minutes": 15, "session_date": "2026-11-23"},
    )
    assert qr2.status_code == 201
    token2 = qr2.json()["token"]
    assert token2 != token1

    # Replay attack: pakai token1 lama meski belum lewat 15m -> ditolak 401!
    replay = client.post(
        "/attendance/scan",
        json={
            "token": token1,
            "nim": "20260010002",
            "scanned_at": (t_start + timedelta(minutes=13)).isoformat(),
        },
    )
    assert replay.status_code == 401
    assert "replay" in replay.json()["detail"].lower()

    # Budi scan memakai token2 pukul 08:20 (QR valid, namun 20 menit sejak 08:00 -> TERLAMBAT)
    scan_budi = client.post(
        "/attendance/scan",
        json={
            "token": token2,
            "nim": "20260010002",
            "scanned_at": (t_start + timedelta(minutes=20)).isoformat(),
        },
    )
    assert scan_budi.status_code == 200
    assert scan_budi.json()["success"] is True
    assert scan_budi.json()["status"] == "terlambat"

    # Refresh QR pukul 08:25 lalu Citra coba scan pukul 08:35 (>30 menit dari mulai kuliah -> 400)
    qr3 = client.post(
        f"/attendance/qr/{world['jadwal_a_id']}/session",
        params={"generated_at": (t_start + timedelta(minutes=25)).isoformat()},
        json={"valid_minutes": 15, "session_date": "2026-11-23"},
    )
    token3 = qr3.json()["token"]
    late_reject = client.post(
        "/attendance/scan",
        json={
            "token": token3,
            "nim": "20260010003",
            "scanned_at": (t_start + timedelta(minutes=35)).isoformat(),
        },
    )
    assert late_reject.status_code == 400
    assert "30 menit" in late_reject.json()["detail"]


def test_expired_qr_wrong_class_wrong_day_and_duplicate_scan(client, db):
    """Kriteria sukses: expired QR ditolak, kelas orang lain ditolak, hari salah ditolak, duplikat ditolak."""
    world = _seed_base_attendance_world(db)
    t_start = datetime(2026, 11, 23, 8, 0, 0)  # Senin

    qr_resp = client.get(
        f"/attendance/qr/{world['jadwal_a_id']}",
        params={"session_date": "2026-11-23", "generated_at": t_start.isoformat()},
    )
    token = qr_resp.headers["x-attendance-token"]

    # 1. Expired QR (>15 menit) -> 401
    expired = client.post(
        "/attendance/scan",
        json={
            "token": token,
            "nim": "20260010001",
            "scanned_at": (t_start + timedelta(minutes=16)).isoformat(),
        },
    )
    assert expired.status_code == 401
    assert "expired" in expired.json()["detail"].lower()

    # 2. Mahasiswa tidak terdaftar di MK IF101 (Eka hanya ambil IF202) -> 403
    not_enrolled = client.post(
        "/attendance/scan",
        json={
            "token": token,
            "nim": "20260010005",
            "scanned_at": (t_start + timedelta(minutes=5)).isoformat(),
        },
    )
    assert not_enrolled.status_code == 403

    # 3. Mahasiswa terdaftar di MK sama tetapi KELAS PARALEL LAIN (Dodi di Kelas B) -> 403
    wrong_class = client.post(
        "/attendance/scan",
        json={
            "token": token,
            "nim": "20260010004",
            "scanned_at": (t_start + timedelta(minutes=5)).isoformat(),
        },
    )
    assert wrong_class.status_code == 403
    assert "kelas lain" in wrong_class.json()["detail"].lower()

    # 4. Scan valid pertama Andi -> 200
    ok = client.post(
        "/attendance/scan",
        json={
            "token": token,
            "nim": "20260010001",
            "scanned_at": (t_start + timedelta(minutes=5)).isoformat(),
        },
    )
    assert ok.status_code == 200

    # 5. Duplicate scan oleh Andi di sesi yang sama -> 409
    dup = client.post(
        "/attendance/scan",
        json={
            "token": token,
            "nim": "20260010001",
            "scanned_at": (t_start + timedelta(minutes=7)).isoformat(),
        },
    )
    assert dup.status_code == 409

    # 6. Jadwal hari salah (Selasa untuk jadwal Senin) -> 400
    tuesday = datetime(2026, 11, 24, 8, 5, 0)
    qr_tue = client.get(
        f"/attendance/qr/{world['jadwal_a_id']}",
        params={"session_date": "2026-11-24", "generated_at": tuesday.isoformat()},
    )
    wrong_day = client.post(
        "/attendance/scan",
        json={
            "token": qr_tue.headers["x-attendance-token"],
            "nim": "20260010002",
            "scanned_at": (tuesday + timedelta(minutes=2)).isoformat(),
        },
    )
    assert wrong_day.status_code == 400
    assert "tidak ada jadwal kuliah" in wrong_day.json()["detail"].lower()


def test_geolocation_validation_and_rate_limiting(client, db):
    """Geolocation Haversine (opsional) + Security Rate Limiting 429."""
    world = _seed_base_attendance_world(db)
    t_start = datetime(2026, 11, 23, 8, 0, 0)

    # Sesi dengan lokasi kampus UI Depok (-6.3649, 106.8286), radius 100 meter
    qr_resp = client.post(
        f"/attendance/qr/{world['jadwal_a_id']}/session",
        params={"generated_at": t_start.isoformat()},
        json={
            "valid_minutes": 15,
            "session_date": "2026-11-23",
            "latitude": -6.3649,
            "longitude": 106.8286,
            "radius_meters": 100.0,
            "require_location": True,
        },
    )
    assert qr_resp.status_code == 201
    token = qr_resp.json()["token"]

    # Tanpa koordinat -> 400
    no_loc = client.post(
        "/attendance/scan",
        json={
            "token": token,
            "nim": "20260010001",
            "scanned_at": (t_start + timedelta(minutes=3)).isoformat(),
        },
    )
    assert no_loc.status_code == 400

    # Di luar radius (~550 meter jauhnya) -> 400
    far_loc = client.post(
        "/attendance/scan",
        json={
            "token": token,
            "nim": "20260010001",
            "latitude": -6.3700,
            "longitude": 106.8286,
            "scanned_at": (t_start + timedelta(minutes=4)).isoformat(),
        },
    )
    assert far_loc.status_code == 400
    assert "di luar radius" in far_loc.json()["detail"].lower()

    # Dalam radius (~22 meter) -> 200 + distance_meters tercatat
    near_loc = client.post(
        "/attendance/scan",
        json={
            "token": token,
            "nim": "20260010001",
            "latitude": -6.3651,
            "longitude": 106.8286,
            "scanned_at": (t_start + timedelta(minutes=5)).isoformat(),
        },
    )
    assert near_loc.status_code == 200
    assert near_loc.json()["distance_meters"] is not None
    assert near_loc.json()["distance_meters"] < 100.0

    # Rate Limiting: spam scan 5x boleh diproses, ke-6 wajib 429 Too Many Requests
    reset_scan_rate_limiter()
    for i in range(5):
        r = client.post(
            "/attendance/scan",
            json={
                "token": "invalid.jwt.token",
                "nim": "20260010002",
            },
        )
        assert r.status_code == 401
    r6 = client.post(
        "/attendance/scan",
        json={
            "token": "invalid.jwt.token",
            "nim": "20260010002",
        },
    )
    assert r6.status_code == 429
    assert "retry-after" in r6.headers


def test_attendance_reports_close_session_and_excel_export(client, db):
    """Deliverable 3: Report per mahasiswa, per MK (<75% warning), & Export Excel (.xlsx) valid."""
    world = _seed_base_attendance_world(db)
    mondays = [
        datetime(2026, 11, 2, 8, 0, 0),
        datetime(2026, 11, 9, 8, 0, 0),
        datetime(2026, 11, 16, 8, 0, 0),
        datetime(2026, 11, 23, 8, 0, 0),
    ]

    # 4 pertemuan:
    # Andi (001): hadir 4/4 (100% -> AMAN)
    # Budi (002): hadir 1/4 (25% -> WARNING)
    # Citra (003): hadir 2/4 (50% -> WARNING)
    for idx, t_start in enumerate(mondays):
        reset_scan_rate_limiter()
        qr_res = client.post(
            f"/attendance/qr/{world['jadwal_a_id']}/session",
            params={"generated_at": t_start.isoformat()},
            json={"valid_minutes": 15, "session_date": t_start.date().isoformat()},
        )
        session_id = qr_res.json()["session_id"]
        token = qr_res.json()["token"]

        # Andi selalu hadir
        assert (
            client.post(
                "/attendance/scan",
                json={
                    "token": token,
                    "nim": "20260010001",
                    "scanned_at": (t_start + timedelta(minutes=5)).isoformat(),
                },
            ).status_code
            == 200
        )
        # Budi hanya hadir di pertemuan 1
        if idx == 0:
            assert (
                client.post(
                    "/attendance/scan",
                    json={
                        "token": token,
                        "nim": "20260010002",
                        "scanned_at": (t_start + timedelta(minutes=6)).isoformat(),
                    },
                ).status_code
                == 200
            )
        # Citra hadir di pertemuan 1 dan 2
        if idx in (0, 1):
            assert (
                client.post(
                    "/attendance/scan",
                    json={
                        "token": token,
                        "nim": "20260010003",
                        "scanned_at": (t_start + timedelta(minutes=7)).isoformat(),
                    },
                ).status_code
                == 200
            )

        # Tutup sesi -> otomatis catat alpha bagi yang tidak scan
        close_res = client.post(f"/attendance/sessions/{session_id}/close")
        assert close_res.status_code == 200
        assert close_res.json()["status"] == "closed"

    # Cek list sesi
    sessions_res = client.get("/attendance/sessions", params={"kode_mk": "IF101"})
    assert sessions_res.status_code == 200
    assert len(sessions_res.json()) == 4

    # Report per mahasiswa (Budi: 25% warning)
    student_res = client.get("/attendance/report/student/20260010002")
    assert student_res.status_code == 200
    st_data = student_res.json()
    assert st_data["warning_course_count"] == 1
    assert st_data["courses"][0]["attendance_percentage"] == 25.0
    assert st_data["courses"][0]["warning"] is True

    # Report per MK (IF101 Kelas A)
    course_res = client.get(
        "/attendance/report/course/IF101",
        params={"semester": "20261", "kelas_id": world["kelas_a_id"]},
    )
    assert course_res.status_code == 200
    c_data = course_res.json()
    assert c_data["total_sessions"] == 4
    assert c_data["warning_count"] == 2
    warn_nims = [w["nim"] for w in c_data["warning_students"]]
    assert warn_nims == ["20260010002", "20260010003"]

    # Export Excel (.xlsx) dan validasi isi workbook dengan openpyxl
    excel_res = client.get(
        "/attendance/report/course/IF101/export",
        params={"semester": "20261", "kelas_id": world["kelas_a_id"]},
        headers={"X-Role": "dosen"},
    )
    assert excel_res.status_code == 200
    assert (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        in excel_res.headers["content-type"]
    )
    wb = load_workbook(io.BytesIO(excel_res.content))
    assert wb.sheetnames == ["Rekap Presensi", "Warning (<75%)", "Log Presensi Detail"]
    ws_rekap = wb["Rekap Presensi"]
    nims_in_excel = [ws_rekap.cell(row=r, column=2).value for r in range(5, 8)]
    assert set(nims_in_excel) == {"20260010001", "20260010002", "20260010003"}
    ws_warn = wb["Warning (<75%)"]
    warn_in_excel = [ws_warn.cell(row=r, column=2).value for r in range(4, 6)]
    assert set(warn_in_excel) == {"20260010002", "20260010003"}

    # AI Attendance Insights langsung dari DB
    ai_db_res = client.get("/attendance/ai-insights", params={"kode_mk": "IF101"})
    assert ai_db_res.status_code == 200
    ai_db = ai_db_res.json()
    assert ai_db["summary"]["at_risk_count"] == 2
    assert "2 mahasiswa berisiko" in ai_db["summary"]["early_warning"]
    assert len(ai_db["insights"]) >= 2
    assert len(ai_db["actions"]) >= 2


def test_ai_attendance_analyzer_json_array_and_portal_ui(client):
    """Step 3: POST /attendance/ai-insights dengan attendance_records JSON array + Web UI."""
    records = [
        # Budi sering absent Senin pagi (07:00) -> 25% hadir, 75% absent Senin pagi
        {"nim": "2026001", "nama": "Budi", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "hadir"},
        {"nim": "2026001", "nama": "Budi", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "absent"},
        {"nim": "2026001", "nama": "Budi", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "absent"},
        {"nim": "2026001", "nama": "Budi", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "absent"},
        # Sinta 50% (berisiko <75%)
        {"nim": "2026002", "nama": "Sinta", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "hadir"},
        {"nim": "2026002", "nama": "Sinta", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "terlambat"},
        {"nim": "2026002", "nama": "Sinta", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "alpha"},
        {"nim": "2026002", "nama": "Sinta", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "alpha"},
        # Andi 100% hadir
        {"nim": "2026003", "nama": "Andi", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "hadir"},
        {"nim": "2026003", "nama": "Andi", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "hadir"},
        {"nim": "2026003", "nama": "Andi", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "hadir"},
        {"nim": "2026003", "nama": "Andi", "kelas": "IF101-A", "hari": "Senin", "jam_mulai": "07:00", "status": "hadir"},
    ]

    resp = client.post("/attendance/ai-insights", json={"attendance_records": records})
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_students"] == 3
    assert data["summary"]["at_risk_count"] == 2
    assert "2 mahasiswa berisiko" in data["summary"]["early_warning"]

    action_msgs = " | ".join(a["message"] for a in data["actions"])
    assert "konseling" in action_msgs.lower()
    assert "reschedule" in action_msgs.lower()
    assert "07:00" in action_msgs

    # Portal web UI dosen
    ui_res = client.get("/dosen/attendance")
    assert ui_res.status_code == 200
    assert "Portal Presensi QR Code" in ui_res.text


def test_direct_generate_attendance_qr_helper():
    """Memastikan helper `generate_attendance_qr(schedule_id, valid_minutes=15)` bekerja mandiri."""
    now = datetime(2026, 11, 23, 8, 0, 0)
    qr1, token1 = generate_attendance_qr(42, valid_minutes=15, now=now)
    qr2, token2 = generate_attendance_qr(42, valid_minutes=15, now=now)
    assert token1 != token2  # jti unik setiap kali generate
    c1 = decode_attendance_token(token1, now=now + timedelta(minutes=10))
    c2 = decode_attendance_token(token2, now=now + timedelta(minutes=10))
    assert c1.schedule_id == 42
    assert c1.jti != c2.jti
    assert qr1 is not None and qr2 is not None


@given(
    lat1=st.floats(min_value=-89.0, max_value=89.0, allow_nan=False, allow_infinity=False),
    lon1=st.floats(min_value=-179.0, max_value=179.0, allow_nan=False, allow_infinity=False),
    lat2=st.floats(min_value=-89.0, max_value=89.0, allow_nan=False, allow_infinity=False),
    lon2=st.floats(min_value=-179.0, max_value=179.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=80)
def test_pbt_haversine_metric_properties(lat1, lon1, lat2, lon2):
    """Property-Based Test: Haversine bersifat non-negatif, simetris, dan jarak titik ke diri sendiri = 0."""
    assert haversine_distance_meters(lat1, lon1, lat1, lon1) == 0.0
    d12 = haversine_distance_meters(lat1, lon1, lat2, lon2)
    d21 = haversine_distance_meters(lat2, lon2, lat1, lon1)
    assert d12 >= 0.0
    assert abs(d12 - d21) <= 0.01


@given(
    statuses=st.lists(
        st.sampled_from(["hadir", "terlambat", "alpha", "izin"]),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=100)
def test_pbt_attendance_percentage_and_warning_invariant(statuses):
    """Property-Based Test: Persentase kehadiran selalu 0..100 dan warning tepat saat < 75%."""
    records = [
        {
            "nim": "2026999",
            "nama": "Tester PBT",
            "kelas": "IF101-A",
            "hari": "Senin",
            "jam_mulai": "08:00",
            "status": s,
        }
        for s in statuses
    ]
    res = analyze_attendance_records(records)
    st_row = res["student_attendance"][0]
    expected_attended = sum(1 for s in statuses if s in ("hadir", "terlambat"))
    expected_pct = round((expected_attended / len(statuses)) * 100.0, 2)

    assert 0.0 <= st_row["attendance_percentage"] <= 100.0
    assert st_row["attendance_percentage"] == expected_pct
    assert st_row["warning"] is (expected_pct < 75.0)
    assert res["summary"]["at_risk_count"] == (1 if expected_pct < 75.0 else 0)
