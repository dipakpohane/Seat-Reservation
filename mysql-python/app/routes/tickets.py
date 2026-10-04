"""Retrieve ticket QR images, verify scans, and record one-time check-in."""

import hashlib

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.auth import authenticate, require_admin
from app.database import DatabasePool, get_database
from app.services import reservation_id_from_ticket_code, reservation_payload

router = APIRouter()


def ticket_record(connection, ticket_code: str, *, for_update: bool = False):
    reservation_id = reservation_id_from_ticket_code(ticket_code)
    if reservation_id is None:
        return None
    token_hash = hashlib.sha256(ticket_code.encode()).hexdigest()
    lock = " FOR UPDATE" if for_update else ""
    return connection.execute(
        "SELECT r.*, s.name AS show_name, q.checked_in_at "
        "FROM reservations r "
        "JOIN shows s ON s.id = r.show_id "
        "JOIN ticket_qr_codes q ON q.reservation_id = r.id "
        "WHERE r.id = %s AND q.token_hash = %s" + lock,
        (reservation_id, token_hash),
    ).fetchone()


@router.get("/reservations/{reservation_id}/qr")
def get_ticket_qr(reservation_id: str, identity: str = Depends(authenticate), database: DatabasePool = Depends(get_database)):
    with database.connection() as connection:
        reservation = connection.execute(
            "SELECT r.user_id, q.image_png FROM reservations r "
            "JOIN ticket_qr_codes q ON q.reservation_id = r.id WHERE r.id = %s",
            (reservation_id,),
        ).fetchone()
    if reservation is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if reservation["user_id"] != identity:
        raise HTTPException(status_code=403, detail="Only the ticket owner may retrieve its QR")
    return Response(
        content=bytes(reservation["image_png"]),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/tickets/verify/{ticket_code}")
def verify_ticket(ticket_code: str, database: DatabasePool = Depends(get_database)):
    with database.connection() as connection:
        ticket = ticket_record(connection, ticket_code)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket code is invalid")
        details = reservation_payload(connection, ticket)
    valid = ticket["status"] == "confirmed"
    return {
        "verified": True,
        "valid": valid,
        "status": ticket["status"],
        "checked_in": bool(ticket["checked_in_at"]),
        "show_id": ticket["show_id"],
        "show_name": ticket["show_name"],
        "seats": details["seats"],
        "amount_paise": ticket["amount_paise"],
    }


@router.post("/tickets/verify/{ticket_code}/check-in")
def check_in_ticket(ticket_code: str, _: str = Depends(require_admin), database: DatabasePool = Depends(get_database)):
    reservation_id = reservation_id_from_ticket_code(ticket_code)
    if reservation_id is None:
        raise HTTPException(status_code=404, detail="Ticket code is invalid")

    with database.connection() as connection, connection.transaction():
        reservation = connection.execute(
            "SELECT show_id FROM reservations WHERE id = %s", (reservation_id,)
        ).fetchone()
        if reservation is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        connection.execute("SELECT id FROM shows WHERE id = %s FOR UPDATE", (reservation["show_id"],))
        ticket = ticket_record(connection, ticket_code, for_update=True)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket code is invalid")
        if ticket["status"] != "confirmed":
            raise HTTPException(status_code=409, detail="Cancelled ticket cannot be checked in")
        if ticket["checked_in_at"]:
            raise HTTPException(status_code=409, detail="Ticket has already been checked in")
        updated = connection.execute(
            "UPDATE ticket_qr_codes SET checked_in_at = CURRENT_TIMESTAMP(6) "
            "WHERE reservation_id = %s AND checked_in_at IS NULL",
            (reservation_id,),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=409, detail="Ticket has already been checked in")
        ticket["checked_in_at"] = True

    return {
        "verified": True,
        "valid": True,
        "checked_in": True,
        "reservation_id": reservation_id,
        "show_id": ticket["show_id"],
        "show_name": ticket["show_name"],
    }