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
    f.write_text("# Time log\n\n## Entries\n\n2026-05-26 09:00Z–09:20Z | x · y | z | 20m\n")
    assert read_existing_entries(str(f)) == {"2026-05-26 09:00Z–09:20Z | x · y | z | 20m"}


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows))


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
    text, tool_count = scan_transcript(str(f))
    assert "hello <time-log>X</time-log>" in text
    assert "ignored" not in text
    assert tool_count == 2


def test_scan_missing_file_returns_empty():
    assert scan_transcript("/nope/missing.jsonl") == ("", 0)


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
    f.write_text("\n".join(json.dumps(r) for r in rows))
    return str(f)


def test_valid_marker_is_appended_and_passes(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    marker = "<time-log>2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m</time-log>"
    tpath = _transcript(tmp_path, marker, tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr
    assert "did thing | 20m" in (ws / ".time-log.md").read_text()


def test_work_without_marker_blocks(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "did lots of work but no marker", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 2
    assert "BLOCKED" in r.stderr


def test_skip_marker_bypasses_enforcement(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "<time-log>SKIP: only Q&A</time-log>", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr


def test_below_threshold_passes_without_marker(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "tiny session", tool_uses=2)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws)})
    assert r.returncode == 0, r.stderr


def test_enforce_disabled_never_blocks(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tpath = _transcript(tmp_path, "work no marker", tool_uses=6)
    r = _run_hook({"transcript_path": tpath, "cwd": str(ws), "hook_event_name": "Stop"},
                  {"CLAUDE_PROJECT_DIR": str(ws), "TIMELOG_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
