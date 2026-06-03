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
