#!/usr/bin/env python3
"""
Angebot Draft Tool — Mein Geselle (Hermes plug-in tool)

Single Hermes tool entry ``angebot_draft`` that drafts a German Handwerker
Angebot (price quote), persists it into the customer DB, and renders a
clean PDF via WeasyPrint + Jinja2.

When Hermes should call this tool:
    - The planner classified an inbound message as `anfrage` (via
      ``lead_classify``) and has gathered enough scope to put concrete
      line items on a quote.
    - The user explicitly asks "Erstell mir ein Angebot für ..." in chat.
    - A `follow_up` message references an existing Angebot and the user
      asks to re-render the PDF (use action `regenerate_pdf`).
    - The agent wants to show the customer their last few Angebote
      (action `list_recent`).

Actions (selected via the ``action`` argument):
    - draft(customer_id, title, line_items, notes?, style_skill_path?)
        → {angebot_id, total_net_eur, total_gross_eur, pdf_path, draft_md}
    - regenerate_pdf(angebot_id)
        → re-renders the PDF from the DB-stored ``scope_md`` payload
    - list_recent(customer_id?, limit=10)
        → list of past Angebote (newest first)

Storage:
    SQLite file at ``~/.hermes/data/handwerk.db`` (owned by the
    ``customer_db`` tool — we read/write via raw sqlite3 here to avoid
    triggering the registry side-effects of importing that module).

Rendered PDFs land in ``~/.hermes/data/angebote/AN-YYYYMMDD-####.pdf``.

Dependencies:
    - WeasyPrint (HTML → PDF)
    - Jinja2 (HTML template)

WeasyPrint needs Pango / GLib at the OS level. On macOS install with
``brew install pango``. If the venv's Python is x86_64 but Homebrew is
arm64 (or vice versa), WeasyPrint will fail to load ``libgobject``; in
that case rebuild the venv with a matching-arch Python.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

DB_PATH = Path(os.path.expanduser("~/.hermes/data/handwerk.db"))
PDF_DIR = Path(os.path.expanduser("~/.hermes/data/angebote"))

# Default settings; may be overridden by an angebot_style SKILL.md.
DEFAULT_STYLE: Dict[str, Any] = {
    "greeting": (
        "vielen Dank fuer Ihre Anfrage. Gerne unterbreiten wir Ihnen "
        "folgendes Angebot."
    ),
    "vat_mode": "exclusive",  # German default: net + 19 % MwSt ausgewiesen
    "vat_rate": 0.19,  # 19 % Mehrwertsteuer (Stand 2026)
    "discount_threshold_eur": 0.0,
    "discount_pct": 0.0,
    "signature": (
        "Mit freundlichen Gruessen\n"
        "Maler Schulz\n"
        "Malerbetrieb Schulz, Berlin"
    ),
    "payment_terms": "14 Tage netto",
}


# ---------------------------------------------------------------------------
# Connection helper (mirrors tool_customer_db.connect; copied to avoid
# importing that module — importing it would trigger its registry hook
# even when this tool is loaded standalone.)
# ---------------------------------------------------------------------------

def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a read/write connection to the customer DB.

    The schema is owned by ``tool_customer_db``. We don't run DDL here so
    that we never silently shadow an evolving schema in that module.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now_iso() -> str:
    """UTC timestamp in ISO-8601 with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# German number / currency formatting
# ---------------------------------------------------------------------------

def fmt_eur(amount: float) -> str:
    """Format ``amount`` as ``1.234,56 €`` (German locale style).

    We don't rely on ``locale.setlocale`` because it's process-global and
    flaky in venvs; building the string manually is cheap and portable.
    """
    sign = "-" if amount < 0 else ""
    n = abs(round(float(amount), 2))
    int_part = int(n)
    frac_part = int(round((n - int_part) * 100))
    # Thousands separator: dot.
    int_str = f"{int_part:,}".replace(",", ".")
    return f"{sign}{int_str},{frac_part:02d} €"


def fmt_qty(qty: float) -> str:
    """Format a quantity (e.g. 4 → '4', 8.5 → '8,5')."""
    n = float(qty)
    if n.is_integer():
        return str(int(n))
    return f"{n:.2f}".rstrip("0").rstrip(".").replace(".", ",")


# ---------------------------------------------------------------------------
# Angebot number generation
# ---------------------------------------------------------------------------

