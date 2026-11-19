import re
from datetime import datetime, timezone

import pytest

from app.models import NIMSequence, ProgramStudi
from app.services.pmb_service import NIMTidakValid, format_nim
from tests.conftest import daftar


# --- Unit tests murni untuk format_nim (tanpa database) ---


def test_format_nim_valid():
    assert format_nim(2026, "001", 1) == "20260010001"
    assert format_nim(2026, "002", 42) == "20260020042"
    assert format_nim(2026, "001", 9999) == "20260019999"


@pytest.mark.parametrize("tahun", [99, 999, 10000])
def test_format_nim_invalid_year(tahun):
    with pytest.raises(NIMTidakValid):
        format_nim(tahun, "001", 1)


@pytest.mark.parametrize("kode_prodi", ["", "01", "0001", "ABC"])
def test_format_nim_invalid_kode_prodi(kode_prodi):
    with pytest.raises(NIMTidakValid):
        format_nim(2026, kode_prodi, 1)


@pytest.mark.parametrize("nomor_urut", [0, -1, 10000])
def test_format_nim_invalid_sequence_number(nomor_urut):
    with pytest.raises(NIMTidakValid):
        format_nim(2026, "001", nomor_urut)


# --- Integration tests untuk Approve & Generator NIM ---


def test_approve_generate_nim_format_and_sequence(client):
    sinta = daftar(client, nama_lengkap="Sinta Dewi", email="sinta@example.com")
    budi = daftar(client, nama_lengkap="Budi Santoso", email="budi@example.com")

    res_sinta = client.put(f"/api/pmb/approve/{sinta['id']}")
    res_budi = client.put(f"/api/pmb/approve/{budi['id']}")

    assert res_sinta.status_code == 200
    assert res_budi.status_code == 200

    nim_sinta = res_sinta.json()["nim"]
    nim_budi = res_budi.json()["nim"]

    assert res_sinta.json()["status"] == "approved"
    assert res_sinta.json()["approved_at"] is not None
    assert re.fullmatch(r"\d{4}001\d{4}", nim_sinta)
    assert re.fullmatch(r"\d{4}001\d{4}", nim_budi)
    assert int(nim_budi[-4:]) == int(nim_sinta[-4:]) + 1


def test_approve_nim_resets_per_prodi(client):
    si = daftar(
        client,
        nama_lengkap="Ani SI",
        email="ani@example.com",
        program_studi_id=1,
    )
    ti = daftar(
        client,
        nama_lengkap="Doni TI",
        email="doni@example.com",
        program_studi_id=2,
    )

    nim_si = client.put(f"/api/pmb/approve/{si['id']}").json()["nim"]
    nim_ti = client.put(f"/api/pmb/approve/{ti['id']}").json()["nim"]

    assert nim_si.endswith("0010001")
    assert nim_ti.endswith("0020001")


