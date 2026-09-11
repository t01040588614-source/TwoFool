"""
SCMAGLEV Streamlit 데모
=======================

Streamlit Community Cloud(share.streamlit.io)에 올리기 위한 버전입니다.

원본 앱(`book/app.py`)은 Flask + Flask-SocketIO(실시간 웹소켓) + JWT 로그인 +
토스페이먼츠 연동으로 되어 있어서, 파이썬 스크립트 하나만 실행하는 Streamlit
구조에는 그대로 올라가지 않습니다. 그래서:

- 실시간 웹소켓 대신: 매 새로고침(또는 자동 새로고침)마다 다시 조회
- JWT 로그인 대신: 접속 브라우저 세션마다 임시 게스트 계정을 하나 만들어 사용
- 토스 결제 대신: 예매 즉시 모의결제 완료 처리 (원본도 TOSS_PAYMENTS_MOCK_ONLY=1과 동일)

로 단순화했습니다. 데이터/요금 계산/좌석 추천/열차 검색/실시간 위치 계산 로직은
전부 `book/app.py`에 있는 실제 함수를 그대로 가져다 씁니다 (재구현 X) — 즉
승객용 예매 화면과 관제 대시보드 요약은 원본 앱과 같은 로직으로 동작합니다.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

# --- book/ 폴더를 파이썬 경로에 추가해서 원본 백엔드 코드를 그대로 재사용 ---
APP_DIR = Path(__file__).resolve().parent
BOOK_DIR = APP_DIR.parent / "book"
if str(BOOK_DIR) not in sys.path:
    sys.path.insert(0, str(BOOK_DIR))

# book/app.py를 import 하기 전에 필요한 환경변수를 먼저 정해줘야 합니다.
# (book/config.py가 import 시점에 JWT_SECRET_KEY 존재 여부를 검사합니다)
DEMO_DB_PATH = APP_DIR / "streamlit_demo.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DEMO_DB_PATH}")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("TOSS_PAYMENTS_MOCK_ONLY", "1")
os.environ.setdefault("OPENAI_ENABLED", "0")
os.environ.setdefault("SCMAGLEV_MAX_TRACKED_TRAINS", "150")

import streamlit as st  # noqa: E402

# book/app.py의 `with app.app_context(): ...` 블록이 import 시점에 테이블 생성 +
# 데이터 시드까지 전부 끝내줍니다. Python은 모듈을 프로세스당 한 번만 import
# 하므로, Streamlit이 스크립트를 다시 실행해도 이 시드 작업은 한 번만 일어납니다.
import app as backend  # noqa: E402
from sqlalchemy import or_  # noqa: E402

st.set_page_config(
    page_title="SCMAGLEV 승차권예매 (Streamlit 데모)",
    page_icon="🚄",
    layout="wide",
)

SEAT_TYPE_LABELS = {
    "general": "일반",
    "senior": "경로우대",
    "pregnant": "임산부",
    "accessible": "교통약자",
    "wheelchair": "휠체어",
}

STATUS_LABELS = {
    "normal": "정상",
    "arrived": "도착",
    "stopped": "정차",
    "disrupted": "장애",
    "delayed": "지연",
}

STATUS_COLORS = {
    "normal": [34, 197, 94],
    "arrived": [148, 163, 184],
    "stopped": [250, 204, 21],
    "disrupted": [239, 68, 68],
    "delayed": [249, 115, 22],
}


# --------------------------------------------------------------------------
# 게스트 계정 (JWT 로그인 대신 브라우저 세션마다 임시 계정 하나)
# --------------------------------------------------------------------------
def get_or_create_guest_user():
    if "guest_user_id" in st.session_state:
        user = backend.db.session.get(backend.User, st.session_state["guest_user_id"])
        if user:
            return user

    username = f"guest_{secrets.token_hex(5)}"
    user = backend.User(
        username=username,
        email=f"{username}@streamlit.demo",
        password=backend.generate_password_hash(secrets.token_urlsafe(16)),
        role="passenger",
    )
    backend.db.session.add(user)
    backend.db.session.commit()
    st.session_state["guest_user_id"] = user.id
    st.session_state["guest_display_name"] = st.session_state.get(
        "guest_display_name", "승객"
    )
    return user


# --------------------------------------------------------------------------
# 승차권 예매 탭
# --------------------------------------------------------------------------
def render_booking_tab():
    with backend.app.app_context():
        user = get_or_create_guest_user()
        stations = backend.Station.query.order_by(backend.Station.name.asc()).all()
        station_names = [s.name for s in stations]

        st.caption(
            f"게스트 계정으로 접속 중입니다 · `{user.username}` "
            "(이 브라우저 세션 동안만 유지되며, 실제 결제는 발생하지 않습니다 · 모의결제)"
        )

        st.subheader("🔍 열차 검색")
        default_dep = "서울" if "서울" in station_names else (station_names[0] if station_names else "")
        default_arr = "부산" if "부산" in station_names else (
            station_names[1] if len(station_names) > 1 else ""
        )
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            departure = st.selectbox(
                "출발역", station_names,
                index=station_names.index(default_dep) if default_dep in station_names else 0,
            )
        with col2:
            arrival = st.selectbox(
                "도착역", station_names,
                index=station_names.index(default_arr) if default_arr in station_names else 0,
            )
        with col3:
            st.write("")
            st.write("")
            search_clicked = st.button("검색", type="primary", use_container_width=True)

        if departure == arrival:
            st.warning("출발역과 도착역이 같습니다.")

        if search_clicked and departure != arrival:
            with st.spinner("운행 일정을 검색하는 중..."):
                results, search_mode = backend._search_train_schedules(
                    departure_name=departure,
                    arrival_name=arrival,
                    departure_after_time=None,
                    sort_by="departure",
                )
            st.session_state["search_results"] = results
            st.session_state["search_mode"] = search_mode
            st.session_state["search_route"] = (departure, arrival)
            st.session_state.pop("selected_schedule_id", None)

        results = st.session_state.get("search_results")
        if results is not None:
            route = st.session_state.get("search_route", (departure, arrival))
            mode_label = "환승 포함" if st.session_state.get("search_mode") == "transfer" else "직통"
            st.markdown(f"**{route[0]} → {route[1]}** 검색 결과 ({mode_label}, {len(results)}건)")

            if not results:
                st.info("표시할 운행이 없습니다. 다른 역 조합으로 검색해보세요.")
            for row in results:
                dep_time = str(row.get("departure_time", "")).replace("T", " ")[:16]
                arr_time = str(row.get("arrival_time", "")).replace("T", " ")[:16]
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([2, 3, 2, 2])
                    c1.markdown(f"**{row.get('train_number')}**\n\n{row.get('train_name')}")
                    c2.markdown(
                        f"{row.get('departure_station')} `{dep_time}`\n\n"
                        f"→ {row.get('arrival_station')} `{arr_time}`"
                    )
                    c3.markdown(f"소요 {row.get('duration_minutes')}분")
                    c4.markdown(f"**{int(row.get('estimated_fare') or 0):,}원~**")
                    if st.button("이 열차 선택", key=f"pick_{row['id']}"):
                        st.session_state["selected_schedule_id"] = row["id"]
                        st.rerun()

        selected_schedule_id = st.session_state.get("selected_schedule_id")
        if selected_schedule_id:
            render_seat_picker(selected_schedule_id, user)

        st.divider()
        render_my_reservations(user)


def render_seat_picker(schedule_id, user):
    schedule = backend.db.session.get(backend.Schedule, schedule_id)
    if not schedule:
        st.error("선택한 운행 일정을 더 이상 찾을 수 없습니다. 다시 검색해주세요.")
        st.session_state.pop("selected_schedule_id", None)
        return

    st.subheader("💺 좌석 선택")
    now = backend.now_utc_naive()
    reserved_seat_ids = {
        row.seat_id
        for row in backend.Reservation.query.filter(
            backend.Reservation.schedule_id == schedule_id,
            or_(
                backend.Reservation.status == "booked",
                (backend.Reservation.status == "pending_payment")
                & (backend.Reservation.payment_due_at >= now),
            ),
        ).all()
    }
    seats = (
        backend.Seat.query.filter_by(train_id=schedule.train_id, is_active=True)
        .order_by(backend.Seat.car_number.asc(), backend.Seat.seat_number.asc())
        .all()
    )
    available_seats = [s for s in seats if s.id not in reserved_seat_ids]
    recommended_ids = {
        row["seat_id"]
        for row in backend.build_seat_recommendations(seats, reserved_seat_ids)[:5]
    }

    if not available_seats:
        st.warning("이 열차는 현재 예약 가능한 좌석이 없습니다.")
        return

    col1, col2 = st.columns([2, 1])
    with col1:
        seat_type = st.selectbox(
            "좌석 타입",
            list(SEAT_TYPE_LABELS.keys()),
            format_func=lambda key: SEAT_TYPE_LABELS[key],
        )
        seat_options = {
            f"{s.car_number}호차 {s.seat_number}{'  ⭐추천' if s.id in recommended_ids else ''}": s.id
            for s in available_seats
        }
        seat_label = st.selectbox("좌석", list(seat_options.keys()))
        seat_id = seat_options[seat_label]
    with col2:
        fare = backend.calculate_estimated_fare(schedule, seat_type=seat_type)
        st.metric("예상 요금", f"{fare:,}원")

    passenger_name = st.text_input(
        "예매자 이름 (선택)", value=st.session_state.get("guest_display_name", "")
    )

    confirm_col, cancel_col = st.columns([1, 1])
    with confirm_col:
        if st.button("✅ 예매 확정 (모의결제)", type="primary", use_container_width=True):
            st.session_state["guest_display_name"] = passenger_name or "승객"
            existing = backend.Reservation.query.filter(
                backend.Reservation.schedule_id == schedule_id,
                backend.Reservation.seat_id == seat_id,
                or_(
                    backend.Reservation.status == "booked",
                    (backend.Reservation.status == "pending_payment")
                    & (backend.Reservation.payment_due_at >= backend.now_utc_naive()),
                ),
            ).first()
            if existing:
                st.error("방금 다른 승객이 먼저 예매한 좌석입니다. 다른 좌석을 선택해주세요.")
            else:
                reservation = backend.Reservation(
                    user_id=user.id,
                    schedule_id=schedule_id,
                    seat_id=seat_id,
                    status="booked",
                    payment_status="paid",
                    paid_at=backend.now_utc_naive(),
                )
                backend.db.session.add(reservation)
                try:
                    backend.db.session.flush()
                    backend.log_operation_event(
                        event_type="RESERVATION_CREATED",
                        message=f"[Streamlit 데모] 예약 생성: {reservation.id}",
                        severity="info",
                        source="passenger",
                        train_id=schedule.train_id,
                        reservation_id=reservation.id,
                        user_id=user.id,
                        payload={"schedule_id": schedule_id, "seat_id": seat_id, "seat_type": seat_type},
                    )
                    backend.db.session.commit()
                except backend.IntegrityError:
                    backend.db.session.rollback()
                    st.error("이미 예매된 좌석입니다.")
                else:
                    st.success(f"예매가 완료되었습니다! (예약번호 #{reservation.id})")
                    st.session_state.pop("selected_schedule_id", None)
                    st.session_state.pop("search_results", None)
                    st.rerun()
    with cancel_col:
        if st.button("선택 취소", use_container_width=True):
            st.session_state.pop("selected_schedule_id", None)
            st.rerun()


def render_my_reservations(user):
    st.subheader("🎟️ 내 예약 (이 세션)")
    reservations = (
        backend.Reservation.query.filter_by(user_id=user.id)
        .order_by(backend.Reservation.booked_at.desc())
        .all()
    )
    if not reservations:
        st.caption("아직 예약이 없습니다.")
        return

    for reservation in reservations:
        schedule = backend.db.session.get(backend.Schedule, reservation.schedule_id)
        seat = backend.db.session.get(backend.Seat, reservation.seat_id)
        if not schedule or not seat:
            continue
        dep_station = backend.db.session.get(backend.Station, schedule.departure_station_id)
        arr_station = backend.db.session.get(backend.Station, schedule.arrival_station_id)
        train = backend.db.session.get(backend.Train, schedule.train_id)
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 1])
            c1.markdown(
                f"**{train.train_number if train else '-'}** · "
                f"{dep_station.name if dep_station else '?'} → {arr_station.name if arr_station else '?'} · "
                f"{seat.car_number}호차 {seat.seat_number}"
            )
            status_map = {"booked": "✅ 예약완료", "cancelled": "❌ 취소됨", "pending_payment": "⏳ 결제대기"}
            c2.markdown(status_map.get(reservation.status, reservation.status))
            if reservation.status == "booked":
                if c3.button("취소", key=f"cancel_{reservation.id}"):
                    reservation.status = "cancelled"
                    reservation.cancelled_at = backend.now_utc_naive()
                    backend.db.session.commit()
                    st.rerun()


# --------------------------------------------------------------------------
# 관제 대시보드 탭 (단순화 — 실시간 웹소켓 대신 새로고침 기반)
# --------------------------------------------------------------------------
def render_dashboard_tab():
    with backend.app.app_context():
        st.caption(
            "원본 대시보드는 SocketIO로 실시간 갱신되지만, 여기서는 새로고침할 때마다 "
            "다시 계산합니다. 위치·지연·혼잡도 계산 로직은 원본과 동일합니다."
        )
        auto_refresh = st.checkbox("5초마다 자동 새로고침", value=False)

        locations = backend.TrainLocation.query.order_by(backend.TrainLocation.train_id.asc()).all()
        rows = []
        for location in locations:
            item = backend._update_train_location(location)
            if item:
                rows.append(item)
        backend.db.session.commit()

        in_service = sum(1 for r in rows if r["is_in_service"])
        waiting = sum(1 for r in rows if r["is_waiting_departure"])
        arrived = sum(1 for r in rows if r["operation_status"] == "arrived")
        disrupted = sum(1 for r in rows if r["operation_status"] in ("disrupted", "stopped"))

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("추적 편성", f"{len(rows)}대")
        m2.metric("운행중", f"{in_service}대")
        m3.metric("출발대기", f"{waiting}대")
        m4.metric("도착", f"{arrived}대")
        m5.metric("지연/장애", f"{disrupted}대")

        map_rows = [r for r in rows if r["is_in_service"]]
        if map_rows:
            try:
                import pydeck as pdk

                layer_data = [
                    {
                        "position": [r["longitude"], r["latitude"]],
                        "color": STATUS_COLORS.get(r["operation_status"], [59, 130, 246]),
                        "train_number": r["train_number"],
                        "route": f"{r['departure_station']} → {r['arrival_station']}",
                        "status": STATUS_LABELS.get(r["operation_status"], r["operation_status"]),
                    }
                    for r in map_rows
                ]
                layer = pdk.Layer(
                    "ScatterplotLayer",
                    data=layer_data,
                    get_position="position",
                    get_fill_color="color",
                    get_radius=6000,
                    pickable=True,
                )
                view_state = pdk.ViewState(latitude=36.3, longitude=127.8, zoom=6.2)
                st.pydeck_chart(
                    pdk.Deck(
                        layers=[layer],
                        initial_view_state=view_state,
                        map_style=None,
                        tooltip={"text": "{train_number}\n{route}\n{status}"},
                    )
                )
            except ImportError:
                st.info("pydeck이 설치되어 있지 않아 지도를 표시할 수 없습니다.")
        else:
            st.info("현재 운행 중인 열차가 없습니다.")

        problem_rows = [r for r in rows if r["operation_status"] in ("disrupted", "stopped")]
        if problem_rows:
            st.subheader("⚠️ 지연/장애 열차")
            st.dataframe(
                [
                    {
                        "열차번호": r["train_number"],
                        "구간": f"{r['departure_station']} → {r['arrival_station']}",
                        "상태": STATUS_LABELS.get(r["operation_status"], r["operation_status"]),
                        "원인": (r.get("fault") or {}).get("label", "-"),
                        "ETA(분)": r["eta_minutes"],
                    }
                    for r in problem_rows
                ],
                use_container_width=True,
                hide_index=True,
            )

        if auto_refresh:
            import time

            time.sleep(5)
            st.rerun()


# --------------------------------------------------------------------------
# 메인
# --------------------------------------------------------------------------
st.title("🚄 SCMAGLEV 승차권예매 (Streamlit 데모)")
st.caption(
    "원본 Flask 프로젝트를 Streamlit Community Cloud에 올리기 위해 단순화한 버전입니다. "
    "실제 서비스용 전체 기능(실시간 관제, 로그인, 결제)은 원본 Render 배포판을 이용해주세요."
)

tab_booking, tab_dashboard = st.tabs(["🎫 승차권 예매", "🛰️ 관제 대시보드 (요약)"])
with tab_booking:
    render_booking_tab()
with tab_dashboard:
    render_dashboard_tab()
