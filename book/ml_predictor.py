"""Congestion prediction facade backed by trained Random Forest + LSTM models."""

from congestion_ai.fallback import build_heuristic_comparison, build_heuristic_forecast
from congestion_ai.service import get_forecast_service


def predict_congestion(hour, route_name, recent_passengers, is_event_day, model_type="ml"):
    try:
        service = get_forecast_service()
        return service.predict_legacy(
            hour=hour,
            route_name=route_name,
            recent_passengers=recent_passengers,
            is_event_day=is_event_day,
            model_type=model_type if model_type in {"ml", "dl"} else "ml",
        )
    except Exception:
        comparison = build_heuristic_comparison(
            hour=hour,
            route_name=route_name,
            recent_passengers=recent_passengers,
            is_event_day=is_event_day,
        )
        model_type = model_type if model_type in {"ml", "dl"} else "ml"
        return comparison[model_type]


def compare_congestion_models(hour, route_name, recent_passengers, is_event_day, **kwargs):
    try:
        service = get_forecast_service()
        return service.compare_legacy(
            hour=hour,
            route_name=route_name,
            recent_passengers=recent_passengers,
            is_event_day=is_event_day,
            **kwargs,
        )
    except Exception:
        comparison = build_heuristic_comparison(
            hour=hour,
            route_name=route_name,
            recent_passengers=recent_passengers,
            is_event_day=is_event_day,
        )
        comparison["forecast"] = build_heuristic_forecast(
            hour=hour,
            route_name=route_name,
            recent_passengers=recent_passengers,
            is_event_day=is_event_day,
            train_id=kwargs.get("train_id"),
        )
        return comparison


def forecast_congestion(**kwargs):
    try:
        service = get_forecast_service()
        return service.forecast(**kwargs)
    except Exception:
        return build_heuristic_forecast(
            hour=kwargs.get("hour", 12),
            route_name=kwargs.get("route_name", "서울권 순환"),
            recent_passengers=kwargs.get("recent_passengers", 500),
            is_event_day=bool(kwargs.get("is_event_day", False)),
            train_id=kwargs.get("train_id"),
        )


def get_congestion_model_info():
    try:
        service = get_forecast_service()
        return service.get_model_info()
    except Exception:
        return {
            "ready": True,
            "training": False,
            "heuristic_only": True,
            "data_status": "규칙 기반 fallback",
        }
