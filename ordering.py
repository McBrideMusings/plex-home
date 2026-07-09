from __future__ import annotations
import logging
from dataclasses import dataclass, field

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

log = logging.getLogger(__name__)


@dataclass
class OrderResult:
    moved: list[str] = field(default_factory=list)    # hubs successfully repositioned
    failed: list[str] = field(default_factory=list)   # hubs whose Move Hub call errored


def _managed_hubs_by_title(plex: PlexServer, library_names: list[str]) -> dict[str, object]:
    """Map managed-hub title → ManagedHub across the managed libraries.

    Only managed recommendation hubs (promoted collections) are returned by
    ``section.managedHubs()`` — Plex system hubs never surface here.
    """
    by_title: dict[str, object] = {}
    for name in library_names:
        try:
            section = plex.library.section(name)
        except NotFound:
            log.warning("Library %r not found during ordering — skipping", name)
            continue
        except Exception as e:
            log.warning("Could not fetch library %r during ordering: %s — skipping", name, e)
            continue
        try:
            hubs = section.managedHubs()
        except Exception as e:
            log.warning("Could not list managed hubs in %r: %s — skipping", name, e)
            continue
        for hub in hubs:
            title = getattr(hub, "title", None)
            if title is not None:
                by_title[title] = hub
    return by_title


def apply_order(plex: PlexServer, library_names: list[str], resolved: list[str]) -> OrderResult:
    """Reorder the home-screen managed hubs to match the resolved slot order (issue #9).

    Walks ``resolved`` top-to-bottom and moves each managed hub after the
    previous one via the Plex Move Hub API (``ManagedHub.move``). The first hub
    is moved to the top (``after=None``). Only hubs whose title matches a
    resolved collection are moved, so Plex system hubs are never reordered. A
    Move Hub error on a single hub is logged with its name and does not abort
    the remaining moves. With fewer than two matching hubs no move is needed.
    """
    result = OrderResult()
    by_title = _managed_hubs_by_title(plex, library_names)

    ordered = [(t, by_title[t]) for t in resolved if t in by_title]
    if len(ordered) < 2:
        log.info("Ordering skipped: %d managed hub(s) to order — already in position", len(ordered))
        return result

    prev = None
    for title, hub in ordered:
        try:
            hub.move(after=prev)
        except Exception as e:
            log.error("Failed to move hub %r into position: %s", title, e)
            result.failed.append(title)
            continue
        result.moved.append(title)
        prev = hub

    log.info("Hub ordering complete: %d moved, %d failed", len(result.moved), len(result.failed))
    return result
