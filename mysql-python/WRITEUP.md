# Design Notes

## Atomic decision

Tables use InnoDB. Every reservation transaction takes `SELECT ... FOR UPDATE` on its show row before checking the idempotency key, seat states, or user quota. Transactions for one show serialize at that row. In the same transaction, the implementation inserts the reservation, confirms every requested seat, and updates the success metric; errors roll back all changes. MySQL primary and unique keys additionally enforce unique `(show_id, seat_label)` and `(show_id, user_id, idempotency_key)`. Multi-seat requests are all-or-nothing. Since the show row is acquired before any seat or reservation changes, multi-seat requests have a deterministic lock order and cannot deadlock by locking requested seats in opposite orders. This deliberately favors understandable correctness over throughput for a single show.

## Idempotency and release

The reservation table stores the key, identity, canonical sorted-seat request hash, and requested seat JSON. The unique scope is `(show_id, user_id, idempotency_key)`. Same-key/same-body requests return the original reservation; a changed seat set returns 409. The show lock serializes concurrent retries, while the unique constraint is the final database guard. Cancellation takes the same show lock, verifies the bearer-token identity owns the reservation, releases seats still assigned to that reservation, and marks it cancelled in one transaction. There are no expiring holds.

## Consistency and operations

MySQL/InnoDB is authoritative. The service fails closed when MySQL cannot be reached; readiness executes `SELECT 1`. Show state is read in a `REPEATABLE READ` transaction and exposes available, held (zero), and confirmed counts. Reservation metrics are incremented within the transaction; the available gauge is read from current seat rows. A multi-region deployment should use a single writable primary for reservations and reject writes when it cannot be reached rather than fail over to a potentially divergent writer.

Page on readiness failure, unexpected 5xx, database pool exhaustion, lock-wait spikes, metric/API reconciliation mismatch, or unexpected booking/decline rates. JSON request IDs correlate client reports with logs. Platform-level monitoring should also track MySQL storage, CPU, connection use, and replication lag if replicas are introduced.

## Security, AI, next steps

The compact HMAC token exists to make token-derived identity testable; production should validate identity-provider tokens and use separate admin authorization. Production must set a private, strong `AUTH_SECRET` and least-privilege MySQL credentials. AI assisted with scaffolding and reviewing the database flow, burst harness, packaging, and docs. The deliberate choices are MySQL/InnoDB, show-row serialization, all-or-nothing requests, explicit cancellation, and consistency over availability. The author should independently run the MySQL-backed burst test and inspect transaction behavior before treating the result as production-ready.

Next: execute the 20,000-request test against the actual MySQL version and hosting tier, add PostgreSQL-independent automated integration tests, measure lock waits and pool saturation, add latency histograms and alerts, and replace sample HMAC auth with a production identity provider.
