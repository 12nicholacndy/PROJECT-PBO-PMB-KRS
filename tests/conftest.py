import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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
def db():
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    session.add_all([
        ProgramStudi(
            id=1,
            kode="001",
            nama="Sistem Informasi",
            fakultas="Fakultas Ilmu Komputer",
        ),
        ProgramStudi(
            id=2,
            kode="002",
            nama="Teknik Informatika",
            fakultas="Fakultas Ilmu Komputer",
        ),
        JalurMasuk(id=1, nama="SNBP"),
        JalurMasuk(id=2, nama="SNBT"),
        JalurMasuk(id=3, nama="Mandiri"),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client(db):
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


def daftar(client: TestClient, **overrides) -> dict:
    response = client.post("/api/pmb/register", json=payload(**overrides))
    assert response.status_code == 201
    return response.json()
