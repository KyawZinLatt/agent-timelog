"""
Test that plugin copies are byte-identical to their timelog/ sources.

The plugin keeps its own copies so it can be installed standalone.
claude_hook.py is intentionally patched (sibling import), so it is not checked.
core.py and remind_hook.py must be byte-identical.
"""

import filecmp
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_plugin_core_matches_source():
    assert filecmp.cmp(
        os.path.join(REPO, "timelog/core.py"),
        os.path.join(REPO, "claude-plugin/scripts/core.py"),
        shallow=False,
    ), "claude-plugin/scripts/core.py drifted from timelog/core.py — re-copy to sync"


def test_plugin_remind_hook_matches_source():
    # remind_hook.py uses a try/except dual-layout import, so BOTH copies are
    # byte-identical (unlike claude_hook.py, which is intentionally patched).
    assert filecmp.cmp(
        os.path.join(REPO, "timelog/remind_hook.py"),
        os.path.join(REPO, "claude-plugin/scripts/remind_hook.py"),
        shallow=False,
    ), "claude-plugin/scripts/remind_hook.py drifted from timelog/remind_hook.py — re-copy to sync"
