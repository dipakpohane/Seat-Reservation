"""Reserve seats and cancel reservations belonging to the caller."""

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth import authenticate
from app.database import DatabasePool, get_database
from app.models import ReserveSeats
from app.services import create_ticket_qr, increment_metric, reservation_payload

router = APIRouter()


@router.post("/shows/{show_id}/reserve", status_code=201)
def reserve(show_id: str, body: ReserveSeats, identity: str = Depends(authenticate), database: DatabasePool = Depends(get_database)):
    if len(set(body.seats)) != len(body.seats) or any(not seat.strip() for seat in body.seats):
        raise HTTPException(status_code=422, detail="Seat labels must be non-empty and unique")

    requested = sorted(body.seats)
    request_hash = hashlib.sha256(json.dumps(requested, separators=(",", ":")).encode()).hexdigest()
    marks = ", ".join(["%s"] * len(requested))
    declined = None
    result = None

    with database.connection() as connection, connection.transaction():
        # Lock the show first so seat and user-limit checks are one serialized decision.
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
                    qr_token_hash, qr_image_png = create_ticket_qr(reservation_id)
                    connection.execute(
                        "INSERT INTO ticket_qr_codes (reservation_id, token_hash, image_png) VALUES (%s, %s, %s)",
                        (reservation_id, qr_token_hash, qr_image_png),
                    )
                    updated = connection.execute(
                        f"UPDATE seats SET status = 'confirmed', reservation_id = %s "
                        f"WHERE show_id = %s AND label IN ({marks}) AND status = 'available'",
                        (reservation_id, show_id, *requested),
                    )
                    if updated.rowcount != len(requested):
                        raise RuntimeError("Seat state changed while the show row was locked")
                    increment_metric(connection, "reservations_confirmed")
                    result = {
                        "reservation_id": reservation_id,
                        "show_id": show_id,
                        "user_id": identity,
                        "seats": requested,
                        "amount_paise": amount,
                        "status": "confirmed",
                        "ticket_qr_url": f"/reservations/{reservation_id}/qr",
                        "checked_in": False,
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


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: str, identity: str = Depends(authenticate), database: DatabasePool = Depends(get_database)):
    with database.connection() as connection, connection.transaction():
        reservation = connection.execute(
            "SELECT show_id FROM reservations WHERE id = %s", (reservation_id,)
        ).fetchone()
        if reservation is None:
            raise HTTPException(status_code=404, detail="Reservation not found")
        connection.execute("SELECT id FROM shows WHERE id = %s FOR UPDATE", (reservation["show_id"],))
        row = connection.execute("SELECT * FROM reservations WHERE id = %s FOR UPDATE", (reservation_id,)).fetchone()
        if row["user_id"] != identity:
            raise HTTPException(status_code=403, detail="Only the reservation owner may cancel")
        ticket = connection.execute(
            "SELECT checked_in_at FROM ticket_qr_codes WHERE reservation_id = %s",
            (reservation_id,),
        ).fetchone()
        if row["status"] == "confirmed" and ticket and ticket["checked_in_at"]:
            raise HTTPException(status_code=409, detail="A checked-in ticket cannot be cancelled")
        if row["status"] == "confirmed":
            connection.execute(
                "UPDATE seats SET status = 'available', reservation_id = NULL WHERE reservation_id = %s",
                (reservation_id,),
            )
            connection.execute("UPDATE reservations SET status = 'cancelled' WHERE id = %s", (reservation_id,))
            row["status"] = "cancelled"
        return reservation_payload(connection, row)