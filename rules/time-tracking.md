# Time tracking (agent-timelog)

This workspace uses **agent-timelog** to record how time is spent per session. A Stop / PreCompact / SubagentStop hook writes entries to `<workspace>/.time-log.md`. It logs the markers you emit; if you emit none, it auto-synthesizes a generic entry so every working session is recorded. **By default it enforces a real marker:** on session end (Stop) it blocks **once** to ask you for one if you did work and emitted none — so emit a marker (or a SKIP) in your final response. It blocks at most once, never blocks subagents or `/compact`, and always exits 0. (Set `TIMELOG_ENFORCE=0` to disable enforcement.)

## How to log your time (preferred path)

Emit one `<time-log>` block per distinct task in your FINAL response, using the canonical format:

`YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration`

- **Times:** UTC with `Z` suffix; en-dash `–` (U+2013) between the two times — NOT a hyphen.
- **Separator:** middle dot `·` (U+00B7) between category and scope — NOT a period or hyphen.
- **Category / scope:** lowercase letters + hyphens. Scope is project-defined (e.g. `backend`, `frontend`, `infra`, `docs`, `workspace`).
- **Duration:** `Nm` · `Nh` · `Nh Nm` (e.g. `54m`, `2h`, `2h 10m`).
- **Summary:** anything EXCEPT ` | ` (space-pipe-space) — use commas or em-dashes inside.

Agent-authored markers are preferred because their summaries are meaningful. If you omit them, the hook still logs an entry synthesized from session metadata — category and summary inferred from your tool use (see below); it only falls back to a generic `auto` line when no recognizable tool activity is present.

**Log Q&A too.** A session that only answered questions or discussed a design still spent your time — emit a real marker for it (use a fitting category such as `qa`). Don't SKIP it. SKIP is only for sessions with genuinely nothing to record.

## What the hook does

1. Parses every `<time-log>…</time-log>` marker from the session, validates each against the canonical format, dedups, and appends new valid ones.
2. If you emitted no valid marker AND did not opt out AND the session made at least `TIMELOG_MIN_TOOLS` tool calls, it SYNTHESIZES one entry from the transcript. Category + summary are inferred from your tool use — files edited → `feature · edited a.py, b.py (N tool calls)`, commands run → `ops · ran K commands (N tool calls)`, files read → `research · read/searched K files (N tool calls)` — falling back to a generic `auto · auto-logged Stop, N tool calls` line only when no recognizable tool activity is present.
3. Always exits 0. With enforcement on (default), it may block the main `Stop` event **once** to request a marker; it never blocks the retry, never blocks `/compact`, and never blocks a subagent. With `TIMELOG_ENFORCE=0` it never blocks at all.

## Opt out (SKIP)

Reserve SKIP for sessions with genuinely nothing to record — a monitoring tick, an accidental start, nothing answered and nothing changed. **Q&A and discussion are work; log those with a real marker instead.** To suppress synthesis on a truly empty session:

`<time-log>SKIP: <one-line reason></time-log>`

## Subagents

Subagents auto-log too (the hook fires on `SubagentStop`) but are NEVER blocked. A subagent's marker must live in that subagent's own final text to be captured. If a subagent emits no marker, the hook synthesizes a meaningful entry from the subagent's own transcript (`scope = subagent`, category inferred from its tool use, summary from its agent type and dispatch prompt) — so its work is recorded distinctly, not rolled up into the parent.

## Knobs (environment variables)

| Knob | Default | Effect |
|---|---|---|
| `TIMELOG_MIN_TOOLS` | `1` | Minimum tool calls before anything is logged/synthesized. |
| `TIMELOG_SYNTHESIZE` | `1` | Set to `0` to log only agent-emitted markers (disable auto-synthesis). |
| `TIMELOG_DEST` | `local` | Destination: `local` (per-workspace), `global` (one central file), or `both`. |
| `TIMELOG_GLOBAL_PATH` | `~/.claude/.time-log.md` | Global file used when `TIMELOG_DEST` is `global` or `both`. |
| `TIMELOG_ENFORCE` | `1` | Require a real marker (default on). On `Stop`, a working session with no quality marker is blocked **once** (you must emit a marker or `SKIP`); the retry falls through to synthesis. Lazy/synthesized-looking summaries are rejected as absent. Subagents and `PreCompact` are never blocked. Set `0` to disable. |

Hook: `$HOME/.claude/hooks/timelog/claude_hook.py`. Data file: `<workspace>/.time-log.md` (local), and/or the global file above per `TIMELOG_DEST` (gitignored by this tool; never committed automatically).
