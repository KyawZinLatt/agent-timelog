import datetime
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


def entry_summary(entry):
    """Return the summary (3rd) field of a canonical entry, or '' if unparseable.

    The format forbids ' | ' inside a summary, so a valid line splits into
    exactly four ' | '-delimited fields and parts[2] is the summary.
    """
    parts = " ".join(entry.split()).split(" | ")
    return parts[2] if len(parts) == 4 else ""


LAZY_SUMMARY_MIN_LEN = 8
LAZY_GENERIC = frozenset(
    {"auto", "work", "stuff", "misc", "task", "session", "done", "na", "n/a", "todo"}
)
# Synthesis tells: the "(N tool calls)" suffix this hook itself appends, the
# generic "auto-logged …" line, and the deterministic "ran N commands" /
# "read/searched N files" bodies. An agent describing real work writes none of these.
LAZY_SUMMARY_RE = re.compile(
    r"\b\d+\s+tool\s+calls?\b"
    r"|^auto-logged\b"
    r"|^ran\s+\d+\s+commands?\b"
    r"|^read/searched\s+\d+\s+files?\b",
    re.IGNORECASE,
)


def is_lazy_summary(summary):
    """True if a summary looks synthesized or generic rather than real work.

    Gates agent-emitted markers on the enforce path so a junk or copy-of-synthesis
    summary is treated as absent. Heuristic only — no semantic understanding.
    """
    s = " ".join(summary.split())
    if len(s) < LAZY_SUMMARY_MIN_LEN:
        return True
    if s.lower() in LAZY_GENERIC:
        return True
    return bool(LAZY_SUMMARY_RE.search(s))


def filter_quality(candidates):
    """Collapsed candidates that are valid canonical entries with non-lazy summaries.

    The enforce path uses this to treat invalid or synthesized/generic markers as
    absent — both for the block decision and for what actually gets logged.
    """
    out = []
    for entry in candidates:
        collapsed = " ".join(entry.split())
        if is_valid_entry(collapsed) and not is_lazy_summary(entry_summary(collapsed)):
            out.append(collapsed)
    return out


def skip_exempts_block(tool_count, max_tools):
    """True if a SKIP marker should exempt this session from the enforce block.

    A SKIP is honored silently for low-activity sessions (a monitoring tick, an
    accidental start). Above `max_tools` the session likely did real work that a
    SKIP would wrongly discard — so it is NOT exempt and the caller challenges it.
    Count-based heuristic only; the boundary value is inclusive (still exempt).
    """
    return tool_count <= max_tools


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


def correct_entry_date(entry, today):
    """Trust the system clock, not the marker's date string.

    `today` is a datetime.date supplied by the caller (kept pure — no now() here).
    A valid entry whose date is `today` or yesterday (UTC) is accepted verbatim
    (allows midnight-crossing sessions). Anything else — a stale date, a future
    date, or a shape-valid-but-impossible date like 2026-13-40 — has its date
    field rewritten to `today`, preserving times and duration.

    Returns (entry, old_date). old_date is the replaced string only when a rewrite
    happened, else None — the caller uses it to print a one-line stderr note.
    Non-canonical input is returned collapsed and unchanged (old_date None).
    """
    collapsed = " ".join(entry.split())
    if not is_valid_entry(collapsed):
        return collapsed, None
    date_str = collapsed[:10]
    try:
        marker_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return today.isoformat() + collapsed[10:], date_str
    yesterday = today - datetime.timedelta(days=1)
    if marker_date in (today, yesterday):
        return collapsed, None
    return today.isoformat() + collapsed[10:], date_str


def normalize_scope_token(scope, workspace_slug, repo_suffix=None):
    """Force a scope token to identify its workspace, so a shared GLOBAL log can
    tell which project an entry belongs to.

    A scope that already names the workspace — equal to the slug, or beginning
    with `<slug>-` — passes through verbatim (so an agent-supplied
    "dev-server-setup-backend" is untouched). Otherwise the slug is prepended:
    "backend" -> "dev-server-setup-backend", "workspace" -> "dev-server-setup-workspace".
    The boundary check ("<slug>-", not bare startswith) keeps slug "dev" from
    swallowing an unrelated scope "developer".

    repo_suffix is the conservative best-effort repo hint. It is appended only
    when the agent supplied no suffix of its own — i.e. the scope collapsed to
    exactly the bare slug — so synthesized/bare scopes gain "-<subdir>" while an
    explicit "...-backend" is left alone.
    """
    if scope == workspace_slug or scope.startswith(workspace_slug + "-"):
        out = scope
    else:
        out = f"{workspace_slug}-{scope}"
    if repo_suffix and out == workspace_slug and repo_suffix != workspace_slug:
        out = f"{workspace_slug}-{repo_suffix}"
    return out


