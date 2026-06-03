# PostToolUse Mid-Session Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PostToolUse hook that tracks tool-call count per session in a tmpdir state file and injects a single, stats-rich reminder into the agent's context once a work threshold is crossed — so the time-log marker lands naturally in the final response and the Stop block becomes a rare backstop.

**Architecture:** New `timelog/remind_hook.py` (plus byte-identical plugin copy) keyed off a `{count, fired, ts}` JSON state file in the OS tmpdir. Hot path = read+increment+atomic-write only. At `count >= threshold && !fired`, one transcript scan (reusing `claude_hook.scan_transcript` / `scan_session_detail` and `core` marker functions) decides: marker/SKIP already present → silent; else emit `hookSpecificOutput.additionalContext` reminder. Fail-open everywhere: any error → exit 0, no output.

**Tech Stack:** Python 3 stdlib only (json, os, re, secrets, sys, tempfile, time). pytest. Spec: `docs/superpowers/specs/2026-06-04-posttooluse-reminder-design.md`.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `timelog/remind_hook.py` | Create | Hook entry: state tracking, fire decision, reminder JSON |
| `claude-plugin/scripts/remind_hook.py` | Create (byte-identical copy) | Plugin layout |
| `tests/test_remind_hook.py` | Create | All new unit tests |
| `tests/test_plugin_sync.py` | Modify | Add remind_hook.py sync check |
| `claude-plugin/hooks/hooks.json` | Modify | Register PostToolUse hook |
| `adapters/claude-code/settings-snippet.json` | Modify | Manual-install wiring |
| `install.sh` | Modify | Copy remind_hook.py + register PostToolUse |
| `uninstall.sh` | Modify | Strip PostToolUse entry |
| `README.md` | Modify | Document knobs + behavior |
| `rules/time-tracking.md` | Modify | Knobs table + reminder mention |

Key dual-layout decision: `remind_hook.py` uses a try/except import (`from timelog import claude_hook, core` → fallback to sibling import). Unlike `claude_hook.py`, BOTH copies are byte-identical and `install.sh` copies it without patching.

---

### Task 1: State-file primitives

**Files:**
- Create: `timelog/remind_hook.py`
- Test: `tests/test_remind_hook.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_remind_hook.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remind_hook.py -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError` on `timelog.remind_hook` (collection error is the failure).

- [ ] **Step 3: Write the implementation**

Create `timelog/remind_hook.py`:

```python
import json
import os
import re
import secrets
import sys
import tempfile
import time

try:
    from timelog import claude_hook, core
except ImportError:
    # Flat install / plugin layout: claude_hook.py and core.py are siblings.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import claude_hook
    import core

# State older than this is a different working day; treat as a fresh session
# (tmpdir survives reboots on Windows, so age — not existence — is the guard).
STALE_SECONDS = 24 * 60 * 60

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")

REMINDER_TEMPLATE = (
    "agent-timelog: ~{elapsed} of work so far this session — {stats}. "
    "Remember to emit a <time-log> marker in your final response covering "
    "this work (format: YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | "
    "summary | duration)."
)


def sanitize_session_id(value):
    """Session id safe to embed in a tmpdir filename, or '' to abort.

    Alnum + hyphen only and bounded length: the id comes from hook stdin,
    and anything else risks path traversal in state_path.
    """
    if isinstance(value, str) and SESSION_ID_RE.match(value):
        return value
    return ""


def state_path(session_id):
    return os.path.join(tempfile.gettempdir(), f"timelog-remind-{session_id}.json")


def _fresh_state(now):
    return {"count": 0, "fired": False, "ts": now}


def read_state(path, now):
    """Load state; any unreadable/invalid/stale file degrades to fresh state."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        count = int(raw.get("count", 0))
        fired = bool(raw.get("fired", False))
        ts = float(raw.get("ts", 0.0))
        if count < 0 or now - ts > STALE_SECONDS:
            return _fresh_state(now)
        return {"count": count, "fired": fired, "ts": ts}
    except (OSError, ValueError, TypeError, AttributeError):
        return _fresh_state(now)


def write_state(path, state):
    """Atomic write: unique tmp (pid + nonce, so concurrent hook processes
    cannot clobber each other's tmp) then os.replace. Best-effort — failure
    is swallowed; a lost increment is acceptable, corruption is not."""
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remind_hook.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add timelog/remind_hook.py tests/test_remind_hook.py
git commit -m "feat: remind-hook state-file primitives (sanitize, read, atomic write)"
```

