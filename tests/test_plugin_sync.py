"""
Test that claude-plugin/scripts/core.py is byte-identical to timelog/core.py.

The plugin keeps its own copy of core.py so it can be installed standalone.
claude_hook.py is intentionally patched (sibling import), so only core.py is checked.
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
