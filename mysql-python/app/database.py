"""MySQL connection pooling, transactions, and table definitions."""

import os
import threading
import uuid
from contextlib import contextmanager

from fastapi import HTTPException, Request
from mysql.connector.pooling import MySQLConnectionPool

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS shows (
        id CHAR(36) PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        price_paise BIGINT NOT NULL CHECK (price_paise >= 0),
        per_user_limit INT NOT NULL CHECK (per_user_limit > 0),
        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS reservations (
        id CHAR(36) PRIMARY KEY,
        show_id CHAR(36) NOT NULL,
        user_id VARCHAR(191) NOT NULL,
        idempotency_key VARCHAR(200) NOT NULL,
        request_hash CHAR(64) NOT NULL,
        requested_seats JSON NOT NULL,
        amount_paise BIGINT NOT NULL,
        status ENUM('confirmed', 'cancelled') NOT NULL,
        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uq_reservation_idempotency (show_id, user_id, idempotency_key),
        KEY idx_reservation_user_status (show_id, user_id, status),
        CONSTRAINT fk_reservation_show FOREIGN KEY (show_id) REFERENCES shows(id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS seats (
        show_id CHAR(36) NOT NULL,
        label VARCHAR(64) NOT NULL,
        status ENUM('available', 'confirmed') NOT NULL,
        reservation_id CHAR(36) NULL,
        PRIMARY KEY (show_id, label),
        KEY idx_seat_reservation (reservation_id),
        CONSTRAINT fk_seat_show FOREIGN KEY (show_id) REFERENCES shows(id),
        CONSTRAINT fk_seat_reservation FOREIGN KEY (reservation_id) REFERENCES reservations(id),
        CONSTRAINT chk_seat_reservation CHECK (
            (status = 'available' AND reservation_id IS NULL) OR
            (status = 'confirmed' AND reservation_id IS NOT NULL)
        )
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS metric_counters (
        name VARCHAR(100) PRIMARY KEY,
        value BIGINT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB""",
]


class QueryResult:
    """Provide fetchone/fetchall on buffered cursor results."""

    def __init__(self, rows, rowcount):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class MySQLSession:
    """Run parameterized SQL and manage transaction boundaries."""

    def __init__(self, connection):
        self.raw = connection

    def execute(self, statement, parameters=()):
        cursor = self.raw.cursor(dictionary=True, buffered=True)
        try:
            cursor.execute(statement, parameters)
            rows = cursor.fetchall() if cursor.with_rows else []
            return QueryResult(rows, cursor.rowcount)
        finally:
            cursor.close()

    def executemany(self, statement, parameters):
        cursor = self.raw.cursor()
        try:
            cursor.executemany(statement, parameters)
            return QueryResult([], cursor.rowcount)
        finally:
            cursor.close()

    @contextmanager
    def transaction(self):
        self.raw.start_transaction(isolation_level="REPEATABLE READ")
        try:
            yield
            self.raw.commit()
        except Exception:
            self.raw.rollback()
            raise


class DatabasePool:
    """Queue callers when every pooled MySQL connection is busy."""

    def __init__(self):
        pool_size = int(os.getenv("DB_POOL_SIZE", "20"))
        if not 1 <= pool_size <= 32:
            raise RuntimeError("DB_POOL_SIZE must be between 1 and 32")
        self._slots = threading.BoundedSemaphore(pool_size)
        self._pool = MySQLConnectionPool(
            pool_name=f"seat_reservation_{uuid.uuid4().hex}",
            pool_size=pool_size,
            pool_reset_session=True,
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "seat_reservation"),
            connection_timeout=5,
            autocommit=False,
        )

    @contextmanager
    def connection(self):
        self._slots.acquire()
        raw = None
        try:
            raw = self._pool.get_connection()
            yield MySQLSession(raw)
        finally:
            try:
                if raw is not None:
                    try:
                        raw.rollback()
                    finally:
                        raw.close()
            finally:
                self._slots.release()


def create_schema(database: DatabasePool) -> None:
    """Create missing tables at app startup."""
    with database.connection() as connection:
        for statement in SCHEMA:
            connection.execute(statement)


def get_database(request: Request) -> DatabasePool:
    """Return the database pool initialized during app startup."""
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Database is not initialized")
    return database