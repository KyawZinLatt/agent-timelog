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


def resolve_workspace(cwd_from_input):
    candidate = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if candidate and os.path.isdir(candidate):
        return candidate
    if cwd_from_input and os.path.isdir(cwd_from_input):
        return cwd_from_input
    return ""


def scan_transcript(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return "", 0
    parts = []
    tool_count = 0
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
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
        return "", 0
    return "\n".join(parts), tool_count


MIN_WORK_THRESHOLD = int(os.environ.get("TIMELOG_MIN_TOOLS", "5"))
ENFORCE = os.environ.get("TIMELOG_ENFORCE", "1") != "0"


def read_existing_entries(log_file):
    existing = set()
    try:
        with open(log_file) as f:
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
        with open(path, "w") as f:
            f.write(LOG_HEADER)
        return True
    except OSError:
        return False


def fail_block(tool_count, event, project_dir):
    sys.stderr.write(
        f"BLOCKED [{event}]: {tool_count} tool calls in this session "
        f"(workspace={project_dir}) but no valid <time-log> marker emitted.\n\n"
        f"Emit a canonical marker per task in your final response, e.g.:\n"
        f"  <time-log>YYYY-MM-DD HH:MMZ-HH:MMZ | category | scope | summary | duration</time-log>\n"
        f"  (en-dash U+2013 between times, middle-dot U+00B7 between category and scope)\n"
        f"Or opt out: <time-log>SKIP: reason</time-log>\n"
        f"Threshold: {MIN_WORK_THRESHOLD} tool calls (env TIMELOG_MIN_TOOLS). "
        f"Disable: TIMELOG_ENFORCE=0\n"
    )
    sys.exit(2)


def main():
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
    text, tool_count = scan_transcript(transcript_path)

    candidates = core.extract_markers(text)
    valid, new = core.select_new_entries(candidates, existing)

    if new:
        with open(log_file, "a") as f:
            for entry in new:
                f.write(entry + "\n")

    if ENFORCE and tool_count >= MIN_WORK_THRESHOLD and not valid and not core.has_skip(text):
        fail_block(tool_count, event, project_dir)

    sys.exit(0)


if __name__ == "__main__":
    main()
