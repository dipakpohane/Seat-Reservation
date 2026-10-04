# Seat-Reservation
istributed-ready ticketing API built with Go, PostgreSQL, and Docker. Implements lexicographical deadlock prevention, reconciliation invariants, and sub-millisecond contention handling.

## MySQL/Python implementation

The separate FastAPI implementation using local MySQL/InnoDB is in [`mysql-python/`](mysql-python/). It includes setup instructions, Docker Compose, metrics, and a concurrency burst runner.
