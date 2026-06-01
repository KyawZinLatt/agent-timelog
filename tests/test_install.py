import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SH = os.path.join(REPO, "install.sh")
RULE_SRC = os.path.join(REPO, "rules", "time-tracking.md")
BEGIN = "<!-- agent-timelog:begin -->"
END = "<!-- agent-timelog:end -->"


def _run_install(claude_dir):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(claude_dir))
    return subprocess.run(
        ["bash", INSTALL_SH],
        env=env,
        capture_output=True,
        text=True,
    )


def _between_markers(text):
    start = text.index(BEGIN) + len(BEGIN)
    end = text.index(END)
    return text[start:end]


def test_first_install_appends_rule_block(tmp_path):
    rule_file = tmp_path / "CLAUDE.md"
    rule_file.write_text("# My rules\n\nSome existing guidance.\n")

    r = _run_install(tmp_path)
    assert r.returncode == 0, r.stderr

    out = rule_file.read_text()
    assert out.count(BEGIN) == 1
    assert out.count(END) == 1
    assert "# My rules" in out
    assert open(RULE_SRC).read() in out


def test_reinstall_refreshes_stale_rule_block(tmp_path):
    rule_file = tmp_path / "CLAUDE.md"
    rule_file.write_text(
        "# My rules\n\n"
        f"{BEGIN}\n"
        "OLD STALE RULE TEXT — must be replaced\n"
        f"{END}\n\n"
        "## Trailing section preserved\n"
    )

    r = _run_install(tmp_path)
    assert r.returncode == 0, r.stderr

    out = rule_file.read_text()
    # exactly one marker pair, no duplication
    assert out.count(BEGIN) == 1
    assert out.count(END) == 1
    # stale text gone, current rule text present
    assert "OLD STALE RULE TEXT" not in out
    assert open(RULE_SRC).read().strip() in _between_markers(out)
    # surrounding content preserved
    assert "# My rules" in out
    assert "## Trailing section preserved" in out
