"""Managed-hub data access and operations for the CLI (ADR-0005).

The CLI treats the home screen as a per-library, ordered, indexed list of
*pinned* hubs. Every row is a Plex ``ManagedHub`` — system hubs and collection
hubs are handled uniformly (``move`` / ``promoteHome`` / ``demoteHome``). This
module is the imperative, live layer: it reads and mutates Plex directly and
never touches the config. A running daemon cycle reconciles the home screen
back to the config (ADR-0002), so CLI mutations are ephemeral against it.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

from .config import Config, FixedSlot, PickSlot
from .managed_hubs import is_collection, on_home, realize_order
from .eligibility import matches_group_membership
from .plex_client import CollectionInfo, collection_infos

log = logging.getLogger(__name__)


class HubError(Exception):
    """A CLI hub operation could not be completed (bad target, missing library)."""


@dataclass
class HubView:
    library: str
    index: int            # position among pinned hubs in this library, top = 0
    title: str
    kind: str             # "system" | "collection"
    config_managed: bool  # collection reachable from the config's slots/groups


def _kind(hub: object) -> str:
    return "collection" if is_collection(hub) else "system"


def config_managed_titles(config: Config, library: str, collections: list[CollectionInfo]) -> set[str]:
    """Collection titles the config can pin in ``library`` (static reachability).

    A title is config-managed if this library's ``home`` section either names it in
    a fixed slot, or references (via a ``pick`` slot) a group whose structural
    membership it satisfies — ``include_labels``/``include_collections`` minus
    ``exclude_*``, resolved against the live ``collections`` and their labels
    (:func:`eligibility.matches_group_membership`).

    This answers "could the config pin this hub", not "would this cycle keep it":
    date/time windows, the min-items threshold, repeat-block recency, and per-cycle
    random pick choice are all ignored, so the tag is stable across cycles. Groups
    defined but not referenced by any pick slot in this section cannot be pinned
    here and are excluded (ADR-0006 — a group's library is the section that uses
    it). See issue #16.
    """
    titles: set[str] = set()
    referenced_groups: set[str] = set()
    for slot in config.home.get(library, []):
        if isinstance(slot, FixedSlot):
            titles.add(slot.collection)
        elif isinstance(slot, PickSlot):
            referenced_groups.update(slot.groups)
    for gname in referenced_groups:
        group = config.groups[gname]
        for coll in collections:
            if matches_group_membership(group, coll):
                titles.add(coll.title)
    return titles


def _section(plex: PlexServer, library: str):
    try:
        return plex.library.section(library)
    except NotFound:
        raise HubError(f"Library {library!r} not found")


def _pinned_hubs(section) -> list:
    """Managed hubs currently pinned to home, in home-screen order."""
    return [h for h in section.managedHubs() if on_home(h)]


def _resolve(pinned: list, target: str) -> int:
    """Resolve a target (0-based index string or exact title) to a position in ``pinned``."""
    if target.isdigit():
        idx = int(target)
        if idx < 0 or idx >= len(pinned):
            raise HubError(f"Index {idx} out of range — {len(pinned)} pinned hub(s)")
        return idx
    for i, hub in enumerate(pinned):
        if getattr(hub, "title", "") == target:
            return i
    raise HubError(f"No pinned hub matching {target!r}")


def _target_order(pinned: list, from_index: int, to_index: int) -> list[str]:
    """Title order with the hub at ``from_index`` repositioned to ``to_index``."""
    titles = [getattr(h, "title", "") for h in pinned]
    moving = titles.pop(from_index)
    to_index = max(0, min(to_index, len(titles)))
    titles.insert(to_index, moving)
    return titles


def _verify_placement(section, title: str, requested: int) -> int | None:
    """Re-read the pinned order and warn if ``title`` did not land at ``requested``.

    Some hubs cannot be anchored after (system rows, Plex-curated "On Plex" /
    "Recently Released" hubs), so a move can silently place the hub short of the
    requested index. Report the actual position honestly instead of assuming
    success. Returns the actual index (or None if the hub is no longer pinned).
    """
    section.reload()
    pinned = _pinned_hubs(section)
    actual = next((i for i, h in enumerate(pinned) if getattr(h, "title", "") == title), None)
    if actual is not None and actual != requested:
        log.warning(
            "Requested index %d for %r but Plex placed it at %d — the hub(s) below it "
            "cannot be anchored after (system rows or Plex-curated 'On Plex' / "
            "'Recently Released' hubs stay locked toward the bottom).",
            requested, title, actual,
        )
    return actual


def list_pinned(plex: PlexServer, library_names: list[str], config: Config) -> dict[str, list[HubView]]:
    """Per-library ordered, indexed list of pinned hubs (system + collection).

    Fetches each library's live collections once (for their labels) so the
    ``config_managed`` tag accounts for label-group membership, not just names.
    """
    out: dict[str, list[HubView]] = {}
    for name in library_names:
        section = _section(plex, name)
        managed_titles = config_managed_titles(config, name, collection_infos(section, name))
        views: list[HubView] = []
        for i, hub in enumerate(_pinned_hubs(section)):
            kind = _kind(hub)
            title = getattr(hub, "title", "")
            views.append(HubView(
                library=name,
                index=i,
                title=title,
                kind=kind,
                config_managed=(kind == "collection" and title in managed_titles),
            ))
        out[name] = views
    return out


def pinned_titles(plex: PlexServer, library: str) -> set[str]:
    """Titles of hubs currently pinned in ``library`` (system + collection).

    A lightweight lookup for library resolution: no live-collection fetch and no
    config-membership tagging, unlike :func:`list_pinned`. Callers that only need
    to know *which* library holds a pinned title use this to avoid that cost.
    """
    section = _section(plex, library)
    return {getattr(h, "title", "") for h in _pinned_hubs(section)}


def list_available(plex: PlexServer, library_names: list[str]) -> dict[str, list[str]]:
    """Per-library collection titles not currently pinned (the pool ``pin`` draws from)."""
    out: dict[str, list[str]] = {}
    for name in library_names:
        section = _section(plex, name)
        pinned = {getattr(h, "title", "") for h in _pinned_hubs(section)}
        out[name] = sorted(c.title for c in section.collections() if c.title not in pinned)
    return out


def locate(plex: PlexServer, library: str, target: str) -> tuple[int, int]:
    """Return ``(current_index, pinned_count)`` for a target, for relative moves."""
    section = _section(plex, library)
    pinned = _pinned_hubs(section)
    return _resolve(pinned, target), len(pinned)


def pin(plex: PlexServer, library: str, title: str, to_index: int | None = None, dry_run: bool = False) -> None:
    """Promote a collection to home. Uses the Collection visibility path so a
    never-pinned collection (absent from ``managedHubs``) can still be pinned."""
    section = _section(plex, library)
    try:
        coll = section.collection(title)
    except NotFound:
        raise HubError(f"No collection titled {title!r} in {library!r}")
    if dry_run:
        where = "Plex-native position" if to_index is None else f"index {to_index}"
        log.info("[dry-run] pin %r in %r at %s", title, library, where)
        return
    hub = coll.visibility()
    hub.promoteHome()
    hub.promoteShared()
    log.info("Pinned %r in %r", title, library)
    if to_index is not None:
        section.reload()
        pinned = _pinned_hubs(section)
        order = _target_order(pinned, _resolve(pinned, title), to_index)
        realize_order(section, order, dry_run=False)
        actual = _verify_placement(section, title, to_index)
        if actual is not None:
            log.info("Placed %r at index %d in %r", title, actual, library)


def unpin(plex: PlexServer, library: str, target: str, dry_run: bool = False) -> None:
    """Demote a pinned hub (system or collection), addressed by index or title."""
    section = _section(plex, library)
    pinned = _pinned_hubs(section)
    hub = pinned[_resolve(pinned, target)]
    title = getattr(hub, "title", "")
    if dry_run:
        log.info("[dry-run] unpin %r in %r", title, library)
        return
    hub.demoteHome()
    hub.demoteShared()
    log.info("Unpinned %r in %r", title, library)


def move(plex: PlexServer, library: str, target: str, to_index: int, dry_run: bool = False) -> None:
    """Move a pinned hub to ``to_index`` in the home order, addressed by index or title."""
    section = _section(plex, library)
    pinned = _pinned_hubs(section)
    idx = _resolve(pinned, target)
    title = getattr(pinned[idx], "title", "")
    order = _target_order(pinned, idx, to_index)
    realize_order(section, order, dry_run)
    if not dry_run:
        _verify_placement(section, title, to_index)
