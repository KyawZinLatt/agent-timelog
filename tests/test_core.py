from timelog.core import extract_markers


def test_extract_markers_returns_inner_text():
    text = "noise <time-log>2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m</time-log> noise"
    assert extract_markers(text) == ["2026-05-26 09:00Z–09:20Z | refactor · workspace | did thing | 20m"]


def test_extract_markers_multiple():
    text = "<time-log>a</time-log>\n<time-log>b</time-log>"
    assert extract_markers(text) == ["a", "b"]


def test_extract_markers_none():
    assert extract_markers("no markers here") == []
