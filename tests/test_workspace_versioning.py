import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import _merge_versioned_state, _normalize_versioned_state


def test_merge_append_only_title_keeps_both_user_versions():
    current = {
        "title": {"versions": ["T1", "T2-user1"], "current_index": 1},
        "description": {"versions": [], "current_index": -1},
        "faq_lines": [],
        "card_lines": [],
        "prices": {},
    }
    incoming = {
        "title": {"versions": ["T1", "T2-user2"], "current_index": 1},
        "description": {"versions": [], "current_index": -1},
        "faq_lines": [],
        "card_lines": [],
        "prices": {},
    }

    merged = _merge_versioned_state(current, incoming)
    assert merged["title"]["versions"] == ["T1", "T2-user1", "T2-user2"]
    assert merged["title"]["current_index"] == 2


def test_merge_faq_lines_preserves_history_and_latest_pointer():
    current = {
        "title": {"versions": [], "current_index": -1},
        "description": {"versions": [], "current_index": -1},
        "faq_lines": [
            {
                "approved": True,
                "versions": [{"q": "Q1", "a": "A1"}, {"q": "Q2-u1", "a": "A2-u1"}],
                "current_index": 1,
            }
        ],
        "card_lines": [],
        "prices": {},
    }
    incoming = {
        "title": {"versions": [], "current_index": -1},
        "description": {"versions": [], "current_index": -1},
        "faq_lines": [
            {
                "approved": False,
                "versions": [{"q": "Q1", "a": "A1"}, {"q": "Q2-u2", "a": "A2-u2"}],
                "current_index": 1,
            }
        ],
        "card_lines": [],
        "prices": {},
    }

    merged = _merge_versioned_state(current, incoming)
    assert merged["faq_lines"][0]["versions"] == [
        {"q": "Q1", "a": "A1"},
        {"q": "Q2-u1", "a": "A2-u1"},
        {"q": "Q2-u2", "a": "A2-u2"},
    ]
    assert merged["faq_lines"][0]["current_index"] == 2
    assert merged["faq_lines"][0]["approved"] is False


def test_normalize_state_clamps_indexes_and_drops_prices():
    raw = {
        "title": {"versions": ["A", "B"], "current_index": 99},
        "description": {"versions": ["D"], "current_index": -1},
        "faq_lines": [],
        "card_lines": [],
        "prices": {
            "aggressive_min": {
                "versions": [{"price": "15.9", "metrics": {"margin_percent": "12.3"}}],
                "current_index": 4,
            }
        },
    }

    normalized = _normalize_versioned_state(raw)
    assert normalized["title"]["current_index"] == 1
    assert normalized["description"]["current_index"] == 0
    assert normalized["prices"] == {}
