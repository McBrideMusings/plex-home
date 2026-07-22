from unittest.mock import MagicMock
from plexapi.exceptions import NotFound

from plex_home import ordering
from plex_home.resolver import ResolvedPin


def make_hub(title: str, n: int = 0) -> MagicMock:
    hub = MagicMock()
    hub.title = title
    hub.identifier = f"custom.collection.1.{n}"
    hub.promotedToOwnHome = True
    hub.promotedToSharedHome = True
    return hub


def make_section(hubs: list[MagicMock]) -> MagicMock:
    section = MagicMock()
    section.managedHubs.return_value = hubs
    return section


def make_plex(sections: dict[str, MagicMock]) -> MagicMock:
    plex = MagicMock()

    def get_section(name):
        if name not in sections:
            raise NotFound(name)
        return sections[name]

    plex.library.section.side_effect = get_section
    return plex


def mv(*t) -> list[ResolvedPin]:
    return [ResolvedPin("Movies", x) for x in t]


def pins(*pairs) -> list[ResolvedPin]:
    return [ResolvedPin(lib, title) for lib, title in pairs]


def hubs_of(*titles) -> list[MagicMock]:
    return [make_hub(t, i) for i, t in enumerate(titles)]


def test_each_resolved_hub_moved_to_top():
    a, b, c = hubs_of("A", "B", "C")
    plex = make_plex({"Movies": make_section([a, b, c])})
    result = ordering.apply_order(plex, ["Movies"], mv("A", "B", "C"))
    # Move-to-top in reverse target order; each resolved hub moved exactly once.
    a.move.assert_called_once_with(after=None)
    b.move.assert_called_once_with(after=None)
    c.move.assert_called_once_with(after=None)
    assert result.moved == ["A", "B", "C"]
    assert result.failed == []


def test_order_follows_resolved_not_hub_listing_order():
    a, b, c = hubs_of("A", "B", "C")
    plex = make_plex({"Movies": make_section([c, a, b])})  # Plex returns them shuffled
    result = ordering.apply_order(plex, ["Movies"], mv("A", "B", "C"))
    assert result.moved == ["A", "B", "C"]


def test_each_library_ordered_independently():
    a, b = make_hub("A", 1), make_hub("B", 2)     # Movies block
    c, d = make_hub("C", 3), make_hub("D", 4)     # TV block
    plex = make_plex({
        "Movies": make_section([a, b]),
        "TV Shows": make_section([c, d]),
    })
    result = ordering.apply_order(
        plex, ["Movies", "TV Shows"],
        pins(("Movies", "A"), ("Movies", "B"), ("TV Shows", "C"), ("TV Shows", "D")),
    )
    for h in (a, b, c, d):
        h.move.assert_called_once_with(after=None)
    assert result.moved == ["A", "B", "C", "D"]


def test_same_title_in_other_library_not_reordered():
    # "Featured" resolved only for Movies; TV also has a "Featured" hub that is
    # NOT resolved for TV — it must not be touched by the Movies ordering.
    m_feat, m_other = make_hub("Featured", 1), make_hub("MovieB", 2)
    tv_feat = make_hub("Featured", 3)
    plex = make_plex({
        "Movies": make_section([m_feat, m_other]),
        "TV Shows": make_section([tv_feat]),
    })
    ordering.apply_order(
        plex, ["Movies", "TV Shows"],
        pins(("Movies", "Featured"), ("Movies", "MovieB")),
    )
    m_feat.move.assert_called_once_with(after=None)
    tv_feat.move.assert_not_called()   # TV's Featured is not resolved for TV


def test_single_resolved_hub_per_library_no_move():
    a = make_hub("A", 1)
    c = make_hub("C", 2)
    plex = make_plex({
        "Movies": make_section([a]),
        "TV Shows": make_section([c]),
    })
    result = ordering.apply_order(plex, ["Movies", "TV Shows"], pins(("Movies", "A"), ("TV Shows", "C")))
    a.move.assert_not_called()   # one resolved hub in each library → nothing to order within either
    c.move.assert_not_called()
    assert result.moved == []


def test_single_hub_no_move_needed():
    a = make_hub("A")
    plex = make_plex({"Movies": make_section([a])})
    result = ordering.apply_order(plex, ["Movies"], mv("A"))
    a.move.assert_not_called()
    assert result.moved == []


def test_move_error_does_not_abort_run():
    a, b, c = hubs_of("A", "B", "C")
    b.move.side_effect = RuntimeError("plex 500")
    plex = make_plex({"Movies": make_section([a, b, c])})
    result = ordering.apply_order(plex, ["Movies"], mv("A", "B", "C"))
    assert result.moved == ["A", "C"]     # target order, B dropped
    assert result.failed == ["B"]
    a.move.assert_called_once_with(after=None)
    c.move.assert_called_once_with(after=None)


def test_system_hubs_not_reordered():
    a, b = make_hub("A", 1), make_hub("B", 2)
    recently_added = make_hub("Recently Added", 3)
    recently_added.identifier = "movie.recentlyadded"
    plex = make_plex({"Movies": make_section([a, b, recently_added])})
    ordering.apply_order(plex, ["Movies"], mv("A", "B"))
    recently_added.move.assert_not_called()


def test_resolved_title_without_hub_skipped():
    a, b = make_hub("A", 1), make_hub("B", 2)
    plex = make_plex({"Movies": make_section([a, b])})
    result = ordering.apply_order(plex, ["Movies"], mv("A", "Ghost", "B"))
    a.move.assert_called_once_with(after=None)
    b.move.assert_called_once_with(after=None)   # Ghost absent → dropped before ordering
    assert result.moved == ["A", "B"]
    assert "Ghost" not in result.moved
    assert "Ghost" not in result.failed


def test_missing_library_skipped_not_crashed():
    a, b = make_hub("A", 1), make_hub("B", 2)
    plex = make_plex({"Movies": make_section([a, b])})
    result = ordering.apply_order(plex, ["Movies", "Nonexistent"], mv("A", "B"))
    assert result.moved == ["A", "B"]


def test_empty_resolved_no_moves():
    a = make_hub("A")
    plex = make_plex({"Movies": make_section([a])})
    result = ordering.apply_order(plex, ["Movies"], [])
    a.move.assert_not_called()
    assert result.moved == []
    assert result.failed == []


def test_no_managed_hubs_skipped():
    plex = make_plex({"Movies": make_section([])})
    result = ordering.apply_order(plex, ["Movies"], mv("A", "B"))
    assert result.moved == []


def test_managed_hubs_fetch_error_skips_library():
    section = MagicMock()
    section.managedHubs.side_effect = RuntimeError("boom")
    plex = make_plex({"Movies": section})
    result = ordering.apply_order(plex, ["Movies"], mv("A", "B"))
    assert result.moved == []
    assert result.failed == []
