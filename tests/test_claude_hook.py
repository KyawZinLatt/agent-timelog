import json
import os
import subprocess
import sys

from timelog.claude_hook import resolve_workspace, read_existing_entries, scan_transcript

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_resolve_workspace_prefers_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert resolve_workspace("/some/cwd") == str(tmp_path)


def test_resolve_workspace_falls_back_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert resolve_workspace(str(tmp_path)) == str(tmp_path)


def test_resolve_workspace_empty_when_neither(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert resolve_workspace("/nonexistent/path/xyz") == ""


def test_read_existing_skips_header_and_blanks(tmp_path):
    f = tmp_path / ".time-log.md"
    f.write_text("# Time log\n\n## Entries\n\n2026-05-26 09:00Z–09:20Z | x · y | z | 20m\n", encoding="utf-8")
    assert read_existing_entries(str(f)) == {"2026-05-26 09:00Z–09:20Z | x · y | z | 20m"}


def test_read_existing_tolerates_legacy_cp1252(tmp_path):
    # File written by the old pre-UTF-8 hook: en-dash 0x96, middle-dot 0xB7 (cp1252).
    f = tmp_path / ".time-log.md"
    f.write_text(
        "# Time log\n\n## Entries\n\n2026-05-26 09:00Z–09:20Z | x · y | z | 20m\n",
        encoding="cp1252",
    )
    assert read_existing_entries(str(f)) == {"2026-05-26 09:00Z–09:20Z | x · y | z | 20m"}


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_scan_collects_text_and_counts_tools(tmp_path):
    rows = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "hello <time-log>X</time-log>"},
            {"type": "tool_use", "name": "Bash"},
        ]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "ignored"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit"},
        ]}},
    ]
    f = tmp_path / "t.jsonl"
    _write_jsonl(f, rows)
    text, tool_count, _first, _last = scan_transcript(str(f))
    assert "hello <time-log>X</time-log>" in text
    assert "ignored" not in text
    assert tool_count == 2


def test_scan_missing_file_returns_empty():
    assert scan_transcript("/nope/missing.jsonl") == ("", 0, None, None)


def test_scan_captures_first_and_last_timestamps(tmp_path):
    rows = [
        {"type": "assistant", "timestamp": "2026-05-29T05:30:00.000Z",
         "message": {"content": [{"type": "text", "text": "a"}, {"type": "tool_use", "name": "Bash"}]}},
        {"type": "assistant", "timestamp": "2026-05-29T05:50:00.000Z",
         "message": {"content": [{"type": "text", "text": "b"}]}},
    ]
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    _text, n, first, last = scan_transcript(str(f))
    assert n == 1
    assert first.hour == 5 and first.minute == 30
    assert last.minute == 50


def _run_hook(stdin_obj, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONPATH"] = REPO
    return subprocess.run(
        [sys.executable, "-m", "timelog.claude_hook"],
        input=json.dumps(stdin_obj),
        text=True, capture_output=True, env=env, cwd=REPO,
    )


def _transcript(tmp_path, assistant_text, tool_uses=0):
    content = [{"type": "text", "text": assistant_text}]
    for _ in range(tool_uses):
        content.append({"type": "tool_use", "name": "Bash"})
    rows = [{"type": "assistant", "message": {"content": content}}]
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(f)


def _subagent_transcript(tmp_path, dispatch, tools,
                         ts=("2026-05-29T05:30:00.000Z", "2026-05-29T05:50:00.000Z")):
    """Subagent transcript: leading user dispatch row + assistant row with named tools."""
    user_row = {"type": "user", "timestamp": ts[0], "message": {"content": dispatch}}
    content = [{"type": "text", "text": "subagent did work, no marker"}]
    for name in tools:
        content.append({"type": "tool_use", "name": name})
    asst_row = {"type": "assistant", "timestamp": ts[1], "message": {"content": content}}
    f = tmp_path / "sub.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [user_row, asst_row]), encoding="utf-8")
    return str(f)


def test_valid_marker_is_appended_and_passes(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    marker = "<time-log>2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m</time-log>"
    tpath = _transcript(tmp_path, marker, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert "did thing | 20m" in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_already_logged_marker_dedups_and_passes(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    entry = "2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m"
    (ws / ".time-log.md").write_text("# Time log\n\n## Entries\n\n" + entry + "\n", encoding="utf-8")
    marker = "<time-log>" + entry + "</time-log>"
    tpath = _transcript(tmp_path, marker, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert (ws / ".time-log.md").read_text(encoding="utf-8").count(entry) == 1


def test_scan_skips_malformed_jsonl_line(tmp_path):
    f = tmp_path / "t.jsonl"
    good1 = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "first"}, {"type": "tool_use", "name": "Bash"}]}})
    good2 = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit"}]}})
    f.write_text(good1 + "\nthis is not json\n" + good2 + "\n", encoding="utf-8")
    text, tool_count, _f, _l = scan_transcript(str(f))
    assert "first" in text
    assert tool_count == 2


