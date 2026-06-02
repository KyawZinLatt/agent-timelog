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
ENFORCE = os.environ.get("TIMELOG_ENFORCE", "1") != "0"
# Above this many tool calls, a SKIP is treated as suspect rather than honored
# silently — the session likely did real work a SKIP would discard. Block once
# to challenge it (the retry honors the SKIP, so it stays bounded).
SKIP_MAX_TOOLS = int(os.environ.get("TIMELOG_SKIP_MAX_TOOLS", "5"))

ENFORCE_REASON = (
    "agent-timelog: no <time-log> marker describing this session's work was found. "
    "Before stopping, emit one canonical marker in your final message:\n"
    "<time-log>YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration</time-log>\n"
    "Use UTC times with an en-dash – between them, a middle-dot · between category and "
    "scope, and a summary that must not contain ' | '. If there is genuinely nothing to "
    "record, emit <time-log>SKIP: reason</time-log> instead."
)

SKIP_CHALLENGE_REASON = (
    "agent-timelog: you emitted <time-log>SKIP</time-log>, but this session made "
    "{n} tool calls. SKIP is only for sessions with nothing to record (a monitoring "
    "tick, an accidental start). If you did real work — including answering questions "
    "or discussing design — replace SKIP with a real canonical marker:\n"
    "<time-log>YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration</time-log>\n"
    "To confirm there is genuinely nothing to record, emit the SKIP again."
)

GLOBAL_LOG_DEFAULT = os.path.join("~", ".claude", LOG_FILENAME)


def resolve_workspace(cwd_from_input):
    candidate = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if candidate and os.path.isdir(candidate):
        return candidate
    if cwd_from_input and os.path.isdir(cwd_from_input):
        return cwd_from_input
    return ""


def resolve_global_path():
    return os.path.expanduser(os.environ.get("TIMELOG_GLOBAL_PATH", GLOBAL_LOG_DEFAULT))


def resolve_destinations(project_dir):
    """Ordered list of log files to write, per TIMELOG_DEST (local|global|both).

    Default and any unrecognized value -> local only, preserving prior behavior.
    """
    dest = os.environ.get("TIMELOG_DEST", "local").strip().lower()
    local = os.path.join(project_dir, LOG_FILENAME)
    if dest == "global":
        return [resolve_global_path()]
    if dest == "both":
        return [local, resolve_global_path()]
    return [local]


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
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
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


def _immediate_subdir(path, project_dir):
    """Immediate subdir of `path` under project_dir, '' for a root-level file,
    or None when the path is not under the workspace at all.

    Conservative input for core.dominant_subdir: only under-workspace writes
    contribute a repo hint; out-of-workspace paths are ignored entirely.
    """
    if not path or not project_dir:
        return None
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(project_dir))
    except ValueError:
        return None
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return None
    head = rel.split(os.sep)
    return head[0] if len(head) > 1 else ""


def scan_session_detail(transcript_path, project_dir=None):
    """Tool-name histogram, edited file basenames, and immediate subdirs.

    Reads only assistant tool_use blocks. File basenames come from the input
    file_path of write tools, dedup-ordered by first appearance. `subdirs` lists
    the immediate-subdir name of each under-workspace write ('' for a root file),
    feeding core.dominant_subdir for the conservative repo suffix.
    """
    tool_counts = {}
    files = []
    subdirs = []
    seen = set()
    if not transcript_path or not os.path.exists(transcript_path):
        return tool_counts, files, subdirs
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
                        sub = _immediate_subdir(path, project_dir)
                        if sub is not None:
                            subdirs.append(sub)
    except OSError:
        return {}, [], []
    return tool_counts, files, subdirs


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


