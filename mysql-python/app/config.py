"""Settings shared by API modules."""

import os

from dotenv import load_dotenv

load_dotenv()

AUTH_SECRET = os.getenv("AUTH_SECRET", "local-development-secret-change-me")
DEFAULT_PER_USER_LIMIT = 4