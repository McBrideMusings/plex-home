from unittest.mock import MagicMock
import pytest
from plexapi.exceptions import NotFound

import hubs
from hubs import HubError
from config import Config, Cadence, FixedSlot


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


def make_config(home=None, groups=None, libraries=("Movies",)) -> Config:
    return Config(
        plex_url="http://p", plex_token="t",
        library_names=list(libraries),
        cadence=Cadence(interval_minutes=30),
        home=home or [],
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
    config = make_config(home=[FixedSlot(collection="Halloween")])
    out = hubs.list_pinned(plex, ["Movies"], config)["Movies"]
    assert [(v.index, v.title, v.kind, v.config_managed) for v in out] == [
        (0, "Recently Added Movies", "system", False),
        (1, "Halloween", "collection", True),   # in a fixed slot → config-managed
        (2, "Random", "collection", False),     # pinned but not in config → manual
    ]


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


def test_move_to_top_uses_after_none():
    a, b, c = (collection_hub("A", 1), collection_hub("B", 2), collection_hub("C", 3))
    plex = make_plex({"Movies": make_section([a, b, c])})
    hubs.move(plex, "Movies", "C", 0)  # C from index 2 to top
    c.move.assert_called_once_with(after=None)


def test_move_down_anchors_after_correct_hub():
    a, b, c, d = (collection_hub("A", 1), collection_hub("B", 2),
                  collection_hub("C", 3), collection_hub("D", 4))
    plex = make_plex({"Movies": make_section([a, b, c, d])})
    hubs.move(plex, "Movies", "A", 2)  # A (index 0) to index 2; others=[B,C,D] → after C
    a.move.assert_called_once_with(after=c)


def test_move_to_bottom_anchors_after_last_remaining():
    a, b, c = (collection_hub("A", 1), collection_hub("B", 2), collection_hub("C", 3))
    plex = make_plex({"Movies": make_section([a, b, c])})
    hubs.move(plex, "Movies", "A", 2)  # A to index 2 (bottom); others=[B,C] → after C
    a.move.assert_called_once_with(after=c)


def test_move_dry_run_does_not_call_move():
    a, b = collection_hub("A", 1), collection_hub("B", 2)
    plex = make_plex({"Movies": make_section([a, b])})
    hubs.move(plex, "Movies", "A", 1, dry_run=True)
    a.move.assert_not_called()


def test_locate_returns_index_and_count():
    a, b, c = (collection_hub("A", 1), collection_hub("B", 2), collection_hub("C", 3))
    plex = make_plex({"Movies": make_section([a, b, c])})
    assert hubs.locate(plex, "Movies", "B") == (1, 3)
    assert hubs.locate(plex, "Movies", "2") == (2, 3)
