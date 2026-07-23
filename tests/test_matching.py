from datetime import datetime, timezone

from plex_home import matching as m


def dt(year=2026, month=7, day=23):
    return datetime(year, month, day, tzinfo=timezone.utc)


# --- expand_variables ---

def test_expand_year():
    assert m.expand_variables("Oscars Death Race {YEAR}", dt(2026)) == "Oscars Death Race 2026"


def test_expand_month_day_zero_padded():
    assert m.expand_variables("{MONTH}-{DAY}", dt(2026, 7, 3)) == "07-03"


def test_expand_week_iso():
    assert m.expand_variables("W{WEEK}", dt(2026, 7, 23)) == "W30"


def test_expand_all_tokens_together():
    assert m.expand_variables("{YEAR}/{MONTH}/{DAY}", dt(2026, 1, 5)) == "2026/01/05"


def test_expand_leaves_unknown_braces_alone():
    assert m.expand_variables("Top {GENRE}", dt()) == "Top {GENRE}"


def test_expand_preserves_sigil():
    assert m.expand_variables("re:Oscars {YEAR}", dt(2026)) == "re:Oscars 2026"


# --- parse_spec ---

def test_parse_bare_is_exact():
    assert m.parse_spec("Marvel Movies") == ("exact", "Marvel Movies")


def test_parse_glob():
    assert m.parse_spec("glob:Marvel *") == ("glob", "Marvel *")


def test_parse_regex():
    assert m.parse_spec(r"re:Oscars \d{4}") == ("regex", r"Oscars \d{4}")


# --- title_matches: exact ---

def test_exact_matches_equal():
    assert m.title_matches("Marvel Movies", "Marvel Movies")


def test_exact_rejects_substring():
    assert not m.title_matches("Marvel", "Marvel Movies")


def test_exact_is_case_sensitive():
    assert not m.title_matches("marvel movies", "Marvel Movies")


# --- title_matches: glob ---

def test_glob_star_matches_whole():
    assert m.title_matches("glob:Marvel *", "Marvel Movies")


def test_glob_anchored_no_leading_match():
    assert not m.title_matches("glob:Marvel *", "The Marvel Show")


def test_glob_case_sensitive():
    assert not m.title_matches("glob:marvel *", "Marvel Movies")


def test_glob_question_mark():
    assert m.title_matches("glob:Season ?", "Season 3")
    assert not m.title_matches("glob:Season ?", "Season 12")


# --- title_matches: regex ---

def test_regex_fullmatch_year():
    assert m.title_matches(r"re:Oscars Death Race \d{4}", "Oscars Death Race 2026")


def test_regex_is_fullmatch_not_search():
    assert not m.title_matches(r"re:Oscars \d{4}", "The Oscars 2026 Special")


def test_regex_alternation():
    assert m.title_matches("re:Marvel|DC", "DC")
    assert not m.title_matches("re:Marvel|DC", "Marvel Movies")
