"""FastAPI entrypoint: production-shaped chat API + storefront UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .agent import AgentError, chat, healthcheck
from .config import (
    BASE_DIR,
    CORS_ORIGINS,
    IS_PRODUCTION,
    MAX_BODY_BYTES,
    MAX_HISTORY_CONTENT_LENGTH,
    MAX_MESSAGE_LENGTH,
)
from .db import init_db
from .policies import get_sections
from .security import (
    client_key,
    daily_budget,
    normalize_history,
    public_activity,
    rate_limiter,
    review_tool_calls,
    sanitize_text,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("emporio.api")


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
    content: str

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return sanitize_text(value, MAX_HISTORY_CONTENT_LENGTH)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    # Not capped here: older turns are dropped by normalize_history instead of failing the request.
    history: list[ChatMessage] = Field(default_factory=list)
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
    response: Response
    length = request.headers.get("content-length")
    if request.method == "POST" and length is None:
        response = JSONResponse(status_code=411, content={"detail": "Requisição inválida."})
    elif request.method == "POST" and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        response = JSONResponse(status_code=413, content={"detail": "Mensagem grande demais."})
    else:
        response = await call_next(request)
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
    daily_budget.consume()
    history = normalize_history(payload.history)

    try:
        result = chat(payload.message, history=history)
    except AgentError as exc:
        # Setup hints (e.g. missing API key) are only useful to a local developer.
        detail = str(exc) if not IS_PRODUCTION and exc.developer_hint else (
            "Atendimento temporariamente indisponível. Tente novamente em instantes "
            "ou fale com a gente pelo WhatsApp (67) 3321-4500."
        )
        raise HTTPException(status_code=503, detail=detail) from exc
    except Exception:
        logger.exception("Unexpected error in /api/chat")
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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    fields = {str(err.get("loc", ["", ""])[1]) for err in exc.errors() if len(err.get("loc", ())) > 1}
    detail = (
        f"A mensagem precisa ter entre 1 e {MAX_MESSAGE_LENGTH} caracteres."
        if "message" in fields
        else "Não entendi a requisição. Recarregue a página e tente de novo."
    )
    return JSONResponse(status_code=422, content={"detail": detail})


def run() -> None:
    import uvicorn
    from .config import HOST, PORT

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    run()
