from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re
import yaml

from . import matching
from .schedule import MINUTES_PER_DAY

#: Timezone used for cycle scheduling and for every date/time window in the
#: config when ``cadence.timezone`` is not set.
DEFAULT_TIMEZONE = "America/New_York"

_DATE_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])/(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")

_GROUP_KEYS = frozenset({
    "date", "time",
    "include_labels", "include_collections",
    "exclude_labels", "exclude_collections",
    "repeat_block_hours", "min_items_for_pinning",
})


class ConfigError(Exception):
    pass


@dataclass
class FixedSlot:
    collection: str


@dataclass
class PickSlot:
    groups: list[str]


Slot = Union[FixedSlot, PickSlot]


@dataclass
class Group:
    name: str
    date: Optional[str] = None
    time: Optional[str] = None
    include_labels: list[str] = field(default_factory=list)
    include_collections: list[str] = field(default_factory=list)
    exclude_labels: list[str] = field(default_factory=list)
    exclude_collections: list[str] = field(default_factory=list)
    repeat_block_hours: Optional[float] = None
    min_items_for_pinning: Optional[int] = None


@dataclass
class Cadence:
    interval_minutes: int
    repeat_block_hours: float = 24.0
    min_items_for_pinning: int = 10
    mirror_recommended: bool = False
    #: The clock everything time-based runs on: which wall-clock times the cycles
    #: fire at, and which day and time-of-day a group's ``date``/``time`` window is
    #: compared against. Stored timestamps (``pin_history.json``) stay UTC.
    timezone: tzinfo = ZoneInfo(DEFAULT_TIMEZONE)


@dataclass
class Config:
    plex_url: str
    plex_token: str
    library_names: list[str]
    cadence: Cadence
    home: dict[str, list[Slot]]
    groups: dict[str, Group]
    webhook_url: Optional[str] = None
    #: Where the repeat-block history is read and written. Set by
    #: ``load_config`` to ``pin_history.json`` beside the config file, so the
    #: two files that describe one deployment travel together. Deriving it from
    #: the config path rather than the process working directory is what lets a
    #: container mount one directory and keep its history across recreates.
    history_path: Path = Path("pin_history.json")
    #: Lock file guarding a whole cycle, so a forced ``once`` refresh and the
    #: daemon can't interleave their read-modify-write of the history. Beside the
    #: config for the same reason ``history_path`` is: one mounted directory
    #: holds everything one deployment owns.
    lock_path: Path = Path(".plex-home.lock")
    #: plex-db-ex's published snapshot, opened read-only by ``tagsource``. A
    #: relative path resolves against the config file's directory. Never opened
    #: at load, so a missing snapshot can't stop a home-screen cycle.
    plexdb_snapshot: Optional[Path] = None


def load_config(path: str, expand: bool = True) -> Config:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in config: {e}")

    if not isinstance(raw, dict):
        raise ConfigError("Config must be a YAML mapping at the top level")

    _require_fields(raw, ["plex_url", "plex_token", "library_names", "cadence", "home", "groups"])

    plex_url = _require_str(raw, "plex_url")
    plex_token = _require_str(raw, "plex_token")
    library_names = _require_str_list(raw, "library_names")
    webhook_url = raw.get("webhook_url") or None
    config_dir = Path(path).resolve().parent
    plexdb_snapshot = None
    if raw.get("plexdb_snapshot") is not None:
        plexdb_snapshot = config_dir / _require_str(raw, "plexdb_snapshot")

    cadence = _parse_cadence(raw["cadence"])
    groups = _parse_groups(raw["groups"])
    home = _parse_home(raw["home"], groups, library_names)

    if expand:
        # Expand against the configured clock, not UTC, so a {YEAR}/{DAY} title
        # rolls over at local midnight — the same instant the group date windows do.
        expand_templates(home, groups, datetime.now(cadence.timezone))

    return Config(
        plex_url=plex_url,
        plex_token=plex_token,
        library_names=library_names,
        cadence=cadence,
        home=home,
        groups=groups,
        webhook_url=webhook_url,
        history_path=config_dir / "pin_history.json",
        lock_path=config_dir / ".plex-home.lock",
        plexdb_snapshot=plexdb_snapshot,
    )


def expand_templates(
    home: dict[str, list[Slot]], groups: dict[str, Group], now: datetime
) -> None:
    """Expand ``{YEAR}``-style variables in every collection-title spec in place.

    ``load_config`` runs this once at load (against wall-clock ``now``); since the
    daemon reloads config each cycle, that re-resolves the current
    year/month/week/day for free. ``simulate`` instead loads config raw
    (``expand=False``) and calls this per cycle against its simulated clock, so a
    dated simulation renders the year it is pretending to be. Also validates that
    any ``re:`` spec compiles, so a bad regex fails fast with a ``ConfigError``.
    Only the three title-bearing fields are templated — fixed-slot collections and
    group include/exclude collection lists — never labels.
    """
    def _expand(spec: str, loc: str) -> str:
        expanded = matching.expand_variables(spec, now)
        mode, pattern = matching.parse_spec(expanded)
        if mode == "regex":
            try:
                re.compile(pattern)
            except re.error as e:
                raise ConfigError(f"{loc} has an invalid regex {pattern!r}: {e}")
        return expanded

    for library, slots in home.items():
        for i, slot in enumerate(slots):
            if isinstance(slot, FixedSlot):
                slot.collection = _expand(slot.collection, f"home['{library}'][{i}].collection")

    for name, group in groups.items():
        group.include_collections = [
            _expand(s, f"group '{name}'.include_collections") for s in group.include_collections
        ]
        group.exclude_collections = [
            _expand(s, f"group '{name}'.exclude_collections") for s in group.exclude_collections
        ]


def _require_fields(d: dict, fields: list[str]) -> None:
    for f in fields:
        if f not in d:
            raise ConfigError(f"Missing required field: '{f}'")


def _require_str(d: dict, key: str) -> str:
    val = d.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ConfigError(f"'{key}' must be a non-empty string")
    return val


def _require_str_list(d: dict, key: str) -> list[str]:
    val = d.get(key)
    if not isinstance(val, list) or not val:
        raise ConfigError(f"'{key}' must be a non-empty list")
    for i, item in enumerate(val):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"'{key}[{i}]' must be a non-empty string")
    return val


