# Seat Reservation API

A transactional JSON API for assigned seats. MySQL/InnoDB is the system of record. This project lives separately from the DSA workspace.

For a beginner-friendly code map and instructions on following or adding an endpoint, see [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md).

## Use your local MySQL

Install MySQL Server 8.0+ and make sure its service is running. In MySQL Workbench or your SQL client, create a database and a least-privilege application user (replace the password with one you choose locally):

```sql
CREATE DATABASE seat_reservation CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'seat_reservation'@'localhost' IDENTIFIED BY 'choose-a-local-password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, REFERENCES ON seat_reservation.* TO 'seat_reservation'@'localhost';
```

Copy `.env.example` to `.env` and set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, and a private `AUTH_SECRET` to match your local MySQL account. `.env` is ignored by Git; do not commit passwords or production secrets. If your existing MySQL database or account has different names, use those values instead. The app initializes its tables on startup.

From this folder in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env with your local MySQL settings.
uvicorn app.main:app --reload
```

API: `http://localhost:8000`; docs: `/docs`; liveness: `/live`; MySQL-checked readiness: `/ready`; Prometheus metrics: `/metrics`.

If you do not want to use your installed MySQL for a local demo, `docker compose up --build` starts an isolated MySQL 8 container and API. That container is only an optional demo and does not connect to your existing MySQL instance.

## Authentication and endpoints

Tokens are `identity.HMAC_SHA256(AUTH_SECRET, identity)`. The `admin` identity creates shows; any other verified identity can reserve seats. Generate a token locally after loading `.env`:

```powershell
python -c "import hashlib,hmac,os; from dotenv import load_dotenv; load_dotenv(); identity='admin'; print(identity+'.'+hmac.new(os.environ['AUTH_SECRET'].encode(),identity.encode(),hashlib.sha256).hexdigest())"
```

`POST /shows` body: `{"name":"friday-night","seats":["A1","A2"],"price_paise":25000,"per_user_limit":4}`.

`POST /shows/{id}/reserve` body: `{"seats":["A1"],"idempotency_key":"checkout-123"}`. The user identity comes only from the verified bearer token. Requests for multiple seats are all-or-nothing. Conflicts return 409 with `seat-taken`, `per-user-limit`, or `idempotency-key-reuse`. Repeating the same key and seat set returns the original reservation; changing the seat set with the same key returns 409. `POST /reservations/{id}/cancel` is owner-only and can be repeated; seats are released transactionally. There are no timed holds, so `held` is always zero. Prices and totals use integer paise.

## Burst test

With the API running and `.env` loaded in the shell:

```powershell
python burst.py http://localhost:8000 --hot-requests 20000 --workers 200
```

This races distinct users on one hot seat, sends five concurrent reservations for one user with limit four, checks all-or-nothing requests, concurrent idempotent retries, spoofed cancellation, owner cancellation, and the final seat-count invariant. It prints the HTTP distribution and check results. Start with the default 500 hot requests on a small local MySQL instance.

## Metrics and logs

`/metrics` publishes reservation confirmations, declines by reason, and available seat count. JSON request logs include a request ID; clients may provide `X-Request-ID`. Monitor `/ready`, database connections, lock waits, and any unexpected 5xx.

## GitHub and deployment

The Dockerfile listens on `$PORT`; `render.yaml` describes an API service but expects MySQL connection variables from an external MySQL provider. This project is published inside the `mysql-python/` folder of the Seat-Reservation repository. See [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for the module map. To publish future changes, run these commands from the repository root:

```powershell
git add mysql-python
git commit -m "Update MySQL reservation API"
git push origin main
```

Configure deployment with the same `MYSQL_*` variables, a strong `AUTH_SECRET`, and `APP_ENV=production`; health-check `/ready`. A live URL requires a reachable external MySQL instance and a hosting account.
