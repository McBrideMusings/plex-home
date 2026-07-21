from __future__ import annotations
import logging
from dataclasses import dataclass, field

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from .resolver import ResolvedPin

log = logging.getLogger(__name__)


@dataclass
class OrderResult:
    moved: list[str] = field(default_factory=list)    # hubs successfully repositioned
    failed: list[str] = field(default_factory=list)   # hubs whose Move Hub call errored


def _managed_hubs_by_title(section) -> dict[str, object]:
    """Map managed-hub title → ManagedHub for one library section.

    Only managed recommendation hubs (promoted collections) are returned by
    ``section.managedHubs()`` — Plex system hubs never surface here.
    """
    by_title: dict[str, object] = {}
    for hub in section.managedHubs():
        title = getattr(hub, "title", None)
        if title is not None:
            by_title[title] = hub
    return by_title


def apply_order(plex: PlexServer, library_names: list[str], resolved: list[ResolvedPin]) -> OrderResult:
    """Reorder each library's home-screen managed hubs to match slot order (issue #9).

    The Plex Move Hub API is per-section: ``ManagedHub.move`` targets the hub's
    own library, and an ``after=`` anchor from a different section is a foreign
    identifier Plex cannot honor. So ordering is applied **one library at a
    time** — for each library, the resolved titles that belong to it are walked
    in order, the first moved to the top (``after=None``) and each subsequent
    one after the previous, resetting the anchor at every library boundary.

    Only hubs whose title matches a resolved collection are moved, so Plex
    system hubs are never reordered. A Move Hub error on a single hub is logged
    and does not abort the rest. A library with fewer than two matching hubs
    needs no move.
    """
    result = OrderResult()

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
            by_title = _managed_hubs_by_title(section)
        except Exception as e:
            log.warning("Could not list managed hubs in %r: %s — skipping", name, e)
            continue

        ordered = [
            (p.title, by_title[p.title])
            for p in resolved
            if p.library == name and p.title in by_title
        ]
        if len(ordered) < 2:
            continue

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
