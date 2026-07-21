from __future__ import annotations
import random
import logging
from dataclasses import dataclass
from datetime import datetime

from .config import Config, FixedSlot, PickSlot
from .plex_client import CollectionInfo
from .history import is_blocked
from .eligibility import eligible_collections

log = logging.getLogger(__name__)

_default_rng = random.Random()


@dataclass(frozen=True)
class ResolvedPin:
    """A collection to pin, identified by (library, title).

    Collection titles are only unique within a library, so the library is part
    of the identity everywhere a pin is keyed, compared, or blocked. Frozen so
    it is hashable — used in the dedup set and as a dict key.
    """
    library: str
    title: str


def resolve_slots(
    config: Config,
    all_collections: dict[str, list[CollectionInfo]],
    history: dict[tuple[str, str], datetime],
    now: datetime,
    rng: random.Random | None = None,
) -> list[ResolvedPin]:
    if rng is None:
        rng = _default_rng
    used: set[ResolvedPin] = set()
    resolved: list[ResolvedPin] = []

    for library, slots in config.home.items():
        for slot in slots:
            if isinstance(slot, FixedSlot):
                pin = ResolvedPin(library, slot.collection)
                if pin in used:
                    continue
                resolved.append(pin)
                used.add(pin)

            elif isinstance(slot, PickSlot):
                picked = _resolve_pick(slot, library, config, all_collections, history, used, now, rng)
                if picked is not None:
                    pin = ResolvedPin(library, picked)
                    resolved.append(pin)
                    used.add(pin)

    return resolved


def _resolve_pick(
    slot: PickSlot,
    library: str,
    config: Config,
    all_collections: dict[str, list[CollectionInfo]],
    history: dict[tuple[str, str], datetime],
    used: set[ResolvedPin],
    now: datetime,
    rng: random.Random,
) -> str | None:
    for group_name in slot.groups:
        group = config.groups[group_name]
        colls = eligible_collections(group, library, all_collections, config.cadence.min_items_for_pinning, now)
        if colls is None:
            continue

        rbh = group.repeat_block_hours if group.repeat_block_hours is not None else config.cadence.repeat_block_hours
        available = [
            c for c in colls
            if ResolvedPin(library, c.title) not in used
            and not is_blocked(library, c.title, history, rbh, now)
        ]
        if not available:
            continue

        chosen = rng.choice(available)
        log.info("Slot pick: group %r → %r in %r", group_name, chosen.title, library)
        return chosen.title

    log.info("Pick slot with groups %r resolved to nothing — skipping", slot.groups)
    return None
