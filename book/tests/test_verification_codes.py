from werkzeug.security import check_password_hash

from models import VerificationCode


def request_code(client, create_user):
    create_user(client, "alice", "alice@example.com")
    return client.post(
        "/api/auth/recovery/request",
        json={"email": "alice@example.com", "purpose": "password"},
    )


def test_verification_code_is_hashed_and_marked_used(client, create_user):
    response = request_code(client, create_user)
    code = response.get_json()["verification_code"]

    verification = VerificationCode.query.one()
    assert verification.code_hash != code
    assert check_password_hash(verification.code_hash, code)

    client.post(
        "/api/auth/recovery/verify",
        json={"email": "alice@example.com", "purpose": "password", "code": code},
    )

    assert VerificationCode.query.one().is_used is True


def test_verification_code_is_invalid_after_five_failed_attempts(client, create_user):
    request_code(client, create_user)

    for _ in range(5):
        response = client.post(
            "/api/auth/recovery/verify",
            json={"email": "alice@example.com", "purpose": "password", "code": "000000"},
        )

    verification = VerificationCode.query.one()
    assert response.status_code == 400
    assert verification.attempts == 5
    assert verification.is_used is True
