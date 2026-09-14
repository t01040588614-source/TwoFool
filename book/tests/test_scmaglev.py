import pytest


def test_seoul_jeju_train_search(client):
    response = client.get("/api/scmaglev/trains/search?departure=서울&arrival=제주")
    assert response.status_code == 200
    schedules = response.get_json()["schedules"]
    assert len(schedules) >= 1
    first = schedules[0]
    assert first["departure_station"] == "서울"
    assert first["arrival_station"] == "제주"


def test_cross_region_train_search(client):
    pairs = [
        ("서울", "제주"),
        ("포천", "서귀포"),
        ("속초", "목포"),
        ("인천", "진주"),
        ("세종", "거제"),
    ]
    for departure, arrival in pairs:
        response = client.get(
            f"/api/scmaglev/trains/search?departure={departure}&arrival={arrival}"
        )
        assert response.status_code == 200, f"{departure}->{arrival} status"
        payload = response.get_json()
        schedules = payload["schedules"]
        assert len(schedules) >= 1, f"{departure}->{arrival} empty"
        assert payload["search_mode"] == "direct"
        first = schedules[0]
        assert first["departure_station"] == departure
        assert first["arrival_station"] == arrival
        assert not first.get("is_connection")
        assert first["duration_minutes"] < 500


def test_sokcho_jeju_is_direct(client):
    response = client.get("/api/scmaglev/trains/search?departure=속초&arrival=제주")
    assert response.status_code == 200
    first = response.get_json()["schedules"][0]
    assert first["departure_station"] == "속초"
    assert first["arrival_station"] == "제주"
    assert first["train_number"].startswith("SM-D-")
    assert "직통" in first["train_name"]
    assert first["duration_minutes"] < 300


def test_scmaglev_seed_and_train_search(client):
    dashboard_page = client.get("/dashboard")
    stations_response = client.get("/api/scmaglev/stations")
    search_response = client.get("/api/scmaglev/trains/search?departure=서울&arrival=수원")
    filtered_search = client.get(
        "/api/scmaglev/trains/search?departure=서울&arrival=수원&train_type=METRO&sort_by=fare"
    )

    assert dashboard_page.status_code == 200
    assert stations_response.status_code == 200
    assert len(stations_response.get_json()["stations"]) >= 3
    assert search_response.status_code == 200
    assert len(search_response.get_json()["schedules"]) >= 1
    assert "estimated_fare" in search_response.get_json()["schedules"][0]
    assert filtered_search.status_code == 200

    schedule_rows = search_response.get_json()["schedules"]
    if schedule_rows:
        departure_iso_utc = f"{schedule_rows[0]['departure_time']}+00:00"
        timezone_search = client.get(
            f"/api/scmaglev/trains/search?departure_after={departure_iso_utc}&sort_by=departure"
        )
        assert timezone_search.status_code == 200


def test_seat_lookup_and_reservation_flow(client, auth_headers):
    headers = auth_headers("alice", "alice@example.com")
    schedules = client.get("/api/scmaglev/trains/search").get_json()["schedules"]
    schedule_id = schedules[0]["id"]

    seats_response = client.get(f"/api/scmaglev/schedules/{schedule_id}/seats")
    wheelchair_seats = client.get(
        f"/api/scmaglev/schedules/{schedule_id}/seats?seat_type=wheelchair"
    )
    seats = seats_response.get_json()["seats"]
    available_seat = next(seat for seat in seats if not seat["is_reserved"])

    reservation_response = client.post(
        "/api/scmaglev/reservations",
        json={"schedule_id": schedule_id, "seat_id": available_seat["id"]},
        headers=headers,
    )
    reservation_id = reservation_response.get_json()["reservation"]["id"]
    pay_response = client.post(
        f"/api/scmaglev/reservations/{reservation_id}/pay",
        json={"success": True},
        headers=headers,
    )
    list_response = client.get("/api/scmaglev/reservations", headers=headers)
    cancel_response = client.post(
        f"/api/scmaglev/reservations/{reservation_id}/cancel",
        headers=headers,
    )

    assert seats_response.status_code == 200
    assert wheelchair_seats.status_code == 200
    assert "estimated_fare" in wheelchair_seats.get_json()
    assert reservation_response.status_code == 201
    assert reservation_response.get_json()["reservation"]["payment_status"] == "pending"
    assert pay_response.status_code == 200
    assert list_response.status_code == 200
    assert len(list_response.get_json()["reservations"]) >= 1
    assert cancel_response.status_code == 200


