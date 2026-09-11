import json
import re

from openai_config import get_openai_status, is_openai_enabled
from openai_recommender import _call_openai_chat

DASHBOARD_CHAT_SYSTEM_PROMPT = """\
당신은 SCMagLEV 철도 관제센터 운영 AI 어시스턴트입니다.
간결하고 명확한 한국어로 관제사를 돕습니다.

역할:
- 운행 현황·지연·고장·ACK·인수인계·승객 안내 초안 요약
- context.dashboard 데이터만 사실로 사용
- 없는 수치·열차번호·이벤트 ID를 만들지 않음
- 실행 가능한 조치는 동사로 시작하는 짧은 불릿

답변 형식:
- 2~6문장 또는 3~5개 불릿
- 필요 시 **굵게** 강조 (마크다운 허용)
- status_counts, alarms, delayed_trains, fault_trains를 우선 참고
"""

CHAT_SYSTEM_PROMPT = """\
당신은 SCMagLEV 초고속 자기부상열차 승객 여행 AI 도우미입니다.
친절하고 명확한 한국어로 답변합니다.

역할:
- 열차 검색·예매·운행 지연·좌석·요금·환불·할인 안내
- context에 있는 열차/역/운행 데이터만 사실로 사용
- 데이터에 없는 구체적 시간·요금·열차번호는 만들지 않음
- 모르는 내용은 예매 화면 검색 또는 고객센터(1588-0000) 안내

답변 형식:
- 2~6문장 또는 짧은 불릿 목록
- 필요 시 **굵게** 강조 (마크다운 허용)
- context.schedules가 있으면 상위 3편 이내로 요약 (출발·도착·소요·요금)
- context.control_trust가 있으면 운행 신뢰/지연 위험도 함께 안내
"""

MAX_HISTORY = 12


