from datetime import UTC, datetime

from extensions import db


class User(db.Model):

    __tablename__ = "users"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    username = db.Column(
        db.String(50),
        unique=True,
        nullable=False
    )

    email = db.Column(
        db.String(120),
        unique=True,
        nullable=False
    )

    password = db.Column(
        db.String(255),
        nullable=False
    )

    role = db.Column(
        db.String(20),
        nullable=False,
        default="passenger"
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )

    posts = db.relationship(
        "Post",
        backref="author",
        lazy=True,
        cascade="all, delete-orphan"
    )

    comments = db.relationship(
        "Comment",
        backref="author",
        lazy=True,
        cascade="all, delete-orphan"
    )


class VerificationCode(db.Model):

    __tablename__ = "verification_codes"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    purpose = db.Column(db.String(20), nullable=False, index=True)
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    is_used = db.Column(db.Boolean, nullable=False, default=False)
    verified_token = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )


class Post(db.Model):

    __tablename__ = "posts"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    title = db.Column(
        db.String(200),
        nullable=False
    )

    content = db.Column(
        db.Text,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )

    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC)
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False
    )

    comments = db.relationship(
        "Comment",
        backref="post",
        lazy=True,
        cascade="all, delete-orphan"
    )


class Comment(db.Model):

    __tablename__ = "comments"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    content = db.Column(
        db.Text,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False
    )

    post_id = db.Column(
        db.Integer,
        db.ForeignKey("posts.id"),
        nullable=False
    )


class TrainType(db.Model):

    __tablename__ = "train_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    max_speed_kmh = db.Column(db.Integer, nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )


class Station(db.Model):

    __tablename__ = "stations"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )


class Route(db.Model):

    __tablename__ = "routes"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )


class RouteStation(db.Model):

    __tablename__ = "route_stations"

    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey("routes.id"), nullable=False, index=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    sequence = db.Column(db.Integer, nullable=False)
    dwell_seconds = db.Column(db.Integer, nullable=False, default=120)


class Train(db.Model):

    __tablename__ = "trains"

    id = db.Column(db.Integer, primary_key=True)
    train_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    train_type_id = db.Column(db.Integer, db.ForeignKey("train_types.id"), nullable=False)
    route_id = db.Column(db.Integer, db.ForeignKey("routes.id"), nullable=False)
    cars = db.Column(db.Integer, nullable=False, default=8)
    status = db.Column(db.String(20), nullable=False, default="normal")
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )


class Schedule(db.Model):

    __tablename__ = "schedules"

    id = db.Column(db.Integer, primary_key=True)
    train_id = db.Column(db.Integer, db.ForeignKey("trains.id"), nullable=False, index=True)
    departure_station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    arrival_station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    departure_time = db.Column(db.DateTime, nullable=False, index=True)
    arrival_time = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )


class Seat(db.Model):

    __tablename__ = "seats"

    id = db.Column(db.Integer, primary_key=True)
    train_id = db.Column(db.Integer, db.ForeignKey("trains.id"), nullable=False, index=True)
    car_number = db.Column(db.Integer, nullable=False)
    seat_number = db.Column(db.String(10), nullable=False)
    seat_type = db.Column(db.String(30), nullable=False, default="general")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )


class Reservation(db.Model):

    __tablename__ = "reservations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("schedules.id"), nullable=False, index=True)
    seat_id = db.Column(db.Integer, db.ForeignKey("seats.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="booked")
    payment_status = db.Column(db.String(20), nullable=False, default="pending")
    payment_token = db.Column(db.String(64), nullable=True, index=True)
    payment_due_at = db.Column(db.DateTime, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    failed_at = db.Column(db.DateTime, nullable=True)
    fail_reason = db.Column(db.String(255), nullable=True)
    booked_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC)
    )
    cancelled_at = db.Column(db.DateTime, nullable=True)


class TrainLocation(db.Model):

    __tablename__ = "train_locations"

    id = db.Column(db.Integer, primary_key=True)
    train_id = db.Column(db.Integer, db.ForeignKey("trains.id"), nullable=False, unique=True, index=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    speed_kmh = db.Column(db.Float, nullable=False, default=0)
    direction_deg = db.Column(db.Float, nullable=False, default=0)
    next_station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=True)
    eta_minutes = db.Column(db.Integer, nullable=False, default=0)
    operation_status = db.Column(db.String(20), nullable=False, default="normal")
    fault_cause_code = db.Column(db.String(40), nullable=True, index=True)
    fault_cause_label = db.Column(db.String(80), nullable=True)
    fault_detail = db.Column(db.String(255), nullable=True)
    congestion = db.Column(db.String(20), nullable=False, default="medium")
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC)
    )


