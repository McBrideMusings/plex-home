from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from history import record_pins

log = logging.getLogger(__name__)


@dataclass
class PinResult:
    pinned: list[str] = field(default_factory=list)      # newly promoted this run
    unpinned: list[str] = field(default_factory=list)    # demoted this run
    unchanged: list[str] = field(default_factory=list)   # already promoted and still resolved
    missing: list[str] = field(default_factory=list)     # resolved but no matching live collection
    history: dict[str, datetime] = field(default_factory=dict)


def _iter_live_collections(plex: PlexServer, library_names: list[str]):
    """Yield live plexapi Collection objects across the managed libraries.

    Only user collections are returned — Plex system hubs (Recently Added,
    Continue Watching, On Deck) are not collections and never surface here.
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
                yield coll
        except Exception as e:
            log.warning("Could not list collections in %r: %s — skipping", name, e)


def apply_pins(
    plex: PlexServer,
    library_names: list[str],
    resolved: list[str],
    label: str,
    history: dict[str, datetime],
) -> PinResult:
    """Fully manage the pinned home screen (ADR-0002).

    Pin every collection in ``resolved`` that is not already promoted, unpin
    every currently-promoted collection that is not in ``resolved``, and leave
    already-pinned-and-resolved collections untouched. Records the titles that
    remain pinned after the run in the repeat-block history and returns it on
    the result. A Plex error on a single pin/unpin is logged with the
    collection name and does not abort the rest of the run.
    """
    resolved_set = set(resolved)
    result = PinResult(history=dict(history))

    live_by_title: dict[str, object] = {}
    hub_by_title: dict[str, object] = {}
    promoted_titles: set[str] = set()
    for coll in _iter_live_collections(plex, library_names):
        title = getattr(coll, "title", None)
        if title is None:
            continue
        live_by_title[title] = coll
        try:
            hub = coll.visibility()
        except Exception as e:
            log.error("Could not read promotion state for %r: %s", title, e)
            continue
        hub_by_title[title] = hub
        if hub.promotedToOwnHome or hub.promotedToSharedHome:
            promoted_titles.add(title)

    to_pin = [t for t in resolved if t not in promoted_titles]
    to_unpin = [t for t in promoted_titles if t not in resolved_set]

    for title in to_pin:
        coll = live_by_title.get(title)
        if coll is None:
            log.warning("Resolved collection %r not found in Plex — cannot pin", title)
            result.missing.append(title)
            continue
        try:
            hub = hub_by_title.get(title) or coll.visibility()
            hub.promoteHome()
            hub.promoteShared()
            if label:
                try:
                    coll.addLabel(label)
                except Exception as e:
                    log.error("Failed to add label %r to %r: %s", label, title, e)
            result.pinned.append(title)
            log.info("Pinned %r", title)
        except Exception as e:
            log.error("Failed to pin %r: %s", title, e)

    for title in to_unpin:
        coll = live_by_title.get(title)
        if coll is None:
            continue
        try:
            hub = hub_by_title.get(title) or coll.visibility()
            if label:
                try:
                    current = [lbl.tag for lbl in (coll.labels or [])]
                    if label in current:
                        coll.removeLabel(label)
                except Exception as e:
                    log.error("Failed to remove label %r from %r: %s", label, title, e)
            hub.demoteHome()
            hub.demoteShared()
            result.unpinned.append(title)
            log.info("Unpinned %r", title)
        except Exception as e:
            log.error("Failed to unpin %r: %s", title, e)

    result.unchanged = [t for t in resolved if t in promoted_titles and t in live_by_title]

    pinned_now = result.pinned + result.unchanged
    result.history = record_pins(pinned_now, history)

    log.info(
        "Pin run complete: %d pinned, %d unpinned, %d unchanged, %d missing",
        len(result.pinned), len(result.unpinned), len(result.unchanged), len(result.missing),
    )
    return result
