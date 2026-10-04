# Design Notes

## Assignment Scenario Coverage (2026-10-04)

This status table separates implemented behavior from measured evidence. Do not interpret this as a claim that the full assignment has passed: the 20,000-request run was cancelled, and no public deployment exists yet.

| Requirement | Status | Evidence / remaining work |
| --- | --- | --- |
| Create shows with unique seats and integer paise price | Implemented; price validation checked | API uses the assignment's exact public field `price_paise`. HTTP checks rejected negative, float, string, and overflow prices with 422. Zero and positive integers are allowed. |
| No double-sell for a hot seat | Passed at 500 requests | Local MySQL burst produced exactly 1 winner and 499 `409 seat-taken` results, with no 5xx or transport errors. The 20,000-request criterion has not been verified. |
| Per-user booking limit | Passed in local concurrent smoke test | Five parallel reservations for one identity produced four confirmations and one `409 per-user-limit`. |
| Idempotency and changed-body rejection | Passed in local concurrent smoke test | Eight concurrent retries returned one reservation ID; reusing the key with different seats returned `409 idempotency-key-reuse`. |
| Partial multi-seat requests | Passed; all-or-nothing | Asking for a taken seat and a free seat returned 409 and left the free seat available. |
| Cancellation and owner identity | Passed in local smoke test | Non-owner received 403; owner cancellation marked the reservation cancelled and restored its seat to available. Cancellation is explicit; there are no timed holds. |
| Token-derived identity / body spoofing | Implemented and locally checked | Reservation user ID comes from an HMAC-signed bearer token; a request-body `user_id` is ignored. This sample HMAC format is not a production identity provider. |
| Seat counts reconcile | Passed after the 500-request burst | Final test show state was 3 available + 0 held + 6 confirmed = 9 total. The show read uses a repeatable-read transaction; mutations are transactionally committed. |
| Health endpoints | Implemented; readiness observed healthy locally | `/live` is liveness; `/ready` executes `SELECT 1` and returns 503 on dependency errors. Local `/ready` returned 200. A deliberate database-outage test remains to be done. |
| Prometheus metrics and structured logs | Implemented; smoke-checked locally | Confirmation/decline counters, available-seat gauge, and JSON request-ID logs exist. Public dashboards/log access require deployment. |
| Docker clean-checkout build | Files present; not verified | Dockerfile and Compose with MySQL 8 are included. Docker was unavailable in the development environment, so a clean container build/run was not performed. |
| 20,000-request burst | Not verified | The local 500-request run passed. The attempted 20,000-request run was cancelled; no claim is made about 20k outcomes. `burst.py` accepts `--hot-requests 20000` for a future run. |
| Public deployment and live URL | Not done | GitHub is public, but there is no hosted API URL, external production MySQL, or public runtime logs yet. The Render template requires external MySQL configuration. |
| Payment / double-charge behavior | Not applicable to this implementation | The service creates reservations only; it does not integrate a payment processor or charge cards. Reservation idempotency does not prove payment idempotency. |

### Measured Local Burst

The 500-request local run printed: 201 responses = 16 (including the test cancellation reservation), 409 responses = 502, plus one expected 403 spoofed cancellation and one 200 owner cancellation. The hot seat had exactly 1 winner and 499 `seat-taken` declines. The limit, same-key retry, changed-key-body, partial request, cancellation, and final reconciliation assertions all passed. This is a bounded local MySQL check, not the requested 20,000-request deployment test.

### Money Contract

The public show field is `price_paise`, matching the assignment. It accepts only JSON integers, including zero, and rejects negative values, floats, numeric strings, and values large enough to overflow a 100-seat signed BIGINT total. Reservation amounts are integer multiplication only; no floating-point money arithmetic is used.

## Atomic decision

Tables use InnoDB. Every reservation transaction takes `SELECT ... FOR UPDATE` on its show row before checking the idempotency key, seat states, or user quota. Transactions for one show serialize at that row. In the same transaction, the implementation inserts the reservation, confirms every requested seat, and updates the success metric; errors roll back all changes. MySQL primary and unique keys additionally enforce unique `(show_id, seat_label)` and `(show_id, user_id, idempotency_key)`. Multi-seat requests are all-or-nothing. Since the show row is acquired before any seat or reservation changes, multi-seat requests have a deterministic lock order and cannot deadlock by locking requested seats in opposite orders. This deliberately favors understandable correctness over throughput for a single show.

## Idempotency and release

The reservation table stores the key, identity, canonical sorted-seat request hash, and requested seat JSON. The unique scope is `(show_id, user_id, idempotency_key)`. Same-key/same-body requests return the original reservation; a changed seat set returns 409. The show lock serializes concurrent retries, while the unique constraint is the final database guard. Cancellation takes the same show lock, verifies the bearer-token identity owns the reservation, releases seats still assigned to that reservation, and marks it cancelled in one transaction. There are no expiring holds.

## Consistency and operations

MySQL/InnoDB is authoritative. The service fails closed when MySQL cannot be reached; readiness executes `SELECT 1`. Show state is read in a `REPEATABLE READ` transaction and exposes available, held (zero), and confirmed counts. Reservation metrics are incremented within the transaction; the available gauge is read from current seat rows. A multi-region deployment should use a single writable primary for reservations and reject writes when it cannot be reached rather than fail over to a potentially divergent writer.

Page on readiness failure, unexpected 5xx, database pool exhaustion, lock-wait spikes, metric/API reconciliation mismatch, or unexpected booking/decline rates. JSON request IDs correlate client reports with logs. Platform-level monitoring should also track MySQL storage, CPU, connection use, and replication lag if replicas are introduced.

## Security, AI, next steps

The compact HMAC token exists to make token-derived identity testable; production should validate identity-provider tokens and use separate admin authorization. Production must set a private, strong `AUTH_SECRET` and least-privilege MySQL credentials. AI assisted with scaffolding and reviewing the database flow, burst harness, packaging, and docs. The deliberate choices are MySQL/InnoDB, show-row serialization, all-or-nothing requests, explicit cancellation, and consistency over availability. The author should independently run the MySQL-backed burst test and inspect transaction behavior before treating the result as production-ready.

Next: execute the 20,000-request test against the actual MySQL version and hosting tier, provision a hosted MySQL database and public API deployment, verify a clean Docker build, capture public logs/metrics, add automated integration tests, measure lock waits and pool saturation, add latency histograms and alerts, and replace sample HMAC auth with a production identity provider.
