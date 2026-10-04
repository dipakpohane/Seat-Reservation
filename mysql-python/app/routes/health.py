"""Liveness and database readiness checks."""

from fastapi import APIRouter, Depends, HTTPException

from app.database import DatabasePool, get_database

router = APIRouter()


@router.get("/live")
def liveness():
    return {"status": "live"}


@router.get("/ready")
def readiness(database: DatabasePool = Depends(get_database)):
    try:
        with database.connection() as connection:
            connection.execute("SELECT 1")
        return {"status": "ready", "database": "reachable"}
    except Exception as error:
        raise HTTPException(status_code=503, detail="Database unavailable") from error