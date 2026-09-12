import base64
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from functools import wraps
import json
import math
import os
import random
import secrets
import smtplib
import threading
import time
from urllib import error as urllib_error, request as urllib_request

from flask import Flask, jsonify, render_template, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required
from flask_socketio import SocketIO
from sqlalchemy import inspect, or_, text
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from extensions import db, jwt
from ml_predictor import compare_congestion_models, predict_congestion
from models import (
    Comment,
    OperationEventLog,
    Post,
    Reservation,
    Route,
    RouteStation,
    Schedule,
    Seat,
    Station,
    Train,
    TrainLocation,
    TrainType,
    User,
    VerificationCode,
)
from network_seed import (
    ROUTE_SPECS,
    STATION_SPECS,
    _estimate_duration_minutes,
    iter_train_specs,
)
from openai_assistant import generate_chat_reply, parse_route_from_message
from openai_config import format_openai_boot_message, get_openai_status
from openai_dashboard import generate_dashboard_draft
from openai_recommender import enhance_passenger_recommendations

BACKEND_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
LOCAL_TEMPLATE_DIR = os.path.join(BACKEND_DIR, "templates")
ROOT_TEMPLATE_DIR = os.path.join(PROJECT_DIR, "templates")
TEMPLATE_DIR = (
    LOCAL_TEMPLATE_DIR
    if os.path.isdir(LOCAL_TEMPLATE_DIR)
    else ROOT_TEMPLATE_DIR
)

app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=os.path.join(BACKEND_DIR, "static"),
)

app.config.from_object(Config)

db.init_app(app)
jwt.init_app(app)


@jwt.unauthorized_loader
def jwt_unauthorized_callback(_reason):
    return jsonify({
        "message": "로그인이 필요합니다. 관제 계정으로 다시 로그인해 주세요.",
    }), 401


@jwt.invalid_token_loader
def jwt_invalid_token_callback(_reason):
    return jsonify({
        "message": "인증 정보가 유효하지 않습니다. 다시 로그인해 주세요.",
    }), 401


@jwt.expired_token_loader
def jwt_expired_token_callback(_jwt_header, _jwt_payload):
    return jsonify({
        "message": "로그인 세션이 만료되었습니다. 다시 로그인해 주세요.",
    }), 401


@jwt.revoked_token_loader
def jwt_revoked_token_callback(_jwt_header, _jwt_payload):
    return jsonify({
        "message": "만료된 인증 토큰입니다. 다시 로그인해 주세요.",
    }), 401


@jwt.needs_fresh_token_loader
def jwt_needs_fresh_token_callback(_jwt_header, _jwt_payload):
    return jsonify({
        "message": "재인증이 필요합니다. 다시 로그인해 주세요.",
    }), 401


def _socketio_init_kwargs():
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {}
    if os.environ.get("PORT"):
        return {"async_mode": "eventlet"}
    return {}


socketio = SocketIO(app, cors_allowed_origins="*", **_socketio_init_kwargs())

SIMULATION_INTERVAL_SECONDS = 3
_startup_state = {"ready": False, "error": None}
_startup_lock = threading.Lock()
_startup_init_started = False
_simulator_started = False
_simulator_lock = threading.Lock()
MAX_HISTORY_POINTS = 30
train_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY_POINTS))
train_fault_recovery_at = {}
train_fault_last_logged = {}
PAYMENT_TIMEOUT_MINUTES = 10
ALARM_COOLDOWN_SECONDS = 45
alarm_last_emitted_at = {}
SERVICE_DAY_RESET_HOUR = 4
DIRECT_SERVICE_WINDOW_HOURS = 4
MAX_DASHBOARD_TRACKED_TRAINS = int(
    os.getenv("SCMAGLEV_MAX_TRACKED_TRAINS", "688"))
DIRECT_DASHBOARD_HUB_CODES = frozenset(
    {
        "SEO",
        "BUS",
        "DAE",
        "DGU",
        "GWJ",
        "ULS",
        "SUW",
        "ICN",
        "YON",
        "SEJ",
        "CHC",
        "GAN",
        "WON",
        "JEO",
        "CHE",
        "PTG",
        "GIM",
    }
)

TRAIN_FAULT_CATALOG = [
    {
        "code": "ENGINE",
        "label": "엔진/추진",
        "detail": "추진 모터 출력 이상 및 토크 편차 감지",
        "status": "disrupted",
        "speed_factor": 0.0,
        "recovery_min": 18,
        "recovery_max": 45,
    },
    {
        "code": "WHEEL_BRAKE",
        "label": "바퀴/제동",
        "detail": "제동 디스크 과열 및 바퀴 마모 한계 초과",
        "status": "stopped",
        "speed_factor": 0.0,
        "recovery_min": 12,
        "recovery_max": 35,
    },
    {
        "code": "BEARING",
        "label": "베어링",
        "detail": "차축 베어링 진동 계수 임계치 초과",
        "status": "stopped",
        "speed_factor": 0.05,
        "recovery_min": 10,
        "recovery_max": 30,
    },
    {
        "code": "DOOR",
        "label": "출입문",
        "detail": "객실 출입문 센서 불일치 및 재개폐 반복",
        "status": "delayed",
        "speed_factor": 0.45,
        "recovery_min": 6,
        "recovery_max": 18,
    },
    {
        "code": "POWER",
        "label": "전력/집전",
        "detail": "집전 장치 접촉 불안정 및 전압 급변",
        "status": "disrupted",
        "speed_factor": 0.0,
        "recovery_min": 15,
        "recovery_max": 40,
    },
    {
        "code": "SIGNAL",
        "label": "신호",
        "detail": "열차-선로 신호 연동 지연 및 ACK 타임아웃",
        "status": "delayed",
        "speed_factor": 0.35,
        "recovery_min": 8,
        "recovery_max": 22,
    },
    {
        "code": "COMMUNICATION",
        "label": "통신",
        "detail": "차량-관제 통신 패킷 손실률 증가",
        "status": "delayed",
        "speed_factor": 0.5,
        "recovery_min": 5,
        "recovery_max": 16,
    },
    {
        "code": "COOLING",
        "label": "냉각",
        "detail": "슈퍼컨덕터 냉각 온도 상승 및 냉매 순환 저하",
        "status": "stopped",
        "speed_factor": 0.0,
        "recovery_min": 14,
        "recovery_max": 38,
    },
    {
        "code": "SUSPENSION",
        "label": "부상/현가",
        "detail": "자기부상 높이 센서 편차 및 현가 진동 이상",
        "status": "disrupted",
        "speed_factor": 0.1,
        "recovery_min": 16,
        "recovery_max": 42,
    },
    {
        "code": "SENSOR",
        "label": "센서",
        "detail": "속도/위치 센서 교차 검증 실패",
        "status": "delayed",
        "speed_factor": 0.4,
        "recovery_min": 7,
        "recovery_max": 20,
    },
    {
        "code": "HVAC",
        "label": "공조",
        "detail": "객실 공조 압력 저하 및 환기 효율 감소",
        "status": "delayed",
        "speed_factor": 0.55,
        "recovery_min": 6,
        "recovery_max": 15,
    },
    {
        "code": "CONTROL_UNIT",
        "label": "제어장치",
        "detail": "운전실 제어 CPU 응답 지연 및 페일오버 전환",
        "status": "disrupted",
        "speed_factor": 0.0,
        "recovery_min": 20,
        "recovery_max": 50,
    },
]

TRAIN_FAULT_BY_CODE = {item["code"]: item for item in TRAIN_FAULT_CATALOG}
DEMO_FAULT_SPECS = [
    ("SM-1101", "DOOR"),
    ("SM-1003", "SIGNAL"),
    ("SM-5001", "POWER"),
]
MAX_ACTIVE_FAULT_TRAINS = 3
FAULT_RANDOM_CHANCE = 0.0
FAULT_AUTO_RECOVERY_MINUTES = 3
FAULT_STATUS_POOLS = {
    "delayed": [item for item in TRAIN_FAULT_CATALOG if item["status"] == "delayed"],
    "stopped": [item for item in TRAIN_FAULT_CATALOG if item["status"] == "stopped"],
    "disrupted": [item for item in TRAIN_FAULT_CATALOG if item["status"] == "disrupted"],
}


def pick_random_train_fault():
    roll = random.random()
    if roll < 0.08:
        pool = FAULT_STATUS_POOLS["disrupted"]
    elif roll < 0.30:
        pool = FAULT_STATUS_POOLS["stopped"]
    else:
        pool = FAULT_STATUS_POOLS["delayed"]
    return random.choice(pool or TRAIN_FAULT_CATALOG)


def send_recovery_email(email, code, purpose):
    """Gmail SMTP로 계정 찾기 인증번호를 발송합니다."""
    if app.config["TESTING"]:
        return

    required_settings = (
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
    )

    if any(not app.config[setting] for setting in required_settings):
        raise RuntimeError("SMTP 환경변수가 설정되지 않았습니다.")

    purpose_name = "아이디 찾기" if purpose == "username" else "비밀번호 찾기"
    message = EmailMessage()
    message["Subject"] = f"[MY BOARD] {purpose_name} 인증번호"
    message["From"] = app.config["SMTP_FROM_EMAIL"]
    message["To"] = email
    message.set_content(
        f"{purpose_name} 인증번호는 {code}입니다.\n"
        "인증번호는 10분 동안만 유효합니다."
    )

    with smtplib.SMTP(
        app.config["SMTP_HOST"],
        app.config["SMTP_PORT"],
        timeout=10,
    ) as smtp:
        smtp.starttls()
        smtp.login(
            app.config["SMTP_USERNAME"],
            app.config["SMTP_PASSWORD"],
        )
        smtp.send_message(message)


# ==========================================
# Frontend
# ==========================================

@app.after_request
def prevent_html_cache(response):
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.route("/", methods=["GET"])
def index():

    return render_template("index.html")


@app.route("/dashboard", methods=["GET"])
def dashboard():
    return render_template("dashboard.html")


@app.route("/dashboard/action/<action_key>", methods=["GET"])
def dashboard_action_page(action_key):
    action_pages = {
        "broadcast": {
            "title": "긴급 승객 안내 발송",
            "action_type": "EMERGENCY_BROADCAST",
            "description": "운행 지연/장애 발생 시 승객에게 즉시 안내 메시지를 발송합니다.",
            "default_note": "[안내] 현재 일부 구간 운행 상황 점검으로 지연 가능성이 있습니다.",
        },
        "speed-limit": {
            "title": "전 구간 감속 권고 기록",
            "action_type": "SPEED_LIMIT_ADVISORY",
            "description": "안전 운행을 위해 감속 운행 권고를 운영 로그에 남깁니다.",
            "default_note": "안전 운행을 위해 전 구간 감속 권고를 기록했습니다.",
        },
        "spare-train": {
            "title": "예비편성 투입 기록",
            "action_type": "SPARE_TRAIN_DEPLOYMENT",
            "description": "지연 완화를 위해 예비편성 투입 지시를 기록합니다.",
            "default_note": "지연 완화를 위해 예비편성 투입을 지시했습니다.",
        },
        "incident-focus": {
            "title": "최우선 이슈 열기",
            "action_type": "INCIDENT_FOCUS",
            "description": "중대 이벤트를 선택하고 대응 우선순위로 지정합니다.",
            "default_note": "최우선 이슈 포커스 처리",
        },
        "delay-notice": {
            "title": "지연 안내문 발송",
            "action_type": "PASSENGER_NOTICE_DELAY",
            "description": "승객에게 지연 안내문을 발송하고 운영 로그에 남깁니다.",
            "default_note": "[지연 안내] 현재 일부 구간 혼잡으로 열차 운행이 지연되고 있습니다.",
        },
        "recovery-notice": {
            "title": "복구 안내문 발송",
            "action_type": "PASSENGER_NOTICE_RECOVERY",
            "description": "복구 완료 후 정상화 안내문을 발송합니다.",
            "default_note": "[복구 안내] 장애 조치가 완료되어 순차적으로 정상 운행 중입니다.",
        },
        "handover-copy": {
            "title": "인수인계 메모 복사 기록",
            "action_type": "HANDOVER_NOTE_COPY",
            "description": "교대 메모 복사 사실을 기록해 인수인계 추적성을 유지합니다.",
            "default_note": "인수인계 메모 복사 실행",
        },
    }
    page = action_pages.get(action_key)
    if not page:
        return render_template("dashboard_action.html", not_found=True), 404
    return render_template(
        "dashboard_action.html",
        not_found=False,
        action_key=action_key,
        page=page,
    )


# ==========================================
# Health Check
# ==========================================

@app.route("/ping", methods=["GET"])
def ping():
    return "pong", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/api/health", methods=["GET"])
def health():
    if _startup_state["error"]:
        status = "degraded"
    elif _startup_state["ready"]:
        status = "ok"
    else:
        status = "starting"

    payload = {
        "status": status,
        "message": "Backend server is running",
        "db_ready": _startup_state["ready"] and not _startup_state["error"],
        **({"init_error": _startup_state["error"]} if _startup_state["error"] else {}),
    }
    if status != "starting":
        payload["ai"] = get_openai_status()
    return jsonify(payload)


_STARTUP_PUBLIC_GET_PATHS = frozenset({
    "/api/health",
    "/api/scmaglev/ai/status",
    "/api/scmaglev/payment/mode",
    "/api/scmaglev/stations",
    "/api/scmaglev/routes",
    "/api/scmaglev/dashboard/public-summary",
    "/api/scmaglev/dashboard/trains",
    "/api/scmaglev/dashboard/station-heatmap",
})


@app.before_request
def guard_until_db_ready():
    if _startup_state["ready"] or not request.path.startswith("/api/"):
        return None
    if request.path in _STARTUP_PUBLIC_GET_PATHS and request.method == "GET":
        return None
    if request.path.startswith("/api/scmaglev/trains/search") and request.method == "GET":
        return None
    return jsonify({
        "message": "서버 초기화 중입니다. 잠시 후 다시 시도해 주세요.",
        "status": "starting",
    }), 503


@app.route("/api/scmaglev/ai/status", methods=["GET"])
def scmaglev_ai_status():
    return jsonify(get_openai_status())


def _build_ai_chat_context(message, client_context=None):
    client_context = client_context if isinstance(client_context, dict) else {}
    stations = Station.query.order_by(Station.name.asc()).all()
    station_names = [station.name for station in stations]

    departure = str(client_context.get("departure") or "").strip()
    arrival = str(client_context.get("arrival") or "").strip()
    if not departure or not arrival:
        parsed = parse_route_from_message(message, station_names)
        departure = departure or parsed.get("departure", "")
        arrival = arrival or parsed.get("arrival", "")

    schedules = []
    search_mode = None
    if departure and arrival:
        schedules, search_mode = _search_train_schedules(
            departure_name=departure,
            arrival_name=arrival,
            sort_by="departure",
        )
        schedules = schedules[:5]

    control_trust = {}
    dashboard_snapshot = {}
    page = str(client_context.get("page") or "").strip().lower()
    audience = str(client_context.get("audience")
                   or page or "passenger").strip().lower()
    try:
        summary = build_dashboard_summary()
        control_trust = summary.get("passenger_trust") or {}
        if audience == "dashboard" or page == "dashboard":
            client_dashboard = (
                client_context.get("dashboard")
                if isinstance(client_context.get("dashboard"), dict)
                else {}
            )
            dashboard_snapshot = {
                "status_counts": client_dashboard.get("status_counts") or summary.get("status_counts") or {},
                "fleet_counts": client_dashboard.get("fleet_counts") or summary.get("fleet_counts") or {},
                "alarms": client_dashboard.get("alarms") or summary.get("alarms") or [],
                "passenger_trust": client_dashboard.get("passenger_trust") or control_trust,
                "prediction_summary": client_dashboard.get("prediction_summary") or summary.get("prediction_summary") or {},
                "delayed_trains": client_dashboard.get("delayed_trains") or summary.get("delayed_trains") or [],
                "fault_trains": client_dashboard.get("fault_trains") or summary.get("fault_trains") or [],
                "tracked_train_count": client_dashboard.get("tracked_train_count") or summary.get("tracked_train_count"),
                "selected_incident_id": client_dashboard.get("selected_incident_id"),
            }
    except Exception:
        control_trust = {}

    payload = {
        "departure": departure,
        "arrival": arrival,
        "schedules": schedules,
        "search_mode": search_mode,
        "station_names": station_names[:40],
        "control_trust": control_trust,
        "page": page or client_context.get("page"),
        "audience": audience,
    }
    if dashboard_snapshot:
        payload["dashboard"] = dashboard_snapshot
    return payload


@app.route("/api/scmaglev/ai/chat", methods=["POST"])
def scmaglev_ai_chat():
    data = request.get_json() or {}
    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify({"message": "질문을 입력해주세요."}), 400
    if len(message) > 2000:
        return jsonify({"message": "질문이 너무 깁니다. (2000자 이내)"}), 400

    history = data.get("history") if isinstance(
        data.get("history"), list) else []
    client_context = data.get("context") if isinstance(
        data.get("context"), dict) else {}
    chat_context = _build_ai_chat_context(message, client_context)

    try:
        reply, meta = generate_chat_reply(message, history, chat_context)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400

    payload = {
        "reply": reply,
        "ai_meta": meta,
        "route_hint": {
            "departure": chat_context.get("departure"),
            "arrival": chat_context.get("arrival"),
            "schedule_count": len(chat_context.get("schedules") or []),
        },
    }
    return jsonify(payload)


@app.route("/api/scmaglev/payment/mode", methods=["GET"])
def scmaglev_payment_mode():
    mode = get_payment_mode()
    return jsonify({"provider": "tosspayments", **mode})


@app.errorhandler(HTTPException)
def handle_http_exception(error):
    return jsonify({"message": error.description}), error.code


@app.errorhandler(Exception)
def handle_unexpected_exception(error):
    app.logger.exception("처리되지 않은 예외가 발생했습니다: %s", error)
    return jsonify({"message": "서버 내부 오류가 발생했습니다."}), 500


# ==========================================
# SCMAGLEV MVP / Dashboard
# ==========================================

def now_utc_naive():
    return datetime.now(UTC).replace(tzinfo=None)


