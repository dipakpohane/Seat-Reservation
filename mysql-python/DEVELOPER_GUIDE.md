# Developer Guide

This map explains where the API behavior lives and how to trace a request. Read the module notes, then follow an endpoint from its route to its database helper.

## File Map

- `app/main.py`: startup, request logging, and router registration.
- `app/config.py`: shared environment settings and default booking limit.
- `app/database.py`: MySQL schema, connection pool, transactions, and route dependency.
- `app/models.py`: validates JSON request bodies.
- `app/auth.py`: verifies user tokens and admin permission.
- `app/services.py`: formats reservation responses and increments counters.
- `app/routes/health.py`: liveness and database readiness checks.
- `app/routes/shows.py`: create shows and inspect seat availability.
- `app/routes/reservations.py`: reserve and cancel seats.
- `app/routes/metrics.py`: Prometheus metrics endpoint.

## Follow A Reservation

Start in `app/routes/reservations.py`. The request model is in `app/models.py`; identity verification is in `app/auth.py`; MySQL connections and transactions are in `app/database.py`.

The route locks the show row before checking seat availability and the user's limit. Keep that lock before making any reservation decision. Seat writes, the reservation record, and the success counter commit together. Multi-seat requests are all-or-nothing.

## Run Locally

Follow the MySQL setup steps in `README.md`, then start the server:

```powershell
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` to try the API. Run the burst test after MySQL and the API are running.

## Add An Endpoint

1. Add a Pydantic request type in `app/models.py` if the endpoint accepts JSON.
2. Put the route in the matching file under `app/routes/`.
3. Inject `DatabasePool` with `Depends(get_database)` and use parameterized `%s` placeholders.
4. Wrap related changes in `connection.transaction()`.
5. Register any new route module in `app/main.py`.
6. Update `README.md` and add a focused test when public behavior changes.