def test_payment_retry_flow(client, auth_headers):
    headers = auth_headers("retry_user", "retry@example.com")
    schedules = client.get("/api/scmaglev/trains/search").get_json()["schedules"]
    schedule_id = schedules[0]["id"]
    seats = client.get(f"/api/scmaglev/schedules/{schedule_id}/seats").get_json()["seats"]
    seat_id = next(seat["id"] for seat in seats if not seat["is_reserved"])

    reservation = client.post(
        "/api/scmaglev/reservations",
        json={"schedule_id": schedule_id, "seat_id": seat_id},
        headers=headers,
    ).get_json()["reservation"]
    reservation_id = reservation["id"]

    fail_payment = client.post(
        f"/api/scmaglev/reservations/{reservation_id}/pay",
        json={"success": False, "reason": "network"},
        headers=headers,
    )
    retry_payment = client.post(
        f"/api/scmaglev/reservations/{reservation_id}/retry-payment",
        headers=headers,
    )
    final_payment = client.post(
        f"/api/scmaglev/reservations/{reservation_id}/pay",
        json={"success": True},
        headers=headers,
    )

    assert fail_payment.status_code == 400
    assert retry_payment.status_code == 200
    assert "payment_due_at" in retry_payment.get_json()
    assert final_payment.status_code == 200


def test_reservation_rejects_double_booking(client, auth_headers):
    first_user_headers = auth_headers("alice", "alice@example.com")
    second_user_headers = auth_headers("bob", "bob@example.com")
    schedules = client.get("/api/scmaglev/trains/search").get_json()["schedules"]
    schedule_id = schedules[0]["id"]
    seats = client.get(f"/api/scmaglev/schedules/{schedule_id}/seats").get_json()["seats"]
    seat_id = next(seat["id"] for seat in seats if not seat["is_reserved"])

    first_booking = client.post(
        "/api/scmaglev/reservations",
        json={"schedule_id": schedule_id, "seat_id": seat_id},
        headers=first_user_headers,
    )
    second_booking = client.post(
        "/api/scmaglev/reservations",
        json={"schedule_id": schedule_id, "seat_id": seat_id},
        headers=second_user_headers,
    )

    assert first_booking.status_code == 201
    assert second_booking.status_code == 409


def test_event_log_endpoint_returns_data(client, auth_headers):
    headers = auth_headers("alice", "alice@example.com")
    schedules = client.get("/api/scmaglev/trains/search").get_json()["schedules"]
    schedule_id = schedules[0]["id"]
    seats = client.get(f"/api/scmaglev/schedules/{schedule_id}/seats").get_json()["seats"]
    seat_id = next(seat["id"] for seat in seats if not seat["is_reserved"])

    reservation = client.post(
        "/api/scmaglev/reservations",
        json={"schedule_id": schedule_id, "seat_id": seat_id},
        headers=headers,
    ).get_json()["reservation"]
    client.post(
        f"/api/scmaglev/reservations/{reservation['id']}/pay",
        json={"success": True},
        headers=headers,
    )
    events = client.get("/api/scmaglev/dashboard/events?limit=10")
    warning_only = client.get("/api/scmaglev/dashboard/events?severity=info&limit=10")
    by_type = client.get("/api/scmaglev/dashboard/events?event_type=PAYMENT_COMPLETED&limit=10")

    assert events.status_code == 200
    assert len(events.get_json()["events"]) >= 1
    assert warning_only.status_code == 200
    assert by_type.status_code == 200
    if by_type.get_json()["events"]:
        assert all(event["event_type"] == "PAYMENT_COMPLETED" for event in by_type.get_json()["events"])


def test_event_log_meta_endpoint(client, auth_headers):
    headers = auth_headers("metauser", "metauser@example.com")
    schedules = client.get("/api/scmaglev/trains/search").get_json()["schedules"]
    schedule_id = schedules[0]["id"]
    seats = client.get(f"/api/scmaglev/schedules/{schedule_id}/seats").get_json()["seats"]
    seat_id = next(seat["id"] for seat in seats if not seat["is_reserved"])

    reservation = client.post(
        "/api/scmaglev/reservations",
        json={"schedule_id": schedule_id, "seat_id": seat_id},
        headers=headers,
    ).get_json()["reservation"]
    client.post(
        f"/api/scmaglev/reservations/{reservation['id']}/pay",
        json={"success": False, "reason": "meta"},
        headers=headers,
    )
    meta_response = client.get("/api/scmaglev/dashboard/events/meta")
    payload = meta_response.get_json()

    assert meta_response.status_code == 200
    assert "event_types" in payload
    assert "severities" in payload
    assert "PAYMENT_FAILED" in payload["event_types"]


