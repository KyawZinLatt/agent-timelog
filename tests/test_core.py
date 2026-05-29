from timelog.core import extract_markers, is_valid_entry, has_skip


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


from timelog.core import select_new_entries

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
