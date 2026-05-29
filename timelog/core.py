import re

MARKER_RE = re.compile(r"<time-log>(.+?)</time-log>", re.DOTALL)


def extract_markers(text):
    return [m.group(1).strip() for m in MARKER_RE.finditer(text)]
