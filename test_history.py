import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import history as h


def make_history(hours_ago: float, title: str = "Test Collection") -> dict[str, datetime]:
    pinned_at = datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago)
    return {title: pinned_at}


def test_blocked_within_window():
    hist = make_history(hours_ago=10)
    assert h.is_blocked("Test Collection", hist, repeat_block_hours=24) is True


def test_not_blocked_outside_window():
    hist = make_history(hours_ago=25)
    assert h.is_blocked("Test Collection", hist, repeat_block_hours=24) is False


def test_never_blocked_when_zero():
    hist = make_history(hours_ago=0.001)
    assert h.is_blocked("Test Collection", hist, repeat_block_hours=0) is False


def test_not_blocked_when_no_history():
    assert h.is_blocked("Unknown", {}, repeat_block_hours=24) is False


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


def test_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    hist = {"Movie A": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)}
    h.save_history(hist)
    reloaded = h.load_history()
    assert "Movie A" in reloaded
    assert reloaded["Movie A"] == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_save_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "pin_history.json").exists()
    h.save_history({"X": datetime.now(tz=timezone.utc)})
    assert (tmp_path / "pin_history.json").exists()


def test_save_preserves_unrelated_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    hist = {
        "Movie A": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "Movie B": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    h.save_history(hist)
    updated = h.record_pins(["Movie C"], h.load_history())
    h.save_history(updated)
    final = h.load_history()
    assert "Movie A" in final
    assert "Movie B" in final
    assert "Movie C" in final


def test_record_pins_updates_timestamp():
    hist = make_history(hours_ago=48, title="Old Movie")
    updated = h.record_pins(["Old Movie", "New Movie"], hist)
    now = datetime.now(tz=timezone.utc)
    assert (now - updated["Old Movie"]).total_seconds() < 5
    assert (now - updated["New Movie"]).total_seconds() < 5


def test_record_pins_does_not_mutate_original():
    hist = make_history(hours_ago=48)
    original_ts = hist["Test Collection"]
    h.record_pins(["Test Collection"], hist)
    assert hist["Test Collection"] == original_ts
