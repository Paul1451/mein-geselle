#!/usr/bin/env python3
"""
Calendar Tool — Mein Geselle (Hermes plug-in tool)

Single Hermes tool entry `calendar` that manages a Handwerker's appointment
calendar. Storage is a local iCal (RFC-5545) file at
``~/.hermes/data/handwerk.ics`` so the data stays portable — every modern
calendar app (Apple Calendar, Google Calendar, Thunderbird, Outlook) can
import or subscribe to the file without any extra adapter layer.

When Hermes should call this tool:
    - The user asks for the next free slot ("Wann hätte ich morgen Zeit?")
    - The user wants to book a Termin ("Trag den Müller-Termin Dienstag
      14 Uhr für eine Stunde ein.")
    - The user wants to see upcoming appointments ("Was steht diese Woche
      an?")
    - The user wants to cancel a Termin by its UID

Actions (selected via the `action` argument):
    - list_free_slots(date_iso, duration_min, business_hours_start,
        business_hours_end) -> list[{"start","end"}]
    - book(starts_at_iso, ends_at_iso, title, customer_id?, notes?)
        -> {"event_uid": str}
    - list_upcoming(days_ahead=7) -> list[event]
    - cancel(event_uid) -> {"cancelled": bool}

All datetimes are ISO-8601 in Europe/Berlin (stdlib zoneinfo). A conflict
check runs before every book(). When ``customer_id`` is provided, the
booking is also mirrored into the SQLite ``appointments`` table so the
customer DB and the calendar stay consistent.

The icalendar library is NOT in hermes-agent/pyproject.toml as of writing,
so this module hand-rolls a minimal RFC-5545 reader/writer (VCALENDAR /
VEVENT with UID, DTSTART, DTEND, SUMMARY, DESCRIPTION, X-CUSTOMER-ID,
DTSTAMP). That keeps the dependency footprint at stdlib-only.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

ICS_PATH = Path(os.path.expanduser("~/.hermes/data/handwerk.ics"))
DB_PATH = Path(os.path.expanduser("~/.hermes/data/handwerk.db"))
TZ = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")
PRODID = "-//Mein Geselle//Hermes Calendar Tool//DE"


# ---------------------------------------------------------------------------
# Hand-rolled minimal RFC-5545 helpers
# ---------------------------------------------------------------------------
# Format used: every VEVENT stores DTSTART/DTEND in UTC (suffix "Z"),
# which is the safest interop choice. We convert to/from Europe/Berlin at
# the API boundary.


@dataclass
class Event:
    """An in-memory VEVENT representation."""

    uid: str
    dtstart: datetime  # tz-aware (UTC)
    dtend: datetime  # tz-aware (UTC)
    summary: str = ""
    description: str = ""
    customer_id: Optional[int] = None
    dtstamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ----- iCal serialisation -----

    @staticmethod
    def _fmt_dt(dt: datetime) -> str:
        """Format a tz-aware datetime as UTC RFC-5545 (YYYYMMDDTHHMMSSZ)."""
        return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    @staticmethod
    def _escape(text: str) -> str:
        """Escape special characters per RFC-5545 §3.3.11."""
        return (
            text.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n")
        )

    @staticmethod
    def _unescape(text: str) -> str:
        """Reverse of _escape."""
        # Order matters: handle \\ last to avoid double-unescaping.
        return (
            text.replace("\\n", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\")
        )

    def to_ical(self) -> str:
        """Serialise to a VEVENT block (no leading/trailing newline)."""
        lines = [
            "BEGIN:VEVENT",
            f"UID:{self.uid}",
            f"DTSTAMP:{self._fmt_dt(self.dtstamp)}",
            f"DTSTART:{self._fmt_dt(self.dtstart)}",
            f"DTEND:{self._fmt_dt(self.dtend)}",
            f"SUMMARY:{self._escape(self.summary)}",
        ]
        if self.description:
            lines.append(f"DESCRIPTION:{self._escape(self.description)}")
        if self.customer_id is not None:
            lines.append(f"X-CUSTOMER-ID:{self.customer_id}")
        lines.append("END:VEVENT")
        return "\r\n".join(lines)

    def to_public_dict(self) -> Dict[str, Any]:
        """Render as JSON-safe dict with local-tz ISO timestamps."""
        return {
            "event_uid": self.uid,
            "starts_at": self.dtstart.astimezone(TZ).isoformat(),
            "ends_at": self.dtend.astimezone(TZ).isoformat(),
            "title": self.summary,
            "notes": self.description,
            "customer_id": self.customer_id,
        }


def _parse_ics_datetime(raw: str) -> datetime:
    """Parse a RFC-5545 DATE-TIME value. Accepts the ``Z`` UTC suffix or a
    naive local-time string (which we treat as Europe/Berlin)."""
    raw = raw.strip()
    if raw.endswith("Z"):
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=TZ)


def _read_ics(path: Path) -> List[Event]:
    """Parse the on-disk VCALENDAR into a list of Event objects.

    Returns an empty list when the file is missing. Unknown properties are
    ignored gracefully so a calendar edited by a third-party app stays
    readable.
    """
    if not path.exists():
        return []

    # Unfold long lines (RFC-5545 §3.1: a leading space/tab continues the
    # previous logical line).
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"\r?\n[ \t]", "", raw)

    events: List[Event] = []
    current: Optional[Dict[str, Any]] = None
    for line in raw.splitlines():
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            try:
                ev = Event(
                    uid=current.get("UID", str(uuid.uuid4())),
                    dtstart=_parse_ics_datetime(current["DTSTART"]),
                    dtend=_parse_ics_datetime(current["DTEND"]),
                    summary=Event._unescape(current.get("SUMMARY", "")),
                    description=Event._unescape(current.get("DESCRIPTION", "")),
                    customer_id=(
                        int(current["X-CUSTOMER-ID"])
                        if current.get("X-CUSTOMER-ID")
                        else None
                    ),
                    dtstamp=(
                        _parse_ics_datetime(current["DTSTAMP"])
                        if current.get("DTSTAMP")
                        else datetime.now(UTC)
                    ),
                )
                events.append(ev)
            except (KeyError, ValueError):
                # Skip malformed VEVENT but keep parsing the rest.
                pass
            current = None
        elif current is not None and ":" in line:
            # iCal allows parameters after ";", e.g. DTSTART;TZID=...:...
            # We only care about the property name (before ";" or ":") and
            # the value after the FIRST ":".
            head, _, value = line.partition(":")
            key = head.split(";", 1)[0]
            current[key] = value
    return events


def _write_ics(path: Path, events: List[Event]) -> None:
    """Atomically write a VCALENDAR containing all given events."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
    ]
    for ev in events:
        lines.append(ev.to_ical())
    lines.append("END:VCALENDAR")

    payload = "\r\n".join(lines) + "\r\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX


