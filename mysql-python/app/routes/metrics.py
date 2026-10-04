"""Prometheus counters and available-seat gauge."""

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.database import DatabasePool, get_database

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(database: DatabasePool = Depends(get_database)):
    with database.connection() as connection:
        counters = connection.execute("SELECT name, value FROM metric_counters ORDER BY name").fetchall()
        available = connection.execute(
            "SELECT count(*) AS count FROM seats WHERE status = 'available'"
        ).fetchone()["count"]
    values = {counter["name"]: counter["value"] for counter in counters}
    lines = [
        "# HELP seat_reservations_confirmed_total Successfully confirmed reservations.",
        "# TYPE seat_reservations_confirmed_total counter",
        f"seat_reservations_confirmed_total {values.get('reservations_confirmed', 0)}",
        "# HELP seat_reservations_declined_total Reservation requests declined by reason.",
        "# TYPE seat_reservations_declined_total counter",
    ]
    counter_names = {
        "seat-taken": "reservations_declined_seat_taken",
        "per-user-limit": "reservations_declined_per_user_limit",
        "idempotent-replay": "idempotent_replay",
        "idempotency-key-reuse": "reservations_declined_idempotency_key_reuse",
    }
    for reason, name in counter_names.items():
        lines.append(f'seat_reservations_declined_total{{reason="{reason}"}} {values.get(name, 0)}')
    lines.extend([
        "# HELP seat_available Current number of available seats across all shows.",
        "# TYPE seat_available gauge",
        f"seat_available {available}",
    ])
    return "\n".join(lines) + "\n"