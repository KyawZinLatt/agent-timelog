import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core

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
    # Legacy .time-log.md files written by the pre-UTF-8 hook are cp1252-encoded
    # (en-dash 0x96, middle-dot 0xB7). Read UTF-8 first, fall back to cp1252 so a
    # legacy file never crashes the hook (which must always exit 0).
    existing = set()
    for enc in ("utf-8", "cp1252"):
        try:
            with open(log_file, encoding=enc) as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#") and "|" in s:
                        existing.add(s)
            return existing
        except UnicodeDecodeError:
            existing.clear()
            continue
        except OSError:
            return existing
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


def synthesize_entry(event, tool_count, project_dir, first_ts, last_ts,
                     tool_counts=None, files=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = first_ts or now
    end = last_ts or start
    minutes = int(round((end - start).total_seconds() / 60))
    scope = core.sanitize_token(os.path.basename(project_dir.rstrip("/")), "session")

    # Activity-aware fallback: infer category + summary from tool use. When the
    # session's tools aren't recognizable, fall back to the generic auto line.
    category = "auto"
    summary = f"auto-logged {event}, {tool_count} tool calls"
    if tool_counts:
        bash_count = sum(n for t, n in tool_counts.items() if t in core.OPS_TOOLS)
        research_count = sum(n for t, n in tool_counts.items() if t in core.RESEARCH_TOOLS)
        total = sum(tool_counts.values())
        composed = core.compose_session_summary(files or [], bash_count, research_count, total)
        if composed is not None:
            category = core.infer_category(tool_counts)
            summary = composed

    return core.build_entry(
        start.strftime("%Y-%m-%d"),
        start.strftime("%H:%M"),
        end.strftime("%H:%M"),
        category,
        scope,
        summary,
        core.format_duration(minutes),
    )


def scan_session_detail(transcript_path):
    """Tool-name histogram + edited file basenames for the main Stop synthesis.

    Reads only assistant tool_use blocks. File basenames come from the input
    file_path of write tools, dedup-ordered by first appearance.
    """
    tool_counts = {}
    files = []
    seen = set()
    if not transcript_path or not os.path.exists(transcript_path):
        return tool_counts, files
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("type") != "assistant":
                    continue
                content = msg.get("message", {}).get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    name = c.get("name", "")
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                    if name in core.WRITE_TOOLS:
                        inp = c.get("input") or {}
                        path = inp.get("file_path") or inp.get("notebook_path") or ""
                        base = os.path.basename(path) if path else ""
                        if base and base not in seen:
                            seen.add(base)
                            files.append(base)
    except OSError:
        return {}, []
    return tool_counts, files


def scan_subagent_detail(transcript_path):
    """Extract the dispatch prompt (first user row) and a tool-name histogram.

    Used only on the SubagentStop path to compose a meaningful summary.
    """
    dispatch = ""
    tool_counts = {}
    if not transcript_path or not os.path.exists(transcript_path):
        return dispatch, tool_counts
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                mtype = msg.get("type")
                content = msg.get("message", {}).get("content")
                if mtype == "user" and not dispatch:
                    if isinstance(content, str):
                        dispatch = content
                    elif isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                dispatch = c.get("text", "")
                                break
                elif mtype == "assistant" and isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            name = c.get("name", "")
                            tool_counts[name] = tool_counts.get(name, 0) + 1
    except OSError:
        return "", {}
    return dispatch, tool_counts


def synthesize_subagent_entry(agent_type, dispatch, tool_counts, first_ts, last_ts):
    now = datetime.datetime.now(datetime.timezone.utc)
    start = first_ts or now
    end = last_ts or start
    minutes = int(round((end - start).total_seconds() / 60))
    tool_total = sum(tool_counts.values())
    category = core.infer_category(tool_counts)
    summary = core.compose_subagent_summary(agent_type, dispatch, tool_total)
    return core.build_entry(
        start.strftime("%Y-%m-%d"),
        start.strftime("%H:%M"),
        end.strftime("%H:%M"),
        category,
        "subagent",
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
        entry = None
        agent_type = data.get("agent_type", "")
        if event == "SubagentStop" and agent_type:
            dispatch, tool_counts = scan_subagent_detail(transcript_path)
            entry = synthesize_subagent_entry(
                agent_type, dispatch, tool_counts, first_ts, last_ts
            )
            if not core.is_valid_entry(entry):
                entry = None
        if entry is None:
            tool_counts, files = scan_session_detail(transcript_path)
            entry = synthesize_entry(
                event, tool_count, project_dir, first_ts, last_ts, tool_counts, files
            )
        if core.is_valid_entry(entry) and entry not in existing:
            _append([entry])

    if written:
        emit_summary(written)

    sys.exit(0)


if __name__ == "__main__":
    main()
