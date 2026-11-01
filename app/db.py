"""Load challenge CSVs into SQLite.

Why SQLite: product price, stock and order status must be exact.
A relational store lets tools filter with SQL instead of hoping the
LLM remembers numbers from a long prompt.
"""

from __future__ import annotations

import csv
import sqlite3
import unicodedata
from pathlib import Path

from .config import DATA_DIR, DB_PATH

CSV_FILES = {
    "categories": "desafio_tecnico_ai_eng - categories.csv",
    "customers": "desafio_tecnico_ai_eng - customers.csv",
    "products": "desafio_tecnico_ai_eng - products.csv",
    "promotions": "desafio_tecnico_ai_eng - promotions.csv",
    "orders": "desafio_tecnico_ai_eng - orders.csv",
    "order_items": "desafio_tecnico_ai_eng - order_items.csv",
}


def _find_policy_pdf() -> Path:
    matches = list(DATA_DIR.glob("*.pdf"))
    if not matches:
        raise FileNotFoundError(f"No policy PDF found in {DATA_DIR}")
    return matches[0]


def norm_text(value: str | None) -> str:
    """Lowercase and strip accents, so 'Violões' and 'violoes' compare equal."""
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # SQLite's LOWER() only handles ASCII; this keeps accent-insensitive search in SQL.
    conn.create_function("norm_text", 1, norm_text, deterministic=True)
    return conn


def _read_csv(name: str) -> list[dict[str, str]]:
    path = DATA_DIR / CSV_FILES[name]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def init_db(force: bool = False) -> Path:
    if DB_PATH.exists() and not force:
        return DB_PATH

    if DB_PATH.exists():
        DB_PATH.unlink()

    categories = _read_csv("categories")
    customers = _read_csv("customers")
    products = _read_csv("products")
    promotions = _read_csv("promotions")
    orders = _read_csv("orders")
    order_items = _read_csv("order_items")

    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE categories (
                category_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT
            );
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                city TEXT
            );
            CREATE TABLE products (
                product_id INTEGER PRIMARY KEY,
                price_brl REAL NOT NULL,
                name TEXT NOT NULL,
                category_id INTEGER NOT NULL,
                description TEXT,
                stock_quantity INTEGER NOT NULL,
                status TEXT NOT NULL,
                specs TEXT,
                created_at TEXT,
                FOREIGN KEY (category_id) REFERENCES categories(category_id)
            );
            CREATE TABLE promotions (
                promotion_id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL,
                discount_percent REAL NOT NULL,
                description TEXT,
                is_active INTEGER NOT NULL,
                FOREIGN KEY (product_id) REFERENCES products(product_id)
            );
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                order_date TEXT,
                status TEXT,
                total_brl REAL,
                payment_method TEXT,
                tracking_code TEXT,
                estimated_delivery TEXT,
                notes TEXT,
                FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            );
            CREATE TABLE order_items (
                order_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(order_id),
                FOREIGN KEY (product_id) REFERENCES products(product_id)
            );
            """
        )

        conn.executemany(
            "INSERT INTO categories VALUES (:category_id, :name, :description)",
            categories,
        )
        conn.executemany(
            "INSERT INTO customers VALUES (:customer_id, :name, :phone, :email, :city)",
            customers,
        )
        conn.executemany(
            """
            INSERT INTO products
            VALUES (
                :product_id, :price_brl, :name, :category_id, :description,
                :stock_quantity, :status, :specs, :created_at
            )
            """,
            products,
        )
        conn.executemany(
            """
            INSERT INTO promotions
            VALUES (
                :promotion_id, :product_id, :discount_percent,
                :description, :is_active
            )
            """,
            promotions,
        )
        conn.executemany(
            """
            INSERT INTO orders
            VALUES (
                :order_id, :customer_id, :order_date, :status, :total_brl,
                :payment_method, :tracking_code, :estimated_delivery, :notes
            )
            """,
            orders,
        )
        conn.executemany(
            "INSERT INTO order_items VALUES (:order_id, :quantity, :product_id)",
            order_items,
        )
        conn.commit()

    return DB_PATH


def policy_pdf_path() -> Path:
    return _find_policy_pdf()
