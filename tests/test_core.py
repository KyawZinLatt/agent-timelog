from timelog.core import extract_markers, is_valid_entry, has_skip, select_new_entries, sanitize_token, format_duration, build_entry
from timelog.core import infer_category, compose_subagent_summary, compose_session_summary


def test_extract_markers_returns_inner_text():
    text = "noise <time-log>2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m</time-log> noise"
    assert extract_markers(text) == ["2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m"]


def test_extract_markers_multiple():
    text = "<time-log>a</time-log>\n<time-log>b</time-log>"
    assert extract_markers(text) == ["a", "b"]


def test_extract_markers_none():
    assert extract_markers("no markers here") == []


def test_valid_entry_minutes():
    assert is_valid_entry("2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m")


def test_valid_entry_hours_minutes():
    assert is_valid_entry("2026-05-26 03:54Z–08:02Z | monitoring · backend | watched | 4h 8m")


def test_valid_entry_hours_only():
    assert is_valid_entry("2026-05-26 03:00Z–05:00Z | ops · infra | thing | 2h")


def test_invalid_hyphen_not_endash():
    assert not is_valid_entry("2026-05-26 09:00Z-09:20Z | refactor · workspace | x | 20m")


def test_invalid_prose_fragment():
    assert not is_valid_entry("markers automatically and chmod is read-safe")


def test_invalid_missing_duration():
    assert not is_valid_entry("2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing |")


def test_invalid_dot_separator_not_middledot():
    assert not is_valid_entry("2026-05-26 09:00Z–09:20Z | refactor . workspace | x | 20m")


def test_has_skip_true():
    assert has_skip("text <time-log>SKIP: only Q&A this session</time-log> more")


def test_has_skip_requires_reason():
    assert not has_skip("<time-log>SKIP:</time-log>")


def test_has_skip_false_when_absent():
    assert not has_skip("<time-log>2026-05-26 09:00Z–09:20Z | x · y | z | 20m</time-log>")


def test_select_filters_invalid_and_dedups():
    candidates = [
        "2026-05-26 09:00Z–09:20Z | refactor · workspace | a | 20m",
        "prose fragment not an entry",
        "2026-05-26 09:00Z–09:20Z | refactor · workspace | a | 20m",
        "2026-05-26 10:00Z–10:10Z | ops · infra | b | 10m",
    ]
    existing = {"2026-05-26 10:00Z–10:10Z | ops · infra | b | 10m"}
    valid, new = select_new_entries(candidates, existing)
    assert valid == [
        "2026-05-26 09:00Z–09:20Z | refactor · workspace | a | 20m",
        "2026-05-26 09:00Z–09:20Z | refactor · workspace | a | 20m",
        "2026-05-26 10:00Z–10:10Z | ops · infra | b | 10m",
    ]
    assert new == ["2026-05-26 09:00Z–09:20Z | refactor · workspace | a | 20m"]

def test_select_empty():
    assert select_new_entries([], set()) == ([], [])


def test_invalid_summary_contains_pipe_separator():
    # summary must not contain " | " (the field separator)
    assert not is_valid_entry("2026-05-26 09:00Z–09:20Z | refactor · workspace | part a | part b | 20m")

def test_valid_summary_allows_bare_pipe():
    # a bare pipe (no surrounding spaces) is allowed in summary
    assert is_valid_entry("2026-05-26 09:00Z–09:20Z | refactor · workspace | a|b ratio | 20m")


def test_sanitize_token_basic():
    assert sanitize_token("Agent-TimeLog", "x") == "agent-timelog"


def test_sanitize_token_maps_nonalpha_to_hyphen_and_collapses():
    assert sanitize_token("dev_server  setup", "x") == "dev-server-setup"


def test_sanitize_token_fallback_when_no_leading_alpha():
    assert sanitize_token("123", "session") == "session"


def test_format_duration_variants():
    assert format_duration(0) == "1m"
    assert format_duration(20) == "20m"
    assert format_duration(60) == "1h"
    assert format_duration(125) == "2h 5m"


def test_build_entry_passes_validation():
    e = build_entry("2026-05-29", "05:30", "05:50", "auto", "agent-timelog",
                    "auto-logged Stop, 6 tool calls", "20m")
    assert is_valid_entry(e)
    assert "·" in e and "–" in e


def test_infer_category_writes_to_feature():
    assert infer_category({"Edit": 2, "Bash": 5}) == "feature"


def test_infer_category_research_when_read_or_web_only():
    assert infer_category({"WebFetch": 7}) == "research"
    assert infer_category({"Read": 3, "Grep": 2}) == "research"


def test_infer_category_ops_when_bash_dominant():
    assert infer_category({"Bash": 5, "Read": 1}) == "ops"


def test_infer_category_empty_is_auto():
    assert infer_category({}) == "auto"


def test_compose_subagent_summary_has_agent_intent_and_count():
    s = compose_subagent_summary(
        "claude-code-guide",
        "Research the SubagentStop hook schema and report fields.",
        7,
    )
    assert "claude-code-guide" in s
    assert "Research the SubagentStop" in s
    assert "7 tool calls" in s
    assert " | " not in s


def test_compose_subagent_summary_strips_pipe_and_singular_count():
    s = compose_subagent_summary("worker", "do a | b | c thing", 1)
    assert " | " not in s
    assert "1 tool call" in s and "tool calls" not in s


def test_compose_subagent_summary_truncates_long_intent():
    s = compose_subagent_summary("worker", "x" * 500, 3)
    assert len(s) <= 100
    assert s.endswith("(3 tool calls)")


def test_compose_subagent_summary_empty_dispatch_uses_agent_only():
    s = compose_subagent_summary("worker", "", 4)
    assert "worker" in s and "4 tool calls" in s
    assert " | " not in s


def test_compose_session_summary_files_edited():
    s = compose_session_summary(["core.py", "claude_hook.py"], 0, 0, 8)
    assert s == "edited core.py, claude_hook.py (8 tool calls)"
    assert " | " not in s


def test_compose_session_summary_commands_when_bash():
    s = compose_session_summary([], 6, 0, 6)
    assert s == "ran 6 commands (6 tool calls)"


def test_compose_session_summary_single_command_is_singular():
    s = compose_session_summary([], 1, 0, 1)
    assert s == "ran 1 command (1 tool call)"


def test_compose_session_summary_research_when_reads():
    s = compose_session_summary([], 0, 3, 3)
    assert s == "read/searched 3 files (3 tool calls)"


def test_compose_session_summary_files_win_over_bash():
    # writes dominate the summary just as they dominate infer_category
    s = compose_session_summary(["a.py"], 4, 2, 7)
    assert s.startswith("edited a.py")
    assert s.endswith("(7 tool calls)")


def test_compose_session_summary_none_when_unrecognized():
    # no files, no bash, no research → caller falls back to generic auto line
    assert compose_session_summary([], 0, 0, 5) is None


def test_compose_session_summary_strips_pipe_in_filename():
    s = compose_session_summary(["we|rd.py"], 0, 0, 1)
    assert " | " not in s and "we/rd.py" in s


def test_compose_session_summary_truncates_to_budget():
    s = compose_session_summary(["x" * 200 + ".py"], 0, 0, 3)
    assert len(s) <= 100
    assert s.endswith("(3 tool calls)")
