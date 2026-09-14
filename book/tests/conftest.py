import os

import pytest

# app.py를 불러오기 전에 테스트 전용 메모리 DB를 지정합니다.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-pytest-must-be-32-bytes"
os.environ["SCMAGLEV_SKIP_INIT"] = "1"
os.environ["CONGESTION_AUTO_RETRAIN"] = "0"

from app import app, seed_scmaglev_data
from extensions import db


@pytest.fixture()
def client():
    app.config.update(TESTING=True)

    with app.app_context():
        db.drop_all()
        db.create_all()
        seed_scmaglev_data()

        with app.test_client() as test_client:
            yield test_client

        db.session.remove()
        db.drop_all()


def register_user(client, username, email, password="password123", role="passenger"):
    return client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": email,
            "password": password,
            "role": role,
        },
    )


@pytest.fixture()
def create_user(client):  # noqa: ARG001
    return register_user


@pytest.fixture()
def auth_headers(client, create_user):
    def _auth_headers(username="tester", email="tester@example.com", role="passenger"):
        create_user(client, username, email, role=role)
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": "password123"},
        )
        token = response.get_json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _auth_headers
