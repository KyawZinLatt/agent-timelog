import datetime
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


# --- suppression ---

def test_quality_marker_suppresses():
    text = ("done <time-log>2026-06-04 09:00Z–09:30Z | feature · backend | "
            "added retry logic to uploader | 30m</time-log>")
    assert remind_hook.has_quality_marker_or_skip(text) is True


def test_skip_suppresses():
    assert remind_hook.has_quality_marker_or_skip(
        "<time-log>SKIP: monitoring tick</time-log>") is True


def test_lazy_marker_does_not_suppress():
    # Lazy summaries are treated as absent by the Stop hook; the reminder
    # must apply the same standard or the backstop will still block.
    text = ("<time-log>2026-06-04 09:00Z–09:30Z | auto · ws | "
            "auto-logged Stop, 5 tool calls | 30m</time-log>")
    assert remind_hook.has_quality_marker_or_skip(text) is False


def test_plain_text_does_not_suppress():
    assert remind_hook.has_quality_marker_or_skip("no markers here") is False


# --- composition ---

def _ts(minute):
    return datetime.datetime(2026, 6, 4, 9, minute, tzinfo=datetime.timezone.utc)


def test_compose_reminder_with_files():
    msg = remind_hook.compose_reminder(
        tool_count=12,
        files=["core.py", "remind_hook.py"],
        tool_counts={"Edit": 4, "Bash": 3, "Read": 5},
        first_ts=_ts(0),
        last_ts=_ts(23),
    )
    assert "~23m" in msg
    assert "edited core.py, remind_hook.py" in msg
    assert "<time-log>" in msg


def test_compose_reminder_falls_back_to_tool_count():
    msg = remind_hook.compose_reminder(
        tool_count=7, files=[], tool_counts={}, first_ts=None, last_ts=None,
    )
    assert "7 tool calls" in msg
    assert "<time-log>" in msg


# --- run() decision flow ---


def _write_transcript(path, tool_calls, text="working"):
    content = [{"type": "text", "text": text}]
    content += [{"type": "tool_use", "name": "Bash"}] * tool_calls
    row = {
        "type": "assistant",
        "timestamp": "2026-06-04T09:00:00.000Z",
        "message": {"content": content},
    }
    path.write_text(json.dumps(row), encoding="utf-8")


def _payload(tmp_path, transcript):
    return {
        "session_id": "sess-1",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
    }


def _setup(tmp_path, monkeypatch, threshold="3"):
    monkeypatch.setattr(remind_hook.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("TIMELOG_REMIND_AFTER", threshold)
    monkeypatch.delenv("TIMELOG_REMIND", raising=False)


def test_run_silent_below_threshold(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    t = tmp_path / "t.jsonl"
    _write_transcript(t, 5)
    p = _payload(tmp_path, t)
    assert remind_hook.run(p, now=100.0) == ""   # count 1
    assert remind_hook.run(p, now=101.0) == ""   # count 2


def test_run_fires_at_threshold_with_stats(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    t = tmp_path / "t.jsonl"
    _write_transcript(t, 5)
    p = _payload(tmp_path, t)
    remind_hook.run(p, now=100.0)
    remind_hook.run(p, now=101.0)
    out = remind_hook.run(p, now=102.0)          # count 3 == threshold
    parsed = json.loads(out)
    ctx = parsed["hookSpecificOutput"]["additionalContext"]
    assert parsed["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "agent-timelog" in ctx
    assert "<time-log>" in ctx


def test_run_single_fire(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    t = tmp_path / "t.jsonl"
    _write_transcript(t, 5)
    p = _payload(tmp_path, t)
    for now in (1.0, 2.0, 3.0):
        remind_hook.run(p, now=now)
    assert remind_hook.run(p, now=4.0) == ""     # count 4: already fired
    assert remind_hook.run(p, now=5.0) == ""     # count 5: still silent


def test_run_marker_present_suppresses_but_marks_fired(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, threshold="1")
    t = tmp_path / "t.jsonl"
    _write_transcript(
        t, 2,
        text=("<time-log>2026-06-04 09:00Z–09:30Z | feature · backend | "
              "added retry logic to uploader | 30m</time-log>"),
    )
    p = _payload(tmp_path, t)
    assert remind_hook.run(p, now=1.0) == ""
    state = remind_hook.read_state(remind_hook.state_path("sess-1"), now=2.0)
    assert state["fired"] is True


def test_run_missing_transcript_fires_flag_silently(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, threshold="1")
    p = _payload(tmp_path, tmp_path / "missing.jsonl")
    assert remind_hook.run(p, now=1.0) == ""
    state = remind_hook.read_state(remind_hook.state_path("sess-1"), now=2.0)
    assert state["fired"] is True


def test_run_disabled_by_env(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, threshold="1")
    monkeypatch.setenv("TIMELOG_REMIND", "0")
    t = tmp_path / "t.jsonl"
    _write_transcript(t, 5)
    assert remind_hook.run(_payload(tmp_path, t), now=1.0) == ""
    # Disabled means no state tracking at all.
    assert not os.path.exists(remind_hook.state_path("sess-1"))


def test_run_bad_session_id_silent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, threshold="1")
    t = tmp_path / "t.jsonl"
    _write_transcript(t, 5)
    p = _payload(tmp_path, t)
    p["session_id"] = "../evil"
    assert remind_hook.run(p, now=1.0) == ""


def test_run_garbage_threshold_uses_default(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, threshold="banana")
    t = tmp_path / "t.jsonl"
    _write_transcript(t, 5)
    p = _payload(tmp_path, t)
    assert remind_hook.run(p, now=1.0) == ""    # default 10, count 1: silent
