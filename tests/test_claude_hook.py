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
    # Drop any TIMELOG_* inherited from the dev shell so each test controls its
    # own knobs; otherwise e.g. an exported TIMELOG_DEST=both breaks isolation.
    env = {k: v for k, v in os.environ.items() if not k.startswith("TIMELOG_")}
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


def _session_transcript(tmp_path, tools,
                        ts=("2026-05-29T05:30:00.000Z", "2026-05-29T05:50:00.000Z")):
    """Main-session transcript: assistant rows with named tool_use blocks.

    `tools` is a list of (name, input_dict) so write tools can carry file_path.
    """
    content = [{"type": "text", "text": "did work, emitted no marker"}]
    for name, inp in tools:
        block = {"type": "tool_use", "name": name}
        if inp:
            block["input"] = inp
        content.append(block)
    rows = [
        {"type": "assistant", "timestamp": ts[0], "message": {"content": content}},
        {"type": "assistant", "timestamp": ts[1], "message": {"content": [{"type": "text", "text": "end"}]}},
    ]
    f = tmp_path / "sess.jsonl"
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
    # Bash-only session → ops category + meaningful "ran N commands" summary (A3)
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "did work but emitted no marker", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| ops · ws |" in content
    assert "ran 6 commands" in content
    assert "tool calls" in content


def test_skip_marker_suppresses_synthesis(tmp_path):
    # Isolate the synthesis path: with enforce on, a 6-tool SKIP would now be
    # *challenged* (see test_enforce_challenges_skip_on_high_activity). Here we
    # assert only that a SKIP suppresses synthesis, so disable enforce.
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "<time-log>SKIP: only Q&A</time-log>", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
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
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_SYNTHESIZE": "0", "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    assert "auto ·" not in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_subagent_stop_synthesizes(tmp_path):
    # SubagentStop without agent_type → main synthesis path (now activity-aware): Bash → ops
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "subagent work no marker", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "SubagentStop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert "ops · ws |" in (ws / ".time-log.md").read_text(encoding="utf-8")


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
    # agent_type on a Stop event must NOT trigger the subagent path; main path is
    # activity-aware (WebFetch → research · ws), never the subagent scope.
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _subagent_transcript(tmp_path, "some dispatch", ["WebFetch"] * 3)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws),
                   "hook_event_name": "Stop", "agent_type": "claude-code-guide"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| research · ws |" in content
    assert "subagent" not in content


