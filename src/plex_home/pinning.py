from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from .history import record_pins
from .resolver import ResolvedPin
from .managed_hubs import (
    is_collection,
    on_home,
    promoted_anywhere,
    hub_title,
    title_map,
    iter_library_hubs,
)

log = logging.getLogger(__name__)


@dataclass
class PinResult:
    pinned: list[ResolvedPin] = field(default_factory=list)      # resolved, newly promoted this run
    unchanged: list[ResolvedPin] = field(default_factory=list)   # resolved, already on home
    unpinned: list[ResolvedPin] = field(default_factory=list)    # system hub demoted (can't remove)
    removed: list[ResolvedPin] = field(default_factory=list)     # collection removed from managed list
    missing: list[ResolvedPin] = field(default_factory=list)     # resolved but no matching collection
    history: dict[tuple[str, str], datetime] = field(default_factory=dict)


def _resolve_target(section, by_title: dict, title: str):
    """Resolve a fixed-slot title to the live ``ManagedHub`` to promote.

    Collection wins on a title collision, and does so even when the collection is
    absent from the managed list (previously removed, or never promoted) while a
    same-titled *system* hub is present:

    - a managed **collection** of this title → use it directly;
    - else a **collection** in the library → re-create its record via
      ``visibility()`` — it beats any system twin;
    - else a managed **system** hub of this title → use it;
    - else → ``None`` (nothing matches; the pin is reported missing).
    """
    hub = by_title.get(title)
    if hub is not None and is_collection(hub):
        return hub
    try:
        return section.collection(title).visibility()
    except NotFound:
        return hub  # a system hub of this title, or None


def apply_pins(
    plex: PlexServer,
    library_names: list[str],
    resolved: list[ResolvedPin],
    history: dict[tuple[str, str], datetime],
    mirror_recommended: bool = False,
) -> PinResult:
    """Fully manage the home screen and the Managed Recommendations list (ADR-0007).

    The config is the complete truth for everything promoted anywhere. Per
    library, over ``section.managedHubs()`` (system + collection rows uniformly):

    1. Resolved pins → promote to home + friends' home. Library Recommended is
       left as-is by default; with ``mirror_recommended`` the resolved pins are
       also force-promoted to Recommended, so the Recommended tab mirrors Home.
       A resolved collection absent from the managed list (previously removed or
       never promoted) is re-created via its collection visibility.
    2. Non-resolved **collection** → ``remove()`` from the Managed Recommendations
       list entirely — the ``×`` in Plex, ``DELETE …/manage/{id}``. This does NOT
       delete the collection itself, only its recommendation record, so the list
       stays short instead of accreting every collection ever pinned.
    3. Non-resolved **system hub** that is promoted anywhere → demote all three
       axes (system hubs are not removable).

    Hubs are keyed by ``(library, title)``. On a title collision the collection
    wins (``title_map``). A Plex error on a single hub is logged and does not
    abort the rest. Records the pins that remain in the repeat-block history.
    """
    result = PinResult(history=dict(history))

    for name, section, hubs in iter_library_hubs(plex, library_names, "pin run"):
        by_title = title_map(hubs)

        # 1. Promote (or re-create) every resolved pin for this library. Track the
        #    exact live hub each pin resolved to (by identity) so the sweep leaves
        #    it alone — the resolved hub, and only it, is protected.
        kept: set[int] = set()
        for pin in [p for p in resolved if p.library == name]:
            target = _resolve_target(section, by_title, pin.title)
            if target is None:
                result.missing.append(pin)
                log.warning("Resolved hub %r in %r not found in Plex — cannot pin", pin.title, name)
                continue
            try:
                already = on_home(target)
                vis = {"home": True, "shared": True}
                if mirror_recommended:
                    vis["recommended"] = True
                target.updateVisibility(**vis)
                (result.unchanged if already else result.pinned).append(pin)
                kept.add(id(target))
                log.info("%s %r in %r", "Kept pinned" if already else "Pinned", pin.title, name)
            except Exception as e:
                log.error("Failed to pin %r in %r: %s", pin.title, name, e)

        # 2 + 3. Sweep every hub the config does not pin.
        for hub in hubs:
            if id(hub) in kept:
                continue
            title = hub_title(hub)
            if title is None:
                continue
            key = ResolvedPin(name, title)

            if is_collection(hub) and getattr(hub, "deletable", True):
                try:
                    hub.remove()
                    result.removed.append(key)
                    log.info("Removed managed collection %r in %r", title, name)
                except Exception as e:
                    log.error("Failed to remove %r in %r: %s", title, name, e)
            elif promoted_anywhere(hub):
                try:
                    hub.updateVisibility(recommended=False, home=False, shared=False)
                    result.unpinned.append(key)
                    log.info("Demoted system hub %r in %r", title, name)
                except Exception as e:
                    log.error("Failed to demote %r in %r: %s", title, name, e)

    pinned_now = result.pinned + result.unchanged
    result.history = record_pins([(p.library, p.title) for p in pinned_now], history)

    log.info(
        "Pin run complete: %d pinned, %d unchanged, %d removed, %d demoted, %d missing",
        len(result.pinned), len(result.unchanged), len(result.removed),
        len(result.unpinned), len(result.missing),
    )
    return result
