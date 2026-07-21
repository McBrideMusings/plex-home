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

from .config import Config, FixedSlot

log = logging.getLogger(__name__)

_COLLECTION_ID_PREFIX = "custom.collection."


class HubError(Exception):
    """A CLI hub operation could not be completed (bad target, missing library)."""


@dataclass
class HubView:
    library: str
    index: int            # position among pinned hubs in this library, top = 0
    title: str
    kind: str             # "system" | "collection"
    config_managed: bool  # collection reachable from the config's slots/groups


def _is_collection(hub: object) -> bool:
    return (getattr(hub, "identifier", "") or "").startswith(_COLLECTION_ID_PREFIX)


def _kind(hub: object) -> str:
    return "collection" if _is_collection(hub) else "system"


def _is_pinned(hub: object) -> bool:
    return bool(getattr(hub, "promotedToOwnHome", False) or getattr(hub, "promotedToSharedHome", False))


def config_collection_titles(config: Config) -> set[str]:
    """Collection titles the config can pin, by explicit name.

    Fixed-slot collections plus every group's ``include_collections``. Label-based
    group membership is dynamic and not included — this is a best-effort marker of
    collections the config explicitly references, used only to tag list output.
    """
    titles: set[str] = set()
    for slots in config.home.values():
        for slot in slots:
            if isinstance(slot, FixedSlot):
                titles.add(slot.collection)
    for group in config.groups.values():
        titles.update(group.include_collections)
    return titles


def _section(plex: PlexServer, library: str):
    try:
        return plex.library.section(library)
    except NotFound:
        raise HubError(f"Library {library!r} not found")


def _pinned_hubs(section) -> list:
    """Managed hubs currently pinned to home, in home-screen order."""
    return [h for h in section.managedHubs() if _is_pinned(h)]


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


def _realize_order(section, ordered_titles: list[str], dry_run: bool) -> None:
    """Reorder the pinned hubs to match ``ordered_titles`` (top → bottom).

    Plex's Move Hub API only honors ``move(after=None)`` — move to top — for
    *every* hub. Anchoring after a specific hub silently fails for system rows
    and Plex-curated hubs ("Recently Released", "… On Plex"), which otherwise
    look like ordinary collections. Moving each hub to the top in **reverse**
    target order therefore builds any arrangement reliably. Re-fetch by title on
    each step because curated hub identifiers regenerate over time, so a hub
    object held across moves can go stale and no-op.
    """
    if dry_run:
        log.info("[dry-run] set order: %s", ", ".join(ordered_titles))
        return
    for title in reversed(ordered_titles):
        section.reload()
        hub = next((h for h in _pinned_hubs(section) if getattr(h, "title", "") == title), None)
        if hub is not None:
            hub.move(after=None)


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
    """Per-library ordered, indexed list of pinned hubs (system + collection)."""
    managed_titles = config_collection_titles(config)
    out: dict[str, list[HubView]] = {}
    for name in library_names:
        section = _section(plex, name)
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
        _realize_order(section, order, dry_run=False)
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
    _realize_order(section, order, dry_run)
    if not dry_run:
        _verify_placement(section, title, to_index)