# ---------------------------------------------------------------------------
# Datetime parsing helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string; tz-naive inputs are assumed Europe/Berlin."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def _parse_hhmm(value: str) -> Tuple[int, int]:
    """Parse 'HH:MM' to (hour, minute)."""
    hh, mm = value.split(":", 1)
    return int(hh), int(mm)


def _overlaps(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    """Half-open interval overlap check."""
    return a_start < b_end and b_start < a_end


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def list_free_slots(
    date_iso: str,
    duration_min: int,
    business_hours_start: str = "08:00",
    business_hours_end: str = "17:00",
) -> List[Dict[str, str]]:
    """Return free intervals of at least ``duration_min`` minutes on
    ``date_iso`` (YYYY-MM-DD) within the given business-hour window.

    The search is performed entirely in Europe/Berlin. All returned
    timestamps are tz-aware ISO-8601 strings in Europe/Berlin.
    """
    if duration_min <= 0:
        raise ValueError("duration_min must be positive")

    day = datetime.fromisoformat(date_iso).date()
    bh_start_h, bh_start_m = _parse_hhmm(business_hours_start)
    bh_end_h, bh_end_m = _parse_hhmm(business_hours_end)
    window_start = datetime(
        day.year, day.month, day.day, bh_start_h, bh_start_m, tzinfo=TZ
    )
    window_end = datetime(
        day.year, day.month, day.day, bh_end_h, bh_end_m, tzinfo=TZ
    )
    if window_end <= window_start:
        return []

    # Collect busy intervals overlapping the window, normalised to TZ.
    busy: List[Tuple[datetime, datetime]] = []
    for ev in _read_ics(ICS_PATH):
        s = ev.dtstart.astimezone(TZ)
        e = ev.dtend.astimezone(TZ)
        if _overlaps(s, e, window_start, window_end):
            busy.append((max(s, window_start), min(e, window_end)))
    busy.sort()

    # Merge overlapping busy intervals.
    merged: List[Tuple[datetime, datetime]] = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Walk the gaps and keep ones long enough.
    free: List[Dict[str, str]] = []
    cursor = window_start
    min_delta = timedelta(minutes=duration_min)
    for s, e in merged:
        if s - cursor >= min_delta:
            free.append({"start": cursor.isoformat(), "end": s.isoformat()})
        cursor = max(cursor, e)
    if window_end - cursor >= min_delta:
        free.append({"start": cursor.isoformat(), "end": window_end.isoformat()})
    return free


def book(
    starts_at_iso: str,
    ends_at_iso: str,
    title: str,
    customer_id: Optional[int] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new VEVENT after a conflict check. Mirrors into SQLite
    ``appointments`` when ``customer_id`` is provided."""
    starts_at = _parse_iso(starts_at_iso)
    ends_at = _parse_iso(ends_at_iso)
    if ends_at <= starts_at:
        raise ValueError("ends_at must be after starts_at")
    if not title or not title.strip():
        raise ValueError("title is required")

    events = _read_ics(ICS_PATH)
    for ev in events:
        if _overlaps(
            starts_at.astimezone(UTC),
            ends_at.astimezone(UTC),
            ev.dtstart,
            ev.dtend,
        ):
            raise ValueError(
                f"Conflict with existing event '{ev.summary}' "
                f"({ev.dtstart.astimezone(TZ).isoformat()})"
            )

    uid = f"{uuid.uuid4()}@mein-geselle"
    new_ev = Event(
        uid=uid,
        dtstart=starts_at.astimezone(UTC),
        dtend=ends_at.astimezone(UTC),
        summary=title.strip(),
        description=(notes or "").strip(),
        customer_id=customer_id,
    )
    events.append(new_ev)
    _write_ics(ICS_PATH, events)

    if customer_id is not None:
        _mirror_to_sqlite(new_ev)

    return {"event_uid": uid}


def list_upcoming(days_ahead: int = 7) -> List[Dict[str, Any]]:
    """Return events starting within the next ``days_ahead`` days,
    ordered chronologically."""
    if days_ahead <= 0:
        raise ValueError("days_ahead must be positive")
    now = datetime.now(TZ)
    horizon = now + timedelta(days=days_ahead)
    upcoming = [
        ev
        for ev in _read_ics(ICS_PATH)
        if now <= ev.dtstart.astimezone(TZ) <= horizon
    ]
    upcoming.sort(key=lambda e: e.dtstart)
    return [ev.to_public_dict() for ev in upcoming]


def cancel(event_uid: str) -> Dict[str, Any]:
    """Remove the VEVENT with the given UID. Returns ``{"cancelled": bool}``."""
    events = _read_ics(ICS_PATH)
    remaining = [ev for ev in events if ev.uid != event_uid]
    removed = len(remaining) != len(events)
    if removed:
        _write_ics(ICS_PATH, remaining)
        _cancel_in_sqlite(event_uid)
    return {"cancelled": removed}


# ---------------------------------------------------------------------------
# SQLite mirroring (best-effort — failures don't break calendar ops)
# ---------------------------------------------------------------------------


def _mirror_to_sqlite(ev: Event) -> None:
    """Best-effort insert into the customer DB's appointments table.

    The customer DB owns the schema. If it doesn't exist yet (i.e. the
    customer_db tool hasn't been used), we skip silently — the calendar is
    still authoritative on disk.
    """
    if ev.customer_id is None or not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.execute(
            """
            INSERT INTO appointments
                (customer_id, starts_at, ends_at, title, notes_md,
                 status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.customer_id,
                ev.dtstart.astimezone(TZ).isoformat(),
                ev.dtend.astimezone(TZ).isoformat(),
                ev.summary,
                f"event_uid:{ev.uid}\n\n{ev.description}".strip(),
                "planned",
                datetime.now(TZ).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        # Schema mismatch / locked / missing — calendar remains the source
        # of truth, so we swallow rather than rollback the ICS write.
        pass


def _cancel_in_sqlite(event_uid: str) -> None:
    """Mark mirrored appointments as cancelled. No-op if the DB doesn't
    have the row (e.g. booking made before customer_id was attached)."""
    if not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.execute(
            "UPDATE appointments SET status='cancelled' WHERE notes_md LIKE ?",
            (f"event_uid:{event_uid}%",),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def calendar_tool(args: Dict[str, Any], **_kw: Any) -> str:
    """Hermes-facing entry point. Returns a JSON string."""
    action = (args.get("action") or "").strip()
    try:
        if action == "list_free_slots":
            result: Any = list_free_slots(
                date_iso=args["date_iso"],
                duration_min=int(args["duration_min"]),
                business_hours_start=args.get(
                    "business_hours_start", "08:00"
                ),
                business_hours_end=args.get("business_hours_end", "17:00"),
            )
        elif action == "book":
            result = book(
                starts_at_iso=args["starts_at_iso"],
                ends_at_iso=args["ends_at_iso"],
                title=args["title"],
                customer_id=args.get("customer_id"),
                notes=args.get("notes"),
            )
        elif action == "list_upcoming":
            result = list_upcoming(days_ahead=int(args.get("days_ahead", 7)))
        elif action == "cancel":
            result = cancel(event_uid=args["event_uid"])
        else:
            return json.dumps(
                {"error": f"Unknown action '{action}'"}, ensure_ascii=False
            )
    except KeyError as e:
        return json.dumps(
            {"error": f"Missing required argument: {e.args[0]}"},
            ensure_ascii=False,
        )
    except (ValueError, TypeError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    return json.dumps({"ok": True, "result": result}, ensure_ascii=False)


def check_calendar_requirements() -> bool:
    """Stdlib only — always available."""
    return True


# ---------------------------------------------------------------------------
# Hermes function-calling schema
# ---------------------------------------------------------------------------


CALENDAR_SCHEMA: Dict[str, Any] = {
    "name": "calendar",
    "description": (
        "Mein Geselle Handwerker-Kalender. Backed by a local iCal file at "
        "~/.hermes/data/handwerk.ics. All datetimes ISO-8601 in "
        "Europe/Berlin. Actions:\n"
        "- list_free_slots: free intervals on a day within business hours\n"
        "- book: create an event after a conflict check\n"
        "- list_upcoming: events in the next N days\n"
        "- cancel: remove an event by UID\n"
        "When customer_id is given to book(), the appointment is mirrored "
        "into the customer_db appointments table."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_free_slots",
                    "book",
                    "list_upcoming",
                    "cancel",
                ],
                "description": "Which calendar operation to perform.",
            },
            "date_iso": {
                "type": "string",
                "description": "YYYY-MM-DD (list_free_slots).",
            },
            "duration_min": {
                "type": "integer",
                "description": "Minimum slot duration in minutes "
                "(list_free_slots).",
            },
            "business_hours_start": {
                "type": "string",
                "description": "HH:MM, default 08:00 (list_free_slots).",
                "default": "08:00",
            },
            "business_hours_end": {
                "type": "string",
                "description": "HH:MM, default 17:00 (list_free_slots).",
                "default": "17:00",
            },
            "starts_at_iso": {
                "type": "string",
                "description": "ISO-8601 start (book).",
            },
            "ends_at_iso": {
                "type": "string",
                "description": "ISO-8601 end (book).",
            },
            "title": {
                "type": "string",
                "description": "Event summary (book).",
            },
            "customer_id": {
                "type": "integer",
                "description": "Optional customer DB id to mirror into "
                "appointments (book).",
            },
            "notes": {
                "type": "string",
                "description": "Optional free-form notes (book).",
            },
            "days_ahead": {
                "type": "integer",
                "description": "Lookahead window in days, default 7 "
                "(list_upcoming).",
                "default": 7,
            },
            "event_uid": {
                "type": "string",
                "description": "UID returned by book() (cancel).",
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
        name="calendar",
        toolset="mein_geselle",
        schema=CALENDAR_SCHEMA,
        handler=calendar_tool,
        check_fn=check_calendar_requirements,
        emoji="📅",
        max_result_size_chars=20_000,
    )
except ImportError:
    # Hermes registry isn't on the path (standalone import); module still
    # works as a plain library — see __main__ smoke test below.
    pass


__all__ = [
    "ICS_PATH",
    "Event",
    "book",
    "cancel",
    "list_free_slots",
    "list_upcoming",
    "calendar_tool",
    "CALENDAR_SCHEMA",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    # Use an isolated test path so we don't trample on the user's real
    # calendar. We rebind the module-level constant for the duration of
    # the smoke test.
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="mein-geselle-cal-"))
    ICS_PATH = tmpdir / "handwerk.ics"  # type: ignore[assignment]
    print(f"[smoke] using temp ICS at {ICS_PATH}")

    # 1. Empty calendar → one big free slot for the day.
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).date().isoformat()
    free = list_free_slots(tomorrow, duration_min=60)
    print(f"[smoke] free slots on {tomorrow} (empty cal): {len(free)} slot(s)")
    print(f"        first: {free[0]}")

    # 2. Book a Termin in the middle of the day.
    starts = f"{tomorrow}T10:00:00+02:00"
    ends = f"{tomorrow}T11:30:00+02:00"
    result = book(starts, ends, "Müller Wasserschaden", notes="Küche, OG")
    uid = result["event_uid"]
    print(f"[smoke] booked: uid={uid[:18]}...")

    # 3. Free slots should now skip the booked block.
    free_after = list_free_slots(tomorrow, duration_min=60)
    print(f"[smoke] free slots after booking: {len(free_after)} slot(s)")
    for s in free_after:
        print(f"        {s['start']} → {s['end']}")

    # 4. Conflict detection should reject overlap.
    try:
        book(
            f"{tomorrow}T10:30:00+02:00",
            f"{tomorrow}T12:00:00+02:00",
            "Konflikt-Termin",
        )
        print("[smoke] ERROR: conflict was NOT detected")
    except ValueError as e:
        print(f"[smoke] conflict correctly rejected: {e}")

    # 5. list_upcoming covers tomorrow.
    upcoming = list_upcoming(days_ahead=2)
    print(f"[smoke] upcoming events: {len(upcoming)}")
    for ev in upcoming:
        print(f"        {ev['starts_at']} — {ev['title']}")

    # 6. Cancel and verify.
    cancelled = cancel(uid)
    print(f"[smoke] cancel result: {cancelled}")
    print(f"[smoke] upcoming after cancel: {len(list_upcoming(2))}")

    # 7. Dispatch through the Hermes-facing entrypoint once.
    payload = calendar_tool(
        {
            "action": "list_free_slots",
            "date_iso": tomorrow,
            "duration_min": 30,
        }
    )
    print(f"[smoke] tool dispatch JSON: {payload[:140]}...")
