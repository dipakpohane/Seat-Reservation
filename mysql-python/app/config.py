"""Settings shared by API modules."""

import os

from dotenv import load_dotenv

load_dotenv()

AUTH_SECRET = os.getenv("AUTH_SECRET", "local-development-secret-change-me")
DEFAULT_PER_USER_LIMIT = 4
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")