"""Dry-run simulator — walk the pinner forward in time and render a report.

Reuses the real resolver against a real (read-only) snapshot of the Plex
libraries, but never calls the pin/unpin/order writers. It advances a simulated
clock from one wall-clock cycle boundary to the next, threads the repeat-block
history in memory (recording pins at simulated time, mirroring what
``pinning.apply_pins`` + ``history.record_pins`` would persist), and collects
what *would* be pinned each cycle.

The output is a human-readable text report that lets you eyeball the rotation and
verify the two time-based behaviours:

  - **schedule** — cycle timestamps land on the fixed daily times the daemon
    fires at, in ``cadence.timezone``.
  - **repeat-block** — a pick-selected collection is never re-pinned before its
    effective ``repeat_block_hours`` has elapsed (fixed slots re-pin every cycle
    by design and are reported as such, not flagged).
"""
from __future__ import annotations
import copy
import random
from dataclasses import dataclass
from datetime import datetime, tzinfo

from .config import Config, expand_templates
from .plex_client import CollectionInfo
from .resolver import resolve_slots
from .schedule import next_boundary

_TS_FMT = "%Y-%m-%d %H:%M %Z"


@dataclass
class PinEvent:
    """One pin as it happened in the simulated timeline."""
    cycle: int
    when: datetime
    kind: str            # "fixed" or "pick"
    rbh: float | None    # effective repeat_block_hours for a pick; None for fixed


def _parse_start(start: str | None, tz: tzinfo) -> datetime:
    """Parse a ``--start`` value (YYYY-MM-DD or full ISO) into an aware datetime.

    A value with no offset is read as a wall-clock time in the configured
    timezone — ``--start 2026-12-24`` means local midnight on Christmas Eve, the
    same clock the group ``date``/``time`` windows are compared against.
    """
    if start is None:
        return datetime.now(tz=tz)
    try:
        dt = datetime.fromisoformat(start)
    except ValueError:
        raise ValueError(f"--start must be YYYY-MM-DD or ISO 8601, got: {start!r}")
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def run_simulation(
    config: Config,
    all_collections: dict[str, list[CollectionInfo]],
    *,
    days: float = 7.0,
    start: str | None = None,
    seed: int = 0,
) -> str:
    """Simulate ``days`` of cycles and return a text report. Performs no I/O.

    ``config`` is expected to hold **raw** (unexpanded) title specs — the caller
    loads it with ``load_config(..., expand=False)`` — so ``{YEAR}``-style
    variables can be re-expanded against the *simulated* clock each cycle rather
    than baked to wall-clock time at load. A config with no templates is
    unaffected (expansion is a no-op).
    """
    interval = config.cadence.interval_minutes
    # Start on a real cycle boundary and step boundary-to-boundary, so simulated
    # timestamps are the wall-clock times the daemon would actually fire at —
    # including across a DST change, where a flat +interval step would slide an
    # hour off the schedule for the rest of the run.
    now = next_boundary(_parse_start(start, config.cadence.timezone), interval)
    cycles = max(1, int(days * 24 * 60 / interval))

    rng = random.Random(seed)
    history: dict[tuple[str, str], datetime] = {}

    # (library, title) -> ordered list of PinEvent across the whole run.
    events: dict[tuple[str, str], list[PinEvent]] = {}
    # Per-cycle: (timestamp, {library: [titles in slot order]}, empty-pick notes).
    cycle_log: list[tuple[datetime, dict[str, list[str]], list[str]]] = []

    for cycle in range(cycles):
        trace: list[dict] = []
        # Re-expand {YEAR}/{MONTH}/{WEEK}/{DAY} at the simulated time, so a dated
        # simulation resolves titles for the year it is pretending to be. The copy
        # keeps the raw specs intact for the next cycle.
        cfg_cycle = copy.deepcopy(config)
        expand_templates(cfg_cycle.home, cfg_cycle.groups, now)
        resolved = resolve_slots(cfg_cycle, all_collections, history, now, rng, trace=trace)

        by_library: dict[str, list[str]] = {lib: [] for lib in config.home}
        empties: list[str] = []
        for entry in trace:
            lib = entry["library"]
            if entry["title"] is None:
                empties.append(f"{lib}: pick over {entry['groups']} found nothing")
                continue
            by_library[lib].append(entry["title"])
            key = (lib, entry["title"])
            events.setdefault(key, []).append(
                PinEvent(cycle=cycle, when=now, kind=entry["kind"], rbh=entry.get("rbh"))
            )
        cycle_log.append((now, by_library, empties))

        # Mirror apply_pins + record_pins: every resolved pin updates history at
        # the simulated time, so it can block a later pick.
        for pin in resolved:
            history[(pin.library, pin.title)] = now

        now = next_boundary(now, interval)

    return _render_report(config, days, seed, cycles, cycle_log, events)