---

### Task 2: Suppression check + reminder composition

**Files:**
- Modify: `timelog/remind_hook.py` (append functions)
- Test: `tests/test_remind_hook.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_remind_hook.py`:

```python
import datetime


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remind_hook.py -v -k "suppress or compose"`
Expected: FAIL with `AttributeError: ... has no attribute 'has_quality_marker_or_skip'`.

- [ ] **Step 3: Write the implementation**

Append to `timelog/remind_hook.py`:

```python
def has_quality_marker_or_skip(text):
    """True when the transcript already settles the logging question.

    Mirrors the Stop hook's enforce standard: a SKIP counts, a quality
    (non-lazy canonical) marker counts, anything else does not — otherwise
    the reminder would stay silent for markers the backstop will reject.
    """
    if core.has_skip(text):
        return True
    return bool(core.filter_quality(core.extract_markers(text)))


def compose_reminder(tool_count, files, tool_counts, first_ts, last_ts):
    """One-line reminder with live session stats from the transcript scan."""
    minutes = 0
    if first_ts is not None and last_ts is not None:
        minutes = int(round((last_ts - first_ts).total_seconds() / 60))
    elapsed = core.format_duration(minutes)

    bash = sum(n for t, n in tool_counts.items() if t in core.OPS_TOOLS)
    research = sum(n for t, n in tool_counts.items() if t in core.RESEARCH_TOOLS)
    stats = core.compose_session_summary(files, bash, research, tool_count)
    if stats is None:
        stats = f"{tool_count} tool call" + ("" if tool_count == 1 else "s")

    return REMINDER_TEMPLATE.format(elapsed=elapsed, stats=stats)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remind_hook.py -v`
Expected: 15 PASS.

- [ ] **Step 5: Commit**

```bash
git add timelog/remind_hook.py tests/test_remind_hook.py
git commit -m "feat: remind-hook suppression check and stats-rich reminder text"
```

---

### Task 3: run() decision flow

**Files:**
- Modify: `timelog/remind_hook.py` (append `run`)
- Test: `tests/test_remind_hook.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_remind_hook.py`. The helper writes a minimal transcript
JSONL with N tool calls and optional marker text:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remind_hook.py -v -k run`
Expected: FAIL with `AttributeError: ... has no attribute 'run'`.

- [ ] **Step 3: Write the implementation**

Append to `timelog/remind_hook.py`:

```python
DEFAULT_THRESHOLD = 10


def _threshold():
    raw = os.environ.get("TIMELOG_REMIND_AFTER", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    return value if value > 0 else DEFAULT_THRESHOLD


def run(data, now=None):
    """Decision flow. Returns the reminder JSON string, or '' for silence.

    Hot path (the overwhelmingly common case) is read-state, increment,
    write-state, return '' — no transcript I/O. The transcript is scanned
    exactly once per session, at the fire point. `fired` is set BEFORE the
    scan so a missing transcript can never cause a retry-scan every call.
    """
    if os.environ.get("TIMELOG_REMIND", "1") == "0":
        return ""

    session_id = sanitize_session_id(data.get("session_id"))
    if not session_id:
        return ""

    if now is None:
        now = time.time()

    path = state_path(session_id)
    state = read_state(path, now)
    state["count"] += 1
    state["ts"] = now

    # >= not ==: a lost increment or restarted counter can never skip the trigger.
    if state["fired"] or state["count"] < _threshold():
        write_state(path, state)
        return ""

    state["fired"] = True
    write_state(path, state)

    transcript_path = data.get("transcript_path", "")
    text, tool_count, first_ts, last_ts = claude_hook.scan_transcript(transcript_path)
    if tool_count == 0 and not text:
        return ""  # missing/empty transcript; flag stays set, stay silent

    if has_quality_marker_or_skip(text):
        return ""

    project_dir = claude_hook.resolve_workspace(data.get("cwd", ""))
    tool_counts, files, _subdirs = claude_hook.scan_session_detail(
        transcript_path, project_dir
    )
    message = compose_reminder(tool_count, files, tool_counts, first_ts, last_ts)
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": message,
            }
        }
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remind_hook.py -v`
Expected: 23 PASS.

- [ ] **Step 5: Commit**

```bash
git add timelog/remind_hook.py tests/test_remind_hook.py
git commit -m "feat: remind-hook run() decision flow — single-fire threshold reminder"
```

---

### Task 4: main() entry + fail-open subprocess tests

**Files:**
- Modify: `timelog/remind_hook.py` (append `main`)
- Test: `tests/test_remind_hook.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_remind_hook.py`:

```python
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "timelog", "remind_hook.py")


