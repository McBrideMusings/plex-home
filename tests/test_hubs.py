from unittest.mock import MagicMock
import pytest
from plexapi.exceptions import NotFound

from plex_home import hubs
from plex_home import managed_hubs
from plex_home.hubs import HubError
from plex_home.config import Config, Cadence, FixedSlot, PickSlot, Group


def make_hub(title: str, identifier: str, pinned: bool = True) -> MagicMock:
    h = MagicMock()
    h.title = title
    h.identifier = identifier
    h.promotedToOwnHome = pinned
    h.promotedToSharedHome = pinned
    return h


def collection_hub(title: str, n: int = 0, pinned: bool = True) -> MagicMock:
    return make_hub(title, f"custom.collection.1.{n}", pinned)


def system_hub(title: str, ident: str, pinned: bool = True) -> MagicMock:
    return make_hub(title, ident, pinned)


def make_section(managed=None, collections=None) -> MagicMock:
    section = MagicMock()
    section.managedHubs.return_value = managed or []
    section.collections.return_value = collections or []
    return section


def make_plex(sections: dict) -> MagicMock:
    plex = MagicMock()

    def get(name):
        if name not in sections:
            raise NotFound(name)
        return sections[name]

    plex.library.section.side_effect = get
    return plex


def real_collection(title: str, child_count: int = 20, labels: list[str] | None = None) -> MagicMock:
    """A section.collections() row, with labels shaped as objects carrying .tag
    (matching what plex_client.fetch_collections reads)."""
    c = MagicMock()
    c.title = title
    c.childCount = child_count
    c.labels = [MagicMock(tag=t) for t in (labels or [])]
    return c


def make_config(home=None, groups=None, libraries=("Movies",)) -> Config:
    return Config(
        plex_url="http://p", plex_token="t",
        library_names=list(libraries),
        cadence=Cadence(interval_minutes=30),
        home=home or {},
        groups=groups or {},
    )


def test_list_pinned_indexes_and_tags_kind():
    managed = [
        system_hub("Recently Added Movies", "movie.recentlyadded"),
        collection_hub("Halloween", 1),
        collection_hub("Random", 2),
        collection_hub("Unpinned", 3, pinned=False),  # excluded
    ]
    plex = make_plex({"Movies": make_section(managed)})
    config = make_config(home={"Movies": [FixedSlot(collection="Halloween")]})
    out = hubs.list_pinned(plex, ["Movies"], config)["Movies"]
    assert [(v.index, v.title, v.kind, v.config_managed) for v in out] == [
        (0, "Recently Added Movies", "system", False),
        (1, "Halloween", "collection", True),   # in a fixed slot → config-managed
        (2, "Random", "collection", False),     # pinned but not in config → manual
    ]


def test_list_pinned_tags_label_group_membership():
    # 'Spooky' is pinned and carries the 'horror' label; a pick slot references a
    # group that includes that label → config-managed, even though it is not named.
    managed = [collection_hub("Spooky", 1), collection_hub("Random", 2)]
    colls = [real_collection("Spooky", 30, ["horror"]), real_collection("Random", 30, ["comedy"])]
    plex = make_plex({"Movies": make_section(managed, colls)})
    config = make_config(
        home={"Movies": [PickSlot(groups=["halloween"])]},
        groups={"halloween": Group(name="halloween", include_labels=["horror"])},
    )
    out = hubs.list_pinned(plex, ["Movies"], config)["Movies"]
    tags = {(v.title, v.config_managed) for v in out}
    assert tags == {("Spooky", True), ("Random", False)}


def test_list_pinned_label_membership_ignores_min_items_and_block():
    # Reachable by label but far below the group's min_items — still config-managed
    # (static reachability ignores the min-items and repeat-block gates).
    managed = [collection_hub("Thin", 1)]
    colls = [real_collection("Thin", 1, ["horror"])]
    plex = make_plex({"Movies": make_section(managed, colls)})
    config = make_config(
        home={"Movies": [PickSlot(groups=["halloween"])]},
        groups={"halloween": Group(name="halloween", include_labels=["horror"], min_items_for_pinning=100)},
    )
    out = hubs.list_pinned(plex, ["Movies"], config)["Movies"]
    assert out[0].config_managed is True


def test_list_pinned_ignores_group_not_referenced_by_a_pick_slot():
    # 'orphan' group would match, but no pick slot references it → not reachable.
    managed = [collection_hub("Spooky", 1)]
    colls = [real_collection("Spooky", 30, ["horror"])]
    plex = make_plex({"Movies": make_section(managed, colls)})
    config = make_config(
        home={"Movies": [FixedSlot(collection="Other")]},
        groups={"orphan": Group(name="orphan", include_labels=["horror"])},
    )
    out = hubs.list_pinned(plex, ["Movies"], config)["Movies"]
    assert out[0].config_managed is False


def test_pinned_titles_returns_system_and_collection_titles():
    managed = [
        system_hub("Recently Added Movies", "movie.recentlyadded"),
        collection_hub("Halloween", 1),
        collection_hub("Unpinned", 2, pinned=False),  # excluded
    ]
    plex = make_plex({"Movies": make_section(managed)})
    assert hubs.pinned_titles(plex, "Movies") == {"Recently Added Movies", "Halloween"}


def test_list_available_excludes_pinned():
    managed = [collection_hub("Pinned", 1)]
    colls = [MagicMock(title=t) for t in ("Pinned", "Zeta", "Alpha")]
    plex = make_plex({"Movies": make_section(managed, colls)})
    out = hubs.list_available(plex, ["Movies"])["Movies"]
    assert out == ["Alpha", "Zeta"]  # sorted, Pinned excluded


def test_missing_library_raises_hub_error():
    plex = make_plex({"Movies": make_section()})
    with pytest.raises(HubError):
        hubs.list_pinned(plex, ["Nope"], make_config())


