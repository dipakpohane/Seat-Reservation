# Seat Reservation API

A transactional JSON API for assigned seats. MySQL/InnoDB is the system of record. This project lives separately from the DSA workspace.

For a beginner-friendly code map and instructions on following or adding an endpoint, see [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md).

## Complete Local Walkthrough (Windows)

### 1. Prepare MySQL and start the API once

Open PowerShell in this folder and run:

```powershell
Set-Location D:\new_start\SeatReservation
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup_local.py
```

`setup_local.py` asks for the MySQL root password with hidden input. Type it in the terminal, not in chat. The script creates the app database/user, saves fresh credentials in the Git-ignored `.env`, and starts the API. Keep its API terminal open. Check `http://127.0.0.1:8000/ready`; a ready response means the API can reach MySQL.

### 2. Book a seat by clicking

Double-click `BookTicket.bat`. It starts the API if it is not already running. The first run creates one `Local Cricket Demo` match with seats `A1`-`A10` and `B1`-`B10`; later runs use that same match. The script lists available seats, asks for a buyer name/ID and seat label, then prints the reservation ID, match, seat, and price. Choose a seat from the displayed available list. A seat already booked by another request is declined.

The show ID is saved locally in `demo_show_id.txt` (ignored by Git). To use the API directly instead, create a show with `POST /shows`, then call `POST /shows/{show_id}/reserve`; interactive API docs are at `http://127.0.0.1:8000/docs`.

### 3. What the buyer receives

A successful booking returns HTTP `201` and a response like:

```json
{
	"reservation_id": "<generated-reservation-id>",
	"show_id": "<generated-show-id>",
	"user_id": "fan-1",
	"seats": ["A1"],
	"amount_paise": 25000,
	"status": "confirmed"
}
```

The click script displays those confirmation details in its console. `25000` is integer paise (Rs 250). This demo confirms a reservation only; it does not collect payment or issue an actual cricket-stadium/BookMyShow ticket.

### 4. Check booked and available seats in MySQL Workbench

Connect to the `seat_reservation` database using your MySQL admin connection, then find the demo show ID:

```sql
SELECT id, name, price_paise
FROM shows
ORDER BY created_at DESC;
```

Paste that show's ID into `@show_id`, then list every seat and its current state:

```sql
SET @show_id = 'paste-show-id-here';

SELECT
		s.label AS seat,
		s.status AS seat_status,
		r.user_id,
		r.id AS reservation_id,
		r.status AS reservation_status,
		r.amount_paise
FROM seats AS s
LEFT JOIN reservations AS r ON r.id = s.reservation_id
WHERE s.show_id = @show_id
ORDER BY s.label;
```

`seat_status = 'available'` means the seat can be booked. `seat_status = 'confirmed'` with a reservation ID means it is booked. The API exposes the same inventory at `GET /shows/{show_id}`.

To list only available seats:

```sql
SELECT label
FROM seats
WHERE show_id = @show_id AND status = 'available'
ORDER BY label;
```

To list only currently booked seats:

```sql
SELECT s.label, r.user_id, r.id AS reservation_id, r.amount_paise
FROM seats AS s
JOIN reservations AS r ON r.id = s.reservation_id
WHERE s.show_id = @show_id AND s.status = 'confirmed'
ORDER BY s.label;
```

### 5. Cancellation and common outcomes

Only the buyer identity in the reservation's signed token can cancel it with `POST /reservations/{reservation_id}/cancel`. After cancellation, the reservation record remains with `status = 'cancelled'`, and its seat returns to `available`. A retry with the same idempotency key and same seat returns the original reservation; a different request using that key, a booked seat, or an exceeded booking limit returns HTTP `409`. Multi-seat requests are all-or-nothing. There are no timed holds, and `held` remains zero.

## Use your local MySQL

Install MySQL Server 8.0+ and make sure its service is running. In MySQL Workbench or your SQL client, create a database and a least-privilege application user (replace the password with one you choose locally):

```sql
CREATE DATABASE seat_reservation CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'seat_reservation'@'localhost' IDENTIFIED BY 'choose-a-local-password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, REFERENCES ON seat_reservation.* TO 'seat_reservation'@'localhost';
```

Copy `.env.example` to `.env` and set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, and a private `AUTH_SECRET` to match your local MySQL account. `.env` is ignored by Git; do not commit passwords or production secrets. If your existing MySQL database or account has different names, use those values instead. The app initializes its tables on startup.

For automatic local setup, run `python setup_local.py`. It asks for the MySQL root password with hidden input, creates/updates the least-privilege app user, writes fresh app credentials only to the ignored `.env`, and starts the API. It does not print the generated credentials.

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

## Click-To-Book Demo

After running `setup_local.py` once to configure MySQL, double-click `BookTicket.bat`. It starts the API if needed, creates one local demo cricket match on first use, shows available seats, and asks for a buyer name and seat label. Later clicks reuse the same match so a confirmed seat cannot be booked twice. The generated `demo_show_id.txt` is local-only and ignored by Git.

If you do not want to use your installed MySQL for a local demo, `docker compose up --build` starts an isolated MySQL 8 container and API. That container is only an optional demo and does not connect to your existing MySQL instance.

## Authentication and endpoints

Tokens are `identity.HMAC_SHA256(AUTH_SECRET, identity)`. The `admin` identity creates shows; any other verified identity can reserve seats. Generate a token locally after loading `.env`:

```powershell
python -c "import hashlib,hmac,os; from dotenv import load_dotenv; load_dotenv(); identity='admin'; print(identity+'.'+hmac.new(os.environ['AUTH_SECRET'].encode(),identity.encode(),hashlib.sha256).hexdigest())"
```

`POST /shows` body: `{"name":"friday-night","seats":["A1","A2"],"price":25000,"per_user_limit":4}`. `price` is an integer number of paise.

`POST /shows/{id}/reserve` body: `{"seats":["A1"],"idempotency_key":"checkout-123"}`. The user identity comes only from the verified bearer token. Requests for multiple seats are all-or-nothing. Conflicts return 409 with `seat-taken`, `per-user-limit`, or `idempotency-key-reuse`. Repeating the same key and seat set returns the original reservation; changing the seat set with the same key returns 409. `POST /reservations/{id}/cancel` is owner-only and can be repeated; seats are released transactionally. There are no timed holds, so `held` is always zero. Prices and totals use integer paise.

## Burst test

With the API running, execute the burst script from this folder. It loads `.env` automatically:

```powershell
python burst.py http://localhost:8000 --hot-requests 20000 --workers 200
```

This races distinct users on one hot seat, sends five concurrent reservations for one user with limit four, checks all-or-nothing requests, concurrent idempotent retries, spoofed cancellation, owner cancellation, and the final seat-count invariant. It prints the HTTP distribution and check results. A 500-request local MySQL run passed; the 20,000-request command above has not yet been verified. Start with 500 on a small local MySQL instance.

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