def normalize_scope(entry, workspace_slug, repo_suffix=None):
    """Apply normalize_scope_token to the scope field of a canonical entry.

    The scope is the token after ' · ' inside the second ' | '-delimited field.
    Non-canonical input is returned collapsed and unchanged.
    """
    collapsed = " ".join(entry.split())
    if not is_valid_entry(collapsed):
        return collapsed
    parts = collapsed.split(" | ")
    cat_scope = parts[1].split(" · ")
    cat_scope[1] = normalize_scope_token(cat_scope[1], workspace_slug, repo_suffix)
    parts[1] = " · ".join(cat_scope)
    return " | ".join(parts)


def dominant_subdir(subdirs):
    """Single shared immediate subdir slug across touched files, or None.

    `subdirs` is the immediate-subdir name of each write under the workspace
    ('' for a workspace-root file). Conservative: a slug is returned only when
    every touched file shares ONE non-empty subdir; mixed dirs, a root-level
    file, or no under-workspace files at all -> None (ambiguous, skip).
    """
    uniq = set(subdirs)
    if len(uniq) == 1:
        only = next(iter(uniq))
        if only:
            return sanitize_token(only, "") or None
    return None


WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
OPS_TOOLS = frozenset({"Bash"})
RESEARCH_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch"})

SUBAGENT_SUMMARY_MAX = 100
SESSION_SUMMARY_MAX = 100


def infer_category(tool_counts):
    """Map a {tool_name: count} histogram to a category token, deterministically.

    Any file-mutating tool wins → 'feature'. Otherwise Bash-dominant → 'ops',
    read/search/web → 'research'. Unknown-only or empty → 'auto'.
    """
    if not tool_counts:
        return "auto"
    writes = sum(n for t, n in tool_counts.items() if t in WRITE_TOOLS)
    ops = sum(n for t, n in tool_counts.items() if t in OPS_TOOLS)
    research = sum(n for t, n in tool_counts.items() if t in RESEARCH_TOOLS)
    if writes:
        return "feature"
    if ops and ops >= research:
        return "ops"
    if research:
        return "research"
    return "auto"


def compose_subagent_summary(agent_type, dispatch_prompt, tool_total):
    """Build a one-line summary from agent type + dispatch intent + tool count.

    Deterministic, no LLM. Strips ' | ' (the field separator), collapses
    whitespace, and truncates so the whole line stays within budget.
    """
    label = " ".join((agent_type or "subagent").split()).replace("|", "/")
    calls = f"{tool_total} tool call" + ("" if tool_total == 1 else "s")
    suffix = f" ({calls})"

    intent = ""
    for line in (dispatch_prompt or "").splitlines():
        stripped = line.strip()
        if stripped:
            intent = " ".join(stripped.split()).replace("|", "/")
            break

    if not intent:
        return f"{label}{suffix}"

    prefix = f"{label}: "
    room = SUBAGENT_SUMMARY_MAX - len(prefix) - len(suffix)
    if room < 10:
        room = 10
    if len(intent) > room:
        intent = intent[: room - 1].rstrip() + "…"
    return f"{prefix}{intent}{suffix}"


def compose_session_summary(files, bash_count, research_count, tool_total):
    """Build a meaningful main-session summary from tool activity.

    Deterministic, no LLM. Describes the dominant activity — files edited
    (mirrors infer_category's write-wins rule), else commands run, else files
    read/searched — strips ' | ', and truncates to budget. Returns None when
    activity is unrecognizable so the caller can fall back to the generic line.
    """
    calls = f"{tool_total} tool call" + ("" if tool_total == 1 else "s")
    suffix = f" ({calls})"

    if files:
        clean = [" ".join(f.split()).replace("|", "/") for f in files if f and f.strip()]
        body = "edited " + ", ".join(clean) if clean else ""
    elif bash_count:
        body = f"ran {bash_count} command" + ("" if bash_count == 1 else "s")
    elif research_count:
        body = f"read/searched {research_count} file" + ("" if research_count == 1 else "s")
    else:
        body = ""

    if not body:
        return None

    room = SESSION_SUMMARY_MAX - len(suffix)
    if room < 10:
        room = 10
    if len(body) > room:
        body = body[: room - 1].rstrip() + "…"
    return f"{body}{suffix}"