def _normalize_history(history):
    if not isinstance(history, list):
        return []
    cleaned = []
    for row in history[-MAX_HISTORY:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role", "")).strip().lower()
        content = str(row.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        cleaned.append({"role": role, "content": content[:2000]})
    return cleaned


def _format_schedules(schedules):
    lines = []
    for row in (schedules or [])[:3]:
        lines.append(
            f"- {row.get('train_number', '열차')}: "
            f"{row.get('departure_station', '?')} {row.get('departure_time', '')} → "
            f"{row.get('arrival_station', '?')} · {row.get('duration_minutes', '?')}분 · "
            f"{int(row.get('estimated_fare') or 0):,}원"
        )
    return "\n".join(lines)


def _rule_based_chat(message, context):
    context = context or {}
    message = (message or "").strip()
    lower = message.lower()
    departure = context.get("departure") or ""
    arrival = context.get("arrival") or ""
    schedules = context.get("schedules") or []
    trust = context.get("control_trust") or {}

    if any(word in lower for word in ("안녕", "hello", "hi")):
        return (
            "안녕하세요! SCMagLEV AI 여행 도우미입니다.\n"
            "출발·도착역을 알려주시면 열차를 찾아드리고, 예매·지연·환불도 안내해 드릴게요."
        )

    if any(word in lower for word in ("환불", "취소", "환급")):
        return (
            "**예약 취소·환불**\n"
            "- 예약 확인·취소 메뉴에서 본인 예약을 선택해 취소할 수 있습니다.\n"
            "- 출발 1시간 전까지는 전액 환불, 이후에는 수수료가 적용될 수 있습니다.\n"
            "- 결제 대기 중인 예약은 마감 전까지 결제를 완료하거나 자동 취소됩니다."
        )

    if any(word in lower for word in ("예매", "결제", "승차권", "티켓")):
        return (
            "**승차권 예매 방법**\n"
            "1. 승차권 예매 탭에서 출발·도착역과 날짜를 선택\n"
            "2. AI 추천 또는 검색 결과에서 열차 선택\n"
            "3. 좌석 선택 후 로그인·결제\n"
            "지금 화면의 출발·도착역을 기준으로도 바로 검색해 드릴 수 있어요."
        )

    if any(word in lower for word in ("지연", "운행", "고장", "현황", "상황")):
        risk = trust.get("delay_risk") or "LOW"
        line = trust.get("message_line") or "관제 연동 데이터를 확인 중입니다."
        risk_label = {"HIGH": "높음", "MEDIUM": "보통", "LOW": "낮음"}.get(risk, risk)
        return (
            f"**현재 운행 신뢰**\n"
            f"- {line}\n"
            f"- 지연 위험도: **{risk_label}**\n"
            "구체적인 열차 지연은 예약 확인 또는 운행정보 탭에서 확인해 주세요."
        )

    if schedules and (departure or arrival):
        route = f"{departure or '?'} → {arrival or '?'}"
        body = _format_schedules(schedules)
        mode = context.get("search_mode") or "direct"
        extra = " (환승 포함)" if mode == "transfer" else ""
        return (
            f"**{route}** 검색 결과{extra}입니다.\n{body}\n\n"
            "마음에 드는 편을 선택해 예매 탭에서 바로 예매할 수 있습니다."
        )

    if departure and arrival:
        return (
            f"**{departure} → {arrival}** 구간을 검색했지만 표시할 운행이 없습니다.\n"
            "출발역·도착역 이름을 다시 확인하거나, 예매 화면에서 날짜를 바꿔 검색해 보세요."
        )

    if any(word in lower for word in ("역", "노선", "어디")):
        names = context.get("station_names") or []
        preview = ", ".join(names[:12])
        suffix = " …" if len(names) > 12 else ""
        return (
            f"SCMagLEV는 전국 주요 도시를 연결합니다.\n"
            f"예: {preview}{suffix}\n"
            "「서울에서 부산」처럼 출발·도착을 알려주시면 열차를 찾아드릴게요."
        )

    return (
        "SCMagLEV 여행 도우미입니다.\n"
        "출발·도착역(예: 서울→수원), 지연 현황, 예매·환불 방법 등을 물어보세요.\n"
        "OpenAI Key를 설정하면 더 자연스러운 GPT 답변이 제공됩니다."
    )


def _rule_based_dashboard_chat(message, context):
    context = context or {}
    dashboard = context.get("dashboard") or {}
    status = dashboard.get("status_counts") or {}
    trust = dashboard.get("passenger_trust") or context.get("control_trust") or {}
    alarms = dashboard.get("alarms") or []
    delayed = dashboard.get("delayed_trains") or []
    faults = dashboard.get("fault_trains") or []

    fleet = dashboard.get("fleet_counts") or {}
    in_service = int(status.get("in_service", 0) or fleet.get("in_service", 0) or 0)
    waiting = int(status.get("waiting", 0) or fleet.get("waiting", 0) or 0)
    normal = int(status.get("normal", 0) or 0)
    delayed_count = int(status.get("delayed", 0) or 0)
    stopped = int(status.get("stopped", 0) or 0)
    disrupted = int(status.get("disrupted", 0) or 0)
    arrived = int(status.get("arrived", 0) or fleet.get("arrived", 0) or 0)
    tracked = dashboard.get("tracked_train_count")
    lower = message.lower()

    if any(word in lower for word in ("안녕", "hello", "hi")):
        return (
            "안녕하세요! SCMagLEV **관제 AI**입니다.\n"
            "운행 현황, 지연·고장, ACK 우선순위, 승객 안내 문구를 물어보세요."
        )

    if any(word in lower for word in ("인수인계", "교대", "핸드오버")):
        alarm_lines = "\n".join(f"- {row.get('message', '')}" for row in alarms[:3]) or "- 특이 알람 없음"
        return (
            f"**인수인계 요약**\n"
            f"- 운행 {in_service} (정상 {normal} / 지연 {delayed_count} / 정차·장애 {stopped + disrupted})\n"
            f"- 출발대기 {waiting} · 도착 {arrived}\n"
            f"- 승객 신뢰: {trust.get('message_line', '관제 연동')}\n"
            f"**주요 알람**\n{alarm_lines}\n"
            "「✨ AI 인수인계」 버튼으로 상세 초안을 생성할 수 있습니다."
        )

    if any(word in lower for word in ("지연", "운행", "현황", "상황")):
        lines = []
        if delayed:
            for row in delayed[:3]:
                lines.append(
                    f"- {row.get('train_number', '열차')}: "
                    f"{row.get('departure_station', '?')}→{row.get('arrival_station', '?')} "
                    f"({row.get('operation_status', '-')})"
                )
        body = "\n".join(lines) if lines else "- 현재 표시할 지연 열차 상세 없음"
        tracked_line = f" · 추적 {tracked}대" if tracked else ""
        return (
            f"**운행 현황**{tracked_line}\n"
            f"- 운행 {in_service} (정상 {normal} / 지연 {delayed_count} / 정차·장애 {stopped + disrupted})\n"
            f"- 출발대기 {waiting} · 도착 {arrived}\n"
            f"- 지연 위험: **{trust.get('delay_risk_level', 'LOW')}**\n"
            f"**지연/주의 열차**\n{body}"
        )

    if any(word in lower for word in ("고장", "fault", "복구")):
        if faults:
            body = "\n".join(
                f"- {row.get('train_number', '열차')}: {row.get('fault', {}).get('label', '고장')}"
                for row in faults[:3]
            )
        else:
            body = "- 활성 고장 열차 없음"
        return f"**고장/복구 현황**\n{body}\n「🔧 고장 복구」 또는 AI 복구 안내 버튼을 활용하세요."

    if any(word in lower for word in ("승객", "안내", "공지", "방송")):
        return (
            "**승객 안내**\n"
            "- 「✨ AI 지연안내 발송」「✨ AI 복구안내 발송」으로 초안 생성·발송\n"
            "- 「✨ AI 안내문」으로 인수인계 패널에 초안 작성\n"
            f"- 승객 앱 신뢰: {trust.get('message_line', '관제 연동')}"
        )

    if any(word in lower for word in ("ack", "이벤트", "알람")):
        if alarms:
            body = "\n".join(f"- [{row.get('severity', 'info')}] {row.get('message', '')}" for row in alarms[:4])
        else:
            body = "- 활성 알람 없음"
        return f"**이벤트/알람**\n{body}\n이슈 목록에서 ACK 후 조치하세요."

    return (
        "SCMagLEV 관제 AI입니다.\n"
        "운행 현황, 지연·고장, 인수인계, 승객 안내 문구 등을 물어보세요.\n"
        "OpenAI Key 연결 시 GPT/학습모델로 더 자연스럽게 답변합니다."
    )


def _is_dashboard_chat(context):
    context = context or {}
    audience = str(context.get("audience") or "").strip().lower()
    page = str(context.get("page") or "").strip().lower()
    return audience == "dashboard" or page == "dashboard"


def _build_user_payload(message, context, history):
    return json.dumps(
        {
            "question": message,
            "context": context or {},
            "recent_history": history[-6:],
        },
        ensure_ascii=False,
    )


def generate_chat_reply(message, history=None, context=None):
    message = (message or "").strip()
    if not message:
        raise ValueError("질문을 입력해주세요.")

    history = _normalize_history(history)
    context = context if isinstance(context, dict) else {}
    ai_status = get_openai_status()
    dashboard_mode = _is_dashboard_chat(context)
    fallback = (
        _rule_based_dashboard_chat(message, context)
        if dashboard_mode
        else _rule_based_chat(message, context)
    )

    if not is_openai_enabled():
        return fallback, {**ai_status, "source": "rule_based"}

    system_prompt = DASHBOARD_CHAT_SYSTEM_PROMPT if dashboard_mode else CHAT_SYSTEM_PROMPT
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": _build_user_payload(message, context, history),
        }
    )

    try:
        content = _call_openai_chat(messages, temperature=0.5)
        reply = (content or "").strip()
        if not reply:
            raise ValueError("OpenAI 응답이 비어 있습니다.")
        return reply, {
            **ai_status,
            "source": "openai",
            "model": ai_status.get("model"),
        }
    except (RuntimeError, ValueError, TypeError):
        return fallback, {
            **ai_status,
            "source": "rule_based",
            "fallback": True,
            "model": ai_status.get("model"),
        }


def parse_route_from_message(message, station_names):
    message = (message or "").strip()
    if not message or not station_names:
        return {}

    names = sorted({name for name in station_names if name}, key=len, reverse=True)
    found = [name for name in names if name in message]
    if len(found) >= 2:
        return {"departure": found[0], "arrival": found[1]}

    patterns = [
        r"(.+?)\s*(?:에서|→|->|부터)\s*(.+?)\s*(?:까지|가|행|역)?$",
        r"(.+?)\s*(?:to|TO)\s*(.+?)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if not match:
            continue
        departure = match.group(1).strip().replace("역", "")
        arrival = match.group(2).strip().replace("역", "")
        dep = next((name for name in names if name in departure or departure in name), None)
        arr = next((name for name in names if name in arrival or arrival in name), None)
        if dep and arr:
            return {"departure": dep, "arrival": arr}
    return {}
