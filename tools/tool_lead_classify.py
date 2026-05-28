#!/usr/bin/env python3
"""
Lead Classify Tool — Mein Geselle (Hermes plug-in tool)

Single Hermes tool entry `lead_classify` that turns a free-text inbound
message from a customer (voicemail transcription, SMS, email body, chat)
into a structured triage record so the Handwerker queue can prioritise
emergencies above small talk.

When Hermes should call this tool:
    - A new customer message arrives and the planner needs to decide
      which downstream tool to invoke next (calendar.book for emergencies,
      Angebot draft for Anfragen, etc.).
    - Before persisting a lead into the customer DB so the row carries a
      `category` and `urgency` tag from day one.

Output schema::

    {
        "category": "notfall" | "anfrage" | "follow_up" | "smalltalk",
        "urgency": 1-5,
        "confidence": 0.0-1.0,
        "extracted": {
            "customer_hint": str,
            "service_hint": str,
            "deadline_hint": str,
        },
    }

Implementation is hybrid and deterministic:
    1. A keyword rule set fires `notfall` on any of:
        Wasserschaden, Rohrbruch, Stromausfall, Notfall, dringend, sofort,
        asap (case- and umlaut-insensitive).
    2. A soft heuristic resolves the remaining buckets:
        - `?` AND ("Termin" or "Angebot") → anfrage
        - "Status", "wann", "wo bleibt" → follow_up
        - else → smalltalk
    3. If urgency >= 4, the category is forced to `notfall` regardless of
       step 2 — urgency is the dominant signal for the dispatcher.
    4. Confidence scales with the number of distinct keyword hits.

No LLM call required at this layer — keep it deterministic + cheap. For
fuzzy semantic classification, the agent can chain this with the LLM via
the planner (e.g. when confidence < 0.5).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List, Tuple

from tools.registry import registry  # type: ignore


# ---------------------------------------------------------------------------
# Keyword sets (case- and umlaut-insensitive — normalised via _norm())
# ---------------------------------------------------------------------------

NOTFALL_KEYWORDS: Tuple[str, ...] = (
    "wasserschaden",
    "rohrbruch",
    "stromausfall",
    "notfall",
    "dringend",
    "sofort",
    "asap",
)

# Urgency boosters — words that bump urgency without necessarily forcing
# the notfall category. Each match adds 1 to the urgency score.
URGENCY_BOOSTERS: Tuple[str, ...] = (
    "heute",
    "jetzt",
    "schnell",
    "akut",
    "eilig",
    "ueberlauf",
)

ANFRAGE_TRIGGERS: Tuple[str, ...] = ("termin", "angebot", "kostenvoranschlag")
FOLLOW_UP_TRIGGERS: Tuple[str, ...] = (
    "status",
    "wann",
    "wo bleibt",
    "update",
    "schon fertig",
)

# Service-domain vocabulary used for the `service_hint` extraction. The
# matching is intentionally narrow: a single word in the message wins the
# whole hint — the goal is "good enough for routing", not NLP.
SERVICE_HINTS: Tuple[str, ...] = (
    "heizung",
    "sanitaer",
    "elektro",
    "dach",
    "fliesen",
    "fenster",
    "tuer",
    "boden",
    "kueche",
    "bad",
    "rohr",
    "wasser",
    "strom",
)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    """Lowercase + strip diacritics so 'Wasserüberlauf' matches
    'wasserueberlauf' in our keyword tables."""
    text = text.lower()
    # ä/ö/ü/ß → ae/oe/ue/ss BEFORE NFKD so we don't lose them entirely.
    replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    # Drop any remaining combining marks (e.g. é → e).
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    return text


def _count_hits(needles: Tuple[str, ...], haystack: str) -> List[str]:
    """Return the unique needles that occur as whole-word matches in
    ``haystack``. Multi-word needles (e.g. 'wo bleibt') match literally."""
    hits: List[str] = []
    for needle in needles:
        if " " in needle:
            if needle in haystack:
                hits.append(needle)
        else:
            # Whole-word match to avoid false positives like "sofortig" → "sofort".
            if re.search(rf"\b{re.escape(needle)}\b", haystack):
                hits.append(needle)
    return hits


# ---------------------------------------------------------------------------
# Extraction helpers — best-effort, regex-based
# ---------------------------------------------------------------------------


# Quick-and-dirty German weekday + relative-date set for deadline hints.
_DATE_PATTERNS = (
    r"\b(?:heute|morgen|uebermorgen|am wochenende|naechste woche)\b",
    r"\b(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\b",
    r"\b\d{1,2}\.\d{1,2}\.(?:\d{2,4})?\b",  # e.g. 14.06. or 14.6.2026
    r"\b\d{1,2}:\d{2}\s*uhr\b",  # e.g. "14:00 Uhr"
)


def _extract_customer_hint(original: str) -> str:
    """Pick the first capitalised name-like token from the ORIGINAL text
    (we want the user's original casing here, not the normalised form)."""
    # "Herr/Frau Mueller" or a bare "Mueller GmbH" — both fine.
    m = re.search(
        r"\b(?:Herr|Frau)\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+)", original
    )
    if m:
        return m.group(0)
    m = re.search(r"\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]{2,})\s+(?:GmbH|KG|AG|UG)", original)
    if m:
        return m.group(0)
    return ""


def _extract_service_hint(normalised: str) -> str:
    """Return the first matching service keyword, or ''."""
    for kw in SERVICE_HINTS:
        if re.search(rf"\b{re.escape(kw)}\b", normalised):
            return kw
    return ""


def _extract_deadline_hint(normalised: str) -> str:
    """Return the first deadline-ish phrase from the message, or ''."""
    for pat in _DATE_PATTERNS:
        m = re.search(pat, normalised)
        if m:
            return m.group(0)
    return ""


# ---------------------------------------------------------------------------
# Core classify
# ---------------------------------------------------------------------------


def classify(text: str) -> Dict[str, Any]:
    """Classify ``text`` into a triage record. See module docstring for the
    output schema. Empty input returns a low-confidence smalltalk record."""
    if not text or not text.strip():
        return {
            "category": "smalltalk",
            "urgency": 1,
            "confidence": 0.0,
            "extracted": {
                "customer_hint": "",
                "service_hint": "",
                "deadline_hint": "",
            },
        }

    normalised = _norm(text)

    # --- 1. Keyword scoring ------------------------------------------------
    notfall_hits = _count_hits(NOTFALL_KEYWORDS, normalised)
    urgency_hits = _count_hits(URGENCY_BOOSTERS, normalised)
    anfrage_hits = _count_hits(ANFRAGE_TRIGGERS, normalised)
    follow_up_hits = _count_hits(FOLLOW_UP_TRIGGERS, normalised)

    # --- 2. Urgency calculation -------------------------------------------
    # Baseline 1, +2 per notfall hit, +1 per urgency booster, clamped 1..5.
    urgency = 1 + 2 * len(notfall_hits) + len(urgency_hits)
    urgency = max(1, min(5, urgency))

    # --- 3. Category resolution -------------------------------------------
    # Follow-up triggers ("wo bleibt", "Status", "wann") take precedence
    # over Anfrage triggers — "Wo bleibt mein Angebot?" is a status chase,
    # not a fresh enquiry, even though it contains the word "Angebot".
    if notfall_hits or urgency >= 4:
        category = "notfall"
    elif follow_up_hits:
        category = "follow_up"
    elif anfrage_hits and "?" in text:
        category = "anfrage"
    elif anfrage_hits:
        # "Ich brauche einen Termin nächste Woche." — no '?' but still an
        # Anfrage; the soft rule is "?" XOR an Anfrage trigger.
        category = "anfrage"
    else:
        category = "smalltalk"

    # --- 4. Confidence -----------------------------------------------------
    # Sum the unique signals that voted for the chosen bucket. We cap at
    # 1.0 by dividing through a small denominator so a single strong hit
    # (e.g. "Wasserschaden") already lands at ~0.6.
    if category == "notfall":
        signal = len(notfall_hits) + 0.5 * len(urgency_hits)
    elif category == "anfrage":
        signal = len(anfrage_hits) + (0.5 if "?" in text else 0)
    elif category == "follow_up":
        signal = len(follow_up_hits)
    else:
        signal = 0.2  # smalltalk floor — we're never very confident here.
    confidence = min(1.0, round(signal / 1.5, 2))

    # --- 5. Extraction -----------------------------------------------------
    extracted = {
        "customer_hint": _extract_customer_hint(text),
        "service_hint": _extract_service_hint(normalised),
        "deadline_hint": _extract_deadline_hint(normalised),
    }

    return {
        "category": category,
        "urgency": urgency,
        "confidence": confidence,
        "extracted": extracted,
    }


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def lead_classify_tool(args: Dict[str, Any], **_kw: Any) -> str:
    """Hermes-facing entry point. Returns a JSON string."""
    action = (args.get("action") or "classify").strip()
    try:
        if action == "classify":
            text = args.get("text", "")
            if not isinstance(text, str):
                return json.dumps(
                    {"error": "text must be a string"}, ensure_ascii=False
                )
            result = classify(text)
        else:
            return json.dumps(
                {"error": f"Unknown action '{action}'"}, ensure_ascii=False
            )
    except (ValueError, TypeError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps({"ok": True, "result": result}, ensure_ascii=False)


def check_lead_classify_requirements() -> bool:
    """Stdlib only — always available."""
    return True


# ---------------------------------------------------------------------------
# Hermes function-calling schema
# ---------------------------------------------------------------------------


LEAD_CLASSIFY_SCHEMA: Dict[str, Any] = {
    "name": "lead_classify",
    "description": (
        "Deterministic triage classifier for incoming Handwerker leads. "
        "Returns category (notfall|anfrage|follow_up|smalltalk), urgency "
        "1-5, confidence 0-1, and best-effort extracted hints "
        "(customer/service/deadline). Cheap, keyword-based, no LLM call. "
        "For fuzzy semantic classification the agent can chain this with "
        "the LLM via the planner — e.g. when confidence < 0.5, escalate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["classify"],
                "description": "Always 'classify'. Reserved for future "
                "actions (e.g. batch classify).",
                "default": "classify",
            },
            "text": {
                "type": "string",
                "description": "Raw inbound message text "
                "(voicemail transcript, SMS, email body).",
            },
        },
        "required": ["text"],
    },
}


