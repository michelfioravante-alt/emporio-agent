"""Callable tools exposed to the LLM.

Structured store data goes through SQL. Policy text goes through
section search. That split is intentional: numbers must be exact,
policies need semantic topic matching.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
from datetime import date, timedelta
from typing import Any

from .clock import now_local, store_context
from .db import connect, norm_text
from .policies import search_policies

logger = logging.getLogger("emporio.tools")

TICKET_REASONS = {"reclamacao", "cancelamento", "troca_devolucao", "defeito_garantia", "outro"}

TOOL_DECLARATIONS = [
    {
        "name": "search_products",
        "description": (
            "Search the product catalog by keywords, category and/or price. "
            "Use for availability, price and promotion questions. Results are sorted "
            "by final price and include total_count (all matches) and returned (this page)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Product keywords only (brand, model, material, feature), e.g. 'Takamine', "
                        "'nylon', 'eletroacústico'. Accents optional. Never put prices here."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": "Category name, e.g. violões, guitarras, ukuleles, baixos.",
                },
                "max_price": {
                    "type": "number",
                    "description": "Maximum final price in BRL (promotional price when there is an active promotion).",
                },
                "min_price": {
                    "type": "number",
                    "description": "Minimum final price in BRL.",
                },
                "only_available": {
                    "type": "boolean",
                    "description": "If true, only active products with stock > 0. Default true.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return. Default 8.",
                },
            },
        },
    },
    {
        "name": "get_product_details",
        "description": (
            "Get one product by id or name (partial ok), with active promotion, PIX price, "
            "credit-card installments and, when unavailable, similar available alternatives."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "name": {"type": "string", "description": "Product name or fragment."},
            },
        },
    },
    {
        "name": "list_categories",
        "description": "List instrument categories sold by the store.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_order_status",
        "description": (
            "Look up an order. Requires order_id plus the customer's FULL name "
            "or phone with DDD, exactly as given by the customer. Never guess or "
            "reuse identification from other orders."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "customer_name": {"type": "string"},
                "phone": {"type": "string"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_store_policies",
        "description": (
            "Retrieve store policy sections: hours, payments, exchanges, shipping, "
            "promotions rules, warranty, WhatsApp guidelines, privacy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic in Portuguese or English, e.g. trocas, frete, horário, pagamento.",
                }
            },
            "required": ["topic"],
        },
    },
    {
        "name": "get_store_profile",
        "description": "Return store address, contacts, opening hours and whether it is open right now.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "create_support_ticket",
        "description": (
            "Hand the case over to the human team (complaint, cancellation request, defect, "
            "warranty, or anything the assistant cannot solve). Only call after the customer "
            "agreed and gave name and a contact (phone with DDD or e-mail). Returns a protocol number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": sorted(TICKET_REASONS),
                },
                "customer_name": {"type": "string"},
                "contact": {"type": "string", "description": "Phone with DDD or e-mail given by the customer."},
                "summary": {"type": "string", "description": "Short neutral summary of the request, max 500 chars."},
                "order_id": {"type": "integer"},
            },
            "required": ["reason", "customer_name", "contact", "summary"],
        },
    },
]


_STOPWORDS = {
    "de", "da", "do", "das", "dos", "com", "para", "pra", "e", "o", "a", "os", "as",
    "um", "uma", "em", "ate", "reais", "rs", "preco", "valor",
}


def _stem(token: str) -> str:
    """Tiny Portuguese plural folding: violões -> violao, guitarras -> guitarra."""
    for suffix, replacement in (("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el")):
        if token.endswith(suffix) and len(token) > 4:
            return token[: -len(suffix)] + replacement
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def _search_tokens(value: str | None) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", norm_text(value))
    return [_stem(tok) for tok in tokens if len(tok) >= 2 and tok not in _STOPWORDS]


def _digits(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def _money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


PIX_DISCOUNT_PERCENT = 5

# Policy 3.1. Assumption: "até 3x ... exceto abaixo de R$ 50,00" = minimum installment of R$ 50.
_INSTALLMENT_MINIMUMS = [(3, 50.0), (6, 80.0), (12, 100.0)]


def _installments(total: float) -> dict[str, Any]:
    best = 1
    for n in range(2, 13):
        minimum = next(value for max_n, value in _INSTALLMENT_MINIMUMS if n <= max_n)
        if total / n >= minimum:
            best = n
    return {"max_installments_no_interest": best, "installment_value_brl": _money(total / best)}


def _parse_specs(raw: str | None) -> Any:
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return raw


def _product_row(row: sqlite3.Row) -> dict[str, Any]:
    list_price = float(row["price_brl"])
    discount = row["discount_percent"]
    promo_price = _money(list_price * (1 - float(discount) / 100)) if discount is not None else None
    final_price = promo_price if promo_price is not None else list_price

    available = row["status"] == "active" and int(row["stock_quantity"]) > 0
    return {
        "product_id": row["product_id"],
        "name": row["name"],
        "category": row["category_name"],
        "list_price_brl": _money(list_price),
        "active_promotion": (
            {
                "campaign": row["promo_description"],
                "discount_percent": float(discount),
                "promo_price_brl": promo_price,
                "display_rule": "Always show original price, discount % and promotional price together.",
            }
            if promo_price is not None
            else None
        ),
        # PIX 5% never stacks with a promotion (policy 6.2).
        "pix_price_brl": None if promo_price is not None else _money(list_price * (1 - PIX_DISCOUNT_PERCENT / 100)),
        "credit_card": _installments(final_price),
        "stock_quantity": int(row["stock_quantity"]),
        "status": row["status"],
        "available_for_sale": available,
        "description": row["description"],
        "specs": _parse_specs(row["specs"]),
    }


# Wrapped in a subquery so filters can use the computed effective price and search text.
_PRODUCT_SELECT = """
SELECT * FROM (
    SELECT
        p.product_id,
        p.name,
        p.price_brl,
        p.description,
        p.stock_quantity,
        p.status,
        p.specs,
        p.category_id,
        c.name AS category_name,
        pr.discount_percent,
        pr.description AS promo_description,
        ROUND(p.price_brl * (1 - COALESCE(pr.discount_percent, 0) / 100.0), 2) AS effective_price,
        norm_text(p.name || ' ' || COALESCE(p.description, '') || ' ' || COALESCE(p.specs, '') || ' ' || c.name) AS search_text
    FROM products p
    JOIN categories c ON c.category_id = p.category_id
    LEFT JOIN (
        SELECT product_id, MAX(discount_percent) AS discount_percent, description
        FROM promotions
        WHERE is_active = 1
        GROUP BY product_id
    ) pr ON pr.product_id = p.product_id
) AS catalog
"""

_AVAILABLE_CLAUSE = "status = 'active' AND stock_quantity > 0"


def _category_ids(conn: sqlite3.Connection, category: str) -> list[int]:
    wanted = _search_tokens(category)
    if not wanted:
        return []
    ids = []
    for row in conn.execute("SELECT category_id, name FROM categories"):
        tokens = set(_search_tokens(row["name"]))
        if all(any(tok.startswith(w) or w.startswith(tok) for tok in tokens) for w in wanted):
            ids.append(row["category_id"])
    return ids


def search_products(
    query: str | None = None,
    category: str | None = None,
    max_price: float | None = None,
    min_price: float | None = None,
    only_available: bool = True,
    limit: int = 8,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []

    if only_available:
        clauses.append(_AVAILABLE_CLAUSE)

    for token in _search_tokens(query):
        clauses.append("search_text LIKE ?")
        params.append(f"%{token}%")

    if max_price is not None:
        clauses.append("effective_price <= ?")
        params.append(float(max_price))

    if min_price is not None:
        clauses.append("effective_price >= ?")
        params.append(float(min_price))

    with connect() as conn:
        if category:
            ids = _category_ids(conn, category)
            if not ids:
                return {
                    "total_count": 0,
                    "products": [],
                    "hint": "Unknown category. Call list_categories to see what the store sells.",
                }
            clauses.append(f"category_id IN ({','.join('?' * len(ids))})")
            params.extend(ids)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = conn.execute(f"SELECT COUNT(*) FROM ({_PRODUCT_SELECT} {where})", params).fetchone()[0]
        page_size = max(1, min(int(limit or 8), 20))
        rows = conn.execute(
            f"{_PRODUCT_SELECT} {where} ORDER BY effective_price ASC LIMIT ?",
            [*params, page_size],
        ).fetchall()

    return {
        "total_count": total,
        "returned": len(rows),
        "products": [_product_row(row) for row in rows],
        "hint": (
            "No match: suggest broadening filters or another category."
            if total == 0
            else "If total_count > returned, say there are more options and offer to filter further."
        ),
    }


def _alternatives(conn: sqlite3.Connection, row: sqlite3.Row, limit: int = 3) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""{_PRODUCT_SELECT}
        WHERE {_AVAILABLE_CLAUSE} AND category_id = ? AND product_id != ?
        ORDER BY ABS(effective_price - ?) ASC LIMIT ?""",
        (row["category_id"], row["product_id"], row["effective_price"], limit),
    ).fetchall()
    return [
        {
            "product_id": alt["product_id"],
            "name": alt["name"],
            "price_brl": _money(alt["effective_price"]),
        }
        for alt in rows
    ]


