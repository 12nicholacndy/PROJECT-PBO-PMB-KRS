from tests.conftest import payload


def test_register_success_returns_201_and_pending(client):
    response = client.post("/api/pmb/register", json=payload())
    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert response.json()["nim"] is None


def test_duplicate_email_returns_409(client):
    client.post("/api/pmb/register", json=payload())
    response = client.post("/api/pmb/register", json=payload())
    assert response.status_code == 409


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


def test_blank_name_returns_400(client):
    response = client.post(
        "/api/pmb/register", json=payload(nama_lengkap="   ")
    )
    assert response.status_code == 400
