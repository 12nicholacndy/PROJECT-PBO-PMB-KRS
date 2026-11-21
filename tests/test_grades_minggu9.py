from datetime import time

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import (
    Grade,
    GradeHistory,
    KRS,
    KRSDetail,
    MataKuliah,
    StudentAcademicSummary,
)
from app.services.gpa_calculator import (
    GRADE_SCALE,
    GradeRecord,
    calculate_ipk,
    calculate_ips,
    compute_ipk_stats,
    convert_grade_to_point,
    determine_predikat,
    get_transcript,
    is_passing_grade,
)
from app.services.grade_errors import GradeValidationError
from tests.conftest import daftar


def _buat_mahasiswa_approved(
    client: TestClient,
    *,
    nama: str = "Budi Santoso",
    email: str = "budi@example.com",
    program_studi_id: int = 1,
) -> str:
    reg = daftar(
        client,
        nama_lengkap=nama,
        email=email,
        program_studi_id=program_studi_id,
    )
    app_res = client.put(f"/api/pmb/approve/{reg['id']}")
    assert app_res.status_code == 200
    return app_res.json()["nim"]


def _seed_courses(db: Session) -> list[MataKuliah]:
    courses = [
        MataKuliah(
            kode_mk="IF101",
            nama="Algoritma dan Pemrograman",
            sks=3,
            hari=1,
            jam_mulai=time(8, 0),
            jam_selesai=time(10, 30),
        ),
        MataKuliah(
            kode_mk="IF102",
            nama="Struktur Data",
            sks=4,
            hari=2,
            jam_mulai=time(8, 0),
            jam_selesai=time(11, 20),
        ),
        MataKuliah(
            kode_mk="IF103",
            nama="Basis Data",
            sks=3,
            hari=3,
            jam_mulai=time(9, 0),
            jam_selesai=time(11, 30),
        ),
        MataKuliah(
            kode_mk="IF104",
            nama="Pemrograman Berorientasi Objek",
            sks=3,
            hari=4,
            jam_mulai=time(10, 0),
            jam_selesai=time(12, 30),
        ),
        MataKuliah(
            kode_mk="IF105",
            nama="Etika Profesi",
            sks=2,
            hari=5,
            jam_mulai=time(8, 0),
            jam_selesai=time(9, 40),
        ),
    ]
    db.add_all(courses)
    db.commit()
    return courses


def _input_nilai_api(
    client: TestClient,
    *,
    nim: str,
    kode_mk: str,
    semester: str,
    nilai_huruf: str,
    presensi_persen: float = 90.0,
    created_by: str = "Dr. Budi Hartono",
    role: str = "dosen",
):
    return client.post(
        "/api/grades",
        json={
            "nim": nim,
            "kode_mk": kode_mk,
            "semester": semester,
            "nilai_huruf": nilai_huruf,
            "presensi_persen": presensi_persen,
            "created_by": created_by,
        },
        headers={"X-Role": role},
    )


# =============================================================================
# 1. Konversi Huruf -> Angka & Predikat Kelulusan
# =============================================================================


@pytest.mark.parametrize(
    "huruf,expected_point,expected_pass",
    [
        ("A", 4.0, True),
        ("a", 4.0, True),
        ("A-", 3.7, True),
        ("B+", 3.3, True),
        ("B", 3.0, True),
        ("B-", 2.7, True),
        ("C+", 2.3, True),
        ("C", 2.0, True),
        ("D", 1.0, True),
        ("E", 0.0, False),
    ],
)
def test_konversi_huruf_ke_angka_dan_kelulusan(huruf, expected_point, expected_pass):
    assert convert_grade_to_point(huruf) == expected_point
    assert is_passing_grade(huruf) is expected_pass


def test_konversi_huruf_tidak_valid_melempar_error():
    with pytest.raises(GradeValidationError):
        convert_grade_to_point("Z")


