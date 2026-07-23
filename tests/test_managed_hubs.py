from unittest.mock import MagicMock

from plex_home import managed_hubs as mh


def make_hub(title, identifier, *, own=False, shared=False, recommended=False) -> MagicMock:
    hub = MagicMock()
    hub.title = title
    hub.identifier = identifier
    hub.promotedToOwnHome = own
    hub.promotedToSharedHome = shared
    hub.promotedToRecommended = recommended
    return hub


def collection(title, n=0, **kw):
    return make_hub(title, f"custom.collection.1.{n}", **kw)


def system(title, ident, **kw):
    return make_hub(title, ident, **kw)


# --- is_collection ---

def test_is_collection_true_for_custom_collection():
    assert mh.is_collection(collection("A", 1)) is True


def test_is_collection_false_for_system_hub():
    assert mh.is_collection(system("Recently Added", "movie.recentlyadded")) is False


def test_is_collection_false_for_missing_identifier():
    hub = MagicMock()
    hub.identifier = None
    assert mh.is_collection(hub) is False


# --- on_home / promoted_anywhere ---

def test_on_home_true_when_own():
    assert mh.on_home(collection("A", own=True)) is True


def test_on_home_true_when_shared():
    assert mh.on_home(collection("A", shared=True)) is True


def test_on_home_false_when_only_recommended():
    assert mh.on_home(collection("A", recommended=True)) is False


def test_promoted_anywhere_true_when_only_recommended():
    assert mh.promoted_anywhere(collection("A", recommended=True)) is True


def test_promoted_anywhere_false_when_no_flags():
    assert mh.promoted_anywhere(collection("A")) is False


# --- title_map: collection wins on collision ---

def test_title_map_collection_wins_over_system_regardless_of_order():
    sys = system("Recently Released Movies", "movie.recentlyreleased")
    coll = collection("Recently Released Movies", 170758)
    assert mh.title_map([sys, coll])["Recently Released Movies"] is coll
    assert mh.title_map([coll, sys])["Recently Released Movies"] is coll


def test_title_map_keeps_unique_titles():
    a, b = collection("A", 1), system("B", "movie.b")
    m = mh.title_map([a, b])
    assert m == {"A": a, "B": b}


def test_title_map_skips_titleless_hub():
    hub = MagicMock()
    hub.title = None
    hub.identifier = "custom.collection.1.9"
    assert mh.title_map([hub]) == {}


# --- realize_order: reverse move-to-top ---

def make_reorderable_section(titles):
    section = MagicMock()
    live = []

    def make(t):
        h = collection(t, len(live), own=True)

        def mv(after=None, _h=h):
            live.remove(_h)
            live.insert(0, _h)

        h.move.side_effect = mv
        return h

    for t in titles:
        live.append(make(t))
    section.managedHubs.return_value = live
    return section, live


def test_realize_order_builds_arbitrary_target():
    section, live = make_reorderable_section(["A", "B", "C", "D"])
    moved, failed, actual = mh.realize_order(section, ["C", "A", "D", "B"])
    assert [h.title for h in live] == ["C", "A", "D", "B"]
    assert moved == ["C", "A", "D", "B"]
    assert failed == []
    assert actual == ["C", "A", "D", "B"]   # verified against the live order


def test_realize_order_dry_run_is_noop():
    section, live = make_reorderable_section(["A", "B", "C"])
    moved, failed, actual = mh.realize_order(section, ["C", "B", "A"], dry_run=True)
    assert [h.title for h in live] == ["A", "B", "C"]
    assert moved == ["C", "B", "A"]
    assert failed == []
    assert actual == ["C", "B", "A"]


def test_realize_order_reports_move_failure_in_target_order():
    section, live = make_reorderable_section(["A", "B", "C"])
    b = next(h for h in live if h.title == "B")
    b.move.side_effect = RuntimeError("locked")
    moved, failed, _actual = mh.realize_order(section, ["A", "B", "C"])
    assert moved == ["A", "C"]
    assert failed == ["B"]


def test_realize_order_reports_actual_order_on_unapplied_move():
    # A section that ACCEPTS every move but never actually reorders — the live bug:
    # move() raises nothing, yet the home order stays wrong. The retry runs and the
    # final read still shows the un-applied order, which is surfaced as `actual`.
    section = MagicMock()
    live = [collection(t, i, own=True) for i, t in enumerate(["A", "B", "C"])]
    for h in live:
        h.move.side_effect = lambda after=None: None   # accepted, no effect
    section.managedHubs.return_value = live
    moved, failed, actual = mh.realize_order(section, ["C", "B", "A"])
    assert actual == ["A", "B", "C"]      # real order — NOT the requested ["C","B","A"]
    assert actual != ["C", "B", "A"]


def test_realize_order_retries_once_then_stops():
    # Count move calls: a persistent mismatch must run the loop exactly twice
    # (initial + one retry), never spin.
    section = MagicMock()
    live = [collection(t, i, own=True) for i, t in enumerate(["A", "B", "C"])]
    section.managedHubs.return_value = live
    mh.realize_order(section, ["C", "B", "A"])   # static list → always mismatches
    for h in live:
        assert h.move.call_count == 2   # one initial pass + exactly one retry


def test_realize_order_no_retry_when_first_pass_verifies():
    section, live = make_reorderable_section(["A", "B", "C"])
    mh.realize_order(section, ["C", "B", "A"])
    for h in live:
        assert h.move.call_count == 1   # verified first time → no retry