def test_dashboard_event_acknowledge(client, auth_headers):
    passenger_headers = auth_headers("pax", "pax@example.com", role="passenger")
    controller_headers = auth_headers("controller_ack", "controller_ack@example.com", role="controller")
    schedules = client.get("/api/scmaglev/trains/search").get_json()["schedules"]
    schedule_id = schedules[0]["id"]
    seats = client.get(f"/api/scmaglev/schedules/{schedule_id}/seats").get_json()["seats"]
    seat_id = next(seat["id"] for seat in seats if not seat["is_reserved"])

    reservation = client.post(
        "/api/scmaglev/reservations",
        json={"schedule_id": schedule_id, "seat_id": seat_id},
        headers=passenger_headers,
    ).get_json()["reservation"]
    client.post(
        f"/api/scmaglev/reservations/{reservation['id']}/pay",
        json={"success": False, "reason": "card"},
        headers=passenger_headers,
    )
    events = client.get("/api/scmaglev/dashboard/events?event_type=PAYMENT_FAILED&acknowledged=false&limit=1")
    event_id = events.get_json()["events"][0]["id"]

    ack_response = client.post(
        f"/api/scmaglev/dashboard/events/{event_id}/ack",
        headers=controller_headers,
    )
    acknowledged_events = client.get("/api/scmaglev/dashboard/events?event_type=PAYMENT_FAILED&acknowledged=true&limit=20")

    assert ack_response.status_code == 200
    assert any(event["id"] == event_id for event in acknowledged_events.get_json()["events"])


def test_dashboard_bulk_event_acknowledge(client, auth_headers):
    passenger_headers = auth_headers("bulk_pax", "bulk_pax@example.com", role="passenger")
    controller_headers = auth_headers("bulk_controller", "bulk_controller@example.com", role="controller")
    schedules = client.get("/api/scmaglev/trains/search").get_json()["schedules"]
    schedule_id = schedules[0]["id"]
    seats = client.get(f"/api/scmaglev/schedules/{schedule_id}/seats").get_json()["seats"]
    free_seats = [seat["id"] for seat in seats if not seat["is_reserved"]][:2]

    for seat_id in free_seats:
        reservation = client.post(
            "/api/scmaglev/reservations",
            json={"schedule_id": schedule_id, "seat_id": seat_id},
            headers=passenger_headers,
        ).get_json()["reservation"]
        client.post(
            f"/api/scmaglev/reservations/{reservation['id']}/pay",
            json={"success": False, "reason": "bulk"},
            headers=passenger_headers,
        )

    unacked_before = client.get(
        "/api/scmaglev/dashboard/events?event_type=PAYMENT_FAILED&acknowledged=false&limit=20"
    ).get_json()["events"]
    bulk_ack = client.post(
        "/api/scmaglev/dashboard/events/ack-bulk",
        json={"event_ids": [event["id"] for event in unacked_before]},
        headers=controller_headers,
    )
    unacked_after = client.get(
        "/api/scmaglev/dashboard/events?event_type=PAYMENT_FAILED&acknowledged=false&limit=20"
    ).get_json()["events"]

    assert bulk_ack.status_code == 200
    assert bulk_ack.get_json()["acknowledged_count"] >= 1
    assert len(unacked_after) < len(unacked_before)


def test_dashboard_requires_controller_role(client, auth_headers):
    passenger_headers = auth_headers("alice", "alice@example.com", role="passenger")
    controller_headers = auth_headers("control", "control@example.com", role="controller")

    forbidden_response = client.get(
        "/api/scmaglev/dashboard/summary",
        headers=passenger_headers,
    )
    success_response = client.get(
        "/api/scmaglev/dashboard/summary",
        headers=controller_headers,
    )

    assert forbidden_response.status_code == 403
    assert success_response.status_code == 200
    assert "status_counts" in success_response.get_json()


