"""FastAPI entrypoint: production-shaped chat API + storefront UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .agent import AgentError, chat, healthcheck
from .config import (
    BASE_DIR,
    CORS_ORIGINS,
    IS_PRODUCTION,
    MAX_HISTORY_MESSAGES,
    MAX_MESSAGE_LENGTH,
)
from .db import init_db
from .policies import get_sections
from .security import (
    client_key,
    normalize_history,
    public_activity,
    rate_limiter,
    review_tool_calls,
    sanitize_text,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(force=True)
    get_sections()
    yield


app = FastAPI(
    title="Empório da Música",
    version="1.0.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    max_age=600,
)

static_dir = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        cleaned = sanitize_text(value)
        if not cleaned:
            raise ValueError("empty content")
        return cleaned


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    history: list[ChatMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    include_debug: bool = False

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        cleaned = sanitize_text(value)
        if not cleaned:
            raise ValueError("empty message")
        return cleaned


class ActivityItem(BaseModel):
    label: str
    detail: str


class ChatResponse(BaseModel):
    reply: str
    activity: list[ActivityItem]
    debug: list[dict[str, Any]] | None = None


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    # Tight CSP for this single-page local app + Google Fonts + Unsplash hero.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' https://images.unsplash.com data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    # Public health should not expose model names or key state in production UX.
    ok = healthcheck()["api_key_configured"]
    return {
        "status": "ok" if ok else "degraded",
        "service": "emporio-atendimento",
    }


@app.post("/api/chat", response_model=ChatResponse)
def api_chat(payload: ChatRequest, request: Request) -> ChatResponse:
    rate_limiter.check(client_key(request))
    history = normalize_history(payload.history)

    try:
        result = chat(payload.message, history=history)
    except AgentError as exc:
        # Keep client messages friendly; avoid leaking setup paths in production.
        detail = str(exc)
        if IS_PRODUCTION:
            detail = "Atendimento temporariamente indisponível. Tente novamente em instantes."
        raise HTTPException(status_code=503, detail=detail) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Não foi possível concluir o atendimento agora. Tente novamente.",
        ) from None

    response = ChatResponse(
        reply=result["reply"],
        activity=[ActivityItem(**item) for item in public_activity(result.get("tool_calls", []))],
        debug=None,
    )
    if payload.include_debug and not IS_PRODUCTION:
        response.debug = review_tool_calls(result.get("tool_calls", []))
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def run() -> None:
    import uvicorn
    from .config import HOST, PORT

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    run()
