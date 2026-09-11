def _normalize_route_weight(route_name):
    name = route_name.lower()
    if "부산" in name:
        return 0.2
    if "서울" in name:
        return 0.1
    return 0.05


def _label_to_risk_score(label):
    mapping = {
        "low": 0.25,
        "medium": 0.6,
        "high": 0.9,
    }
    return mapping.get(label, 0.6)


def _risk_score_to_label(score):
    if score >= 0.72:
        return "high"
    if score >= 0.42:
        return "medium"
    return "low"


def _predict_congestion_ml(hour, route_name, recent_passengers, is_event_day):
    """규칙 기반 + 회귀형 경량 ML 예측."""
    rush_hour_weight = 0.25 if hour in {7, 8, 9, 18, 19, 20} else 0.08
    route_weight = _normalize_route_weight(route_name)
    passenger_weight = min(recent_passengers / 2000, 1.0) * 0.55
    event_weight = 0.15 if is_event_day else 0.0

    score = min(rush_hour_weight + route_weight + passenger_weight + event_weight, 1.0)

    if score >= 0.72:
        label = "high"
    elif score >= 0.42:
        label = "medium"
    else:
        label = "low"

    return label, round(score, 3)


def _sigmoid(value):
    # exp를 직접 쓰지 않고도 작은 inference를 안정적으로 처리하기 위한 근사치.
    if value >= 4:
        return 0.982
    if value <= -4:
        return 0.018
    return 0.5 + (value / 8)


def _predict_congestion_dl(hour, route_name, recent_passengers, is_event_day):
    """
    학습된 파라미터를 흉내 낸 Tiny MLP(2-layer) 추론.
    실제 서비스용 딥러닝이 아니라 오전 데모용 경량 DL 프로토타입입니다.
    """
    hour_norm = min(max(hour, 0), 23) / 23
    passenger_norm = min(max(recent_passengers, 0), 2000) / 2000
    route_weight = _normalize_route_weight(route_name)
    event_norm = 1.0 if is_event_day else 0.0

    hidden_1 = _sigmoid(1.2 * hour_norm + 1.7 * passenger_norm + 0.9 * event_norm - 1.3)
    hidden_2 = _sigmoid(1.1 * passenger_norm + 0.8 * route_weight + 0.6 * event_norm - 0.9)
    hidden_3 = _sigmoid(1.6 * hour_norm + 0.5 * route_weight - 0.7)

    score = _sigmoid(1.4 * hidden_1 + 1.1 * hidden_2 + 1.0 * hidden_3 - 1.6)
    score = round(min(max(score, 0.0), 1.0), 3)

    if score >= 0.72:
        label = "high"
    elif score >= 0.42:
        label = "medium"
    else:
        label = "low"

    return label, score


def predict_congestion(hour, route_name, recent_passengers, is_event_day, model_type="ml"):
    """혼잡도 예측 (ml 또는 dl)."""
    selected_model = model_type if model_type in {"ml", "dl"} else "ml"

    if selected_model == "dl":
        label, score = _predict_congestion_dl(hour, route_name, recent_passengers, is_event_day)
    else:
        label, score = _predict_congestion_ml(hour, route_name, recent_passengers, is_event_day)

    return {
        "label": label,
        "confidence": score,
        "model_type": selected_model,
        "features": {
            "hour": hour,
            "route_name": route_name,
            "recent_passengers": recent_passengers,
            "is_event_day": bool(is_event_day),
        },
    }


def compare_congestion_models(hour, route_name, recent_passengers, is_event_day):
    ml = predict_congestion(
        hour=hour,
        route_name=route_name,
        recent_passengers=recent_passengers,
        is_event_day=is_event_day,
        model_type="ml",
    )
    dl = predict_congestion(
        hour=hour,
        route_name=route_name,
        recent_passengers=recent_passengers,
        is_event_day=is_event_day,
        model_type="dl",
    )

    ml_risk = _label_to_risk_score(ml["label"]) * 0.6 + float(ml["confidence"]) * 0.4
    dl_risk = _label_to_risk_score(dl["label"]) * 0.6 + float(dl["confidence"]) * 0.4
    ensemble_risk = round((ml_risk + dl_risk) / 2, 3)
    ensemble_label = _risk_score_to_label(ensemble_risk)
    disagreement = abs(float(ml["confidence"]) - float(dl["confidence"]))

    if disagreement >= 0.2:
        guidance = "모델 간 편차가 큽니다. 관제사의 확인을 권장합니다."
    elif ensemble_label == "high":
        guidance = "고혼잡 구간입니다. 배차 조정 또는 안내 발송이 필요합니다."
    elif ensemble_label == "medium":
        guidance = "중간 혼잡 구간입니다. 혼잡 모니터링을 유지하세요."
    else:
        guidance = "혼잡 리스크가 낮은 구간입니다."

    return {
        "ml": ml,
        "dl": dl,
        "ensemble": {
            "label": ensemble_label,
            "risk_score": ensemble_risk,
            "disagreement": round(disagreement, 3),
            "guidance": guidance,
        },
    }
