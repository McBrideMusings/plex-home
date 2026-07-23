from plex_home.config import Config, Cadence, FixedSlot, PickSlot, Group
from plex_home.plex_client import CollectionInfo
from plex_home.simulate import run_simulation


def make_config(home, groups=None, interval=60, rbh=24.0, min_items=0) -> Config:
    return Config(
        plex_url="http://localhost:32400",
        plex_token="TOKEN",
        library_names=["Movies", "TV Shows"],
        cadence=Cadence(interval_minutes=interval, repeat_block_hours=rbh, min_items_for_pinning=min_items),
        home=home,
        groups=groups or {},
    )


def coll(title: str, item_count: int = 20) -> CollectionInfo:
    return CollectionInfo(title=title, item_count=item_count)


START = "2026-06-15"


def test_deterministic_for_a_given_seed():
    cfg = make_config(
        {"Movies": [PickSlot(["movies"])]},
        {"movies": Group(name="movies")},
    )
    colls = {"Movies": [coll("A"), coll("B"), coll("C")]}
    r1 = run_simulation(cfg, colls, days=2, start=START, seed=7)
    r2 = run_simulation(cfg, colls, days=2, start=START, seed=7)
    assert r1 == r2


def test_cycle_count_matches_days_and_interval():
    cfg = make_config({"Movies": [FixedSlot("Recently Added")]}, interval=60)
    # 1 day at 60-min interval = 24 cycles: 0..23.
    report = run_simulation(cfg, {}, days=1, start=START, seed=0)
    assert "→ 24 cycle(s)" in report
    assert "[cycle  23]" in report
    assert "[cycle  24]" not in report


def test_fixed_slot_repins_every_cycle_and_is_not_flagged():
    cfg = make_config({"Movies": [FixedSlot("Recently Added")]}, interval=60)
    report = run_simulation(cfg, {}, days=1, start=START, seed=0)
    assert "pinned 24x  [fixed]" in report
    # No pick-selected collections → nothing to verify, overall passes.
    assert "OVERALL: PASS" in report
    assert "(no pick-selected collections in this run)" in report


def test_repeat_block_honored_across_the_run():
    # 3 collections, 24h block, 12h interval → each collection can only appear
    # every other cycle at soonest; the verifier must report PASS with no
    # violation (a re-pin gap below 24h would be a FAIL).
    cfg = make_config(
        {"Movies": [PickSlot(["movies"])]},
        {"movies": Group(name="movies")},
        interval=720,  # 12h
        rbh=24.0,
    )
    colls = {"Movies": [coll("A"), coll("B"), coll("C")]}
    report = run_simulation(cfg, colls, days=4, start=START, seed=1)
    assert "OVERALL: PASS — repeat-block honored" in report
    assert "FAIL" not in report


def test_empty_pick_is_reported():
    # Group gated to October; simulating June → the pick finds nothing.
    cfg = make_config(
        {"Movies": [PickSlot(["fall"])]},
        {"fall": Group(name="fall", date="10-01/10-31")},
    )
    colls = {"Movies": [coll("A")]}
    report = run_simulation(cfg, colls, days=1, start=START, seed=0)
    assert "empty pick" in report


def test_date_window_start_activates_seasonal_group():
    # Same October group, but start the sim inside the window → it pins.
    # rbh=0 so the single collection re-pins every cycle, isolating the date
    # window from repeat-block emptiness.
    cfg = make_config(
        {"Movies": [PickSlot(["fall"])]},
        {"fall": Group(name="fall", date="10-01/10-31", repeat_block_hours=0)},
    )
    colls = {"Movies": [coll("Spooky")]}
    report = run_simulation(cfg, colls, days=1, start="2026-10-05", seed=0)
    assert "Movies / Spooky" in report
    assert "empty pick" not in report


def test_year_variable_tracks_simulated_clock():
    # {YEAR} must resolve to the *simulated* year, not wall-clock (issue #2 fix 1).
    cfg = make_config({"Movies": [FixedSlot("Oscars {YEAR}")]}, interval=1440)
    report = run_simulation(cfg, {}, days=1, start="2030-06-15", seed=0)
    assert "Oscars 2030" in report
    assert "Oscars 2026" not in report


def test_year_variable_changes_across_a_year_boundary():
    # Re-expanding each cycle means the title tracks the clock as it crosses years.
    cfg = make_config({"Movies": [FixedSlot("Oscars {YEAR}")]}, interval=1440)
    report = run_simulation(cfg, {}, days=10, start="2030-12-28", seed=0)
    assert "Oscars 2030" in report
    assert "Oscars 2031" in report
