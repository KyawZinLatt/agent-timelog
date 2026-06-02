import datetime

from timelog.core import extract_markers, is_valid_entry, has_skip, select_new_entries, sanitize_token, format_duration, build_entry
from timelog.core import infer_category, compose_subagent_summary, compose_session_summary
from timelog.core import entry_summary, is_lazy_summary, filter_quality, skip_exempts_block
from timelog.core import correct_entry_date, normalize_scope_token, normalize_scope, dominant_subdir


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


# --- enforce path: summary extraction + lazy-marker quality gate (#2/#3) ---

VALID = "2026-06-02 09:40Z–09:41Z | ops · dev-server | started Vite dev server, verified port | 1m"


def test_entry_summary_extracts_third_field():
    assert entry_summary(VALID) == "started Vite dev server, verified port"


def test_entry_summary_empty_for_unparseable():
    assert entry_summary("not a canonical line") == ""


def test_is_lazy_summary_flags_tool_call_suffix():
    # the synthesis tell: "(N tool calls)"
    assert is_lazy_summary("ran 3 commands (3 tool calls)")
    assert is_lazy_summary("edited a.py, b.py (7 tool calls)")


def test_is_lazy_summary_flags_synthesis_bodies():
    assert is_lazy_summary("auto-logged Stop, 5 tool calls")
    assert is_lazy_summary("auto-logged SubagentStop, 9 tool calls")
    assert is_lazy_summary("ran 3 commands")
    assert is_lazy_summary("read/searched 4 files")


def test_is_lazy_summary_flags_empty_and_whitespace():
    assert is_lazy_summary("")
    assert is_lazy_summary("   ")
    assert is_lazy_summary("\t\n")


def test_is_lazy_summary_flags_generic_and_short():
    assert is_lazy_summary("auto")
    assert is_lazy_summary("work")
    assert is_lazy_summary("done")
    assert is_lazy_summary("x")  # too short


def test_is_lazy_summary_accepts_real_descriptions():
    assert not is_lazy_summary("started Vite dev server, verified port")
    assert not is_lazy_summary("fixed token expiry off-by-one in auth middleware")
    assert not is_lazy_summary("answered question on enforcement design")


def test_filter_quality_keeps_only_valid_nonlazy_collapsed():
    lazy = "<n>".replace("<n>", "2026-06-02 09:40Z–09:41Z | ops · dev-server | ran 3 commands (3 tool calls) | 1m")
    out = filter_quality([VALID, lazy, "garbage", "  " + VALID + "  "])
    # VALID kept (collapsed, deduped not required here), lazy + garbage dropped
    assert out == [VALID, VALID]


def test_skip_exempts_block_for_low_activity():
    # A SKIP on a quiet session (tick / accidental start) is honored silently.
    assert skip_exempts_block(0, 5) is True
    assert skip_exempts_block(3, 5) is True
    assert skip_exempts_block(5, 5) is True  # boundary is inclusive


def test_skip_does_not_exempt_high_activity():
    # A SKIP above the trust ceiling likely discards real work → not exempt.
    assert skip_exempts_block(6, 5) is False
    assert skip_exempts_block(40, 5) is False


def test_skip_exempts_block_respects_custom_ceiling():
    assert skip_exempts_block(4, 2) is False
    assert skip_exempts_block(2, 2) is True


# --- A. date correction (kills the wrong-year bug) ---

TODAY = datetime.date(2026, 6, 2)


def test_correct_date_rewrites_wrong_year():
    # the production defect: subagent dated 2025 when it was 2026
    bad = "2025-06-02 09:00Z–09:20Z | feature · ws | real work here | 20m"
    fixed, old = correct_entry_date(bad, TODAY)
    assert old == "2025-06-02"
    assert fixed == "2026-06-02 09:00Z–09:20Z | feature · ws | real work here | 20m"


def test_correct_date_accepts_today():
    e = "2026-06-02 09:00Z–09:20Z | feature · ws | real work here | 20m"
    assert correct_entry_date(e, TODAY) == (e, None)