@pytest.mark.parametrize(
    "ipk,sks,expected",
    [
        (0.0, 0, "Belum Ada Nilai"),
        (4.0, 12, "Cum Laude"),
        (3.50, 12, "Cum Laude"),
        (3.49, 12, "Sangat Memuaskan"),
        (3.00, 12, "Sangat Memuaskan"),
        (2.99, 12, "Memuaskan"),
        (2.50, 12, "Memuaskan"),
        (2.49, 12, "Cukup"),
        (2.00, 12, "Cukup"),
        (1.99, 12, "Kurang"),
        (1.00, 12, "Kurang"),
    ],
)
def test_determine_predikat_batas_tepat(ipk, sks, expected):
    assert determine_predikat(ipk, sks) == expected


# =============================================================================
# 2. Pengujian Perhitungan IPK — 12 Kasus (Melebihi Kriteria 10+ Kasus)
# =============================================================================


@pytest.mark.parametrize(
    "case_name,entries,expected_ipk,expected_sks,expected_predikat",
    [
        (
            "01_semester_awal_belum_ada_nilai",
            [],
            0.0,
            0,
            "Belum Ada Nilai",
        ),
        (
            "02_nilai_sempurna_semua_A",
            [
                ("IF101", "20251", "A"),
                ("IF102", "20251", "A"),
                ("IF103", "20251", "A"),
            ],
            4.0,
            10,
            "Cum Laude",
        ),
        (
            "03_mk_diulang_nilai_naik_D_ke_A",
            [
                ("IF101", "20251", "D"),
                ("IF101", "20252", "A"),
                ("IF103", "20251", "B"),
            ],
            3.5,  # (3*4.0 + 3*3.0) / 6 = 21/6 = 3.50
            6,
            "Cum Laude",
        ),
        (
            "04_mk_diulang_nilai_turun_B_ke_C_ambil_tertinggi",
            [
                ("IF101", "20251", "B"),
                ("IF101", "20252", "C"),
                ("IF105", "20251", "A"),
            ],
            3.4,  # (3*3.0 + 2*4.0) / 5 = 17/5 = 3.40
            5,
            "Sangat Memuaskan",
        ),
        (
            "05_mk_diulang_3_kali_E_D_B",
            [
                ("IF102", "20241", "E"),
                ("IF102", "20242", "D"),
                ("IF102", "20251", "B"),
            ],
            3.0,  # hanya nilai tertinggi B (3.0) pada 4 SKS
            4,
            "Sangat Memuaskan",
        ),
        (
            "06_semua_nilai_E_tidak_lulus",
            [
                ("IF101", "20251", "E"),
                ("IF102", "20251", "E"),
            ],
            0.0,
            0,
            "Belum Ada Nilai",
        ),
        (
            "07_campuran_lulus_dan_E_abaikan_E",
            [
                ("IF101", "20251", "A"),  # 3 * 4 = 12
                ("IF102", "20251", "E"),  # diabaikan
                ("IF103", "20251", "D"),  # 3 * 1 = 3 (lulus >= D)
            ],
            2.5,  # (12 + 3) / 6 = 2.50
            6,
            "Memuaskan",
        ),
        (
            "08_bobot_sks_berbeda_pembulatan_2_desimal",
            [
                ("IF105", "20251", "A"),  # 2 * 4 = 8
                ("IF102", "20251", "C"),  # 4 * 2 = 8
            ],
            2.67,  # 16 / 6 = 2.6666... -> 2.67
            6,
            "Memuaskan",
        ),
        (
            "09_skala_plus_minus_campuran",
            [
                ("IF101", "20251", "A-"),  # 3 * 3.7 = 11.1
                ("IF102", "20251", "B+"),  # 4 * 3.3 = 13.2
                ("IF105", "20251", "B-"),  # 2 * 2.7 = 5.4
            ],
            3.3,  # 29.7 / 9 = 3.30
            9,
            "Sangat Memuaskan",
        ),
        (
            "10_batas_tepat_cum_laude_3_50",
            [
                ("IF101", "20251", "A"),  # 3 * 4 = 12
                ("IF104", "20251", "B"),  # 3 * 3 = 9
            ],
            3.5,
            6,
            "Cum Laude",
        ),
        (
            "11_predikat_cukup_2_00_sampai_2_49",
            [
                ("IF101", "20251", "C"),  # 3 * 2 = 6
                ("IF104", "20251", "C+"),  # 3 * 2.3 = 6.9
            ],
            2.15,  # 12.9 / 6 = 2.15
            6,
            "Cukup",
        ),
        (
            "12_semua_nilai_D_lulus_tapi_predikat_kurang",
            [
                ("IF101", "20251", "D"),
                ("IF103", "20251", "D"),
            ],
            1.0,
            6,
            "Kurang",
        ),
    ],
)
def test_calculate_ipk_12_kasus(
    client: TestClient,
    db: Session,
    case_name: str,
    entries: list[tuple[str, str, str]],
    expected_ipk: float,
    expected_sks: int,
    expected_predikat: str,
):
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(client, email=f"{case_name}@example.com")

    for kode_mk, semester, huruf in entries:
        res = _input_nilai_api(
            client,
            nim=nim,
            kode_mk=kode_mk,
            semester=semester,
            nilai_huruf=huruf,
        )
        assert res.status_code == 201, res.text

    # Verifikasi fungsi calculate_ipk langsung + endpoint API + tabel summary otomatis
    assert calculate_ipk(db, nim) == expected_ipk

    ipk_res = client.get(f"/api/grades/{nim}/ipk")
    assert ipk_res.status_code == 200
    body = ipk_res.json()
    assert body["ipk"] == expected_ipk
    assert body["total_sks_lulus"] == expected_sks
    assert body["predikat"] == expected_predikat


