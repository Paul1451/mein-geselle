#!/usr/bin/env python3
"""
seed.py — Idempotent seeder for the Mein Geselle customer DB.

Inserts 10 fictional clients of "Maler Schulz" (a Berlin Handwerker), each
with a believable Berlin address, German phone number, 2–3 appointments,
and 1–2 historical Angebote.

Idempotency:
    Each customer carries a stable "seed key" embedded in notes_md as a
    machine-readable marker (``[seed:<slug>]``). On every run we look up
    the existing row by that marker; if present, we update in place (and
    skip re-inserting child appointments/Angebote that already exist for
    that customer). Running the script repeatedly will not duplicate rows.

Usage:
    python seed.py            # seed into ~/.hermes/data/handwerk.db
    python seed.py --db PATH  # seed into a custom SQLite file
    python seed.py --reset    # drop & recreate all tables first

All German strings keep umlauts as-is (UTF-8). Comments are English.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

# Make sibling import work both when run as a script and as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_customer_db import (  # noqa: E402  (sys.path tweak above)
    DB_PATH,
    SCHEMA_SQL,
    _now_iso,
    connect,
)


# ---------------------------------------------------------------------------
# Fictional customer fixtures — all data is invented; resemblance is coincidence.
# ---------------------------------------------------------------------------

# Anchor "now" for appointments so the generated dates stay coherent and the
# seed remains deterministic-ish across runs on different days.
_NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _dt(days_from_now: int, hour: int = 9, minute: int = 0) -> str:
    """Helper: ISO timestamp ``days_from_now`` days from _NOW at HH:MM UTC."""
    t = (_NOW + timedelta(days=days_from_now)).replace(
        hour=hour, minute=minute, second=0
    )
    return t.isoformat()


CUSTOMERS: List[Dict[str, Any]] = [
    {
        "seed": "frau-mueller-prenzlauer-berg",
        "name": "Frau Müller",
        "phone": "+49 30 4416 2271",
        "email": "a.mueller@example.de",
        "address": "Schönhauser Allee 142, 10437 Berlin",
        "notes_md": "Stammkundin seit 2019. Bevorzugt Termine vormittags.",
        "appointments": [
            {"d": -45, "h": 9, "title": "Wohnzimmer streichen",
             "notes_md": "Farbe: Alpinaweiß matt", "status": "done"},
            {"d": 7, "h": 10, "title": "Vor-Ort-Besichtigung Flur",
             "notes_md": "Tapete entfernen, Risse prüfen", "status": "planned"},
            {"d": 21, "h": 8, "title": "Flur tapezieren & streichen",
             "notes_md": None, "status": "planned"},
        ],
        "angebote": [
            {"title": "Wohnzimmer Komplettanstrich", "total_eur": 1240.00,
             "status": "accepted",
             "scope_md": "- Wände 2x streichen\n- Decke 1x streichen\n- Abdeckarbeiten"},
        ],
    },
    {
        "seed": "herr-becker-charlottenburg",
        "name": "Herr Becker",
        "phone": "+49 30 8821 5530",
        "email": "becker@example.de",
        "address": "Kantstraße 88, 10627 Berlin",
        "notes_md": "Vermieter mit 3 Objekten. Zahlt zuverlässig per Überweisung.",
        "appointments": [
            {"d": -30, "h": 9, "title": "WE 3.OG nach Auszug streichen",
             "notes_md": "Mieterwechsel zum 01.", "status": "done"},
            {"d": 14, "h": 8, "title": "Treppenhaus Renovierung Beginn",
             "notes_md": "Gerüst stellt der Vermieter.", "status": "planned"},
        ],
        "angebote": [
            {"title": "WE 3.OG Renovierung", "total_eur": 2180.50,
             "status": "accepted",
             "scope_md": "- Alle Wände weiß\n- Türen lackieren\n- Heizkörper streichen"},
            {"title": "Treppenhaus EG–4.OG", "total_eur": 5840.00,
             "status": "sent",
             "scope_md": "- Wände Latexfarbe\n- Handlauf abschleifen + lasieren"},
        ],
    },
    {
        "seed": "familie-yildirim-neukoelln",
        "name": "Familie Yıldırım",
        "phone": "+49 30 6217 9043",
        "email": "yildirim@example.de",
        "address": "Sonnenallee 211, 12059 Berlin",
        "notes_md": "Sprachpräferenz: Deutsch, gerne auch Türkisch. Kinder im Haus — Geräusche reduzieren.",
        "appointments": [
            {"d": -10, "h": 10, "title": "Kinderzimmer streichen",
             "notes_md": "Wandfarbe pastellblau", "status": "done"},
            {"d": 12, "h": 9, "title": "Küche Nachbesserung",
             "notes_md": "Fettflecken nachstreichen", "status": "planned"},
        ],
        "angebote": [
            {"title": "Kinderzimmer + Flur", "total_eur": 980.00,
             "status": "accepted",
             "scope_md": "- 2 Räume, lösungsmittelfreie Farbe (Kinder)"},
        ],
    },
    {
        "seed": "frau-kowalski-friedrichshain",
        "name": "Frau Kowalski",
        "phone": "+49 30 2935 1107",
        "email": "k.kowalski@example.de",
        "address": "Boxhagener Straße 56, 10245 Berlin",
        "notes_md": "Allergisch gegen Lösungsmittel → nur emissionsarme Farben.",
        "appointments": [
            {"d": -60, "h": 9, "title": "Schlafzimmer streichen",
             "notes_md": "Allergikerfarbe verwendet (Caparol Sensitiv).", "status": "done"},
            {"d": 5, "h": 14, "title": "Beratung Wohnzimmer",
             "notes_md": "Farbtonberatung", "status": "planned"},
            {"d": 30, "h": 8, "title": "Wohnzimmer streichen",
             "notes_md": None, "status": "planned"},
        ],
        "angebote": [
            {"title": "Schlafzimmer Allergiker-Anstrich", "total_eur": 690.00,
             "status": "accepted",
             "scope_md": "- Caparol Sensitiv\n- Lüften zwischen Schichten"},
            {"title": "Wohnzimmer (Beratungstermin offen)", "total_eur": 1120.00,
             "status": "draft", "scope_md": "- Voraussichtlich 2 Wände in Akzentfarbe"},
        ],
    },
    {
        "seed": "herr-dr-hartmann-zehlendorf",
        "name": "Herr Dr. Hartmann",
        "phone": "+49 30 8013 4422",
        "email": "hartmann@example.de",
        "address": "Clayallee 174, 14195 Berlin",
        "notes_md": "Arzt, sehr beschäftigt. Termine bevorzugt freitags nachmittags.",
        "appointments": [
            {"d": -7, "h": 15, "title": "Vor-Ort-Besichtigung Praxis",
             "notes_md": "Wartezimmer + Empfang", "status": "done"},
            {"d": 28, "h": 15, "title": "Praxis Wartezimmer streichen",
             "notes_md": "Außerhalb der Sprechzeiten!", "status": "planned"},
        ],
        "angebote": [
            {"title": "Arztpraxis Wartezimmer + Empfang", "total_eur": 3450.00,
             "status": "sent",
             "scope_md": "- Hygiene-tauglicher Anstrich (abwaschbar)\n- Wochenend-Termin"},
        ],
    },
    {
        "seed": "frau-schneider-wedding",
        "name": "Frau Schneider",
        "phone": "+49 30 4488 9971",
        "email": "schneider@example.de",
        "address": "Müllerstraße 152, 13353 Berlin",
        "notes_md": "Rentnerin, ruft lieber an als per E-Mail.",
        "appointments": [
            {"d": -90, "h": 10, "title": "Küche streichen",
             "notes_md": "Decke nikotingelb → Sperrgrund nötig.", "status": "done"},
            {"d": 18, "h": 10, "title": "Schlafzimmer streichen",
             "notes_md": None, "status": "planned"},
        ],
        "angebote": [
            {"title": "Küche inkl. Sperrgrund", "total_eur": 870.00,
             "status": "accepted",
             "scope_md": "- Sperrgrund 1x\n- Decke 2x\n- Wände 2x"},
        ],
    },
    {
        "seed": "herr-okonkwo-kreuzberg",
        "name": "Herr Okonkwo",
        "phone": "+49 30 6914 2208",
        "email": "okonkwo@example.de",
        "address": "Oranienstraße 64, 10969 Berlin",
        "notes_md": "Café-Besitzer. Renovierungen nur Montagvormittag (Ruhetag).",
        "appointments": [
            {"d": -14, "h": 8, "title": "Café Innenraum streichen",
             "notes_md": "Akzentwand dunkelgrün.", "status": "done"},
            {"d": 21, "h": 8, "title": "Toilette Café nachstreichen",
             "notes_md": "Graffiti übermalen.", "status": "planned"},
        ],
        "angebote": [
            {"title": "Café Komplett-Refresh", "total_eur": 2980.00,
             "status": "accepted",
             "scope_md": "- 4 Wände\n- Akzentwand in Petrol\n- Decke weiß"},
            {"title": "WC-Bereich Nachstrich", "total_eur": 340.00,
             "status": "draft", "scope_md": "- Anti-Graffiti-Grundierung"},
        ],
    },
    {
        "seed": "frau-rossi-schoeneberg",
        "name": "Frau Rossi",
        "phone": "+49 30 7720 3318",
        "email": "rossi@example.de",
        "address": "Hauptstraße 33, 10827 Berlin",
        "notes_md": "Italienerin, Deutsch okay. Mag warme Farbtöne (Terrakotta, Ocker).",
        "appointments": [
            {"d": -21, "h": 9, "title": "Wohnzimmer Akzentwand",
             "notes_md": "Terrakotta-Ton gemischt.", "status": "done"},
            {"d": 10, "h": 9, "title": "Esszimmer streichen",
             "notes_md": None, "status": "planned"},
        ],
        "angebote": [
            {"title": "Wohnzimmer Akzentwand", "total_eur": 480.00,
             "status": "accepted",
             "scope_md": "- 1 Wand, Spezialfarbton gemischt"},
        ],
    },
    {
        "seed": "herr-petersen-pankow",
        "name": "Herr Petersen",
        "phone": "+49 30 4452 6680",
        "email": "petersen@example.de",
        "address": "Berliner Straße 27, 13189 Berlin",
        "notes_md": "Hausbesitzer (EFH). Plant größere Sanierung 2026.",
        "appointments": [
            {"d": -3, "h": 11, "title": "Erstgespräch Sanierung",
             "notes_md": "Aufmaß genommen, Fotos gemacht.", "status": "done"},
            {"d": 60, "h": 8, "title": "Sanierung Phase 1: EG",
             "notes_md": "Schwerpunkt Wohnbereich.", "status": "planned"},
        ],
        "angebote": [
            {"title": "Sanierung Phase 1 — Erdgeschoss", "total_eur": 7860.00,
             "status": "sent",
             "scope_md": "- 4 Räume\n- Decken + Wände\n- Türrahmen lackieren"},
            {"title": "Sanierung Phase 2 — OG (Vorschau)", "total_eur": 6420.00,
             "status": "draft", "scope_md": "- Optional, Q3 2026"},
        ],
    },
    {
        "seed": "frau-johansson-mitte",
        "name": "Frau Jóhansson",
        "phone": "+49 30 2018 7745",
        "email": "johansson@example.de",
        "address": "Linienstraße 91, 10119 Berlin",
        "notes_md": "Isländerin, Englisch bevorzugt. Architektin — hohe Detailansprüche.",
        "appointments": [
            {"d": -120, "h": 9, "title": "Loft Wohnbereich streichen",
             "notes_md": "Sehr glatter Untergrund verlangt → Lasur statt Dispersion.",
             "status": "done"},
            {"d": 4, "h": 14, "title": "Vor-Ort-Termin neuer Büroraum",
             "notes_md": "Aufmaß + Materialwunsch klären.", "status": "planned"},
        ],
        "angebote": [
            {"title": "Loft Wohnbereich (Lasurtechnik)", "total_eur": 2240.00,
             "status": "accepted",
             "scope_md": "- Spezial-Lasur 2 Schichten\n- Detailarbeit an Stuck"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Idempotent insertion
# ---------------------------------------------------------------------------

SEED_TAG_PREFIX = "[seed:"


def _seed_marker(seed_key: str) -> str:
    return f"{SEED_TAG_PREFIX}{seed_key}]"


def _find_customer_by_seed(conn, seed_key: str):
    marker = _seed_marker(seed_key)
    return conn.execute(
        "SELECT id, notes_md FROM customers WHERE notes_md LIKE ?",
        (f"%{marker}%",),
    ).fetchone()


def _appointment_exists(conn, customer_id: int, starts_at: str, title: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM appointments WHERE customer_id=? AND starts_at=? AND title=?",
        (customer_id, starts_at, title),
    ).fetchone()
    return row is not None


def _angebot_exists(conn, customer_id: int, title: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM angebote WHERE customer_id=? AND title=?",
        (customer_id, title),
    ).fetchone()
    return row is not None


def seed(db_path: Path = DB_PATH, reset: bool = False) -> Dict[str, int]:
    """Run the idempotent seed. Returns counters of what changed.

    ``reset=True`` wipes the three Mein-Geselle tables first (handy for
    development; never used in production).
    """
    conn = connect(db_path)
    try:
        if reset:
            conn.executescript(
                "DROP TABLE IF EXISTS angebote;"
                "DROP TABLE IF EXISTS appointments;"
                "DROP TABLE IF EXISTS customers;"
            )
            conn.executescript(SCHEMA_SQL)

        counters = {
            "customers_inserted": 0,
            "customers_updated": 0,
            "appointments_inserted": 0,
            "angebote_inserted": 0,
        }

        for c in CUSTOMERS:
            marker = _seed_marker(c["seed"])
            # Embed the seed marker as the last line of notes_md so we can
            # find this row again on later runs without polluting the
            # human-readable note above it.
            user_notes = (c.get("notes_md") or "").strip()
            full_notes = f"{user_notes}\n\n{marker}".strip()

            existing = _find_customer_by_seed(conn, c["seed"])
            now = _now_iso()
            if existing is None:
                cur = conn.execute(
                    "INSERT INTO customers (name, phone, email, address, "
                    "notes_md, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (c["name"], c["phone"], c["email"], c["address"],
                     full_notes, now, now),
                )
                customer_id = cur.lastrowid
                counters["customers_inserted"] += 1
            else:
                customer_id = existing["id"]
                conn.execute(
                    "UPDATE customers SET name=?, phone=?, email=?, "
                    "address=?, notes_md=?, updated_at=? WHERE id=?",
                    (c["name"], c["phone"], c["email"], c["address"],
                     full_notes, now, customer_id),
                )
                counters["customers_updated"] += 1

            # Appointments — only insert ones that aren't already there.
            for appt in c["appointments"]:
                starts_at = _dt(appt["d"], appt["h"], appt.get("m", 0))
                if _appointment_exists(conn, customer_id, starts_at, appt["title"]):
                    continue
                # Default 2-hour window if no explicit end time.
                ends_at = _dt(appt["d"], appt["h"] + 2, appt.get("m", 0))
                conn.execute(
                    "INSERT INTO appointments (customer_id, starts_at, "
                    "ends_at, title, notes_md, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (customer_id, starts_at, ends_at, appt["title"],
                     appt.get("notes_md"), appt.get("status", "planned"), now),
                )
                counters["appointments_inserted"] += 1

            # Angebote — same idempotency rule.
            for ang in c["angebote"]:
                if _angebot_exists(conn, customer_id, ang["title"]):
                    continue
                conn.execute(
                    "INSERT INTO angebote (customer_id, title, scope_md, "
                    "total_eur, status, pdf_path, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (customer_id, ang["title"], ang.get("scope_md"),
                     float(ang["total_eur"]), ang.get("status", "draft"),
                     ang.get("pdf_path"), now),
                )
                counters["angebote_inserted"] += 1

        conn.commit()
        return counters
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Mein Geselle DB.")
    parser.add_argument(
        "--db", type=Path, default=DB_PATH,
        help=f"SQLite DB path (default: {DB_PATH})",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Drop and recreate all tables before seeding.",
    )
    args = parser.parse_args(argv)

    counters = seed(db_path=args.db, reset=args.reset)
    print(f"Seed complete → {args.db}")
    for key, value in counters.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
