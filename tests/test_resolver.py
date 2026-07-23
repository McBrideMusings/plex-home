import random
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from plex_home.config import Config, Cadence, FixedSlot, PickSlot, Group
from plex_home.plex_client import CollectionInfo
from plex_home.resolver import resolve_slots, ResolvedPin


def make_config(slots, groups=None, library="Movies") -> Config:
    home = slots if isinstance(slots, dict) else {library: slots}
    return Config(
        plex_url="http://localhost:32400",
        plex_token="TOKEN",
        library_names=["Movies", "TV Shows"],
        cadence=Cadence(interval_minutes=60, repeat_block_hours=24.0, min_items_for_pinning=0),
        home=home,
        groups=groups or {},
    )


def make_group(name="movies", **kwargs) -> Group:
    return Group(name=name, **kwargs)


def coll(title: str, item_count: int = 20) -> CollectionInfo:
    return CollectionInfo(title=title, item_count=item_count)


def titles(pins) -> list[str]:
    return [p.title for p in pins]


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
EMPTY_HISTORY: dict = {}
ALL_COLLS = {"Movies": [coll("Movie A"), coll("Movie B"), coll("Movie C")]}


def test_fixed_slot_always_resolves():
    cfg = make_config([FixedSlot("Recently Added")])
    result = resolve_slots(cfg, {}, EMPTY_HISTORY, NOW)
    assert result == [ResolvedPin("Movies", "Recently Added")]


def test_fixed_slot_skips_repeat_block():
    pinned_at = NOW - timedelta(hours=1)
    history = {("Movies", "Recently Added"): pinned_at}
    cfg = make_config([FixedSlot("Recently Added")])
    result = resolve_slots(cfg, {}, history, NOW)
    assert titles(result) == ["Recently Added"]


def test_pick_slot_resolves_from_eligible_group():
    group = make_group()
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    result = resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW)
    assert len(result) == 1
    assert result[0].library == "Movies"
    assert result[0].title in {"Movie A", "Movie B", "Movie C"}


def test_pick_slot_skipped_when_no_groups_resolve():
    group = make_group(date="10-01/10-31")
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    result = resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW)
    assert result == []


