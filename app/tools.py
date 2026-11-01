"""Callable tools exposed to the LLM.

Structured store data goes through SQL. Policy text goes through
section search. That split is intentional: numbers must be exact,
policies need semantic topic matching.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

from .db import connect
from .policies import search_policies

TOOL_DECLARATIONS = [
    {
        "name": "search_products",
        "description": (
            "Search the product catalog by free text, category and/or price. "
            "Use for availability, price and promotion questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text filter on product name/description, e.g. Takamine, nylon, ukulele.",
                },
                "category": {
                    "type": "string",
                    "description": "Category name fragment, e.g. Violões, Guitarras, Ukuleles.",
                },
                "max_price": {
                    "type": "number",
                    "description": "Maximum list price in BRL.",
                },
                "min_price": {
                    "type": "number",
                    "description": "Minimum list price in BRL.",
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
        "description": "Get one product by id or exact/partial name, including active promotion if any.",
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
            "Look up an order. Requires order_id plus customer name or phone "
            "for privacy. Never reveal another customer's data."
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
        "description": "Return short store identity facts used for greetings and contact questions.",
        "parameters": {"type": "object", "properties": {}},
    },
]


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _digits(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def _money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _product_row(row: sqlite3.Row) -> dict[str, Any]:
    list_price = float(row["price_brl"])
    discount = row["discount_percent"]
    promo_active = bool(row["promo_active"]) if row["promo_active"] is not None else False
    promo_price = None
    if promo_active and discount is not None:
        promo_price = _money(list_price * (1 - float(discount) / 100))

    available = row["status"] == "active" and int(row["stock_quantity"]) > 0
    return {
        "product_id": row["product_id"],
        "name": row["name"],
        "category": row["category_name"],
        "list_price_brl": _money(list_price),
        "active_promotion": (
            {
                "description": row["promo_description"],
                "discount_percent": float(discount),
                "promo_price_brl": promo_price,
            }
            if promo_active
            else None
        ),
        "stock_quantity": int(row["stock_quantity"]),
        "status": row["status"],
        "available_for_sale": available,
        "description": row["description"],
        "specs": row["specs"],
        "notes": (
            "PIX 5% discount applies only on non-promotional list prices."
            if not promo_active
            else "Promotional price already includes campaign discount. PIX 5% does not stack."
        ),
    }


_PRODUCT_SELECT = """
SELECT
    p.product_id,
    p.name,
    p.price_brl,
    p.description,
    p.stock_quantity,
    p.status,
    p.specs,
    c.name AS category_name,
    pr.discount_percent,
    pr.description AS promo_description,
    pr.is_active AS promo_active
FROM products p
JOIN categories c ON c.category_id = p.category_id
LEFT JOIN promotions pr
    ON pr.product_id = p.product_id
   AND pr.is_active = 1
"""


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
        clauses.append("p.status = 'active' AND p.stock_quantity > 0")

    if query:
        clauses.append("(LOWER(p.name) LIKE ? OR LOWER(p.description) LIKE ? OR LOWER(p.specs) LIKE ?)")
        like = f"%{query.lower()}%"
        params.extend([like, like, like])

    if category:
        clauses.append("LOWER(c.name) LIKE ?")
        params.append(f"%{category.lower()}%")

    if max_price is not None:
        clauses.append("p.price_brl <= ?")
        params.append(max_price)

    if min_price is not None:
        clauses.append("p.price_brl >= ?")
        params.append(min_price)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"{_PRODUCT_SELECT} {where} ORDER BY p.price_brl ASC LIMIT ?"
    params.append(max(1, min(int(limit or 8), 20)))

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    return {
        "count": len(rows),
        "products": [_product_row(row) for row in rows],
        "hint": "If count is 0, suggest broadening filters or another category.",
    }


def get_product_details(product_id: int | None = None, name: str | None = None) -> dict[str, Any]:
    if product_id is None and not name:
        return {"error": "Provide product_id or name."}

    with connect() as conn:
        if product_id is not None:
            row = conn.execute(
                f"{_PRODUCT_SELECT} WHERE p.product_id = ?",
                (product_id,),
            ).fetchone()
        else:
            row = conn.execute(
                f"{_PRODUCT_SELECT} WHERE LOWER(p.name) LIKE ? ORDER BY LENGTH(p.name) ASC LIMIT 1",
                (f"%{(name or '').lower()}%",),
            ).fetchone()

    if not row:
        return {"found": False, "message": "Product not found in catalog."}
    return {"found": True, "product": _product_row(row)}


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


def get_order_status(
    order_id: int,
    customer_name: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    if not customer_name and not phone:
        return {
            "authorized": False,
            "message": "Ask the customer for the order number and full name or phone before revealing order details.",
        }

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
            return {"found": False, "message": "Order not found."}

        name_ok = bool(customer_name) and (
            _norm(customer_name) in _norm(row["customer_name"])
            or _norm(row["customer_name"]) in _norm(customer_name)
        )
        phone_ok = bool(phone) and _digits(phone) and _digits(phone) == _digits(row["customer_phone"])

        if not (name_ok or phone_ok):
            return {
                "authorized": False,
                "found": True,
                "message": "Identification did not match this order. Do not reveal order details.",
            }

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
            "order_date": row["order_date"],
            "total_brl": _money(row["total_brl"]),
            "payment_method": row["payment_method"],
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
        "phone": "(67) 3341-4444",
        "whatsapp_support": "(67) 3321-4500",
        "email": "contato@emporiodamusica.com.br",
        "hours": {
            "monday_to_friday": "09:00 às 18:00",
            "saturday": "09:00 às 13:00",
            "sunday_and_holidays": "Fechado",
        },
        "scope": "Musical instruments only. No accessories.",
        "tone": "Informal but professional, like a music-savvy friend.",
    }


TOOL_HANDLERS = {
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
        return {"error": f"Unknown tool: {name}"}
    try:
        return handler(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - surface tool failures to the model
        return {"error": f"Tool {name} failed: {exc}"}
