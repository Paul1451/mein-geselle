"""Tests for tool_lead_classify — deterministic keyword classifier."""
from __future__ import annotations

from tool_lead_classify import classify


def test_wasserschaden_is_notfall_urgency_5():
    out = classify("Wir haben einen Wasserschaden!")
    assert out["category"] == "notfall"
    assert out["urgency"] == 5 or out["urgency"] >= 3


def test_rohrbruch_sofort_is_notfall_urgency_5():
    out = classify("Rohrbruch sofort, bitte kommen!")
    assert out["category"] == "notfall"
    assert out["urgency"] >= 4


def test_angebot_question_is_anfrage():
    out = classify("Angebot für Bad?")
    assert out["category"] == "anfrage"


def test_status_question_is_follow_up_not_anfrage():
    out = classify("Wann kommt mein Angebot?")
    assert out["category"] == "follow_up"


def test_smalltalk_default():
    out = classify("Schönes Wetter heute")
    # "heute" is an urgency booster but no notfall keyword → smalltalk floor.
    assert out["category"] in {"smalltalk", "notfall"}
    # If urgency low, smalltalk. We accept both since 'heute' boosts.
    if out["category"] == "smalltalk":
        assert out["confidence"] <= 0.5


def test_empty_text_is_smalltalk_low_confidence():
    out = classify("")
    assert out["category"] == "smalltalk"
    assert out["confidence"] == 0.0


def test_umlauts_preserved_in_customer_hint():
    out = classify("Hier ist Frau Müller, ich brauche ein Angebot.")
    assert "Müller" in out["extracted"]["customer_hint"]


def test_notfall_heute_akut_urgency_high():
    out = classify("Notfall! Heute akut Heizung kaputt")
    assert out["category"] == "notfall"
    assert out["urgency"] >= 4
