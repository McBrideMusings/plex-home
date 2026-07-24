from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from plex_home.schedule import next_boundary, seconds_until

NY = ZoneInfo("America/New_York")


def ny(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=NY)


@pytest.mark.parametrize(
    "now, interval, expected",
    [
        # mid-slot → the next multiple of the interval from local midnight
        (ny(2026, 7, 24, 1, 10), 180, ny(2026, 7, 24, 3, 0)),
        (ny(2026, 7, 24, 14, 59), 180, ny(2026, 7, 24, 15, 0)),
        # exactly on a boundary → the following one, never a zero-length wait
        (ny(2026, 7, 24, 15, 0), 180, ny(2026, 7, 24, 18, 0)),
        # last slot of the day rolls over to tomorrow's midnight
        (ny(2026, 7, 24, 23, 30), 180, ny(2026, 7, 25, 0, 0)),
        (ny(2026, 7, 24, 23, 59), 60, ny(2026, 7, 25, 0, 0)),
        # a one-a-day interval fires at local midnight
        (ny(2026, 7, 24, 6, 0), 1440, ny(2026, 7, 25, 0, 0)),
    ],
)
def test_next_boundary(now, interval, expected):
    assert next_boundary(now, interval) == expected


def test_boundaries_are_the_same_clock_times_every_day():
    """Restart-independence: the fire times are a property of the clock, not of start time."""
    interval = 180
    seen = set()
    now = ny(2026, 7, 24, 0, 5)
    for _ in range(16):
        now = next_boundary(now, interval)
        seen.add((now.hour, now.minute))
    assert seen == {(h, 0) for h in range(0, 24, 3)}


def test_start_time_does_not_shift_the_schedule():
    """Two daemons started 40 minutes apart converge on the same boundary."""
    a = next_boundary(ny(2026, 7, 24, 9, 5), 180)
    b = next_boundary(ny(2026, 7, 24, 9, 45), 180)
    assert a == b == ny(2026, 7, 24, 12, 0)


def test_spring_forward_keeps_the_wall_clock_schedule():
    """2026-03-08: 02:00 EST jumps to 03:00 EDT. The 03:00 slot still fires at 03:00."""
    target = next_boundary(ny(2026, 3, 8, 1, 30), 180)
    assert (target.hour, target.minute) == (3, 0)
    # ...and the real wait is the shortened 30 minutes, not the naive 90.
    assert seconds_until(target, ny(2026, 3, 8, 1, 30)) == 30 * 60


def test_fall_back_keeps_the_wall_clock_schedule():
    """2026-11-01: 02:00 EDT repeats as 01:00 EST. Boundaries stay on the clock."""
    target = next_boundary(ny(2026, 11, 1, 0, 30), 180)
    assert (target.hour, target.minute) == (3, 0)
    assert seconds_until(target, ny(2026, 11, 1, 0, 30)) > 2.5 * 3600


def test_next_boundary_is_always_in_the_future():
    now = ny(2026, 3, 8, 0, 0)
    for _ in range(400):
        nxt = next_boundary(now, 60)
        assert nxt > now
        now = nxt


def test_seconds_until_never_negative():
    now = ny(2026, 7, 24, 12, 0)
    assert seconds_until(now - timedelta(hours=1), now) == 0.0