def _parse_cadence(raw: object) -> Cadence:
    if not isinstance(raw, dict):
        raise ConfigError("'cadence' must be a mapping")
    if "interval_minutes" not in raw:
        raise ConfigError("'cadence.interval_minutes' is required")
    interval = raw["interval_minutes"]
    if not isinstance(interval, int) or interval <= 0:
        raise ConfigError("'cadence.interval_minutes' must be a positive integer")
    # Cycles fire at fixed times of day, so the interval has to tile a day exactly
    # — otherwise the last slot before midnight would be a short one.
    if MINUTES_PER_DAY % interval != 0:
        raise ConfigError(
            f"'cadence.interval_minutes' must divide {MINUTES_PER_DAY} evenly so cycles "
            f"land on fixed daily times, got: {interval}"
        )

    tz = _parse_timezone(raw.get("timezone", DEFAULT_TIMEZONE))

    rbh = raw.get("repeat_block_hours", 24.0)
    if not isinstance(rbh, (int, float)) or rbh < 0:
        raise ConfigError("'cadence.repeat_block_hours' must be a non-negative number")

    mip = raw.get("min_items_for_pinning", 10)
    if not isinstance(mip, int) or mip < 0:
        raise ConfigError("'cadence.min_items_for_pinning' must be a non-negative integer")

    mirror = raw.get("mirror_recommended", False)
    if not isinstance(mirror, bool):
        raise ConfigError("'cadence.mirror_recommended' must be a boolean")

    return Cadence(
        interval_minutes=interval,
        repeat_block_hours=float(rbh),
        min_items_for_pinning=mip,
        mirror_recommended=mirror,
        timezone=tz,
    )


def _parse_timezone(raw: object) -> tzinfo:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("'cadence.timezone' must be an IANA timezone name, e.g. America/New_York")
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(
            f"'cadence.timezone' is not a known IANA timezone name: {raw!r} "
            f"(use e.g. America/New_York, not EST)"
        )


