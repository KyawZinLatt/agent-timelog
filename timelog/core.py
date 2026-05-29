import re

MARKER_RE = re.compile(r"<time-log>(.+?)</time-log>", re.DOTALL)


def extract_markers(text):
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
    r".+?"
    r"\s\|\s"
    r"(?:\d+h\s\d+m|\d+h|\d+m)"
    r"$"
)


def is_valid_entry(entry):
    return bool(ENTRY_RE.match(" ".join(entry.split())))
