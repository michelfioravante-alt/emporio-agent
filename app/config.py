from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "emporio.db"

load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# Runtime mode: development keeps OpenAPI docs; production hides them.
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

MAX_TOOL_ROUNDS = 6
MAX_MESSAGE_LENGTH = 1500
MAX_HISTORY_MESSAGES = 12
# Assistant replies can be longer than user messages; history is truncated, never rejected.
MAX_HISTORY_CONTENT_LENGTH = 4000
MAX_BODY_BYTES = 100_000
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

# Comma-separated origins. Empty = same-origin only (no CORS star).
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    if origin.strip()
]
