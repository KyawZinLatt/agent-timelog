import datetime
import json
import os
import sys

from timelog import core

LOG_FILENAME = ".time-log.md"

LOG_HEADER = """# Time log

Auto-appended by Stop + PreCompact hooks (agent-timelog).
Validates strict canonical format; prose containing the tag pair is silently dropped.

## Format

`YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration`

- Times: UTC, `Z` suffix, en-dash `–` (U+2013) between times
- Category / scope: lowercase letters + hyphens; separator is middle-dot `·` (U+00B7)
- Duration: `Nm` · `Nh` · `Nh Nm`
- Summary MUST NOT contain ` | ` (space-pipe-space)

---

## Entries

"""

MIN_WORK_THRESHOLD = int(os.environ.get("TIMELOG_MIN_TOOLS", "1"))
SYNTHESIZE = os.environ.get("TIMELOG_SYNTHESIZE", "1") != "0"


def resolve_workspace(cwd_from_input):
    candidate = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if candidate and os.path.isdir(candidate):
        return candidate
    if cwd_from_input and os.path.isdir(cwd_from_input):
        return cwd_from_input
    return ""


def read_existing_entries(log_file):
    existing = set()
    try:
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and "|" in s:
                    existing.add(s)
    except OSError:
        pass
    return existing


def ensure_log_file(path):
    if os.path.exists(path):
        return True
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(LOG_HEADER)
        return True
    except OSError:
        return False


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def scan_transcript(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return "", 0, None, None
    parts = []
    tool_count = 0
    first_ts = None
    last_ts = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                ts = _parse_ts(msg.get("timestamp"))
                if ts is not None:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
                if msg.get("type") != "assistant":
                    continue
                content = msg.get("message", {}).get("content", [])
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "tool_use":
                        tool_count += 1
    except OSError:
        return "", 0, None, None
    return "\n".join(parts), tool_count, first_ts, last_ts


def synthesize_entry(event, tool_count, project_dir, first_ts, last_ts):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = first_ts or now
    end = last_ts or start
    minutes = int(round((end - start).total_seconds() / 60))
    scope = core.sanitize_token(os.path.basename(project_dir.rstrip("/")), "session")
    summary = f"auto-logged {event}, {tool_count} tool calls"
    return core.build_entry(
        start.strftime("%Y-%m-%d"),
        start.strftime("%H:%M"),
        end.strftime("%H:%M"),
        "auto",
        scope,
        summary,
        core.format_duration(minutes),
    )


def emit_summary(written):
    """Echo written entries back to the user via the Stop-hook systemMessage channel."""
    n = len(written)
    label = "entry" if n == 1 else "entries"
    body = "\n".join(written)
    msg = f"⏱ logged {n} time-log {label} to {LOG_FILENAME}:\n{body}"
    try:
        print(json.dumps({"suppressOutput": True, "systemMessage": msg}))
    except (OSError, ValueError):
        pass


def main():
    """Hook entry point: read stdin JSON, scan transcript, log emitted markers, else synthesize. Never blocks."""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)

    transcript_path = data.get("transcript_path", "")
    event = data.get("hook_event_name", "unknown")
    cwd = data.get("cwd", "")

    project_dir = resolve_workspace(cwd)
    if not project_dir:
        sys.exit(0)

    log_file = os.path.join(project_dir, LOG_FILENAME)
    if not ensure_log_file(log_file):
        sys.exit(0)

    existing = read_existing_entries(log_file)
    text, tool_count, first_ts, last_ts = scan_transcript(transcript_path)

    candidates = core.extract_markers(text)
    valid, new = core.select_new_entries(candidates, existing)

    written = []

    def _append(lines):
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                for ln in lines:
                    f.write(ln + "\n")
            written.extend(lines)
        except OSError:
            pass

    if new:
        _append(new)

    if (
        not valid
        and not core.has_skip(text)
        and tool_count >= MIN_WORK_THRESHOLD
        and SYNTHESIZE
    ):
        entry = synthesize_entry(event, tool_count, project_dir, first_ts, last_ts)
        if core.is_valid_entry(entry) and entry not in existing:
            _append([entry])

    if written:
        emit_summary(written)

    sys.exit(0)


if __name__ == "__main__":
    main()
