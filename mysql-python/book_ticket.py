"""Interactive local demo: start the API if needed and book one cricket seat."""

import os
import subprocess
import sys
import time
import uuid
import webbrowser
from pathlib import Path

import httpx

from app.auth import token_for


ROOT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:8000"
SHOW_ID_FILE = ROOT / "demo_show_id.txt"
DEMO_SEATS = [f"A{number}" for number in range(1, 11)] + [f"B{number}" for number in range(1, 11)]


def start_api_if_needed() -> None:
    """Start Uvicorn in a separate console if this API is not already ready."""
    try:
        with httpx.Client(timeout=2) as client:
            if client.get(f"{BASE_URL}/ready").status_code == 200:
                return
    except httpx.HTTPError:
        pass

    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError("Python environment missing. Run setup_local.py first.")

    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(
        [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
        creationflags=flags,
    )

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1) as client:
                if client.get(f"{BASE_URL}/ready").status_code == 200:
                    return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError("API did not become ready. Check the API terminal for MySQL errors.")


def get_or_create_demo_show(client: httpx.Client) -> dict:
    """Reuse the saved demo match, or create one on the first run."""
    if SHOW_ID_FILE.exists():
        show_id = SHOW_ID_FILE.read_text(encoding="utf-8").strip()
        if show_id:
            response = client.get(f"{BASE_URL}/shows/{show_id}")
            if response.status_code == 200:
                return response.json()

    response = client.post(
        f"{BASE_URL}/shows",
        headers={"Authorization": f"Bearer {token_for('admin')}"},
        json={
            "name": "Local Cricket Demo",
            "seats": DEMO_SEATS,
            "price_paise": 25000,
            "per_user_limit": 4,
        },
    )
    response.raise_for_status()
    show = response.json()
    SHOW_ID_FILE.write_text(show["id"], encoding="utf-8")
    return show


def main() -> int:
    try:
        start_api_if_needed()
        with httpx.Client(timeout=15) as client:
            show = get_or_create_demo_show(client)
            available = [seat["label"] for seat in show["seats"] if seat["status"] == "available"]
            print(f"\nMatch: {show['name']}")
            print(f"Available seats: {', '.join(available) if available else 'none'}")
            if not available:
                print("This demo match has no available seats. Remove demo_show_id.txt to create a fresh demo match.")
                return 1

            buyer = input("Buyer name/ID [fan-1]: ").strip() or "fan-1"
            seat = input("Seat label: ").strip().upper()
            if seat not in available:
                print("That seat is not available for this match.")
                return 1

            idempotency_key = str(uuid.uuid4())
            response = client.post(
                f"{BASE_URL}/shows/{show['id']}/reserve",
                headers={"Authorization": f"Bearer {token_for(buyer)}"},
                json={"seats": [seat], "idempotency_key": idempotency_key},
            )
            if response.status_code == 201:
                booking = response.json()
                print("\nTicket confirmed")
                print(f"Reservation: {booking['reservation_id']}")
                print(f"Match: {show['name']}")
                print(f"Seat: {', '.join(booking['seats'])}")
                print(f"Price: {booking['amount_paise']} paise")
                qr_response = client.get(
                    BASE_URL + booking["ticket_qr_url"],
                    headers={"Authorization": f"Bearer {token_for(buyer)}"},
                )
                qr_response.raise_for_status()
                ticket_dir = ROOT / "tickets"
                ticket_dir.mkdir(exist_ok=True)
                qr_path = ticket_dir / f"{booking['reservation_id']}.png"
                qr_path.write_bytes(qr_response.content)
                print(f"Ticket QR saved: {qr_path}")
                try:
                    if os.name == "nt":
                        os.startfile(qr_path)
                    else:
                        webbrowser.open(qr_path.as_uri())
                except (AttributeError, OSError):
                    print("Open the saved PNG with an image viewer to scan the ticket.")
                return 0

            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            print(f"Booking failed (HTTP {response.status_code}): {detail}")
            return 1
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as error:
        print(f"Could not complete booking: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())