def get_product_details(product_id: int | None = None, name: str | None = None) -> dict[str, Any]:
    if product_id is None and not name:
        return {"error": "Provide product_id or name."}

    with connect() as conn:
        if product_id is not None:
            row = conn.execute(f"{_PRODUCT_SELECT} WHERE product_id = ?", (int(product_id),)).fetchone()
        else:
            tokens = _search_tokens(name)
            if not tokens:
                return {"found": False, "message": "Product not found in catalog."}
            where = " AND ".join("norm_text(name) LIKE ?" for _ in tokens)
            row = conn.execute(
                f"{_PRODUCT_SELECT} WHERE {where} ORDER BY LENGTH(name) ASC LIMIT 1",
                [f"%{tok}%" for tok in tokens],
            ).fetchone()

        if not row:
            return {"found": False, "message": "Product not found in catalog. Try search_products with fewer words."}

        result: dict[str, Any] = {"found": True, "product": _product_row(row)}
        if not result["product"]["available_for_sale"]:
            result["available_alternatives"] = _alternatives(conn, row)
    return result


def list_categories() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.category_id, c.name, c.description, COUNT(p.product_id) AS product_count
            FROM categories c
            LEFT JOIN products p ON p.category_id = c.category_id
            GROUP BY c.category_id
            ORDER BY c.category_id
            """
        ).fetchall()
    return {
        "categories": [
            {
                "category_id": row["category_id"],
                "name": row["name"],
                "description": row["description"],
                "product_count": row["product_count"],
            }
            for row in rows
        ],
        "scope_note": "Store sells musical instruments only. No accessories such as strings, cables, picks, pedals, cases or amplifiers.",
    }


_NAME_PARTICLES = {"da", "de", "do", "das", "dos", "e"}

# Same answer for "order does not exist" and "identity does not match",
# so the tool cannot be used to discover which order numbers exist.
_ORDER_NOT_VERIFIED = {
    "authorized": False,
    "message": (
        "No order matches this number and identification. Ask the customer to double-check "
        "the order number and the full name or phone used in the purchase. Do not reveal anything else."
    ),
}


def _name_tokens(value: str | None) -> list[str]:
    return [tok for tok in re.findall(r"[a-z]+", norm_text(value)) if tok not in _NAME_PARTICLES]


def _name_matches(given: str | None, stored: str) -> bool:
    given_tokens = _name_tokens(given)
    return len(given_tokens) >= 2 and given_tokens == _name_tokens(stored)


def _phone_matches(given: str | None, stored: str | None) -> bool:
    given_digits, stored_digits = _digits(given), _digits(stored)
    # Require at least DDD + number; accept an optional +55 prefix.
    return len(given_digits) >= 10 and given_digits[-11:] == stored_digits[-11:]


ORDER_STATUS_PT = {
    "pending": "pendente (aguardando confirmação do pagamento)",
    "confirmed": "confirmado (em separação, ainda não enviado)",
    "shipped": "enviado (em transporte)",
    "delivered": "entregue",
    "cancelled": "cancelado",
}

_PAYMENT_PT = {"pix": "PIX", "boleto": "boleto bancário", "debit": "cartão de débito"}


def _payment_label(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.fullmatch(r"credit_(\d+)x", raw)
    if match:
        return f"cartão de crédito em {match.group(1)}x"
    return _PAYMENT_PT.get(raw, raw)


def _window(start: date, days: int, today: date) -> dict[str, Any]:
    deadline = start + timedelta(days=days)
    return {
        "deadline": deadline.isoformat(),
        "within_deadline": today <= deadline,
        "days_left": max((deadline - today).days, 0),
    }


def _after_sales(status: str, order_date: str | None, estimated_delivery: str | None) -> dict[str, Any]:
    """Deterministic policy deadlines (sections 4 and 8) so the LLM never does date math."""
    today = now_local().date()
    if status == "cancelled":
        return {"note": "Cancelled order: no return or exchange applies."}
    if status in {"pending", "confirmed"}:
        return {
            "note": (
                "Not shipped yet. Return windows start on delivery. Cancellation requests "
                "are handled by the human team: offer create_support_ticket."
            )
        }
    if status == "shipped":
        return {"note": "In transit. The 7-day withdrawal right starts when the customer receives it."}

    # Assumption: the dataset has no real delivery date, so estimated_delivery is used as receipt date.
    received = date.fromisoformat(estimated_delivery) if estimated_delivery else None
    purchased = date.fromisoformat(order_date) if order_date else None
    result: dict[str, Any] = {
        "today": today.isoformat(),
        "receipt_date_assumed": received.isoformat() if received else None,
    }
    if received:
        result["withdrawal_7_days"] = _window(received, 7, today)
        result["preference_exchange_7_days"] = _window(received, 7, today)
        result["legal_warranty_90_days"] = _window(received, 90, today)
    if purchased:
        result["defect_exchange_30_days"] = _window(purchased, 30, today)
    result["after_deadlines"] = (
        "If defect exchange (30 days) expired, the customer uses the manufacturer warranty; "
        "the store can intermediate on request."
    )
    return result


def get_order_status(
    order_id: int,
    customer_name: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    if not customer_name and not phone:
        return {
            "authorized": False,
            "message": "Ask the customer for the order number and full name or phone (with DDD) before revealing order details.",
        }

    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return dict(_ORDER_NOT_VERIFIED)

    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                o.*,
                c.name AS customer_name,
                c.phone AS customer_phone,
                c.email AS customer_email,
                c.city AS customer_city
            FROM orders o
            JOIN customers c ON c.customer_id = o.customer_id
            WHERE o.order_id = ?
            """,
            (order_id,),
        ).fetchone()

        if not row:
            return dict(_ORDER_NOT_VERIFIED)

        if not (
            _name_matches(customer_name, row["customer_name"])
            or _phone_matches(phone, row["customer_phone"])
        ):
            return dict(_ORDER_NOT_VERIFIED)

        items = conn.execute(
            """
            SELECT oi.quantity, p.product_id, p.name, p.price_brl
            FROM order_items oi
            JOIN products p ON p.product_id = oi.product_id
            WHERE oi.order_id = ?
            """,
            (order_id,),
        ).fetchall()

    return {
        "authorized": True,
        "found": True,
        "order": {
            "order_id": row["order_id"],
            "status": row["status"],
            "status_pt": ORDER_STATUS_PT.get(row["status"], row["status"]),
            "order_date": row["order_date"],
            "total_brl": _money(row["total_brl"]),
            "payment_method": _payment_label(row["payment_method"]),
            "after_sales": _after_sales(row["status"], row["order_date"], row["estimated_delivery"]),
            "tracking_code": row["tracking_code"] or None,
            "estimated_delivery": row["estimated_delivery"] or None,
            "notes": row["notes"] or None,
            "customer": {
                "name": row["customer_name"],
                "city": row["customer_city"],
            },
            "items": [
                {
                    "product_id": item["product_id"],
                    "name": item["name"],
                    "quantity": item["quantity"],
                    "unit_price_brl": _money(item["price_brl"]),
                }
                for item in items
            ],
        },
    }