def _next_angebot_number(conn: sqlite3.Connection) -> str:
    """Build the next ``AN-YYYYMMDD-####`` number.

    Strategy: count rows in the ``angebote`` table and use that count + 1
    as the sequential counter. Falls back to ``0001`` when the table is
    empty. The date component uses local-time today.
    """
    today = datetime.now().strftime("%Y%m%d")
    row = conn.execute("SELECT COUNT(*) AS c FROM angebote").fetchone()
    seq = (row["c"] if row else 0) + 1
    return f"AN-{today}-{seq:04d}"


# ---------------------------------------------------------------------------
# Style skill parsing
# ---------------------------------------------------------------------------

def _parse_style_skill(path: Optional[str]) -> Dict[str, Any]:
    """Best-effort parse of an ``angebot_style`` SKILL.md.

    We look for ``key:`` lines anywhere in the markdown — the skill is
    free-form prose with embedded slots, not a strict schema. Unknown
    keys are ignored. Missing files fall back to DEFAULT_STYLE silently.
    """
    style = dict(DEFAULT_STYLE)
    if not path:
        return style
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return style

    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return style

    # Each line of form ``key: value`` — we only act on a small allowlist.
    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip().strip("\"'`")
        if not value:
            continue
        if key == "greeting":
            style["greeting"] = value
        elif key == "vat_mode" and value in ("inclusive", "exclusive"):
            style["vat_mode"] = value
        elif key == "discount_threshold_eur":
            try:
                style["discount_threshold_eur"] = float(value.replace(",", "."))
            except ValueError:
                pass
        elif key == "discount_pct":
            try:
                style["discount_pct"] = float(value.replace(",", ".").rstrip("%"))
            except ValueError:
                pass
        elif key == "signature":
            style["signature"] = value
        elif key == "payment_terms":
            style["payment_terms"] = value
    return style


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------

@dataclass
class CalculatedLineItem:
    """A single line item with computed total."""

    pos: int
    description: str
    qty: float
    unit: str
    unit_price_eur: float
    total_eur: float


@dataclass
class AngebotTotals:
    """Computed totals for the whole Angebot."""

    net_subtotal_eur: float
    discount_eur: float
    net_after_discount_eur: float
    vat_eur: float
    gross_eur: float
    discount_pct_applied: float


def _calculate(
    line_items: List[Dict[str, Any]], style: Dict[str, Any]
) -> tuple[List[CalculatedLineItem], AngebotTotals]:
    """Compute per-line totals + the Angebot summary.

    Discount rule: if the net subtotal exceeds ``discount_threshold_eur``,
    apply ``discount_pct`` to the subtotal. Otherwise no discount.

    VAT rule: 19 % Mehrwertsteuer applied to the discounted net subtotal.
    ``vat_mode == "inclusive"`` means the line item prices already include
    VAT — we then back-derive the net for display.
    """
    calc_lines: List[CalculatedLineItem] = []
    for idx, raw in enumerate(line_items, start=1):
        qty = float(raw.get("qty", 0))
        unit_price = float(raw.get("unit_price_eur", 0))
        calc_lines.append(
            CalculatedLineItem(
                pos=idx,
                description=str(raw.get("description", "")).strip(),
                qty=qty,
                unit=str(raw.get("unit", "")).strip(),
                unit_price_eur=unit_price,
                total_eur=round(qty * unit_price, 2),
            )
        )

    gross_sum = round(sum(li.total_eur for li in calc_lines), 2)
    vat_rate = float(style.get("vat_rate", 0.19))

    if style.get("vat_mode") == "inclusive":
        # Prices are gross — derive net for the summary.
        net_subtotal = round(gross_sum / (1 + vat_rate), 2)
    else:
        # Prices are net (German default for B2B Angebote).
        net_subtotal = gross_sum

    threshold = float(style.get("discount_threshold_eur", 0.0) or 0.0)
    discount_pct = float(style.get("discount_pct", 0.0) or 0.0)
    if threshold > 0 and net_subtotal >= threshold and discount_pct > 0:
        discount_eur = round(net_subtotal * (discount_pct / 100.0), 2)
        pct_applied = discount_pct
    else:
        discount_eur = 0.0
        pct_applied = 0.0

    net_after = round(net_subtotal - discount_eur, 2)
    vat_eur = round(net_after * vat_rate, 2)
    gross_total = round(net_after + vat_eur, 2)

    totals = AngebotTotals(
        net_subtotal_eur=net_subtotal,
        discount_eur=discount_eur,
        net_after_discount_eur=net_after,
        vat_eur=vat_eur,
        gross_eur=gross_total,
        discount_pct_applied=pct_applied,
    )
    return calc_lines, totals


