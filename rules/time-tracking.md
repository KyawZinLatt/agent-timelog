# Time tracking (agent-timelog)

This workspace uses **agent-timelog** to record how time is spent per session. A Stop / PreCompact / SubagentStop hook writes entries to `<workspace>/.time-log.md`. **The hook never blocks — it always exits 0.** It logs the markers you emit; if you emit none, it auto-synthesizes a generic entry so every working session is recorded.

## How to log your time (preferred path)

Emit one `<time-log>` block per distinct task in your FINAL response, using the canonical format:

`YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration`

- **Times:** UTC with `Z` suffix; en-dash `–` (U+2013) between the two times — NOT a hyphen.
- **Separator:** middle dot `·` (U+00B7) between category and scope — NOT a period or hyphen.
- **Category / scope:** lowercase letters + hyphens. Scope is project-defined (e.g. `backend`, `frontend`, `infra`, `docs`, `workspace`).
- **Duration:** `Nm` · `Nh` · `Nh Nm` (e.g. `54m`, `2h`, `2h 10m`).
- **Summary:** anything EXCEPT ` | ` (space-pipe-space) — use commas or em-dashes inside.

Agent-authored markers are preferred because their summaries are meaningful. If you omit them, the hook still logs a generic `auto`-category entry derived from session metadata (see below).

**Log Q&A too.** A session that only answered questions or discussed a design still spent your time — emit a real marker for it (use a fitting category such as `qa`). Don't SKIP it. SKIP is only for sessions with genuinely nothing to record.

## What the hook does

1. Parses every `<time-log>…</time-log>` marker from the session, validates each against the canonical format, dedups, and appends new valid ones.
2. If you emitted no valid marker AND did not opt out AND the session made at least `TIMELOG_MIN_TOOLS` tool calls, it SYNTHESIZES one `auto`-category entry (times from the transcript, summary like `auto-logged Stop, N tool calls`).
3. Always exits 0. It never blocks session end or `/compact`, and never blocks a subagent.

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

Hook: `$HOME/.claude/hooks/timelog/claude_hook.py`. Data file: `<workspace>/.time-log.md` (gitignored by this tool; never committed automatically).