def isoformat_utc(dt):
    """UTC naive datetime을 ISO 문자열로 (브라우저 파싱용 Z 접미사)."""
    if not dt:
        return None
    return f"{dt.isoformat()}Z"


def service_day(date_time):
    """새벽 기준(기본 04시) 운영일 계산."""
    return (date_time - timedelta(hours=SERVICE_DAY_RESET_HOUR)).date()


def rollover_schedule_to_current_service_day(schedule, current_time):
    """
    운영일이 지났으면 스케줄을 같은 시각으로 다음 운영일에 맞춰 이동합니다.
    예: 새벽 4시 이후 하루가 바뀌면 기존 arrived 상태가 자연스럽게 초기화됩니다.
    """
    schedule_day = service_day(schedule.departure_time)
    current_day = service_day(current_time)
    if schedule_day >= current_day:
        return False
    shift_days = (current_day - schedule_day).days
    schedule.departure_time = schedule.departure_time + \
        timedelta(days=shift_days)
    schedule.arrival_time = schedule.arrival_time + timedelta(days=shift_days)
    return True


def _direct_service_window_minutes():
    return max(int(DIRECT_SERVICE_WINDOW_HOURS * 60), 60)


def _direct_schedule_phase_minutes(train_number):
    return abs(hash(str(train_number or ""))) % _direct_service_window_minutes()