def get_store_policies(topic: str) -> dict[str, Any]:
    sections = search_policies(topic, limit=2)
    return {
        "topic": topic,
        "sections": [
            {
                "section_id": section.section_id,
                "title": section.title,
                "content": section.text,
            }
            for section in sections
        ],
    }


def get_store_profile() -> dict[str, Any]:
    return {
        "name": "Empório da Música",
        "tagline": "Sua música começa aqui.",
        "city": "Campo Grande - MS",
        "address": "Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333",
        # The manual lists (67) 3341-4444 as phone/WhatsApp on the cover and in 1.2,
        # but (67) 3321-4500 as the WhatsApp channel in section 7. Both are kept as written.
        "phone": "(67) 3341-4444",
        "whatsapp_support": "(67) 3321-4500",
        "email": "contato@emporiodamusica.com.br",
        "hours": {
            "monday_to_friday": "09:00 às 18:00",
            "saturday": "09:00 às 13:00",
            "sunday_and_holidays": "Fechado",
            "special_dates": "Black Friday, aniversário da loja e Natal podem ter horário estendido.",
        },
        "now": store_context(),
        "scope": "Musical instruments only. No accessories.",
        "tone": "Informal but professional, like a music-savvy friend.",
    }


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_support_ticket(
    reason: str,
    customer_name: str,
    contact: str,
    summary: str,
    order_id: int | None = None,
) -> dict[str, Any]:
    reason = (reason or "").strip().lower()
    customer_name = (customer_name or "").strip()[:120]
    contact = (contact or "").strip()[:120]
    summary = (summary or "").strip()[:500]

    if reason not in TICKET_REASONS:
        reason = "outro"
    if len(_name_tokens(customer_name)) < 1:
        return {"created": False, "message": "Ask the customer for their name."}
    if not (len(_digits(contact)) >= 10 or _EMAIL_RE.match(contact)):
        return {"created": False, "message": "Ask for a valid phone with DDD or e-mail."}
    if len(summary) < 10:
        return {"created": False, "message": "Ask the customer to briefly describe the issue."}

    now = now_local()
    # Random suffix: protocols must not be guessable or enumerable.
    protocol = f"EMP-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO support_tickets (protocol, created_at, reason, customer_name, contact, order_id, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (protocol, now.isoformat(timespec="seconds"), reason, customer_name, contact,
             int(order_id) if order_id is not None else None, summary),
        )
    logger.info("Support ticket %s created (reason=%s)", protocol, reason)
    return {
        "created": True,
        "protocol": protocol,
        "response_time": "até 24 horas úteis",
        "message": "Give the protocol to the customer and say the team will contact them.",
    }


TOOL_HANDLERS = {
    "create_support_ticket": create_support_ticket,
    "search_products": search_products,
    "get_product_details": get_product_details,
    "list_categories": list_categories,
    "get_order_status": get_order_status,
    "get_store_policies": get_store_policies,
    "get_store_profile": get_store_profile,
}


def run_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {"error": "unknown_tool"}
    try:
        return handler(**(arguments or {}))
    except TypeError:
        logger.warning("Invalid arguments for tool %s", name)
        return {"error": "invalid_arguments", "hint": "Check the tool parameters and try again."}
    except Exception:  # noqa: BLE001 - the model gets a generic error, details stay in server logs
        logger.exception("Tool %s failed", name)
        return {"error": "tool_failed", "hint": "Tell the customer the system is unavailable and offer human support."}
