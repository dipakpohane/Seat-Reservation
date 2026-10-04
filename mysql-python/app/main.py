"""Application startup, request logging, and route registration."""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import AUTH_SECRET
from app.database import DatabasePool, create_schema
from app.routes import health, metrics, reservations, shows, tickets


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect to MySQL and create missing tables before serving requests."""
    if os.getenv("APP_ENV") == "production" and AUTH_SECRET == "local-development-secret-change-me":
        raise RuntimeError("AUTH_SECRET must be set in production")

    database = DatabasePool()
    create_schema(database)
    app.state.database = database
    yield
    app.state.database = None


app = FastAPI(title="Seat Reservation API", version="1.0.0", lifespan=lifespan)
logger = logging.getLogger("seat_reservation")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
# Uvicorn's default access log includes URL paths, which contain signed QR tokens.
logging.getLogger("uvicorn.access").disabled = True


@app.middleware("http")
async def request_logging(request: Request, call_next):
    """Attach a correlation ID to each request and structured log record."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log_path = request.url.path
        if log_path.startswith("/tickets/verify/"):
            suffix = "/check-in" if log_path.endswith("/check-in") else ""
            log_path = "/tickets/verify/{ticket_code}" + suffix
        logger.info(json.dumps({
            "request_id": request_id,
            "method": request.method,
            "path": log_path,
            "status": status_code,
        }))


app.include_router(health.router)
app.include_router(shows.router)
app.include_router(reservations.router)
app.include_router(metrics.router)
app.include_router(tickets.router)
