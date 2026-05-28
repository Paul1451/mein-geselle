"""Tests for tool_angebot_draft — German Angebot with 19% VAT + PDF."""
from __future__ import annotations

import tool_angebot_draft as ang
import tool_customer_db as cdb


def _seed_customer(tmp_db) -> int:
    conn = cdb.connect(tmp_db)
    cid = cdb.upsert_customer(conn, name="Test Kunde",
                              address="Berlin", phone="030", email="a@b")["customer_id"]
    conn.close()
    return cid


def test_draft_returns_expected_keys(tmp_db, monkeypatch):
    monkeypatch.setattr(ang, "_render_pdf",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stub")))
    cid = _seed_customer(tmp_db)
    out = ang.draft(customer_id=cid, title="Test",
                    line_items=[{"description": "X", "qty": 1, "unit": "Std",
                                 "unit_price_eur": 100.0}])
    for key in ("total_net_eur", "total_gross_eur", "draft_md", "pdf_path"):
        assert key in out


def test_vat_calc_1000_net_to_1190_gross(tmp_db, monkeypatch):
    monkeypatch.setattr(ang, "_render_pdf",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stub")))
    cid = _seed_customer(tmp_db)
    out = ang.draft(customer_id=cid, title="VAT",
                    line_items=[{"description": "Y", "qty": 1, "unit": "pcs",
                                 "unit_price_eur": 1000.0}])
    assert out["total_net_eur"] == 1000.0
    assert out["vat_eur"] == 190.0
    assert out["total_gross_eur"] == 1190.0


def test_line_items_summed_correctly(tmp_db, monkeypatch):
    monkeypatch.setattr(ang, "_render_pdf",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("stub")))
    cid = _seed_customer(tmp_db)
    out = ang.draft(customer_id=cid, title="Sum",
                    line_items=[
                        {"description": "A", "qty": 2, "unit": "h",
                         "unit_price_eur": 50.0},
                        {"description": "B", "qty": 3, "unit": "h",
                         "unit_price_eur": 100.0},
                    ])
    assert out["total_net_eur"] == 400.0  # 100 + 300


def test_draft_missing_customer_returns_error(tmp_db):
    out = ang.draft(customer_id=99999, title="X",
                    line_items=[{"description": "X", "qty": 1, "unit": "h",
                                 "unit_price_eur": 1.0}])
    assert "error" in out
    assert "99999" in out["error"]


def test_pdf_failure_keeps_markdown(tmp_db, monkeypatch):
    """Simulate WeasyPrint failure — draft_md must still be returned, pdf_error set."""
    def boom(*args, **kwargs):
        raise RuntimeError("WeasyPrint failed to load Pango")
    monkeypatch.setattr(ang, "_render_pdf", boom)
    cid = _seed_customer(tmp_db)
    out = ang.draft(customer_id=cid, title="PDF fail",
                    line_items=[{"description": "X", "qty": 1, "unit": "h",
                                 "unit_price_eur": 100.0}])
    assert out["pdf_path"] is None
    assert "pdf_error" in out
    assert "Pango" in out["pdf_error"]
    assert out["draft_md"].startswith("# Angebot AN-")
