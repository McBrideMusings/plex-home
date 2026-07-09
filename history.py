from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

HISTORY_FILE = Path("pin_history.json")
_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S"

log = logging.getLogger(__name__)


def load_history() -> dict[str, datetime]:
    if not HISTORY_FILE.exists():
        return {}
    try:
        raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("expected a JSON object")
        result: dict[str, datetime] = {}
        for title, ts in raw.items():
            try:
                result[title] = datetime.strptime(ts, _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                log.warning("Skipping malformed history entry for %r: %r", title, ts)
        return result
    except Exception as e:
        log.warning("Could not load pin history (%s) — starting fresh", e)
        return {}


def save_history(history: dict[str, datetime]) -> None:
    payload = {title: dt.strftime(_TIMESTAMP_FMT) for title, dt in history.items()}
    HISTORY_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_blocked(
    title: str,
    history: dict[str, datetime],
    repeat_block_hours: float,
    now: datetime | None = None,
) -> bool:
    if repeat_block_hours == 0:
        return False
    last_pinned = history.get(title)
    if last_pinned is None:
        return False
    if now is None:
        now = datetime.now(tz=timezone.utc)
    age_hours = (now - last_pinned).total_seconds() / 3600
    return age_hours < repeat_block_hours


def record_pins(titles: list[str], history: dict[str, datetime]) -> dict[str, datetime]:
    now = datetime.now(tz=timezone.utc)
    updated = dict(history)
    for title in titles:
        updated[title] = now
    return updated
