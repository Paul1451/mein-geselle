#!/usr/bin/env python3
"""
Customer DB Tool — Mein Geselle (Hermes plug-in tool)

Single Hermes tool entry `customer_db` exposing CRUD over a tiny SQLite
database that backs the "Mein Geselle" Handwerker assistant.

Actions (selected via the `action` argument):
    - get_customer(query)              : fuzzy match by name / phone / email
    - upsert_customer(...)             : insert or update a customer row
    - list_recent_appointments(...)    : last N appointments for a customer
    - log_angebot(...)                 : append a new Angebot (quote) record

Storage:
    SQLite file at ~/.hermes/data/handwerk.db (created on first use).

Registration follows the same pattern as tools/todo_tool.py:
    registry.register(name=..., toolset=..., schema=..., handler=..., ...)

All comments are in English (submission repo). All user-facing data is
allowed to contain umlauts (ä ö ü ß) — connections set text_factory=str so
no ASCII mangling occurs.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.registry import registry  # type: ignore


# ---------------------------------------------------------------------------
# Paths & schema
# ---------------------------------------------------------------------------

DB_PATH = Path(os.path.expanduser("~/.hermes/data/handwerk.db"))

# DDL is idempotent — safe to run on every connect().
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone       TEXT,
    email       TEXT,
    address     TEXT,
    notes_md    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    starts_at    TEXT NOT NULL,
    ends_at      TEXT,
    title        TEXT NOT NULL,
    notes_md     TEXT,
    status       TEXT NOT NULL DEFAULT 'planned',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS angebote (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    scope_md     TEXT,
    total_eur    REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'draft',
    pdf_path     TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_appointments_customer ON appointments(customer_id);
CREATE INDEX IF NOT EXISTS idx_angebote_customer    ON angebote(customer_id);
CREATE INDEX IF NOT EXISTS idx_customers_name_lower ON customers(LOWER(name));
"""


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    """UTC timestamp in ISO-8601 with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a connection to the customer DB, creating tables if needed.

    Uses ``Row`` factory so callers can index columns by name. ``text_factory``
    stays as the stdlib default (``str``) which already preserves UTF-8 —
    important for German umlauts in customer data.
    """
    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Fuzzy match (stdlib only — no external deps)
# ---------------------------------------------------------------------------

_NORMALIZE_MAP = str.maketrans({
    # Fold German umlauts to ASCII for matching only (display values stay
    # untouched in the DB). This lets a user type "Mueller" and still hit
    # "Müller", or vice versa.
    "ä": "a", "ö": "o", "ü": "u", "ß": "s",
    "Ä": "a", "Ö": "o", "Ü": "u",
})


def _normalize(s: str) -> str:
    """Lowercase + strip + fold umlauts. Pure best-effort matching key."""
    if not s:
        return ""
    return s.translate(_NORMALIZE_MAP).lower().strip()


def _normalize_phone(s: str) -> str:
    """Strip everything except digits so '+49 30 1234' matches '030/1234'."""
    return re.sub(r"\D+", "", s or "")


def _fuzzy_score(needle_norm: str, hay_norm: str) -> float:
    """Cheap substring/token-overlap score in [0, 1]. No external deps."""
    if not needle_norm or not hay_norm:
        return 0.0
    if needle_norm == hay_norm:
        return 1.0
    if needle_norm in hay_norm:
        # Length-weighted substring hit so "mueller" beats "m" on the same row.
        return 0.6 + 0.4 * (len(needle_norm) / max(len(hay_norm), 1))
    # Token overlap fallback (covers "frau mueller" vs "mueller, frau").
    tokens_n = set(needle_norm.split())
    tokens_h = set(hay_norm.split())
    if not tokens_n or not tokens_h:
        return 0.0
    overlap = tokens_n & tokens_h
    return len(overlap) / max(len(tokens_n), 1) * 0.5


# ---------------------------------------------------------------------------
# Action handlers — each takes a connection and returns a JSON-able dict
# ---------------------------------------------------------------------------

