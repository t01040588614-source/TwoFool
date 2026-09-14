from datetime import datetime
import json

from config import Config
from openai_config import get_openai_status, is_openai_enabled
from openai_recommender import _call_openai_chat

DRAFT_TYPES = {
    "handover",
    "shift_report",
    "delay_notice",
    "recovery_notice",
    "broadcast",
    "incident_actions",
}

INCIDENT_SYSTEM_PROMPT = """\
당신은 SCMaglev 철도 관제센터 운영 보조 AI입니다.
선택된 이벤트와 현재 운행 상태를 바탕으로 관제사가 즉시 실행할 권장 조치 3가지를 한국어로 작성합니다.
반드시 JSON만 출력하세요. 마크다운 코드블록은 사용하지 마세요.

출력 형식:
{"actions": ["조치 1", "조치 2", "조치 3"]}

규칙:
- 각 조치는 35자 이내, 실행 가능한 동사로 시작
- 사실에 없는 정보를 만들지 마세요
- ACK, 승객 공지, 고장 복구, 감속 권고 등 관제 업무에 맞게 작성
"""

SYSTEM_PROMPT = """\
당신은 SCMaglev 철도 관제센터 운영 보조 AI입니다.
관제사가 승객 안내·인수인계·교대 리포트에 바로 사용할 수 있는 한국어 문구를 작성합니다.
반드시 JSON만 출력하세요. 마크다운 코드블록은 사용하지 마세요.

출력 형식:
{"text": "작성된 본문"}

규칙:
- 사실에 없는 수치·열차번호·역명을 만들지 마세요
- 공손하고 간결한 관제/승객 안내 톤
- 승객 안내문은 [지연 안내] 또는 [복구 안내] 또는 [안내] 접두어 포함
- 인수인계·교대 리포트는 불릿(-) 또는 줄바꿈으로 구조화
- 800자 이내
"""


def _count_delayed(status_counts):
    counts = status_counts or {}
    return (
        int(counts.get("delayed", 0) or 0)
        + int(counts.get("stopped", 0) or 0)
        + int(counts.get("disrupted", 0) or 0)
    )


def _rule_based_draft(draft_type, context):
    context = context or {}
    status_counts = context.get("status_counts") or {}
    events = context.get("events") or []
    timeline = context.get("action_timeline") or []
    incident = context.get("selected_incident") or {}
    fault_trains = context.get("fault_trains") or []
    delayed_trains = context.get("delayed_trains") or []
    passenger_trust = context.get("passenger_trust") or {}

    now_label = datetime.now().strftime("%Y-%m-%d %H:%M")
    normal = int(status_counts.get("normal", 0) or 0)
    delayed_total = _count_delayed(status_counts)
    arrived = int(status_counts.get("arrived", 0) or 0)
    unresolved = sum(1 for row in events if not row.get("is_acknowledged"))
    critical = sum(
        1 for row in events if row.get("severity") == "critical" and not row.get("is_acknowledged")
    )
    trust_line = passenger_trust.get("message_line") or "관제 연동 정상"

    if draft_type == "shift_report":
        return "\n".join(
            [
                f"[교대 리포트] {now_label}",
                f"- 운행 상태: 정상 {normal} / 지연·정차·장애 {delayed_total} / 도착 {arrived}",
                f"- 이벤트: 미해결 {unresolved}건 / Critical {critical}건",
                f"- 승객 연동: {trust_line}",
                f"- 주요 조치: {timeline[0] if timeline else '기록 없음'}",
            ]
        )

    if draft_type == "handover":
        top_issues = [
            row.get("message")
            for row in events
            if not row.get("is_acknowledged")
        ][:3]
        fault_lines = [
            f"{row.get('train_number', '?')} · {((row.get('fault') or {}).get('label')) or '고장'}"
            for row in fault_trains[:3]
        ]
        lines = [
            f"[인수인계] {now_label}",
            f"- 네트워크: 정상 {normal} / 이상 {delayed_total}",
            f"- 미해결 이벤트 {unresolved}건 (Critical {critical}건)",
        ]
        if top_issues:
            lines.append("- 주요 이슈:")
            lines.extend(f"  · {msg}" for msg in top_issues if msg)
        if fault_lines:
            lines.append("- 고장 열차:")
            lines.extend(f"  · {line}" for line in fault_lines)
        if timeline:
            lines.append(f"- 최근 조치: {timeline[0]}")
        lines.append("- 다음 교대: ACK 미처리 건 우선 확인, 승객 공지 발송 여부 점검")
        return "\n".join(lines)

    if draft_type == "delay_notice":
        detail = ""
        if incident.get("message"):
            detail = f" ({incident['message']})"
        elif delayed_trains:
            first = delayed_trains[0]
            detail = f" ({first.get('train_number', '일부 열차')} 등 {len(delayed_trains)}대)"
        return (
            f"[지연 안내] 현재 일부 구간 혼잡 및 운행 점검으로 열차가 지연되고 있습니다{detail}. "
            "승객 여러분의 양해 부탁드립니다."
        )

    if draft_type == "recovery_notice":
        return (
            "[복구 안내] 장애 조치가 완료되어 열차가 순차적으로 정상 운행을 재개하고 있습니다. "
            "이용에 불편을 드려 죄송합니다."
        )

    if draft_type == "broadcast":
        if incident.get("message"):
            return f"[안내] {incident['message']} 관련 운행 상황을 점검 중입니다. 지연 가능성이 있으니 승차 시간을 확인해주세요."
        if delayed_total > 0:
            return (
                f"[안내] 현재 지연·점검 중인 열차가 {delayed_total}대 있습니다. "
                "역 안내 방송 및 앱 알림을 확인해주세요."
            )
        return "[안내] 현재 일부 열차 운행 상황을 점검 중입니다. 잠시만 기다려주세요."

    if draft_type == "incident_actions":
        incident = incident or {}
        msg = incident.get("message") or "선택된 이벤트"
        severity = incident.get("severity") or "info"
        return "\n".join(
            [
                f"[권장 조치] {msg}",
                f"1. 심각도({severity}) 확인 후 ACK 처리",
                "2. 영향 열차·노선 점검 및 승객 안내 발송",
                "3. 조치 완료 시 복구완료 로그 기록",
            ]
        )

    return ""