def test_subagent_stop_logs_marker_without_enforcing(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    marker = "<time-log>2026-05-26 09:00Z–09:20Z | feature · backend | sub work | 20m</time-log>"
    tpath = _transcript(tmp_path, marker, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "SubagentStop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert "sub work | 20m" in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_work_without_marker_synthesizes(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "did work but emitted no marker", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| auto · ws |" in content
    assert "tool calls" in content


def test_skip_marker_suppresses_synthesis(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "<time-log>SKIP: only Q&A</time-log>", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert "auto ·" not in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_zero_tools_logs_nothing(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "just chatting", tool_uses=0)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert "auto ·" not in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_synthesize_disabled_logs_nothing(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "work no marker", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_SYNTHESIZE": "0"})
    assert r.returncode == 0, r.stderr
    assert "auto ·" not in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_subagent_stop_synthesizes(tmp_path):
    # SubagentStop without agent_type → generic auto fallback (legacy behavior)
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "subagent work no marker", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "SubagentStop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert "auto · ws |" in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_subagent_rich_summary_uses_dispatch_and_agent_type(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _subagent_transcript(
        tmp_path, "Research the SubagentStop hook schema and report fields", ["WebFetch"] * 5)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws),
                   "hook_event_name": "SubagentStop", "agent_type": "claude-code-guide"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| research · subagent |" in content
    assert "claude-code-guide" in content
    assert "Research the SubagentStop" in content
    assert "auto · ws" not in content


def test_subagent_category_feature_when_writes(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _subagent_transcript(
        tmp_path, "Implement the parser change", ["Edit", "Edit", "Bash"])
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws),
                   "hook_event_name": "SubagentStop", "agent_type": "general-purpose"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert "| feature · subagent |" in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_stop_event_ignores_subagent_path(tmp_path):
    # agent_type on a Stop event must NOT trigger the subagent path
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _subagent_transcript(tmp_path, "some dispatch", ["WebFetch"] * 3)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws),
                   "hook_event_name": "Stop", "agent_type": "claude-code-guide"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| auto · ws |" in content
    assert "subagent" not in content


def test_systemmessage_echoes_logged_marker(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    marker = "<time-log>2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m</time-log>"
    tpath = _transcript(tmp_path, marker, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "did thing | 20m" in out["systemMessage"]


def test_systemmessage_echoes_synthesized_entry(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "did work but emitted no marker", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "auto · ws |" in out["systemMessage"]


def test_no_stdout_when_nothing_logged(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "just chatting", tool_uses=0)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_no_stdout_when_marker_dedups(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    entry = "2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m"
    (ws / ".time-log.md").write_text("# Time log\n\n## Entries\n\n" + entry + "\n", encoding="utf-8")
    marker = "<time-log>" + entry + "</time-log>"
    tpath = _transcript(tmp_path, marker, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_synthesis_uses_transcript_timestamps_for_duration(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    rows = [
        {"type": "assistant", "timestamp": "2026-05-29T05:30:00.000Z",
         "message": {"content": [{"type": "text", "text": "start, no marker"}]
                     + [{"type": "tool_use", "name": "Bash"} for _ in range(6)]}},
        {"type": "assistant", "timestamp": "2026-05-29T05:50:00.000Z",
         "message": {"content": [{"type": "text", "text": "end"}]}},
    ]
    tpath = tmp_path / "ts.jsonl"
    tpath.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    r = _run_hook({"transcript_path": str(tpath), "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| auto · ws |" in content          # middle-dot scope
    assert "| 20m" in content                        # 05:30 -> 05:50 = 20 minutes
    assert "05:30Z–05:50Z" in content           # en-dash time range
