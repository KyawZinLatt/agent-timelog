import json
import os

from timelog import remind_hook


# --- sanitize_session_id ---

def test_sanitize_accepts_uuid_like():
    assert remind_hook.sanitize_session_id("abc-123-DEF") == "abc-123-DEF"


def test_sanitize_rejects_traversal():
    assert remind_hook.sanitize_session_id("../evil") == ""


def test_sanitize_rejects_empty_and_none():
    assert remind_hook.sanitize_session_id("") == ""
    assert remind_hook.sanitize_session_id(None) == ""


def test_sanitize_rejects_oversized():
    assert remind_hook.sanitize_session_id("a" * 129) == ""


# --- state read/write ---

def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(remind_hook.tempfile, "gettempdir", lambda: str(tmp_path))
    path = remind_hook.state_path("s1")
    remind_hook.write_state(path, {"count": 3, "fired": False, "ts": 1000.0})
    assert remind_hook.read_state(path, now=1000.0) == {
        "count": 3, "fired": False, "ts": 1000.0
    }


def test_state_missing_file_is_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(remind_hook.tempfile, "gettempdir", lambda: str(tmp_path))
    path = remind_hook.state_path("nope")
    assert remind_hook.read_state(path, now=50.0) == {
        "count": 0, "fired": False, "ts": 50.0
    }


def test_state_corrupt_file_is_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(remind_hook.tempfile, "gettempdir", lambda: str(tmp_path))
    path = remind_hook.state_path("bad")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert remind_hook.read_state(path, now=50.0) == {
        "count": 0, "fired": False, "ts": 50.0
    }


def test_state_stale_is_discarded(tmp_path, monkeypatch):
    monkeypatch.setattr(remind_hook.tempfile, "gettempdir", lambda: str(tmp_path))
    path = remind_hook.state_path("old")
    remind_hook.write_state(path, {"count": 9, "fired": True, "ts": 0.0})
    now = remind_hook.STALE_SECONDS + 1.0
    assert remind_hook.read_state(path, now=now) == {
        "count": 0, "fired": False, "ts": now
    }


def test_write_state_leaves_no_tmp_litter(tmp_path, monkeypatch):
    monkeypatch.setattr(remind_hook.tempfile, "gettempdir", lambda: str(tmp_path))
    path = remind_hook.state_path("s2")
    remind_hook.write_state(path, {"count": 1, "fired": False, "ts": 1.0})
    leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    assert leftovers == []