def test_calculate_ips_per_semester_dan_kosong(client: TestClient, db: Session):
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(client, email="ips@example.com")

    # Semester belum ada nilai -> 0.0
    assert calculate_ips(db, nim, "20251") == 0.0

    # Semester 20251: IF101 (3 SKS, A=4.0 -> 12) + IF102 (4 SKS, E=0.0 -> 0) => IPS = 12/7 = 1.71
    _input_nilai_api(client, nim=nim, kode_mk="IF101", semester="20251", nilai_huruf="A")
    _input_nilai_api(client, nim=nim, kode_mk="IF102", semester="20251", nilai_huruf="E")

    # Semester 20252: IF102 diulang (4 SKS, B=3.0 -> 12) => IPS = 3.00
    _input_nilai_api(client, nim=nim, kode_mk="IF102", semester="20252", nilai_huruf="B")

    assert calculate_ips(db, nim, "20251") == 1.71
    assert calculate_ips(db, nim, "20252") == 3.0

    res = client.get(f"/api/grades/{nim}/ips/20251")
    assert res.status_code == 200
    assert res.json()["ips"] == 1.71


# =============================================================================
# 3. Business Rules, Validasi Presensi < 75%, Otorisasi & Sinkronisasi KRS
# =============================================================================


def test_business_rule_presensi_kurang_dari_75_ditolak(client: TestClient, db: Session):
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(client, email="absen@example.com")

    res_low = _input_nilai_api(
        client,
        nim=nim,
        kode_mk="IF101",
        semester="20251",
        nilai_huruf="A",
        presensi_persen=74.9,
    )
    assert res_low.status_code == 400
    assert "75%" in res_low.json()["detail"]
    assert res_low.json()["presensi_persen"] == 74.9

    # Batas tepat 75.0% harus diterima
    res_ok = _input_nilai_api(
        client,
        nim=nim,
        kode_mk="IF101",
        semester="20251",
        nilai_huruf="A",
        presensi_persen=75.0,
    )
    assert res_ok.status_code == 201
    assert res_ok.json()["summary"]["ipk"] == 4.0


