"""Shared helpers for reservations and metric counters."""

import json

from app.database import MySQLSession


def increment_metric(connection: MySQLSession, name: str, amount: int = 1) -> None:
    """Increment a metric inside the caller's transaction."""
    connection.execute(
        "INSERT INTO metric_counters (name, value) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE value = value + VALUES(value)",
        (name, amount),
    )


def reservation_payload(connection: MySQLSession, reservation: dict) -> dict:
    """Build a reservation response, including after cancellation."""
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