def _rule_based_incident_actions(context):
    incident = (context or {}).get("selected_incident") or {}
    msg = incident.get("message") or "선택된 이벤트"
    severity = incident.get("severity") or "info"
    event_type = incident.get("event_type") or ""
    actions = [
        f"심각도 {severity} 이벤트 ACK 및 담당자 지정",
        "영향 열차·노선 확인 후 승객 공지 검토",
        "조치 후 복구완료 로그 기록",
    ]
    if "VEHICLE" in event_type:
        actions[1] = "고장 열차 상태 확인 및 수동 복구 여부 판단"
    elif "ALARM" in event_type:
        actions[0] = f"알람 '{msg[:24]}' 원인 확인 후 ACK"
    return actions


def _build_user_prompt(draft_type, context):
    instructions = {
        "handover": "다음 교대 관제사를 위한 인수인계 메모 초안을 작성하세요.",
        "shift_report": "금일 교대 마감 리포트 초안을 작성하세요.",
        "delay_notice": "승객에게 발송할 지연 안내문 초안을 작성하세요.",
        "recovery_notice": "승객에게 발송할 복구/정상화 안내문 초안을 작성하세요.",
        "broadcast": "역 및 열차 방송용 긴급 승객 안내 초안을 작성하세요.",
        "incident_actions": "선택된 이벤트에 대한 관제 권장 조치 3가지를 작성하세요.",
    }
    return json.dumps(
        {
            "task": instructions.get(draft_type, "관제 운영 문구를 작성하세요."),
            "draft_type": draft_type,
            "context": context or {},
        },
        ensure_ascii=False,
    )


def _merge_incident_actions(parsed, context):
    actions = parsed.get("actions") if isinstance(parsed, dict) else None
    if not isinstance(actions, list):
        return _rule_based_incident_actions(context)
    cleaned = [str(item).strip() for item in actions if str(item).strip()]
    if len(cleaned) < 3:
        fallback = _rule_based_incident_actions(context)
        while len(cleaned) < 3 and fallback:
            cleaned.append(fallback[len(cleaned)])
    return cleaned[:3]


def generate_dashboard_draft(draft_type, context=None):
    draft_type = (draft_type or "").strip().lower()
    if draft_type not in DRAFT_TYPES:
        raise ValueError("지원하지 않는 draft_type입니다.")

    fallback_text = _rule_based_draft(draft_type, context)
    ai_status = get_openai_status()

    if draft_type == "incident_actions":
        fallback_actions = _rule_based_incident_actions(context)
        if not is_openai_enabled():
            return None, {
                **ai_status,
                "source": "rule_based",
                "draft_type": draft_type,
                "actions": fallback_actions,
            }
        try:
            content = _call_openai_chat(
                [
                    {"role": "system", "content": INCIDENT_SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(draft_type, context)},
                ],
                response_format={"type": "json_object"},
            )
            parsed = json.loads(content)
            actions = _merge_incident_actions(parsed, context)
            return None, {
                **ai_status,
                "source": "openai",
                "draft_type": draft_type,
                "model": Config.OPENAI_MODEL,
                "actions": actions,
            }
        except (RuntimeError, json.JSONDecodeError, TypeError, ValueError):
            return None, {
                **ai_status,
                "source": "rule_based",
                "draft_type": draft_type,
                "fallback": True,
                "model": Config.OPENAI_MODEL,
                "actions": fallback_actions,
            }

    if not is_openai_enabled():
        return fallback_text, {
            **ai_status,
            "source": "rule_based",
            "draft_type": draft_type,
        }

    try:
        content = _call_openai_chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(draft_type, context)},
            ],
            response_format={"type": "json_object"},
        )
        parsed = json.loads(content)
        text = parsed.get("text") if isinstance(parsed, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ValueError("OpenAI 응답에 text가 없습니다.")
        return text.strip(), {
            **ai_status,
            "source": "openai",
            "draft_type": draft_type,
            "model": Config.OPENAI_MODEL,
        }
    except (RuntimeError, json.JSONDecodeError, TypeError, ValueError):
        return fallback_text, {
            **ai_status,
            "source": "rule_based",
            "draft_type": draft_type,
            "fallback": True,
            "model": Config.OPENAI_MODEL,
        }