def test_validasi_input_duplikat_peran_dan_not_found(client: TestClient, db: Session):
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(client, email="rules@example.com")

    # Peran mahasiswa dilarang menginput nilai
    res_forbidden = _input_nilai_api(
        client,
        nim=nim,
        kode_mk="IF101",
        semester="20251",
        nilai_huruf="A",
        role="mahasiswa",
    )
    assert res_forbidden.status_code == 403

    # Presensi di luar rentang 0..100
    res_invalid_att = _input_nilai_api(
        client,
        nim=nim,
        kode_mk="IF101",
        semester="20251",
        nilai_huruf="A",
        presensi_persen=120.0,
    )
    assert res_invalid_att.status_code == 400

    # Mahasiswa tidak ditemukan
    res_404_mhs = _input_nilai_api(
        client,
        nim="99999999999",
        kode_mk="IF101",
        semester="20251",
        nilai_huruf="A",
    )
    assert res_404_mhs.status_code == 404

    # MK tidak ditemukan
    res_404_mk = _input_nilai_api(
        client,
        nim=nim,
        kode_mk="ZZ999",
        semester="20251",
        nilai_huruf="A",
    )
    assert res_404_mk.status_code == 404

    # Input sah pertama lalu duplikat pada semester sama -> 409 Conflict
    assert (
        _input_nilai_api(
            client, nim=nim, kode_mk="IF101", semester="20251", nilai_huruf="B"
        ).status_code
        == 201
    )
    res_dup = _input_nilai_api(
        client, nim=nim, kode_mk="IF101", semester="20251", nilai_huruf="A"
    )
    assert res_dup.status_code == 409


def test_sinkronisasi_krs_detail_dan_auto_update_summary(client: TestClient, db: Session):
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(client, email="sync@example.com")

    krs = KRS(nim=nim, semester="20251")
    db.add(krs)
    db.flush()
    detail = KRSDetail(krs_id=krs.id, kode_mk="IF101", nilai=None)
    db.add(detail)
    db.commit()

    res = _input_nilai_api(
        client, nim=nim, kode_mk="IF101", semester="20251", nilai_huruf="B"
    )
    assert res.status_code == 201
    assert res.json()["summary"]["ipk"] == 3.0

    db.expire_all()
    refreshed_detail = db.get(KRSDetail, detail.id)
    assert refreshed_detail is not None
    assert refreshed_detail.nilai == "B"

    summary = db.get(StudentAcademicSummary, nim)
    assert summary is not None
    assert summary.ipk == 3.0
    assert summary.total_sks_lulus == 3
    assert summary.predikat == "Sangat Memuaskan"


# =============================================================================
# 4. Audit Trail & Database Trigger (`grade_history`)
# =============================================================================


def test_edit_nilai_memicu_audit_trail_dan_update_ipk_otomatis(
    client: TestClient, db: Session
):
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(client, email="audit@example.com")

    create_res = _input_nilai_api(
        client, nim=nim, kode_mk="IF101", semester="20251", nilai_huruf="C"
    )
    assert create_res.status_code == 201
    grade_id = create_res.json()["grade"]["id"]
    assert create_res.json()["summary"]["ipk"] == 2.0

    # Edit nilai yang sama harus ditolak agar audit trail bersih
    same_res = client.put(
        f"/api/grades/{grade_id}",
        json={
            "nilai_huruf": "C",
            "changed_by": "Dr. Sari",
            "reason": "Cek ulang",
        },
        headers={"X-Role": "dosen"},
    )
    assert same_res.status_code == 400

    # Edit 1: C -> B
    upd1 = client.put(
        f"/api/grades/{grade_id}",
        json={
            "nilai_huruf": "B",
            "changed_by": "Dr. Sari",
            "reason": "Koreksi salah input nilai UAS",
        },
        headers={"X-Role": "dosen"},
    )
    assert upd1.status_code == 200
    body1 = upd1.json()
    assert body1["grade"]["nilai_huruf"] == "B"
    assert body1["summary"]["ipk"] == 3.0
    assert body1["history"]["old_value"] == "C"
    assert body1["history"]["new_value"] == "B"
    assert body1["history"]["changed_by"] == "Dr. Sari"
    assert body1["history"]["reason"] == "Koreksi salah input nilai UAS"

    # Edit 2: B -> A
    upd2 = client.put(
        f"/api/grades/{grade_id}",
        json={
            "nilai_huruf": "A",
            "changed_by": "Prof. Hendra",
            "reason": "Penambahan nilai tugas bonus",
        },
        headers={"X-Role": "admin_akademik"},
    )
    assert upd2.status_code == 200
    assert upd2.json()["summary"]["ipk"] == 4.0
    assert upd2.json()["summary"]["predikat"] == "Cum Laude"

    # Cek view riwayat perubahan per mahasiswa (terbaru di atas)
    hist_res = client.get(f"/api/grades/{nim}/history")
    assert hist_res.status_code == 200
    hist = hist_res.json()
    assert len(hist) == 2
    assert hist[0]["old_value"] == "B"
    assert hist[0]["new_value"] == "A"
    assert hist[0]["changed_by"] == "Prof. Hendra"
    assert hist[1]["old_value"] == "C"
    assert hist[1]["new_value"] == "B"
    assert hist[1]["changed_by"] == "Dr. Sari"