# ---------------------------------------------------------------------------
# Markdown draft (for chat review)
# ---------------------------------------------------------------------------

def _render_markdown(
    angebot_no: str,
    customer: sqlite3.Row,
    title: str,
    notes: str,
    lines: List[CalculatedLineItem],
    totals: AngebotTotals,
    style: Dict[str, Any],
) -> str:
    """Build a compact markdown summary the agent can show in chat.

    The PDF is the deliverable artefact; this Markdown is for the human
    in the loop to sanity-check totals before sending.
    """
    today = datetime.now().strftime("%d.%m.%Y")
    md: List[str] = [
        f"# Angebot {angebot_no}",
        f"**Datum:** {today}",
        f"**Kunde:** {customer['name']}",
    ]
    if customer["address"]:
        md.append(f"**Adresse:** {customer['address']}")
    md.append("")
    md.append(f"**Betreff:** {title}")
    md.append("")
    md.append(f"Sehr geehrte/r {customer['name']},")
    md.append("")
    md.append(style["greeting"])
    md.append("")
    md.append("| Pos | Beschreibung | Menge | Einheit | Einzelpreis | Gesamt |")
    md.append("|----:|:-------------|------:|:--------|------------:|-------:|")
    for li in lines:
        md.append(
            f"| {li.pos} | {li.description} | {fmt_qty(li.qty)} | "
            f"{li.unit} | {fmt_eur(li.unit_price_eur)} | "
            f"{fmt_eur(li.total_eur)} |"
        )
    md.append("")
    md.append(f"**Zwischensumme (netto):** {fmt_eur(totals.net_subtotal_eur)}")
    if totals.discount_eur > 0:
        md.append(
            f"**Rabatt ({fmt_qty(totals.discount_pct_applied)} %):** "
            f"-{fmt_eur(totals.discount_eur)}"
        )
        md.append(
            f"**Netto nach Rabatt:** {fmt_eur(totals.net_after_discount_eur)}"
        )
    md.append(f"**MwSt 19 %:** {fmt_eur(totals.vat_eur)}")
    md.append(f"**Gesamtbetrag (brutto):** {fmt_eur(totals.gross_eur)}")
    md.append("")
    md.append(f"**Zahlungsziel:** {style['payment_terms']}")
    if notes:
        md.append("")
        md.append(f"**Hinweise:** {notes}")
    md.append("")
    md.append(style["signature"])
    return "\n".join(md)


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

