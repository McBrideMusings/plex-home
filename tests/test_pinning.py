from datetime import datetime, timezone
from unittest.mock import MagicMock
from plexapi.exceptions import NotFound

from plex_home import pinning
from plex_home.resolver import ResolvedPin


def make_hub(
    title: str,
    *,
    identifier: str | None = None,
    n: int = 0,
    own: bool = False,
    shared: bool = False,
    recommended: bool = False,
    deletable: bool = True,
) -> MagicMock:
    hub = MagicMock()
    hub.title = title
    hub.identifier = identifier if identifier is not None else f"custom.collection.1.{n}"
    hub.promotedToOwnHome = own
    hub.promotedToSharedHome = shared
    hub.promotedToRecommended = recommended
    hub.deletable = deletable
    return hub


def system_hub(title: str, identifier: str, *, own=False, shared=False, recommended=False) -> MagicMock:
    return make_hub(title, identifier=identifier, own=own, shared=shared, recommended=recommended, deletable=False)


def make_collection(title: str) -> MagicMock:
    """A live collection object for the re-create path — visibility() → vis hub.

    A never-promoted collection's visibility reports all flags off, so re-pinning
    it classifies as newly ``pinned`` (not ``unchanged``).
    """
    coll = MagicMock()
    coll.title = title
    vis = MagicMock()
    vis.promotedToOwnHome = False
    vis.promotedToSharedHome = False
    vis.promotedToRecommended = False
    coll.visibility.return_value = vis
    coll._vis = vis
    return coll


def make_section(hubs: list[MagicMock], collections: list[MagicMock] | None = None) -> MagicMock:
    section = MagicMock()
    section.managedHubs.return_value = hubs
    colls = {c.title: c for c in (collections or [])}

    def get_coll(title):
        if title not in colls:
            raise NotFound(title)
        return colls[title]

    section.collection.side_effect = get_coll
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


# --- promoting resolved pins ---

def test_resolved_collection_promoted():
    hub = make_hub("Halloween", own=False)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Halloween")), {})
    hub.updateVisibility.assert_called_once_with(home=True, shared=True)
    hub.remove.assert_not_called()
    assert result.pinned == pins(("Movies", "Halloween"))
    assert result.removed == []


def test_mirror_recommended_force_promotes_recommended():
    hub = make_hub("Halloween", own=False)
    plex = make_plex({"Movies": make_section([hub])})
    pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Halloween")), {}, mirror_recommended=True)
    hub.updateVisibility.assert_called_once_with(home=True, shared=True, recommended=True)


def test_default_leaves_recommended_untouched():
    hub = make_hub("Halloween", own=False)
    plex = make_plex({"Movies": make_section([hub])})
    pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Halloween")), {})
    hub.updateVisibility.assert_called_once_with(home=True, shared=True)   # no recommended kwarg


def test_resolved_system_hub_promoted():
    hub = system_hub("Recently Added Movies", "movie.recentlyadded", own=False)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Recently Added Movies")), {})
    hub.updateVisibility.assert_called_once_with(home=True, shared=True)
    hub.remove.assert_not_called()
    assert result.pinned == pins(("Movies", "Recently Added Movies"))


def test_already_pinned_resolved_is_unchanged():
    hub = make_hub("Keep", own=True)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Keep")), {})
    hub.updateVisibility.assert_called_once_with(home=True, shared=True)   # idempotent re-assert
    hub.remove.assert_not_called()
    assert result.unchanged == pins(("Movies", "Keep"))
    assert result.pinned == []


