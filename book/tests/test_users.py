def test_user_profile_requires_authentication(client):
    response = client.get("/api/users/me")

    assert response.status_code == 401


def test_get_current_user_hides_password(client, auth_headers):
    headers = auth_headers("alice", "alice@example.com")

    response = client.get("/api/users/me", headers=headers)
    user = response.get_json()

    assert response.status_code == 200
    assert user["username"] == "alice"
    assert user["email"] == "alice@example.com"
    assert "password" not in user


def test_update_current_user_and_password(client, auth_headers):
    headers = auth_headers("alice", "alice@example.com")

    update_response = client.put(
        "/api/users/me",
        json={
            "username": "alice-updated",
            "email": "updated@example.com",
            "password": "new-password123",
        },
        headers=headers,
    )
    login_response = client.post(
        "/api/auth/login",
        json={"username": "alice-updated", "password": "new-password123"},
    )

    assert update_response.status_code == 200
    assert update_response.get_json()["user"]["email"] == "updated@example.com"
    assert "password" not in update_response.get_json()["user"]
    assert login_response.status_code == 200


def test_update_rejects_duplicate_email(client, auth_headers):
    alice_headers = auth_headers("alice", "alice@example.com")
    auth_headers("bob", "bob@example.com")

    response = client.put(
        "/api/users/me",
        json={"email": "bob@example.com"},
        headers=alice_headers,
    )

    assert response.status_code == 409


def test_delete_current_user(client, auth_headers):
    headers = auth_headers("alice", "alice@example.com")

    delete_response = client.delete("/api/users/me", headers=headers)
    login_response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "password123"},
    )

    assert delete_response.status_code == 200
    assert login_response.status_code == 401