class OperationEventLog(db.Model):

    __tablename__ = "operation_event_logs"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    severity = db.Column(db.String(20), nullable=False, default="info", index=True)
    source = db.Column(db.String(30), nullable=False, default="system")
    train_id = db.Column(db.Integer, db.ForeignKey("trains.id"), nullable=True, index=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=True, index=True)
    reservation_id = db.Column(db.Integer, db.ForeignKey("reservations.id"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    message = db.Column(db.String(255), nullable=False)
    payload_json = db.Column(db.Text, nullable=True)
    is_acknowledged = db.Column(db.Boolean, nullable=False, default=False, index=True)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        index=True,
    )


class CongestionObservation(db.Model):
    """열차별 혼잡도·운행 시계열 (운영 중 자동 수집)."""

    __tablename__ = "congestion_observations"

    id = db.Column(db.Integer, primary_key=True)
    measured_at = db.Column(db.DateTime, nullable=False, index=True)
    train_id = db.Column(db.Integer, db.ForeignKey("trains.id"), nullable=False, index=True)
    route_id = db.Column(db.Integer, db.ForeignKey("routes.id"), nullable=True, index=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=True, index=True)
    congestion_ratio = db.Column(db.Float, nullable=False)
    congestion_label = db.Column(db.String(20), nullable=False, default="medium")
    passengers_board = db.Column(db.Integer, nullable=False, default=0)
    passengers_alight = db.Column(db.Integer, nullable=False, default=0)
    headway_min = db.Column(db.Float, nullable=False, default=8.0)
    speed_kmh = db.Column(db.Float, nullable=False, default=0.0)
    operation_status = db.Column(db.String(20), nullable=False, default="normal")
    data_origin = db.Column(db.String(30), nullable=False, default="operational", index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


class StationPassengerFlow(db.Model):
    """역별 승하차 시계열."""

    __tablename__ = "station_passenger_flows"

    id = db.Column(db.Integer, primary_key=True)
    measured_at = db.Column(db.DateTime, nullable=False, index=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    train_id = db.Column(db.Integer, db.ForeignKey("trains.id"), nullable=True, index=True)
    route_id = db.Column(db.Integer, db.ForeignKey("routes.id"), nullable=True, index=True)
    passengers_board = db.Column(db.Integer, nullable=False, default=0)
    passengers_alight = db.Column(db.Integer, nullable=False, default=0)
    data_origin = db.Column(db.String(30), nullable=False, default="operational", index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


class RouteHeadwaySnapshot(db.Model):
    """노선별 배차 간격 스냅샷."""

    __tablename__ = "route_headway_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    measured_at = db.Column(db.DateTime, nullable=False, index=True)
    route_id = db.Column(db.Integer, db.ForeignKey("routes.id"), nullable=False, index=True)
    headway_min = db.Column(db.Float, nullable=False)
    active_trains = db.Column(db.Integer, nullable=False, default=0)
    data_origin = db.Column(db.String(30), nullable=False, default="operational", index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))


class ModelTrainingRun(db.Model):
    """혼잡도 AI 모델 학습·버전 이력."""

    __tablename__ = "model_training_runs"

    id = db.Column(db.Integer, primary_key=True)
    version_tag = db.Column(db.String(40), unique=True, nullable=False, index=True)
    data_source = db.Column(db.String(40), nullable=False)
    sample_count = db.Column(db.Integer, nullable=False, default=0)
    operational_sample_count = db.Column(db.Integer, nullable=False, default=0)
    ml_mae = db.Column(db.Float, nullable=True)
    dl_mae = db.Column(db.Float, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=False, index=True)
    metadata_json = db.Column(db.Text, nullable=True)
    trained_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))