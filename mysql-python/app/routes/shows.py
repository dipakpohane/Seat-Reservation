"""Create shows and report their seat inventory."""

import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_admin
from app.database import DatabasePool, get_database
from app.models import CreateShow

router = APIRouter(prefix="/shows")


@router.post("", status_code=201)
def create_show(body: CreateShow, _: str = Depends(require_admin), database: DatabasePool = Depends(get_database)):
    if len(set(body.seats)) != len(body.seats) or any(not seat.strip() for seat in body.seats):
        raise HTTPException(status_code=422, detail="Seat labels must be non-empty and unique")

    show_id = str(uuid.uuid4())
    with database.connection() as connection, connection.transaction():
        connection.execute(
            "INSERT INTO shows (id, name, price_paise, per_user_limit) VALUES (%s, %s, %s, %s)",
            (show_id, body.name, body.price, body.per_user_limit),
        )
        connection.executemany(
            "INSERT INTO seats (show_id, label, status) VALUES (%s, %s, 'available')",
            [(show_id, seat) for seat in body.seats],
        )

    return {
        "id": show_id,
        "name": body.name,
        "price": body.price,
        "per_user_limit": body.per_user_limit,
        "seats": [{"label": seat, "status": "available"} for seat in sorted(body.seats)],
    }


@router.get("/{show_id}")
def get_show(show_id: str, database: DatabasePool = Depends(get_database)):
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
        "price": show["price_paise"],
        "per_user_limit": show["per_user_limit"],
        "total_seats": len(seats),
        "counts": counts,
        "seats": seats,
    }