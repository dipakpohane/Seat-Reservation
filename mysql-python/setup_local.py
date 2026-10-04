"""Provision local MySQL access and start the API without displaying secrets."""

import os
import re
import secrets
import socket
from getpass import getpass
from pathlib import Path

import mysql.connector
import uvicorn
from dotenv import dotenv_values, load_dotenv


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
APP_USER = "seat_reservation"


def choose_api_port() -> int:
    """Use 8000 unless another local process is already listening there."""
    for port in (8000, 8001):
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Ports 8000 and 8001 are both in use")


def main() -> None:
    settings = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    host = settings.get("MYSQL_HOST") or "127.0.0.1"
    port = int(settings.get("MYSQL_PORT") or "3306")
    database_name = settings.get("MYSQL_DATABASE") or "seat_reservation"
    if not re.fullmatch(r"[A-Za-z0-9_]+", database_name):
        raise RuntimeError("MYSQL_DATABASE may contain only letters, numbers, and underscores")

    admin_password = getpass("MySQL root password (input hidden): ")
    app_password = secrets.token_urlsafe(32)
    app_secret = secrets.token_urlsafe(48)

    connection = mysql.connector.connect(
        host=host,
        port=port,
        user="root",
        password=admin_password,
        connection_timeout=5,
        autocommit=True,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        )
        cursor.execute(
            f"CREATE USER IF NOT EXISTS '{APP_USER}'@'localhost' IDENTIFIED BY %s",
            (app_password,),
        )
        cursor.execute(
            f"ALTER USER '{APP_USER}'@'localhost' IDENTIFIED BY %s",
            (app_password,),
        )
        cursor.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, REFERENCES "
            f"ON `{database_name}`.* TO '{APP_USER}'@'localhost'"
        )
        cursor.close()
    finally:
        connection.close()

    ENV_FILE.write_text(
        "\n".join([
            "MYSQL_HOST=" + host,
            "MYSQL_PORT=" + str(port),
            "MYSQL_USER=" + APP_USER,
            "MYSQL_PASSWORD=" + app_password,
            "MYSQL_DATABASE=" + database_name,
            "DB_POOL_SIZE=" + (settings.get("DB_POOL_SIZE") or "20"),
            "AUTH_SECRET=" + app_secret,
            "PUBLIC_BASE_URL=" + (settings.get("PUBLIC_BASE_URL") or "http://127.0.0.1:8000"),
            "APP_ENV=development",
            "",
        ]),
        encoding="utf-8",
    )
    load_dotenv(ENV_FILE, override=True)

    api_port = choose_api_port()
    print(f"MySQL setup complete. Starting API at http://127.0.0.1:{api_port}")
    print("Keep this terminal open. Open /ready to check MySQL connectivity.")
    uvicorn.run("app.main:app", host="127.0.0.1", port=api_port, reload=False)


if __name__ == "__main__":
    try:
        main()
    except mysql.connector.Error as error:
        print(f"MySQL setup failed ({error.__class__.__name__}). Check the root password and local MySQL service.")
        raise SystemExit(1) from None
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Local setup failed: {error}")
        raise SystemExit(1) from None