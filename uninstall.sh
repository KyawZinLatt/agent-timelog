#!/usr/bin/env bash
set -euo pipefail

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
HOOK_DIR="$CLAUDE_DIR/hooks/timelog"
SETTINGS="$CLAUDE_DIR/settings.json"
RULE_FILE="$CLAUDE_DIR/CLAUDE.md"

echo "Removing agent-timelog from $CLAUDE_DIR ..."

# 1. Remove hook files
rm -rf "$HOOK_DIR"
echo "  hook dir removed"

# 2. Remove /log command
rm -f "$CLAUDE_DIR/commands/log.md"
echo "  /log command removed"

# 3. Strip hook entries from settings.json for all three events
python3 - "$SETTINGS" "$HOOK_DIR" <<'PY'
import json, os, sys

settings_path, hook_dir = sys.argv[1], sys.argv[2]
if not os.path.exists(settings_path):
    sys.exit(0)

with open(settings_path) as f:
    try:
        data = json.load(f)
    except json.JSONDecodeError:
        sys.exit(0)

changed = False
for event in ("Stop", "PreCompact", "SubagentStop", "PostToolUse"):
    arr = data.get("hooks", {}).get(event, [])
    filtered = [
        b for b in arr
        if not any(hook_dir in h.get("command", "") for h in b.get("hooks", []))
    ]
    if len(filtered) != len(arr):
        changed = True
        if filtered:
            data["hooks"][event] = filtered
        else:
            data["hooks"].pop(event, None)

# Clean up empty hooks dict
if "hooks" in data and not data["hooks"]:
    del data["hooks"]

if changed:
    with open(settings_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print("  settings.json hook entries removed")
else:
    print("  settings.json: no matching entries found")
PY

# 4. Strip the rule block between marker comments
python3 - "$RULE_FILE" <<'PY'
import os, re, sys

rule_file = sys.argv[1]
if not os.path.exists(rule_file):
    sys.exit(0)

with open(rule_file) as f:
    content = f.read()

stripped = re.sub(
    r"\n?<!-- agent-timelog:begin -->.*?<!-- agent-timelog:end -->\n?",
    "",
    content,
    flags=re.DOTALL,
)

if stripped != content:
    with open(rule_file, "w") as f:
        f.write(stripped)
    print("  rule block stripped from CLAUDE.md")
else:
    print("  CLAUDE.md: no rule block found")
PY

echo "agent-timelog removed. .time-log.md data files are left untouched."
