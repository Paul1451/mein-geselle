"""Tests for tool_calendar — RFC-5545 + Europe/Berlin + conflict detection."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import tool_calendar as cal

TZ = ZoneInfo("Europe/Berlin")


def _future_day_iso(days: int = 2) -> str:
    return (datetime.now(TZ) + timedelta(days=days)).date().isoformat()


def test_list_free_slots_empty_calendar_returns_one_big_slot(tmp_ical):
    day = _future_day_iso()
    slots = cal.list_free_slots(day, duration_min=60)
    assert len(slots) == 1
    assert slots[0]["start"].startswith(f"{day}T08:00")
    assert slots[0]["end"].startswith(f"{day}T17:00")


def test_book_non_conflicting_returns_uid(tmp_ical):
    day = _future_day_iso()
    out = cal.book(f"{day}T10:00:00+02:00", f"{day}T11:00:00+02:00", "Maler")
    assert "event_uid" in out
    assert out["event_uid"].endswith("@mein-geselle")


def test_book_conflicting_raises(tmp_ical):
    day = _future_day_iso()
    cal.book(f"{day}T10:00:00+02:00", f"{day}T11:00:00+02:00", "First")
    with pytest.raises(ValueError, match="Conflict"):
        cal.book(f"{day}T10:30:00+02:00", f"{day}T12:00:00+02:00", "Second")


def test_cancel_removes_event(tmp_ical):
    day = _future_day_iso()
    uid = cal.book(f"{day}T09:00:00+02:00", f"{day}T10:00:00+02:00", "X")["event_uid"]
    assert cal.cancel(uid) == {"cancelled": True}
    titles = [e["title"] for e in cal.list_upcoming(days_ahead=5)]
    assert "X" not in titles


def test_list_free_slots_respects_business_hours(tmp_ical):
    day = _future_day_iso()
    slots = cal.list_free_slots(day, duration_min=30,
                                business_hours_start="09:00",
                                business_hours_end="12:00")
    assert slots[0]["start"].endswith("09:00:00+01:00") or \
        slots[0]["start"].endswith("09:00:00+02:00")
    assert "12:00:00" in slots[0]["end"]


def test_disk_format_is_utc_api_boundary_is_berlin(tmp_ical):
    day = _future_day_iso()
    uid = cal.book(f"{day}T10:00:00+02:00", f"{day}T11:00:00+02:00", "Z")["event_uid"]
    raw = tmp_ical.read_text(encoding="utf-8")
    assert "DTSTART:" in raw and raw.split("DTSTART:")[1][:16].endswith("Z")
    upcoming = cal.list_upcoming(days_ahead=5)
    matching = [e for e in upcoming if e["event_uid"] == uid][0]
    assert "+0" in matching["starts_at"]  # Berlin tz offset in API output


def test_round_trip_book_then_list_upcoming(tmp_ical):
    day = _future_day_iso(days=1)
    uid = cal.book(f"{day}T14:00:00+02:00", f"{day}T15:00:00+02:00", "Tour")["event_uid"]
    uids = [e["event_uid"] for e in cal.list_upcoming(days_ahead=3)]
    assert uid in uids


def test_malformed_line_skipped_does_not_crash(tmp_ical):
    tmp_ical.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:bad\r\nDTSTART:not-a-date\r\nDTEND:also-bad\r\n"
        "SUMMARY:Broken\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n",
        encoding="utf-8",
    )
    # Should not raise — malformed VEVENT silently skipped.
    out = cal.list_upcoming(days_ahead=30)
    assert out == []
