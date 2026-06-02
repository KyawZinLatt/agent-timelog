# agent-timelog

Automatic per-session time tracking for AI coding agents. Every session is logged to
`<workspace>/.time-log.md` — either from a `<time-log>` marker the agent emits, or from
a synthesized fallback entry derived from transcript metadata. The hook never blocks the
agent; it always exits 0.

---

## How it works

1. **Marker path (preferred):** The agent emits a `<time-log>…</time-log>` tag in its
   final response. The hook extracts, validates, deduplicates, and appends the entry.
2. **Synthesis path (fallback):** If no valid marker is emitted and no SKIP is present,
   the hook synthesizes an entry from transcript metadata. The category and summary are
   activity-aware — derived from the session's tool use (files edited → `feature`,
   commands run → `ops`, files read → `research`); a session with no recognizable tool
   activity falls back to a generic `auto`-category line. The agent is never blocked.
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
transcript — `scope = subagent`, a category inferred from its tool use, and a summary
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

### The `/log` command

Run `/log` mid-session to append a one-off entry directly to `.time-log.md`, bypassing
the marker pipeline.

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

Set these in the shell profile that launches Claude Code (e.g. `~/.zshrc`).

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
