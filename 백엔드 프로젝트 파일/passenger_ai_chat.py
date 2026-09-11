"""Passenger AI chat — rule-based answers with optional OpenAI."""

from __future__ import annotations

import json
import re
from typing import Any

from config import Config
from openai_recommender import _call_openai_chat, is_openai_enabled

CHAT_SYSTEM_PROMPT = """\
당신은 SCMagLEV 승객 여행 AI 도우미입니다.
열차 검색, 예매 방법, 지연·환불 안내를 짧고 친절한 한국어로 답합니다.
답변은 3~6문장, 불릿은 최대 3개. 마크다운 코드블록은 쓰지 마세요.
제공된 운행·추천 컨텍스트(JSON)가 있으면 반드시 반영하세요.
"""


def is_openai_key_configured() -> bool:
    key = (Config.OPENAI_API_KEY or "").strip()
    if not key:
        return False
    lowered = key.lower()
    if "your-openai" in lowered or "sk-your" in lowered:
        return False
    if key.endswith("..."):
        return False
    return len(key) >= 20


def _parse_route(message: str) -> tuple[str, str]:
    text = message.replace(" ", "")
    arrow = re.search(
        r"([가-힣]{2,8})[-→>]+([가-힣]{2,8})",
        text,
    )
    if arrow:
        return arrow.group(1), arrow.group(2)
    from_to = re.search(
        r"([가-힣]{2,8})(?:에서|부터)([가-힣]{2,8})(?:까지|로|행)?",
        message,
    )
    if from_to:
        return from_to.group(1), from_to.group(2)
    return "", ""


def _format_search_rows(rows: list[dict[str, Any]], limit: int = 3) -> str:
    if not rows:
        return "현재 조건에 맞는 직통·연결 편성을 찾지 못했습니다. 출발·도착 역 이름을 다시 확인해 주세요."
    lines = []
    for row in rows[:limit]:
        dep = row.get("departure_station") or row.get("departure_name") or "?"
        arr = row.get("arrival_station") or row.get("arrival_name") or "?"
        train_no = row.get("train_number") or "열차"
        dep_time = row.get("departure_time") or ""
        duration = row.get("duration_minutes")
        fare = row.get("estimated_fare")
        extra = []
        if duration is not None:
            extra.append(f"약 {duration}분")
        if fare is not None:
            extra.append(f"예상 {int(fare):,}원")
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"• {train_no}: {dep} → {arr} {dep_time}{suffix}")
    return "\n".join(lines)


def _format_recommendations(recs: list[dict[str, Any]]) -> str:
    if not recs:
        return ""
    lines = ["추천 3종:"]
    for row in recs:
        schedule = row.get("schedule") or {}
        lines.append(
            f"• {row.get('tag_label', '추천')}: "
            f"{schedule.get('departure_station', '?')}→{schedule.get('arrival_station', '?')} "
            f"— {row.get('reason_line', '')}"
        )
    return "\n".join(lines)