def test_main_synth_feature_when_edits(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _session_transcript(tmp_path, [
        ("Edit", {"file_path": "/repo/core.py"}),
        ("Edit", {"file_path": "/repo/hook.py"}),
        ("Bash", {"command": "pytest"}),
    ])
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| feature · ws |" in content
    assert "edited core.py, hook.py" in content
    assert "(3 tool calls)" in content


def test_main_synth_research_when_reads(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _session_transcript(tmp_path, [
        ("Read", {"file_path": "/a"}), ("Grep", {}), ("Read", {"file_path": "/b"})])
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| research · ws |" in content
    assert "read/searched 3 files" in content


def test_main_synth_falls_back_to_auto_for_unknown_tools(tmp_path):
    # No write/ops/research tools → generic auto line preserved
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _session_transcript(tmp_path, [("TodoWrite", {}), ("TodoWrite", {})])
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| auto · ws |" in content
    assert "auto-logged Stop, 2 tool calls" in content


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
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "ops · ws |" in out["systemMessage"]


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


# --- Configurable log destination (TIMELOG_DEST: local | global | both) ---

_MARKER = "<time-log>2026-05-26 09:00Z–09:20Z | refactor · workspace | dest thing | 20m</time-log>"
_ENTRY = "2026-05-26 09:00Z–09:20Z | refactor · workspace | dest thing | 20m"


def test_dest_default_is_local_only(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    gpath = tmp_path / "global" / ".time-log.md"; gpath.parent.mkdir()
    tpath = _transcript(tmp_path, _MARKER, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_GLOBAL_PATH": str(gpath)})
    assert r.returncode == 0, r.stderr
    assert _ENTRY in (ws / ".time-log.md").read_text(encoding="utf-8")
    assert not gpath.exists()


def test_dest_global_only(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    gpath = tmp_path / "global" / ".time-log.md"; gpath.parent.mkdir()
    tpath = _transcript(tmp_path, _MARKER, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_DEST": "global",
                   "TIMELOG_GLOBAL_PATH": str(gpath)})
    assert r.returncode == 0, r.stderr
    assert _ENTRY in gpath.read_text(encoding="utf-8")
    assert not (ws / ".time-log.md").exists()


def test_dest_both_writes_each(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    gpath = tmp_path / "global" / ".time-log.md"; gpath.parent.mkdir()
    tpath = _transcript(tmp_path, _MARKER, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_DEST": "both",
                   "TIMELOG_GLOBAL_PATH": str(gpath)})
    assert r.returncode == 0, r.stderr
    assert _ENTRY in (ws / ".time-log.md").read_text(encoding="utf-8")
    assert _ENTRY in gpath.read_text(encoding="utf-8")


def test_dest_both_dedups_per_file_independently(tmp_path):
    # local already has the entry; global is empty. both mode must leave local
    # at count 1 and still write the entry to global.
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / ".time-log.md").write_text("# Time log\n\n## Entries\n\n" + _ENTRY + "\n", encoding="utf-8")
    gpath = tmp_path / "global" / ".time-log.md"; gpath.parent.mkdir()
    tpath = _transcript(tmp_path, _MARKER, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_DEST": "both",
                   "TIMELOG_GLOBAL_PATH": str(gpath)})
    assert r.returncode == 0, r.stderr
    assert (ws / ".time-log.md").read_text(encoding="utf-8").count(_ENTRY) == 1
    assert gpath.read_text(encoding="utf-8").count(_ENTRY) == 1


def test_dest_both_global_failure_does_not_block_local(tmp_path):
    # global path's parent is a regular file -> makedirs/open fails -> tolerated.
    ws = tmp_path / "ws"; ws.mkdir()
    afile = tmp_path / "afile"; afile.write_text("x", encoding="utf-8")
    gpath = afile / ".time-log.md"  # parent is a file, not a dir
    tpath = _transcript(tmp_path, _MARKER, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_DEST": "both",
                   "TIMELOG_GLOBAL_PATH": str(gpath)})
    assert r.returncode == 0, r.stderr
    assert _ENTRY in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_dest_global_creates_missing_parent_dir(tmp_path):
    # ~/.claude analogue missing -> ensure_log_file makes parents.
    ws = tmp_path / "ws"; ws.mkdir()
    gpath = tmp_path / "nope" / "deep" / ".time-log.md"  # parent dirs do not exist
    tpath = _transcript(tmp_path, _MARKER, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_DEST": "global",
                   "TIMELOG_GLOBAL_PATH": str(gpath)})
    assert r.returncode == 0, r.stderr
    assert _ENTRY in gpath.read_text(encoding="utf-8")


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
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert "| ops · ws |" in content            # Bash-only → ops; middle-dot scope
    assert "| 20m" in content                        # 05:30 -> 05:50 = 20 minutes
    assert "05:30Z–05:50Z" in content           # en-dash time range


def _is_block(stdout):
    """True if the hook emitted a Stop-block decision."""
    stdout = stdout.strip()
    if not stdout:
        return False
    try:
        return json.loads(stdout).get("decision") == "block"
    except ValueError:
        return False


# --- enforce mode: bounded block-then-synthesize (#2) + lazy gate (#3) ---

_QUALITY = ("<time-log>2026-06-02 09:00Z–09:20Z | feature · ws | "
            "implemented real feature work | 20m</time-log>")
