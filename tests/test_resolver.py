import random
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from plex_home.config import Config, Cadence, FixedSlot, PickSlot, Group
from plex_home.plex_client import CollectionInfo
from plex_home.resolver import resolve_slots


def make_config(slots, groups=None) -> Config:
    return Config(
        plex_url="http://localhost:32400",
        plex_token="TOKEN",
        library_names=["Movies"],
        cadence=Cadence(interval_minutes=60, repeat_block_hours=24.0, min_items_for_pinning=0),
        home=slots,
        groups=groups or {},
    )


def make_group(name="movies", library="Movies", **kwargs) -> Group:
    return Group(name=name, library=library, **kwargs)


def coll(title: str, item_count: int = 20) -> CollectionInfo:
    return CollectionInfo(title=title, item_count=item_count)


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
EMPTY_HISTORY: dict = {}
ALL_COLLS = {"Movies": [coll("Movie A"), coll("Movie B"), coll("Movie C")]}


def test_fixed_slot_always_resolves():
    cfg = make_config([FixedSlot("Recently Added")])
    result = resolve_slots(cfg, {}, EMPTY_HISTORY, NOW)
    assert result == ["Recently Added"]


def test_fixed_slot_skips_repeat_block():
    pinned_at = NOW - timedelta(hours=1)
    history = {"Recently Added": pinned_at}
    cfg = make_config([FixedSlot("Recently Added")])
    result = resolve_slots(cfg, {}, history, NOW)
    assert result == ["Recently Added"]


def test_pick_slot_resolves_from_eligible_group():
    group = make_group()
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    result = resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW)
    assert len(result) == 1
    assert result[0] in {"Movie A", "Movie B", "Movie C"}


def test_pick_slot_skipped_when_no_groups_resolve():
    group = make_group(date="10-01/10-31")
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    result = resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW)
    assert result == []


def test_pick_slot_skipped_when_all_blocked():
    history = {
        "Movie A": NOW - timedelta(hours=1),
        "Movie B": NOW - timedelta(hours=1),
        "Movie C": NOW - timedelta(hours=1),
    }
    group = make_group()
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    result = resolve_slots(cfg, ALL_COLLS, history, NOW)
    assert result == []


def test_pick_slot_tries_groups_in_order():
    ineligible = make_group("ineligible", date="10-01/10-31")
    eligible = make_group("eligible")
    cfg = make_config(
        [PickSlot(["ineligible", "eligible"])],
        {"ineligible": ineligible, "eligible": eligible},
    )
    result = resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW)
    assert len(result) == 1
    assert result[0] in {"Movie A", "Movie B", "Movie C"}


def test_no_duplicate_across_slots():
    group = make_group()
    colls = {"Movies": [coll("Only Movie")]}
    cfg = make_config(
        [PickSlot(["movies"]), PickSlot(["movies"])],
        {"movies": group},
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert result.count("Only Movie") == 1


def test_fixed_slot_excluded_from_pick_pool():
    group = make_group()
    colls = {"Movies": [coll("Only Movie")]}
    cfg = make_config(
        [FixedSlot("Only Movie"), PickSlot(["movies"])],
        {"movies": group},
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert result == ["Only Movie"]


def test_output_order_matches_slot_declaration():
    group = make_group()
    colls = {"Movies": [coll("Movie A"), coll("Movie B")]}
    cfg = make_config(
        [FixedSlot("Fixed First"), PickSlot(["movies"]), FixedSlot("Fixed Last")],
        {"movies": group},
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert result[0] == "Fixed First"
    assert result[-1] == "Fixed Last"
    assert len(result) == 3


def test_per_group_repeat_block_zero_bypasses():
    history = {
        "Movie A": NOW - timedelta(hours=1),
        "Movie B": NOW - timedelta(hours=1),
        "Movie C": NOW - timedelta(hours=1),
    }
    group = make_group(repeat_block_hours=0)
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    result = resolve_slots(cfg, ALL_COLLS, history, NOW)
    assert len(result) == 1


def test_per_group_repeat_block_override():
    history = {"Movie A": NOW - timedelta(hours=2)}
    group = make_group(repeat_block_hours=1)
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    colls = {"Movies": [coll("Movie A")]}
    result = resolve_slots(cfg, colls, history, NOW)
    assert result == ["Movie A"]


def test_duplicate_fixed_slots_dedup():
    cfg = make_config([FixedSlot("Recently Added"), FixedSlot("Recently Added")])
    result = resolve_slots(cfg, {}, EMPTY_HISTORY, NOW)
    assert result == ["Recently Added"]


def test_injected_rng_makes_pick_deterministic():
    group = make_group()
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    picks = {
        resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW, rng=random.Random(1))[0]
        for _ in range(5)
    }
    assert len(picks) == 1


def test_multiple_fixed_and_pick_slots():
    group = make_group()
    cfg = make_config(
        [FixedSlot("A"), FixedSlot("B"), PickSlot(["movies"]), FixedSlot("C")],
        {"movies": group},
    )
    result = resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW)
    assert result[0] == "A"
    assert result[1] == "B"
    assert result[3] == "C"
    assert result[2] in {"Movie A", "Movie B", "Movie C"}