def _rule_based_reply(
    message: str,
    departure: str,
    arrival: str,
    context: dict[str, Any],
) -> str:
    msg = message.strip()
    dep = departure or context.get("departure") or "서울"
    arr = arrival or context.get("arrival") or "부산"
    parsed_dep, parsed_arr = _parse_route(msg)
    if parsed_dep:
        dep = parsed_dep
    if parsed_arr:
        arr = parsed_arr

    lower = msg.lower()
    summary = context.get("public_summary") or {}
    trust = summary.get("passenger_trust") or {}

    if any(k in msg for k in ("예매", "결제", "승차권", "좌석")):
        return (
            "예매는 상단 예매 페이지에서 출발·도착 역을 선택한 뒤 "
            "「열차 조회」→ 좌석 선택→ 결제 순서입니다.\n"
            "• 로그인 후 예약·결제·내역 조회가 가능합니다.\n"
            "• 결제 실패 시 「결제 재시도」로 이어서 진행할 수 있습니다.\n"
            "• 테스트 환경에서는 토스 모의 결제도 지원합니다."
        )

    if any(k in msg for k in ("지연", "장애", "운행", "현황", "복구")):
        risk = trust.get("delay_risk_level") or "LOW"
        accuracy = trust.get("prediction_accuracy_pct") or summary.get("prediction_accuracy_pct") or 92
        disrupted = trust.get("active_disruptions") or 0
        return (
            f"현재 관제 연동 지연 위험: {risk}, 예측 정확도 약 {accuracy}%입니다.\n"
            f"• 활성 장애/중단 징후 열차: {disrupted}대 수준\n"
            "• 지도·관제 대시보드(/dashboard)에서 실시간 위치와 이벤트 ACK를 확인할 수 있습니다.\n"
            "• 일부 시연 열차는 자동 복구 시나리오가 적용됩니다."
        )

    if any(k in msg for k in ("환불", "취소")):
        return (
            "예약 확인·취소 메뉴에서 본인 예약을 선택해 취소할 수 있습니다.\n"
            "• 결제 완료 전(pending)은 결제 마감 시간 내 재결제 또는 만료 처리됩니다.\n"
            "• 취소 후 좌석은 다시 예매 가능 상태로 돌아갑니다."
        )

    if any(k in lower for k in ("fastest",)) or "빠른" in msg or "빨리" in msg:
        rows = context.get("search_rows") or []
        return (
            f"{dep} → {arr} 구간에서 가장 빠른 편 위주로 정리했습니다.\n"
            + _format_search_rows(rows)
        )

    if "저렴" in msg or "싸" in msg or "할인" in msg:
        rec_text = _format_recommendations(context.get("recommendations") or [])
        if rec_text:
            return f"{dep} → {arr} 기준 {rec_text}"
        rows = context.get("search_rows") or []
        return (
            f"{dep} → {arr} 구간 요금·시간 비교입니다.\n"
            + _format_search_rows(rows)
        )

    if any(k in msg for k in ("추천", "부산", "서울", "수원", "편성", "열차")):
        rec_text = _format_recommendations(context.get("recommendations") or [])
        search_text = _format_search_rows(context.get("search_rows") or [])
        header = f"{dep} → {arr} 여정 기준 안내입니다.\n"
        if rec_text:
            return header + rec_text + "\n\n" + search_text
        return header + search_text

    return (
        "안녕하세요! SCMagLEV 여행 AI 도우미입니다.\n"
        "열차 검색, 예매 방법, 지연·환불 안내를 도와드릴게요.\n"
        "• 「서울→부산 추천」처럼 구간을 적어 주세요.\n"
        "• 아래 추천 질문 버튼으로 바로 시작할 수 있습니다.\n"
        "• OpenAI Key 연결 시 GPT/학습모델로 더 자연스럽게 답변합니다."
    )


def _openai_reply(message: str, context: dict[str, Any]) -> str:
    payload = {
        "question": message,
        "context": {
            "departure": context.get("departure"),
            "arrival": context.get("arrival"),
            "public_summary": context.get("public_summary"),
            "search_preview": (context.get("search_rows") or [])[:3],
            "recommendations": context.get("recommendations"),
        },
    }
    content = _call_openai_chat(
        [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
    )
    return content.strip()


def build_chat_reply(
    message: str,
    departure: str,
    arrival: str,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    configured = is_openai_key_configured()
    meta: dict[str, Any] = {
        "source": "rule_based",
        "openai_configured": configured,
        "openai_enabled": is_openai_enabled() and configured,
        "model": Config.OPENAI_MODEL,
    }

    rule_reply = _rule_based_reply(message, departure, arrival, context)

    if meta["openai_enabled"]:
        try:
            gpt_reply = _openai_reply(message, context)
            if gpt_reply:
                meta["source"] = "openai"
                return gpt_reply, meta
        except (RuntimeError, json.JSONDecodeError, TypeError, ValueError):
            meta["fallback"] = True

    return rule_reply, meta


def chat_mode_label(meta: dict[str, Any]) -> str:
    if meta.get("source") == "openai":
        return "GPT"
    if meta.get("openai_configured") and meta.get("fallback"):
        return "규칙 기반 (GPT fallback)"
    return "규칙 기반"