def _parse_groups(raw: object) -> dict[str, Group]:
    if not isinstance(raw, dict):
        raise ConfigError("'groups' must be a mapping")
    groups: dict[str, Group] = {}
    for name, graw in raw.items():
        if not isinstance(graw, dict):
            raise ConfigError(f"Group '{name}' must be a mapping")

        unknown = set(graw) - _GROUP_KEYS
        if unknown:
            raise ConfigError(
                f"Group '{name}' has unknown key(s): {', '.join(sorted(unknown))} — "
                f"allowed keys are: {', '.join(sorted(_GROUP_KEYS))}"
            )

        date = graw.get("date")
        if date is not None:
            if not isinstance(date, str) or not _DATE_RE.match(date):
                raise ConfigError(
                    f"Group '{name}.date' must be in MM-DD/MM-DD format, got: {date!r}"
                )

        time_ = graw.get("time")
        if time_ is not None:
            if not isinstance(time_, str) or not _TIME_RE.match(time_):
                raise ConfigError(
                    f"Group '{name}.time' must be in HH:MM-HH:MM format, got: {time_!r}"
                )

        rbh = graw.get("repeat_block_hours")
        if rbh is not None:
            if not isinstance(rbh, (int, float)) or rbh < 0:
                raise ConfigError(f"Group '{name}.repeat_block_hours' must be a non-negative number")

        mip = graw.get("min_items_for_pinning")
        if mip is not None:
            if not isinstance(mip, int) or mip < 0:
                raise ConfigError(f"Group '{name}.min_items_for_pinning' must be a non-negative integer")

        groups[name] = Group(
            name=name,
            date=date,
            time=time_,
            include_labels=_str_list_field(graw, name, "include_labels"),
            include_collections=_str_list_field(graw, name, "include_collections"),
            exclude_labels=_str_list_field(graw, name, "exclude_labels"),
            exclude_collections=_str_list_field(graw, name, "exclude_collections"),
            repeat_block_hours=float(rbh) if rbh is not None else None,
            min_items_for_pinning=int(mip) if mip is not None else None,
        )
    return groups


def _str_list_field(graw: dict, group_name: str, key: str) -> list[str]:
    val = graw.get(key, [])
    if not isinstance(val, list):
        raise ConfigError(f"Group '{group_name}.{key}' must be a list")
    for i, item in enumerate(val):
        if not isinstance(item, str):
            raise ConfigError(f"Group '{group_name}.{key}[{i}]' must be a string")
    return val


def _parse_home(
    raw: object, groups: dict[str, Group], library_names: list[str]
) -> dict[str, list[Slot]]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(
            "'home' must be a non-empty mapping of library name → list of slots"
        )

    home: dict[str, list[Slot]] = {}
    for library, raw_slots in raw.items():
        if library not in library_names:
            raise ConfigError(
                f"home library '{library}' is not in library_names "
                f"({', '.join(library_names)})"
            )
        if not isinstance(raw_slots, list) or not raw_slots:
            raise ConfigError(f"home['{library}'] must be a non-empty list of slots")
        home[library] = _parse_slots(raw_slots, groups, library)

    return home


def _parse_slots(raw: list, groups: dict[str, Group], library: str) -> list[Slot]:
    slots: list[Slot] = []
    for i, item in enumerate(raw):
        loc = f"home['{library}'][{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{loc} must be a mapping")
        if "collection" in item:
            name = item["collection"]
            if not isinstance(name, str) or not name.strip():
                raise ConfigError(f"{loc}.collection must be a non-empty string")
            slots.append(FixedSlot(collection=name))
        elif "pick" in item:
            pick_list = item["pick"]
            if not isinstance(pick_list, list) or not pick_list:
                raise ConfigError(f"{loc}.pick must be a non-empty list of group names")
            for j, gname in enumerate(pick_list):
                if not isinstance(gname, str):
                    raise ConfigError(f"{loc}.pick[{j}] must be a string (group name)")
                if gname not in groups:
                    raise ConfigError(
                        f"{loc}.pick[{j}] references unknown group '{gname}' — "
                        f"defined groups are: {', '.join(sorted(groups))}"
                    )
            slots.append(PickSlot(groups=pick_list))
        else:
            raise ConfigError(f"{loc} must have either 'collection' or 'pick' key")

    return slots
