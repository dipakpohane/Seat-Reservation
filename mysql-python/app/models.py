"""Validated JSON request bodies."""

from pydantic import BaseModel, Field

from app.config import DEFAULT_PER_USER_LIMIT

MAX_SIGNED_BIGINT = 2**63 - 1
MAX_SEATS_PER_RESERVATION = 100
MAX_PRICE_PAISE = MAX_SIGNED_BIGINT // MAX_SEATS_PER_RESERVATION


class CreateShow(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    seats: list[str] = Field(min_length=1, max_length=100000)
    price: int = Field(
        strict=True,
        ge=0,
        le=MAX_PRICE_PAISE,
        description="Ticket price in integer paise; bounded so 100 seats fit in BIGINT",
    )
    per_user_limit: int = Field(default=DEFAULT_PER_USER_LIMIT, gt=0)


class ReserveSeats(BaseModel):
    seats: list[str] = Field(min_length=1, max_length=MAX_SEATS_PER_RESERVATION)
    idempotency_key: str = Field(min_length=1, max_length=200)