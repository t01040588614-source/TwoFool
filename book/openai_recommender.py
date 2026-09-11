import json
from urllib import error as urllib_error, request as urllib_request

from config import Config
from openai_config import get_openai_status, is_openai_enabled

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = """\
당신은 한국 고속철도·SCMaglev 승객 여행 추천 어시스턴트입니다.
사용자에게 3개의 열차 추천(가장 빠름/가장 저렴/가장 쾌적)에 대해 짧고 신뢰감 있는 한국어 reason_line을 작성합니다.
반드시 JSON만 출력하세요. 마크다운 코드블록은 사용하지 마세요.

출력 형식:
{
  "recommendations": [
    {"tag": "fastest", "tag_label": "가장 빠름", "reason_line": "한 줄 설명"},
    {"tag": "cheapest", "tag_label": "가장 저렴", "reason_line": "한 줄 설명"},
    {"tag": "comfortable", "tag_label": "가장 쾌적", "reason_line": "한 줄 설명"}
  ]
}

규칙:
- tag 값은 fastest, cheapest, comfortable 중 하나만 사용
- reason_line은 40자 이내, 구체적 수치(소요시간·요금·혼잡)를 포함
- tag_label은 입력과 동일하게 유지
"""


def _call_openai_chat(messages, *, response_format=None, temperature=0.4):
    api_key = Config.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    payload = {
        "model": Config.OPENAI_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format

    request_body = json.dumps(payload).encode("utf-8")
    request_obj = urllib_request.Request(
        url=OPENAI_CHAT_URL,
        data=request_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request_obj, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"message": body or "OpenAI API 호출에 실패했습니다."}
        message = parsed.get("error", {}).get("message") or parsed.get("message") or "OpenAI API 호출에 실패했습니다."
        raise RuntimeError(message) from exc
    except urllib_error.URLError as exc:
        raise RuntimeError("OpenAI API 서버와 통신할 수 없습니다.") from exc

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI API 응답에 choices가 없습니다.")
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("OpenAI API 응답 본문이 비어 있습니다.")
    return content


def _build_user_prompt(recommendations, context):
    departure = (context or {}).get("departure") or "전체"
    arrival = (context or {}).get("arrival") or "전체"
    rows = []
    for row in recommendations:
        schedule = row.get("schedule") or {}
        ensemble = (row.get("ai") or {}).get("ensemble") or {}
        rows.append(
            {
                "tag": row.get("tag"),
                "tag_label": row.get("tag_label"),
                "train_number": schedule.get("train_number"),
                "departure_station": schedule.get("departure_station"),
                "arrival_station": schedule.get("arrival_station"),
                "departure_time": schedule.get("departure_time"),
                "duration_minutes": schedule.get("duration_minutes"),
                "estimated_fare": schedule.get("estimated_fare"),
                "congestion_label": ensemble.get("label"),
                "current_reason_line": row.get("reason_line"),
            }
        )
    return json.dumps(
        {
            "route": {"departure": departure, "arrival": arrival},
            "recommendations": rows,
        },
        ensure_ascii=False,
    )


def _merge_openai_recommendations(base_recommendations, openai_payload):
    if not isinstance(openai_payload, dict):
        return base_recommendations
    ai_rows = openai_payload.get("recommendations")
    if not isinstance(ai_rows, list):
        return base_recommendations

    by_tag = {}
    for row in ai_rows:
        tag = row.get("tag")
        if tag:
            by_tag[tag] = row

    merged = []
    for base in base_recommendations:
        tag = base.get("tag")
        ai_row = by_tag.get(tag) or {}
        updated = dict(base)
        reason_line = ai_row.get("reason_line")
        tag_label = ai_row.get("tag_label")
        if isinstance(reason_line, str) and reason_line.strip():
            updated["reason_line"] = reason_line.strip()
        if isinstance(tag_label, str) and tag_label.strip():
            updated["tag_label"] = tag_label.strip()
        merged.append(updated)
    return merged


def _build_ai_meta(**extra):
    status = get_openai_status()
    return {
        "openai_configured": status.get("openai_configured", False),
        "openai_enabled": status.get("openai_enabled", False),
        "display_label": status.get("display_label"),
        "is_fine_tuned": status.get("is_fine_tuned", False),
        "model_type": status.get("model_type"),
        "model": status.get("model") or Config.OPENAI_MODEL,
        **extra,
    }


def enhance_passenger_recommendations(recommendations, context=None):
    if not recommendations:
        return recommendations, _build_ai_meta(source="rule_based")

    if not is_openai_enabled():
        return recommendations, _build_ai_meta(source="rule_based")

    try:
        content = _call_openai_chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(recommendations, context)},
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(content)
        enhanced = _merge_openai_recommendations(recommendations, parsed)
        return enhanced, _build_ai_meta(source="openai")
    except (RuntimeError, json.JSONDecodeError, TypeError, ValueError):
        return recommendations, _build_ai_meta(source="rule_based", fallback=True)