def _display_path(path):
    """Collapse the home prefix to ~ for a compact, unambiguous destination label."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def emit_block(reason):
    """Reject session end (Stop) so the agent emits a marker. Bounded to one retry."""
    try:
        print(json.dumps({"decision": "block", "reason": reason}))
    except (OSError, ValueError):
        pass


def emit_summary(entries, files):
    """Echo distinct written entries + destination files via the systemMessage channel."""
    n = len(entries)
    label = "entry" if n == 1 else "entries"
    dest = ", ".join(_display_path(f) for f in files)
    body = "\n".join(entries)
    msg = f"⏱ logged {n} time-log {label} to {dest}:\n{body}"
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

    destinations = resolve_destinations(project_dir)

    text, tool_count, first_ts, last_ts = scan_transcript(transcript_path)

    # Workspace identity + UTC "today" drive the two correctness rewrites below.
    today = datetime.datetime.now(datetime.timezone.utc).date()
    workspace_slug = core.sanitize_token(
        os.path.basename(project_dir.rstrip("/")), "workspace"
    )

    # Conservative repo hint from this session's writes (main path only; a subagent
    # keeps the bare "<slug>-subagent" scope). Scanned once here and reused by the
    # synthesis branch so the transcript is not re-read.
    is_subagent = event == "SubagentStop" and bool(data.get("agent_type"))
    main_tool_counts, main_files, repo_suffix = {}, [], None
    if not is_subagent:
        main_tool_counts, main_files, main_subdirs = scan_session_detail(
            transcript_path, project_dir
        )
        repo_suffix = core.dominant_subdir(main_subdirs)

    # Normalize every emitted marker before anything else looks at it: rewrite an
    # untrustworthy date to today (UTC), then force the scope to identify its
    # workspace. A date rewrite emits a one-line stderr note. Both rewrites leave
    # non-canonical text untouched for select_new_entries to drop.
    candidates = []
    for raw in core.extract_markers(text):
        corrected, old_date = core.correct_entry_date(raw, today)
        if old_date:
            print(
                f"[timelog] corrected date {old_date} -> {today.isoformat()}",
                file=sys.stderr,
            )
        candidates.append(core.normalize_scope(corrected, workspace_slug, repo_suffix))

    # Enforce mode (default on; set TIMELOG_ENFORCE=0 to disable): on the main Stop
    # event, if the session did real work but produced no QUALITY marker, block ONCE
    # so the agent must describe its work. Lazy/synthesized-looking markers are
    # filtered out and treated as absent — both for this decision and for what gets
    # logged. The retry carries stop_hook_active=True; we then fall through to the
    # normal synthesize path, so the hook is never PERMANENTLY blocking. Subagents
    # and PreCompact are never blocked.
    if ENFORCE:
        candidates = core.filter_quality(candidates)
        if (
            event == "Stop"
            and not data.get("stop_hook_active")
            and tool_count >= MIN_WORK_THRESHOLD
            and not candidates
        ):
            if core.has_skip(text):
                # A SKIP exempts quiet sessions silently, but a SKIP on a busy
                # session likely discards real work — challenge it once.
                if not core.skip_exempts_block(tool_count, SKIP_MAX_TOOLS):
                    emit_block(SKIP_CHALLENGE_REASON.format(n=tool_count))
                    sys.exit(0)
            else:
                emit_block(ENFORCE_REASON)
                sys.exit(0)

    # Markers and the synthesized entry are transcript-derived, so compute them
    # once. Only per-file dedup differs across destinations.
    valid, _ = core.select_new_entries(candidates, set())

    synth = None
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
            # Subagent fallback re-reads the transcript for main-style detail since
            # the up-front scan was skipped on the subagent path.
            if is_subagent:
                main_tool_counts, main_files, _ = scan_session_detail(
                    transcript_path, project_dir
                )
            entry = synthesize_entry(
                event, tool_count, project_dir, first_ts, last_ts,
                main_tool_counts, main_files,
            )
        if core.is_valid_entry(entry):
            # Same scope normalization the markers get, so synthesized and
            # subagent lines also read "<slug>-…" instead of a bare scope.
            synth = core.normalize_scope(entry, workspace_slug, repo_suffix)

    written_entries = []  # distinct entries written to at least one file
    written_files = []    # files that received at least one append

    for log_file in destinations:
        if not ensure_log_file(log_file):
            continue
        existing = read_existing_entries(log_file)
        _, new = core.select_new_entries(candidates, existing)
        to_write = list(new)
        if synth is not None and synth not in existing:
            to_write.append(synth)
        if not to_write:
            continue
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                for ln in to_write:
                    f.write(ln + "\n")
        except OSError:
            continue
        written_files.append(log_file)
        for ln in to_write:
            if ln not in written_entries:
                written_entries.append(ln)

    if written_entries:
        emit_summary(written_entries, written_files)

    sys.exit(0)


if __name__ == "__main__":
    main()
