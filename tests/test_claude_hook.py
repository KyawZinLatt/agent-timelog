from timelog.claude_hook import resolve_workspace, read_existing_entries


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
