import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from plex_home import history as h


def make_history(hours_ago: float, key=("Movies", "Test Collection")) -> dict:
    pinned_at = datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago)
    return {key: pinned_at}


def test_blocked_within_window():
    hist = make_history(hours_ago=10)
    assert h.is_blocked("Movies", "Test Collection", hist, repeat_block_hours=24) is True


def test_not_blocked_outside_window():
    hist = make_history(hours_ago=25)
    assert h.is_blocked("Movies", "Test Collection", hist, repeat_block_hours=24) is False


def test_never_blocked_when_zero():
    hist = make_history(hours_ago=0.001)
    assert h.is_blocked("Movies", "Test Collection", hist, repeat_block_hours=0) is False


def test_not_blocked_when_no_history():
    assert h.is_blocked("Movies", "Unknown", {}, repeat_block_hours=24) is False


def test_block_is_per_library():
    # blocked in Movies must not block a same-titled collection in TV Shows
    hist = make_history(hours_ago=1, key=("Movies", "Featured"))
    assert h.is_blocked("Movies", "Featured", hist, repeat_block_hours=24) is True
    assert h.is_blocked("TV Shows", "Featured", hist, repeat_block_hours=24) is False


def test_load_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = h.load_history()
    assert result == {}


def test_load_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pin_history.json").write_text("not json", encoding="utf-8")
    result = h.load_history()
    assert result == {}


def test_load_wrong_type(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pin_history.json").write_text("[]", encoding="utf-8")
    result = h.load_history()
    assert result == {}


def test_load_legacy_flat_file_ignored(tmp_path, monkeypatch):
    # old flat {title: timestamp} shape has string values, not per-library maps
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pin_history.json").write_text(
        json.dumps({"Movie A": "2026-01-01T12:00:00"}), encoding="utf-8"
    )
    result = h.load_history()
    assert result == {}


def test_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    hist = {("Movies", "Movie A"): datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)}
    h.save_history(hist)
    reloaded = h.load_history()
    assert reloaded[("Movies", "Movie A")] == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_save_nested_by_library(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    h.save_history({("Movies", "Featured"): dt, ("TV Shows", "Featured"): dt})
    raw = json.loads((tmp_path / "pin_history.json").read_text(encoding="utf-8"))
    assert raw == {
        "Movies": {"Featured": "2026-01-01T12:00:00"},
        "TV Shows": {"Featured": "2026-01-01T12:00:00"},
    }


def test_save_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "pin_history.json").exists()
    h.save_history({("Movies", "X"): datetime.now(tz=timezone.utc)})
    assert (tmp_path / "pin_history.json").exists()


def test_save_preserves_unrelated_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    hist = {
        ("Movies", "Movie A"): datetime(2026, 1, 1, tzinfo=timezone.utc),
        ("Movies", "Movie B"): datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    h.save_history(hist)
    updated = h.record_pins([("Movies", "Movie C")], h.load_history())
    h.save_history(updated)
    final = h.load_history()
    assert ("Movies", "Movie A") in final
    assert ("Movies", "Movie B") in final
    assert ("Movies", "Movie C") in final


def test_record_pins_updates_timestamp():
    hist = make_history(hours_ago=48, key=("Movies", "Old Movie"))
    updated = h.record_pins([("Movies", "Old Movie"), ("Movies", "New Movie")], hist)
    now = datetime.now(tz=timezone.utc)
    assert (now - updated[("Movies", "Old Movie")]).total_seconds() < 5
    assert (now - updated[("Movies", "New Movie")]).total_seconds() < 5


def test_record_pins_does_not_mutate_original():
    hist = make_history(hours_ago=48)
    original_ts = hist[("Movies", "Test Collection")]
    h.record_pins([("Movies", "Test Collection")], hist)
    assert hist[("Movies", "Test Collection")] == original_ts
