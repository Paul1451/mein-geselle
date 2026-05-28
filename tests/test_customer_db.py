"""Tests for tool_customer_db — fuzzy match + CRUD over SQLite."""
from __future__ import annotations

import json
import sqlite3

import tool_customer_db as cdb


def _seed_mueller(db_path):
    conn = cdb.connect(db_path)
    cdb.upsert_customer(conn, name="Frau Müller", phone="+49 30 4416 2271",
                        email="a.mueller@example.de", address="Berlin")
    conn.close()


def test_get_customer_exact_name_match(tmp_db):
    _seed_mueller(tmp_db)
    conn = cdb.connect(tmp_db)
    out = cdb.get_customer(conn, "Frau Müller")
    conn.close()
    assert out["count"] == 1
    assert out["matches"][0]["name"] == "Frau Müller"


def test_get_customer_fuzzy_umlaut_match(tmp_db):
    _seed_mueller(tmp_db)
    conn = cdb.connect(tmp_db)
    out = cdb.get_customer(conn, "Mueller")
    conn.close()
    assert out["count"] >= 1
    assert out["matches"][0]["name"] == "Frau Müller"


def test_get_customer_by_phone_suffix(tmp_db):
    _seed_mueller(tmp_db)
    conn = cdb.connect(tmp_db)
    out = cdb.get_customer(conn, "4416 2271")
    conn.close()
    assert out["count"] == 1
    assert out["matches"][0]["match_score"] >= 0.9


def test_get_customer_missing_returns_empty(tmp_db):
    conn = cdb.connect(tmp_db)
    out = cdb.get_customer(conn, "DoesNotExist")
    conn.close()
    assert out["count"] == 0
    assert out["matches"] == []


def test_upsert_customer_creates_then_updates_same_id(tmp_db):
    conn = cdb.connect(tmp_db)
    first = cdb.upsert_customer(conn, name="Herr Becker", phone="030 1")
    cid = first["customer_id"]
    second = cdb.upsert_customer(conn, name="Herr Becker", phone="030 2",
                                 customer_id=cid)
    conn.close()
    assert first["action"] == "inserted"
    assert second["action"] == "updated"
    assert second["customer_id"] == cid


def test_list_recent_appointments_returns_n_newest(tmp_db):
    conn = cdb.connect(tmp_db)
    cid = cdb.upsert_customer(conn, name="X")["customer_id"]
    for hour in (8, 10, 14):
        conn.execute(
            "INSERT INTO appointments (customer_id, starts_at, title, "
            "status, created_at) VALUES (?, ?, ?, 'planned', ?)",
            (cid, f"2026-06-01T{hour:02d}:00:00", f"t{hour}", cdb._now_iso()),
        )
    conn.commit()
    out = cdb.list_recent_appointments(conn, customer_id=cid, limit=2)
    conn.close()
    assert out["count"] == 2
    assert out["appointments"][0]["title"] == "t14"


def test_list_recent_appointments_empty_customer(tmp_db):
    conn = cdb.connect(tmp_db)
    cid = cdb.upsert_customer(conn, name="Empty")["customer_id"]
    out = cdb.list_recent_appointments(conn, customer_id=cid)
    conn.close()
    assert out["count"] == 0
    assert out["appointments"] == []


def test_log_angebot_inserts_returns_id(tmp_db):
    conn = cdb.connect(tmp_db)
    cid = cdb.upsert_customer(conn, name="A")["customer_id"]
    out = cdb.log_angebot(conn, customer_id=cid, title="Bad streichen",
                          total_eur=500.0)
    conn.close()
    assert out["action"] == "inserted"
    assert isinstance(out["angebot_id"], int)
    assert out["total_eur"] == 500.0


def test_sql_injection_in_name_is_safe(tmp_db):
    conn = cdb.connect(tmp_db)
    cdb.upsert_customer(conn, name="Legit")
    payload = "X'; DROP TABLE customers; --"
    cdb.upsert_customer(conn, name=payload)
    out = cdb.get_customer(conn, "Legit")
    # Customers table must still exist and Legit must still be retrievable.
    rows = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()
    conn.close()
    assert out["count"] >= 1
    assert rows["c"] == 2


def test_concurrent_writes_do_not_corrupt(tmp_db):
    c1 = sqlite3.connect(tmp_db)
    c2 = sqlite3.connect(tmp_db)
    c1.row_factory = sqlite3.Row
    c2.row_factory = sqlite3.Row
    c1.execute("INSERT INTO customers (name, created_at, updated_at) "
               "VALUES ('A', '2026', '2026')")
    c1.commit()
    c2.execute("INSERT INTO customers (name, created_at, updated_at) "
               "VALUES ('B', '2026', '2026')")
    c2.commit()
    rows = c1.execute("SELECT name FROM customers ORDER BY name").fetchall()
    c1.close(); c2.close()
    assert [r["name"] for r in rows] == ["A", "B"]


def test_dispatcher_get_customer_returns_json(tmp_db):
    _seed_mueller(tmp_db)
    raw = cdb.customer_db_tool({"action": "get_customer", "query": "Müller"})
    payload = json.loads(raw)
    assert payload["count"] == 1