def test_public_dashboard_summary_available_without_auth(client):
    response = client.get("/api/scmaglev/dashboard/public-summary")

    assert response.status_code == 200
    payload = response.get_json()
    assert "prediction_summary" in payload
    assert "fleet_counts" in payload
    status = payload["status_counts"]
    fleet = payload["fleet_counts"]
    assert status["in_service"] == fleet["in_service"]
    assert status["waiting"] == fleet["waiting"]
    assert status["arrived"] == fleet["arrived"]
    assert status["in_service"] + status["waiting"] + status["arrived"] == payload["tracked_train_count"]


def test_train_locations_in_service_matches_summary(client):
    summary = client.get("/api/scmaglev/dashboard/public-summary").get_json()
    locations = client.get("/api/scmaglev/train-locations").get_json()
    live_in_service = sum(1 for train in locations["trains"] if train.get("is_in_service"))
    assert summary["status_counts"]["in_service"] == live_in_service
    for train in locations["trains"]:
        phase = train.get("service_phase")
        if phase == "waiting":
            assert train["progress"] == 0
            assert not train["is_in_service"]
        elif phase == "arrived":
            assert train["progress"] == 1
            assert not train["is_in_service"]


def test_dashboard_train_endpoints(client):
    trains_response = client.get("/api/scmaglev/dashboard/trains")
    assert trains_response.status_code == 200
    trains = trains_response.get_json()["trains"]
    assert len(trains) >= 6
    assert "train_type_code" in trains[0]

    train_id = trains[0]["train_id"]
    detail_response = client.get(f"/api/scmaglev/dashboard/train/{train_id}")
    assert detail_response.status_code == 200
    body = detail_response.get_json()
    assert body["train"]["train_id"] == train_id
    assert "history" in body
    assert "predictions" in body


def test_ml_compare_endpoint(client):
    response = client.post(
        "/api/scmaglev/ml/congestion/compare",
        json={
            "hour": 9,
            "route_name": "서울-수원",
            "recent_passengers": 900,
            "is_event_day": False,
        },
    )

    assert response.status_code == 200
    comparison = response.get_json()["comparison"]
    assert "ml" in comparison
    assert "dl" in comparison
    assert "ensemble" in comparison
    assert comparison["ensemble"]["label"] in {"low", "medium", "high"}


def test_passenger_recommendation_endpoint(client):
    response = client.get("/api/scmaglev/passenger/recommendations?departure=서울&limit=3")

    assert response.status_code == 200
    payload = response.get_json()
    assert "recommendations" in payload
    assert "criteria" in payload
    assert len(payload["recommendations"]) <= 3
    if payload["recommendations"]:
        first = payload["recommendations"][0]
        assert "tag" in first
        assert "tag_label" in first
        assert "reason_line" in first
        assert "trust" in first
        assert "trust_line" in first["trust"]
        assert "prediction_accuracy_pct" in first["trust"]
    assert "trust_summary" in payload


def test_passenger_journey_endpoint(client, auth_headers):
    headers = auth_headers("journey_user", "journey@example.com")
    schedules = client.get("/api/scmaglev/trains/search").get_json()["schedules"]
    schedule_id = schedules[0]["id"]
    seats = client.get(f"/api/scmaglev/schedules/{schedule_id}/seats").get_json()["seats"]
    seat_id = next(seat["id"] for seat in seats if not seat["is_reserved"])

    reservation_id = client.post(
        "/api/scmaglev/reservations",
        json={"schedule_id": schedule_id, "seat_id": seat_id},
        headers=headers,
    ).get_json()["reservation"]["id"]

    list_payload = client.get("/api/scmaglev/reservations", headers=headers).get_json()
    listed = next(row for row in list_payload["reservations"] if row["id"] == reservation_id)
    assert "journey" in listed
    assert "current_phase" in listed["journey"]
    assert "live" in listed

    journey_response = client.get(
        f"/api/scmaglev/passenger/journey/{reservation_id}",
        headers=headers,
    )
    assert journey_response.status_code == 200
    journey = journey_response.get_json()["journey"]
    assert journey["reservation_id"] == reservation_id
    assert isinstance(journey["events"], list)
    assert journey["events"]


