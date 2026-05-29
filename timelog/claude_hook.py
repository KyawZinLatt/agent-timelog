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
