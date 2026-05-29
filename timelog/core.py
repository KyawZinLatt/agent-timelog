import re

MARKER_RE = re.compile(r"<time-log>(.+?)</time-log>", re.DOTALL)


def extract_markers(text):
    """Return a list of stripped content strings found inside <time-log>…</time-log> tags."""
    return [m.group(1).strip() for m in MARKER_RE.finditer(text)]


ENTRY_RE = re.compile(
    r"^"
    r"\d{4}-\d{2}-\d{2}"
    r"\s"
    r"\d{2}:\d{2}Z–\d{2}:\d{2}Z"
    r"\s\|\s"
    r"[a-z][a-z-]*"
    r"\s·\s"
    r"[a-z][a-z-]*"
    r"\s\|\s"
    r"(?:(?! \| ).)+?"
    r"\s\|\s"
    r"(?:\d+h\s\d+m|\d+h|\d+m)"
    r"$"
)


def is_valid_entry(entry):
    """Return True if entry matches the canonical time-log format, False otherwise."""
    return bool(ENTRY_RE.match(" ".join(entry.split())))


SKIP_RE = re.compile(r"<time-log>\s*SKIP\s*:\s*\S.*?</time-log>", re.DOTALL)


def has_skip(text):
    """Return True if text contains a valid SKIP marker, False otherwise."""
    return bool(SKIP_RE.search(text))


def select_new_entries(candidates, existing):
    """Return (all_valid_collapsed, new_unique_collapsed).

    all_valid_collapsed: every candidate passing is_valid_entry (collapsed) —
        caller uses this to decide whether the session produced any valid marker.
    new_unique_collapsed: validated entries not in `existing` and not repeated
        within this run, first-seen order.
    """
    valid = []
    new = []
    seen = set()
    for entry in candidates:
        collapsed = " ".join(entry.split())
        if not collapsed or not is_valid_entry(collapsed):
            continue
        valid.append(collapsed)
        if collapsed in existing or collapsed in seen:
            continue
        seen.add(collapsed)
        new.append(collapsed)
    return valid, new


def sanitize_token(value, fallback):
    """Coerce a string to a valid category/scope token: lowercase letters + hyphens."""
    lowered = "".join(
        c if ((c.isalpha() and c.isascii()) or c == "-") else "-"
        for c in value.lower()
    )
    while "--" in lowered:
        lowered = lowered.replace("--", "-")
    lowered = lowered.strip("-")
    if lowered and lowered[0].isalpha():
        return lowered
    return fallback


def format_duration(total_minutes):
    """Render minutes as 'Nm' | 'Nh' | 'Nh Nm'; minimum 1m."""
    if total_minutes < 1:
        total_minutes = 1
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def build_entry(date, start, end, category, scope, summary, duration):
    """Assemble a canonical time-log line. Caller ensures fields are clean."""
    return f"{date} {start}Z–{end}Z | {category} · {scope} | {summary} | {duration}"
