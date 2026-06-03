---
description: Append a time-log entry for the most recent task to ${CLAUDE_PROJECT_DIR}/.time-log.md
---

# /log

User invoked `/log` to record a time-tracking entry NOW for a recently completed task in the current workspace.

## Your job

1. Resolve workspace: `${CLAUDE_PROJECT_DIR}` (env var, set by Claude Code). If unset, fall back to current working directory.
2. Resolve destination file(s) from `TIMELOG_DEST` (see Destinations section below) — `local`, `global`, or `both`.
3. For EACH destination: if the file does not exist, create it with the standard header (see Auto-create section below)
4. Infer the most recent task: start time, end time, category, scope, summary, duration
5. Append ONE identical line, in canonical format, to EVERY resolved destination:

`YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration`

6. Confirm with a one-liner showing the entry you wrote and each file path written

## Destinations (honor `TIMELOG_DEST`)

The hook writes to one or more files based on the `TIMELOG_DEST` env var. `/log` MUST match, so a manual entry lands in the same place(s) as an auto-logged one:

- **`local`** (default, and any unset/unrecognized value): `<workspace>/.time-log.md`
- **`global`**: the global file ONLY — `${TIMELOG_GLOBAL_PATH}` if set, else `~/.claude/.time-log.md`
- **`both`**: local AND global — write the identical line to each

Read `TIMELOG_DEST` from the environment (e.g. `echo "${TIMELOG_DEST:-local}"`). Create any missing destination with the header first, then append.

### Scope must stay workspace-attributable

The global file is shared across projects, so a bare scope like `backend` is ambiguous there. Mirror the hook's `normalize_scope`: prefix the scope with the workspace slug (the basename of `<workspace>`) unless it already equals `<slug>` or starts with `<slug>-`.

- `backend` → `<slug>-backend` (e.g. `agent-timelog-backend`)
- `agent-timelog-backend` → unchanged (already prefixed)

Apply the SAME prefixed scope to every destination (local included), so `/log` output is byte-identical to what the hook would have written.

## Format

- **Date:** ISO 8601 (`YYYY-MM-DD`)
- **Time range:** UTC with `Z` suffix, en-dash `–` (U+2013) between times — NOT hyphen `-`
- **Separator between category and scope:** middle dot `·` (U+00B7) — NOT period or hyphen
- **Field separator:** ` | ` (space-pipe-space)
- **Duration:** `Nm` · `Nh` · `Nh Nm` (e.g. `54m`, `2h`, `2h 10m`)
- **Summary:** anything EXCEPT ` | ` (use commas or em-dashes inside)

## Categories (suggested vocabulary)

`bug-fix · feature · incident · investigation · ops · deploy · refactor · docs · planning · monitoring · review · research · setup · comms · cleanup`

Pick the closest match. If a task genuinely spans multiple categories, pick the primary one or write multiple lines.

## Scopes (project-defined)

Lowercase letters + hyphens. Free choice per project. Examples:

- Generic: `backend · frontend · infra · scripts · docs · multi · workspace`

Use whatever scope vocabulary fits the workspace. Check `<workspace>/.time-log.md` header or project CLAUDE.md for project-specific conventions before guessing.

## If user passed arguments

If `/log` has arguments (e.g. `/log incident · backend · my note`), use them to override your inference.

## Constraints

- ONE line per invocation (unless splitting a multi-category task)
- UTC times with `Z` suffix
- Duration: `Nm` · `Nh` · `Nh Nm`
- Do NOT emit `<time-log>...</time-log>` markers from `/log` — write directly to the file. The hook handles marker-based emission; `/log` is the direct-write bypass.
- Confirm tight: e.g. "Logged to `<workspace>/.time-log.md`: 2026-05-26 03:30Z–04:24Z | incident · backend | short summary | 54m"

## Auto-create header (if file missing)

```markdown
# Time log

Auto-appended by Stop + PreCompact hooks (`$HOME/.claude/hooks/timelog/claude_hook.py`).
Validates strict canonical format; prose containing the tag pair is silently dropped.

## Format

`YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration`

- Times: UTC, `Z` suffix, en-dash `–` (U+2013) between times
- Category / scope: lowercase letters + hyphens; separator is middle-dot `·` (U+00B7)
- Duration: `Nm` · `Nh` · `Nh Nm`
- Summary MUST NOT contain ` | ` (space-pipe-space)

---

## Entries

```
