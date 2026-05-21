from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Union
import re
import yaml

_DATE_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])/(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


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
    library: str
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


@dataclass
class Config:
    plex_url: str
    plex_token: str
    library_names: list[str]
    cadence: Cadence
    home: list[Slot]
    groups: dict[str, Group]
    webhook_url: Optional[str] = None


def load_config(path: str) -> Config:
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

    cadence = _parse_cadence(raw["cadence"])
    groups = _parse_groups(raw["groups"])
    home = _parse_home(raw["home"], groups)

    return Config(
        plex_url=plex_url,
        plex_token=plex_token,
        library_names=library_names,
        cadence=cadence,
        home=home,
        groups=groups,
        webhook_url=webhook_url,
    )


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

    rbh = raw.get("repeat_block_hours", 24.0)
    if not isinstance(rbh, (int, float)) or rbh < 0:
        raise ConfigError("'cadence.repeat_block_hours' must be a non-negative number")

    mip = raw.get("min_items_for_pinning", 10)
    if not isinstance(mip, int) or mip < 0:
        raise ConfigError("'cadence.min_items_for_pinning' must be a non-negative integer")

    return Cadence(interval_minutes=interval, repeat_block_hours=float(rbh), min_items_for_pinning=mip)


def _parse_groups(raw: object) -> dict[str, Group]:
    if not isinstance(raw, dict):
        raise ConfigError("'groups' must be a mapping")
    groups: dict[str, Group] = {}
    for name, graw in raw.items():
        if not isinstance(graw, dict):
            raise ConfigError(f"Group '{name}' must be a mapping")
        if "library" not in graw:
            raise ConfigError(f"Group '{name}' is missing required field 'library'")
        library = graw["library"]
        if not isinstance(library, str) or not library.strip():
            raise ConfigError(f"Group '{name}.library' must be a non-empty string")

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
            library=library,
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


def _parse_home(raw: object, groups: dict[str, Group]) -> list[Slot]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("'home' must be a non-empty list of slots")

    slots: list[Slot] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"home[{i}] must be a mapping")
        if "collection" in item:
            name = item["collection"]
            if not isinstance(name, str) or not name.strip():
                raise ConfigError(f"home[{i}].collection must be a non-empty string")
            slots.append(FixedSlot(collection=name))
        elif "pick" in item:
            pick_list = item["pick"]
            if not isinstance(pick_list, list) or not pick_list:
                raise ConfigError(f"home[{i}].pick must be a non-empty list of group names")
            for j, gname in enumerate(pick_list):
                if not isinstance(gname, str):
                    raise ConfigError(f"home[{i}].pick[{j}] must be a string (group name)")
                if gname not in groups:
                    raise ConfigError(
                        f"home[{i}].pick[{j}] references unknown group '{gname}' — "
                        f"defined groups are: {', '.join(sorted(groups))}"
                    )
            slots.append(PickSlot(groups=pick_list))
        else:
            raise ConfigError(f"home[{i}] must have either 'collection' or 'pick' key")

    return slots