def _run_hook(stdin_text, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, HOOK],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_main_no_stdin_exits_zero_silent():
    r = _run_hook("")
    assert r.returncode == 0
    assert r.stdout == ""


def test_main_bad_json_exits_zero_silent():
    r = _run_hook("{not json")
    assert r.returncode == 0
    assert r.stdout == ""


def test_main_minimal_payload_exits_zero(tmp_path):
    payload = json.dumps({"session_id": "subproc-test-1",
                          "transcript_path": str(tmp_path / "none.jsonl"),
                          "cwd": str(tmp_path)})
    r = _run_hook(payload, env_extra={"TIMELOG_REMIND_AFTER": "999"})
    assert r.returncode == 0
    assert r.stdout == ""
```

- [ ] **Step 2: Verify main is missing**

The subprocess tests pass vacuously without a `__main__` guard (script runs,
defines functions, exits 0). Verify the real gap directly:

Run: `python -c "from timelog import remind_hook; remind_hook.main"`
Expected: `AttributeError: module 'timelog.remind_hook' has no attribute 'main'`

- [ ] **Step 3: Write the implementation**

Append to `timelog/remind_hook.py`:

```python
def main():
    """Hook entry: read stdin JSON, run, print reminder if any. Always exit 0."""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    try:
        out = run(data)
        if out:
            print(out)
    except Exception:
        # Fail-open: the reminder must never break tool execution.
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full module test file**

Run: `python -m pytest tests/test_remind_hook.py -v`
Expected: 26 PASS.

Also: `python -c "from timelog import remind_hook; remind_hook.main"` → no error.

- [ ] **Step 5: Commit**

```bash
git add timelog/remind_hook.py tests/test_remind_hook.py
git commit -m "feat: remind-hook main() entry — fail-open, always exit 0"
```

---

### Task 5: Plugin copy + sync test

**Files:**
- Create: `claude-plugin/scripts/remind_hook.py`
- Modify: `tests/test_plugin_sync.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_plugin_sync.py`, replace the module docstring with:

```python
"""
Test that plugin copies are byte-identical to their timelog/ sources.

The plugin keeps its own copies so it can be installed standalone.
claude_hook.py is intentionally patched (sibling import), so it is not checked.
core.py and remind_hook.py must be byte-identical.
"""
```

and append:

```python
def test_plugin_remind_hook_matches_source():
    # remind_hook.py uses a try/except dual-layout import, so BOTH copies are
    # byte-identical (unlike claude_hook.py, which is intentionally patched).
    assert filecmp.cmp(
        os.path.join(REPO, "timelog/remind_hook.py"),
        os.path.join(REPO, "claude-plugin/scripts/remind_hook.py"),
        shallow=False,
    ), "claude-plugin/scripts/remind_hook.py drifted from timelog/remind_hook.py — re-copy to sync"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plugin_sync.py -v`
Expected: `test_plugin_remind_hook_matches_source` FAIL (file missing).

- [ ] **Step 3: Copy the file**

```bash
cp timelog/remind_hook.py claude-plugin/scripts/remind_hook.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plugin_sync.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add claude-plugin/scripts/remind_hook.py tests/test_plugin_sync.py
git commit -m "feat: sync remind_hook.py into claude-plugin (byte-identical copy)"
```

---

### Task 6: Hook wiring — plugin, snippet, install, uninstall

**Files:**
- Modify: `claude-plugin/hooks/hooks.json`
- Modify: `adapters/claude-code/settings-snippet.json`
- Modify: `install.sh`
- Modify: `uninstall.sh`

