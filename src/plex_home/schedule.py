"""Wall-clock cycle scheduling.

The daemon fires on fixed times of day rather than "interval after the last
cycle", so the rotation lands at the same clock times every day no matter when
the container was started or last restarted. With ``interval_minutes: 180`` and
``timezone: America/New_York`` that is 00:00, 03:00, 06:00 … Eastern, forever.

``config`` rejects an ``interval_minutes`` that does not divide 1440, so the
boundaries always tile a day exactly and there is no ragged slot before midnight.

Both the daemon (``main.run_daemon``) and the simulator (``simulate``) walk the
clock with :func:`next_boundary`, so a simulated timeline lands on the same
timestamps the daemon would actually fire at — including across a DST change,
where a fixed ``+interval`` step would drift by an hour for the rest of the run.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

MINUTES_PER_DAY = 24 * 60


def next_boundary(now: datetime, interval_minutes: int) -> datetime:
    """Return the next wall-clock cycle time strictly after ``now``.

    ``now`` must be timezone-aware; the result carries the same timezone. The
    boundaries are the multiples of ``interval_minutes`` measured from local
    midnight, so the answer is computed from ``now``'s wall-clock fields rather
    than by adding a duration — that is what keeps the schedule pinned to the
    clock across a DST transition instead of sliding with it.
    """
    minutes_now = now.hour * 60 + now.minute
    slot = ((minutes_now // interval_minutes) + 1) * interval_minutes

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if slot >= MINUTES_PER_DAY:
        target = (midnight + timedelta(days=1)).replace(hour=0, minute=0)
    else:
        target = midnight.replace(hour=slot // 60, minute=slot % 60)

    # A spring-forward transition can make the next boundary a wall-clock time
    # that does not exist (or one that is not actually in the future once the
    # offset change is applied). Step a whole interval on rather than returning a
    # non-positive wait, which would spin the daemon.
    if target <= now:
        target = next_boundary(target, interval_minutes)
    return target


def seconds_until(target: datetime, now: datetime) -> float:
    """Absolute seconds to wait until ``target``, never negative.

    Both sides are converted to UTC first. Subtracting two aware datetimes that
    share one ``tzinfo`` object gives the *naive* wall-clock difference — and
    since ``ZoneInfo`` instances are cached, ``target`` and ``now`` always share
    one here. Without the conversion, the hour that a DST transition adds or
    removes between the two instants would be silently dropped and the daemon
    would wake an hour early or late twice a year.
    """
    delta = target.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    return max(0.0, delta.total_seconds())
