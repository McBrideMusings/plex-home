from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

HISTORY_FILE = Path("pin_history.json")
_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S"

log = logging.getLogger(__name__)


def load_history() -> dict[tuple[str, str], datetime]:
    """Load the repeat-block history keyed by ``(library, title)``.

    On-disk format is nested by library: ``{library: {title: timestamp}}``.
    A legacy flat ``{title: timestamp}`` file (values are strings, not maps)
    fails the per-library shape check and is skipped — the history is disposable
    runtime state ("delete to reset"), so an old file just starts fresh.
    """
    if not HISTORY_FILE.exists():
        return {}
    try:
        raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("expected a JSON object")
        result: dict[tuple[str, str], datetime] = {}
        for library, titles in raw.items():
            if not isinstance(titles, dict):
                log.warning("Skipping malformed history entry for library %r: %r", library, titles)
                continue
            for title, ts in titles.items():
                try:
                    result[(library, title)] = datetime.strptime(ts, _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    log.warning("Skipping malformed history entry for %r/%r: %r", library, title, ts)
        return result
    except Exception as e:
        log.warning("Could not load pin history (%s) — starting fresh", e)
        return {}


def save_history(history: dict[tuple[str, str], datetime]) -> None:
    nested: dict[str, dict[str, str]] = {}
    for (library, title), dt in history.items():
        nested.setdefault(library, {})[title] = dt.strftime(_TIMESTAMP_FMT)
    HISTORY_FILE.write_text(json.dumps(nested, indent=2), encoding="utf-8")


def is_blocked(
    library: str,
    title: str,
    history: dict[tuple[str, str], datetime],
    repeat_block_hours: float,
    now: datetime | None = None,
) -> bool:
    if repeat_block_hours == 0:
        return False
    last_pinned = history.get((library, title))
    if last_pinned is None:
        return False
    if now is None:
        now = datetime.now(tz=timezone.utc)
    age_hours = (now - last_pinned).total_seconds() / 3600
    return age_hours < repeat_block_hours


def record_pins(
    keys: list[tuple[str, str]],
    history: dict[tuple[str, str], datetime],
) -> dict[tuple[str, str], datetime]:
    now = datetime.now(tz=timezone.utc)
    updated = dict(history)
    for key in keys:
        updated[key] = now
    return updated