No unit tests for shell wiring; verified by JSON validity check + syntax check below.

- [ ] **Step 1: Add PostToolUse to `claude-plugin/hooks/hooks.json`**

Full new content:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py\""
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py\""
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py\""
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/remind_hook.py\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Add PostToolUse to `adapters/claude-code/settings-snippet.json`**

Full new content:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 \"$HOME/.claude/hooks/timelog/claude_hook.py\"" } ] }
    ],
    "PreCompact": [
      { "hooks": [ { "type": "command", "command": "python3 \"$HOME/.claude/hooks/timelog/claude_hook.py\"" } ] }
    ],
    "SubagentStop": [
      { "hooks": [ { "type": "command", "command": "python3 \"$HOME/.claude/hooks/timelog/claude_hook.py\"" } ] }
    ],
    "PostToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "python3 \"$HOME/.claude/hooks/timelog/remind_hook.py\"", "timeout": 10 } ] }
    ]
  }
}
```

- [ ] **Step 3: Update `install.sh`**

3a. After the `chmod +x "$HOOK_DIR/claude_hook.py"` line, add:

```bash
# remind_hook.py needs no import patch — it carries a try/except dual-layout
# import that falls back to sibling imports on its own.
cp "$SRC/timelog/remind_hook.py" "$HOOK_DIR/remind_hook.py"
chmod +x "$HOOK_DIR/remind_hook.py"
```

3b. Replace the settings-merge section (currently `HOOK_CMD=...` through the
end of that `PY` heredoc) with a version that registers the PostToolUse
reminder too:

```bash
# 3. settings.json hook merge (idempotent — skips if entry already present)
HOOK_CMD="python3 \"$HOOK_DIR/claude_hook.py\""
REMIND_CMD="python3 \"$HOOK_DIR/remind_hook.py\""
python3 - "$SETTINGS" "$HOOK_CMD" "$REMIND_CMD" <<'PY'
import json, os, sys

