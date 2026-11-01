from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes"}


# Vercel sets VERCEL=1. Its filesystem is read-only except /tmp, and it
# overwrites X-Forwarded-For, so the client IP header can be trusted there.
ON_VERCEL = bool(os.getenv("VERCEL"))

DB_PATH = Path(os.getenv("DB_PATH") or ("/tmp/emporio.db" if ON_VERCEL else BASE_DIR / "emporio.db"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# Runtime mode: development keeps OpenAPI docs and the review panel; production hides them.
APP_ENV = os.getenv("APP_ENV", "production" if ON_VERCEL else "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

MAX_TOOL_ROUNDS = 6
MAX_OUTPUT_TOKENS = 1024
MAX_MESSAGE_LENGTH = 1500
MAX_HISTORY_MESSAGES = 12
# Assistant replies can be longer than user messages; history is truncated, never rejected.
MAX_HISTORY_CONTENT_LENGTH = 4000
MAX_BODY_BYTES = 100_000
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
# Protects the LLM quota/billing of a public deployment. Counted per process.
DAILY_CHAT_LIMIT = int(os.getenv("DAILY_CHAT_LIMIT", "500"))
# Only trust X-Forwarded-For behind a proxy that overwrites it (e.g. Vercel).
TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", ON_VERCEL)

# Comma-separated origins. Empty = same-origin only (no CORS star).
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    if origin.strip()
]