# ---------------------------------------------------------------------------
# Hermes registry hook
# ---------------------------------------------------------------------------
# Top-level call so the hermes-agent registry AST scanner discovers this
# module (it only picks up bare `registry.register(...)` calls — not ones
# wrapped in try/except).


registry.register(
    name="lead_classify",
    toolset="mein_geselle",
    schema=LEAD_CLASSIFY_SCHEMA,
    handler=lead_classify_tool,
    check_fn=check_lead_classify_requirements,
    emoji="🏷️",
    max_result_size_chars=4_000,
)


__all__ = [
    "classify",
    "lead_classify_tool",
    "LEAD_CLASSIFY_SCHEMA",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    samples: List[Tuple[str, str]] = [
        (
            "notfall (Wasserschaden)",
            "Hallo, hier ist Herr Müller. Wir haben einen Wasserschaden "
            "in der Küche, brauche dringend Hilfe!",
        ),
        (
            "notfall (Rohrbruch + sofort)",
            "Rohrbruch im Bad, kommt bitte sofort!",
        ),
        (
            "anfrage",
            "Guten Tag, könnten Sie mir ein Angebot für eine neue "
            "Heizung machen? Termin nächste Woche möglich?",
        ),
        (
            "follow_up",
            "Hallo, wann kommt eigentlich mein Angebot? Wo bleibt der "
            "Status?",
        ),
        (
            "smalltalk",
            "Danke nochmal für die gute Arbeit letzte Woche, alles bestens!",
        ),
        (
            "edge — booster only",
            "Heute noch akut bitte, Heizung ist aus.",
        ),
    ]

    print("[smoke] lead_classify — running 6 samples\n")
    for label, text in samples:
        r = classify(text)
        print(f"  • {label}")
        print(f"      text: {text}")
        print(
            f"      → category={r['category']:9}  urgency={r['urgency']}  "
            f"confidence={r['confidence']:.2f}"
        )
        print(f"      extracted: {r['extracted']}\n")

    # Round-trip through the Hermes-facing entrypoint.
    payload = lead_classify_tool(
        {"action": "classify", "text": "Wasserschaden, sofort kommen!"}
    )
    print(f"[smoke] tool dispatch JSON: {payload}")

    # Empty input edge case.
    print(f"[smoke] empty input → {json.dumps(classify(''), ensure_ascii=False)}")
