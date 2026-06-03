# agent-timelog

Automatic per-session time tracking for AI coding agents. Every session is logged to
`<workspace>/.time-log.md` — either from a `<time-log>` marker the agent emits, or from
a synthesized fallback entry derived from transcript metadata. By default the hook
requires a real marker: on session end it blocks **once** to ask for one, then never
again (see [Enforce mode](#enforce-mode)). Set `TIMELOG_ENFORCE=0` for the legacy
never-block behavior. Either way the hook always exits 0.

---

## How it works

1. **Marker path (preferred):** The agent emits a `<time-log>…</time-log>` tag in its
   final response. The hook extracts, validates, deduplicates, and appends the entry.
2. **Synthesis path (fallback):** If no valid marker is emitted and no SKIP is present,
   the hook synthesizes an entry from transcript metadata. The category and summary are
   activity-aware — derived from the session's tool use (files edited → `feature`,
   commands run → `ops`, files read → `research`); a session with no recognizable tool
   activity falls back to a generic `auto`-category line. Under the default enforce mode
   this fallback is what gets written on the retry after a single block (see below).
3. **SKIP path:** The agent emits `<time-log>SKIP: reason</time-log>` to suppress
   synthesis for a session with genuinely nothing to record (e.g. a monitoring tick
   or accidental start). Q&A and discussion DO count — log those with a real marker.

The hook fires on three Claude Code events: **Stop**, **PreCompact**, **SubagentStop**.
It never exits with a non-zero code.

Whenever it writes one or more entries, it echoes them back to you via a one-line
`systemMessage` (`⏱ logged N time-log entr…`) so you can see exactly what landed in
`.time-log.md`. If nothing is written (deduped, SKIP, or no work), it stays silent.

### Subagent behavior

Subagents (dispatched via `SubagentStop`) are logged the same way as the main agent.
A marker in the subagent's own response text is captured directly. A subagent that does
work without emitting a marker gets a synthesized entry derived from its **own**
transcript — `scope = <workspace>-subagent`, a category inferred from its tool use, and a summary
built from its agent type and dispatch prompt (e.g.
`claude-code-guide: researched the hook schema (7 tool calls)`). This works for any
subagent, including third-party ones, because the logic lives in this hook rather than
in the subagent. Subagents are never blocked.

---

## Install — repo path

```bash
git clone https://github.com/KyawZinLatt/agent-timelog
cd agent-timelog
bash install.sh
```

`install.sh` copies the Python files to `~/.claude/hooks/timelog/`, merges the three hook
events (`Stop`, `PreCompact`, `SubagentStop`) into `~/.claude/settings.json`, and appends
the rule block to `~/.claude/CLAUDE.md`. Re-running is idempotent.

### Uninstall

```bash
bash uninstall.sh
```

Removes the hook files, strips the settings entries, and removes the rule block.
`.time-log.md` data files are left untouched.

---

## Install — Claude Code plugin

The `claude-plugin/` directory is a self-contained Claude Code plugin package. It
registers all three hook events via `hooks/hooks.json` — no manual `settings.json` edit
required.

Install it per the Claude Code plugin documentation:

```bash
claude plugin install ./claude-plugin
```

The plugin uses `${CLAUDE_PLUGIN_ROOT}` to locate its scripts, so it works regardless of
where Claude Code is installed.

---

## Emitting markers

In your final response (or before `/compact`), emit one `<time-log>` block per distinct
task completed:

```
<time-log>2026-05-29 09:00Z–09:45Z | feature · backend | add user auth endpoint | 45m</time-log>
```

Format: `YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration`

- **Times:** UTC, `Z` suffix, **en-dash `–` (U+2013)** between start and end
- **Separator** between category and scope: **middle-dot `·` (U+00B7)**
- **category / scope:** lowercase letters and hyphens
- **duration:** `Nm` | `Nh` | `Nh Nm` (e.g. `20m`, `2h`, `1h 30m`)
- **summary:** any text — MUST NOT contain ` | ` (space-pipe-space)

Multiple markers per session are allowed.

### Automatic correctness

You don't have to get the date or scope exactly right — the hook does not trust either
string and rewrites them deterministically before logging:

- **Date.** A marker dated today or yesterday (UTC) is kept as-is (so a session that
  crosses midnight is fine). Any other date — a stale year, a future date, or an
  impossible one — is rewritten to today's UTC date, preserving the times and duration,
  with a one-line `[timelog] corrected date … -> …` note on stderr. A future- or
  >1-day-stale line never lands.
- **Scope.** The workspace slug is prepended unless your scope already names it:
  `backend` → `dev-server-setup-backend`, while `dev-server-setup-backend` passes through
  verbatim. The same normalization is applied to synthesized and subagent lines (a
  subagent logs `<slug>-subagent`), so a shared global log stays attributable to its
  project. When a session's writes all land under one subdirectory and you gave no repo
  suffix, that subdirectory is appended as a best-effort hint.

### The `/log` command

Run `/log` mid-session to append a one-off entry directly to the log, bypassing the
marker pipeline. It honors `TIMELOG_DEST` (writing to the local file, the global file, or
both) and applies the same workspace-slug scope prefix as the hook, so a manual `/log`
entry is indistinguishable from an auto-logged one.

---

## SKIP opt-out

Reserve SKIP for sessions with genuinely nothing to record (monitoring ticks,
accidental starts). **Q&A and discussion count as work — log them with a real marker**
(e.g. category `qa`) instead of skipping.

```
<time-log>SKIP: monitoring tick, no work</time-log>
```

This suppresses auto-synthesis. The session is not logged.

---

## Environment knobs

| Variable | Default | Effect |
|---|---|---|
| `TIMELOG_MIN_TOOLS` | `1` | Sessions with fewer tool calls than this threshold log nothing |
| `TIMELOG_SYNTHESIZE` | `1` | Set to `0` to disable auto-synthesis (only agent-emitted markers are logged) |
| `TIMELOG_DEST` | `local` | Where entries are written: `local` (per-workspace `.time-log.md`), `global` (one central file), or `both` |
| `TIMELOG_GLOBAL_PATH` | `~/.claude/.time-log.md` | Path of the global file used by `TIMELOG_DEST=global\|both` |
| `TIMELOG_ENFORCE` | `1` | Require a real agent marker (see [Enforce mode](#enforce-mode)). Set to `0` for the legacy never-block behavior |
| `TIMELOG_REMIND` | `1` | Mid-session reminder (PostToolUse). Once a session crosses `TIMELOG_REMIND_AFTER` tool calls with no quality marker in the transcript, the hook injects ONE context reminder — with live session stats — to emit a `<time-log>` marker in the final response. Set `0` to disable. |
| `TIMELOG_REMIND_AFTER` | `10` | Tool calls before the one-shot reminder fires. |
| `TIMELOG_SKIP_MAX_TOOLS` | `5` | Tool-call ceiling under which a `SKIP` is honored silently. Above it the `SKIP` is challenged once — the session likely did real work. Only applies when enforce is on |

Set these in the shell profile that launches Claude Code (e.g. `~/.zshrc`).

**Destinations.** By default each workspace keeps its own `.time-log.md` (`local`). Set
`TIMELOG_DEST=global` to funnel every session into one central file instead, or
`TIMELOG_DEST=both` to keep a per-workspace copy *and* a central roll-up. In `global`/`both`
the `scope` token on each line still names the originating workspace, so a shared file stays
readable. Each file dedups independently; in `both` the same entry intentionally appears in
both places.

### Enforce mode

**On by default.** Synthesized fallback lines (e.g. `ops · workspace | ran 3 commands
(3 tool calls) | 1m`) keep every session recorded, but their summaries are mechanical.
Enforce mode demands a *real* marker instead.

On the main `Stop` event, if the session did work (≥ `TIMELOG_MIN_TOOLS`) but produced no
quality marker, the hook blocks **once** and asks the agent to emit a canonical `<time-log>`
line describing the work (or a `<time-log>SKIP: reason</time-log>` opt-out). The retry is
never blocked again — if the marker is still missing it falls through to the synthesized
fallback. So enforcement costs at most one extra turn and the hook is never *permanently*
blocking (and always exits 0; the block is signalled via a `decision` field, not a non-zero
exit).

It also filters out **lazy** markers: a summary that merely copies the synthesis shape
(`ran N commands`, `read/searched N files`, anything ending in `(N tool calls)`,
`auto-logged …`), is a generic single word (`auto`, `work`, `done`, …), or is too short is
treated as absent — so the agent can't satisfy the gate with junk.

It also guards `SKIP` against misuse. A `SKIP` is honored silently only for quiet sessions
(a monitoring tick, an accidental start). If the session made more than `TIMELOG_SKIP_MAX_TOOLS`
tool calls (default `5`), the `SKIP` is **challenged once** — the agent is asked to replace it
with a real marker or re-emit the `SKIP` to confirm. Like the missing-marker block this is
bounded: the retry honors the `SKIP`, so a genuine no-op still costs at most one extra turn.

Only `Stop` is enforced. `SubagentStop` and `PreCompact` are never blocked, preserving the
never-block contract for subagents and `/compact`.

**Set `TIMELOG_ENFORCE=0`** to disable enforcement entirely and restore the legacy
never-block behavior (synthesis only, no marker ever required).

### Mid-session reminder (PostToolUse)

Enforcement is reactive — it blocks at session end. The reminder is the
proactive half: a `PostToolUse` hook counts tool calls in a tmpdir state file
(`timelog-remind-<session_id>.json`) and, once past the threshold, scans the
transcript once. If no quality marker or SKIP exists yet, it injects a single
`additionalContext` reminder with live stats (elapsed time, files edited,
commands run). One reminder per session, fail-open, never blocks tools.
Steady state: the marker lands in the final response and the Stop block never
fires.

---

## Data

- **File:** `<workspace>/.time-log.md` — auto-created on first session in a workspace
- **Gitignored:** the repo's `.gitignore` excludes `.time-log.md`; the hook never commits it
- **Format:** append-only; one entry per line; human-readable

Example entries:

```
2026-05-29 09:00Z–09:45Z | feature · backend | add user auth endpoint | 45m
2026-05-29 10:00Z–10:20Z | docs · workspace | update README | 20m
2026-05-29 11:00Z–11:15Z | feature · my-project | edited auth.py, routes.py (8 tool calls) | 15m
```

---

## No runtime dependencies

The hook uses Python 3 standard library only. End users need no `pip install` or `uv add`.
Python 3.8+ is sufficient. Dev / test uses `uv run pytest`.

---

## Agent-agnostic note

The core engine (`timelog/core.py`) is agent-neutral: pure functions, no I/O, no
Claude-specific imports. The Claude Code adapter (`timelog/claude_hook.py`) is the only
adapter that ships today. The `adapters/` directory and `spec/format.md` reserve the
extension points for future adapters (Cursor, Codex, OpenCode, Gemini, etc.).

---

## Privacy

- No usernames, real IP addresses, email addresses, or private credentials appear in any
  tracked file in this repo.
- Example IP addresses in documentation use the RFC 5737 TEST-NET range only;
  example emails use the RFC 2606 reserved `example.com` domain only.
- `.time-log.md` files (which may contain private work details) are gitignored and are
  never committed or scanned by this tool.
- Run `bash scripts/sanitize-check.sh` before publishing to catch accidental leaks.