def test_sqlite_trigger_audit_bekerja_pada_raw_sql_update(
    client: TestClient, db: Session
):
    """Membuktikan trigger database `trg_grades_after_update` aktif langsung di level SQL."""
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(client, email="trigger@example.com")

    create_res = _input_nilai_api(
        client, nim=nim, kode_mk="IF103", semester="20251", nilai_huruf="D"
    )
    grade_id = create_res.json()["grade"]["id"]

    db.execute(
        text(
            "UPDATE grades "
            "SET nilai_huruf = 'B', nilai_angka = 3.0, "
            "    updated_by = 'SQL Admin', change_reason = 'Uji trigger DB' "
            "WHERE id = :gid"
        ),
        {"gid": grade_id},
    )
    db.commit()

    rows = db.query(GradeHistory).filter(GradeHistory.grade_id == grade_id).all()
    assert len(rows) == 1
    assert rows[0].old_value == "D"
    assert rows[0].new_value == "B"
    assert rows[0].changed_by == "SQL Admin"
    assert rows[0].reason == "Uji trigger DB"


# =============================================================================
# 5. Transcript JSON, HTML Template Profesional, PDF Download & Portal Dosen UI
# =============================================================================


def test_transcript_json_html_dan_pdf_download(client: TestClient, db: Session):
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(
        client,
        nama="Nadia Kusuma",
        email="nadia@example.com",
        program_studi_id=2,
    )

    _input_nilai_api(client, nim=nim, kode_mk="IF101", semester="20251", nilai_huruf="D")
    _input_nilai_api(client, nim=nim, kode_mk="IF102", semester="20251", nilai_huruf="A")
    _input_nilai_api(client, nim=nim, kode_mk="IF103", semester="20251", nilai_huruf="E")
    _input_nilai_api(client, nim=nim, kode_mk="IF101", semester="20252", nilai_huruf="A")

    # 1. JSON full academic record
    t_data = get_transcript(db, nim)
    assert t_data.biodata.nama == "Nadia Kusuma"
    assert t_data.biodata.program_studi == "Teknik Informatika"
    assert len(t_data.semesters) == 2
    assert t_data.ipk == 4.0
    assert t_data.total_sks_lulus == 7
    assert t_data.predikat == "Cum Laude"

    json_res = client.get(f"/api/grades/{nim}/transcript")
    assert json_res.status_code == 200
    assert json_res.json()["ipk"] == 4.0

    # 2. HTML template dinamis dengan CSS profesional
    html_res = client.get(f"/api/grades/{nim}/transcript/html")
    assert html_res.status_code == 200
    html = html_res.text
    assert "TRANSKRIP AKADEMIK" in html
    assert nim in html
    assert "Nadia Kusuma" in html
    assert "Teknik Informatika" in html
    assert "IF101" in html
    assert "Cum Laude" in html
    assert "TTD" in html

    # 3. PDF generator ReportLab bisa di-download & valid
    pdf_res = client.get(f"/api/grades/{nim}/transcript/pdf")
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert f'filename="transkrip_{nim}.pdf"' in pdf_res.headers["content-disposition"]
    assert pdf_res.content.startswith(b"%PDF-")
    assert b"%%EOF" in pdf_res.content[-64:]
    assert len(pdf_res.content) > 1500


