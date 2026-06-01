#!/usr/bin/env bash
set -euo pipefail

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
HOOK_DIR="$CLAUDE_DIR/hooks/timelog"
CMD_DIR="$CLAUDE_DIR/commands"
SETTINGS="$CLAUDE_DIR/settings.json"
RULE_FILE="$CLAUDE_DIR/CLAUDE.md"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$CLAUDE_DIR" ]; then
  echo "error: $CLAUDE_DIR not found. Is Claude Code installed?" >&2
  exit 1
fi

echo "Installing agent-timelog into $CLAUDE_DIR ..."

# 1. Python files
mkdir -p "$HOOK_DIR"
cp "$SRC/timelog/__init__.py" "$HOOK_DIR/__init__.py"
cp "$SRC/timelog/core.py" "$HOOK_DIR/core.py"
cp "$SRC/timelog/claude_hook.py" "$HOOK_DIR/claude_hook.py"
chmod +x "$HOOK_DIR/claude_hook.py"

# claude_hook.py uses `from timelog import core`; flat-copy needs sibling import
# Replace the package import with a sys.path.insert + bare import so the script
# locates core.py in the same directory regardless of the working directory
# when the hook fires.
python3 - "$HOOK_DIR/claude_hook.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
old = "from timelog import core"
new = "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport core"
patched = s.replace(old, new)
open(p, "w").write(patched)
PY

# 2. /log command
mkdir -p "$CMD_DIR"
cp "$SRC/adapters/claude-code/commands/log.md" "$CMD_DIR/log.md"

# 3. settings.json hook merge (idempotent — skips if entry already present)
HOOK_CMD="python3 \"$HOOK_DIR/claude_hook.py\""
python3 - "$SETTINGS" "$HOOK_CMD" <<'PY'
import json, os, sys

settings_path, hook_cmd = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(settings_path):
    with open(settings_path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = {}

hooks = data.setdefault("hooks", {})

def ensure(event):
    arr = hooks.setdefault(event, [])
    for block in arr:
        for h in block.get("hooks", []):
            if h.get("command") == hook_cmd:
                print(f"  {event}: already registered — skipping")
                return
    arr.append({"hooks": [{"type": "command", "command": hook_cmd}]})
    print(f"  {event}: registered")

ensure("Stop")
ensure("PreCompact")
ensure("SubagentStop")

with open(settings_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY

# 4. Rule injection (idempotent via HTML comment markers).
# If the markers already exist, refresh the block in place so rule-text edits
# propagate on reinstall; otherwise append a fresh block.
RULE_BEGIN="<!-- agent-timelog:begin -->"
RULE_END="<!-- agent-timelog:end -->"
python3 - "$RULE_FILE" "$SRC/rules/time-tracking.md" "$RULE_BEGIN" "$RULE_END" <<'PY'
import os, re, sys

rule_file, rule_src, begin, end = sys.argv[1:5]
body = open(rule_src).read().rstrip("\n")
block = f"{begin}\n{body}\n{end}"

existing = open(rule_file).read() if os.path.exists(rule_file) else ""

if begin in existing and end in existing:
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    updated = pattern.sub(lambda _m: block, existing, count=1)
    if updated == existing:
        print(f"  rule already current in {rule_file}")
    else:
        open(rule_file, "w").write(updated)
        print(f"  rule refreshed in {rule_file}")
else:
    sep = "" if existing.endswith("\n") or existing == "" else "\n"
    open(rule_file, "a").write(f"{sep}\n{block}\n")
    print(f"  rule appended to {rule_file}")
PY

echo "Done. Start a new Claude Code session to activate."
