from __future__ import annotations
import logging
from dataclasses import dataclass, field

from plexapi.server import PlexServer

from .resolver import ResolvedPin
from .managed_hubs import title_map, realize_order, iter_library_hubs

log = logging.getLogger(__name__)


@dataclass
class OrderMismatch:
    """A library whose home order did not match slot order after reordering.

    The move did not error — Plex accepted it but did not apply it — so this is
    kept apart from ``failed`` (which is for moves that raised or vanished).
    """
    library: str
    target: list[str]   # the slot order we asked Plex for
    actual: list[str]   # the order Plex actually produced (filtered to target)


@dataclass
class OrderResult:
    moved: list[str] = field(default_factory=list)    # hubs successfully repositioned
    failed: list[str] = field(default_factory=list)   # hubs whose move errored or vanished
    mismatch: list[OrderMismatch] = field(default_factory=list)  # libraries left in the wrong order


def apply_order(plex: PlexServer, library_names: list[str], resolved: list[ResolvedPin]) -> OrderResult:
    """Reorder each library's pinned hubs to match slot order (ADR-0007).

    Ordering is per-section: a hub's ``move`` targets its own library, so each
    library is ordered independently. For each library, the resolved titles that
    are present in the Managed Recommendations list are walked in slot order and
    realized via move-to-top (``managed_hubs.realize_order``), which reliably
    positions system and Plex-curated hubs that reject an ``after=`` anchor.

    Only resolved titles are moved: the resolved pins are lifted to the top in
    slot order, so any system hubs the config doesn't name settle below them
    while keeping their order relative to one another. A library with fewer than
    two resolved hubs needs no move.
    """
    result = OrderResult()

    for name, section, hubs in iter_library_hubs(plex, library_names, "ordering"):
        present = title_map(hubs)

        ordered = [p.title for p in resolved if p.library == name and p.title in present]
        if len(ordered) < 2:
            continue

        moved, failed, actual = realize_order(section, ordered, dry_run=False)
        result.moved.extend(moved)
        result.failed.extend(failed)
        if actual != ordered:
            result.mismatch.append(OrderMismatch(name, ordered, actual))
            log.warning(
                "Hub order in %r did not verify: target=%s actual=%s",
                name, ordered, actual,
            )

    log.info(
        "Hub ordering complete: %d moved, %d failed, %d mismatched",
        len(result.moved), len(result.failed), len(result.mismatch),
    )
    return result
