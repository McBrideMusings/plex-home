import json
import sqlite3

import pytest
import yaml

from plex_home import main
from plex_home.tagsource import SnapshotTagSource, TagSourceError, normalize_keyword

# The subset of plex-db-ex's documented schema (docs/schema.md) this reader queries.
DDL = """
CREATE TABLE items (item_id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
    title_sort TEXT, show_title TEXT, show_item_id TEXT, season INTEGER, episode INTEGER,
    year INTEGER, duration_ms INTEGER, content_rating TEXT, studio TEXT);
CREATE TABLE plex_items (rating_key TEXT PRIMARY KEY, item_id TEXT NOT NULL,
    section_id TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE enrichment (item_id TEXT NOT NULL, namespace TEXT NOT NULL, source TEXT NOT NULL,
    key TEXT NOT NULL, value TEXT NOT NULL, fetched_at TEXT NOT NULL, rank INTEGER,
    PRIMARY KEY (item_id, namespace, source, key, value));
CREATE TABLE plays (history_key TEXT PRIMARY KEY, item_id TEXT NOT NULL,
    plex_account_id INTEGER NOT NULL, client_identifier TEXT, platform TEXT,
    viewed_at INTEGER NOT NULL, ip TEXT, percent_complete INTEGER, paused_counter INTEGER,
    seconds_watched INTEGER, tautulli_id INTEGER);
CREATE TABLE tag_network_edge (kind TEXT, a TEXT, b TEXT, shared INTEGER, PRIMARY KEY (kind, a, b));
"""


def build_snapshot(path):
    db = sqlite3.connect(path)
    db.executescript(DDL)
    db.executemany("INSERT INTO items (item_id, type, title, year, show_item_id) VALUES (?,?,?,?,?)", [
        ("imdb:heat", "movie", "Heat", 1995, None),
        ("imdb:inception", "movie", "Inception", 2010, None),
        ("imdb:oceans", "movie", "Ocean's Eleven", 2001, None),   # not in Plex
        ("tvdb:leverage", "show", "Leverage", 2008, None),
        ("tvdb:leverage-s1e1", "episode", "The Nigerian Job", 2008, "tvdb:leverage"),
    ])
    db.executemany("INSERT INTO plex_items VALUES (?,?,?,'t')", [
        ("101", "imdb:heat", "1"),
        ("301", "imdb:heat", "3"),            # same title in a second section
        ("102", "imdb:inception", "1"),
        ("201", "tvdb:leverage", "2"),
    ])
    kw = [
        ("imdb:heat", "tmdb", "heist"), ("imdb:heat", "other", "heist"),  # two sources agree
        ("imdb:heat", "tmdb", "crime"),
        ("imdb:inception", "tmdb", "heist"), ("imdb:inception", "tmdb", "dream"),
        ("imdb:oceans", "tmdb", "heist"), ("imdb:oceans", "tmdb", "crime"),
        ("tvdb:leverage", "tmdb", "heist"),
    ]
    db.executemany(
        "INSERT INTO enrichment VALUES (?, 'keywords', ?, 'keyword', ?, 't', NULL)", kw
    )
    db.executemany(
        "INSERT INTO plays (history_key, item_id, plex_account_id, viewed_at, seconds_watched) "
        "VALUES (?,?,1,0,?)",
        [
            ("h1", "imdb:heat", 4000),
            ("h2", "imdb:heat", None),               # Plex-only play: counts
            ("h3", "imdb:inception", 20),            # abandoned after 20s
            ("h4", "tvdb:leverage-s1e1", None),      # episodes roll up to the show
            ("h5", "tvdb:leverage-s1e1", None),
        ],
    )
    db.executemany("INSERT INTO tag_network_edge VALUES (?,?,?,?)", [
        ("movie", "crime", "heist", 2),
        ("movie", "dream", "heist", 1),
        ("show", "heist", "revenge", 5),
    ])
    db.commit()
    db.close()
    return path


@pytest.fixture
def snapshot(tmp_path):
    return build_snapshot(tmp_path / "plexdb.snapshot.db")


@pytest.fixture
def source(snapshot):
    s = SnapshotTagSource(snapshot)
    yield s
    s.close()


def test_titles_for_tags_resolves_raw_spelling_and_skips_titles_plex_lacks(source):
    titles = source.titles_for_tags(["Heists"])
    assert [t.title for t in titles] == ["Heat", "Inception", "Leverage"]


def test_titles_for_tags_requires_every_tag_and_keeps_every_plex_key(source):
    titles = source.titles_for_tags(["heist", "crime"], kind="movie")
    assert len(titles) == 1
    assert titles[0].title == "Heat"
    assert titles[0].plex_keys == (("1", "101"), ("3", "301"))


def test_titles_for_tags_filters_by_kind(source):
    assert [t.title for t in source.titles_for_tags(["heist"], kind="show")] == ["Leverage"]


