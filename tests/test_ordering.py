from unittest.mock import MagicMock, call
from plexapi.exceptions import NotFound

from plex_home import ordering


def make_hub(title: str) -> MagicMock:
    hub = MagicMock()
    hub.title = title
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


def test_hubs_moved_into_resolved_order():
    a, b, c = make_hub("A"), make_hub("B"), make_hub("C")
    plex = make_plex({"Movies": make_section([a, b, c])})
    result = ordering.apply_order(plex, ["Movies"], ["A", "B", "C"])
    a.move.assert_called_once_with(after=None)
    b.move.assert_called_once_with(after=a)
    c.move.assert_called_once_with(after=b)
    assert result.moved == ["A", "B", "C"]
    assert result.failed == []


def test_order_follows_resolved_not_hub_listing_order():
    a, b, c = make_hub("A"), make_hub("B"), make_hub("C")
    plex = make_plex({"Movies": make_section([c, a, b])})  # Plex returns them shuffled
    result = ordering.apply_order(plex, ["Movies"], ["A", "B", "C"])
    a.move.assert_called_once_with(after=None)
    b.move.assert_called_once_with(after=a)
    c.move.assert_called_once_with(after=b)
    assert result.moved == ["A", "B", "C"]


def test_anchor_resets_per_library():
    a, b = make_hub("A"), make_hub("B")       # Movies block
    c, d = make_hub("C"), make_hub("D")       # TV block
    plex = make_plex({
        "Movies": make_section([a, b]),
        "TV Shows": make_section([c, d]),
    })
    result = ordering.apply_order(plex, ["Movies", "TV Shows"], ["A", "B", "C", "D"])
    a.move.assert_called_once_with(after=None)
    b.move.assert_called_once_with(after=a)
    c.move.assert_called_once_with(after=None)   # first TV hub → top of its own section, NOT after B
    d.move.assert_called_once_with(after=c)
    assert result.moved == ["A", "B", "C", "D"]


def test_single_resolved_hub_per_library_no_move():
    a = make_hub("A")
    c = make_hub("C")
    plex = make_plex({
        "Movies": make_section([a]),
        "TV Shows": make_section([c]),
    })
    result = ordering.apply_order(plex, ["Movies", "TV Shows"], ["A", "C"])
    a.move.assert_not_called()   # one resolved hub in each library → nothing to order within either
    c.move.assert_not_called()
    assert result.moved == []


def test_single_hub_no_move_needed():
    a = make_hub("A")
    plex = make_plex({"Movies": make_section([a])})
    result = ordering.apply_order(plex, ["Movies"], ["A"])
    a.move.assert_not_called()
    assert result.moved == []


def test_move_error_does_not_abort_and_anchor_holds():
    a, b, c = make_hub("A"), make_hub("B"), make_hub("C")
    b.move.side_effect = RuntimeError("plex 500")
    plex = make_plex({"Movies": make_section([a, b, c])})
    result = ordering.apply_order(plex, ["Movies"], ["A", "B", "C"])
    assert result.moved == ["A", "C"]
    assert result.failed == ["B"]
    # B failed → anchor stays A, so C is moved after A (the last known-good position)
    c.move.assert_called_once_with(after=a)


def test_system_hubs_not_reordered():
    a, b = make_hub("A"), make_hub("B")
    recently_added = make_hub("Recently Added")
    plex = make_plex({"Movies": make_section([a, b, recently_added])})
    ordering.apply_order(plex, ["Movies"], ["A", "B"])
    recently_added.move.assert_not_called()


def test_resolved_title_without_hub_skipped():
    a, b = make_hub("A"), make_hub("B")
    plex = make_plex({"Movies": make_section([a, b])})
    result = ordering.apply_order(plex, ["Movies"], ["A", "Ghost", "B"])
    a.move.assert_called_once_with(after=None)
    b.move.assert_called_once_with(after=a)  # Ghost skipped, B anchors after A
    assert result.moved == ["A", "B"]
    assert "Ghost" not in result.moved
    assert "Ghost" not in result.failed


def test_missing_library_skipped_not_crashed():
    a, b = make_hub("A"), make_hub("B")
    plex = make_plex({"Movies": make_section([a, b])})
    result = ordering.apply_order(plex, ["Movies", "Nonexistent"], ["A", "B"])
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
    result = ordering.apply_order(plex, ["Movies"], ["A", "B"])
    assert result.moved == []


def test_managed_hubs_fetch_error_skips_library():
    section = MagicMock()
    section.managedHubs.side_effect = RuntimeError("boom")
    plex = make_plex({"Movies": section})
    result = ordering.apply_order(plex, ["Movies"], ["A", "B"])
    assert result.moved == []
    assert result.failed == []