def get_customer(conn: sqlite3.Connection, query: str, limit: int = 5) -> Dict[str, Any]:
    """Return best-matching customers by name / phone / email.

    Strategy: fetch all rows (the DB is tiny — at most a few hundred
    customers for a single Handwerker), score them, return top ``limit``.
    """
    q_raw = (query or "").strip()
    if not q_raw:
        return {"error": "query is required"}

    q_norm = _normalize(q_raw)
    q_phone = _normalize_phone(q_raw)

    rows = conn.execute(
        "SELECT id, name, phone, email, address, notes_md, "
        "created_at, updated_at FROM customers"
    ).fetchall()

    scored: List[tuple[float, sqlite3.Row]] = []
    for row in rows:
        score = max(
            _fuzzy_score(q_norm, _normalize(row["name"] or "")),
            _fuzzy_score(q_norm, _normalize(row["email"] or "")),
        )
        # Phone match: exact-suffix on digits-only form, weighted highest.
        if q_phone and row["phone"]:
            phone_digits = _normalize_phone(row["phone"])
            if phone_digits.endswith(q_phone) or q_phone.endswith(phone_digits):
                score = max(score, 0.95)
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda t: t[0], reverse=True)
    matches = [
        {**dict(row), "match_score": round(score, 3)}
        for score, row in scored[:limit]
    ]
    return {"query": q_raw, "matches": matches, "count": len(matches)}


