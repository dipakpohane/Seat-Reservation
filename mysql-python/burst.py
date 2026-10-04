import argparse
import concurrent.futures
import hashlib
import hmac
import os
import sys
import threading
import uuid
from collections import Counter

import httpx
from dotenv import load_dotenv

load_dotenv()


def token_for(identity: str, secret: str) -> str:
    signature = hmac.new(secret.encode(), identity.encode(), hashlib.sha256).hexdigest()
    return f"{identity}.{signature}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise hot-seat, limit, retry, cancellation, and reconciliation behavior.")
    parser.add_argument("base_url", nargs="?", default=os.getenv("BASE_URL", "http://localhost:8000"))
    parser.add_argument("--hot-requests", type=int, default=500)
    parser.add_argument("--workers", type=int, default=100)
    args = parser.parse_args()
    if args.hot_requests < 1 or args.workers < 1:
        parser.error("--hot-requests and --workers must be positive")
    secret = os.getenv("AUTH_SECRET", "local-development-secret-change-me")
    base_url = args.base_url.rstrip("/")
    failures = Counter()
    status_counts = Counter()
    failure_lock = threading.Lock()

    def headers(identity: str) -> dict:
        return {"Authorization": f"Bearer {token_for(identity, secret)}"}

    with httpx.Client(timeout=180) as client:
        created = client.post(
            f"{base_url}/shows",
            headers=headers("admin"),
            json={
                "name": f"burst-{uuid.uuid4()}",
                "seats": ["HOT", "LIMIT-1", "LIMIT-2", "LIMIT-3", "LIMIT-4", "LIMIT-5", "PARTIAL", "IDEMP", "CANCEL"],
                "price": 25000,
            },
        )
        created.raise_for_status()
        show_id = created.json()["id"]

        def reserve(identity: str, seats: list[str], key: str):
            try:
                return client.post(
                    f"{base_url}/shows/{show_id}/reserve",
                    headers=headers(identity),
                    json={"seats": seats, "idempotency_key": key},
                )
            except Exception as error:
                with failure_lock:
                    failures["transport-error"] += 1
                return error

        def has_reason(response, reason: str) -> bool:
            if isinstance(response, Exception) or response.status_code != 409:
                return False
            try:
                return response.json().get("reason") == reason
            except ValueError:
                return False

        def count_result(result):
            if isinstance(result, Exception):
                return
            status_counts[result.status_code] += 1
            if result.status_code == 409:
                failures[result.json().get("reason", "unknown-409")] += 1
            elif result.status_code >= 500:
                failures["5xx"] += 1

        hot_tasks = [(f"hot-{index}", ["HOT"], f"hot-key-{index}") for index in range(args.hot_requests)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            hot_results = list(executor.map(lambda task: reserve(*task), hot_tasks))
        for result in hot_results:
            count_result(result)
        hot_wins = sum(not isinstance(result, Exception) and result.status_code == 201 for result in hot_results)
        hot_taken = sum(has_reason(result, "seat-taken") for result in hot_results)
        hot_clean = hot_wins == 1 and hot_taken == args.hot_requests - 1

        limit_tasks = [("limited-user", [f"LIMIT-{index}"], f"limit-key-{index}") for index in range(1, 6)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            limit_results = list(executor.map(lambda task: reserve(*task), limit_tasks))
        for result in limit_results:
            count_result(result)
        limit_wins = sum(not isinstance(result, Exception) and result.status_code == 201 for result in limit_results)
        limit_declines = sum(has_reason(result, "per-user-limit") for result in limit_results)
        limit_clean = limit_wins == 4 and limit_declines == 1

        partial = reserve("partial-user", ["HOT", "PARTIAL"], "partial-key")
        count_result(partial)
        partial_declined = has_reason(partial, "seat-taken")

        replay_tasks = [("replay-user", ["IDEMP"], "same-key") for _ in range(10)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            replay_results = list(executor.map(lambda task: reserve(*task), replay_tasks))
        replay_ids = set()
        for result in replay_results:
            count_result(result)
            if not isinstance(result, Exception) and result.status_code == 201:
                replay_ids.add(result.json()["reservation_id"])
        replay_clean = all(not isinstance(result, Exception) and result.status_code == 201 for result in replay_results) and len(replay_ids) == 1
        changed_key = reserve("replay-user", ["PARTIAL"], "same-key")
        count_result(changed_key)
        changed_key_rejected = has_reason(changed_key, "idempotency-key-reuse")

        cancel = reserve("cancel-owner", ["CANCEL"], "cancel-key")
        count_result(cancel)
        cancel_id = cancel.json()["reservation_id"] if cancel.status_code == 201 else None
        spoof_cancel = client.post(f"{base_url}/reservations/{cancel_id}/cancel", headers=headers("intruder")) if cancel_id else None
        owner_cancel = client.post(f"{base_url}/reservations/{cancel_id}/cancel", headers=headers("cancel-owner")) if cancel_id else None
        spoof_rejected = spoof_cancel is not None and spoof_cancel.status_code == 403
        cancel_released = owner_cancel is not None and owner_cancel.status_code == 200 and owner_cancel.json()["status"] == "cancelled"
        for result in (spoof_cancel, owner_cancel):
            if result is not None:
                status_counts[result.status_code] += 1
                if result.status_code >= 500:
                    failures["5xx"] += 1

        state_response = client.get(f"{base_url}/shows/{show_id}")
        state_response.raise_for_status()
        state = state_response.json()
        counts = state["counts"]
        reconciles = sum(counts.values()) == state["total_seats"]
        partial_free = next(seat for seat in state["seats"] if seat["label"] == "PARTIAL")["status"] == "available"
        cancel_seat_free = next(seat for seat in state["seats"] if seat["label"] == "CANCEL")["status"] == "available"
        print(f"show_id: {show_id}")
        print(f"HTTP outcomes: {dict(status_counts)}")
        print(f"declines/errors: {dict(failures)}")
        print(f"hot seat: {hot_wins} winner(s), {hot_taken} seat-taken conflicts in {args.hot_requests} requests ({'PASS' if hot_clean else 'FAIL'})")
        print(f"per-user limit: {limit_wins} winners, {limit_declines} limit conflicts ({'PASS' if limit_clean else 'FAIL'})")
        print(f"idempotency retries: {'PASS' if replay_clean else 'FAIL'}; changed body rejected: {'PASS' if changed_key_rejected else 'FAIL'}")
        print(f"spoofed cancellation rejected: {'PASS' if spoof_rejected else 'FAIL'}")
        print(f"owner cancellation released seat: {'PASS' if cancel_released and cancel_seat_free else 'FAIL'}")
        print(f"all-or-nothing left PARTIAL available: {'PASS' if partial_declined and partial_free else 'FAIL'}")
        print(f"reconciliation: {counts} / total={state['total_seats']} ({'PASS' if reconciles else 'FAIL'})")
        print(f"metrics: {base_url}/metrics")
        if not hot_clean or not limit_clean or not replay_clean or not changed_key_rejected or not reconciles or not partial_declined or not partial_free or not spoof_rejected or not cancel_released or not cancel_seat_free or failures["5xx"] or failures["transport-error"]:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