def test_correct_date_accepts_yesterday_for_midnight_crossing():
    e = "2026-06-01 23:50Z–00:10Z | feature · ws | crossed midnight | 20m"
    assert correct_entry_date(e, TODAY) == (e, None)


def test_correct_date_rewrites_future():
    bad = "2026-06-05 09:00Z–09:20Z | feature · ws | real work here | 20m"
    fixed, old = correct_entry_date(bad, TODAY)
    assert old == "2026-06-05"
    assert fixed.startswith("2026-06-02 ")


def test_correct_date_rewrites_two_days_stale():
    bad = "2026-05-31 09:00Z–09:20Z | feature · ws | real work here | 20m"
    fixed, old = correct_entry_date(bad, TODAY)
    assert old == "2026-05-31"
    assert fixed.startswith("2026-06-02 ")


def test_correct_date_rewrites_impossible_date():
    # shape passes ENTRY_RE (\d{4}-\d{2}-\d{2}) but is not a real calendar date
    bad = "2026-13-40 09:00Z–09:20Z | feature · ws | real work here | 20m"
    fixed, old = correct_entry_date(bad, TODAY)
    assert old == "2026-13-40"
    assert fixed.startswith("2026-06-02 ")


def test_correct_date_leaves_noncanonical_untouched():
    assert correct_entry_date("not an entry", TODAY) == ("not an entry", None)


# --- B. scope normalization (enforce workspace-repo) ---

SLUG = "dev-server-setup"


def test_normalize_scope_token_prepends_bare_repo():
    assert normalize_scope_token("backend", SLUG) == "dev-server-setup-backend"


def test_normalize_scope_token_prepends_workspace_literal():
    assert normalize_scope_token("workspace", SLUG) == "dev-server-setup-workspace"


def test_normalize_scope_token_passthrough_when_already_prefixed():
    assert normalize_scope_token("dev-server-setup-backend", SLUG) == "dev-server-setup-backend"


def test_normalize_scope_token_passthrough_when_equals_slug():
    assert normalize_scope_token("dev-server-setup", SLUG) == "dev-server-setup"


def test_normalize_scope_token_boundary_not_bare_prefix():
    # slug "dev" must not swallow an unrelated scope "developer"
    assert normalize_scope_token("developer", "dev") == "dev-developer"


def test_normalize_scope_token_repo_suffix_only_on_bare_slug():
    # bare slug + a dominant subdir -> append; explicit suffix left alone
    assert normalize_scope_token("dev-server-setup", SLUG, repo_suffix="api") == "dev-server-setup-api"
    assert normalize_scope_token("backend", SLUG, repo_suffix="api") == "dev-server-setup-backend"


def test_normalize_scope_token_repo_suffix_skips_when_equal_to_slug():
    assert normalize_scope_token("dev-server-setup", SLUG, repo_suffix="dev-server-setup") == "dev-server-setup"


def test_normalize_scope_rewrites_entry_scope_only():
    e = "2026-06-02 09:00Z–09:20Z | feature · backend | real work here | 20m"
    out = normalize_scope(e, SLUG)
    assert out == "2026-06-02 09:00Z–09:20Z | feature · dev-server-setup-backend | real work here | 20m"


def test_normalize_scope_subagent_token():
    e = "2026-06-02 09:00Z–09:20Z | research · subagent | did research | 20m"
    out = normalize_scope(e, "ws")
    assert "· ws-subagent |" in out


def test_normalize_scope_leaves_noncanonical_untouched():
    assert normalize_scope("garbage line", SLUG) == "garbage line"


def test_dominant_subdir_single_shared():
    assert dominant_subdir(["timelog", "timelog"]) == "timelog"


def test_dominant_subdir_ambiguous_when_mixed():
    assert dominant_subdir(["timelog", "tests"]) is None


def test_dominant_subdir_none_when_root_present():
    assert dominant_subdir(["timelog", ""]) is None


def test_dominant_subdir_none_when_empty():
    assert dominant_subdir([]) is None


def test_dominant_subdir_slugifies():
    assert dominant_subdir(["My Dir", "My Dir"]) == "my-dir"