_PDF_TEMPLATE = """\
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Angebot {{ angebot_no }}</title>
<style>
  @page { size: A4; margin: 22mm 18mm 22mm 18mm; }
  body {
    font-family: "Helvetica", "Arial", sans-serif;
    font-size: 10.5pt;
    color: #1a1a1a;
    line-height: 1.45;
  }
  header { border-bottom: 2px solid #1a1a1a; padding-bottom: 8mm;
           margin-bottom: 8mm; display: flex;
           justify-content: space-between; align-items: flex-end; }
  header .brand { font-size: 18pt; font-weight: 700;
                  letter-spacing: -0.02em; }
  header .meta { text-align: right; font-size: 9pt; color: #555; }
  .addresses { display: flex; justify-content: space-between;
               margin-bottom: 8mm; }
  .addresses .block { width: 48%; }
  .addresses h3 { font-size: 9pt; text-transform: uppercase;
                  letter-spacing: 0.08em; color: #777; margin: 0 0 2mm 0; }
  .addresses p { margin: 0; white-space: pre-line; }
  h1.title { font-size: 14pt; margin: 6mm 0 4mm 0; }
  .greeting { margin-bottom: 4mm; }
  table.items { width: 100%; border-collapse: collapse;
                margin-top: 4mm; margin-bottom: 4mm; }
  table.items thead th {
    text-align: left; font-size: 9pt; text-transform: uppercase;
    letter-spacing: 0.05em; border-bottom: 1.5px solid #1a1a1a;
    padding: 2mm 1.5mm; color: #333;
  }
  table.items thead th.num { text-align: right; }
  table.items tbody td {
    padding: 2mm 1.5mm; border-bottom: 0.4px solid #ddd;
    vertical-align: top;
  }
  table.items tbody td.num { text-align: right;
                             font-variant-numeric: tabular-nums; }
  table.items tbody td.pos { color: #888; width: 8mm; }
  table.totals { width: 60%; margin-left: 40%; margin-top: 4mm;
                 border-collapse: collapse; }
  table.totals td { padding: 1.5mm 1.5mm;
                    font-variant-numeric: tabular-nums; }
  table.totals td.label { text-align: right; color: #444; }
  table.totals td.num { text-align: right; width: 30mm; }
  table.totals tr.grand td { font-weight: 700; font-size: 12pt;
                             border-top: 1.5px solid #1a1a1a;
                             padding-top: 3mm; }
  .notes { margin-top: 6mm; padding: 3mm 4mm; background: #f6f6f4;
           border-left: 3px solid #888; font-size: 10pt; }
  .terms { margin-top: 6mm; font-size: 9.5pt; color: #555; }
  footer { margin-top: 10mm; padding-top: 4mm;
           border-top: 0.5px solid #ccc; font-size: 8.5pt;
           color: #666; white-space: pre-line; }
  .signature { margin-top: 8mm; white-space: pre-line; }
</style>
</head>
<body>
  <header>
    <div class="brand">Malerbetrieb Schulz</div>
    <div class="meta">
      Angebot {{ angebot_no }}<br>
      Datum: {{ datum }}
    </div>
  </header>

  <section class="addresses">
    <div class="block">
      <h3>Kunde</h3>
      <p>{{ customer_block }}</p>
    </div>
    <div class="block" style="text-align:right;">
      <h3>Aussteller</h3>
      <p>Maler Schulz
Malerbetrieb Schulz
Berlin</p>
    </div>
  </section>

  <h1 class="title">{{ title }}</h1>
  <div class="greeting">
    <p>Sehr geehrte/r {{ customer_name }},</p>
    <p>{{ greeting }}</p>
  </div>

  <table class="items">
    <thead>
      <tr>
        <th class="num">Pos</th>
        <th>Beschreibung</th>
        <th class="num">Menge</th>
        <th>Einheit</th>
        <th class="num">Einzelpreis</th>
        <th class="num">Gesamt</th>
      </tr>
    </thead>
    <tbody>
      {% for li in lines %}
      <tr>
        <td class="pos num">{{ li.pos }}</td>
        <td>{{ li.description }}</td>
        <td class="num">{{ li.qty_fmt }}</td>
        <td>{{ li.unit }}</td>
        <td class="num">{{ li.unit_price_fmt }}</td>
        <td class="num">{{ li.total_fmt }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <table class="totals">
    <tr>
      <td class="label">Zwischensumme (netto)</td>
      <td class="num">{{ subtotal_fmt }}</td>
    </tr>
    {% if discount_eur_fmt %}
    <tr>
      <td class="label">Rabatt ({{ discount_pct_fmt }} %)</td>
      <td class="num">-{{ discount_eur_fmt }}</td>
    </tr>
    <tr>
      <td class="label">Netto nach Rabatt</td>
      <td class="num">{{ net_after_fmt }}</td>
    </tr>
    {% endif %}
    <tr>
      <td class="label">MwSt 19 %</td>
      <td class="num">{{ vat_fmt }}</td>
    </tr>
    <tr class="grand">
      <td class="label">Gesamtbetrag (brutto)</td>
      <td class="num">{{ gross_fmt }}</td>
    </tr>
  </table>

  {% if notes %}
  <div class="notes"><strong>Hinweise:</strong> {{ notes }}</div>
  {% endif %}

  <div class="terms">
    <strong>Zahlungsziel:</strong> {{ payment_terms }}<br>
    Angebot gueltig 30 Tage ab Ausstellungsdatum.
  </div>

  <div class="signature">{{ signature }}</div>

  <footer>Malerbetrieb Schulz - Berlin - Mein Geselle Hermes Agent</footer>
</body>
</html>
"""