def _render_report(
    config: Config,
    days: float,
    seed: int,
    cycles: int,
    cycle_log: list[tuple[datetime, dict[str, list[str]], list[str]]],
    events: dict[tuple[str, str], list[PinEvent]],
) -> str:
    cad = config.cadence
    lines: list[str] = []
    a = lines.append

    a("=" * 72)
    a("PLEX HOME — SIMULATION REPORT (dry run, no Plex writes)")
    a("=" * 72)
    a(f"Libraries          : {', '.join(config.library_names)}")
    a(f"Interval           : {cad.interval_minutes} min (on fixed daily boundaries)")
    a(f"Timezone           : {cad.timezone}")
    a(f"Repeat-block        : {cad.repeat_block_hours} h (cadence default)")
    a(f"Min items          : {cad.min_items_for_pinning}")
    a(f"Simulated span     : {days} day(s) → {cycles} cycle(s)")
    a(f"Start              : {cycle_log[0][0].strftime(_TS_FMT)}")
    a(f"End                : {cycle_log[-1][0].strftime(_TS_FMT)}")
    a(f"RNG seed           : {seed} (reproducible)")
    a("")

    a("-" * 72)
    a("TIMELINE")
    a("-" * 72)
    for cycle, (when, by_library, empties) in enumerate(cycle_log):
        a(f"[cycle {cycle:>3}] {when.strftime(_TS_FMT)}")
        for lib in config.home:
            titles = by_library.get(lib, [])
            shown = ", ".join(titles) if titles else "(none)"
            a(f"    {lib}: {shown}")
        for note in empties:
            a(f"    ! empty pick — {note}")
    a("")

    a("-" * 72)
    a("PIN FREQUENCY")
    a("-" * 72)
    for (lib, title), evs in sorted(events.items()):
        kinds = sorted({e.kind for e in evs})
        first = evs[0].when.strftime(_TS_FMT)
        last = evs[-1].when.strftime(_TS_FMT)
        a(f"  {lib} / {title}")
        a(f"      pinned {len(evs)}x  [{'/'.join(kinds)}]  first {first}  last {last}")
    a("")

    a("-" * 72)
    a("REPEAT-BLOCK VERIFICATION")
    a("-" * 72)
    a("Fixed slots re-pin every cycle by design and are not checked.")
    a("For each pick, the gap since the previous pin must be >= its repeat_block_hours.")
    a("")
    overall_ok = True
    checked_any = False
    for (lib, title), evs in sorted(events.items()):
        pick_evs = [e for e in evs if e.kind == "pick"]
        if not pick_evs:
            continue
        checked_any = True
        violations: list[str] = []
        min_gap: float | None = None
        for i in range(1, len(evs)):
            cur = evs[i]
            if cur.kind != "pick":
                continue
            gap_h = (cur.when - evs[i - 1].when).total_seconds() / 3600
            min_gap = gap_h if min_gap is None else min(min_gap, gap_h)
            if cur.rbh and gap_h < cur.rbh:
                violations.append(
                    f"cycle {cur.cycle}: only {gap_h:.1f}h since previous pin "
                    f"(< {cur.rbh}h block)"
                )
        status = "PASS" if not violations else "FAIL"
        if violations:
            overall_ok = False
        gap_str = f"{min_gap:.1f}h" if min_gap is not None else "n/a (pinned once)"
        a(f"  [{status}] {lib} / {title}  — min gap {gap_str}")
        for v in violations:
            a(f"           {v}")
    if not checked_any:
        a("  (no pick-selected collections in this run)")
    a("")
    a(f"OVERALL: {'PASS — repeat-block honored' if overall_ok else 'FAIL — see violations above'}")
    a("")

    return "\n".join(lines)