def align_schedule_for_live_service(schedule, train, current_time):
    """추적 열차 출발 시각을 롤링 윈도우에 맞춰 동시 운행 대수를 늘립니다."""
    if not train:
        return False

    duration = schedule.arrival_time - schedule.departure_time
    if duration.total_seconds() <= 0:
        duration = timedelta(minutes=30)

    window_minutes = _direct_service_window_minutes()
    phase = _direct_schedule_phase_minutes(train.train_number)
    target_departure = current_time - timedelta(minutes=window_minutes - phase)
    target_arrival = target_departure + duration

    if target_arrival < current_time - timedelta(minutes=20):
        shift = max(window_minutes // 2,
                    int(duration.total_seconds() // 60) + 5)
        target_departure += timedelta(minutes=shift)
        target_arrival = target_departure + duration

    if target_departure > current_time + timedelta(minutes=90):
        target_departure -= timedelta(minutes=max(window_minutes // 3, 30))
        target_arrival = target_departure + duration

    if (
        abs((schedule.departure_time - target_departure).total_seconds()) < 60
        and abs((schedule.arrival_time - target_arrival).total_seconds()) < 60
    ):
        return False

    schedule.departure_time = target_departure
    schedule.arrival_time = target_arrival
    return True


def refresh_schedule_for_service(schedule, current_time):
    """지난 운행은 다음 운행일로 넘겨 검색·시뮬레이션에 계속 노출되게 합니다."""
    changed = rollover_schedule_to_current_service_day(schedule, current_time)
    duration = schedule.arrival_time - schedule.departure_time
    if duration.total_seconds() <= 0:
        duration = timedelta(minutes=30)
    while schedule.arrival_time < current_time - timedelta(hours=1):
        schedule.departure_time = schedule.departure_time + timedelta(days=1)
        schedule.arrival_time = schedule.departure_time + duration
        changed = True

    train = db.session.get(Train, schedule.train_id)
    if train and align_schedule_for_live_service(schedule, train, current_time):
        changed = True
    return changed


def align_direct_schedule_for_live_service(schedule, train, current_time):
    return align_schedule_for_live_service(schedule, train, current_time)


def parse_departure_after_filter(raw_value):
    value = (raw_value or "").strip()
    if not value:
        return None
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        try:
            return datetime.fromisoformat(f"{value}T00:00:00")
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def parse_int(value, default, minimum=None, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    if minimum is not None:
        parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _retry_sqlite_locked(action, max_attempts=5, delay_seconds=0.2):
    for attempt in range(1, max_attempts + 1):
        try:
            action()
            return
        except OperationalError as exc:
            db.session.rollback()
            message = str(exc).lower()
            if "database is locked" not in message or attempt == max_attempts:
                raise
            time.sleep(delay_seconds * attempt)


def commit_with_sqlite_retry(max_attempts=5, delay_seconds=0.2):
    _retry_sqlite_locked(db.session.commit, max_attempts, delay_seconds)


def flush_with_sqlite_retry(max_attempts=5, delay_seconds=0.2):
    _retry_sqlite_locked(db.session.flush, max_attempts, delay_seconds)


def configure_sqlite():
    if not str(db.engine.url).startswith("sqlite"):
        return
    db.session.execute(text("PRAGMA journal_mode=WAL"))
    db.session.execute(text("PRAGMA busy_timeout=60000"))
    db.session.execute(text("PRAGMA synchronous=NORMAL"))
    commit_with_sqlite_retry()


def serialize_station(station):
    return {
        "id": station.id,
        "code": station.code,
        "name": station.name,
        "latitude": station.latitude,
        "longitude": station.longitude,
    }


def serialize_schedule(schedule):
    train = db.session.get(Train, schedule.train_id)
    train_type = db.session.get(
        TrainType, train.train_type_id) if train else None
    departure_station = db.session.get(Station, schedule.departure_station_id)
    arrival_station = db.session.get(Station, schedule.arrival_station_id)
    duration_minutes = int(
        max((schedule.arrival_time - schedule.departure_time).total_seconds() // 60, 0)
    )
    return {
        "id": schedule.id,
        "train_id": schedule.train_id,
        "train_number": train.train_number if train else None,
        "train_name": train.name if train else None,
        "train_type": train_type.name if train_type else None,
        "train_type_code": train_type.code if train_type else None,
        "departure_station": departure_station.name if departure_station else None,
        "arrival_station": arrival_station.name if arrival_station else None,
        "departure_time": schedule.departure_time.isoformat(),
        "arrival_time": schedule.arrival_time.isoformat(),
        "duration_minutes": duration_minutes,
    }


def calculate_estimated_fare(schedule, seat_type="general"):
    duration_minutes = int(
        max((schedule.arrival_time - schedule.departure_time).total_seconds() // 60, 0)
    )
    train = db.session.get(Train, schedule.train_id)
    train_type = db.session.get(
        TrainType, train.train_type_id) if train else None
    base_fare = 8500
    speed_factor = 1.9 if (
        train_type and train_type.code == "HIGHSPEED") else 1.0
    time_factor = 1 + (duration_minutes / 110)
    seat_factor_map = {
        "general": 1.0,
        "senior": 0.9,
        "pregnant": 0.95,
        "accessible": 0.85,
        "wheelchair": 0.8,
    }
    seat_factor = seat_factor_map.get(seat_type, 1.0)
    fare = int(round(base_fare * speed_factor * time_factor * seat_factor, -2))
    return max(fare, 3000)


def user_role_or_default(user):
    return user.role if user and user.role else "passenger"


def get_toss_keys():
    return (
        app.config.get("TOSS_PAYMENTS_CLIENT_KEY", ""),
        app.config.get("TOSS_PAYMENTS_SECRET_KEY", ""),
    )


def has_real_toss_keys():
    client_key, secret_key = get_toss_keys()
    client_key = str(client_key or "").strip()
    secret_key = str(secret_key or "").strip()
    if not client_key or not secret_key:
        return False
    placeholder_values = {
        "test_ck_xxx",
        "test_sk_xxx",
        "test_ck_your_client_key",
        "test_sk_your_secret_key",
    }
    if client_key in placeholder_values or secret_key in placeholder_values:
        return False
    if "..." in client_key or "..." in secret_key:
        return False
    if len(client_key) < 20 or len(secret_key) < 20:
        return False
    return client_key.startswith("test_ck_") and secret_key.startswith("test_sk_")


def get_payment_mode():
    value = str(app.config.get(
        "TOSS_PAYMENTS_MOCK_ONLY", "auto")).strip().lower()
    keys_configured = has_real_toss_keys()

    if value in {"1", "true", "yes", "on"}:
        return {
            "mock_only": True,
            "mode_label": "테스트 모의 결제",
            "reason": "forced_mock",
            "keys_configured": keys_configured,
        }
    if value in {"0", "false", "no", "off"}:
        return {
            "mock_only": False,
            "mode_label": "토스 테스트 결제창" if keys_configured else "토스 테스트 결제창(키 필요)",
            "reason": "forced_live" if keys_configured else "forced_live_missing_keys",
            "keys_configured": keys_configured,
        }

    if keys_configured:
        return {
            "mock_only": False,
            "mode_label": "토스 테스트 결제창",
            "reason": "auto_live_keys_detected",
            "keys_configured": True,
        }
    return {
        "mock_only": True,
        "mode_label": "테스트 모의 결제",
        "reason": "auto_mock_missing_keys",
        "keys_configured": False,
    }


def is_toss_mock_only():
    return get_payment_mode()["mock_only"]


def build_toss_order_id(reservation):
    token_prefix = (reservation.payment_token or "notoken").replace(
        "_", "").replace("-", "")
    token_prefix = token_prefix[:12] if token_prefix else "notoken"
    return f"SCM-{reservation.id}-{token_prefix}"


def build_toss_order_name(_reservation, schedule, seat):
    train = db.session.get(
        Train, schedule.train_id) if schedule and schedule.train_id else None
    departure_station = db.session.get(
        Station, schedule.departure_station_id) if schedule else None
    arrival_station = db.session.get(
        Station, schedule.arrival_station_id) if schedule else None

    train_label = "SCMAGLEV"
    if train and train.train_number and train.name:
        train_label = f"{train.train_number} {train.name}"
    elif train and train.train_number:
        train_label = train.train_number

    section_label = "구간미정"
    if departure_station and arrival_station:
        section_label = f"{departure_station.name}-{arrival_station.name}"

    seat_label = "좌석미정"
    if seat:
        seat_label = f"{seat.car_number}호차 {seat.seat_number}번"

    departure_label = ""
    if schedule and schedule.departure_time:
        departure_label = schedule.departure_time.strftime("%m/%d %H:%M출발")

    order_parts = [train_label, section_label, seat_label]
    if departure_label:
        order_parts.append(departure_label)
    order_name = " | ".join(order_parts)
    return order_name[:100]


def reservation_is_payment_expired(reservation, now):
    return bool(reservation.payment_due_at and reservation.payment_due_at < now)


def mark_reservation_as_expired(reservation, user_id):
    now = now_utc_naive()
    reservation.status = "cancelled"
    reservation.payment_status = "expired"
    reservation.failed_at = now
    reservation.fail_reason = "결제 유효시간 만료"
    log_operation_event(
        event_type="PAYMENT_EXPIRED",
        message=f"결제 만료: 예약 {reservation.id}",
        severity="warning",
        source="payment",
        reservation_id=reservation.id,
        user_id=user_id,
    )


def confirm_toss_payment(payment_key, order_id, amount):
    _, secret_key = get_toss_keys()
    credentials = base64.b64encode(f"{secret_key}:".encode()).decode("utf-8")
    request_body = json.dumps(
        {
            "paymentKey": payment_key,
            "orderId": order_id,
            "amount": amount,
        }
    ).encode("utf-8")
    request_obj = urllib_request.Request(
        url="https://api.tosspayments.com/v1/payments/confirm",
        data=request_body,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request_obj, timeout=10) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload), None
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"message": body or "토스 결제 승인에 실패했습니다."}
        return None, parsed
    except urllib_error.URLError:
        return None, {"message": "토스 결제 서버와 통신할 수 없습니다."}


def seed_scmaglev_data():
    train_type_specs = [
        ("METRO", "SCMAGLEV 전철형", 250),
        ("HIGHSPEED", "SCMAGLEV 고속철도형", 500),
    ]
    for code, name, speed in train_type_specs:
        if not TrainType.query.filter_by(code=code).first():
            db.session.add(
                TrainType(code=code, name=name, max_speed_kmh=speed))
    db.session.flush()
    type_by_code = {row.code: row for row in TrainType.query.all()}

    station_specs = STATION_SPECS
    for code, name, lat, lng in station_specs:
        station = Station.query.filter_by(code=code).first()
        if not station:
            db.session.add(Station(code=code, name=name,
                           latitude=lat, longitude=lng))
        else:
            station.name = name
            station.latitude = lat
            station.longitude = lng
    db.session.flush()
    valid_station_codes = {code for code, *_ in station_specs}
    for station in Station.query.filter(~Station.code.in_(valid_station_codes)).all():
        station_id = station.id
        RouteStation.query.filter_by(station_id=station_id).delete(
            synchronize_session=False)
        Schedule.query.filter(
            or_(
                Schedule.departure_station_id == station_id,
                Schedule.arrival_station_id == station_id,
            )
        ).delete(synchronize_session=False)
        TrainLocation.query.filter_by(next_station_id=station_id).update(
            {TrainLocation.next_station_id: None},
            synchronize_session=False,
        )
        OperationEventLog.query.filter_by(station_id=station_id).update(
            {OperationEventLog.station_id: None},
            synchronize_session=False,
        )
        db.session.delete(station)
    db.session.flush()
    station_by_code = {row.code: row for row in Station.query.all()}

    route_specs = ROUTE_SPECS
    route_by_code = {}
    for route_code, route_name, station_codes in route_specs:
        route = Route.query.filter_by(code=route_code).first()
        if not route:
            route = Route(code=route_code, name=route_name)
            db.session.add(route)
            db.session.flush()
        route_by_code[route_code] = route
        for sequence, station_code in enumerate(station_codes, start=1):
            station = station_by_code.get(station_code)
            if not station:
                continue
            exists = RouteStation.query.filter_by(
                route_id=route.id,
                station_id=station.id,
                sequence=sequence,
            ).first()
            if not exists:
                db.session.add(
                    RouteStation(
                        route_id=route.id,
                        station_id=station.id,
                        sequence=sequence,
                    )
                )

    train_specs = list(iter_train_specs())

    now = now_utc_naive()
    for index, spec in enumerate(train_specs):
        (
            train_number,
            train_name,
            type_code,
            route_code,
            cars,
            departure_code,
            arrival_code,
            duration_minutes,
            offset_minutes,
        ) = spec
        train = Train.query.filter_by(train_number=train_number).first()
        if not train:
            train = Train(
                train_number=train_number,
                name=train_name,
                train_type_id=type_by_code[type_code].id,
                route_id=route_by_code[route_code].id,
                cars=cars,
                status="normal",
            )
            db.session.add(train)
            db.session.flush()

        seat_count = Seat.query.filter_by(train_id=train.id).count()
        if seat_count == 0:
            for car_number in range(1, train.cars + 1):
                for seat_index in range(1, 11):
                    db.session.add(
                        Seat(
                            train_id=train.id,
                            car_number=car_number,
                            seat_number=f"{seat_index:02d}",
                            seat_type="general",
                        )
                    )

        has_schedule = Schedule.query.filter_by(train_id=train.id).first()
        if not has_schedule:
            departure_station = station_by_code[departure_code]
            arrival_station = station_by_code[arrival_code]
            departure_time = now + \
                timedelta(minutes=offset_minutes + (index * 2))
            arrival_time = departure_time + timedelta(minutes=duration_minutes)
            db.session.add(
                Schedule(
                    train_id=train.id,
                    departure_station_id=departure_station.id,
                    arrival_station_id=arrival_station.id,
                    departure_time=departure_time,
                    arrival_time=arrival_time,
                )
            )
            if not TrainLocation.query.filter_by(train_id=train.id).first():
                db.session.add(
                    TrainLocation(
                        train_id=train.id,
                        latitude=departure_station.latitude,
                        longitude=departure_station.longitude,
                        speed_kmh=0,
                        direction_deg=0,
                        next_station_id=arrival_station.id,
                        eta_minutes=duration_minutes,
                        operation_status="normal",
                        congestion="medium",
                    )
                )

    db.session.commit()
    normalize_active_fault_trains()


def ensure_demo_fault_trains():
    normalize_active_fault_trains()


def seed_default_operator_account():
    username = os.getenv("CONTROLLER_USERNAME", "gygs1010")
    # CONTROLLER_PASSWORD가 명시적으로 설정된 경우에만 비밀번호를 강제 반영한다.
    # (이 값이 없다고 해서 매번 기본 비밀번호로 되돌리면, 관제 계정이
    #  /api/users/me 로 직접 바꾼 비밀번호가 서버 재시작마다 초기화돼버린다.)
    password_override = os.getenv("CONTROLLER_PASSWORD")
    email = os.getenv("CONTROLLER_EMAIL",
                      "gygs1010@scmaglev.local").strip().lower()

    user = User.query.filter_by(username=username).first()
    if user:
        if password_override:
            user.password = generate_password_hash(password_override)
        user.role = "controller"
        if user.email != email:
            email_owner = User.query.filter_by(email=email).first()
            if not email_owner or email_owner.id == user.id:
                user.email = email
    else:
        password = password_override or "zxc123123"
        hashed_password = generate_password_hash(password)
        existing_email = User.query.filter_by(email=email).first()
        if existing_email:
            email = f"{username}@scmaglev.local"
        db.session.add(
            User(
                username=username,
                email=email,
                password=hashed_password,
                role="controller",
            )
        )
    db.session.commit()


def ensure_role_column_for_existing_database():
    inspector = inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "role" not in columns:
        db.session.execute(
            text(
                "ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'passenger' NOT NULL")
        )
        db.session.commit()


def ensure_train_location_fault_columns():
    inspector = inspect(db.engine)
    if "train_locations" not in inspector.get_table_names():
        return
    columns = {column["name"]
               for column in inspector.get_columns("train_locations")}
    alter_statements = []
    if "fault_cause_code" not in columns:
        alter_statements.append(
            "ALTER TABLE train_locations ADD COLUMN fault_cause_code VARCHAR(40)")
    if "fault_cause_label" not in columns:
        alter_statements.append(
            "ALTER TABLE train_locations ADD COLUMN fault_cause_label VARCHAR(80)")
    if "fault_detail" not in columns:
        alter_statements.append(
            "ALTER TABLE train_locations ADD COLUMN fault_detail VARCHAR(255)")
    for statement in alter_statements:
        db.session.execute(text(statement))
    if alter_statements:
        db.session.commit()


def ensure_reservation_columns_for_existing_database():
    inspector = inspect(db.engine)
    if "reservations" not in inspector.get_table_names():
        return
    columns = {column["name"]
               for column in inspector.get_columns("reservations")}
    alter_statements = []
    if "payment_token" not in columns:
        alter_statements.append(
            "ALTER TABLE reservations ADD COLUMN payment_token VARCHAR(64)")
    if "payment_due_at" not in columns:
        alter_statements.append(
            "ALTER TABLE reservations ADD COLUMN payment_due_at DATETIME")
    if "paid_at" not in columns:
        alter_statements.append(
            "ALTER TABLE reservations ADD COLUMN paid_at DATETIME")
    if "failed_at" not in columns:
        alter_statements.append(
            "ALTER TABLE reservations ADD COLUMN failed_at DATETIME")
    if "fail_reason" not in columns:
        alter_statements.append(
            "ALTER TABLE reservations ADD COLUMN fail_reason VARCHAR(255)")
    for statement in alter_statements:
        db.session.execute(text(statement))
    if alter_statements:
        db.session.commit()

    db.session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ux_reservation_active_seat "
            "ON reservations (schedule_id, seat_id) "
            "WHERE status IN ('pending_payment', 'booked')"
        )
    )
    db.session.commit()


def log_operation_event(
    event_type,
    message,
    *,
    severity="info",
    source="system",
    train_id=None,
    station_id=None,
    reservation_id=None,
    user_id=None,
    payload=None,
):
    event = OperationEventLog(
        event_type=event_type,
        severity=severity,
        source=source,
        train_id=train_id,
        station_id=station_id,
        reservation_id=reservation_id,
        user_id=user_id,
        message=message,
        payload_json=json.dumps(
            payload, ensure_ascii=False) if payload else None,
    )
    db.session.add(event)
    flush_with_sqlite_retry()
    return event


def has_unacked_alarm(key):
    """동일 알람 키의 미ACK 이벤트가 있으면 중복 생성하지 않습니다."""
    rows = (
        OperationEventLog.query.filter(
            OperationEventLog.is_acknowledged.is_(False),
            OperationEventLog.event_type == "ALARM",
        )
        .order_by(OperationEventLog.created_at.desc())
        .limit(80)
        .all()
    )
    for row in rows:
        if not row.payload_json:
            continue
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            continue
        if payload.get("alarm_key") == key:
            return True
    return False


def apply_event_group_filter(query, event_group):
    group = str(event_group or "").strip().lower()
    if group == "control":
        return query.filter(OperationEventLog.event_type.like("CONTROL_%"))
    if group == "alarm":
        return query.filter(OperationEventLog.event_type.like("ALARM%"))
    if group == "payment":
        return query.filter(OperationEventLog.event_type.like("PAYMENT_%"))
    if group == "vehicle":
        return query.filter(OperationEventLog.event_type.like("VEHICLE_%"))
    return query


def maybe_emit_alarm(key, message, *, severity="warning", payload=None, train_id=None):
    now_ts = time.time()
    last_ts = alarm_last_emitted_at.get(key, 0.0)
    if now_ts - last_ts < ALARM_COOLDOWN_SECONDS:
        return
    if has_unacked_alarm(key):
        return
    alarm_last_emitted_at[key] = now_ts
    alarm_payload = dict(payload or {})
    alarm_payload["alarm_key"] = key
    log_operation_event(
        event_type="ALARM",
        message=message,
        severity=severity,
        source="alarm_rule",
        train_id=train_id,
        payload=alarm_payload,
    )


def require_roles(*allowed_roles):
    def decorator(view):
        @wraps(view)
        @jwt_required()
        def wrapped(*args, **kwargs):
            user_id = int(get_jwt_identity())
            user = db.session.get(User, user_id)
            if not user:
                return jsonify({"message": "사용자를 찾을 수 없습니다."}), 404
            if user_role_or_default(user) not in allowed_roles:
                return jsonify({"message": "관제 권한이 없습니다."}), 403
            return view(*args, **kwargs)

        wrapped.__name__ = view.__name__
        return wrapped

    return decorator


def serialize_train_fault(location):
    if not location or not getattr(location, "fault_cause_code", None):
        return None
    return {
        "code": location.fault_cause_code,
        "label": location.fault_cause_label,
        "detail": location.fault_detail,
    }


def count_active_fault_trains():
    return TrainLocation.query.filter(
        TrainLocation.fault_cause_code.isnot(None)
    ).count()


def _reset_train_fault_state(location, train):
    location.fault_cause_code = None
    location.fault_cause_label = None
    location.fault_detail = None
    location.operation_status = "normal"
    train_fault_recovery_at.pop(train.id, None)
    train_fault_last_logged.pop(train.id, None)


def normalize_active_fault_trains(max_active=None):
    """시연용 고장 열차만 남기고 나머지 지연·장애 상태를 정상화합니다."""
    limit = max_active or MAX_ACTIVE_FAULT_TRAINS
    now = now_utc_naive()
    demo_pairs = list(DEMO_FAULT_SPECS[:limit])
    demo_numbers = {train_number for train_number, _ in demo_pairs}

    for location in TrainLocation.query.filter(
        TrainLocation.fault_cause_code.isnot(None)
    ).all():
        train = db.session.get(Train, location.train_id)
        if not train or train.train_number in demo_numbers:
            continue
        _reset_train_fault_state(location, train)

    for location in TrainLocation.query.filter(
        TrainLocation.operation_status.in_(
            ("delayed", "stopped", "disrupted")),
        TrainLocation.fault_cause_code.is_(None),
    ).all():
        train = db.session.get(Train, location.train_id)
        if train:
            _reset_train_fault_state(location, train)
        else:
            location.operation_status = "normal"

    for train_number, cause_code in demo_pairs:
        train = Train.query.filter_by(train_number=train_number).first()
        cause = TRAIN_FAULT_BY_CODE.get(cause_code)
        if not train or not cause:
            continue
        location = TrainLocation.query.filter_by(train_id=train.id).first()
        if not location:
            continue
        if location.fault_cause_code == cause_code:
            location.operation_status = cause["status"]
            continue
        assign_train_fault(location, train, cause, now)

    db.session.commit()


def assign_train_fault(location, train, cause, current_time):
    location.operation_status = cause["status"]
    location.fault_cause_code = cause["code"]
    location.fault_cause_label = cause["label"]
    location.fault_detail = cause["detail"]
    location.speed_kmh = max(
        0,
        round(float(location.speed_kmh or 0) *
              float(cause.get("speed_factor", 0.3)), 1),
    )
    recovery_minutes = FAULT_AUTO_RECOVERY_MINUTES
    train_fault_recovery_at[train.id] = current_time + \
        timedelta(minutes=recovery_minutes)
    if train_fault_last_logged.get(train.id) != cause["code"]:
        log_operation_event(
            event_type="VEHICLE_FAULT",
            message=f"{train.train_number} {cause['label']} 고장: {cause['detail']}",
            severity="critical" if cause["status"] == "disrupted" else "warning",
            source="vehicle_monitor",
            train_id=train.id,
            payload={
                "cause_code": cause["code"],
                "cause_label": cause["label"],
                "detail": cause["detail"],
                "operation_status": cause["status"],
            },
        )
        train_fault_last_logged[train.id] = cause["code"]


def clear_train_fault(location, train, _current_time):
    previous_label = location.fault_cause_label or location.fault_cause_code or "고장"
    location.fault_cause_code = None
    location.fault_cause_label = None
    location.fault_detail = None
    location.operation_status = "normal"
    location.speed_kmh = 220 if train.train_number.startswith("SM-5") else 130
    train_fault_recovery_at.pop(train.id, None)
    train_fault_last_logged.pop(train.id, None)
    log_operation_event(
        event_type="VEHICLE_FAULT_RECOVERED",
        message=f"{train.train_number} {previous_label} 고장 복구 완료",
        severity="info",
        source="vehicle_monitor",
        train_id=train.id,
        payload={"recovered_cause": previous_label},
    )


def apply_train_fault_simulation(location, train, current_time):
    if location.operation_status == "arrived":
        location.fault_cause_code = None
        location.fault_cause_label = None
        location.fault_detail = None
        train_fault_recovery_at.pop(train.id, None)
        train_fault_last_logged.pop(train.id, None)
        return

    recovery_at = train_fault_recovery_at.get(train.id)
    if location.fault_cause_code and recovery_at is None:
        train_fault_recovery_at[train.id] = current_time + \
            timedelta(minutes=FAULT_AUTO_RECOVERY_MINUTES)
        recovery_at = train_fault_recovery_at[train.id]
    if location.fault_cause_code and recovery_at and current_time >= recovery_at:
        clear_train_fault(location, train, current_time)
        return

    if location.fault_cause_code:
        catalog = TRAIN_FAULT_BY_CODE.get(location.fault_cause_code)
        if catalog:
            location.operation_status = catalog["status"]
            location.speed_kmh = max(
                0,
                round(float(location.speed_kmh or 0) *
                      float(catalog.get("speed_factor", 0.3)), 1),
            )
        return

    if FAULT_RANDOM_CHANCE <= 0:
        return
    if count_active_fault_trains() >= MAX_ACTIVE_FAULT_TRAINS:
        return
    if random.random() > FAULT_RANDOM_CHANCE:
        return
    assign_train_fault(
        location, train, pick_random_train_fault(), current_time)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * \
        math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * \
        math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def resolve_train_service_phase(schedule, current_time):
    """일정 시각 기준 열차 단계: waiting | in_service | arrived."""
    if current_time < schedule.departure_time:
        return "waiting"
    if current_time >= schedule.arrival_time:
        return "arrived"
    return "in_service"


def _update_train_location(location):
    train = db.session.get(Train, location.train_id)
    if not train:
        return None
    train_type = db.session.get(
        TrainType, train.train_type_id) if train.train_type_id else None
    schedule = (
        Schedule.query.filter_by(train_id=train.id)
        .order_by(Schedule.departure_time.desc())
        .first()
    )
    if not schedule:
        return None
    departure_station = db.session.get(Station, schedule.departure_station_id)
    arrival_station = db.session.get(Station, schedule.arrival_station_id)
    if not departure_station or not arrival_station:
        return None

    current_time = now_utc_naive()
    refresh_schedule_for_service(schedule, current_time)
    total_seconds = max(
        (schedule.arrival_time - schedule.departure_time).total_seconds(), 1)
    elapsed_seconds = (current_time - schedule.departure_time).total_seconds()
    if current_time < schedule.departure_time:
        schedule_progress = 0.0
        location.operation_status = "normal"
    elif current_time >= schedule.arrival_time:
        schedule_progress = 1.0
        location.operation_status = "arrived"
    else:
        schedule_progress = min(max(elapsed_seconds / total_seconds, 0.0), 1.0)
        location.operation_status = "normal"

    service_phase = resolve_train_service_phase(schedule, current_time)
    is_in_service = service_phase == "in_service"
    is_waiting_departure = service_phase == "waiting"
    if is_in_service:
        map_progress = schedule_progress
    elif service_phase == "waiting":
        map_progress = 0.0
    else:
        map_progress = 1.0

    location.latitude = departure_station.latitude + (
        arrival_station.latitude - departure_station.latitude
    ) * map_progress
    location.longitude = departure_station.longitude + (
        arrival_station.longitude - departure_station.longitude
    ) * map_progress
    if service_phase == "waiting":
        remaining_minutes = max(
            int((schedule.departure_time - current_time).total_seconds() // 60), 0)
    elif service_phase == "arrived":
        remaining_minutes = 0
    else:
        remaining_minutes = max(
            int((schedule.arrival_time - current_time).total_seconds() // 60), 0)
    location.eta_minutes = remaining_minutes

    distance_km = _haversine_km(
        departure_station.latitude,
        departure_station.longitude,
        arrival_station.latitude,
        arrival_station.longitude,
    )
    duration_hours = total_seconds / 3600
    cruise_speed = distance_km / duration_hours if duration_hours > 0 else 0.0
    if _is_direct_train_number(train.train_number) or str(train.train_number).startswith("SM-5"):
        type_cap = 500.0
    else:
        type_cap = 250.0
    if service_phase == "arrived":
        location.speed_kmh = 0.0
    elif is_in_service:
        location.speed_kmh = round(min(cruise_speed, type_cap), 1)
    else:
        location.speed_kmh = 0.0

    location.direction_deg = _bearing_deg(
        departure_station.latitude,
        departure_station.longitude,
        arrival_station.latitude,
        arrival_station.longitude,
    )
    apply_train_fault_simulation(location, train, current_time)
    route = db.session.get(Route, train.route_id) if train.route_id else None
    is_direct = _is_direct_train_number(train.train_number) or (
        route is not None and route.code == DIRECT_ROUTE_CODE
    )
    prediction = predict_congestion(
        hour=current_time.hour,
        route_name=route.name if route else "",
        recent_passengers=random.randint(120, 1200),
        is_event_day=False,
        model_type="ml",
    )
    location.congestion = prediction["label"]

    payload = {
        "train_id": train.id,
        "train_number": train.train_number,
        "train_name": train.name,
        "train_type": train_type.name if train_type else None,
        "train_type_code": train_type.code if train_type else None,
        "route_id": train.route_id,
        "route_name": "전국 직통" if is_direct else (route.name if route else None),
        "is_direct": is_direct,
        "departure_station": departure_station.name,
        "arrival_station": arrival_station.name,
        "latitude": round(location.latitude, 6),
        "longitude": round(location.longitude, 6),
        "speed_kmh": location.speed_kmh,
        "direction_deg": location.direction_deg,
        "eta_minutes": location.eta_minutes,
        "next_station": arrival_station.name,
        "operation_status": location.operation_status,
        "congestion": location.congestion,
        "fault": serialize_train_fault(location),
        "updated_at": current_time.isoformat(),
        "schedule_departure_time": schedule.departure_time.isoformat(),
        "schedule_arrival_time": schedule.arrival_time.isoformat(),
        "departure_latitude": departure_station.latitude,
        "departure_longitude": departure_station.longitude,
        "arrival_latitude": arrival_station.latitude,
        "arrival_longitude": arrival_station.longitude,
        "progress": round(map_progress, 6),
        "schedule_progress": round(schedule_progress, 6),
        "is_in_service": is_in_service,
        "is_waiting_departure": is_waiting_departure,
        "service_phase": service_phase,
        "duration_minutes": int(total_seconds // 60),
    }
    train_history[train.id].append(
        {
            "timestamp": current_time.isoformat(),
            "speed_kmh": location.speed_kmh,
            "eta_minutes": location.eta_minutes,
            "congestion": location.congestion,
            "latitude": payload["latitude"],
            "longitude": payload["longitude"],
        }
    )
    return payload


def _broadcast_train_locations(payload):
    if socketio is not None:
        socketio.emit("train_locations", {"trains": payload})


def run_location_simulator():
    while True:
        with app.app_context():
            locations = TrainLocation.query.all()
            payload = []
            for location in locations:
                row = _update_train_location(location)
                if row:
                    payload.append(row)
            db.session.commit()
            if payload:
                _broadcast_train_locations(payload)
        time.sleep(SIMULATION_INTERVAL_SECONDS)


def start_location_simulator():
    global _simulator_started
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    db_url = os.getenv("DATABASE_URL", app.config.get(
        "SQLALCHEMY_DATABASE_URI", "")).rstrip("/")
    if db_url in ("sqlite:", "sqlite://"):
        return
    with _simulator_lock:
        if _simulator_started:
            return
        thread = threading.Thread(target=run_location_simulator, daemon=True)
        thread.start()
        _simulator_started = True


@app.route("/api/scmaglev/stations", methods=["GET"])
def scmaglev_stations():
    stations = Station.query.order_by(Station.name.asc()).all()
    return jsonify({"stations": [serialize_station(station) for station in stations]})


@app.route("/api/scmaglev/routes", methods=["GET"])
def scmaglev_routes():
    routes = Route.query.order_by(Route.id.asc()).all()
    data = []
    for route in routes:
        route_stations = (
            RouteStation.query.filter_by(route_id=route.id)
            .order_by(RouteStation.sequence.asc())
            .all()
        )
        stations = []
        for route_station in route_stations:
            station = db.session.get(Station, route_station.station_id)
            if station:
                stations.append(
                    {
                        "id": station.id,
                        "code": station.code,
                        "name": station.name,
                        "sequence": route_station.sequence,
                    }
                )
        data.append({"id": route.id, "code": route.code,
                    "name": route.name, "stations": stations})
    return jsonify({"routes": data})


DIRECT_ROUTE_CODE = "R-DIRECT-NATIONAL"


def _get_or_create_direct_route():
    route = Route.query.filter_by(code=DIRECT_ROUTE_CODE).first()
    if route:
        return route
    route = Route(code=DIRECT_ROUTE_CODE, name="전국 직통")
    db.session.add(route)
    db.session.flush()
    return route


def _estimate_direct_duration_minutes(departure_station, arrival_station):
    station_coords = {
        station.code: (station.latitude, station.longitude)
        for station in Station.query.all()
    }
    return _estimate_duration_minutes(
        station_coords,
        departure_station.code,
        arrival_station.code,
        "HIGHSPEED",
    )


def _is_direct_train_number(train_number):
    return str(train_number or "").startswith("SM-D-")


def _upsert_direct_train_pair(
    departure_station,
    arrival_station,
    now,
    *,
    with_location=False,
    departure_after_time=None,
):
    train_type = TrainType.query.filter_by(code="HIGHSPEED").first()
    if not train_type:
        return None

    route = _get_or_create_direct_route()
    train_number = f"SM-D-{departure_station.code}-{arrival_station.code}"
    train = Train.query.filter_by(train_number=train_number).first()
    duration_minutes = _estimate_direct_duration_minutes(
        departure_station, arrival_station)
    phase_minutes = _direct_schedule_phase_minutes(train_number)
    window_minutes = _direct_service_window_minutes()

    if not train:
        train = Train(
            train_number=train_number,
            name=f"{departure_station.name}→{arrival_station.name} 직통",
            train_type_id=train_type.id,
            route_id=route.id,
            cars=10,
            status="normal",
        )
        db.session.add(train)
        db.session.flush()
        if not Seat.query.filter_by(train_id=train.id).first():
            for car_number in range(1, train.cars + 1):
                for seat_index in range(1, 11):
                    db.session.add(
                        Seat(
                            train_id=train.id,
                            car_number=car_number,
                            seat_number=f"{seat_index:02d}",
                            seat_type="general",
                        )
                    )

    schedule = Schedule.query.filter_by(train_id=train.id).first()
    if not schedule:
        departure_time = now - \
            timedelta(minutes=window_minutes - phase_minutes)
        if departure_after_time and departure_time < departure_after_time:
            while departure_time < departure_after_time:
                departure_time += timedelta(
                    minutes=max(window_minutes // 2, 30))
        schedule = Schedule(
            train_id=train.id,
            departure_station_id=departure_station.id,
            arrival_station_id=arrival_station.id,
            departure_time=departure_time,
            arrival_time=departure_time + timedelta(minutes=duration_minutes),
        )
        db.session.add(schedule)
    else:
        refresh_schedule_for_service(schedule, now)
        if departure_after_time and schedule.departure_time < departure_after_time:
            while schedule.departure_time < departure_after_time:
                schedule.departure_time += timedelta(days=1)
                schedule.arrival_time = schedule.departure_time + timedelta(
                    minutes=duration_minutes
                )

    if with_location and not TrainLocation.query.filter_by(train_id=train.id).first():
        db.session.add(
            TrainLocation(
                train_id=train.id,
                latitude=departure_station.latitude,
                longitude=departure_station.longitude,
                speed_kmh=0,
                direction_deg=0,
                next_station_id=arrival_station.id,
                eta_minutes=duration_minutes,
                operation_status="normal",
                congestion="medium",
            )
        )

    return schedule


def _direct_train_endpoint_codes(train_number):
    parts = str(train_number or "").split("-")
    if len(parts) < 4 or parts[0] != "SM" or parts[1] != "D":
        return None, None
    return parts[2], parts[3]


def _is_hub_direct_train(train_number):
    departure_code, arrival_code = _direct_train_endpoint_codes(train_number)
    if not departure_code or not arrival_code:
        return False
    return (
        departure_code in DIRECT_DASHBOARD_HUB_CODES
        or arrival_code in DIRECT_DASHBOARD_HUB_CODES
    )


def select_dashboard_tracked_train_ids():
    """대시보드 지도에 표시할 열차 ID를 우선순위·상한으로 선정합니다."""
    selected = []
    seen = set()

    route_trains = (
        Train.query.filter(~Train.train_number.like("SM-D-%"))
        .order_by(Train.train_number.asc())
        .all()
    )
    for train in route_trains:
        if train.id in seen:
            continue
        seen.add(train.id)
        selected.append(train.id)
        if len(selected) >= MAX_DASHBOARD_TRACKED_TRAINS:
            return selected

    direct_trains = (
        Train.query.filter(Train.train_number.like("SM-D-%"))
        .order_by(Train.train_number.asc())
        .all()
    )
    hub_direct = [
        train for train in direct_trains if _is_hub_direct_train(train.train_number)]
    other_direct = [train for train in direct_trains if not _is_hub_direct_train(
        train.train_number)]
    for train in hub_direct + other_direct:
        if train.id in seen:
            continue
        seen.add(train.id)
        selected.append(train.id)
        if len(selected) >= MAX_DASHBOARD_TRACKED_TRAINS:
            break
    return selected


def _add_train_location_if_missing(train, schedule):
    if TrainLocation.query.filter_by(train_id=train.id).first():
        return False
    departure_station = db.session.get(Station, schedule.departure_station_id)
    arrival_station = db.session.get(Station, schedule.arrival_station_id)
    if not departure_station or not arrival_station:
        return False
    db.session.add(
        TrainLocation(
            train_id=train.id,
            latitude=departure_station.latitude,
            longitude=departure_station.longitude,
            speed_kmh=0,
            direction_deg=0,
            next_station_id=arrival_station.id,
            eta_minutes=max(
                int((schedule.arrival_time -
                    schedule.departure_time).total_seconds() // 60),
                1,
            ),
            operation_status="normal",
            congestion="medium",
        )
    )
    return True


def seed_direct_hub_pairs():
    """주요 허브 간 직통 편성만 미리 준비합니다(대시보드·검색용)."""
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return

    stations = {station.code: station for station in Station.query.all()}
    hub_stations = [
        stations[code]
        for code in DIRECT_DASHBOARD_HUB_CODES
        if code in stations
    ]
    if len(hub_stations) < 2:
        return

    now = now_utc_naive()
    created = 0
    for departure_station in hub_stations:
        for arrival_station in hub_stations:
            if departure_station.id == arrival_station.id:
                continue
            train_number = f"SM-D-{departure_station.code}-{arrival_station.code}"
            if Train.query.filter_by(train_number=train_number).first():
                continue
            _upsert_direct_train_pair(
                departure_station,
                arrival_station,
                now,
                with_location=False,
            )
            created += 1

    if created:
        commit_with_sqlite_retry()
        print(f"[SCMAGLEV] 허브 직통 편성 시드: {created}건", flush=True)


def seed_direct_national_trains():
    """직통 편성 시드 — 기본은 허브 노선만, 전체 전국 편성은 환경변수로만 허용."""
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return

    seed_all = os.getenv("SCMAGLEV_SEED_ALL_DIRECT", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not seed_all:
        seed_direct_hub_pairs()
        return

    print("[SCMAGLEV] 전국 직통 편성 시드 준비 중...", flush=True)
    stations = Station.query.order_by(Station.code.asc()).all()
    station_count = len(stations)
    if station_count < 2:
        return

    expected_pairs = station_count * (station_count - 1)
    existing_direct = Train.query.filter(
        Train.train_number.like("SM-D-%")).count()
    if existing_direct >= expected_pairs:
        return

    now = now_utc_naive()
    created = 0
    for departure_station in stations:
        for arrival_station in stations:
            if departure_station.id == arrival_station.id:
                continue
            train_number = f"SM-D-{departure_station.code}-{arrival_station.code}"
            if Train.query.filter_by(train_number=train_number).first():
                continue
            _upsert_direct_train_pair(
                departure_station,
                arrival_station,
                now,
                with_location=False,
            )
            created += 1
            if created % 250 == 0:
                commit_with_sqlite_retry()
                print(
                    f"[SCMAGLEV] 직통 편성 시드 진행: {created}/{expected_pairs}",
                    flush=True,
                )

    if created:
        commit_with_sqlite_retry()
        print(f"[SCMAGLEV] 직통 편성 시드 완료: {created}건", flush=True)


def prune_dashboard_tracking():
    """대시보드 지도 추적 대상을 상한 이내로 줄입니다."""
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return 0

    keep_ids = set(select_dashboard_tracked_train_ids())
    removed = 0
    for location in TrainLocation.query.all():
        if location.train_id in keep_ids:
            continue
        db.session.delete(location)
        removed += 1

    if removed:
        commit_with_sqlite_retry()
        print(
            f"[SCMAGLEV] 대시보드 추적 축소: {removed}대 제거 · {len(keep_ids)}대 유지",
            flush=True,
        )
    return removed


def ensure_direct_train_locations():
    """대시보드에 표시할 열차만 위치 추적 레코드를 준비합니다."""
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return 0

    keep_ids = select_dashboard_tracked_train_ids()
    created = 0
    for train_id in keep_ids:
        train = db.session.get(Train, train_id)
        if not train:
            continue
        schedule = Schedule.query.filter_by(train_id=train.id).first()
        if not schedule:
            continue
        if _add_train_location_if_missing(train, schedule):
            created += 1

    if created:
        commit_with_sqlite_retry()
        print(f"[SCMAGLEV] 대시보드 추적 추가: {created}대", flush=True)
    return created


def bootstrap_tracked_fleet_live_ops():
    """지도 추적 중인 전체 편성 일정을 롤링 운행 윈도우에 맞춰 재배치합니다."""
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return

    current_time = now_utc_naive()
    realigned = 0
    for location in TrainLocation.query.all():
        train = db.session.get(Train, location.train_id)
        if not train:
            continue
        schedule = Schedule.query.filter_by(train_id=train.id).first()
        if not schedule:
            continue
        if align_schedule_for_live_service(schedule, train, current_time):
            realigned += 1
    if realigned:
        commit_with_sqlite_retry()
        print(
            f"[SCMAGLEV] 추적 편성 운행 시각 재배치: {realigned}편 "
            f"(롤링 {DIRECT_SERVICE_WINDOW_HOURS}시간 · 상한 {MAX_DASHBOARD_TRACKED_TRAINS}대)",
            flush=True,
        )


def bootstrap_direct_fleet_live_ops():
    bootstrap_tracked_fleet_live_ops()


def ensure_direct_train_schedule(departure_name, arrival_name, departure_after_time=None):
    """역 간 직통 SCMAGLEV 열차·일정을 없으면 생성합니다."""
    departure_station = Station.query.filter_by(name=departure_name).first()
    arrival_station = Station.query.filter_by(name=arrival_name).first()
    if not departure_station or not arrival_station:
        return None
    if departure_station.id == arrival_station.id:
        return None

    now = now_utc_naive()
    schedule = _upsert_direct_train_pair(
        departure_station,
        arrival_station,
        now,
        with_location=True,
        departure_after_time=departure_after_time,
    )
    db.session.commit()
    return schedule


def _schedule_row_is_active(schedule, _train_type, departure_after_time, now):
    if departure_after_time and schedule.departure_time < departure_after_time:
        return False
    return schedule.arrival_time >= now - timedelta(hours=1)


def _collect_schedule_rows(schedules, now):
    schedule_shifted = False
    rows = []
    for schedule in schedules:
        if refresh_schedule_for_service(schedule, now):
            schedule_shifted = True
        train = db.session.get(Train, schedule.train_id)
        if not train:
            continue
        departure_station = db.session.get(
            Station, schedule.departure_station_id)
        arrival_station = db.session.get(Station, schedule.arrival_station_id)
        if not departure_station or not arrival_station:
            continue
        train_type = db.session.get(TrainType, train.train_type_id)
        rows.append(
            {
                "schedule": schedule,
                "train_type": train_type,
                "departure_station": departure_station,
                "arrival_station": arrival_station,
            }
        )
    return rows, schedule_shifted


def _serialize_schedule_result(row):
    schedule_data = serialize_schedule(row["schedule"])
    schedule_data["estimated_fare"] = calculate_estimated_fare(row["schedule"])
    return schedule_data


def _search_train_schedules(
    departure_name="",
    arrival_name="",
    train_type_code="",
    departure_after_time=None,
    sort_by="departure",
):
    schedules = Schedule.query.order_by(Schedule.departure_time.asc()).all()
    now = now_utc_naive()
    rows, schedule_shifted = _collect_schedule_rows(schedules, now)

    def collect_direct_rows(type_code):
        direct = []
        for row in rows:
            schedule = row["schedule"]
            departure_station = row["departure_station"]
            arrival_station = row["arrival_station"]
            train_type = row["train_type"]
            if departure_name and departure_station.name != departure_name:
                continue
            if arrival_name and arrival_station.name != arrival_name:
                continue
            if type_code and (not train_type or train_type.code != type_code):
                continue
            if not _schedule_row_is_active(schedule, train_type, departure_after_time, now):
                continue
            direct.append(_serialize_schedule_result(row))
        return direct

    results = collect_direct_rows(train_type_code)
    type_relaxed = False
    if not results and train_type_code and departure_name and arrival_name:
        results = collect_direct_rows("")
        type_relaxed = bool(results)

    search_mode = "direct"
    if not results and departure_name and arrival_name:
        created = ensure_direct_train_schedule(
            departure_name,
            arrival_name,
            departure_after_time,
        )
        if created:
            schedules = Schedule.query.order_by(
                Schedule.departure_time.asc()).all()
            rows, extra_shifted = _collect_schedule_rows(schedules, now)
            schedule_shifted = schedule_shifted or extra_shifted
            results = collect_direct_rows(train_type_code)
            if not results and train_type_code:
                results = collect_direct_rows("")
                type_relaxed = bool(results)

    if schedule_shifted:
        db.session.commit()

    if sort_by == "fare":
        results.sort(key=lambda row: row.get("estimated_fare", 0))
    elif sort_by == "duration":
        results.sort(key=lambda row: row.get("duration_minutes", 0))
    else:
        results.sort(key=lambda row: row.get("departure_time", ""))

    if type_relaxed:
        for row in results:
            row["type_filter_relaxed"] = True
    return results, search_mode


@app.route("/api/scmaglev/trains/search", methods=["GET"])
def scmaglev_search_trains():
    departure_name = request.args.get("departure", "").strip()
    arrival_name = request.args.get("arrival", "").strip()
    train_type_code = request.args.get("train_type", "").strip().upper()
    departure_after = request.args.get("departure_after", "").strip()
    departure_date = request.args.get("departure_date", "").strip()
    sort_by = request.args.get("sort_by", "departure").strip().lower()
    departure_after_time = parse_departure_after_filter(
        departure_date or departure_after)

    results, search_mode = _search_train_schedules(
        departure_name=departure_name,
        arrival_name=arrival_name,
        train_type_code=train_type_code,
        departure_after_time=departure_after_time,
        sort_by=sort_by,
    )
    return jsonify({"schedules": results, "search_mode": search_mode})


@app.route("/api/scmaglev/schedules/<int:schedule_id>/seats", methods=["GET"])
def scmaglev_schedule_seats(schedule_id):
    schedule = db.session.get(Schedule, schedule_id)
    if not schedule:
        return jsonify({"message": "운행 일정을 찾을 수 없습니다."}), 404

    now = now_utc_naive()
    reserved_seat_ids = {
        row.seat_id
        for row in Reservation.query.filter(
            Reservation.schedule_id == schedule_id,
            or_(
                Reservation.status == "booked",
                (Reservation.status == "pending_payment") & (
                    Reservation.payment_due_at >= now),
            ),
        ).all()
    }

    seats = (
        Seat.query.filter_by(train_id=schedule.train_id, is_active=True)
        .order_by(Seat.car_number.asc(), Seat.seat_number.asc())
        .all()
    )
    seat_type = request.args.get("seat_type", "general").strip().lower()
    recommendations = build_seat_recommendations(seats, reserved_seat_ids)
    return jsonify(
        {
            "schedule_id": schedule_id,
            "estimated_fare": calculate_estimated_fare(schedule, seat_type=seat_type),
            "seats": [
                {
                    "id": seat.id,
                    "car_number": seat.car_number,
                    "seat_number": seat.seat_number,
                    "seat_type": seat.seat_type,
                    "is_reserved": seat.id in reserved_seat_ids,
                }
                for seat in seats
            ],
            "recommended_seats": recommendations,
        }
    )


@app.route("/api/scmaglev/reservations", methods=["POST"])
@jwt_required()
def scmaglev_create_reservation():
    user_id = int(get_jwt_identity())
    data = request.get_json() or {}
    schedule_id = data.get("schedule_id")
    seat_id = data.get("seat_id")
    seat_type = str(data.get("seat_type", "general")).strip().lower()

    if not schedule_id or not seat_id:
        return jsonify({"message": "schedule_id와 seat_id를 입력해주세요."}), 400

    schedule = db.session.get(Schedule, schedule_id)
    seat = db.session.get(Seat, seat_id)
    if not schedule or not seat:
        return jsonify({"message": "일정 또는 좌석 정보를 찾을 수 없습니다."}), 404
    if seat.train_id != schedule.train_id:
        return jsonify({"message": "선택한 좌석은 해당 일정의 열차 좌석이 아닙니다."}), 400
    if seat_type not in {"general", "senior", "pregnant", "accessible", "wheelchair"}:
        return jsonify({"message": "지원하지 않는 좌석 타입입니다."}), 400

    now = now_utc_naive()
    existing = Reservation.query.filter(
        Reservation.schedule_id == schedule_id,
        Reservation.seat_id == seat_id,
        or_(
            Reservation.status == "booked",
            (Reservation.status == "pending_payment") & (
                Reservation.payment_due_at >= now),
        ),
    ).first()
    if existing:
        return jsonify({"message": "이미 예매된 좌석입니다."}), 409

    payment_token = secrets.token_urlsafe(16)

    reservation = Reservation(
        user_id=user_id,
        schedule_id=schedule_id,
        seat_id=seat_id,
        status="pending_payment",
        payment_status="pending",
        payment_token=payment_token,
        payment_due_at=now + timedelta(minutes=PAYMENT_TIMEOUT_MINUTES),
    )
    db.session.add(reservation)
    try:
        db.session.flush()
        log_operation_event(
            event_type="RESERVATION_CREATED",
            message=f"예약 생성: {reservation.id}",
            severity="info",
            source="passenger",
            train_id=schedule.train_id,
            reservation_id=reservation.id,
            user_id=user_id,
            payload={
                "schedule_id": schedule_id,
                "seat_id": seat_id,
                "seat_type": seat_type,
                "estimated_fare": calculate_estimated_fare(schedule, seat_type=seat_type),
            },
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"message": "이미 예매된 좌석입니다."}), 409

    return jsonify(
        {
            "message": "예매가 접수되었습니다. 결제를 완료해주세요.",
            "reservation": {
                "id": reservation.id,
                "schedule_id": reservation.schedule_id,
                "seat_id": reservation.seat_id,
                "estimated_fare": calculate_estimated_fare(schedule, seat_type=seat_type),
                "status": reservation.status,
                "payment_status": reservation.payment_status,
                "payment_token": reservation.payment_token,
                "payment_due_at": reservation.payment_due_at.isoformat() if reservation.payment_due_at else None,
            },
        }
    ), 201


@app.route("/api/scmaglev/reservations/<int:reservation_id>/pay", methods=["POST"])
@jwt_required()
def scmaglev_pay_reservation(reservation_id):
    user_id = int(get_jwt_identity())
    reservation = db.session.get(Reservation, reservation_id)
    if not reservation or reservation.user_id != user_id:
        return jsonify({"message": "예매 정보를 찾을 수 없습니다."}), 404
    if reservation.status == "cancelled":
        return jsonify({"message": "취소된 예매는 결제할 수 없습니다."}), 400
    if reservation.payment_status == "paid":
        return jsonify({"message": "이미 결제 완료된 예매입니다."}), 400

    now = now_utc_naive()
    if reservation.payment_due_at and reservation.payment_due_at < now:
        reservation.status = "cancelled"
        reservation.payment_status = "expired"
        reservation.failed_at = now
        reservation.fail_reason = "결제 유효시간 만료"
        log_operation_event(
            event_type="PAYMENT_EXPIRED",
            message=f"결제 만료: 예약 {reservation.id}",
            severity="warning",
            source="payment",
            reservation_id=reservation.id,
            user_id=user_id,
        )
        db.session.commit()
        return jsonify({"message": "결제 시간이 만료되었습니다. 다시 예매해주세요."}), 400

    data = request.get_json() or {}
    success = bool(data.get("success", True))
    if success:
        reservation.status = "booked"
        reservation.payment_status = "paid"
        reservation.paid_at = now
        log_operation_event(
            event_type="PAYMENT_COMPLETED",
            message=f"결제 완료: 예약 {reservation.id}",
            severity="info",
            source="payment",
            reservation_id=reservation.id,
            user_id=user_id,
            train_id=(db.session.get(
                Schedule, reservation.schedule_id).train_id if reservation.schedule_id else None),
        )
        db.session.commit()
        return jsonify({"message": "결제가 완료되었습니다.", "reservation_id": reservation.id})

    reservation.payment_status = "failed"
    reservation.failed_at = now
    reservation.fail_reason = str(data.get("reason", "결제 실패"))
    log_operation_event(
        event_type="PAYMENT_FAILED",
        message=f"결제 실패: 예약 {reservation.id}",
        severity="warning",
        source="payment",
        reservation_id=reservation.id,
        user_id=user_id,
        payload={"reason": reservation.fail_reason},
    )
    db.session.commit()
    return jsonify({"message": "결제에 실패했습니다.", "reservation_id": reservation.id}), 400


@app.route("/api/scmaglev/reservations/<int:reservation_id>/toss/prepare", methods=["POST"])
@jwt_required()
def scmaglev_prepare_toss_payment(reservation_id):
    user_id = int(get_jwt_identity())
    reservation = db.session.get(Reservation, reservation_id)
    if not reservation or reservation.user_id != user_id:
        return jsonify({"message": "예매 정보를 찾을 수 없습니다."}), 404
    if reservation.status == "cancelled":
        return jsonify({"message": "취소된 예매는 결제할 수 없습니다."}), 400
    if reservation.payment_status == "paid":
        return jsonify({"message": "이미 결제 완료된 예매입니다."}), 400
    if reservation.payment_status != "pending":
        return jsonify({"message": "결제 대기 상태에서만 결제를 시작할 수 있습니다."}), 400

    now = now_utc_naive()
    if reservation_is_payment_expired(reservation, now):
        mark_reservation_as_expired(reservation, user_id)
        db.session.commit()
        return jsonify({"message": "결제 시간이 만료되었습니다. 결제 재시도를 먼저 진행해주세요."}), 400

    payment_mode = get_payment_mode()
    mock_only = payment_mode["mock_only"]
    client_key, _ = get_toss_keys()
    if not mock_only and not client_key:
        return jsonify({"message": "토스페이먼츠 클라이언트 키가 설정되지 않았습니다."}), 503
    if not mock_only and not has_real_toss_keys():
        return jsonify({"message": "토스 테스트 키가 예시값입니다. 실제 테스트 키(test_ck_/test_sk_)를 입력해주세요."}), 503

    schedule = db.session.get(Schedule, reservation.schedule_id)
    seat = db.session.get(Seat, reservation.seat_id)
    if not schedule or not seat:
        return jsonify({"message": "결제 대상 예매의 일정 또는 좌석 정보가 유효하지 않습니다."}), 400

    user = db.session.get(User, user_id)
    amount = calculate_estimated_fare(
        schedule, seat_type=(seat.seat_type if seat else "general"))
    order_id = build_toss_order_id(reservation)
    order_name = build_toss_order_name(reservation, schedule, seat)
    base_url = request.host_url.rstrip("/")
    success_url = f"{base_url}/?toss=success&reservation_id={reservation.id}#reservation"
    fail_url = f"{base_url}/?toss=fail&reservation_id={reservation.id}#reservation"

    return jsonify(
        {
            "client_key": client_key,
            "mock_only": mock_only,
            "mode_reason": payment_mode["reason"],
            "order_id": order_id,
            "order_name": order_name,
            "amount": amount,
            "customer_name": user.username if user else "SCMAGLEV 사용자",
            "customer_email": user.email if user and user.email else "",
            "success_url": success_url,
            "fail_url": fail_url,
        }
    )


@app.route("/api/scmaglev/reservations/<int:reservation_id>/toss/confirm", methods=["POST"])
@jwt_required()
def scmaglev_confirm_toss_payment(reservation_id):
    user_id = int(get_jwt_identity())
    reservation = db.session.get(Reservation, reservation_id)
    if not reservation or reservation.user_id != user_id:
        return jsonify({"message": "예매 정보를 찾을 수 없습니다."}), 404
    if reservation.status == "cancelled":
        return jsonify({"message": "취소된 예매는 결제할 수 없습니다."}), 400
    if reservation.payment_status == "paid":
        return jsonify({"message": "이미 결제 완료된 예매입니다."}), 400
    if reservation.payment_status != "pending":
        return jsonify({"message": "결제 대기 상태에서만 결제 승인할 수 있습니다."}), 400

    now = now_utc_naive()
    if reservation_is_payment_expired(reservation, now):
        mark_reservation_as_expired(reservation, user_id)
        db.session.commit()
        return jsonify({"message": "결제 시간이 만료되었습니다. 결제 재시도를 먼저 진행해주세요."}), 400

    data = request.get_json() or {}
    payment_key = str(data.get("paymentKey", "")).strip()
    order_id = str(data.get("orderId", "")).strip()
    try:
        amount = int(data.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"message": "amount 값이 올바르지 않습니다."}), 400
    if not payment_key or not order_id:
        return jsonify({"message": "paymentKey/orderId를 입력해주세요."}), 400

    expected_order_id = build_toss_order_id(reservation)
    if order_id != expected_order_id:
        return jsonify({"message": "유효하지 않은 주문번호입니다."}), 400

    schedule = db.session.get(Schedule, reservation.schedule_id)
    seat = db.session.get(Seat, reservation.seat_id)
    if not schedule or not seat:
        return jsonify({"message": "결제 대상 예매의 일정 또는 좌석 정보가 유효하지 않습니다."}), 400

    expected_amount = calculate_estimated_fare(
        schedule, seat_type=(seat.seat_type if seat else "general"))
    if amount != expected_amount:
        return jsonify({"message": "결제 금액이 예약 정보와 일치하지 않습니다."}), 400

    _, secret_key = get_toss_keys()
    if not secret_key:
        return jsonify({"message": "토스페이먼츠 시크릿 키가 설정되지 않았습니다."}), 503

    confirmed, error_payload = confirm_toss_payment(
        payment_key, order_id, amount)
    if error_payload:
        reservation.payment_status = "failed"
        reservation.failed_at = now_utc_naive()
        reservation.fail_reason = str(
            error_payload.get("message")
            or error_payload.get("code")
            or "토스 결제 승인 실패"
        )[:255]
        log_operation_event(
            event_type="PAYMENT_FAILED",
            message=f"결제 실패: 예약 {reservation.id}",
            severity="warning",
            source="payment",
            reservation_id=reservation.id,
            user_id=user_id,
            payload={"reason": reservation.fail_reason},
        )
        db.session.commit()
        return jsonify({"message": reservation.fail_reason}), 400

    reservation.status = "booked"
    reservation.payment_status = "paid"
    reservation.paid_at = now_utc_naive()
    reservation.failed_at = None
    reservation.fail_reason = None
    log_operation_event(
        event_type="PAYMENT_COMPLETED",
        message=f"결제 완료: 예약 {reservation.id}",
        severity="info",
        source="payment",
        reservation_id=reservation.id,
        user_id=user_id,
        train_id=(schedule.train_id if schedule else None),
        payload={"provider": "tosspayments", "payment_key": payment_key},
    )
    db.session.commit()
    return jsonify(
        {
            "message": "토스 결제가 완료되었습니다.",
            "reservation_id": reservation.id,
            "payment": {
                "order_id": confirmed.get("orderId"),
                "method": confirmed.get("method"),
                "approved_at": confirmed.get("approvedAt"),
                "status": confirmed.get("status"),
            },
        }
    )


@app.route("/api/scmaglev/reservations/<int:reservation_id>/retry-payment", methods=["POST"])
@jwt_required()
def scmaglev_retry_payment(reservation_id):
    user_id = int(get_jwt_identity())
    reservation = db.session.get(Reservation, reservation_id)
    if not reservation or reservation.user_id != user_id:
        return jsonify({"message": "예매 정보를 찾을 수 없습니다."}), 404
    if reservation.payment_status == "paid":
        return jsonify({"message": "이미 결제 완료된 예매입니다."}), 400
    if reservation.payment_status not in {"failed", "expired"}:
        return jsonify({"message": "결제 재시도는 실패/만료 건에서만 가능합니다."}), 400
    if reservation.status == "cancelled" and reservation.payment_status == "refunded":
        return jsonify({"message": "재시도할 수 없는 예약 상태입니다."}), 400

    now = now_utc_naive()
    conflict = Reservation.query.filter(
        Reservation.id != reservation.id,
        Reservation.schedule_id == reservation.schedule_id,
        Reservation.seat_id == reservation.seat_id,
        or_(
            Reservation.status == "booked",
            (Reservation.status == "pending_payment") & (
                Reservation.payment_due_at >= now),
        ),
    ).first()
    if conflict:
        return jsonify({"message": "좌석이 이미 다른 사용자에게 배정되어 결제를 재시도할 수 없습니다."}), 409

    reservation.status = "pending_payment"
    reservation.payment_status = "pending"
    reservation.payment_token = secrets.token_urlsafe(16)
    reservation.payment_due_at = now + \
        timedelta(minutes=PAYMENT_TIMEOUT_MINUTES)
    reservation.failed_at = None
    reservation.fail_reason = None
    log_operation_event(
        event_type="PAYMENT_RETRY",
        message=f"결제 재시도: 예약 {reservation.id}",
        severity="info",
        source="payment",
        reservation_id=reservation.id,
        user_id=user_id,
    )
    db.session.commit()
    return jsonify(
        {
            "message": "결제 재시도 상태로 전환되었습니다.",
            "reservation_id": reservation.id,
            "payment_due_at": reservation.payment_due_at.isoformat() if reservation.payment_due_at else None,
        }
    )


@app.route("/api/scmaglev/reservations", methods=["GET"])
@jwt_required()
def scmaglev_list_reservations():
    user_id = int(get_jwt_identity())
    reservations = (
        Reservation.query.filter_by(user_id=user_id)
        .order_by(Reservation.booked_at.desc())
        .all()
    )
    data = []
    for reservation in reservations:
        schedule = db.session.get(Schedule, reservation.schedule_id)
        seat = db.session.get(Seat, reservation.seat_id)
        journey = build_passenger_journey(reservation)
        data.append(
            {
                "id": reservation.id,
                "schedule": serialize_schedule(schedule) if schedule else None,
                "seat": {
                    "id": seat.id,
                    "car_number": seat.car_number,
                    "seat_number": seat.seat_number,
                } if seat else None,
                "status": reservation.status,
                "payment_status": reservation.payment_status,
                "payment_due_at": reservation.payment_due_at.isoformat() if reservation.payment_due_at else None,
                "paid_at": reservation.paid_at.isoformat() if reservation.paid_at else None,
                "fail_reason": reservation.fail_reason,
                "live": journey.get("live"),
                "journey": {
                    "current_phase": journey["current_phase"],
                    "current_label": journey["current_label"],
                    "next_milestone": journey["next_milestone"],
                    "progress_pct": journey["progress_pct"],
                },
                "trust": journey.get("trust"),
            }
        )
    return jsonify({"reservations": data})


@app.route("/api/scmaglev/passenger/journey/<int:reservation_id>", methods=["GET"])
@jwt_required()
def scmaglev_passenger_journey(reservation_id):
    user_id = int(get_jwt_identity())
    reservation = db.session.get(Reservation, reservation_id)
    if not reservation or reservation.user_id != user_id:
        return jsonify({"message": "예매 정보를 찾을 수 없습니다."}), 404
    return jsonify({"journey": build_passenger_journey(reservation)})


@app.route("/api/scmaglev/reservations/<int:reservation_id>/cancel", methods=["POST"])
@jwt_required()
def scmaglev_cancel_reservation(reservation_id):
    user_id = int(get_jwt_identity())
    reservation = db.session.get(Reservation, reservation_id)
    if not reservation or reservation.user_id != user_id:
        return jsonify({"message": "예매 정보를 찾을 수 없습니다."}), 404
    if reservation.status == "cancelled":
        return jsonify({"message": "이미 취소된 예매입니다."}), 400

    reservation.status = "cancelled"
    reservation.payment_status = "refunded" if reservation.payment_status == "paid" else "cancelled"
    reservation.cancelled_at = now_utc_naive()
    log_operation_event(
        event_type="RESERVATION_CANCELLED",
        message=f"예약 취소: {reservation.id}",
        severity="info",
        source="passenger",
        reservation_id=reservation.id,
        user_id=user_id,
    )
    db.session.commit()
    return jsonify({"message": "예매가 취소되었습니다."})


@app.route("/api/scmaglev/train-locations", methods=["GET"])
def scmaglev_train_locations():
    locations = TrainLocation.query.order_by(
        TrainLocation.train_id.asc()).all()
    payload = []
    for location in locations:
        item = _update_train_location(location)
        if item:
            payload.append(item)
    db.session.commit()
    return jsonify({"interval_seconds": SIMULATION_INTERVAL_SECONDS, "trains": payload})


@app.route("/api/scmaglev/dashboard/summary", methods=["GET"])
@require_roles("controller", "admin")
def scmaglev_dashboard_summary():
    return jsonify(build_dashboard_summary())


def _empty_dashboard_summary():
    return {
        "status_counts": {
            "normal": 0,
            "delayed": 0,
            "stopped": 0,
            "disrupted": 0,
            "arrived": 0,
            "waiting": 0,
            "in_service": 0,
        },
        "fleet_counts": {"in_service": 0, "waiting": 0, "arrived": 0},
        "tracked_train_count": 0,
        "delayed_trains": [],
        "fault_trains": [],
        "fault_summary": [],
        "starting": True,
    }


@app.route("/api/scmaglev/dashboard/public-summary", methods=["GET"])
def scmaglev_dashboard_public_summary():
    if not _startup_state["ready"]:
        return jsonify(_empty_dashboard_summary())
    try:
        return jsonify(build_dashboard_summary())
    except Exception as exc:
        app.logger.exception("dashboard public-summary failed: %s", exc)
        payload = _empty_dashboard_summary()
        payload["error"] = "summary_unavailable"
        return jsonify(payload), 503


@app.route("/api/scmaglev/dashboard/ai/draft", methods=["POST"])
def scmaglev_dashboard_ai_draft():
    data = request.get_json() or {}
    draft_type = str(data.get("draft_type", "")).strip().lower()
    context = data.get("context") if isinstance(
        data.get("context"), dict) else {}
    try:
        text, meta = generate_dashboard_draft(draft_type, context)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    payload = {"text": text, "ai_meta": meta}
    if meta.get("actions"):
        payload["actions"] = meta["actions"]
    return jsonify(payload)


PASSENGER_NOTICE_EVENT_TYPES = (
    "CONTROL_PASSENGER_NOTICE_DELAY",
    "CONTROL_PASSENGER_NOTICE_RECOVERY",
    "CONTROL_EMERGENCY_BROADCAST",
)


def build_passenger_notices(limit=5):
    rows = (
        OperationEventLog.query.filter(
            OperationEventLog.event_type.in_(PASSENGER_NOTICE_EVENT_TYPES)
        )
        .order_by(OperationEventLog.created_at.desc())
        .limit(limit)
        .all()
    )
    notices = []
    for row in rows:
        notice_type = "delay"
        if row.event_type == "CONTROL_PASSENGER_NOTICE_RECOVERY":
            notice_type = "recovery"
        elif row.event_type == "CONTROL_EMERGENCY_BROADCAST":
            notice_type = "emergency"
        payload = {}
        if row.payload_json:
            try:
                payload = json.loads(row.payload_json)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        notices.append(
            {
                "id": row.id,
                "type": notice_type,
                "event_type": row.event_type,
                "severity": row.severity,
                "message": row.message,
                "source": row.source,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "action_type": payload.get("action_type"),
            }
        )
    return notices


def build_passenger_control_trust(status_counts, prediction_summary):
    in_service = int(status_counts.get("in_service", 0) or 0)
    total = in_service or sum(
        int(status_counts.get(key, 0) or 0)
        for key in ("normal", "delayed", "stopped", "disrupted")
    )
    normal_count = int(status_counts.get("normal", 0) or 0)
    normal_rate = round((normal_count / total) * 100) if total else 100
    disrupted = int(status_counts.get("stopped", 0) or 0) + \
        int(status_counts.get("disrupted", 0) or 0)
    delayed = int(status_counts.get("delayed", 0) or 0)
    high_risk = int(prediction_summary.get("high", 0) or 0)

    if disrupted >= 2 or high_risk >= 3:
        delay_risk = "HIGH"
    elif disrupted >= 1 or delayed >= 3 or high_risk >= 1:
        delay_risk = "MEDIUM"
    else:
        delay_risk = "LOW"

    pred_total = sum(int(prediction_summary.get(key, 0) or 0)
                     for key in ("high", "medium", "low")) or 1
    low_ratio = int(prediction_summary.get("low", 0) or 0) / pred_total
    accuracy = min(99, max(88, 90 + int(low_ratio * 8)))

    recovery_status = "attention" if disrupted else "stable"
    message_line = f"관제 연동 · 지연 위험 {delay_risk} · 예측 정확도 {accuracy}%"

    return {
        "control_linked": True,
        "network_normal_rate_pct": normal_rate,
        "prediction_accuracy_pct": accuracy,
        "delay_risk_level": delay_risk,
        "high_congestion_predictions": high_risk,
        "active_disruptions": disrupted,
        "recovery_status": recovery_status,
        "message_line": message_line,
    }


def build_station_congestion_heatmap():
    stations = Station.query.order_by(Station.name.asc()).all()
    current_hour = now_utc_naive().hour
    station_routes = defaultdict(set)
    for route in Route.query.all():
        for route_station in RouteStation.query.filter_by(route_id=route.id).all():
            station_routes[route_station.station_id].add(route.name)

    station_train_count = defaultdict(int)
    for location in TrainLocation.query.all():
        if location.next_station_id:
            station_train_count[location.next_station_id] += 1

    heatmap = []
    for station in stations:
        routes = list(station_routes.get(station.id, ["전국 통합"]))
        route_name = routes[0] if routes else "전국 통합"
        passengers = 400 + \
            station_train_count.get(station.id, 0) * \
            180 + ((station.id * 137) % 500)
        prediction = predict_congestion(
            hour=current_hour,
            route_name=route_name,
            recent_passengers=passengers,
            is_event_day=False,
            model_type="ml",
        )
        heatmap.append(
            {
                "station_id": station.id,
                "code": station.code,
                "name": station.name,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "congestion_label": prediction["label"],
                "risk_score": float(prediction.get("risk_score", 0)),
                "confidence": float(prediction.get("confidence", 0)),
            }
        )
    return heatmap


def build_dashboard_summary():
    locations = TrainLocation.query.all()
    current_time = now_utc_naive()
    fleet_counts = {"in_service": 0, "waiting": 0, "arrived": 0}
    status_counts = {
        "normal": 0,
        "delayed": 0,
        "stopped": 0,
        "disrupted": 0,
        "arrived": 0,
        "waiting": 0,
        "in_service": 0,
    }
    phase_by_location_id = {}

    for location in locations:
        train = db.session.get(Train, location.train_id)
        if not train:
            continue
        schedule = (
            Schedule.query.filter_by(train_id=train.id)
            .order_by(Schedule.departure_time.desc())
            .first()
        )
        if not schedule:
            continue
        refresh_schedule_for_service(schedule, current_time)
        phase = resolve_train_service_phase(schedule, current_time)
        phase_by_location_id[location.train_id] = phase
        fleet_counts[phase] += 1
        if phase == "waiting":
            status_counts["waiting"] += 1
        elif phase == "arrived":
            status_counts["arrived"] += 1
        else:
            status_counts["in_service"] += 1
            op_status = (
                location.operation_status
                if location.operation_status in {"normal", "delayed", "stopped", "disrupted"}
                else "normal"
            )
            status_counts[op_status] += 1

    delayed = []
    fault_trains = []
    fault_counts = {}
    predicted = []
    current_hour = current_time.hour
    for location in locations:
        train = db.session.get(Train, location.train_id)
        if not train:
            continue
        phase = phase_by_location_id.get(train.id)
        if phase != "in_service":
            continue
        route = db.session.get(
            Route, train.route_id) if train.route_id else None
        schedule = (
            Schedule.query.filter_by(train_id=train.id)
            .order_by(Schedule.departure_time.desc())
            .first()
        )
        departure_station = (
            db.session.get(Station, schedule.departure_station_id)
            if schedule
            else None
        )
        arrival_station = (
            db.session.get(Station, schedule.arrival_station_id)
            if schedule
            else None
        )
        fault = serialize_train_fault(location)
        if fault:
            fault_counts[fault["code"]] = fault_counts.get(
                fault["code"], 0) + 1
            fault_trains.append(
                {
                    "train_id": train.id,
                    "train_number": train.train_number,
                    "route_name": route.name if route else None,
                    "departure_station": departure_station.name if departure_station else None,
                    "arrival_station": arrival_station.name if arrival_station else None,
                    "operation_status": location.operation_status,
                    "eta_minutes": location.eta_minutes,
                    "fault": fault,
                }
            )
        if location.operation_status in {"delayed", "stopped", "disrupted"}:
            delayed.append(
                {
                    "train_id": train.id,
                    "train_number": train.train_number,
                    "route_name": route.name if route else None,
                    "departure_station": departure_station.name if departure_station else None,
                    "arrival_station": arrival_station.name if arrival_station else None,
                    "operation_status": location.operation_status,
                    "eta_minutes": location.eta_minutes,
                    "fault": fault,
                }
            )
        predicted.append(
            predict_congestion(
                hour=current_hour,
                route_name=route.name if route else "",
                recent_passengers=random.randint(200, 1800),
                is_event_day=False,
                model_type="ml",
            )
        )

    high_count = sum(1 for item in predicted if item["label"] == "high")
    medium_count = sum(1 for item in predicted if item["label"] == "medium")
    low_count = sum(1 for item in predicted if item["label"] == "low")
    disrupted_count = status_counts["stopped"] + status_counts["disrupted"]

    alarms = []
    if disrupted_count >= 2:
        message = f"장애/정차 상태 열차 {disrupted_count}대 감지"
        alarms.append(
            {"key": "disrupted", "severity": "critical", "message": message})
        maybe_emit_alarm(
            "alarm_disrupted",
            message,
            severity="critical",
            payload={"disrupted_count": disrupted_count},
        )
    elif disrupted_count == 1:
        message = f"장애/정차 상태 열차 {disrupted_count}대 감지"
        alarms.append(
            {"key": "disrupted", "severity": "warning", "message": message})
    if status_counts["delayed"] >= 4:
        message = f"지연 열차 {status_counts['delayed']}대 감지"
        alarms.append(
            {"key": "delayed", "severity": "warning", "message": message})
        maybe_emit_alarm(
            "alarm_delayed",
            message,
            severity="warning",
            payload={"delayed_count": status_counts["delayed"]},
        )
    if high_count >= 2:
        message = f"고혼잡 예측 {high_count}건 감지"
        alarms.append({"key": "congestion_high",
                      "severity": "warning", "message": message})
        maybe_emit_alarm(
            "alarm_high_congestion",
            message,
            severity="warning",
            payload={"high_prediction_count": high_count},
        )

    if alarms:
        db.session.commit()

    prediction_summary = {
        "high": high_count,
        "medium": medium_count,
        "low": low_count,
    }

    direct_fleet_total = Train.query.filter(
        Train.train_number.like("SM-D-%")).count()
    direct_tracked_count = (
        TrainLocation.query.join(Train, Train.id == TrainLocation.train_id)
        .filter(Train.train_number.like("SM-D-%"))
        .count()
    )

    return {
        "status_counts": status_counts,
        "fleet_counts": fleet_counts,
        "direct_fleet_total": direct_fleet_total,
        "direct_tracked_count": direct_tracked_count,
        "tracked_train_count": len(locations),
        "delayed_trains": delayed,
        "fault_trains": fault_trains,
        "fault_summary": [
            {
                "code": code,
                "label": TRAIN_FAULT_BY_CODE.get(code, {}).get("label", code),
                "count": count,
            }
            for code, count in sorted(fault_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "alarms": alarms,
        "prediction_summary": prediction_summary,
        "passenger_trust": build_passenger_control_trust(status_counts, prediction_summary),
        "passenger_notices": build_passenger_notices(),
        "ai_status": get_openai_status(),
    }


@app.route("/api/scmaglev/passenger/control-trust", methods=["GET"])
def scmaglev_passenger_control_trust():
    summary = build_dashboard_summary()
    return jsonify(
        {
            "trust": summary.get("passenger_trust"),
            "prediction_summary": summary.get("prediction_summary"),
        }
    )


@app.route("/api/scmaglev/dashboard/station-heatmap", methods=["GET"])
def scmaglev_dashboard_station_heatmap():
    heatmap = build_station_congestion_heatmap()
    return jsonify({"stations": heatmap, "updated_at": now_utc_naive().isoformat()})


@app.route("/api/scmaglev/dashboard/events", methods=["GET"])
def scmaglev_dashboard_events():
    limit = parse_int(request.args.get("limit", 30), 30, 1, 200)
    severity = request.args.get("severity", "").strip().lower()
    event_type = request.args.get("event_type", "").strip().upper()
    event_group = request.args.get("event_group", "").strip().lower()
    acknowledged = request.args.get("acknowledged", "").strip().lower()
    query = OperationEventLog.query.order_by(
        OperationEventLog.created_at.desc())
    if severity:
        query = query.filter(OperationEventLog.severity == severity)
    if event_type:
        query = query.filter(OperationEventLog.event_type == event_type)
    query = apply_event_group_filter(query, event_group)
    if acknowledged in {"true", "false"}:
        query = query.filter(
            OperationEventLog.is_acknowledged == (acknowledged == "true"))
    rows = query.limit(limit).all()
    events = []
    for row in rows:
        events.append(
            {
                "id": row.id,
                "event_type": row.event_type,
                "severity": row.severity,
                "source": row.source,
                "train_id": row.train_id,
                "station_id": row.station_id,
                "reservation_id": row.reservation_id,
                "user_id": row.user_id,
                "message": row.message,
                "payload_json": row.payload_json,
                "is_acknowledged": row.is_acknowledged,
                "created_at": isoformat_utc(row.created_at),
                "acknowledged_at": isoformat_utc(row.acknowledged_at),
            }
        )
    return jsonify({"events": events})


@app.route("/api/scmaglev/dashboard/events/meta", methods=["GET"])
def scmaglev_dashboard_event_meta():
    rows = OperationEventLog.query.order_by(
        OperationEventLog.created_at.desc()).limit(400).all()
    event_types = sorted({row.event_type for row in rows if row.event_type})
    severities = sorted({row.severity for row in rows if row.severity})
    return jsonify(
        {
            "event_types": event_types,
            "severities": severities,
            "ack_options": ["false", "true"],
            "event_groups": ["all", "control", "alarm", "payment", "vehicle"],
        }
    )


@app.route("/api/scmaglev/dashboard/fault-causes", methods=["GET"])
def scmaglev_dashboard_fault_causes():
    return jsonify(
        {
            "fault_causes": TRAIN_FAULT_CATALOG,
            "labels": {item["code"]: item["label"] for item in TRAIN_FAULT_CATALOG},
        }
    )


@app.route("/api/scmaglev/dashboard/trains/<int:train_id>/fault/clear", methods=["POST"])
@require_roles("controller", "admin")
def scmaglev_clear_train_fault(train_id):
    train = db.session.get(Train, train_id)
    if not train:
        return jsonify({"message": "열차를 찾을 수 없습니다."}), 404

    location = TrainLocation.query.filter_by(train_id=train.id).first()
    if not location:
        return jsonify({"message": "열차 위치 정보가 없습니다."}), 404
    if not location.fault_cause_code:
        return jsonify({"message": "현재 고장 상태가 아닙니다."}), 400

    data = request.get_json(silent=True) or {}
    note = str(data.get("note", "")).strip()
    previous_fault = serialize_train_fault(location)
    current_time = now_utc_naive()
    clear_train_fault(location, train, current_time)

    user_id = int(get_jwt_identity())
    log_operation_event(
        event_type="CONTROL_VEHICLE_FAULT_CLEARED",
        message=note or f"{train.train_number} {previous_fault['label']} 고장 수동 복구 처리",
        severity="info",
        source="controller",
        user_id=user_id,
        train_id=train.id,
        payload={
            "action_type": "VEHICLE_FAULT_CLEAR",
            "previous_fault": previous_fault,
            "note": note or None,
        },
    )
    commit_with_sqlite_retry()
    return jsonify(
        {
            "message": f"{train.train_number} 고장 복구 처리 완료",
            "train": {
                "train_id": train.id,
                "train_number": train.train_number,
                "operation_status": location.operation_status,
                "speed_kmh": location.speed_kmh,
                "fault": serialize_train_fault(location),
            },
        }
    )


@app.route("/api/scmaglev/dashboard/events/ack-bulk", methods=["POST"])
@require_roles("controller", "admin")
def scmaglev_ack_dashboard_events_bulk():
    data = request.get_json() or {}
    user_id = int(get_jwt_identity())
    severity = str(data.get("severity", "")).strip().lower()
    event_type = str(data.get("event_type", "")).strip().upper()
    event_group = str(data.get("event_group", "")).strip().lower()
    limit = parse_int(data.get("limit", 200), 200, 1, 200)
    raw_event_ids = data.get("event_ids") or []
    event_ids = []
    for value in raw_event_ids:
        try:
            event_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    event_ids = sorted(set(event_ids))[:200]

    query = OperationEventLog.query.filter(
        OperationEventLog.is_acknowledged.is_(False))
    if event_ids:
        query = query.filter(OperationEventLog.id.in_(event_ids))
    else:
        if severity:
            query = query.filter(OperationEventLog.severity == severity)
        if event_type:
            query = query.filter(OperationEventLog.event_type == event_type)
        query = apply_event_group_filter(query, event_group)
        query = query.order_by(
            OperationEventLog.created_at.desc()).limit(limit)
    rows = query.all()

    if not rows:
        return jsonify({"message": "확인 처리할 이벤트가 없습니다.", "acknowledged_count": 0})

    now = now_utc_naive()
    ack_ids = []
    try:
        for row in rows:
            row.is_acknowledged = True
            row.acknowledged_at = now
            ack_ids.append(row.id)
        log_operation_event(
            event_type="ALARM_BULK_ACKNOWLEDGED",
            message=f"이벤트 일괄 확인 처리: {len(ack_ids)}건",
            severity="info",
            source="controller",
            user_id=user_id,
            payload={"event_ids": ack_ids[:50],
                     "acknowledged_count": len(ack_ids)},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("이벤트 일괄 ACK 처리 실패")
        return jsonify({
            "message": "이벤트 확인 처리 중 서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        }), 500

    return jsonify(
        {
            "message": f"{len(ack_ids)}건의 이벤트를 확인 처리했습니다.",
            "acknowledged_count": len(ack_ids),
            "event_ids": ack_ids,
        }
    )


@app.route("/api/scmaglev/dashboard/events/<int:event_id>/ack", methods=["POST"])
@require_roles("controller", "admin")
def scmaglev_ack_dashboard_event(event_id):
    event = db.session.get(OperationEventLog, event_id)
    if not event:
        return jsonify({"message": "이벤트를 찾을 수 없습니다."}), 404
    if event.is_acknowledged:
        return jsonify({"message": "이미 확인 처리된 이벤트입니다."}), 400

    user_id = int(get_jwt_identity())
    try:
        event.is_acknowledged = True
        event.acknowledged_at = now_utc_naive()
        log_operation_event(
            event_type="ALARM_ACKNOWLEDGED",
            message=f"이벤트 확인 처리: {event.id}",
            severity="info",
            source="controller",
            user_id=user_id,
            payload={"event_id": event.id},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("이벤트 ACK 처리 실패: event_id=%s", event_id)
        return jsonify({
            "message": "이벤트 확인 처리 중 서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        }), 500

    return jsonify({"message": "이벤트 확인 처리 완료"})


@app.route("/api/scmaglev/dashboard/actions", methods=["POST"])
@require_roles("controller", "admin")
def scmaglev_dashboard_action():
    data = request.get_json() or {}
    action_type = str(data.get("action_type", "")).strip().upper()
    note = str(data.get("note", "")).strip()
    event_id = data.get("event_id")
    train_id = data.get("train_id")
    station_id = data.get("station_id")

    action_specs = {
        "EMERGENCY_BROADCAST": {
            "event_type": "CONTROL_EMERGENCY_BROADCAST",
            "severity": "warning",
            "default_message": "긴급 승객 안내 방송 발송",
        },
        "SPEED_LIMIT_ADVISORY": {
            "event_type": "CONTROL_SPEED_LIMIT_ADVISORY",
            "severity": "warning",
            "default_message": "전 구간 감속 운행 권고 기록",
        },
        "SPARE_TRAIN_DEPLOYMENT": {
            "event_type": "CONTROL_SPARE_TRAIN_DEPLOYMENT",
            "severity": "info",
            "default_message": "예비편성 투입 기록",
        },
        "INCIDENT_FOCUS": {
            "event_type": "CONTROL_INCIDENT_FOCUS",
            "severity": "info",
            "default_message": "최우선 이슈 포커스 처리",
        },
        "INCIDENT_IN_PROGRESS": {
            "event_type": "CONTROL_INCIDENT_IN_PROGRESS",
            "severity": "warning",
            "default_message": "이슈 조치중 상태 업데이트",
        },
        "INCIDENT_RESOLVED": {
            "event_type": "CONTROL_INCIDENT_RESOLVED",
            "severity": "info",
            "default_message": "이슈 복구완료 상태 업데이트",
        },
        "PASSENGER_NOTICE_DELAY": {
            "event_type": "CONTROL_PASSENGER_NOTICE_DELAY",
            "severity": "warning",
            "default_message": "지연 안내문 발송",
        },
        "PASSENGER_NOTICE_RECOVERY": {
            "event_type": "CONTROL_PASSENGER_NOTICE_RECOVERY",
            "severity": "info",
            "default_message": "복구 안내문 발송",
        },
        "HANDOVER_NOTE_COPY": {
            "event_type": "CONTROL_HANDOVER_NOTE_COPY",
            "severity": "info",
            "default_message": "인수인계 메모 복사 실행",
        },
    }

    spec = action_specs.get(action_type)
    if not spec:
        return jsonify({"message": "지원하지 않는 액션 타입입니다."}), 400

    target_event = None
    if event_id not in {None, ""}:
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            return jsonify({"message": "event_id 형식이 올바르지 않습니다."}), 400
        target_event = db.session.get(OperationEventLog, event_id)
        if not target_event:
            return jsonify({"message": "대상 이벤트를 찾을 수 없습니다."}), 404

    resolved_train_id = None
    if train_id not in {None, ""}:
        try:
            resolved_train_id = int(train_id)
        except (TypeError, ValueError):
            resolved_train_id = None
    if resolved_train_id is None and target_event and target_event.train_id:
        resolved_train_id = target_event.train_id

    resolved_station_id = None
    if station_id not in {None, ""}:
        try:
            resolved_station_id = int(station_id)
        except (TypeError, ValueError):
            resolved_station_id = None
    if resolved_station_id is None and target_event and target_event.station_id:
        resolved_station_id = target_event.station_id

    user_id = int(get_jwt_identity())
    event = log_operation_event(
        event_type=spec["event_type"],
        message=note or spec["default_message"],
        severity=spec["severity"],
        source="controller",
        user_id=user_id,
        train_id=resolved_train_id,
        station_id=resolved_station_id,
        payload={
            "action_type": action_type,
            "target_event_id": target_event.id if target_event else None,
            "note": note or None,
        },
    )
    commit_with_sqlite_retry()
    return jsonify(
        {
            "message": "운영 액션이 기록되었습니다.",
            "action": {
                "id": event.id,
                "action_type": action_type,
                "event_type": event.event_type,
                "severity": event.severity,
                "message": event.message,
                "train_id": event.train_id,
                "station_id": event.station_id,
                "target_event_id": target_event.id if target_event else None,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            },
        }
    )


@app.route("/api/scmaglev/dashboard/trains", methods=["GET"])
def scmaglev_dashboard_trains():
    status_filter = request.args.get("status", "").strip().lower()
    try:
        locations = TrainLocation.query.order_by(
            TrainLocation.train_id.asc()).all()
        payload = []
        for location in locations:
            item = _update_train_location(location)
            if not item:
                continue
            if status_filter and item["operation_status"] != status_filter:
                continue
            payload.append(item)
        db.session.commit()
        return jsonify({"trains": payload, "status_filter": status_filter or "all"})
    except Exception as exc:
        app.logger.exception("dashboard trains failed: %s", exc)
        return jsonify({"trains": [], "status_filter": status_filter or "all", "starting": True}), 503


@app.route("/api/scmaglev/dashboard/train/<int:train_id>", methods=["GET"])
def scmaglev_dashboard_train_detail(train_id):
    train = db.session.get(Train, train_id)
    location = TrainLocation.query.filter_by(train_id=train_id).first()
    if not train or not location:
        return jsonify({"message": "열차 정보를 찾을 수 없습니다."}), 404

    current = _update_train_location(location)
    db.session.commit()
    if not current:
        return jsonify({"message": "열차 위치를 계산할 수 없습니다."}), 500

    history = list(train_history[train_id])
    ml_prediction = predict_congestion(
        hour=now_utc_naive().hour,
        route_name=current.get("route_name") or "",
        recent_passengers=random.randint(200, 1800),
        is_event_day=False,
        model_type="ml",
    )
    dl_prediction = predict_congestion(
        hour=now_utc_naive().hour,
        route_name=current.get("route_name") or "",
        recent_passengers=random.randint(200, 1800),
        is_event_day=False,
        model_type="dl",
    )

    return jsonify(
        {
            "train": current,
            "history": history,
            "predictions": {
                "ml": ml_prediction,
                "dl": dl_prediction,
            },
            "history_points": len(history),
        }
    )


@app.route("/api/scmaglev/ml/congestion/predict", methods=["POST"])
def scmaglev_predict_congestion():
    data = request.get_json() or {}
    hour = parse_int(data.get("hour", now_utc_naive().hour),
                     now_utc_naive().hour, 0, 23)
    route_name = str(data.get("route_name", "서울권 순환"))
    recent_passengers = parse_int(
        data.get("recent_passengers", 500), 500, 0, 5000)
    is_event_day = bool(data.get("is_event_day", False))
    model_type = str(data.get("model_type", "ml")).lower()

    prediction = predict_congestion(
        hour=hour,
        route_name=route_name,
        recent_passengers=recent_passengers,
        is_event_day=is_event_day,
        model_type=model_type,
    )
    return jsonify({"prediction": prediction})


@app.route("/api/scmaglev/ml/congestion/compare", methods=["POST"])
def scmaglev_compare_congestion():
    data = request.get_json() or {}
    hour = parse_int(data.get("hour", now_utc_naive().hour),
                     now_utc_naive().hour, 0, 23)
    route_name = str(data.get("route_name", "서울권 순환"))
    recent_passengers = parse_int(
        data.get("recent_passengers", 500), 500, 0, 5000)
    is_event_day = bool(data.get("is_event_day", False))
    comparison = compare_congestion_models(
        hour=hour,
        route_name=route_name,
        recent_passengers=recent_passengers,
        is_event_day=is_event_day,
    )
    return jsonify({"comparison": comparison})


def build_seat_recommendations(seats, reserved_seat_ids):
    available = [seat for seat in seats if seat.id not in reserved_seat_ids]
    if not available:
        return []

    def column_for_seat(seat_number):
        try:
            index = (int(seat_number) - 1) % 4
        except (TypeError, ValueError):
            index = 0
        return ["A", "B", "C", "D"][index]

    scored = []
    for seat in available:
        column = column_for_seat(seat.seat_number)
        reasons = []
        score = 0
        if column in {"A", "D"}:
            score += 12
            reasons.append("창측 좌석")
        reserved_in_car = len(
            [row for row in seats if row.car_number ==
                seat.car_number and row.id in reserved_seat_ids]
        )
        total_in_car = len(
            [row for row in seats if row.car_number == seat.car_number])
        free_ratio = (total_in_car - reserved_in_car) / max(total_in_car, 1)
        if free_ratio >= 0.55:
            score += 8
            reasons.append("혼잡 낮은 호차")
        try:
            seat_no = int(seat.seat_number)
            if 3 <= seat_no <= 12:
                score += 4
                reasons.append("진행방향 전방부")
        except (TypeError, ValueError):
            pass
        if not reasons:
            reasons.append("잔여 좌석 균형 배치")
        scored.append(
            {
                "seat_id": seat.id,
                "car_number": seat.car_number,
                "seat_number": seat.seat_number,
                "column": column,
                "side": "창측" if column in {"A", "D"} else "복도측",
                "direction": "정방향",
                "score": score,
                "reasons": reasons[:2],
                "reason_line": " · ".join(reasons[:2]),
            }
        )

    scored.sort(key=lambda row: (-row["score"],
                row["car_number"], str(row["seat_number"])))
    return scored[:3]


def compute_schedule_trip_progress(schedule, current_time=None):
    current_time = current_time or now_utc_naive()
    if not schedule:
        return 0.0, 0
    rollover_schedule_to_current_service_day(schedule, current_time)
    total_seconds = max(
        (schedule.arrival_time - schedule.departure_time).total_seconds(), 1)
    elapsed_seconds = (current_time - schedule.departure_time).total_seconds()
    if elapsed_seconds < 0:
        progress = 0.0
    else:
        progress = min(max(elapsed_seconds / total_seconds, 0.0), 1.0)
    remaining_minutes = max(
        int((schedule.arrival_time - current_time).total_seconds() // 60), 0)
    return progress, remaining_minutes


def build_recommendation_trust(ai_comparison):
    ensemble = ai_comparison.get("ensemble", {}) if ai_comparison else {}
    label = str(ensemble.get("label", "medium")).lower()
    confidence = float(ensemble.get("confidence", 0.9) or 0.9)
    accuracy_pct = min(99, max(88, round(confidence * 100)))
    delay_risk_map = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
    trust_lines = {
        "low": "관제 연동 · 지연 위험 낮음",
        "medium": "관제 연동 · 혼잡 보통",
        "high": "관제 연동 · 혼잡 주의",
    }
    return {
        "trust_line": trust_lines.get(label, trust_lines["medium"]),
        "trust_level": label,
        "prediction_accuracy_pct": accuracy_pct,
        "delay_risk_level": delay_risk_map.get(label, "MEDIUM"),
    }


def serialize_train_live_snapshot(train_id):
    if not train_id:
        return None
    location = TrainLocation.query.filter_by(train_id=train_id).first()
    train = db.session.get(Train, train_id)
    schedule = (
        Schedule.query.filter_by(train_id=train_id)
        .order_by(Schedule.departure_time.desc())
        .first()
    )
    departure_station = None
    arrival_station = None
    progress_pct = 0.0
    next_station = None
    if schedule:
        departure_station = db.session.get(
            Station, schedule.departure_station_id)
        arrival_station = db.session.get(Station, schedule.arrival_station_id)
        progress, _ = compute_schedule_trip_progress(schedule)
        progress_pct = round(progress * 100, 1)
        if location and location.next_station_id:
            next_station_row = db.session.get(
                Station, location.next_station_id)
            next_station = next_station_row.name if next_station_row else None
        elif arrival_station:
            next_station = arrival_station.name

    status_labels = {
        "normal": "정상 운행",
        "delayed": "지연",
        "stopped": "정차",
        "disrupted": "장애",
        "arrived": "도착",
    }

    if not location:
        if not schedule:
            return None
        return {
            "train_id": train_id,
            "train_number": train.train_number if train else None,
            "eta_minutes": None,
            "speed_kmh": None,
            "operation_status": "normal",
            "delay_label": "운행 예정",
            "congestion": "low",
            "progress_pct": progress_pct,
            "departure_station": departure_station.name if departure_station else None,
            "arrival_station": arrival_station.name if arrival_station else None,
            "next_station": next_station,
            "fault": None,
            "updated_at": None,
        }

    return {
        "train_id": train_id,
        "train_number": train.train_number if train else None,
        "eta_minutes": location.eta_minutes,
        "speed_kmh": location.speed_kmh,
        "operation_status": location.operation_status,
        "delay_label": status_labels.get(location.operation_status, location.operation_status),
        "congestion": location.congestion,
        "progress_pct": progress_pct,
        "departure_station": departure_station.name if departure_station else None,
        "arrival_station": arrival_station.name if arrival_station else None,
        "next_station": next_station,
        "fault": serialize_train_fault(location),
        "updated_at": location.updated_at.isoformat() if location.updated_at else None,
    }


def build_passenger_journey(reservation):
    schedule = db.session.get(Schedule, reservation.schedule_id)
    live = serialize_train_live_snapshot(
        schedule.train_id if schedule else None)
    current_time = now_utc_naive()
    events = []

    def append_event(phase, label, at, status):
        events.append(
            {
                "phase": phase,
                "label": label,
                "at": at.isoformat() if at else None,
                "status": status,
            }
        )

    append_event("booked", "예매접수", reservation.booked_at, "done")

    if reservation.status == "cancelled":
        append_event("cancelled", "예매 취소", reservation.cancelled_at, "done")
        return {
            "reservation_id": reservation.id,
            "current_phase": "cancelled",
            "current_label": "취소됨",
            "next_milestone": "—",
            "progress_pct": 0,
            "events": events,
            "live": live,
            "trust": None,
        }

    if reservation.payment_status == "pending":
        append_event("payment", "결제대기", reservation.booked_at, "active")
    elif reservation.payment_status == "paid":
        append_event("payment", "결제대기", reservation.booked_at, "done")
        append_event("confirmed", "예매 확정", reservation.paid_at, "done")
    elif reservation.payment_status in {"failed", "expired"}:
        append_event("payment", "결제대기", reservation.booked_at, "failed")
        append_event("payment_failed", "결제 실패",
                     reservation.failed_at, "active")

    current_phase = "payment_pending"
    next_milestone = "결제 완료"
    phase_labels = {
        "payment_pending": "결제 대기",
        "awaiting_departure": "출발 대기",
        "in_transit": "운행 중",
        "arrived": "도착 완료",
    }

    if reservation.payment_status == "paid" and schedule:
        departure_time = schedule.departure_time
        arrival_time = schedule.arrival_time
        if current_time < departure_time:
            current_phase = "awaiting_departure"
            schedule_data = serialize_schedule(schedule)
            next_milestone = f"{schedule_data.get('departure_station', '출발역')} 출발"
            boarding_at = departure_time - timedelta(minutes=15)
            boarding_status = "active" if current_time >= boarding_at else "upcoming"
            append_event("boarding", "탑승 준비", boarding_at, boarding_status)
        elif current_time < arrival_time and (not live or live.get("operation_status") != "arrived"):
            current_phase = "in_transit"
            next_milestone = f"{live.get('arrival_station') if live else '도착역'} 도착"
            append_event("in_transit", "운행 중", departure_time, "active")
        else:
            current_phase = "arrived"
            next_milestone = "여정 완료"
            append_event("arrived", "도착", arrival_time, "done")

    trust = None
    if schedule:
        candidate = build_schedule_recommendation_candidate(
            schedule, current_time)
        if candidate:
            trust = build_recommendation_trust(candidate["ai"])

    return {
        "reservation_id": reservation.id,
        "current_phase": current_phase,
        "current_label": phase_labels.get(current_phase, current_phase),
        "next_milestone": next_milestone,
        "progress_pct": live.get("progress_pct", 0) if live else 0,
        "events": events,
        "live": live,
        "trust": trust,
    }


def build_schedule_recommendation_candidate(schedule, now):
    if schedule.arrival_time < now:
        return None
    schedule_data = serialize_schedule(schedule)
    estimated_fare = calculate_estimated_fare(schedule)
    train = db.session.get(Train, schedule.train_id)
    route = db.session.get(
        Route, train.route_id) if train and train.route_id else None
    route_name = route.name if route else schedule_data.get(
        "train_name") or "전국 통합"
    comparison = compare_congestion_models(
        hour=schedule.departure_time.hour,
        route_name=route_name,
        recent_passengers=random.randint(250, 1400),
        is_event_day=False,
    )
    risk_score = float(comparison["ensemble"]["risk_score"])
    return {
        "schedule": {
            **schedule_data,
            "estimated_fare": estimated_fare,
        },
        "ai": comparison,
        "risk_score": risk_score,
    }


def pick_unique_recommendation(candidates, key_fn, used_ids):
    for candidate in sorted(candidates, key=key_fn):
        schedule_id = candidate["schedule"]["id"]
        if schedule_id not in used_ids:
            used_ids.add(schedule_id)
            return candidate
    return candidates[0] if candidates else None


def build_passenger_recommendation_picks(candidates):
    if not candidates:
        return []
    used_ids = set()
    picks = []

    fastest = pick_unique_recommendation(
        candidates,
        lambda row: (row["schedule"]["duration_minutes"], row["risk_score"]),
        used_ids,
    )
    if fastest:
        picks.append(
            {
                "tag": "fastest",
                "tag_label": "가장 빠름",
                "reason_line": f"소요 {fastest['schedule']['duration_minutes']}분 · 환승 없음",
                "schedule": fastest["schedule"],
                "ai": fastest["ai"],
                "trust": build_recommendation_trust(fastest["ai"]),
            }
        )

    cheapest = pick_unique_recommendation(
        candidates,
        lambda row: (row["schedule"]["estimated_fare"],
                     row["schedule"]["duration_minutes"]),
        used_ids,
    )
    if cheapest:
        picks.append(
            {
                "tag": "cheapest",
                "tag_label": "가장 저렴",
                "reason_line": f"예상 {cheapest['schedule']['estimated_fare']:,}원 · 직통 운행",
                "schedule": cheapest["schedule"],
                "ai": cheapest["ai"],
                "trust": build_recommendation_trust(cheapest["ai"]),
            }
        )

    comfortable = pick_unique_recommendation(
        candidates,
        lambda row: (row["risk_score"], row["schedule"]["duration_minutes"]),
        used_ids,
    )
    if comfortable:
        label = comfortable["ai"]["ensemble"]["label"]
        risk_text = "지연 위험 낮음" if label == "low" else "혼잡 보통" if label == "medium" else "혼잡 주의"
        picks.append(
            {
                "tag": "comfortable",
                "tag_label": "가장 쾌적",
                "reason_line": f"{risk_text} · 혼잡 {label.upper()}",
                "schedule": comfortable["schedule"],
                "ai": comfortable["ai"],
                "trust": build_recommendation_trust(comfortable["ai"]),
            }
        )
    return picks


def _candidate_from_search_row(search_row, now):
    schedule_id = search_row.get("booking_schedule_id") or search_row.get("id")
    schedule = db.session.get(Schedule, schedule_id)
    if not schedule:
        return None
    candidate = build_schedule_recommendation_candidate(schedule, now)
    if not candidate:
        return None
    candidate["schedule"].update(
        {
            key: search_row[key]
            for key in (
                "departure_station",
                "arrival_station",
                "departure_time",
                "arrival_time",
                "duration_minutes",
                "estimated_fare",
                "train_number",
                "train_name",
                "is_connection",
                "connection_legs",
                "transfer_station",
                "transfer_stations",
                "transfer_wait_minutes",
                "type_filter_relaxed",
            )
            if key in search_row
        }
    )
    return candidate


@app.route("/api/scmaglev/passenger/recommendations", methods=["GET"])
def scmaglev_passenger_recommendations():
    departure_name = request.args.get("departure", "").strip()
    arrival_name = request.args.get("arrival", "").strip()
    now = now_utc_naive()

    search_rows, _search_mode = _search_train_schedules(
        departure_name=departure_name,
        arrival_name=arrival_name,
        train_type_code="",
        departure_after_time=None,
        sort_by="departure",
    )
    candidates = []
    for search_row in search_rows:
        candidate = _candidate_from_search_row(search_row, now)
        if candidate:
            candidates.append(candidate)

    if not candidates and (departure_name or arrival_name):
        schedules = Schedule.query.order_by(
            Schedule.departure_time.asc()).all()
        for schedule in schedules:
            candidate = build_schedule_recommendation_candidate(schedule, now)
            if candidate:
                candidates.append(candidate)

    recommendations = build_passenger_recommendation_picks(candidates)
    recommendations, ai_meta = enhance_passenger_recommendations(
        recommendations,
        {"departure": departure_name, "arrival": arrival_name},
    )
    trust_values = [row["trust"]["prediction_accuracy_pct"]
                    for row in recommendations if row.get("trust")]
    trust_summary = {
        "control_linked": True,
        "prediction_accuracy_pct": int(sum(trust_values) / len(trust_values)) if trust_values else 92,
    }
    criteria = "가장 빠름 / 가장 저렴 / 가장 쾌적 3카드"
    if ai_meta.get("source") == "openai":
        criteria = f"GPT 추천 · {criteria}"
    return jsonify(
        {
            "recommendations": recommendations,
            "criteria": criteria,
            "trust_summary": trust_summary,
            "ai_source": ai_meta.get("source", "rule_based"),
            "ai_meta": ai_meta,
        }
    )


# ==========================================
# 회원가입
# ==========================================

@app.route("/api/auth/register", methods=["POST"])
def register():

    data = request.get_json()

    if not data:

        return jsonify({
            "message": "요청 데이터가 없습니다."
        }), 400

    username = data.get("username")
    email = data.get("email", "").strip().lower()
    password = data.get("password")
    role = data.get("role", "passenger")

    if not username or not email or not password:

        return jsonify({
            "message": "아이디, 이메일, 비밀번호를 입력해주세요."
        }), 400

    if len(username) < 3:

        return jsonify({
            "message": "아이디는 3자 이상이어야 합니다."
        }), 400

    if "@" not in email:

        return jsonify({
            "message": "올바른 이메일 주소를 입력해주세요."
        }), 400

    if len(password) < 4:

        return jsonify({
            "message": "비밀번호는 4자 이상이어야 합니다."
        }), 400

    existing_user = User.query.filter_by(
        username=username
    ).first()

    if existing_user:

        return jsonify({
            "message": "이미 존재하는 아이디입니다."
        }), 409

    existing_email = User.query.filter_by(
        email=email
    ).first()

    if existing_email:

        return jsonify({
            "message": "이미 사용 중인 이메일입니다."
        }), 409

    hashed_password = generate_password_hash(
        password
    )

    user = User(
        username=username,
        email=email,
        password=hashed_password,
        role=role if role in {"passenger",
                              "controller", "admin"} else "passenger",
    )

    db.session.add(user)
    try:
        commit_with_sqlite_retry()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"message": "이미 사용 중인 아이디 또는 이메일입니다."}), 409
    except OperationalError:
        db.session.rollback()
        return jsonify({"message": "일시적으로 데이터베이스가 바쁩니다. 잠시 후 다시 시도해주세요."}), 503

    return jsonify({
        "message": "회원가입 성공",
        "user": {
            "id": user.id,
            "username": user.username
        }
    }), 201


# ==========================================
# 아이디 / 비밀번호 찾기
# ==========================================

@app.route("/api/auth/recovery/request", methods=["POST"])
def request_recovery_code():

    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    purpose = data.get("purpose")

    if not email or purpose not in {"username", "password"}:

        return jsonify({
            "message": "이메일과 요청 종류를 확인해주세요."
        }), 400

    user = User.query.filter_by(email=email).first()

    if not user:
        return jsonify({
            "message": "가입된 이메일을 찾을 수 없습니다."
        }), 404

    VerificationCode.query.filter_by(
        email=email,
        purpose=purpose,
        is_used=False
    ).update({"is_used": True})

    code = "".join(secrets.choice("0123456789") for _ in range(6))
    verification = VerificationCode(
        email=email,
        purpose=purpose,
        code_hash=generate_password_hash(code),
        expires_at=datetime.now(UTC).replace(
            tzinfo=None) + timedelta(minutes=10)
    )

    try:
        send_recovery_email(email, code, purpose)
    except (OSError, RuntimeError, smtplib.SMTPException):
        db.session.rollback()
        app.logger.exception("인증번호 이메일 발송에 실패했습니다.")
        return jsonify({
            "message": "인증번호 이메일 발송에 실패했습니다. 잠시 후 다시 시도해주세요."
        }), 503

    db.session.add(verification)
    db.session.commit()

    response_data = {
        "message": "인증번호를 이메일로 전송했습니다."
    }

    # 기존 테스트는 실제 이메일 전송 없이 인증 흐름을 검증합니다.
    if app.config["TESTING"]:
        response_data["verification_code"] = code

    return jsonify(response_data)


@app.route("/api/auth/recovery/verify", methods=["POST"])
def verify_recovery_code():

    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    purpose = data.get("purpose")
    code = data.get("code", "")
    recovery = VerificationCode.query.filter_by(
        email=email,
        purpose=purpose,
        is_used=False
    ).order_by(VerificationCode.created_at.desc()).first()

    if (
        purpose not in {"username", "password"}
        or not recovery
        or recovery.expires_at < datetime.now(UTC).replace(tzinfo=None)
        or recovery.attempts >= 5
    ):
        return jsonify({
            "message": "인증번호가 올바르지 않거나 만료되었습니다."
        }), 400

    if not check_password_hash(recovery.code_hash, code):
        recovery.attempts += 1
        if recovery.attempts >= 5:
            recovery.is_used = True
        db.session.commit()
        return jsonify({
            "message": "인증번호가 올바르지 않거나 만료되었습니다."
        }), 400

    recovery.is_used = True
    recovery.verified_token = secrets.token_urlsafe(32)
    db.session.commit()

    return jsonify({
        "message": "이메일 인증이 완료되었습니다.",
        "recovery_token": recovery.verified_token
    })


def valid_recovery_request(email, purpose, recovery_token):

    recovery = VerificationCode.query.filter_by(
        email=email,
        purpose=purpose,
        is_used=True
    ).order_by(VerificationCode.created_at.desc()).first()

    return (
        recovery
        and recovery.expires_at >= datetime.now(UTC).replace(tzinfo=None)
        and recovery.verified_token
        and secrets.compare_digest(recovery.verified_token, recovery_token)
    )


@app.route("/api/auth/recover-username", methods=["POST"])
def recover_username():

    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    recovery_token = data.get("recovery_token", "")

    if not valid_recovery_request(email, "username", recovery_token):

        return jsonify({
            "message": "이메일 인증이 필요합니다."
        }), 403

    user = User.query.filter_by(email=email).first()

    if not user:

        return jsonify({
            "message": "가입 정보를 찾을 수 없습니다."
        }), 404

    return jsonify({
        "message": "아이디를 찾았습니다.",
        "username": user.username
    })


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():

    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    recovery_token = data.get("recovery_token", "")
    password = data.get("password", "")

    if not valid_recovery_request(email, "password", recovery_token):

        return jsonify({
            "message": "이메일 인증이 필요합니다."
        }), 403

    if len(password) < 4:

        return jsonify({
            "message": "비밀번호는 4자 이상이어야 합니다."
        }), 400

    user = User.query.filter_by(email=email).first()

    if not user:

        return jsonify({
            "message": "가입 정보를 찾을 수 없습니다."
        }), 404

    user.password = generate_password_hash(password)
    db.session.commit()
    return jsonify({
        "message": "비밀번호를 변경했습니다. 새 비밀번호로 로그인해주세요."
    })


# ==========================================
# 로그인
# ==========================================

@app.route("/api/auth/login", methods=["POST"])
def login():

    data = request.get_json()

    if not data:

        return jsonify({
            "message": "요청 데이터가 없습니다."
        }), 400

    username = data.get("username")
    password = data.get("password")

    user = User.query.filter_by(
        username=username
    ).first()

    if not user:

        return jsonify({
            "message": "아이디 또는 비밀번호가 올바르지 않습니다."
        }), 401

    password_correct = check_password_hash(
        user.password,
        password
    )

    if not password_correct:

        return jsonify({
            "message": "아이디 또는 비밀번호가 올바르지 않습니다."
        }), 401

    access_token = create_access_token(
        identity=str(user.id)
    )

    return jsonify({
        "message": "로그인 성공",
        "access_token": access_token
    })


# ==========================================
# 회원 정보 관리
# ==========================================

@app.route("/api/users/me", methods=["GET"])
@jwt_required()
def get_current_user():

    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)

    if not user:
        return jsonify({
            "message": "사용자를 찾을 수 없습니다."
        }), 404

    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user_role_or_default(user),
        "created_at": user.created_at
    })


@app.route("/api/users/me", methods=["PUT"])
@jwt_required()
def update_current_user():

    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)
    data = request.get_json() or {}

    if not user:
        return jsonify({
            "message": "사용자를 찾을 수 없습니다."
        }), 404

    if not any(field in data for field in ("username", "email", "password")):
        return jsonify({
            "message": "수정할 회원 정보가 없습니다."
        }), 400

    if "username" in data:
        username = str(data["username"]).strip()

        if len(username) < 3:
            return jsonify({
                "message": "아이디는 3자 이상이어야 합니다."
            }), 400

        existing_user = User.query.filter(
            User.username == username,
            User.id != user_id
        ).first()

        if existing_user:
            return jsonify({
                "message": "이미 존재하는 아이디입니다."
            }), 409

        user.username = username

    if "email" in data:
        email = str(data["email"]).strip().lower()

        if "@" not in email:
            return jsonify({
                "message": "올바른 이메일 주소를 입력해주세요."
            }), 400

        existing_email = User.query.filter(
            User.email == email,
            User.id != user_id
        ).first()

        if existing_email:
            return jsonify({
                "message": "이미 사용 중인 이메일입니다."
            }), 409

        user.email = email

    if "password" in data:
        password = str(data["password"])

        if len(password) < 4:
            return jsonify({
                "message": "비밀번호는 4자 이상이어야 합니다."
            }), 400

        user.password = generate_password_hash(password)

    db.session.commit()

    return jsonify({
        "message": "회원 정보를 수정했습니다.",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user_role_or_default(user),
            "created_at": user.created_at
        }
    })


@app.route("/api/users/me", methods=["DELETE"])
@jwt_required()
def delete_current_user():

    user_id = int(get_jwt_identity())
    user = db.session.get(User, user_id)

    if not user:
        return jsonify({
            "message": "사용자를 찾을 수 없습니다."
        }), 404

    db.session.delete(user)
    db.session.commit()

    return jsonify({
        "message": "회원탈퇴가 완료되었습니다."
    })


# ==========================================
# 게시글 전체 조회
# 검색 + 페이지네이션
# ==========================================

@app.route("/api/posts", methods=["GET"])
def get_posts():

    keyword = request.args.get(
        "keyword",
        ""
    )

    page = request.args.get(
        "page",
        1,
        type=int
    )

    per_page = request.args.get(
        "per_page",
        10,
        type=int
    )

    page = max(page, 1)

    if per_page < 1:
        per_page = 10

    per_page = min(per_page, 100)

    query = Post.query

    if keyword:

        query = query.filter(
            or_(
                Post.title.contains(keyword),
                Post.content.contains(keyword)
            )
        )

    pagination = query.order_by(
        Post.created_at.desc()
    ).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    posts = []

    for post in pagination.items:

        posts.append({
            "id": post.id,
            "title": post.title,
            "content": post.content,
            "user_id": post.user_id,
            "author": post.author.username,
            "created_at": post.created_at
        })

    return jsonify({
        "posts": posts,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages
        }
    })


# ==========================================
# 게시글 하나 조회
# ==========================================

@app.route("/api/posts/<int:post_id>", methods=["GET"])
def get_post(post_id):

    post = db.session.get(
        Post,
        post_id
    )

    if not post:

        return jsonify({
            "message": "게시글을 찾을 수 없습니다."
        }), 404

    comments = []

    for comment in post.comments:

        comments.append({
            "id": comment.id,
            "content": comment.content,
            "user_id": comment.user_id,
            "author": comment.author.username,
            "created_at": comment.created_at
        })

    return jsonify({
        "id": post.id,
        "title": post.title,
        "content": post.content,
        "user_id": post.user_id,
        "author": post.author.username,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "comments": comments
    })


# ==========================================
# 게시글 작성
# ==========================================

@app.route("/api/posts", methods=["POST"])
@jwt_required()
def create_post():

    user_id = int(
        get_jwt_identity()
    )

    data = request.get_json()

    if not data:

        return jsonify({
            "message": "요청 데이터가 없습니다."
        }), 400

    title = data.get("title")
    content = data.get("content")

    if not title or not content:

        return jsonify({
            "message": "제목과 내용을 입력해주세요."
        }), 400

    post = Post(
        title=title,
        content=content,
        user_id=user_id
    )

    db.session.add(post)
    db.session.commit()

    return jsonify({
        "message": "게시글 작성 성공",
        "post": {
            "id": post.id,
            "title": post.title,
            "content": post.content
        }
    }), 201


# ==========================================
# 게시글 수정
# ==========================================

@app.route("/api/posts/<int:post_id>", methods=["PUT"])
@jwt_required()
def update_post(post_id):

    user_id = int(
        get_jwt_identity()
    )

    post = db.session.get(
        Post,
        post_id
    )

    if not post:

        return jsonify({
            "message": "게시글을 찾을 수 없습니다."
        }), 404

    if post.user_id != user_id:

        return jsonify({
            "message": "수정 권한이 없습니다."
        }), 403

    data = request.get_json()

    if not data:

        return jsonify({
            "message": "수정할 데이터가 없습니다."
        }), 400

    if "title" in data:
        post.title = data["title"]

    if "content" in data:
        post.content = data["content"]

    db.session.commit()

    return jsonify({
        "message": "게시글 수정 성공"
    })


# ==========================================
# 게시글 삭제
# ==========================================

@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
@jwt_required()
def delete_post(post_id):

    user_id = int(
        get_jwt_identity()
    )

    post = db.session.get(
        Post,
        post_id
    )

    if not post:

        return jsonify({
            "message": "게시글을 찾을 수 없습니다."
        }), 404

    if post.user_id != user_id:

        return jsonify({
            "message": "삭제 권한이 없습니다."
        }), 403

    db.session.delete(post)
    db.session.commit()

    return jsonify({
        "message": "게시글 삭제 성공"
    })


# ==========================================
# 댓글 조회
# ==========================================

@app.route(
    "/api/posts/<int:post_id>/comments",
    methods=["GET"]
)
def get_comments(post_id):

    post = db.session.get(
        Post,
        post_id
    )

    if not post:

        return jsonify({
            "message": "게시글을 찾을 수 없습니다."
        }), 404

    comments = []

    for comment in post.comments:

        comments.append({
            "id": comment.id,
            "content": comment.content,
            "user_id": comment.user_id,
            "author": comment.author.username,
            "created_at": comment.created_at
        })

    return jsonify({
        "comments": comments
    })


# ==========================================
# 댓글 작성
# ==========================================

@app.route(
    "/api/posts/<int:post_id>/comments",
    methods=["POST"]
)
@jwt_required()
def create_comment(post_id):

    user_id = int(
        get_jwt_identity()
    )

    post = db.session.get(
        Post,
        post_id
    )

    if not post:

        return jsonify({
            "message": "게시글을 찾을 수 없습니다."
        }), 404

    data = request.get_json()

    if not data:

        return jsonify({
            "message": "요청 데이터가 없습니다."
        }), 400

    content = data.get("content")

    if not content:

        return jsonify({
            "message": "댓글 내용을 입력해주세요."
        }), 400

    comment = Comment(
        content=content,
        user_id=user_id,
        post_id=post_id
    )

    db.session.add(comment)
    db.session.commit()

    return jsonify({
        "message": "댓글 작성 성공",
        "comment": {
            "id": comment.id,
            "content": comment.content
        }
    }), 201


# ==========================================
# 댓글 삭제
# ==========================================

@app.route(
    "/api/comments/<int:comment_id>",
    methods=["DELETE"]
)
@jwt_required()
def delete_comment(comment_id):

    user_id = int(
        get_jwt_identity()
    )

    comment = db.session.get(
        Comment,
        comment_id
    )

    if not comment:

        return jsonify({
            "message": "댓글을 찾을 수 없습니다."
        }), 404

    if comment.user_id != user_id:

        return jsonify({
            "message": "댓글 삭제 권한이 없습니다."
        }), 403

    db.session.delete(comment)
    db.session.commit()

    return jsonify({
        "message": "댓글 삭제 성공"
    })


# ==========================================
# DB 테이블 생성
# ==========================================

def add_email_column_for_existing_database():

    inspector = inspect(db.engine)

    if "users" not in inspector.get_table_names():
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("users")
    }

    if "email" not in columns:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN email VARCHAR(120)")
        )
        db.session.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_users_email ON users (email)"
            )
        )
        db.session.commit()


def _run_heavy_startup_tasks():
    try:
        with app.app_context():
            bootstrap_direct_fleet_live_ops()
            ensure_demo_fault_trains()
            seed_default_operator_account()
            print("[SCMAGLEV] DB 후처리 완료.", flush=True)
    except Exception as exc:
        app.logger.exception("SCMAGLEV DB 후처리 실패")
        _startup_state["error"] = str(exc)


def initialize_scmaglev_data():
    global _startup_init_started

    with _startup_lock:
        if _startup_init_started:
            return
        _startup_init_started = True

    try:
        with app.app_context():
            print("[SCMAGLEV] DB 테이블 생성/마이그레이션 중...", flush=True)
            db.create_all()
            configure_sqlite()
            add_email_column_for_existing_database()
            ensure_role_column_for_existing_database()
            ensure_train_location_fault_columns()
            ensure_reservation_columns_for_existing_database()
            print("[SCMAGLEV] 역·노선·편성 시드 중...", flush=True)
            seed_scmaglev_data()
            seed_direct_national_trains()
            prune_dashboard_tracking()
            ensure_direct_train_locations()
            print("[SCMAGLEV] 기본 DB 준비 완료 · API 사용 가능", flush=True)
        start_location_simulator()
        if os.environ.get("PYTEST_CURRENT_TEST"):
            _run_heavy_startup_tasks()
        else:
            threading.Thread(target=_run_heavy_startup_tasks, daemon=True).start()
    except Exception as exc:
        app.logger.exception("SCMAGLEV 데이터 초기화 실패")
        _startup_state["error"] = str(exc)
        print(f"[SCMAGLEV] DB 초기화 오류: {exc}", flush=True)
    finally:
        _startup_state["ready"] = True


def _should_defer_startup():
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if os.environ.get("SCMAGLEV_DEFER_INIT") == "0":
        return False
    return bool(os.environ.get("PORT"))


if _should_defer_startup():
    threading.Thread(target=initialize_scmaglev_data, daemon=True).start()
else:
    initialize_scmaglev_data()


# ==========================================
# 서버 실행
# ==========================================

if __name__ == "__main__":

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5001"))
    print(f"[SCMAGLEV] 서버 시작: http://{host}:{port}/", flush=True)
    print(f"[SCMAGLEV] 관제 대시보드: http://{host}:{port}/dashboard", flush=True)
    print(format_openai_boot_message(), flush=True)

    if socketio is not None:
        socketio.run(
            app,
            host=host,
            port=port,
            debug=False,
            allow_unsafe_werkzeug=True,
        )
    else:
        app.run(
            host=host,
            port=port,
            debug=False
        )