def test_pick_slot_skipped_when_all_blocked():
    history = {
        ("Movies", "Movie A"): NOW - timedelta(hours=1),
        ("Movies", "Movie B"): NOW - timedelta(hours=1),
        ("Movies", "Movie C"): NOW - timedelta(hours=1),
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
    assert result[0].title in {"Movie A", "Movie B", "Movie C"}


def test_no_duplicate_across_slots():
    group = make_group()
    colls = {"Movies": [coll("Only Movie")]}
    cfg = make_config(
        [PickSlot(["movies"]), PickSlot(["movies"])],
        {"movies": group},
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert titles(result).count("Only Movie") == 1


def test_same_title_in_two_libraries_both_resolve():
    # Movies and TV each have a collection titled "Featured" — they are distinct
    # pins keyed by (library, title), so both resolve.
    colls = {
        "Movies": [coll("Featured")],
        "TV Shows": [coll("Featured")],
    }
    cfg = make_config(
        {
            "Movies": [FixedSlot("Featured")],
            "TV Shows": [FixedSlot("Featured")],
        }
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert result == [ResolvedPin("Movies", "Featured"), ResolvedPin("TV Shows", "Featured")]


def test_fixed_slot_excluded_from_pick_pool():
    group = make_group()
    colls = {"Movies": [coll("Only Movie")]}
    cfg = make_config(
        [FixedSlot("Only Movie"), PickSlot(["movies"])],
        {"movies": group},
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert titles(result) == ["Only Movie"]


def test_output_order_matches_slot_declaration():
    group = make_group()
    colls = {"Movies": [coll("Movie A"), coll("Movie B")]}
    cfg = make_config(
        [FixedSlot("Fixed First"), PickSlot(["movies"]), FixedSlot("Fixed Last")],
        {"movies": group},
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert result[0].title == "Fixed First"
    assert result[-1].title == "Fixed Last"
    assert len(result) == 3


def test_per_group_repeat_block_zero_bypasses():
    history = {
        ("Movies", "Movie A"): NOW - timedelta(hours=1),
        ("Movies", "Movie B"): NOW - timedelta(hours=1),
        ("Movies", "Movie C"): NOW - timedelta(hours=1),
    }
    group = make_group(repeat_block_hours=0)
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    result = resolve_slots(cfg, ALL_COLLS, history, NOW)
    assert len(result) == 1


def test_per_group_repeat_block_override():
    history = {("Movies", "Movie A"): NOW - timedelta(hours=2)}
    group = make_group(repeat_block_hours=1)
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    colls = {"Movies": [coll("Movie A")]}
    result = resolve_slots(cfg, colls, history, NOW)
    assert titles(result) == ["Movie A"]


def test_repeat_block_is_per_library():
    # "Movie A" blocked in Movies must NOT block a same-titled collection in TV.
    history = {("Movies", "Solo"): NOW - timedelta(hours=1)}
    cfg = make_config(
        {"TV Shows": [PickSlot(["tv"])]},
        {"tv": make_group("tv")},
    )
    colls = {"TV Shows": [coll("Solo")]}
    result = resolve_slots(cfg, colls, history, NOW)
    assert result == [ResolvedPin("TV Shows", "Solo")]


def test_duplicate_fixed_slots_dedup():
    cfg = make_config([FixedSlot("Recently Added"), FixedSlot("Recently Added")])
    result = resolve_slots(cfg, {}, EMPTY_HISTORY, NOW)
    assert titles(result) == ["Recently Added"]


def test_injected_rng_makes_pick_deterministic():
    group = make_group()
    cfg = make_config([PickSlot(["movies"])], {"movies": group})
    picks = {
        resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW, rng=random.Random(1))[0].title
        for _ in range(5)
    }
    assert len(picks) == 1


def test_home_grouped_by_library_in_mapping_order():
    colls = {
        "Movies": [coll("Movie Pick")],
        "TV Shows": [coll("Show Pick")],
    }
    cfg = make_config(
        {
            "Movies": [FixedSlot("Movie Fixed"), PickSlot(["mv"])],
            "TV Shows": [FixedSlot("Show Fixed"), PickSlot(["tv"])],
        },
        {"mv": make_group("mv"), "tv": make_group("tv")},
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert result == [
        ResolvedPin("Movies", "Movie Fixed"),
        ResolvedPin("Movies", "Movie Pick"),
        ResolvedPin("TV Shows", "Show Fixed"),
        ResolvedPin("TV Shows", "Show Pick"),
    ]


def test_pick_draws_from_its_own_library():
    colls = {
        "Movies": [coll("Movie Only")],
        "TV Shows": [coll("Show Only")],
    }
    cfg = make_config(
        {"TV Shows": [PickSlot(["shared"])]},
        {"shared": make_group("shared")},
    )
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert result == [ResolvedPin("TV Shows", "Show Only")]


def test_multiple_fixed_and_pick_slots():
    group = make_group()
    cfg = make_config(
        [FixedSlot("A"), FixedSlot("B"), PickSlot(["movies"]), FixedSlot("C")],
        {"movies": group},
    )
    result = resolve_slots(cfg, ALL_COLLS, EMPTY_HISTORY, NOW)
    assert result[0].title == "A"
    assert result[1].title == "B"
    assert result[3].title == "C"
    assert result[2].title in {"Movie A", "Movie B", "Movie C"}


# --- pattern fixed slots (issue #2) ---

def test_fixed_glob_slot_picks_first_by_title_sort():
    colls = {"Movies": [coll("Oscars Death Race 2027"), coll("Oscars Death Race 2026")]}
    cfg = make_config([FixedSlot("glob:Oscars Death Race *")])
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert titles(result) == ["Oscars Death Race 2026"]


def test_fixed_regex_slot_matches():
    colls = {"Movies": [coll("Oscars Death Race 2026"), coll("Comedy")]}
    cfg = make_config([FixedSlot(r"re:Oscars Death Race \d{4}")])
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert titles(result) == ["Oscars Death Race 2026"]


def test_fixed_pattern_slot_skips_when_no_match():
    colls = {"Movies": [coll("Comedy"), coll("Drama")]}
    cfg = make_config([FixedSlot("glob:Oscars *")])
    result = resolve_slots(cfg, colls, EMPTY_HISTORY, NOW)
    assert result == []


def test_fixed_exact_slot_still_blind_pins_without_collection_list():
    cfg = make_config([FixedSlot("Recently Added")])
    result = resolve_slots(cfg, {}, EMPTY_HISTORY, NOW)
    assert titles(result) == ["Recently Added"]
