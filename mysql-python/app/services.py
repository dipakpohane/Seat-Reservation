"""Shared helpers for reservations and metric counters."""

import hashlib
import hmac
import io
import json

import qrcode

from app.config import AUTH_SECRET, PUBLIC_BASE_URL
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
    ticket = connection.execute(
        "SELECT checked_in_at FROM ticket_qr_codes WHERE reservation_id = %s",
        (reservation["id"],),
    ).fetchone()
    return {
        "reservation_id": reservation["id"],
        "show_id": reservation["show_id"],
        "user_id": reservation["user_id"],
        "seats": [row["label"] for row in rows] or requested,
        "amount_paise": reservation["amount_paise"],
        "status": reservation["status"],
        "ticket_qr_url": f"{PUBLIC_BASE_URL}/reservations/{reservation['id']}/qr",
        "checked_in": bool(ticket and ticket["checked_in_at"]),
    }


def ticket_code_for(reservation_id: str) -> str:
    """Sign the reservation ID so QR scans cannot invent ticket references."""
    message = f"ticket:{reservation_id}".encode()
    signature = hmac.new(AUTH_SECRET.encode(), message, hashlib.sha256).hexdigest()
    return f"{reservation_id}.{signature}"


def reservation_id_from_ticket_code(ticket_code: str) -> str | None:
    """Validate a scanned ticket code and return its reservation ID."""
    reservation_id, separator, signature = ticket_code.rpartition(".")
    if not separator:
        return None
    expected = hmac.new(AUTH_SECRET.encode(), f"ticket:{reservation_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return reservation_id


def create_ticket_qr(reservation_id: str) -> tuple[str, bytes]:
    """Return the stored PNG and a hash of its signed verification code."""
    ticket_code = ticket_code_for(reservation_id)
    verify_url = f"{PUBLIC_BASE_URL}/tickets/verify/{ticket_code}"
    image = qrcode.make(verify_url)
    output = io.BytesIO()
    image.save(output, format="PNG")
    token_hash = hashlib.sha256(ticket_code.encode()).hexdigest()
    return token_hash, output.getvalue()