def test_pin_promotes_via_collection_visibility():
    coll = MagicMock()
    vis = MagicMock()
    coll.visibility.return_value = vis
    section = make_section()
    section.collection.return_value = coll
    plex = make_plex({"Movies": section})
    hubs.pin(plex, "Movies", "Halloween")
    vis.promoteHome.assert_called_once()
    vis.promoteShared.assert_called_once()


def test_pin_unknown_collection_raises():
    section = make_section()
    section.collection.side_effect = NotFound("x")
    plex = make_plex({"Movies": section})
    with pytest.raises(HubError):
        hubs.pin(plex, "Movies", "Ghost")


def test_pin_dry_run_does_not_touch_plex():
    coll = MagicMock()
    section = make_section()
    section.collection.return_value = coll
    plex = make_plex({"Movies": section})
    hubs.pin(plex, "Movies", "Halloween", dry_run=True)
    coll.visibility.assert_not_called()


def test_unpin_by_index_demotes():
    a, b = collection_hub("A", 1), collection_hub("B", 2)
    plex = make_plex({"Movies": make_section([a, b])})
    hubs.unpin(plex, "Movies", "1")
    b.demoteHome.assert_called_once()
    b.demoteShared.assert_called_once()
    a.demoteHome.assert_not_called()


def test_unpin_by_title_demotes():
    a, b = collection_hub("A", 1), collection_hub("B", 2)
    plex = make_plex({"Movies": make_section([a, b])})
    hubs.unpin(plex, "Movies", "A")
    a.demoteHome.assert_called_once()


def test_unpin_index_out_of_range_raises():
    plex = make_plex({"Movies": make_section([collection_hub("A", 1)])})
    with pytest.raises(HubError):
        hubs.unpin(plex, "Movies", "5")


def test_unpin_unknown_title_raises():
    plex = make_plex({"Movies": make_section([collection_hub("A", 1)])})
    with pytest.raises(HubError):
        hubs.unpin(plex, "Movies", "Ghost")


def make_reorderable_section(titles):
    """Section whose managedHubs is a live list; each hub.move(after=None) moves
    that hub to the front — simulating Plex's move-to-top."""
    section = MagicMock()
    live = []

    def make(t):
        h = collection_hub(t, len(live))

        def mv(after=None, _h=h):
            live.remove(_h)
            live.insert(0, _h)

        h.move.side_effect = mv
        return h

    for t in titles:
        live.append(make(t))
    section.managedHubs.return_value = live
    return section, live


# --- _target_order (pure repositioning math) ---

def test_target_order_moves_hub_down():
    pinned = [collection_hub(t, i) for i, t in enumerate(["A", "B", "C", "D"])]
    assert hubs._target_order(pinned, 0, 2) == ["B", "C", "A", "D"]


def test_target_order_moves_hub_to_top():
    pinned = [collection_hub(t, i) for i, t in enumerate(["A", "B", "C"])]
    assert hubs._target_order(pinned, 2, 0) == ["C", "A", "B"]


def test_target_order_clamps_high_index_to_bottom():
    pinned = [collection_hub(t, i) for i, t in enumerate(["A", "B", "C"])]
    assert hubs._target_order(pinned, 0, 9) == ["B", "C", "A"]


# --- realize_order (reverse move-to-top builds any order) ---

def test_realize_order_builds_target_via_move_to_top():
    section, live = make_reorderable_section(["A", "B", "C", "D"])
    managed_hubs.realize_order(section, ["C", "A", "D", "B"], dry_run=False)
    assert [h.title for h in live] == ["C", "A", "D", "B"]


def test_realize_order_dry_run_moves_nothing():
    section, live = make_reorderable_section(["A", "B", "C"])
    managed_hubs.realize_order(section, ["C", "B", "A"], dry_run=True)
    assert [h.title for h in live] == ["A", "B", "C"]


# --- move() end to end (works past a locked bottom hub) ---

def test_move_reorders_including_below_unanchorable_hub():
    # 'Locked' is a curated/system-style hub; move-to-top still relocates around it.
    section, live = make_reorderable_section(["A", "B", "Locked", "C"])
    plex = make_plex({"Movies": section})
    hubs.move(plex, "Movies", "A", 3)  # A to the bottom, below 'Locked'
    assert [h.title for h in live] == ["B", "Locked", "C", "A"]


def test_move_dry_run_does_not_reorder():
    section, live = make_reorderable_section(["A", "B"])
    plex = make_plex({"Movies": section})
    hubs.move(plex, "Movies", "A", 1, dry_run=True)
    assert [h.title for h in live] == ["A", "B"]


def test_verify_placement_warns_on_mismatch(caplog):
    import logging
    a, b, c = collection_hub("A", 1), collection_hub("B", 2), collection_hub("C", 3)
    section = make_section([a, b, c])
    with caplog.at_level(logging.WARNING):
        actual = hubs._verify_placement(section, "C", 0)  # C is at index 2, requested 0
    assert actual == 2
    assert "placed it at 2" in caplog.text


def test_verify_placement_silent_on_match(caplog):
    import logging
    a, b = collection_hub("A", 1), collection_hub("B", 2)
    section = make_section([a, b])
    with caplog.at_level(logging.WARNING):
        hubs._verify_placement(section, "A", 0)  # A already at index 0
    assert caplog.text == ""


def test_locate_returns_index_and_count():
    a, b, c = (collection_hub("A", 1), collection_hub("B", 2), collection_hub("C", 3))
    plex = make_plex({"Movies": make_section([a, b, c])})
    assert hubs.locate(plex, "Movies", "B") == (1, 3)
    assert hubs.locate(plex, "Movies", "2") == (2, 3)
