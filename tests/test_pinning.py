from datetime import datetime, timezone
from unittest.mock import MagicMock
from plexapi.exceptions import NotFound

from plex_home import pinning
from plex_home.resolver import ResolvedPin


def make_collection(title: str, promoted: bool) -> MagicMock:
    coll = MagicMock()
    coll.title = title
    hub = MagicMock()
    hub.promotedToOwnHome = promoted
    hub.promotedToSharedHome = promoted
    coll.visibility.return_value = hub
    coll._hub = hub  # test convenience accessor
    return coll


def make_section(collections: list[MagicMock]) -> MagicMock:
    section = MagicMock()
    section.collections.return_value = collections
    return section


def make_plex(sections: dict[str, MagicMock]) -> MagicMock:
    plex = MagicMock()

    def get_section(name):
        if name not in sections:
            raise NotFound(name)
        return sections[name]

    plex.library.section.side_effect = get_section
    return plex


def pins(*pairs) -> list[ResolvedPin]:
    return [ResolvedPin(lib, title) for lib, title in pairs]


def test_resolved_not_promoted_gets_pinned():
    coll = make_collection("Halloween", promoted=False)
    plex = make_plex({"Movies": make_section([coll])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Halloween")), {})
    coll._hub.promoteHome.assert_called_once()
    coll._hub.promoteShared.assert_called_once()
    assert result.pinned == pins(("Movies", "Halloween"))
    assert result.unpinned == []


def test_promoted_not_resolved_gets_unpinned():
    coll = make_collection("Stale", promoted=True)
    plex = make_plex({"Movies": make_section([coll])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    coll._hub.demoteHome.assert_called_once()
    coll._hub.demoteShared.assert_called_once()
    assert result.unpinned == pins(("Movies", "Stale"))
    assert result.pinned == []


def test_already_pinned_and_resolved_left_alone():
    coll = make_collection("Keep", promoted=True)
    plex = make_plex({"Movies": make_section([coll])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Keep")), {})
    coll._hub.promoteHome.assert_not_called()
    coll._hub.demoteHome.assert_not_called()
    assert result.unchanged == pins(("Movies", "Keep"))
    assert result.pinned == []
    assert result.unpinned == []


def test_history_updated_with_pinned_keys():
    new = make_collection("New", promoted=False)
    keep = make_collection("Keep", promoted=True)
    plex = make_plex({"Movies": make_section([new, keep])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "New"), ("Movies", "Keep")), {})
    assert set(result.history.keys()) == {("Movies", "New"), ("Movies", "Keep")}
    assert all(isinstance(v, datetime) for v in result.history.values())


def test_unpinned_keys_not_added_to_history():
    stale = make_collection("Stale", promoted=True)
    plex = make_plex({"Movies": make_section([stale])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    assert ("Movies", "Stale") not in result.history


def test_existing_history_preserved():
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    coll = make_collection("New", promoted=False)
    plex = make_plex({"Movies": make_section([coll])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "New")), {("Movies", "Older"): old})
    assert result.history[("Movies", "Older")] == old
    assert ("Movies", "New") in result.history


def test_pin_error_does_not_abort_run():
    bad = make_collection("Bad", promoted=False)
    bad._hub.promoteHome.side_effect = RuntimeError("plex 500")
    good = make_collection("Good", promoted=False)
    plex = make_plex({"Movies": make_section([bad, good])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Bad"), ("Movies", "Good")), {})
    assert result.pinned == pins(("Movies", "Good"))
    good._hub.promoteHome.assert_called_once()


def test_unpin_error_does_not_abort_run():
    bad = make_collection("BadUnpin", promoted=True)
    bad._hub.demoteHome.side_effect = RuntimeError("plex 500")
    good = make_collection("GoodUnpin", promoted=True)
    plex = make_plex({"Movies": make_section([bad, good])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    assert result.unpinned == pins(("Movies", "GoodUnpin"))


def test_resolved_key_missing_from_plex():
    coll = make_collection("Present", promoted=False)
    plex = make_plex({"Movies": make_section([coll])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Present"), ("Movies", "Ghost")), {})
    assert result.missing == pins(("Movies", "Ghost"))
    assert result.pinned == pins(("Movies", "Present"))
    assert ("Movies", "Ghost") not in result.history


def test_missing_library_skipped_not_crashed():
    coll = make_collection("A", promoted=False)
    plex = make_plex({"Movies": make_section([coll])})
    result = pinning.apply_pins(plex, ["Movies", "Nonexistent"], pins(("Movies", "A")), {})
    assert result.pinned == pins(("Movies", "A"))


def test_multiple_libraries_managed_together():
    m = make_collection("MoviePin", promoted=False)
    t = make_collection("TVStale", promoted=True)
    plex = make_plex({"Movies": make_section([m]), "TV Shows": make_section([t])})
    result = pinning.apply_pins(plex, ["Movies", "TV Shows"], pins(("Movies", "MoviePin")), {})
    assert result.pinned == pins(("Movies", "MoviePin"))
    assert result.unpinned == pins(("TV Shows", "TVStale"))


def test_same_title_across_libraries_managed_independently():
    # "Featured" pinned in Movies (resolved) must stay; "Featured" pinned in TV
    # (not resolved) must be swept — they are different collections.
    movies_featured = make_collection("Featured", promoted=True)
    tv_featured = make_collection("Featured", promoted=True)
    plex = make_plex({
        "Movies": make_section([movies_featured]),
        "TV Shows": make_section([tv_featured]),
    })
    result = pinning.apply_pins(plex, ["Movies", "TV Shows"], pins(("Movies", "Featured")), {})
    movies_featured._hub.demoteHome.assert_not_called()   # resolved for Movies → kept
    tv_featured._hub.demoteHome.assert_called_once()       # not resolved for TV → swept
    assert result.unchanged == pins(("Movies", "Featured"))
    assert result.unpinned == pins(("TV Shows", "Featured"))


def test_shared_promoted_counts_as_promoted():
    # own-home off but shared-home on (external tampering / divergence):
    # still counts as promoted so an unresolved collection gets swept.
    coll = make_collection("Divergent", promoted=False)
    coll._hub.promotedToSharedHome = True
    plex = make_plex({"Movies": make_section([coll])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    coll._hub.demoteHome.assert_called_once()
    coll._hub.demoteShared.assert_called_once()
    assert result.unpinned == pins(("Movies", "Divergent"))


def test_visibility_read_once_per_collection():
    # fix 1: read visibility a single time per collection, reuse for the pin op.
    coll = make_collection("Once", promoted=False)
    plex = make_plex({"Movies": make_section([coll])})
    pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Once")), {})
    coll.visibility.assert_called_once()


def test_promotion_state_read_error_treated_as_not_promoted():
    coll = make_collection("Weird", promoted=False)
    coll.visibility.side_effect = RuntimeError("boom")
    plex = make_plex({"Movies": make_section([coll])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    # promotion state unreadable → treated as not promoted; not resolved → no-op, no crash
    assert result.unpinned == []
