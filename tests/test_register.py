from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, JalurMasuk, ProgramStudi

TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

@event.listens_for(test_engine, "connect")
def enable_test_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

TestingSessionLocal = sessionmaker(
    bind=test_engine, autoflush=False, autocommit=False
)


@pytest.fixture()
def client():
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    db.add_all([
        ProgramStudi(
            id=1, kode="001", nama="Sistem Informasi",
            fakultas="Fakultas Ilmu Komputer"
        ),
        JalurMasuk(id=1, nama="SNBP"),
    ])
    db.commit()
    db.close()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


def payload(**overrides):
    data = {
        "nama_lengkap": "Budi Santoso",
        "email": "budi@example.com",
        "no_hp": "081234567890",
        "tanggal_lahir": "2007-04-12",
        "alamat": "Cibinong, Bogor",
        "program_studi_id": 1,
        "jalur_masuk_id": 1,
    }
    data.update(overrides)
    return data


def test_register_success_returns_201_and_pending(client):
    response = client.post("/api/pmb/register", json=payload())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["nim"] is None
    assert "created_at" in body
    # Data sensitif tidak ikut dikembalikan pada respons pendaftaran.
    assert "alamat" not in body
    assert "tanggal_lahir" not in body


def test_register_accepts_628_and_plus628_phone_formats(client):
    res_62 = client.post(
        "/api/pmb/register",
        json=payload(email="siti@example.com", no_hp="6281234567890"),
    )
    assert res_62.status_code == 201

    res_plus62 = client.post(
        "/api/pmb/register",
        json=payload(email="andi@example.com", no_hp="+6281234567890"),
    )
    assert res_plus62.status_code == 201


def test_duplicate_email_returns_409(client):
    client.post("/api/pmb/register", json=payload())
    response = client.post("/api/pmb/register", json=payload())
    assert response.status_code == 409


def test_duplicate_email_case_insensitive_returns_409(client):
    client.post("/api/pmb/register", json=payload(email="budi@example.com"))
    response = client.post(
        "/api/pmb/register", json=payload(email="Budi@example.com")
    )
    assert response.status_code == 409


def test_whitespace_padded_short_name_returns_400(client):
    response = client.post(
        "/api/pmb/register", json=payload(nama_lengkap=" a ")
    )
    assert response.status_code == 400


def test_invalid_phone_returns_400(client):
    response = client.post(
        "/api/pmb/register", json=payload(no_hp="021123456")
    )
    assert response.status_code == 400


def test_unknown_program_study_returns_400(client):
    response = client.post(
        "/api/pmb/register", json=payload(program_studi_id=999)
    )
    assert response.status_code == 400


def test_unknown_admission_path_returns_400(client):
    response = client.post(
        "/api/pmb/register", json=payload(jalur_masuk_id=999)
    )
    assert response.status_code == 400


def test_invalid_email_returns_400(client):
    response = client.post(
        "/api/pmb/register", json=payload(email="bukan-email")
    )
    assert response.status_code == 400


def test_database_rejects_invalid_status_and_foreign_key(client):
    db = TestingSessionLocal()
    try:
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO calon_mahasiswa "
                    "(nama_lengkap, email, no_hp, tanggal_lahir, alamat, "
                    "program_studi_id, jalur_masuk_id, status) "
                    "VALUES ('Dummy', 'dummy1@example.com', '081234567890', "
                    "'2007-01-01', 'Bogor', 1, 1, 'hampir_diterima')"
                )
            )
            db.commit()
        db.rollback()

        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO calon_mahasiswa "
                    "(nama_lengkap, email, no_hp, tanggal_lahir, alamat, "
                    "program_studi_id, jalur_masuk_id, status) "
                    "VALUES ('Dummy', 'dummy2@example.com', '081234567890', "
                    "'2007-01-01', 'Bogor', 999, 1, 'pending')"
                )
            )
            db.commit()
        db.rollback()
    finally:
        db.close()