def upsert_customer(
    conn: sqlite3.Connection,
    name: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    address: Optional[str] = None,
    notes_md: Optional[str] = None,
    customer_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Insert a new customer or update an existing one by id.

    If ``customer_id`` is given, update that row (any provided field replaces
    the old value; omitted fields stay as-is). Otherwise insert a new row.
    """
    name = (name or "").strip()
    if not name and customer_id is None:
        return {"error": "name is required for new customers"}

    now = _now_iso()

    if customer_id is not None:
        cur = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,))
        existing = cur.fetchone()
        if existing is None:
            return {"error": f"customer_id {customer_id} not found"}
        merged = {
            "name": name or existing["name"],
            "phone": phone if phone is not None else existing["phone"],
            "email": email if email is not None else existing["email"],
            "address": address if address is not None else existing["address"],
            "notes_md": notes_md if notes_md is not None else existing["notes_md"],
        }
        conn.execute(
            "UPDATE customers SET name=?, phone=?, email=?, address=?, "
            "notes_md=?, updated_at=? WHERE id=?",
            (
                merged["name"], merged["phone"], merged["email"],
                merged["address"], merged["notes_md"], now, customer_id,
            ),
        )
        conn.commit()
        return {"action": "updated", "customer_id": customer_id, **merged}

    cur = conn.execute(
        "INSERT INTO customers (name, phone, email, address, notes_md, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, phone, email, address, notes_md, now, now),
    )
    conn.commit()
    return {
        "action": "inserted",
        "customer_id": cur.lastrowid,
        "name": name,
        "phone": phone,
        "email": email,
        "address": address,
        "notes_md": notes_md,
    }


def list_recent_appointments(
    conn: sqlite3.Connection, customer_id: int, limit: int = 5
) -> Dict[str, Any]:
    """Return the ``limit`` most recent appointments for a customer."""
    if customer_id is None:
        return {"error": "customer_id is required"}
    rows = conn.execute(
        "SELECT id, customer_id, starts_at, ends_at, title, notes_md, "
        "status, created_at FROM appointments "
        "WHERE customer_id = ? ORDER BY starts_at DESC LIMIT ?",
        (customer_id, max(1, int(limit))),
    ).fetchall()
    return {
        "customer_id": customer_id,
        "appointments": [dict(r) for r in rows],
        "count": len(rows),
    }


def log_angebot(
    conn: sqlite3.Connection,
    customer_id: int,
    title: str,
    scope_md: Optional[str] = None,
    total_eur: float = 0.0,
    status: str = "draft",
    pdf_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a new Angebot (quote) record for a customer."""
    if customer_id is None:
        return {"error": "customer_id is required"}
    title = (title or "").strip()
    if not title:
        return {"error": "title is required"}

    # Validate the FK so we return a clean error instead of an integrity
    # exception that the registry would have to sanitize.
    exists = conn.execute(
        "SELECT 1 FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    if exists is None:
        return {"error": f"customer_id {customer_id} not found"}

    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO angebote (customer_id, title, scope_md, total_eur, "
        "status, pdf_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (customer_id, title, scope_md, float(total_eur), status, pdf_path, now),
    )
    conn.commit()
    return {
        "action": "inserted",
        "angebot_id": cur.lastrowid,
        "customer_id": customer_id,
        "title": title,
        "total_eur": float(total_eur),
        "status": status,
        "pdf_path": pdf_path,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# Dispatcher (single Hermes tool entry, multiple actions)
# ---------------------------------------------------------------------------

_ACTIONS = {
    "get_customer", "upsert_customer",
    "list_recent_appointments", "log_angebot",
}


def customer_db_tool(args: Dict[str, Any], **_kwargs: Any) -> str:
    """Single-entry handler that dispatches on ``args['action']``.

    Returns a JSON string (Hermes tools always serialize to JSON).
    """
    action = (args or {}).get("action")
    if action not in _ACTIONS:
        return _err(
            f"unknown action {action!r}; expected one of: {sorted(_ACTIONS)}"
        )

    conn = connect()
    try:
        if action == "get_customer":
            result = get_customer(
                conn,
                query=args.get("query", ""),
                limit=int(args.get("limit", 5)),
            )
        elif action == "upsert_customer":
            result = upsert_customer(
                conn,
                name=args.get("name", ""),
                phone=args.get("phone"),
                email=args.get("email"),
                address=args.get("address"),
                notes_md=args.get("notes_md"),
                customer_id=args.get("customer_id"),
            )
        elif action == "list_recent_appointments":
            result = list_recent_appointments(
                conn,
                customer_id=args.get("customer_id"),
                limit=int(args.get("limit", 5)),
            )
        elif action == "log_angebot":
            result = log_angebot(
                conn,
                customer_id=args.get("customer_id"),
                title=args.get("title", ""),
                scope_md=args.get("scope_md"),
                total_eur=float(args.get("total_eur", 0.0)),
                status=args.get("status", "draft"),
                pdf_path=args.get("pdf_path"),
            )
        else:  # pragma: no cover — guarded by the membership check above
            result = {"error": f"unhandled action {action}"}
    finally:
        conn.close()

    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def check_customer_db_requirements() -> bool:
    """Toolset availability check. Always True — sqlite3 is in stdlib and
    we create the DB on demand."""
    return True


# ---------------------------------------------------------------------------
# OpenAI function-calling schema
# ---------------------------------------------------------------------------

CUSTOMER_DB_SCHEMA = {
    "name": "customer_db",
    "description": (
        "Mein Geselle customer / appointment / Angebot CRUD over SQLite "
        "(~/.hermes/data/handwerk.db).\n"
        "Select an action via the 'action' field. Supported actions:\n"
        " - get_customer: fuzzy match by name / phone / email "
        "(returns top matches with score)\n"
        " - upsert_customer: insert a new customer or update one by "
        "customer_id (omitted fields preserved on update)\n"
        " - list_recent_appointments: most recent N appointments for a "
        "customer_id (newest first)\n"
        " - log_angebot: append an Angebot (quote) row for a customer\n\n"
        "Always returns JSON. German umlauts (ä ö ü ß) are preserved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(_ACTIONS),
                "description": "Which CRUD operation to perform.",
            },
            # get_customer
            "query": {
                "type": "string",
                "description": "Fuzzy search string (get_customer only).",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to return (default 5).",
                "default": 5,
            },
            # upsert_customer
            "customer_id": {
                "type": "integer",
                "description": (
                    "Customer primary key. Required for "
                    "list_recent_appointments and log_angebot; optional for "
                    "upsert_customer (omit to insert, supply to update)."
                ),
            },
            "name": {
                "type": "string",
                "description": "Customer display name (e.g. 'Frau Müller').",
            },
            "phone": {"type": "string"},
            "email": {"type": "string"},
            "address": {"type": "string"},
            "notes_md": {
                "type": "string",
                "description": "Free-form markdown notes about the customer.",
            },
            # log_angebot
            "title": {
                "type": "string",
                "description": "Angebot title (log_angebot only).",
            },
            "scope_md": {
                "type": "string",
                "description": "Markdown scope/description of the Angebot.",
            },
            "total_eur": {
                "type": "number",
                "description": "Total price in EUR.",
                "default": 0,
            },
            "status": {
                "type": "string",
                "description": "Angebot status (e.g. draft/sent/accepted).",
                "default": "draft",
            },
            "pdf_path": {
                "type": "string",
                "description": "Optional filesystem path to the rendered PDF.",
            },
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Hermes registry hook
# ---------------------------------------------------------------------------
# Importing the registry at module load mirrors how tools/todo_tool.py does
# it. Top-level call so the hermes-agent registry AST scanner discovers this
# module (it only picks up bare `registry.register(...)` calls — not ones
# wrapped in try/except).

registry.register(
    name="customer_db",
    toolset="mein_geselle",
    schema=CUSTOMER_DB_SCHEMA,
    handler=customer_db_tool,
    check_fn=check_customer_db_requirements,
    emoji="🧰",
    description=(
        "CRUD over the Mein Geselle customer SQLite DB "
        "(customers, appointments, Angebote)."
    ),
)


__all__ = [
    "DB_PATH",
    "connect",
    "customer_db_tool",
    "get_customer",
    "upsert_customer",
    "list_recent_appointments",
    "log_angebot",
    "CUSTOMER_DB_SCHEMA",
]
