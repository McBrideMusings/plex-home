from __future__ import annotations
import random
import logging
from datetime import datetime

from config import Config, FixedSlot, PickSlot
from plex_client import CollectionInfo
from history import is_blocked
from eligibility import eligible_collections

log = logging.getLogger(__name__)


def resolve_slots(
    config: Config,
    all_collections: dict[str, list[CollectionInfo]],
    history: dict[str, datetime],
    now: datetime,
) -> list[str]:
    used: set[str] = set()
    resolved: list[str] = []

    for slot in config.home:
        if isinstance(slot, FixedSlot):
            resolved.append(slot.collection)
            used.add(slot.collection)

        elif isinstance(slot, PickSlot):
            picked = _resolve_pick(slot, config, all_collections, history, used, now)
            if picked is not None:
                resolved.append(picked)
                used.add(picked)

    return resolved


def _resolve_pick(
    slot: PickSlot,
    config: Config,
    all_collections: dict[str, list[CollectionInfo]],
    history: dict[str, datetime],
    used: set[str],
    now: datetime,
) -> str | None:
    for group_name in slot.groups:
        group = config.groups[group_name]
        colls = eligible_collections(group, all_collections, config.cadence.min_items_for_pinning, now)
        if colls is None:
            continue

        rbh = group.repeat_block_hours if group.repeat_block_hours is not None else config.cadence.repeat_block_hours
        available = [
            c for c in colls
            if c.title not in used and not is_blocked(c.title, history, rbh, now)
        ]
        if not available:
            continue

        chosen = random.choice(available)
        log.info("Slot pick: group %r → %r", group_name, chosen.title)
        return chosen.title

    log.info("Pick slot with groups %r resolved to nothing — skipping", slot.groups)
    return None
