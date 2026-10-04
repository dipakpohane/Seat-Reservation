"""Validated JSON request bodies."""

from pydantic import BaseModel, Field

from app.config import DEFAULT_PER_USER_LIMIT


class CreateShow(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    seats: list[str] = Field(min_length=1, max_length=100000)
    price_paise: int = Field(ge=0)
    per_user_limit: int = Field(default=DEFAULT_PER_USER_LIMIT, gt=0)


class ReserveSeats(BaseModel):
    seats: list[str] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)