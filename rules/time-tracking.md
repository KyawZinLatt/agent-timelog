## Time tracking (HARD-ENFORCED, global)

**This rule applies to every Claude Code session.** Stop + PreCompact + SubagentStop hooks (`$HOME/.claude/hooks/timelog/claude_hook.py`) parse `<time-log>` markers from your final response and append validated entries to `${CLAUDE_PROJECT_DIR}/.time-log.md` (auto-created per workspace). Non-compliance blocks session end and `/compact`.

### MUST

- MUST emit one `<time-log>` block per distinct task completed, in the FINAL response of the session OR before invoking `/compact`
- MUST use canonical format: `YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration`
- MUST use en-dash `–` (U+2013) between times — NOT hyphen `-`
- MUST use middle dot `·` (U+00B7) between category and scope — NOT period or hyphen
- MUST use UTC with `Z` suffix on times
- MUST emit BEFORE invoking `/compact` for any in-flight work — PreCompact hook fires first and blocks compaction otherwise

### MUST NOT

- MUST NOT emit a canonical-format `<time-log>` block outside of intentional task logging. The hook validates strict format and appends any match — emitting real-looking markers in docs, examples, or explanation logs them
- MUST NOT include ` | ` (space-pipe-space) inside summary — breaks the parser; use commas or em-dashes
- MUST NOT use placeholder text (`...`, `TBD`, `XXX`) inside a real marker — fails validator, dropped silently
- MUST NOT ignore enforcement failure — if hook exits 2, FIX the marker, do not bypass

### Opt-out for non-work sessions

If session was purely Q&A, monitoring ticks, or no meaningful work happened (but `tool_call_count ≥ MIN_WORK_THRESHOLD`):

`<time-log>SKIP: <one-line reason></time-log>`

Validator special-cases this; enforcement passes.

### Format

- **Category:** lowercase letters + hyphens. Suggested set: `bug-fix · feature · incident · investigation · ops · deploy · refactor · docs · planning · monitoring · review · research · setup · comms · cleanup`. Projects may define their own — validator only checks shape, not vocabulary.
- **Scope:** lowercase letters + hyphens, project-defined. Examples: `backend · frontend · infra · scripts · blog · workspace · multi`. Free choice per project.
- **Times:** UTC with `Z` suffix, en-dash `–` (U+2013) separator
- **Duration:** `Nm` · `Nh` · `Nh Nm` (e.g. `54m`, `2h`, `2h 10m`)
- **Summary:** anything except ` | ` (space-pipe-space)

### Final-response checklist (RUN BEFORE SENDING LAST REPLY)

1. Did I do meaningful work this session (≥5 tool calls)? No → no marker needed; yes → continue
2. Did I emit a `<time-log>` block for each distinct task in this final response? No → STOP, add markers
3. Does each marker match canonical format? (en-dash, middle dot, valid duration, no internal pipe in summary)
4. If discussing the time-log system in this response: am I avoiding canonical-format examples? Use `<TIMELOG>` placeholder or break the tag: `<time-log >`
5. If session was non-work but tool-heavy: did I emit `<time-log>SKIP: <reason></time-log>`?

If any answer fails → fix before sending. Hook WILL block on exit 2.

### SubagentStop behavior

Hooks fire on `Stop`, `PreCompact`, AND `SubagentStop` (a dispatched subagent finishing).

- **Append:** all three events scan the event's transcript, validate markers, dedup, and append to `<workspace>/.time-log.md`.
- **Enforcement (exit 2 block):** runs ONLY for `Stop` and `PreCompact` — NEVER for `SubagentStop`.
- A subagent that did work without a marker is NOT blocked; its work rolls up into the parent session's marker.
- If a subagent DOES emit a valid marker, it IS logged immediately.
- Markers must live in the emitting agent's own final text — parent and subagent markers are independent.

### Enforcement

| Knob | Default | Env var |
|---|---|---|
| Tool-call threshold | 5 | `TIMELOG_MIN_TOOLS` |
| Enforcement on/off | on | `TIMELOG_ENFORCE=0` disables |
| Workspace root | `$CLAUDE_PROJECT_DIR` | (set by Claude Code) |
| Log file path | `<workspace>/.time-log.md` | (auto-created) |

Hook pipeline on Stop + PreCompact (+ SubagentStop for append only):

1. Resolve workspace from `$CLAUDE_PROJECT_DIR`
2. Auto-create `<workspace>/.time-log.md` (with header) if missing
3. READ existing entries (dedup set)
4. SCAN transcript JSONL: collect all assistant text + count `tool_use` blocks
5. VALIDATE each `<time-log>` capture against canonical regex (drops prose pollution)
6. DEDUP validated entries against existing + within-run
7. APPEND new validated entries to `<workspace>/.time-log.md`
8. ENFORCE (Stop/PreCompact only): if `tool_calls ≥ threshold` AND no validated markers AND no SKIP → exit 2 with stderr describing the missing marker. Claude Code shows stderr to model; session does not end / compact does not run until compliance.

### Manual emission

Invoke `/log` mid-session for one-off entries (writes directly to `<workspace>/.time-log.md`, bypasses marker pipeline).

### Project overrides

Per-project CLAUDE.md MAY narrow the category/scope vocabulary or add project-specific conventions, but MUST NOT relax format validation — the hook regex is canonical.
