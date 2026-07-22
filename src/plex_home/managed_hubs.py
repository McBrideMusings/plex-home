"""Shared managed-hub primitives (system + collection, kind-agnostic).

The daemon's pin/order engines and the imperative CLI (``hubs.py``) all operate
on Plex ``ManagedHub`` rows — the entries of the "Manage Recommendations" list,
which mixes built-in *system* hubs (Recently Added, Top Rated) with *collection*
hubs. This module holds the small kind-agnostic helpers those three modules
share so none of them owns the others' internals (ADR-0007).

A row is a "collection" iff its identifier is a ``custom.collection.*`` — every
other identifier (``movie.recentlyadded``, ``tv.toprated``, …) is a system hub
Plex owns. System hubs cannot be removed from the list; collections can.
"""
from __future__ import annotations
import logging
from typing import Iterator

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

log = logging.getLogger(__name__)

COLLECTION_ID_PREFIX = "custom.collection."


def is_collection(hub: object) -> bool:
    """True if the hub is a user collection (removable), False for a system hub."""
    return (getattr(hub, "identifier", "") or "").startswith(COLLECTION_ID_PREFIX)


def hub_title(hub: object) -> str | None:
    return getattr(hub, "title", None)


def on_home(hub: object) -> bool:
    """Promoted to the home screen (own or friends' home) — home-screen membership."""
    return bool(getattr(hub, "promotedToOwnHome", False) or getattr(hub, "promotedToSharedHome", False))


def promoted_anywhere(hub: object) -> bool:
    """Promoted on ANY of the three visibility axes — home, friends' home, or
    Library Recommended. This is the full-management scope (ADR-0007): a hub is
    "in play" if it shows up anywhere, so a stray recommended-only collection is
    still swept."""
    return on_home(hub) or bool(getattr(hub, "promotedToRecommended", False))


def title_map(hubs: list) -> dict[str, object]:
    """Map hub title → hub for one library, **collection wins on title collision**.

    A library can contain two hubs of the same title — e.g. section 1 has both a
    system ``movie.recentlyreleased`` and a collection both titled "Recently
    Released Movies". Fixed slots name a collection, so when a title is ambiguous
    the collection is the match; a system hub is chosen only when no collection
    in the library carries the title.
    """
    out: dict[str, object] = {}
    for hub in hubs:
        title = hub_title(hub)
        if title is None:
            continue
        existing = out.get(title)
        if existing is None or (is_collection(hub) and not is_collection(existing)):
            out[title] = hub
    return out


def iter_library_hubs(
    plex: PlexServer, library_names: list[str], action: str
) -> Iterator[tuple[str, object, list]]:
    """Yield ``(name, section, hubs)`` for each library, skipping the unreadable ones.

    The daemon's writers (``apply_pins``, ``apply_order``) both open each library
    and list its managed hubs before doing their own work; that fetch-and-skip
    boilerplate lives here with the other kind-agnostic primitives (ADR-0007).
    ``action`` names the caller ("pin run", "ordering") for the log lines. A
    missing library, a section-fetch error, or a managed-hub listing error each
    logs a warning and drops just that library, so one bad section never aborts
    the whole run.
    """
    for name in library_names:
        try:
            section = plex.library.section(name)
        except NotFound:
            log.warning("Library %r not found during %s — skipping", name, action)
            continue
        except Exception as e:
            log.warning("Could not fetch library %r during %s: %s — skipping", name, action, e)
            continue
        try:
            hubs = list(section.managedHubs())
        except Exception as e:
            log.warning("Could not list managed hubs in %r: %s — skipping", name, e)
            continue
        yield name, section, hubs


def realize_order(section, ordered_titles: list[str], dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Reorder a library's pinned hubs to ``ordered_titles`` (top → bottom).

    Plex's Move Hub API only reliably honors ``move(after=None)`` — move to top —
    for *every* hub. Anchoring after a specific hub silently fails for system rows
    and Plex-curated hubs ("Recently Released", "… On Plex"), which otherwise look
    like ordinary collections. Moving each hub to the top in **reverse** target
    order therefore builds any arrangement. Re-fetch the pinned hubs by title on
    each step because curated hub identifiers regenerate over time, so a hub held
    across moves can go stale and no-op.

    Returns ``(moved, failed)`` — titles that were successfully repositioned and
    titles whose move raised or that were no longer pinned — both in target order.
    A per-hub move error is logged and does not abort the rest.
    """
    if dry_run:
        log.info("[dry-run] set order: %s", ", ".join(ordered_titles))
        return list(ordered_titles), []

    done: set[str] = set()
    bad: set[str] = set()
    for title in reversed(ordered_titles):
        try:
            section.reload()
        except Exception:
            pass
        hub = next((h for h in section.managedHubs() if on_home(h) and hub_title(h) == title), None)
        if hub is None:
            bad.add(title)
            continue
        try:
            hub.move(after=None)
            done.add(title)
        except Exception as e:
            log.error("Failed to move hub %r into position: %s", title, e)
            bad.add(title)

    moved = [t for t in ordered_titles if t in done]
    failed = [t for t in ordered_titles if t in bad and t not in done]
    return moved, failed