def test_passenger_control_trust_endpoint(client):
    response = client.get("/api/scmaglev/passenger/control-trust")
    assert response.status_code == 200
    payload = response.get_json()
    assert "trust" in payload
    trust = payload["trust"]
    assert trust["control_linked"] is True
    assert "message_line" in trust
    assert trust["delay_risk_level"] in {"LOW", "MEDIUM", "HIGH"}
    assert 88 <= trust["prediction_accuracy_pct"] <= 99


def test_station_heatmap_endpoint(client):
    response = client.get("/api/scmaglev/dashboard/station-heatmap")
    assert response.status_code == 200
    payload = response.get_json()
    assert "stations" in payload
    assert len(payload["stations"]) >= 1
    first = payload["stations"][0]
    assert "congestion_label" in first
    assert first["congestion_label"] in {"low", "medium", "high"}


def test_public_summary_includes_passenger_trust(client):
    response = client.get("/api/scmaglev/dashboard/public-summary")
    assert response.status_code == 200
    payload = response.get_json()
    assert "passenger_trust" in payload
    assert "message_line" in payload["passenger_trust"]
    assert "passenger_notices" in payload
    assert isinstance(payload["passenger_notices"], list)
    assert "ai_status" in payload
    assert "label" in payload["ai_status"]


def test_passenger_notices_after_controller_delay_action(client, auth_headers):
    controller_headers = auth_headers(
        "notice_ctrl",
        "notice_ctrl@example.com",
        role="controller",
    )
    notice_text = "[테스트] 서울~부산 구간 지연 안내"

    action_response = client.post(
        "/api/scmaglev/dashboard/actions",
        json={"action_type": "PASSENGER_NOTICE_DELAY", "note": notice_text},
        headers=controller_headers,
    )
    assert action_response.status_code == 200

    summary = client.get("/api/scmaglev/dashboard/public-summary").get_json()
    assert summary["passenger_notices"]
    assert summary["passenger_notices"][0]["message"] == notice_text
    assert summary["passenger_notices"][0]["type"] == "delay"


