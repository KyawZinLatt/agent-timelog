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
s = open(p, encoding="utf-8").read()
old = "from timelog import core"
new = "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport core"
patched = s.replace(old, new)
open(p, "w", encoding="utf-8").write(patched)
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
    with open(settings_path, encoding="utf-8") as f:
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

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY

# 4. Rule append (idempotent via HTML comment markers)
RULE_BEGIN="<!-- agent-timelog:begin -->"
RULE_END="<!-- agent-timelog:end -->"
if [ -f "$RULE_FILE" ] && grep -qF "$RULE_BEGIN" "$RULE_FILE"; then
  echo "rule already present in $RULE_FILE — skipping"
else
  {
    echo ""
    echo "$RULE_BEGIN"
    cat "$SRC/rules/time-tracking.md"
    echo "$RULE_END"
  } >> "$RULE_FILE"
  echo "rule appended to $RULE_FILE"
fi

echo "Done. Start a new Claude Code session to activate."
