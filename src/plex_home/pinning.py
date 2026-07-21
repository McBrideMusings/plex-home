from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from .history import record_pins
from .resolver import ResolvedPin

log = logging.getLogger(__name__)


@dataclass
class PinResult:
    pinned: list[ResolvedPin] = field(default_factory=list)      # newly promoted this run
    unpinned: list[ResolvedPin] = field(default_factory=list)    # demoted this run
    unchanged: list[ResolvedPin] = field(default_factory=list)   # already promoted and still resolved
    missing: list[ResolvedPin] = field(default_factory=list)     # resolved but no matching live collection
    history: dict[tuple[str, str], datetime] = field(default_factory=dict)


def _iter_live_collections(plex: PlexServer, library_names: list[str]):
    """Yield ``(library_name, live plexapi Collection)`` across the managed libraries.

    Only user collections are returned — Plex system hubs (Recently Added,
    Continue Watching, On Deck) are not collections and never surface here.
    The library name is yielded alongside each collection because collection
    titles are only unique within a library.
    """
    for name in library_names:
        try:
            section = plex.library.section(name)
        except NotFound:
            log.warning("Library %r not found during pin run — skipping", name)
            continue
        except Exception as e:
            log.warning("Could not fetch library %r during pin run: %s — skipping", name, e)
            continue
        try:
            for coll in section.collections():
                yield name, coll
        except Exception as e:
            log.warning("Could not list collections in %r: %s — skipping", name, e)


def apply_pins(
    plex: PlexServer,
    library_names: list[str],
    resolved: list[ResolvedPin],
    history: dict[tuple[str, str], datetime],
) -> PinResult:
    """Fully manage the pinned home screen (ADR-0002).

    Pin every collection in ``resolved`` that is not already promoted, unpin
    every currently-promoted collection that is not in ``resolved``, and leave
    already-pinned-and-resolved collections untouched. Collections are keyed by
    ``(library, title)``, so two collections sharing a title in different
    libraries are managed independently. Records the pins that remain after the
    run in the repeat-block history and returns it on the result. A Plex error
    on a single pin/unpin is logged and does not abort the rest of the run.
    """
    resolved_set = set(resolved)
    result = PinResult(history=dict(history))

    live_by_key: dict[ResolvedPin, object] = {}
    hub_by_key: dict[ResolvedPin, object] = {}
    promoted: set[ResolvedPin] = set()
    for name, coll in _iter_live_collections(plex, library_names):
        title = getattr(coll, "title", None)
        if title is None:
            continue
        key = ResolvedPin(name, title)
        live_by_key[key] = coll
        try:
            hub = coll.visibility()
        except Exception as e:
            log.error("Could not read promotion state for %r in %r: %s", title, name, e)
            continue
        hub_by_key[key] = hub
        if hub.promotedToOwnHome or hub.promotedToSharedHome:
            promoted.add(key)

    to_pin = [p for p in resolved if p not in promoted]
    to_unpin = [p for p in promoted if p not in resolved_set]

    for pin in to_pin:
        coll = live_by_key.get(pin)
        if coll is None:
            log.warning("Resolved collection %r in %r not found in Plex — cannot pin", pin.title, pin.library)
            result.missing.append(pin)
            continue
        try:
            hub = hub_by_key.get(pin) or coll.visibility()
            hub.promoteHome()
            hub.promoteShared()
            result.pinned.append(pin)
            log.info("Pinned %r in %r", pin.title, pin.library)
        except Exception as e:
            log.error("Failed to pin %r in %r: %s", pin.title, pin.library, e)

    for pin in to_unpin:
        coll = live_by_key.get(pin)
        if coll is None:
            continue
        try:
            hub = hub_by_key.get(pin) or coll.visibility()
            hub.demoteHome()
            hub.demoteShared()
            result.unpinned.append(pin)
            log.info("Unpinned %r in %r", pin.title, pin.library)
        except Exception as e:
            log.error("Failed to unpin %r in %r: %s", pin.title, pin.library, e)

    result.unchanged = [p for p in resolved if p in promoted and p in live_by_key]

    pinned_now = result.pinned + result.unchanged
    result.history = record_pins([(p.library, p.title) for p in pinned_now], history)

    log.info(
        "Pin run complete: %d pinned, %d unpinned, %d unchanged, %d missing",
        len(result.pinned), len(result.unpinned), len(result.unchanged), len(result.missing),
    )
    return result