def test_never_managed_resolved_collection_recreated():
    coll = make_collection("Fresh")
    plex = make_plex({"Movies": make_section([], collections=[coll])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Fresh")), {})
    coll.visibility.assert_called_once()
    coll._vis.updateVisibility.assert_called_once_with(home=True, shared=True)
    assert result.pinned == pins(("Movies", "Fresh"))


def test_resolved_missing_entirely():
    plex = make_plex({"Movies": make_section([], collections=[])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Ghost")), {})
    assert result.missing == pins(("Movies", "Ghost"))
    assert result.pinned == []
    assert ("Movies", "Ghost") not in result.history


# --- clean-slate: removing non-resolved collections ---

def test_nonresolved_promoted_collection_removed():
    hub = make_hub("Stale", own=True)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    hub.remove.assert_called_once()
    assert result.removed == pins(("Movies", "Stale"))
    assert result.unpinned == []


def test_nonresolved_unpinned_collection_still_removed():
    # The clutter case: a collection with ALL flags off but still a managed row.
    hub = make_hub("Clutter", own=False, shared=False, recommended=False)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    hub.remove.assert_called_once()
    assert result.removed == pins(("Movies", "Clutter"))


def test_recommended_only_collection_removed():
    hub = make_hub("RecOnly", own=False, shared=False, recommended=True)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    hub.remove.assert_called_once()
    assert result.removed == pins(("Movies", "RecOnly"))


def test_shared_only_collection_removed():
    hub = make_hub("Divergent", own=False, shared=True)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    hub.remove.assert_called_once()
    assert result.removed == pins(("Movies", "Divergent"))


# --- system hubs: demote, never remove ---

def test_nonresolved_promoted_system_hub_demoted():
    hub = system_hub("Top Rated TV", "tv.toprated", own=True)
    plex = make_plex({"TV Shows": make_section([hub])})
    result = pinning.apply_pins(plex, ["TV Shows"], [], {})
    hub.updateVisibility.assert_called_once_with(recommended=False, home=False, shared=False)
    hub.remove.assert_not_called()
    assert result.unpinned == pins(("TV Shows", "Top Rated TV"))
    assert result.removed == []


def test_nonresolved_unpromoted_system_hub_left_alone():
    hub = system_hub("Rediscover", "tv.rediscover", own=False, shared=False, recommended=False)
    plex = make_plex({"TV Shows": make_section([hub])})
    result = pinning.apply_pins(plex, ["TV Shows"], [], {})
    hub.updateVisibility.assert_not_called()
    hub.remove.assert_not_called()
    assert result.unpinned == []
    assert result.removed == []


# --- collection wins on title collision ---

def test_collection_wins_on_title_collision():
    # section 1 holds a system 'movie.recentlyreleased' AND a collection, both
    # titled 'Recently Released Movies'. The resolved title must match the
    # collection; the system twin is left alone.
    sys = system_hub("Recently Released Movies", "movie.recentlyreleased", own=False)
    coll = make_hub("Recently Released Movies", identifier="custom.collection.1.170758", own=True)
    plex = make_plex({"Movies": make_section([sys, coll])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Recently Released Movies")), {})
    coll.updateVisibility.assert_called_once_with(home=True, shared=True)  # kept + pinned
    coll.remove.assert_not_called()
    sys.updateVisibility.assert_not_called()                              # unpromoted twin untouched
    sys.remove.assert_not_called()
    assert result.unchanged == pins(("Movies", "Recently Released Movies"))
    assert result.removed == []


def test_collection_wins_even_when_unmanaged_and_system_twin_present():
    # The collection was swept last cycle → absent from managedHubs — but its
    # promoted system twin is present. Collection-wins must re-create the
    # collection AND demote the stray system twin (not promote it).
    sys = system_hub("Recently Released Movies", "movie.recentlyreleased", own=True)
    coll = make_collection("Recently Released Movies")
    plex = make_plex({"Movies": make_section([sys], collections=[coll])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Recently Released Movies")), {})
    coll._vis.updateVisibility.assert_called_once_with(home=True, shared=True)  # collection re-created
    sys.updateVisibility.assert_called_once_with(recommended=False, home=False, shared=False)  # twin demoted
    assert result.pinned == pins(("Movies", "Recently Released Movies"))
    assert result.unpinned == pins(("Movies", "Recently Released Movies"))


# --- history ---

def test_history_records_pinned_and_unchanged():
    new = make_hub("New", own=False)
    keep = make_hub("Keep", own=True)
    plex = make_plex({"Movies": make_section([new, keep])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "New"), ("Movies", "Keep")), {})
    assert set(result.history.keys()) == {("Movies", "New"), ("Movies", "Keep")}
    assert all(isinstance(v, datetime) for v in result.history.values())


def test_removed_and_demoted_keys_not_in_history():
    stale = make_hub("Stale", own=True)                          # removed
    sys = system_hub("Top Rated", "movie.toprated", own=True)    # demoted
    plex = make_plex({"Movies": make_section([stale, sys])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    assert ("Movies", "Stale") not in result.history
    assert ("Movies", "Top Rated") not in result.history


def test_existing_history_preserved():
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    hub = make_hub("New", own=False)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "New")), {("Movies", "Older"): old})
    assert result.history[("Movies", "Older")] == old
    assert ("Movies", "New") in result.history


# --- resilience: one Plex error does not abort the run ---

def test_pin_error_does_not_abort_run():
    bad = make_hub("Bad", own=False)
    bad.updateVisibility.side_effect = RuntimeError("plex 500")
    good = make_hub("Good", own=False)
    plex = make_plex({"Movies": make_section([bad, good])})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "Bad"), ("Movies", "Good")), {})
    assert result.pinned == pins(("Movies", "Good"))


def test_remove_error_does_not_abort_run():
    bad = make_hub("Bad", own=True)
    bad.remove.side_effect = RuntimeError("plex 500")
    good = make_hub("Good", own=True)
    plex = make_plex({"Movies": make_section([bad, good])})
    result = pinning.apply_pins(plex, ["Movies"], [], {})
    assert result.removed == pins(("Movies", "Good"))
    good.remove.assert_called_once()


# --- multi-library ---

def test_missing_library_skipped_not_crashed():
    hub = make_hub("A", own=False)
    plex = make_plex({"Movies": make_section([hub])})
    result = pinning.apply_pins(plex, ["Movies", "Nonexistent"], pins(("Movies", "A")), {})
    assert result.pinned == pins(("Movies", "A"))


def test_managed_hubs_fetch_error_skips_library():
    section = MagicMock()
    section.managedHubs.side_effect = RuntimeError("boom")
    plex = make_plex({"Movies": section})
    result = pinning.apply_pins(plex, ["Movies"], pins(("Movies", "A")), {})
    assert result.pinned == []


def test_multiple_libraries_managed_together():
    m = make_hub("MoviePin", own=False)
    t = make_hub("TVStale", own=True)
    plex = make_plex({"Movies": make_section([m]), "TV Shows": make_section([t])})
    result = pinning.apply_pins(plex, ["Movies", "TV Shows"], pins(("Movies", "MoviePin")), {})
    assert result.pinned == pins(("Movies", "MoviePin"))
    assert result.removed == pins(("TV Shows", "TVStale"))


def test_same_title_across_libraries_managed_independently():
    # "Featured" resolved for Movies must stay; "Featured" in TV (not resolved)
    # is removed — different collections keyed by (library, title).
    movies_featured = make_hub("Featured", own=True)
    tv_featured = make_hub("Featured", own=True)
    plex = make_plex({
        "Movies": make_section([movies_featured]),
        "TV Shows": make_section([tv_featured]),
    })
    result = pinning.apply_pins(plex, ["Movies", "TV Shows"], pins(("Movies", "Featured")), {})
    movies_featured.remove.assert_not_called()   # resolved for Movies → kept
    tv_featured.remove.assert_called_once()        # not resolved for TV → removed
    assert result.unchanged == pins(("Movies", "Featured"))
    assert result.removed == pins(("TV Shows", "Featured"))
