"""Production-minded request guards and response redaction."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any

from fastapi import HTTPException, Request

from .config import (
    DAILY_CHAT_LIMIT,
    MAX_HISTORY_CONTENT_LENGTH,
    MAX_HISTORY_MESSAGES,
    MAX_MESSAGE_LENGTH,
    RATE_LIMIT_BURST,
    RATE_LIMIT_WINDOW_SECONDS,
    TRUST_PROXY_HEADERS,
)

ALLOWED_ROLES = {"user", "assistant"}

# Soft prompt-injection markers. We do not block the chat; matching messages
# are wrapped with a reminder that the text is untrusted customer input.
# Real protection lives in the tools (authorization is enforced in code).
_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions"),
    re.compile(r"(?i)system\s*prompt"),
    re.compile(r"(?i)developer\s*(message|mode)"),
    re.compile(r"(?i)reveal\s+(your|the)\s+(hidden\s+)?prompt"),
    re.compile(r"(?i)ignor[ea]\s+(todas\s+)?(as\s+)?(suas\s+)?(instru[cç][oõ]es|regras)"),
    re.compile(r"(?i)prompt\s+(do|de)\s+sistema"),
    re.compile(r"(?i)modo\s+(desenvolvedor|dev|admin)"),
    re.compile(r"(?i)(revele|mostre|mostra|me\s+d[aê])\s+(o\s+|seu\s+)?(prompt|instru[cç][oõ]es)"),
    re.compile(r"(?i)\bjailbreak\b"),
]

TOOL_LABELS = {
    "search_products": "Catálogo de instrumentos",
    "get_product_details": "Detalhes do produto",
    "list_categories": "Categorias da loja",
    "get_order_status": "Status do pedido",
    "get_store_policies": "Políticas da loja",
    "get_store_profile": "Dados da loja",
    "create_support_ticket": "Equipe de atendimento",
}


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 10_000:
                self._prune(now)
            bucket = self._hits[key]
            while bucket and now - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                raise HTTPException(
                    status_code=429,
                    detail="Muitas mensagens em pouco tempo. Aguarde um instante e tente de novo.",
                )
            bucket.append(now)

    def _prune(self, now: float) -> None:
        stale = [key for key, bucket in self._hits.items() if not bucket or now - bucket[-1] > self.window]
        for key in stale:
            del self._hits[key]


class DailyBudget:
    """Caps LLM calls per UTC day so a public demo cannot drain the API quota."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._day = ""
        self._count = 0
        self._lock = Lock()

    def consume(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock:
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= self.limit:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Nosso atendimento online atingiu o limite de hoje. "
                        "Fale com a gente pelo WhatsApp (67) 3321-4500."
                    ),
                )
            self._count += 1


rate_limiter = RateLimiter(RATE_LIMIT_BURST, RATE_LIMIT_WINDOW_SECONDS)
daily_budget = DailyBudget(DAILY_CHAT_LIMIT)


def client_key(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:128]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def sanitize_text(value: str, max_length: int = MAX_MESSAGE_LENGTH) -> str:
    text = (value or "").replace("\x00", "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if len(text) > max_length:
        text = text[:max_length]
    return text


def looks_like_injection(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _INJECTION_PATTERNS)


def normalize_history(raw_history: list[Any]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in raw_history[-MAX_HISTORY_MESSAGES:]:
        role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None)
        content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else None)
        if role not in ALLOWED_ROLES:
            continue
        text = sanitize_text(str(content or ""), MAX_HISTORY_CONTENT_LENGTH)
        if not text:
            continue
        cleaned.append({"role": role, "content": text})
    return cleaned


def public_activity(tool_calls: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Customer-safe activity feed. No raw payloads, no PII dumps."""
    activity: list[dict[str, str]] = []
    for call in tool_calls:
        name = call.get("name", "")
        label = TOOL_LABELS.get(name, "Consulta interna")
        args = call.get("arguments") or {}
        detail = _activity_detail(name, args, call.get("result"))
        activity.append({"label": label, "detail": detail})
    return activity


def _activity_detail(name: str, args: dict[str, Any], result: Any) -> str:
    if name == "search_products":
        bits = []
        if args.get("query"):
            bits.append(f"busca “{str(args['query'])[:40]}”")
        if args.get("category"):
            bits.append(f"categoria {str(args['category'])[:30]}")
        if args.get("max_price") is not None:
            bits.append(f"até R$ {args['max_price']}")
        count = None
        if isinstance(result, dict):
            count = result.get("total_count")
        suffix = f" · {count} item(ns)" if count is not None else ""
        return (", ".join(bits) or "filtros do catálogo") + suffix

    if name == "get_product_details":
        if args.get("name"):
            return f"produto “{str(args['name'])[:50]}”"
        if args.get("product_id") is not None:
            return f"produto #{args['product_id']}"
        return "consulta de produto"

    if name == "get_order_status":
        order_id = args.get("order_id")
        authorized = isinstance(result, dict) and result.get("authorized")
        if authorized:
            return f"pedido #{order_id} · identificação validada"
        return f"pedido #{order_id} · aguardando/validando identificação"

    if name == "get_store_policies":
        topic = str(args.get("topic") or "geral")[:40]
        return f"tema: {topic}"

    if name == "list_categories":
        return "lista de categorias"

    if name == "get_store_profile":
        return "endereço, contatos e horário"

    if name == "create_support_ticket":
        if isinstance(result, dict) and result.get("created"):
            return f"chamado {result.get('protocol')} registrado"
        return "aguardando dados para registrar o chamado"

    return "consulta concluída"


def review_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Redacted technical trace for reviewers. Still avoids leaking secrets."""
    safe: list[dict[str, Any]] = []
    for call in tool_calls:
        safe.append(
            {
                "name": call.get("name"),
                "arguments": _redact_args(call.get("arguments") or {}),
                "result_summary": _summarize_result(call.get("name"), call.get("result")),
            }
        )
    return safe


def _redact_args(args: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(args)
    if "phone" in redacted and redacted["phone"]:
        digits = re.sub(r"\D+", "", str(redacted["phone"]))
        redacted["phone"] = f"***{digits[-4:]}" if len(digits) >= 4 else "***"
    if "customer_name" in redacted and redacted["customer_name"]:
        name = str(redacted["customer_name"]).strip()
        parts = name.split()
        redacted["customer_name"] = (parts[0] + (" ***" if len(parts) > 1 else "")) if parts else "***"
    if "contact" in redacted and redacted["contact"]:
        redacted["contact"] = "***"
    if "summary" in redacted and redacted["summary"]:
        redacted["summary"] = f"<{len(str(redacted['summary']))} chars>"
    return redacted


def _summarize_result(name: str | None, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"ok": True}
    if name == "search_products":
        return {"total_count": result.get("total_count"), "returned": result.get("returned")}
    if name == "get_product_details":
        product = result.get("product") or {}
        return {
            "found": result.get("found"),
            "product_id": product.get("product_id"),
            "available_for_sale": product.get("available_for_sale"),
        }
    if name == "get_order_status":
        return {
            "found": result.get("found"),
            "authorized": result.get("authorized"),
            "status": (result.get("order") or {}).get("status") if result.get("authorized") else None,
        }
    if name == "create_support_ticket":
        return {"created": result.get("created"), "protocol": result.get("protocol")}
    if name == "get_store_policies":
        sections = result.get("sections") or []
        return {"sections": [s.get("section_id") for s in sections if isinstance(s, dict)]}
    if "error" in result:
        return {"error": "tool_error"}
    return {"ok": True}