def test_dashboard_ai_draft_shift_report(client):
    response = client.post(
        "/api/scmaglev/dashboard/ai/draft",
        json={
            "draft_type": "shift_report",
            "context": {
                "status_counts": {"normal": 10, "delayed": 2, "stopped": 0, "disrupted": 0, "arrived": 1},
                "events": [],
                "action_timeline": ["테스트 조치"],
            },
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["text"]
    assert "교대 리포트" in payload["text"]
    assert payload["ai_meta"]["draft_type"] == "shift_report"
    assert payload["ai_meta"]["source"] in {"openai", "rule_based"}


def test_dashboard_ai_draft_invalid_type(client):
    response = client.post(
        "/api/scmaglev/dashboard/ai/draft",
        json={"draft_type": "unknown"},
    )
    assert response.status_code == 400


def test_ai_status_endpoint_without_key(client, monkeypatch):
    monkeypatch.setattr("openai_config.Config.OPENAI_API_KEY", None)
    response = client.get("/api/scmaglev/ai/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "rule_based"
    assert payload["display_label"] == "규칙 기반"


def test_ai_chat_endpoint(client):
    response = client.post(
        "/api/scmaglev/ai/chat",
        json={
            "message": "서울에서 수원 가는 열차 알려줘",
            "context": {"departure": "서울", "arrival": "수원"},
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["reply"]
    assert payload["ai_meta"]["source"] in {"openai", "rule_based"}
    assert payload["route_hint"]["departure"] == "서울"
    assert payload["route_hint"]["arrival"] == "수원"


def test_ai_chat_requires_message(client):
    response = client.post("/api/scmaglev/ai/chat", json={})
    assert response.status_code == 400


def test_ai_chat_dashboard_context(client):
    response = client.post(
        "/api/scmaglev/ai/chat",
        json={
            "message": "지금 운행 현황 알려줘",
            "context": {
                "page": "dashboard",
                "audience": "dashboard",
                "dashboard": {
                    "status_counts": {
                        "in_service": 120,
                        "waiting": 20,
                        "normal": 117,
                        "delayed": 3,
                        "stopped": 0,
                        "disrupted": 0,
                        "arrived": 40,
                    },
                    "fleet_counts": {"in_service": 120, "waiting": 20, "arrived": 40},
                },
            },
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["reply"]
    assert "운행" in payload["reply"] or "정상" in payload["reply"]


def test_ai_status_detects_fine_tuned_model(monkeypatch):
    from openai_config import describe_openai_model, get_openai_status

    monkeypatch.setattr(
        "openai_config.Config.OPENAI_API_KEY",
        "sk-test",
    )
    monkeypatch.setattr(
        "openai_config.Config.OPENAI_MODEL",
        "ft:gpt-4o-mini:org:scmaglev-ops:abc123",
    )
    monkeypatch.setattr("openai_config.Config.OPENAI_ENABLED", "auto")
    described = describe_openai_model("ft:gpt-4o-mini:org:scmaglev-ops:abc123")
    assert described["is_fine_tuned"] is True
    assert "scmaglev-ops" in described["short_label"]
    status = get_openai_status()
    assert status["status"] == "ready"
    assert status["is_fine_tuned"] is True


def test_dashboard_ai_draft_incident_actions(client):
    response = client.post(
        "/api/scmaglev/dashboard/ai/draft",
        json={
            "draft_type": "incident_actions",
            "context": {
                "selected_incident": {
                    "id": 1,
                    "message": "테스트 알람",
                    "severity": "warning",
                    "event_type": "ALARM_DELAYED",
                },
                "status_counts": {"normal": 8, "delayed": 1, "stopped": 0, "disrupted": 0, "arrived": 0},
            },
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["actions"]) == 3
    assert payload["ai_meta"]["draft_type"] == "incident_actions"


def test_ml_congestion_prediction(client):
    response = client.post(
        "/api/scmaglev/ml/congestion/predict",
        json={
            "hour": 8,
            "route_name": "서울-부산",
            "recent_passengers": 1600,
            "is_event_day": True,
        },
    )

    prediction = response.get_json()["prediction"]
    assert response.status_code == 200
    assert prediction["label"] in {"low", "medium", "high"}
    assert 0 <= prediction["confidence"] <= 1


def test_dl_congestion_prediction(client):
    response = client.post(
        "/api/scmaglev/ml/congestion/predict",
        json={
            "hour": 19,
            "route_name": "서울-부산",
            "recent_passengers": 1800,
            "is_event_day": True,
            "model_type": "dl",
        },
    )

    prediction = response.get_json()["prediction"]
    assert response.status_code == 200
    assert prediction["model_type"] == "dl"
    assert prediction["label"] in {"low", "medium", "high"}


def test_fault_causes_and_summary(client):
    causes = client.get("/api/scmaglev/dashboard/fault-causes")
    summary = client.get("/api/scmaglev/dashboard/public-summary")

    assert causes.status_code == 200
    payload = causes.get_json()
    assert len(payload["fault_causes"]) >= 10
    assert "ENGINE" in payload["labels"]
    assert "WHEEL_BRAKE" in payload["labels"]

    assert summary.status_code == 200
    summary_payload = summary.get_json()
    assert "fault_summary" in summary_payload
    assert "fault_trains" in summary_payload
    assert isinstance(summary_payload["fault_summary"], list)
    assert "event_ack_stats" in summary_payload
    ack_stats = summary_payload["event_ack_stats"]
    assert "critical_unacked" in ack_stats
    assert "oldest_unacked_minutes" in ack_stats
    assert isinstance(ack_stats["critical_unacked"], int)


def test_clear_train_fault_requires_controller(client, auth_headers):
    summary = client.get("/api/scmaglev/dashboard/public-summary").get_json()
    fault_trains = summary.get("fault_trains") or []
    if not fault_trains:
        pytest.skip("seeded fault trains unavailable")

    train_id = fault_trains[0]["train_id"]
    passenger_headers = auth_headers("passenger_fault", "passenger_fault@example.com", role="passenger")
    controller_headers = auth_headers("controller_fault", "controller_fault@example.com", role="controller")

    denied = client.post(
        f"/api/scmaglev/dashboard/trains/{train_id}/fault/clear",
        json={"note": "should fail"},
        headers=passenger_headers,
    )
    assert denied.status_code == 403

    cleared = client.post(
        f"/api/scmaglev/dashboard/trains/{train_id}/fault/clear",
        json={"note": "현장 조치 완료"},
        headers=controller_headers,
    )
    assert cleared.status_code == 200
    cleared_payload = cleared.get_json()
    assert cleared_payload["train"]["fault"] is None

    again = client.post(
        f"/api/scmaglev/dashboard/trains/{train_id}/fault/clear",
        headers=controller_headers,
    )
    assert again.status_code == 400