settings_path, hook_cmd, remind_cmd = sys.argv[1], sys.argv[2], sys.argv[3]
data = {}
if os.path.exists(settings_path):
    with open(settings_path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = {}

hooks = data.setdefault("hooks", {})

def ensure(event, cmd, matcher=None):
    arr = hooks.setdefault(event, [])
    for block in arr:
        for h in block.get("hooks", []):
            if h.get("command") == cmd:
                print(f"  {event}: already registered — skipping")
                return
    block = {"hooks": [{"type": "command", "command": cmd}]}
    if matcher is not None:
        block["matcher"] = matcher
    arr.append(block)
    print(f"  {event}: registered")

ensure("Stop", hook_cmd)
ensure("PreCompact", hook_cmd)
ensure("SubagentStop", hook_cmd)
ensure("PostToolUse", remind_cmd, matcher="*")

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
```

- [ ] **Step 4: Update `uninstall.sh`**

Change:

```python
for event in ("Stop", "PreCompact", "SubagentStop"):
```

to:

```python
for event in ("Stop", "PreCompact", "SubagentStop", "PostToolUse"):
```

(The filter matches on `hook_dir in command`, which already covers
`remind_hook.py` since it lives in the same directory; `rm -rf "$HOOK_DIR"`
already removes the script file.)

- [ ] **Step 5: Validate JSON + shell syntax**

Run:
```bash
python -m json.tool claude-plugin/hooks/hooks.json > /dev/null && echo hooks-ok
python -m json.tool adapters/claude-code/settings-snippet.json > /dev/null && echo snippet-ok
bash -n install.sh && echo install-ok
bash -n uninstall.sh && echo uninstall-ok
```
Expected: `hooks-ok`, `snippet-ok`, `install-ok`, `uninstall-ok`.

- [ ] **Step 6: Commit**

```bash
git add claude-plugin/hooks/hooks.json adapters/claude-code/settings-snippet.json install.sh uninstall.sh
git commit -m "feat: wire PostToolUse reminder hook into plugin, snippet, installer"
```

---

### Task 7: Docs

**Files:**
- Modify: `README.md`
- Modify: `rules/time-tracking.md`

- [ ] **Step 1: README knobs table**

In the README's knobs/environment-variables table (search for `TIMELOG_ENFORCE`), add two rows after the `TIMELOG_ENFORCE` row:

```markdown
| `TIMELOG_REMIND` | `1` | Mid-session reminder (PostToolUse). Once a session crosses `TIMELOG_REMIND_AFTER` tool calls with no quality marker in the transcript, the hook injects ONE context reminder — with live session stats — to emit a `<time-log>` marker in the final response. Set `0` to disable. |
| `TIMELOG_REMIND_AFTER` | `10` | Tool calls before the one-shot reminder fires. |
```

Also add a short subsection after the enforce-mode section (match the README's existing heading level and tone):

```markdown
### Mid-session reminder (PostToolUse)

Enforcement is reactive — it blocks at session end. The reminder is the
proactive half: a `PostToolUse` hook counts tool calls in a tmpdir state file
(`timelog-remind-<session_id>.json`) and, once past the threshold, scans the
transcript once. If no quality marker or SKIP exists yet, it injects a single
`additionalContext` reminder with live stats (elapsed time, files edited,
commands run). One reminder per session, fail-open, never blocks tools.
Steady state: the marker lands in the final response and the Stop block never
fires.
```

- [ ] **Step 2: rules/time-tracking.md**

In the knobs table, add the same two rows (`TIMELOG_REMIND`, `TIMELOG_REMIND_AFTER`). In the "What the hook does" (or equivalent) section, append one sentence:

```markdown
Mid-session, a PostToolUse hook may inject ONE reminder (with live session
stats) once your tool-call count passes `TIMELOG_REMIND_AFTER` and no quality
marker exists yet — respond to it by emitting the marker in your final
response as usual.
```

- [ ] **Step 3: Sanity-check the rule file renders**

`install.sh` injects `rules/time-tracking.md` into `~/.claude/CLAUDE.md` between
marker comments on reinstall — no extra wiring needed.

Run: `python -c "print(open('rules/time-tracking.md', encoding='utf-8').read()[:400])"`

- [ ] **Step 4: Commit**

```bash
git add README.md rules/time-tracking.md
git commit -m "docs: document TIMELOG_REMIND / TIMELOG_REMIND_AFTER and reminder behavior"
```

---

### Task 8: Full verification

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -v`
Expected: ALL PASS (existing suite + 26 new).

- [ ] **Step 2: End-to-end smoke test of the hook script**

```bash
python - <<'EOF'
import json, subprocess, sys, tempfile, os
td = tempfile.mkdtemp()
t = os.path.join(td, "t.jsonl")
row = {"type": "assistant", "timestamp": "2026-06-04T09:00:00.000Z",
       "message": {"content": [{"type": "text", "text": "work"}] +
                   [{"type": "tool_use", "name": "Bash"}] * 5}}
open(t, "w", encoding="utf-8").write(json.dumps(row))
payload = json.dumps({"session_id": "smoke-1", "transcript_path": t, "cwd": td})
env = dict(os.environ, TIMELOG_REMIND_AFTER="2")
for i in range(3):
    r = subprocess.run([sys.executable, "timelog/remind_hook.py"],
                       input=payload, capture_output=True, text=True, env=env)
    print(f"call {i+1}: rc={r.returncode} stdout={r.stdout.strip()[:120]}")
EOF
```

Expected: call 1 silent, call 2 prints `hookSpecificOutput` JSON containing
`additionalContext`, call 3 silent (single-fire). All rc=0.

- [ ] **Step 3: Confirm clean tree**

```bash
git status --short
```
Expected: clean tree; all work committed across Tasks 1-7.

---

## Self-review notes

- **Spec coverage:** state primitives (Task 1), suppression + stats message (Task 2), threshold/single-fire/fail-open decision flow (Task 3), entry point (Task 4), plugin sync (Task 5), wiring incl. installer/uninstaller (Task 6), docs (Task 7), E2E (Task 8). Spec's error-handling table is covered by tests in Tasks 1, 3, 4.
- **Types consistent:** `run(data, now=None) -> str`; state dict `{count: int, fired: bool, ts: float}` everywhere; `compose_reminder(tool_count, files, tool_counts, first_ts, last_ts)` matches call site.
- **No placeholders:** every step carries full code/commands.
