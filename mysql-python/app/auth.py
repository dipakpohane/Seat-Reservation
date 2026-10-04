"""Verify bearer tokens and protect admin-only operations."""

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.config import AUTH_SECRET


def token_for(identity: str) -> str:
    """Create a signed token for examples and the burst runner."""
    signature = hmac.new(AUTH_SECRET.encode(), identity.encode(), hashlib.sha256).hexdigest()
    return f"{identity}.{signature}"


def authenticate(authorization: Annotated[str | None, Header()] = None) -> str:
    """Return an identity only when its bearer-token signature is valid."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    identity, separator, signature = authorization[7:].rpartition(".")
    expected = hmac.new(AUTH_SECRET.encode(), identity.encode(), hashlib.sha256).hexdigest()
    if not separator or not identity or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    return identity


def require_admin(identity: str = Depends(authenticate)) -> str:
    """Allow show creation only for the signed admin identity."""
    if identity != "admin":
        raise HTTPException(status_code=403, detail="Admin token required")
    return identity