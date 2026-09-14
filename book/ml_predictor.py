"""Congestion prediction facade backed by trained Random Forest + LSTM models."""

from congestion_ai.service import get_forecast_service


def predict_congestion(hour, route_name, recent_passengers, is_event_day, model_type="ml"):
    service = get_forecast_service()
    return service.predict_legacy(
        hour=hour,
        route_name=route_name,
        recent_passengers=recent_passengers,
        is_event_day=is_event_day,
        model_type=model_type if model_type in {"ml", "dl"} else "ml",
    )


def compare_congestion_models(hour, route_name, recent_passengers, is_event_day, **kwargs):
    service = get_forecast_service()
    return service.compare_legacy(
        hour=hour,
        route_name=route_name,
        recent_passengers=recent_passengers,
        is_event_day=is_event_day,
        **kwargs,
    )


def forecast_congestion(**kwargs):
    service = get_forecast_service()
    return service.forecast(**kwargs)


def get_congestion_model_info():
    service = get_forecast_service()
    return service.get_model_info()
