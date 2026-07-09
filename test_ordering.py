from unittest.mock import MagicMock, call
from plexapi.exceptions import NotFound

import ordering


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


def test_multiple_libraries_single_ordered_list():
    m = make_hub("MovieHub")
    t = make_hub("TVHub")
    plex = make_plex({"Movies": make_section([m]), "TV Shows": make_section([t])})
    result = ordering.apply_order(plex, ["Movies", "TV Shows"], ["MovieHub", "TVHub"])
    m.move.assert_called_once_with(after=None)
    t.move.assert_called_once_with(after=m)
    assert result.moved == ["MovieHub", "TVHub"]


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
