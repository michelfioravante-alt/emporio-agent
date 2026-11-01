import os
import sys
import tempfile
from pathlib import Path

# Must run before app.config is imported: tests use a throwaway database and no real API key.
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "emporio-test.db")
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["APP_ENV"] = "development"
os.environ.pop("VERCEL", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.db import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    init_db(force=True)
