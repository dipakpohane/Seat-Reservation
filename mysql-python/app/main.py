import hashlib
import hmac
import json
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from mysql.connector.pooling import MySQLConnectionPool
from pydantic import BaseModel, Field

load_dotenv()

AUTH_SECRET = os.getenv("AUTH_SECRET", "local-development-secret-change-me")
DEFAULT_PER_USER_LIMIT = 4
pool = None

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS shows (
        id CHAR(36) PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        price_paise BIGINT NOT NULL CHECK (price_paise >= 0),
        per_user_limit INT NOT NULL CHECK (per_user_limit > 0),
        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS reservations (
        id CHAR(36) PRIMARY KEY,
        show_id CHAR(36) NOT NULL,
        user_id VARCHAR(191) NOT NULL,
        idempotency_key VARCHAR(200) NOT NULL,
        request_hash CHAR(64) NOT NULL,
        requested_seats JSON NOT NULL,
        amount_paise BIGINT NOT NULL,
        status ENUM('confirmed', 'cancelled') NOT NULL,
        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uq_reservation_idempotency (show_id, user_id, idempotency_key),
        KEY idx_reservation_user_status (show_id, user_id, status),
        CONSTRAINT fk_reservation_show FOREIGN KEY (show_id) REFERENCES shows(id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS seats (
        show_id CHAR(36) NOT NULL,
        label VARCHAR(64) NOT NULL,
        status ENUM('available', 'confirmed') NOT NULL,
        reservation_id CHAR(36) NULL,
        PRIMARY KEY (show_id, label),
        KEY idx_seat_reservation (reservation_id),
        CONSTRAINT fk_seat_show FOREIGN KEY (show_id) REFERENCES shows(id),
        CONSTRAINT fk_seat_reservation FOREIGN KEY (reservation_id) REFERENCES reservations(id),
        CONSTRAINT chk_seat_reservation CHECK (
            (status = 'available' AND reservation_id IS NULL) OR
            (status = 'confirmed' AND reservation_id IS NOT NULL)
        )
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS metric_counters (
        name VARCHAR(100) PRIMARY KEY,
        value BIGINT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB""",
]


class QueryResult:
    def __init__(self, rows, rowcount):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class MySQLSession:
    def __init__(self, connection):
        self.raw = connection

    def execute(self, statement, parameters=()):
        cursor = self.raw.cursor(dictionary=True, buffered=True)
        try:
            cursor.execute(statement, parameters)
            rows = cursor.fetchall() if cursor.with_rows else []
            return QueryResult(rows, cursor.rowcount)
        finally:
            cursor.close()

    def executemany(self, statement, parameters):
        cursor = self.raw.cursor()
        try:
            cursor.executemany(statement, parameters)
            return QueryResult([], cursor.rowcount)
        finally:
            cursor.close()

    @contextmanager
    def transaction(self):
        self.raw.start_transaction(isolation_level="REPEATABLE READ")
        try:
            yield
            self.raw.commit()
        except Exception:
            self.raw.rollback()
            raise


class DatabasePool:
    def __init__(self):
        pool_size = int(os.getenv("DB_POOL_SIZE", "20"))
        if not 1 <= pool_size <= 32:
            raise RuntimeError("DB_POOL_SIZE must be between 1 and 32")
        self._slots = threading.BoundedSemaphore(pool_size)
        self._pool = MySQLConnectionPool(
            pool_name="seat_reservation_pool",
            pool_size=pool_size,
            pool_reset_session=True,
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "seat_reservation"),
            connection_timeout=5,
            autocommit=False,
        )

    @contextmanager
    def connection(self):
        self._slots.acquire()
        raw = None
        try:
            raw = self._pool.get_connection()
            yield MySQLSession(raw)
        finally:
            try:
                if raw is not None:
                    try:
                        raw.rollback()
                    finally:
                        raw.close()
            finally:
                self._slots.release()


class CreateShow(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    seats: list[str] = Field(min_length=1, max_length=100000)
    price_paise: int = Field(ge=0)
    per_user_limit: int = Field(default=DEFAULT_PER_USER_LIMIT, gt=0)


class ReserveSeats(BaseModel):
    seats: list[str] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)


def token_for(identity: str) -> str:
    signature = hmac.new(AUTH_SECRET.encode(), identity.encode(), hashlib.sha256).hexdigest()
    return f"{identity}.{signature}"


def authenticate(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    identity, separator, signature = authorization[7:].rpartition(".")
    expected = hmac.new(AUTH_SECRET.encode(), identity.encode(), hashlib.sha256).hexdigest()
    if not separator or not identity or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    return identity


def require_admin(identity: str = Depends(authenticate)) -> str:
    if identity != "admin":
        raise HTTPException(status_code=403, detail="Admin token required")
    return identity


def db_pool() -> DatabasePool:
    if pool is None:
        raise HTTPException(status_code=503, detail="Database is not initialized")
    return pool


def increment_metric(connection: MySQLSession, name: str, amount: int = 1) -> None:
    connection.execute(
        "INSERT INTO metric_counters (name, value) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE value = value + VALUES(value)",
        (name, amount),
    )


def reservation_payload(connection: MySQLSession, reservation: dict) -> dict:
    rows = connection.execute(
        "SELECT label FROM seats WHERE reservation_id = %s ORDER BY label",
        (reservation["id"],),
    ).fetchall()
    requested = reservation["requested_seats"]
    if isinstance(requested, str):
        requested = json.loads(requested)
    return {
        "reservation_id": reservation["id"],
        "show_id": reservation["show_id"],
        "user_id": reservation["user_id"],
        "seats": [row["label"] for row in rows] or requested,
        "amount_paise": reservation["amount_paise"],
        "status": reservation["status"],
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool
    if os.getenv("APP_ENV") == "production" and AUTH_SECRET == "local-development-secret-change-me":
        raise RuntimeError("AUTH_SECRET must be set in production")
    pool = DatabasePool()
    with pool.connection() as connection:
        for statement in SCHEMA:
            connection.execute(statement)
    yield
    pool = None


app = FastAPI(title="Seat Reservation API", version="1.0.0", lifespan=lifespan)
logger = logging.getLogger("seat_reservation")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        logger.info(json.dumps({"request_id": request_id, "method": request.method, "path": request.url.path, "status": status_code}))


@app.get("/live")
def liveness():
    return {"status": "live"}


@app.get("/ready")
def readiness(database: DatabasePool = Depends(db_pool)):
    try:
        with database.connection() as connection:
            connection.execute("SELECT 1")
        return {"status": "ready", "database": "reachable"}
    except Exception as error:
        raise HTTPException(status_code=503, detail="Database unavailable") from error


@app.post("/shows", status_code=201)
def create_show(body: CreateShow, _: str = Depends(require_admin), database: DatabasePool = Depends(db_pool)):
    if len(set(body.seats)) != len(body.seats) or any(not seat.strip() for seat in body.seats):
        raise HTTPException(status_code=422, detail="Seat labels must be non-empty and unique")
    show_id = str(uuid.uuid4())
    with database.connection() as connection, connection.transaction():
        connection.execute(
            "INSERT INTO shows (id, name, price_paise, per_user_limit) VALUES (%s, %s, %s, %s)",
            (show_id, body.name, body.price_paise, body.per_user_limit),
        )
        connection.executemany(
            "INSERT INTO seats (show_id, label, status) VALUES (%s, %s, 'available')",
            [(show_id, seat) for seat in body.seats],
        )
    return {
        "id": show_id,
        "name": body.name,
        "price_paise": body.price_paise,
        "per_user_limit": body.per_user_limit,
        "seats": [{"label": seat, "status": "available"} for seat in sorted(body.seats)],
    }


@app.post("/shows/{show_id}/reserve", status_code=201)
def reserve(show_id: str, body: ReserveSeats, identity: str = Depends(authenticate), database: DatabasePool = Depends(db_pool)):
    if len(set(body.seats)) != len(body.seats) or any(not seat.strip() for seat in body.seats):
        raise HTTPException(status_code=422, detail="Seat labels must be non-empty and unique")
    requested = sorted(body.seats)
    request_hash = hashlib.sha256(json.dumps(requested, separators=(",", ":")).encode()).hexdigest()
    marks = ", ".join(["%s"] * len(requested))
    declined = None
    result = None
    with database.connection() as connection, connection.transaction():
        show = connection.execute("SELECT * FROM shows WHERE id = %s FOR UPDATE", (show_id,)).fetchone()
        if show is None:
            raise HTTPException(status_code=404, detail="Show not found")
        prior = connection.execute(
            "SELECT * FROM reservations WHERE show_id = %s AND user_id = %s AND idempotency_key = %s",
            (show_id, identity, body.idempotency_key),
        ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                declined = "idempotency-key-reuse"
            else:
                increment_metric(connection, "idempotent_replay")
                result = reservation_payload(connection, prior)
        else:
            seat_rows = connection.execute(
                f"SELECT label, status FROM seats WHERE show_id = %s AND label IN ({marks}) ORDER BY label",
                (show_id, *requested),
            ).fetchall()
            if len(seat_rows) != len(requested):
                raise HTTPException(status_code=404, detail="One or more seats do not exist")
            if any(row["status"] != "available" for row in seat_rows):
                declined = "seat-taken"
            else:
                current_count = connection.execute(
                    "SELECT count(*) AS count FROM seats s JOIN reservations r ON r.id = s.reservation_id "
                    "WHERE s.show_id = %s AND r.user_id = %s AND r.status = 'confirmed'",
                    (show_id, identity),
                ).fetchone()["count"]
                if current_count + len(requested) > show["per_user_limit"]:
                    declined = "per-user-limit"
                else:
                    reservation_id = str(uuid.uuid4())
                    amount = len(requested) * show["price_paise"]
                    requested_json = json.dumps(requested, separators=(",", ":"))
                    connection.execute(
                        "INSERT INTO reservations (id, show_id, user_id, idempotency_key, request_hash, requested_seats, amount_paise, status) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed')",
                        (reservation_id, show_id, identity, body.idempotency_key, request_hash, requested_json, amount),
                    )
                    connection.execute(
                        f"UPDATE seats SET status = 'confirmed', reservation_id = %s "
                        f"WHERE show_id = %s AND label IN ({marks}) AND status = 'available'",
                        (reservation_id, show_id, *requested),
                    )
                    increment_metric(connection, "reservations_confirmed")
                    result = {
                        "reservation_id": reservation_id,
                        "show_id": show_id,
                        "user_id": identity,
                        "seats": requested,
                        "amount_paise": amount,
                        "status": "confirmed",
                    }
    if declined:
        with database.connection() as connection, connection.transaction():
            increment_metric(connection, f"reservations_declined_{declined.replace('-', '_')}")
        reasons = {
            "seat-taken": "One or more seats are already taken",
            "per-user-limit": "Per-user seat limit exceeded",
            "idempotency-key-reuse": "Idempotency key was used with a different request",
        }
        return JSONResponse(status_code=409, content={"detail": reasons[declined], "reason": declined})
    return result


@app.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: str, identity: str = Depends(authenticate), database: DatabasePool = Depends(db_pool)):
    with database.connection() as connection, connection.transaction():
        reservation = connection.execute("SELECT show_id FROM reservations WHERE id = %s", (reservation_id,)).fetchone()
        if reservation is None:
            raise HTTPException(status_code=404, detail="Reservation not found")
        connection.execute("SELECT id FROM shows WHERE id = %s FOR UPDATE", (reservation["show_id"],))
        row = connection.execute("SELECT * FROM reservations WHERE id = %s FOR UPDATE", (reservation_id,)).fetchone()
        if row["user_id"] != identity:
            raise HTTPException(status_code=403, detail="Only the reservation owner may cancel")
        if row["status"] == "confirmed":
            connection.execute(
                "UPDATE seats SET status = 'available', reservation_id = NULL WHERE reservation_id = %s",
                (reservation_id,),
            )
            connection.execute("UPDATE reservations SET status = 'cancelled' WHERE id = %s", (reservation_id,))
            row["status"] = "cancelled"
        payload = reservation_payload(connection, row)
    return payload


@app.get("/shows/{show_id}")
def get_show(show_id: str, database: DatabasePool = Depends(db_pool)):
    with database.connection() as connection, connection.transaction():
        show = connection.execute("SELECT * FROM shows WHERE id = %s", (show_id,)).fetchone()
        if show is None:
            raise HTTPException(status_code=404, detail="Show not found")
        seats = connection.execute(
            "SELECT label, status FROM seats WHERE show_id = %s ORDER BY label", (show_id,)
        ).fetchall()
    counts = {state: sum(seat["status"] == state for seat in seats) for state in ("available", "confirmed")}
    counts["held"] = 0
    return {
        "id": show["id"],
        "name": show["name"],
        "price_paise": show["price_paise"],
        "per_user_limit": show["per_user_limit"],
        "total_seats": len(seats),
        "counts": counts,
        "seats": seats,
    }


@app.get("/metrics", response_class=PlainTextResponse)
def metrics(database: DatabasePool = Depends(db_pool)):
    with database.connection() as connection:
        counters = connection.execute("SELECT name, value FROM metric_counters ORDER BY name").fetchall()
        available = connection.execute("SELECT count(*) AS count FROM seats WHERE status = 'available'").fetchone()["count"]
    values = {counter["name"]: counter["value"] for counter in counters}
    lines = [
        "# HELP seat_reservations_confirmed_total Successfully confirmed reservations.",
        "# TYPE seat_reservations_confirmed_total counter",
        f"seat_reservations_confirmed_total {values.get('reservations_confirmed', 0)}",
        "# HELP seat_reservations_declined_total Reservation requests declined by reason.",
        "# TYPE seat_reservations_declined_total counter",
    ]
    reasons = ("seat-taken", "per-user-limit", "idempotent-replay", "idempotency-key-reuse")
    counter_names = {
        "seat-taken": "reservations_declined_seat_taken",
        "per-user-limit": "reservations_declined_per_user_limit",
        "idempotent-replay": "idempotent_replay",
        "idempotency-key-reuse": "reservations_declined_idempotency_key_reuse",
    }
    for reason in reasons:
        lines.append(f'seat_reservations_declined_total{{reason="{reason}"}} {values.get(counter_names[reason], 0)}')
    lines.extend([
        "# HELP seat_available Current number of available seats across all shows.",
        "# TYPE seat_available gauge",
        f"seat_available {available}",
    ])
    return "\n".join(lines) + "\n"
