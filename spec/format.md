# agent-timelog Format Specification

Version: 0.1  
Status: Stable  
Scope: Agent-neutral contract. Any adapter (Claude Code, Cursor, Codex, etc.) that writes
to a `.time-log.md` file MUST produce lines conforming to this spec.

---

## 1. Log-file shape

The default destination is one file per workspace: `<workspace-root>/.time-log.md`.  
An adapter MAY also offer a global destination (a single central file) or write to both
the per-workspace file and the global file; the line format below is identical regardless
of destination, and each file dedups independently.  
The file is append-only. Each entry occupies exactly one line.  
The file is gitignored by default; adapters MUST NOT commit it.

---

## 2. Canonical entry format

```
YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration
```

### Fields

| Field | Syntax | Notes |
|-------|--------|-------|
| `date` | `YYYY-MM-DD` | UTC calendar date of the session start |
| `start` | `HH:MMZ` | UTC 24-hour clock, `Z` suffix |
| `–` | U+2013 EN DASH | **Not** a hyphen `-` (U+002D). Required exactly as shown. |
| `end` | `HH:MMZ` | UTC 24-hour clock, `Z` suffix |
| `category` | `[a-z][a-z-]*` | Lowercase letters and hyphens; must start with a letter |
| `·` | U+00B7 MIDDLE DOT | **Not** a period `.`. Required exactly as shown. |
| `scope` | `[a-z][a-z-]*` | Same token rules as category |
| `summary` | any text | MUST NOT contain ` | ` (space + pipe + space) |
| `duration` | `Nm` \| `Nh` \| `Nh Nm` | Examples: `20m`, `2h`, `1h 30m` |

Field separators are ` | ` (space + U+007C VERTICAL LINE + space).

### Suggested category vocabulary

Adapters may extend or restrict this list; the validator only checks token shape.

`bug-fix` · `feature` · `incident` · `investigation` · `ops` · `deploy` · `refactor`
· `docs` · `planning` · `monitoring` · `review` · `research` · `setup` · `comms`
· `cleanup` · `qa` · `auto`

> `auto` is reserved for synthesized (machine-generated) entries.

---

## 3. Validation regex (`ENTRY_RE`)

Adapters MUST use (or be equivalent to) this Python regex to accept or reject a candidate line:

```python
import re

ENTRY_RE = re.compile(
    r"^"
    r"\d{4}-\d{2}-\d{2}"
    r"\s"
    r"\d{2}:\d{2}Z–\d{2}:\d{2}Z"   # en-dash U+2013
    r"\s\|\s"
    r"[a-z][a-z-]*"
    r"\s·\s"                          # middle-dot U+00B7
    r"[a-z][a-z-]*"
    r"\s\|\s"
    r"(?:(?! \| ).)+?"                     # summary — no embedded ` | `
    r"\s\|\s"
    r"(?:\d+h\s\d+m|\d+h|\d+m)"
    r"$"
)
```

Apply after collapsing internal whitespace: `" ".join(entry.split())`.

---

## 4. The `<time-log>` marker

Agents emit entries by placing them inside a marker tag in their response text:

```
<time-log>YYYY-MM-DD HH:MMZ–HH:MMZ | category · scope | summary | duration</time-log>
```

- Multiple markers per response are allowed; each is extracted independently.
- Prose or partial text inside a `<time-log>` tag that fails validation is silently dropped.
- Markers MAY span newlines; the content is stripped before validation.

### SKIP opt-out

An agent that did genuinely nothing to record (a monitoring tick, an accidental start)
may suppress auto-synthesis by emitting the SKIP marker below. Q&A and discussion are
work and should be logged with a real marker, not skipped.

```
<time-log>SKIP: <reason></time-log>
```

The reason MUST be non-empty (at least one non-whitespace character after the colon).

---

## 5. Auto-synthesis (adapter behavior, not wire format)

When no valid marker is emitted and no SKIP is present, a conforming adapter MAY synthesize
an entry automatically. Two variants are defined.

### 5.1 Main-session synthesis (Stop / PreCompact)

`scope` = sanitized workspace basename (fallback: `session`); `duration` derived from
first–last transcript timestamps, minimum 1m. The `category`/`summary` are activity-aware:
the adapter SHOULD derive them from the session's tool-use histogram instead of a generic
line.

- `category` = inferred from the tool-use histogram: any file-mutating tool → `feature`;
  else Bash-dominant → `ops`; else read/search/web → `research`; else `auto`
- `summary`, by dominant activity (` | `-stripped, truncated to budget):
  - files written → `edited <basename>, <basename>, … (<N> tool calls)`
  - else commands run → `ran <K> command(s) (<N> tool calls)`
  - else files read/searched → `read/searched <K> file(s) (<N> tool calls)`

If the activity is unrecognizable (no write/ops/research tools), the adapter MUST fall
back to the generic form:

- `category` = `auto`
- `summary` = `auto-logged <event>, <N> tool calls`  (no ` | `)

### 5.2 Subagent synthesis (SubagentStop)

When the event is a subagent completion and the adapter knows the subagent's type, it
SHOULD derive a meaningful summary from the subagent's own transcript instead of the
generic form:

- `category` = inferred from the subagent's tool-use histogram: any file-mutating tool
  → `feature`; else Bash-dominant → `ops`; else read/search/web → `research`; else `auto`
- `scope` = `subagent`
- `summary` = `<agent-type>: <dispatch-prompt intent> (<N> tool calls)` — derived from the
  subagent's dispatch prompt (its first user turn); truncated and ` | `-stripped
- `duration` derived from first–last transcript timestamps; minimum 1m

If the agent type is unknown or composition yields an invalid line, the adapter MUST fall
back to §5.1.

Both variants MUST pass `ENTRY_RE` before being written.

---

## 6. Deduplication

Before appending, adapters MUST read existing entries and skip any candidate whose
collapsed form already appears in the file. Within a single run, first-seen wins.

---

## 7. Future adapters

This spec is intentionally agent-neutral. The `agent-timelog` repo ships a Claude Code
adapter today; `adapters/` and this `spec/` directory reserve the extension points for
Cursor, Codex, OpenCode, and others.