def _render_pdf(
    pdf_path: Path,
    angebot_no: str,
    customer: sqlite3.Row,
    title: str,
    notes: str,
    lines: List[CalculatedLineItem],
    totals: AngebotTotals,
    style: Dict[str, Any],
) -> None:
    """Render the Angebot to ``pdf_path`` via WeasyPrint + Jinja2.

    Imported lazily so the rest of the module stays usable when
    WeasyPrint / Pango aren't installed yet (e.g. on a CI image without
    the system deps).
    """
    try:
        from jinja2 import Template  # type: ignore
        from weasyprint import HTML  # type: ignore
    except ImportError as e:  # pragma: no cover — env-dependent
        raise RuntimeError(
            "WeasyPrint or Jinja2 not installed. Run "
            "`uv pip install weasyprint jinja2` inside the hermes-agent "
            "venv. On macOS you also need `brew install pango`."
        ) from e
    except OSError as e:  # pragma: no cover — native-lib loading issue
        # WeasyPrint imports its native deps (Pango/GLib) at import time.
        # A missing or arch-mismatched system lib raises OSError from cffi.
        raise RuntimeError(
            f"WeasyPrint failed to load its native dependencies "
            f"(Pango/GLib): {e}. On macOS install `brew install pango` "
            "and make sure the venv's Python matches the Homebrew "
            "architecture (both arm64 or both x86_64)."
        ) from e

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the customer address block as a single text blob.
    parts = [customer["name"]]
    if customer["address"]:
        parts.append(customer["address"])
    if customer["phone"]:
        parts.append(f"Tel: {customer['phone']}")
    if customer["email"]:
        parts.append(customer["email"])
    customer_block = "\n".join(parts)

    template_ctx: Dict[str, Any] = {
        "angebot_no": angebot_no,
        "datum": datetime.now().strftime("%d.%m.%Y"),
        "customer_block": customer_block,
        "customer_name": customer["name"],
        "title": title,
        "greeting": style["greeting"],
        "lines": [
            {
                "pos": li.pos,
                "description": li.description,
                "qty_fmt": fmt_qty(li.qty),
                "unit": li.unit,
                "unit_price_fmt": fmt_eur(li.unit_price_eur),
                "total_fmt": fmt_eur(li.total_eur),
            }
            for li in lines
        ],
        "subtotal_fmt": fmt_eur(totals.net_subtotal_eur),
        "discount_eur_fmt": (
            fmt_eur(totals.discount_eur) if totals.discount_eur > 0 else ""
        ),
        "discount_pct_fmt": fmt_qty(totals.discount_pct_applied),
        "net_after_fmt": fmt_eur(totals.net_after_discount_eur),
        "vat_fmt": fmt_eur(totals.vat_eur),
        "gross_fmt": fmt_eur(totals.gross_eur),
        "notes": notes,
        "payment_terms": style["payment_terms"],
        "signature": style["signature"],
    }

    html = Template(_PDF_TEMPLATE).render(**template_ctx)
    HTML(string=html).write_pdf(str(pdf_path))


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def _serialize_scope(
    title: str,
    lines: List[CalculatedLineItem],
    notes: str,
    style: Dict[str, Any],
) -> str:
    """Serialise the Angebot draft into a JSON payload stored in
    ``angebote.scope_md`` so ``regenerate_pdf`` can rebuild the document
    later without re-receiving the line items."""
    payload = {
        "title": title,
        "notes": notes,
        "style": style,
        "line_items": [
            {
                "description": li.description,
                "qty": li.qty,
                "unit": li.unit,
                "unit_price_eur": li.unit_price_eur,
            }
            for li in lines
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _deserialize_scope(scope_md: str) -> Dict[str, Any]:
    """Best-effort load of the JSON blob written by ``_serialize_scope``.
    Falls back to ``{}`` if the row predates this tool (e.g. seeded
    Angebote where scope_md is human markdown)."""
    try:
        data = json.loads(scope_md)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        pass
    return {}


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------

def draft(
    customer_id: int,
    title: str,
    line_items: List[Dict[str, Any]],
    notes: str = "",
    style_skill_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Draft a new Angebot for ``customer_id``.

    Inserts into the ``angebote`` table with ``status='draft'`` and the
    rendered PDF path. Returns a dict containing the angebot id, totals,
    PDF path, and a markdown draft for review.
    """
    if not line_items:
        return {"error": "line_items must contain at least one entry"}
    if not title or not title.strip():
        return {"error": "title is required"}

    style = _parse_style_skill(style_skill_path)
    lines, totals = _calculate(line_items, style)

    conn = _connect()
    try:
        customer = conn.execute(
            "SELECT id, name, phone, email, address FROM customers "
            "WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if customer is None:
            return {"error": f"customer_id {customer_id} not found"}

        angebot_no = _next_angebot_number(conn)
        pdf_path = PDF_DIR / f"{angebot_no}.pdf"

        # Render markdown BEFORE the PDF — even if PDF rendering blows up
        # we want to keep the human-readable draft in the response.
        draft_md = _render_markdown(
            angebot_no, customer, title, notes, lines, totals, style
        )

        pdf_error: Optional[str] = None
        try:
            _render_pdf(
                pdf_path, angebot_no, customer, title.strip(),
                notes, lines, totals, style,
            )
        except RuntimeError as e:
            pdf_error = str(e)

        scope_payload = _serialize_scope(title.strip(), lines, notes, style)
        cur = conn.execute(
            "INSERT INTO angebote (customer_id, title, scope_md, total_eur, "
            "status, pdf_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                customer_id,
                title.strip(),
                scope_payload,
                totals.gross_eur,
                "draft",
                str(pdf_path) if pdf_error is None else None,
                _now_iso(),
            ),
        )
        conn.commit()

        result: Dict[str, Any] = {
            "angebot_id": cur.lastrowid,
            "angebot_no": angebot_no,
            "customer_id": customer_id,
            "total_net_eur": totals.net_after_discount_eur,
            "total_gross_eur": totals.gross_eur,
            "vat_eur": totals.vat_eur,
            "discount_eur": totals.discount_eur,
            "pdf_path": str(pdf_path) if pdf_error is None else None,
            "draft_md": draft_md,
        }
        if pdf_error is not None:
            result["pdf_error"] = pdf_error
        return result
    finally:
        conn.close()


def regenerate_pdf(angebot_id: int) -> Dict[str, Any]:
    """Re-render the PDF for an existing Angebot row.

    Uses the JSON payload stashed in ``scope_md`` by ``draft``. If the
    row predates this tool, we cannot reconstruct the line items and
    return a clear error.
    """
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT a.id, a.customer_id, a.title, a.scope_md, a.pdf_path, "
            "a.created_at, c.name, c.phone, c.email, c.address "
            "FROM angebote a JOIN customers c ON c.id = a.customer_id "
            "WHERE a.id = ?",
            (angebot_id,),
        ).fetchone()
        if row is None:
            return {"error": f"angebot_id {angebot_id} not found"}

        payload = _deserialize_scope(row["scope_md"] or "")
        if not payload.get("line_items"):
            return {
                "error": (
                    f"angebot_id {angebot_id} has no machine-readable "
                    "line_items in scope_md (likely a legacy / seeded row)."
                )
            }

        style = {**DEFAULT_STYLE, **(payload.get("style") or {})}
        lines, totals = _calculate(payload["line_items"], style)

        # Recover the original AN-number by reusing the existing pdf_path
        # filename when present; otherwise derive a fresh one.
        if row["pdf_path"]:
            angebot_no = Path(row["pdf_path"]).stem
        else:
            angebot_no = _next_angebot_number(conn)
        pdf_path = PDF_DIR / f"{angebot_no}.pdf"

        # Build a minimal sqlite3.Row-compatible mapping for the renderer.
        customer = {
            "name": row["name"], "phone": row["phone"],
            "email": row["email"], "address": row["address"],
        }
        _render_pdf(
            pdf_path, angebot_no, customer, payload.get("title", row["title"]),
            payload.get("notes", ""), lines, totals, style,
        )

        conn.execute(
            "UPDATE angebote SET pdf_path = ? WHERE id = ?",
            (str(pdf_path), angebot_id),
        )
        conn.commit()
        return {
            "angebot_id": angebot_id,
            "angebot_no": angebot_no,
            "pdf_path": str(pdf_path),
            "total_gross_eur": totals.gross_eur,
        }
    finally:
        conn.close()


def list_recent(
    customer_id: Optional[int] = None, limit: int = 10
) -> Dict[str, Any]:
    """Return the newest ``limit`` Angebote, optionally scoped to a customer."""
    limit = max(1, min(int(limit), 100))
    conn = _connect()
    try:
        if customer_id is not None:
            rows = conn.execute(
                "SELECT id, customer_id, title, total_eur, status, "
                "pdf_path, created_at FROM angebote "
                "WHERE customer_id = ? ORDER BY id DESC LIMIT ?",
                (int(customer_id), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, customer_id, title, total_eur, status, "
                "pdf_path, created_at FROM angebote "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "count": len(rows),
            "angebote": [dict(r) for r in rows],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

_ACTIONS = {"draft", "regenerate_pdf", "list_recent"}


def angebot_draft_tool(args: Dict[str, Any], **_kw: Any) -> str:
    """Hermes-facing entry point. Returns a JSON string."""
    action = (args.get("action") or "").strip()
    if action not in _ACTIONS:
        return json.dumps(
            {"error": f"unknown action {action!r}; "
                      f"expected one of: {sorted(_ACTIONS)}"},
            ensure_ascii=False,
        )

    try:
        if action == "draft":
            result = draft(
                customer_id=int(args["customer_id"]),
                title=args.get("title", ""),
                line_items=args.get("line_items") or [],
                notes=args.get("notes", "") or "",
                style_skill_path=args.get("style_skill_path"),
            )
        elif action == "regenerate_pdf":
            result = regenerate_pdf(angebot_id=int(args["angebot_id"]))
        elif action == "list_recent":
            result = list_recent(
                customer_id=args.get("customer_id"),
                limit=int(args.get("limit", 10)),
            )
        else:  # pragma: no cover — guarded above
            return json.dumps({"error": f"unhandled action {action}"})
    except KeyError as e:
        return json.dumps(
            {"error": f"missing required argument: {e.args[0]}"},
            ensure_ascii=False,
        )
    except (ValueError, TypeError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except sqlite3.Error as e:
        return json.dumps({"error": f"DB error: {e}"}, ensure_ascii=False)

    return json.dumps({"ok": True, "result": result}, ensure_ascii=False)


def check_angebot_draft_requirements() -> bool:
    """Toolset availability check.

    We require WeasyPrint + Jinja2 at module level — if either is
    missing, the tool will still load (for the markdown-only path) but
    PDF rendering will return a clear error in the result.
    """
    try:
        import jinja2  # noqa: F401
        import weasyprint  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Hermes function-calling schema
# ---------------------------------------------------------------------------

ANGEBOT_DRAFT_SCHEMA: Dict[str, Any] = {
    "name": "angebot_draft",
    "description": (
        "Draft a German Handwerker Angebot (price quote) and render it "
        "as PDF. Persists into the customer_db angebote table with "
        "status='draft' and returns both a markdown summary (for chat "
        "review) and the PDF path. VAT is 19% MwSt; supports optional "
        "rabatt rules and styling via an angebot_style SKILL.md. "
        "Actions:\n"
        " - draft: build a new Angebot from line items\n"
        " - regenerate_pdf: re-render an existing Angebot from its "
        "stored JSON payload\n"
        " - list_recent: list recent Angebote (optionally by customer)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(_ACTIONS),
                "description": "Which Angebot operation to perform.",
            },
            "customer_id": {
                "type": "integer",
                "description": (
                    "Customer primary key. Required for 'draft'; "
                    "optional filter for 'list_recent'."
                ),
            },
            "title": {
                "type": "string",
                "description": "Angebot title / Betreff (draft).",
            },
            "line_items": {
                "type": "array",
                "description": (
                    "Line items for the Angebot (draft). Each entry: "
                    "{description, qty, unit, unit_price_eur}."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "qty": {"type": "number"},
                        "unit": {"type": "string"},
                        "unit_price_eur": {"type": "number"},
                    },
                    "required": [
                        "description", "qty", "unit", "unit_price_eur",
                    ],
                },
            },
            "notes": {
                "type": "string",
                "description": "Optional notes / Hinweise (draft).",
            },
            "style_skill_path": {
                "type": "string",
                "description": (
                    "Optional path to an angebot_style SKILL.md to apply "
                    "greeting / vat_mode / discount / signature slots "
                    "(draft)."
                ),
            },
            "angebot_id": {
                "type": "integer",
                "description": "Angebot primary key (regenerate_pdf).",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows for list_recent (default 10).",
                "default": 10,
            },
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Hermes registry hook
# ---------------------------------------------------------------------------

try:  # pragma: no cover — depends on host process
    from tools.registry import registry  # type: ignore

    registry.register(
        name="angebot_draft",
        toolset="mein_geselle",
        schema=ANGEBOT_DRAFT_SCHEMA,
        handler=angebot_draft_tool,
        check_fn=check_angebot_draft_requirements,
        emoji="\U0001f4dd",  # 📝
        max_result_size_chars=20_000,
        description=(
            "Draft a German Handwerker Angebot (PDF + markdown) for a "
            "customer; persists into the customer_db angebote table."
        ),
    )
except ImportError:
    # Hermes registry isn't on the path (standalone import). Module still
    # works as a plain library — see __main__ smoke test below.
    pass


__all__ = [
    "DB_PATH",
    "PDF_DIR",
    "DEFAULT_STYLE",
    "draft",
    "regenerate_pdf",
    "list_recent",
    "angebot_draft_tool",
    "ANGEBOT_DRAFT_SCHEMA",
    "fmt_eur",
    "fmt_qty",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover — manual smoke test
    print(f"[smoke] DB:  {DB_PATH}")
    print(f"[smoke] PDF: {PDF_DIR}")

    # 1. Make sure the DB has at least one customer; seed if empty.
    _conn = _connect()
    try:
        _row = _conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()
        _customer_count = _row["c"] if _row else 0
    except sqlite3.OperationalError:
        # Schema doesn't exist yet — tool_customer_db owns DDL. Bail out
        # with a clear message rather than silently recreate the schema.
        print(
            "[smoke] customers table missing — run "
            "`python tools/seed.py` first (it owns the schema)."
        )
        _conn.close()
        raise SystemExit(1)
    _conn.close()

    if _customer_count == 0:
        print(
            "[smoke] customers table is empty — run "
            "`python tools/seed.py` to populate fixtures."
        )
        raise SystemExit(1)
    print(f"[smoke] customers in DB: {_customer_count}")

    # 2. Build the smoke-test Angebot.
    smoke_items = [
        {
            "description": "Vorbereitung & Untergrund (abdecken, spachteln)",
            "qty": 4,
            "unit": "Std",
            "unit_price_eur": 65.00,
        },
        {
            "description": "Dispersionsfarbe (Alpinaweiß matt)",
            "qty": 8,
            "unit": "Liter",
            "unit_price_eur": 24.50,
        },
        {
            "description": "Streichen — 2 Anstriche",
            "qty": 18,
            "unit": "Std",
            "unit_price_eur": 65.00,
        },
    ]

    local_skill = (
        "/Users/paul/Desktop/hermes-challenge/mein-geselle/"
        "skills/handwerk/angebot_style/SKILL.md"
    )
    style_path = local_skill if Path(local_skill).exists() else None

    result = draft(
        customer_id=1,
        title="Wohnzimmer streichen (25 qm)",
        line_items=smoke_items,
        notes="Material wird gestellt. Termin nach Absprache.",
        style_skill_path=style_path,
    )

    if "error" in result:
        print(f"[smoke] ERROR: {result['error']}")
        raise SystemExit(1)

    print(f"[smoke] angebot_id     = {result['angebot_id']}")
    print(f"[smoke] angebot_no     = {result['angebot_no']}")
    print(f"[smoke] total_net_eur  = {fmt_eur(result['total_net_eur'])}")
    print(f"[smoke] vat_eur        = {fmt_eur(result['vat_eur'])}")
    print(f"[smoke] total_gross_eur= {fmt_eur(result['total_gross_eur'])}")
    print(f"[smoke] pdf_path       = {result['pdf_path']}")
    if result.get("pdf_error"):
        print(f"[smoke] pdf_error      = {result['pdf_error']}")

    # 3. Verify PDF exists & non-empty (when rendering succeeded).
    if result.get("pdf_path"):
        pdf_p = Path(result["pdf_path"])
        if pdf_p.exists() and pdf_p.stat().st_size > 0:
            print(
                f"[smoke] PDF OK         = {pdf_p.stat().st_size} bytes"
            )
        else:
            print("[smoke] WARNING: PDF missing or empty")
    else:
        print(
            "[smoke] PDF not rendered (see pdf_error above). "
            "The Angebot is still persisted with the markdown draft."
        )

    # 4. Print the markdown draft (truncated for terminal sanity).
    print("\n[smoke] --- draft_md ---")
    md = result["draft_md"]
    print(md if len(md) < 2000 else md[:2000] + "\n... (truncated)")

    # 5. list_recent round-trip.
    listing = list_recent(customer_id=1, limit=3)
    print(
        f"\n[smoke] list_recent(customer_id=1, limit=3) "
        f"→ {listing['count']} row(s)"
    )
    for a in listing["angebote"]:
        print(
            f"        #{a['id']:>3} {a['title'][:40]:40} "
            f"{fmt_eur(a['total_eur'])}  status={a['status']}"
        )
