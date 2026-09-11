def create_post(client, headers, title="테스트 제목", content="테스트 내용"):
    return client.post(
        "/api/posts",
        json={"title": title, "content": content},
        headers=headers,
    )


def test_post_creation_requires_login(client):
    response = create_post(client, headers={})

    assert response.status_code == 401


def test_post_search_update_delete_and_owner_permission(client, auth_headers):
    alice_headers = auth_headers("alice", "alice@example.com")
    bob_headers = auth_headers("bob", "bob@example.com")
    create_response = create_post(
        client,
        alice_headers,
        title="Flask 공부",
        content="게시글 작성 테스트",
    )
    post_id = create_response.get_json()["post"]["id"]

    search_response = client.get("/api/posts?keyword=Flask")
    update_response = client.put(
        f"/api/posts/{post_id}",
        json={"title": "수정된 제목", "content": "수정된 내용"},
        headers=alice_headers,
    )
    forbidden_update = client.put(
        f"/api/posts/{post_id}",
        json={"title": "다른 사용자의 수정"},
        headers=bob_headers,
    )
    forbidden_delete = client.delete(
        f"/api/posts/{post_id}",
        headers=bob_headers,
    )
    delete_response = client.delete(
        f"/api/posts/{post_id}",
        headers=alice_headers,
    )

    assert create_response.status_code == 201
    assert search_response.get_json()["posts"][0]["title"] == "Flask 공부"
    assert update_response.status_code == 200
    assert forbidden_update.status_code == 403
    assert forbidden_delete.status_code == 403
    assert delete_response.status_code == 200


def test_comment_creation_list_and_owner_delete_permission(client, auth_headers):
    alice_headers = auth_headers("alice", "alice@example.com")
    bob_headers = auth_headers("bob", "bob@example.com")
    post_id = create_post(client, alice_headers).get_json()["post"]["id"]

    create_comment_response = client.post(
        f"/api/posts/{post_id}/comments",
        json={"content": "댓글 테스트"},
        headers=bob_headers,
    )
    comment_id = create_comment_response.get_json()["comment"]["id"]
    list_response = client.get(f"/api/posts/{post_id}/comments")
    forbidden_delete = client.delete(
        f"/api/comments/{comment_id}",
        headers=alice_headers,
    )
    delete_response = client.delete(
        f"/api/comments/{comment_id}",
        headers=bob_headers,
    )

    assert create_comment_response.status_code == 201
    assert list_response.get_json()["comments"][0]["content"] == "댓글 테스트"
    assert forbidden_delete.status_code == 403
    assert delete_response.status_code == 200
