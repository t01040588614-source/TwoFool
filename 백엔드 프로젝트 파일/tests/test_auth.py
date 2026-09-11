def test_register_user_success(client, create_user):
    response = create_user(client, "alice", "alice@example.com")

    assert response.status_code == 201
    assert response.get_json()["user"]["username"] == "alice"


def test_register_rejects_duplicate_username_and_email(client, create_user):
    create_user(client, "alice", "alice@example.com")

    duplicate_username = create_user(client, "alice", "other@example.com")
    duplicate_email = create_user(client, "other", "alice@example.com")

    assert duplicate_username.status_code == 409
    assert duplicate_email.status_code == 409


def test_register_rejects_invalid_input(client):
    response = client.post(
        "/api/auth/register",
        json={"username": "ab", "email": "invalid-email", "password": "123"},
    )

    assert response.status_code == 400


def test_login_and_get_current_user(client, create_user):
    create_user(client, "alice", "alice@example.com")

    login_response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    token = login_response.get_json()["access_token"]
    me_response = client.get(
        "/api/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert login_response.status_code == 200
    assert me_response.status_code == 200
    assert me_response.get_json()["username"] == "alice"


def test_login_rejects_wrong_password_and_protected_route_requires_token(
    client,
    create_user,
):
    create_user(client, "alice", "alice@example.com")

    login_response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )
    me_response = client.get("/api/users/me")

    assert login_response.status_code == 401
    assert me_response.status_code == 401


def test_recover_username_after_verifying_code(client, create_user):
    create_user(client, "alice", "alice@example.com")

    request_response = client.post(
        "/api/auth/recovery/request",
        json={"email": "alice@example.com", "purpose": "username"},
    )
    code = request_response.get_json()["verification_code"]
    verify_response = client.post(
        "/api/auth/recovery/verify",
        json={
            "email": "alice@example.com",
            "purpose": "username",
            "code": code,
        },
    )
    recovery_token = verify_response.get_json()["recovery_token"]
    username_response = client.post(
        "/api/auth/recover-username",
        json={
            "email": "alice@example.com",
            "recovery_token": recovery_token,
        },
    )

    assert request_response.status_code == 200
    assert verify_response.status_code == 200
    assert username_response.get_json()["username"] == "alice"


def test_reset_password_after_verifying_code(client, create_user):
    create_user(client, "alice", "alice@example.com")

    request_response = client.post(
        "/api/auth/recovery/request",
        json={"email": "alice@example.com", "purpose": "password"},
    )
    code = request_response.get_json()["verification_code"]
    verify_response = client.post(
        "/api/auth/recovery/verify",
        json={
            "email": "alice@example.com",
            "purpose": "password",
            "code": code,
        },
    )
    reset_response = client.post(
        "/api/auth/reset-password",
        json={
            "email": "alice@example.com",
            "recovery_token": verify_response.get_json()["recovery_token"],
            "password": "new-password123",
        },
    )
    login_response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "new-password123"},
    )

    assert reset_response.status_code == 200
    assert login_response.status_code == 200