def test_normalize_keyword_matches_plex_db_ex_stored_form():
    assert normalize_keyword("  Bank-Heists ") == "bank heist"
    assert normalize_keyword("time_travel") == "time travel"


def test_tags_for_title_dedupes_across_sources(source):
    assert source.tags_for_title("imdb:heat") == ["crime", "heist"]


def test_co_tags_strongest_first_per_kind(source):
    assert source.co_tags("heist", "movie") == [("crime", 2), ("dream", 1)]
    assert source.co_tags("Heists", "show") == [("revenge", 5)]


def test_play_counts_roll_up_episodes_and_apply_floor_to_known_durations(source):
    assert source.play_counts() == {"imdb:heat": 2, "imdb:inception": 1, "tvdb:leverage": 2}
    assert source.play_counts(min_seconds=30) == {"imdb:heat": 2, "tvdb:leverage": 2}


def test_missing_snapshot_is_a_clear_error(tmp_path):
    with pytest.raises(TagSourceError, match="not found"):
        SnapshotTagSource(tmp_path / "nope.db")


def test_snapshot_missing_a_table_names_it(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(DDL.replace("CREATE TABLE tag_network_edge", "CREATE TABLE unrelated"))
    db.close()
    with pytest.raises(TagSourceError, match="tag_network_edge"):
        SnapshotTagSource(path)


def test_snapshot_from_older_schema_missing_a_column_names_it(tmp_path):
    path = tmp_path / "pre-v4.db"
    db = sqlite3.connect(path)
    db.executescript(DDL.replace("seconds_watched INTEGER, tautulli_id INTEGER", "x INTEGER"))
    db.close()
    with pytest.raises(TagSourceError, match=r"plays\.seconds_watched"):
        SnapshotTagSource(path)


def test_non_sqlite_file_is_unreadable_not_a_crash(tmp_path):
    path = tmp_path / "junk.db"
    path.write_bytes(b"not a database at all" * 100)
    with pytest.raises(TagSourceError, match="unreadable"):
        SnapshotTagSource(path)


def test_connection_is_read_only_and_works_from_a_read_only_directory(tmp_path):
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    build_snapshot(ro_dir / "plexdb.snapshot.db")
    ro_dir.chmod(0o555)
    try:
        s = SnapshotTagSource(ro_dir / "plexdb.snapshot.db")
        assert len(s.titles_for_tags(["heist"])) == 3
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            s._db.execute("DELETE FROM items")
        s.close()
    finally:
        ro_dir.chmod(0o755)


# --- `plex-home tags` through main, which never connects to Plex ---

def write_config(tmp_path, **extra):
    cfg = {
        "plex_url": "http://localhost:32400", "plex_token": "T", "library_names": ["Movies"],
        "cadence": {"interval_minutes": 60}, "home": {"Movies": [{"collection": "X"}]},
        "groups": {}, **extra,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(path)


def test_tags_query_json_through_main(tmp_path, snapshot, capsys):
    cfg = write_config(tmp_path, plexdb_snapshot=snapshot.name)
    rc = main.main(["--config", cfg, "tags", "query", "Heists", "--kind", "movie", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 2
    assert [r["title"] for r in out["rows"]] == ["Heat", "Inception"]
    assert out["rows"][0]["plex_keys"][1] == {"section_id": "3", "rating_key": "301"}


def test_tags_plays_json_through_main(tmp_path, snapshot, capsys):
    cfg = write_config(tmp_path, plexdb_snapshot=snapshot.name)
    rc = main.main(["--config", cfg, "tags", "plays", "--min-seconds", "30", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 2
    assert [(r["title"], r["plays"]) for r in out["rows"]] == [("Heat", 2), ("Leverage", 2)]


def test_tags_json_is_uncapped_unless_limit_given(tmp_path, snapshot, capsys):
    cfg = write_config(tmp_path, plexdb_snapshot=snapshot.name)
    main.main(["--config", cfg, "tags", "query", "heist", "--json"])
    assert len(json.loads(capsys.readouterr().out)["rows"]) == 3
    main.main(["--config", cfg, "tags", "query", "heist", "--json", "--limit", "1"])
    out = json.loads(capsys.readouterr().out)
    assert (out["total"], len(out["rows"])) == (3, 1)


def test_tags_rejects_non_positive_limit():
    with pytest.raises(SystemExit):
        main.cli.build_parser().parse_args(["tags", "query", "heist", "--limit", "0"])


def test_tags_without_snapshot_configured_exits_1(tmp_path):
    assert main.main(["--config", write_config(tmp_path), "tags", "query", "heist"]) == 1


def test_tags_with_missing_snapshot_exits_1(tmp_path):
    cfg = write_config(tmp_path, plexdb_snapshot="absent.db")
    assert main.main(["--config", cfg, "tags", "related", "heist", "--kind", "movie"]) == 1
