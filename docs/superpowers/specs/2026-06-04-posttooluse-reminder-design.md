# PostToolUse Mid-Session Reminder — Design

**Date:** 2026-06-04
**Status:** Approved (pending spec review)
**Sub-project:** A of 3 (B: escaped-session recovery, C: per-task segmentation — future specs)

## Problem

agent-timelog enforces time-log markers reactively: the Stop hook blocks once at
session end when no quality marker exists. This works, but the agent experiences
it as an interruption after it believed it was done, and the blocked retry often
produces a rushed summary. ECC (everything-claude-code) solves the analogous
problem for cost warnings proactively: a PostToolUse hook with matcher `*`
deterministically checks thresholds on every tool call and injects warnings into
the agent's context mid-session, so the agent acts before the end.

This spec ports that "never misses" design to agent-timelog: remind the agent
mid-session — with live session stats — so the marker lands naturally in the
final response and the Stop block becomes a rare backstop.

## Design principles (ported from ECC context-monitor)

1. **Harness-enforced, not model-enforced.** PostToolUse with matcher `*` runs
   deterministically on every tool call. The model cannot forget to check.
2. **Injection, not instruction.** The reminder is delivered via
   `hookSpecificOutput.additionalContext`, so it physically appears in the
   agent's context.
3. **Cheap hot path.** Per-call work is a small JSON state-file read+write.
   The transcript is scanned exactly once per session, at fire time.
4. **Fail-open everywhere.** Any error → exit 0, no output. The reminder must
   never break tool execution.
5. **Single-fire.** One reminder per session (user decision: no debounced
   repeats, no escalation). A marker already present suppresses it.

## Architecture

```
PostToolUse (every tool call, matcher *)
   └─ remind_hook.py
        ├─ read state file <tmpdir>/timelog-remind-<session_id>.json  {count, fired, ts}
        ├─ count++ ; atomic write (tmp.<pid>.<nonce> + os.replace)
        ├─ fired, or count < threshold  → exit 0, silent (hot path)
        └─ count >= threshold && !fired  (>= not ==, so a lost increment
           or restarted counter can never skip past the trigger):
             ├─ scan transcript ONCE (reuse existing scan functions)
             ├─ quality marker or SKIP already present → fired=true, silent
             └─ else → print hookSpecificOutput.additionalContext reminder
                       with live stats; fired=true
```

## Components

| Component | Change |
|---|---|
| `timelog/remind_hook.py` | New hook script (entry point) |
| `claude-plugin/scripts/remind_hook.py` | Synced copy (plugin layout) |
| `claude-plugin/hooks/hooks.json` | Add `PostToolUse` entry, matcher `*` |
| `adapters/claude-code/settings-snippet.json` | Add PostToolUse wiring for non-plugin installs |
| `timelog/claude_hook.py` / `timelog/core.py` | Reused, not modified (scan + marker functions) |
| `tests/test_remind_hook.py` | New test module |
| `tests/test_plugin_sync.py` | Extend to cover remind_hook.py sync |
| `README.md`, `rules/time-tracking.md` | Document knobs + behavior |

### Reminder message (composed from one transcript scan)

> agent-timelog: ~23m of work so far (12 tool calls — edited core.py,
> remind_hook.py; ran 3 commands). Remember to emit a `<time-log>` marker in
> your final response covering this work.

Stats reuse `scan_transcript` (elapsed, tool count) and `scan_session_detail`
(file basenames, tool histogram). Marker detection reuses
`core.extract_markers` + `core.filter_quality` + `core.has_skip`.

## State file

- Path: `<tempfile.gettempdir()>/timelog-remind-<session_id>.json`
- Shape: `{"count": int, "fired": bool, "ts": float}` (`ts` = last update, epoch seconds)
- `session_id` sanitized to `[A-Za-z0-9-]` only; unsanitizable → exit 0
  (defends state-file path traversal).
- Atomic write: write `target.<pid>.<nonce>.tmp`, then `os.replace` (atomic on
  Windows and POSIX). On replace failure: best-effort unlink tmp, exit 0.
- Stale guard: state older than 24h is discarded and treated as a fresh
  session (tmpdir survives reboots on Windows).

## Error handling

| Failure | Behavior |
|---|---|
| Any uncaught exception | exit 0, no output |
| Missing/unsanitizable session_id | exit 0 |
| Corrupt/missing state file | treat as `{count: 0, fired: false}` |
| Concurrent hook processes race | lost increment acceptable (count is heuristic); atomic replace prevents corruption |
| Transcript missing at fire time | set fired=true, silent (no retry-scan every call) |
| Reminder JSON print fails | swallow, exit 0 |

## Configuration

| Knob | Default | Effect |
|---|---|---|
| `TIMELOG_REMIND` | `1` | `0` disables the reminder hook entirely |
| `TIMELOG_REMIND_AFTER` | `10` | Tool calls before the reminder fires |

Default-on (user decision), matching the project's enforce-by-default
philosophy (`TIMELOG_ENFORCE=1` since #9). Ship as `feat!`.

## Interaction with existing enforcement

Stop-hook enforcement is unchanged. Expected steady state: reminder fires
mid-session → agent emits marker in final response → Stop hook logs it and
never blocks. The block remains the backstop for sessions shorter than the
threshold or where the agent ignores the reminder.

Subagent sessions: the hook keys state by the `session_id` in the hook payload
and scans the `transcript_path` from the same payload, so whichever transcript
the harness reports is the one checked. No subagent-specific logic in v1.

## Testing

pytest, mirroring existing test style (`tests/test_claude_hook.py` patterns):

1. State round-trip; corrupt file recovery; stale (>24h) discard.
2. Sanitization: `../evil`, empty, oversized session ids rejected.
3. Threshold: silent below, fires exactly at N, silent after (fired flag).
4. Suppression: existing quality marker or SKIP in transcript → no reminder.
5. Stats composition from fixture transcript (`tests/fixtures/work.jsonl`).
6. `TIMELOG_REMIND=0` → silent pass-through.
7. Fail-open: no stdin, bad JSON, missing transcript → exit 0, no output.
8. Plugin sync: `claude-plugin/scripts/remind_hook.py` byte-identical to
   `timelog/remind_hook.py`.

## Out of scope (future sub-projects)

- **B — escaped-session recovery:** the state file written here (count, ts) is
  the breadcrumb foundation; a sweeper will reconcile orphaned state into log
  entries.
- **C — per-task segmentation:** activity-boundary detection for splitting
  long sessions into multiple entries.
- Debounced/escalating reminders (explicitly rejected in favor of single-fire).
