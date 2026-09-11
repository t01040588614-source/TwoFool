from config import Config


def is_openai_enabled():
    mode = (Config.OPENAI_ENABLED or "auto").strip().lower()
    if mode in {"0", "false", "off", "rule", "rules"}:
        return False
    if mode in {"1", "true", "on", "openai", "gpt"}:
        return bool(Config.OPENAI_API_KEY)
    return bool(Config.OPENAI_API_KEY)


def describe_openai_model(model_id):
    model = (model_id or "").strip()
    if not model:
        return {
            "model": None,
            "model_type": "none",
            "short_label": "GPT",
            "is_fine_tuned": False,
        }
    if model.startswith("ft:"):
        parts = model.split(":")
        # ft:<base>:<org>:<name>:<id>
        name = parts[3] if len(parts) > 3 else parts[-1]
        return {
            "model": model,
            "model_type": "fine_tuned",
            "short_label": f"학습모델 · {name}",
            "is_fine_tuned": True,
        }
    return {
        "model": model,
        "model_type": "standard",
        "short_label": f"GPT · {model}",
        "is_fine_tuned": False,
    }


def get_openai_status():
    configured = bool((Config.OPENAI_API_KEY or "").strip())
    enabled = is_openai_enabled()
    model_info = describe_openai_model(Config.OPENAI_MODEL)

    if not configured:
        return {
            "status": "rule_based",
            "label": "규칙 기반",
            "display_label": "규칙 기반",
            "openai_configured": False,
            "openai_enabled": False,
            "model": None,
            "model_type": "none",
            "is_fine_tuned": False,
        }

    if not enabled:
        return {
            "status": "rule_based",
            "label": "규칙 기반 (GPT 비활성)",
            "display_label": "GPT · 비활성",
            "openai_configured": True,
            "openai_enabled": False,
            "model": model_info["model"],
            "model_type": model_info["model_type"],
            "is_fine_tuned": model_info["is_fine_tuned"],
        }

    return {
        "status": "ready",
        "label": "GPT 연동" if not model_info["is_fine_tuned"] else "학습모델 연동",
        "display_label": model_info["short_label"],
        "openai_configured": True,
        "openai_enabled": True,
        "model": model_info["model"],
        "model_type": model_info["model_type"],
        "is_fine_tuned": model_info["is_fine_tuned"],
    }


def format_openai_boot_message():
    status = get_openai_status()
    if status.get("openai_enabled"):
        kind = "학습(파인튜닝) OpenAI" if status.get("is_fine_tuned") else "OpenAI"
        return f"[SCMAGLEV] {kind} 활성 · {status.get('display_label')}"
    if status.get("openai_configured"):
        return "[SCMAGLEV] OpenAI Key 감지 · OPENAI_ENABLED=0 으로 규칙 기반 사용"
    return "[SCMAGLEV] OpenAI 미설정 · 규칙 기반 모드"