def test_pdf_transkrip_mahasiswa_semester_awal_tanpa_nilai(
    client: TestClient, db: Session
):
    nim = _buat_mahasiswa_approved(client, nama="Maba Baru", email="maba@example.com")

    pdf_res = client.get(f"/api/grades/{nim}/transcript/pdf")
    assert pdf_res.status_code == 200
    assert pdf_res.content.startswith(b"%PDF-")
    assert len(pdf_res.content) > 1000


def test_portal_dosen_web_ui_dan_daftar_mahasiswa_dan_nilai(
    client: TestClient, db: Session
):
    _seed_courses(db)
    nim = _buat_mahasiswa_approved(client, nama="Raka Pratama", email="raka@example.com")
    _input_nilai_api(client, nim=nim, kode_mk="IF101", semester="20251", nilai_huruf="A")

    ui_res = client.get("/dosen/grades")
    assert ui_res.status_code == 200
    assert "Form Input Nilai Dosen" in ui_res.text
    assert "grade_history" in ui_res.text

    students_res = client.get("/api/grades/students")
    assert students_res.status_code == 200
    students = students_res.json()
    assert len(students) == 1
    assert students[0]["nim"] == nim
    assert students[0]["ipk"] == 4.0

    grades_res = client.get(f"/api/grades?nim={nim}&semester=20251")
    assert grades_res.status_code == 200
    assert len(grades_res.json()) == 1
    assert grades_res.json()[0]["nilai_huruf"] == "A"


# =============================================================================
# 6. Property-Based Testing (Hypothesis) — Invarian Perhitungan IPK
# =============================================================================


@settings(max_examples=100)
@given(
    records_spec=st.lists(
        st.tuples(
            st.sampled_from(["IF101", "IF102", "IF103", "IF104", "IF105"]),
            st.sampled_from(["20241", "20242", "20251", "20252"]),
            st.sampled_from(list(GRADE_SCALE.keys())),
        ),
        min_size=0,
        max_size=15,
    )
)
def test_pbt_ipk_invariants(records_spec):
    sks_map = {"IF101": 3, "IF102": 4, "IF103": 3, "IF104": 3, "IF105": 2}
    # Hilangkan duplikat (kode_mk, semester) sesuai UniqueConstraint DB
    seen: set[tuple[str, str]] = set()
    records: list[GradeRecord] = []
    for kode_mk, sem, huruf in records_spec:
        if (kode_mk, sem) in seen:
            continue
        seen.add((kode_mk, sem))
        records.append(
            GradeRecord(
                kode_mk=kode_mk,
                nama_mk=f"MK {kode_mk}",
                sks=sks_map[kode_mk],
                semester=sem,
                nilai_huruf=huruf,
                nilai_angka=GRADE_SCALE[huruf],
            )
        )

    stats = compute_ipk_stats(records)

    # Invarian 1: IPK selalu di rentang [0.0, 4.0]
    assert 0.0 <= stats.ipk <= 4.0

    # Invarian 2: Total SKS lulus tidak pernah melebihi jumlah SKS unik seluruh MK
    assert stats.total_sks_lulus <= sum(sks_map.values())

    # Invarian 3: Jika ada MK lulus, IPK minimal 1.0 (nilai D) dan <= nilai tertinggi yang diraih
    passing_points = [r.nilai_angka for r in records if r.nilai_angka >= 1.0]
    if not passing_points:
        assert stats.ipk == 0.0
        assert stats.total_sks_lulus == 0
        assert stats.predikat == "Belum Ada Nilai"
    else:
        assert 1.0 <= stats.ipk <= max(passing_points) + 1e-9
        assert stats.total_sks_lulus > 0
