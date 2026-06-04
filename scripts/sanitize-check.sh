#!/usr/bin/env bash
# Scan tracked files for private info before publishing. Exit 1 on any hit.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

PATTERNS=(
  '/Users/[a-z]'
  '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'
  '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
  'BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY'
  'AKIA[0-9A-Z]{16}'
  'xox[baprs]-'
  'ghp_[0-9A-Za-z]{36}'
)

fail=0
files=$(git ls-files)
for pat in "${PATTERNS[@]}"; do
  hits=$(printf '%s\n' "$files" | xargs grep -InE "$pat" 2>/dev/null || true)
  if [ -n "$hits" ]; then
    echo "DENYLIST HIT: /$pat/"
    echo "$hits"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "" >&2
  echo "FAIL: private info found in tracked files. Scrub before publishing." >&2
  exit 1
fi
echo "PASS: no private info in tracked files."
