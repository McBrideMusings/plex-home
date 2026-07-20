from __future__ import annotations
from datetime import datetime, date, time
from .config import Group
from .plex_client import CollectionInfo


def is_group_eligible(group: Group, now: datetime) -> bool:
    if group.date and not _date_in_range(group.date, now.date()):
        return False
    if group.time and not _time_in_range(group.time, now.time()):
        return False
    return True


def filter_collections(
    group: Group,
    collections: list[CollectionInfo],
    global_min_items: int,
) -> list[CollectionInfo]:
    min_items = group.min_items_for_pinning if group.min_items_for_pinning is not None else global_min_items
    has_include = bool(group.include_labels or group.include_collections)
    result = []
    for coll in collections:
        if coll.item_count < min_items:
            continue
        if has_include:
            label_match = any(lbl in coll.labels for lbl in group.include_labels)
            name_match = coll.title in group.include_collections
            if not (label_match or name_match):
                continue
        label_excluded = any(lbl in coll.labels for lbl in group.exclude_labels)
        name_excluded = coll.title in group.exclude_collections
        if label_excluded or name_excluded:
            continue
        result.append(coll)
    return result


def eligible_collections(
    group: Group,
    all_collections: dict[str, list[CollectionInfo]],
    global_min_items: int,
    now: datetime,
) -> list[CollectionInfo] | None:
    if not is_group_eligible(group, now):
        return None
    library_colls = all_collections.get(group.library, [])
    return filter_collections(group, library_colls, global_min_items)


def _date_in_range(date_constraint: str, today: date) -> bool:
    start_str, end_str = date_constraint.split("/")
    s_month, s_day = (int(x) for x in start_str.split("-"))
    e_month, e_day = (int(x) for x in end_str.split("-"))
    s = (s_month, s_day)
    e = (e_month, e_day)
    t = (today.month, today.day)
    if s <= e:
        return s <= t <= e
    return t >= s or t <= e


def _time_in_range(time_constraint: str, now: time) -> bool:
    start_str, end_str = time_constraint.split("-")
    s_h, s_m = (int(x) for x in start_str.split(":"))
    e_h, e_m = (int(x) for x in end_str.split(":"))
    s = (s_h, s_m)
    e = (e_h, e_m)
    t = (now.hour, now.minute)
    if s <= e:
        return s <= t <= e
    return t >= s or t <= e