def test_approve_idempotent_does_not_increment_counter(client):
    sinta = daftar(client, nama_lengkap="Sinta Dewi", email="sinta@example.com")
    budi = daftar(client, nama_lengkap="Budi Santoso", email="budi@example.com")

    first = client.put(f"/api/pmb/approve/{sinta['id']}")
    second = client.put(f"/api/pmb/approve/{sinta['id']}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["nim"] == second.json()["nim"]
    assert first.json()["approved_at"] == second.json()["approved_at"]

    # Pendaftar berikutnya harus mendapat nomor urut 0002 (bukan 0003).
    budi_res = client.put(f"/api/pmb/approve/{budi['id']}")
    assert budi_res.status_code == 200
    assert budi_res.json()["nim"].endswith("0010002")


def test_approve_not_found_returns_404(client):
    response = client.put("/api/pmb/approve/99999")
    assert response.status_code == 404


def test_approve_rejected_returns_409(client):
    pendaftar = daftar(client)
    rej = client.put(f"/api/pmb/reject/{pendaftar['id']}")
    assert rej.status_code == 200

    res = client.put(f"/api/pmb/approve/{pendaftar['id']}")
    assert res.status_code == 409


def test_approve_invalid_prodi_code_returns_400(client, db):
    db.add(
        ProgramStudi(
            id=3,
            kode="ABC",
            nama="Prodi Invalid",
            fakultas="Fakultas Uji",
        )
    )
    db.commit()

    pendaftar = daftar(client, program_studi_id=3)
    response = client.put(f"/api/pmb/approve/{pendaftar['id']}")
    assert response.status_code == 400


def test_approve_sequence_overflow_returns_400(client, db):
    current_year = datetime.now(timezone.utc).year
    db.add(NIMSequence(tahun=current_year, kode_prodi="001", last_number=9999))
    db.commit()

    pendaftar = daftar(client, program_studi_id=1)
    response = client.put(f"/api/pmb/approve/{pendaftar['id']}")
    assert response.status_code == 400


# --- Integration tests untuk Reject ---


def test_reject_pending_and_idempotent(client):
    pendaftar = daftar(client)

    first = client.put(f"/api/pmb/reject/{pendaftar['id']}")
    assert first.status_code == 200
    assert first.json()["status"] == "rejected"
    assert first.json()["nim"] is None

    second = client.put(f"/api/pmb/reject/{pendaftar['id']}")
    assert second.status_code == 200
    assert second.json()["status"] == "rejected"


def test_reject_approved_returns_409(client):
    pendaftar = daftar(client)
    assert client.put(f"/api/pmb/approve/{pendaftar['id']}").status_code == 200

    response = client.put(f"/api/pmb/reject/{pendaftar['id']}")
    assert response.status_code == 409


def test_reject_not_found_returns_404(client):
    response = client.put("/api/pmb/reject/99999")
    assert response.status_code == 404


# --- Integration tests untuk Status & Statistik ---


def test_get_status_hides_sensitive_personal_data(client):
    pendaftar = daftar(client)
    client.put(f"/api/pmb/approve/{pendaftar['id']}")

    response = client.get(f"/api/pmb/status/{pendaftar['id']}")
    assert response.status_code == 200
    body = response.json()

    assert body["id"] == pendaftar["id"]
    assert body["nama_lengkap"] == "Budi Santoso"
    assert body["status"] == "approved"
    assert body["nim"] is not None
    assert body["approved_at"] is not None

    # Pastikan data pribadi sensitif tidak ikut terekspos di endpoint status.
    assert "no_hp" not in body
    assert "alamat" not in body
    assert "tanggal_lahir" not in body
    assert "email" not in body


def test_get_status_not_found_returns_404(client):
    response = client.get("/api/pmb/status/99999")
    assert response.status_code == 404


def test_get_stats_grouped_correctly(client):
    p1 = daftar(
        client,
        nama_lengkap="Pendaftar Satu",
        email="satu@example.com",
        program_studi_id=1,
        jalur_masuk_id=1,
    )
    p2 = daftar(
        client,
        nama_lengkap="Pendaftar Dua",
        email="dua@example.com",
        program_studi_id=1,
        jalur_masuk_id=2,
    )
    daftar(
        client,
        nama_lengkap="Pendaftar Tiga",
        email="tiga@example.com",
        program_studi_id=1,
        jalur_masuk_id=1,
    )

    client.put(f"/api/pmb/approve/{p1['id']}")
    client.put(f"/api/pmb/reject/{p2['id']}")

    response = client.get("/api/pmb/stats")
    assert response.status_code == 200
    stats = response.json()

    assert stats["total"] == 3
    assert stats["per_status"] == {"pending": 1, "approved": 1, "rejected": 1}
    assert stats["per_program_studi"] == {
        "Sistem Informasi": 3,
        "Teknik Informatika": 0,
    }
    assert stats["per_jalur_masuk"] == {
        "SNBP": 2,
        "SNBT": 1,
        "Mandiri": 0,
    }


def test_home_and_swagger_docs_available(client):
    assert client.get("/").status_code == 200
    assert client.get("/docs").status_code == 200