_LAZY = ("<time-log>2026-06-02 09:40Z–09:41Z | ops · dev-server | "
         "ran 3 commands (3 tool calls) | 1m</time-log>")
_ENTRY_LAZY = "2026-06-02 09:40Z–09:41Z | ops · dev-server | ran 3 commands (3 tool calls) | 1m"


def test_enforce_blocks_when_no_marker(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "did work, emitted nothing", tool_uses=4)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert "time-log" in out["reason"]
    # nothing logged yet — agent must produce a marker first
    assert not (ws / ".time-log.md").exists()


def test_enforce_passes_quality_marker(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, _QUALITY, tool_uses=4)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    assert not _is_block(r.stdout)
    assert "implemented real feature work | 20m" in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_enforce_blocks_lazy_marker_and_drops_it(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, _LAZY, tool_uses=4)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["decision"] == "block"
    # lazy marker treated as absent → not logged
    assert not (ws / ".time-log.md").exists()


def test_enforce_retry_synthesizes_when_still_missing(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _session_transcript(tmp_path, [("Bash", None), ("Bash", None), ("Bash", None)])
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop",
                   "stop_hook_active": True},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    assert not _is_block(r.stdout)
    # bounded: on retry it falls through to synthesis
    assert "ops · ws" in (ws / ".time-log.md").read_text(encoding="utf-8")


def test_enforce_does_not_block_skip(tmp_path):
    # Low-activity SKIP (4 tools ≤ default ceiling 5) is honored silently.
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "<time-log>SKIP: nothing to record</time-log>", tool_uses=4)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    assert not _is_block(r.stdout)


def test_enforce_challenges_skip_on_high_activity(tmp_path):
    # SKIP above the default ceiling (8 > 5): the session did real work a SKIP
    # would discard → block once and demand a real marker.
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "<time-log>SKIP: nothing to record</time-log>", tool_uses=8)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert "SKIP" in out["reason"]
    # nothing logged — the agent must reconsider first
    assert not (ws / ".time-log.md").exists()


def test_enforce_skip_challenge_is_bounded(tmp_path):
    # On the retry (stop_hook_active) the SKIP is honored: no block, nothing logged.
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "<time-log>SKIP: nothing to record</time-log>", tool_uses=8)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop",
                   "stop_hook_active": True},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    assert not _is_block(r.stdout)
    # SKIP honored on retry → synthesis suppressed, no entry line appended
    content = (ws / ".time-log.md").read_text(encoding="utf-8")
    assert not any(ln.startswith("2026-") for ln in content.splitlines())


def test_enforce_skip_ceiling_is_configurable(tmp_path):
    # Lowering the ceiling to 2 makes a 4-tool SKIP a challenge.
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "<time-log>SKIP: nothing to record</time-log>", tool_uses=4)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1",
                   "TIMELOG_SKIP_MAX_TOOLS": "2"})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["decision"] == "block"


def test_enforce_never_blocks_subagent(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _subagent_transcript(tmp_path, "do a thing", ["Bash", "Bash"])
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "SubagentStop",
                   "agent_type": "worker"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    assert not _is_block(r.stdout)


def test_enforce_never_blocks_precompact(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "did work, no marker", tool_uses=4)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "PreCompact"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    assert not _is_block(r.stdout)


def test_enforce_does_not_block_empty_session(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "no work", tool_uses=0)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "1"})
    assert r.returncode == 0, r.stderr
    assert not _is_block(r.stdout)


def test_default_off_never_blocks_and_synthesizes(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "did work, no marker", tool_uses=4)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    assert not _is_block(r.stdout)
    assert (ws / ".time-log.md").exists()


def test_default_is_enforce_on(tmp_path):
    # Shipped default flipped to on: a working Stop with no marker blocks once,
    # with no TIMELOG_ENFORCE present in the environment.
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "did work, no marker", tool_uses=4)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert _is_block(r.stdout)
    assert not (ws / ".time-log.md").exists()
