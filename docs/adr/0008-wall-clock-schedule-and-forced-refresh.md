# Wall-clock schedule, a configured timezone, and a phase-preserving forced refresh

The daemon fires cycles at **fixed times of day** rather than "`interval_minutes` after the last cycle finished", and every time-based decision in the tool reads a **timezone named in the config** rather than UTC. A `once` subcommand forces a reconcile without disturbing that schedule.

## The clock belongs to the config

Before this, `run_cycle` built `now` as `datetime.now(timezone.utc)` and `eligibility` compared a group's `date`/`time` window against it. That made `time: 21:00-03:00` mean 9pm **UTC** — an unannounced afternoon window for a US operator, and one that silently moves an hour twice a year relative to anyone's evening. Since the point of a `time` window is "show these collections when people are actually watching at that hour", a hardcoded UTC clock cannot express the feature at all.

`cadence.timezone` (an IANA name, default `America/New_York`) is now the single clock for: which wall-clock times cycles fire at, which day and time-of-day a group's `date`/`time` window is compared against, and what `{YEAR}`/`{DAY}` expand to. Stored timestamps in `pin_history.json` stay UTC, so the repeat block keeps measuring real elapsed hours and is unaffected by a DST change.

The alternative was to set `TZ` in the container and read system local time. Rejected: the zone would then be a property of *where the process happens to run*, so `simulate` on a developer's laptop would silently answer a different question than the deployed daemon, and a test's result would depend on ambient environment. The config already travels with the deployment; the clock belongs with it. `TZ` is still set in the image, but only so `docker logs` timestamps read locally — nothing reads it for behaviour. The `tzdata` dependency is what makes zone names resolvable at all in a slim image, which ships no system zone database.

## Boundaries, not intervals

The old loop slept `interval_minutes` after each cycle, so the schedule's phase was an accident of when the container last started, and it drifted forward by each cycle's own duration. Users saw the home screen change at a different time every day, and any restart moved it again.

`schedule.next_boundary` instead returns the next multiple of `interval_minutes` measured from local midnight. Two consequences are deliberate:

- **`interval_minutes` must divide 1440.** The boundaries have to tile a day exactly, or the last slot before midnight is a short one. `config.py` rejects a non-divisor with a `ConfigError` at load rather than quietly producing a ragged day.
- **DST shifts the gap between two cycles, never the schedule.** The next boundary is computed from `now`'s wall-clock *fields*, so 03:00 stays 03:00 across a transition and the real gap becomes 2h or 4h for that one day. This is the reason `seconds_until` converts both sides to UTC before subtracting: two aware datetimes that share a `tzinfo` object subtract *naively* in Python, and `ZoneInfo` instances are cached, so the naive form would have silently dropped the transition hour and woken the daemon an hour off for the rest of the run.

`simulate` walks the same function rather than adding a fixed `timedelta`, so a simulated timeline shows the timestamps the daemon would really fire at, including across a transition.

## Forcing a refresh without paying for it

Deploying an edited config previously meant waiting for the next cycle or restarting the container — and a restart is exactly what resets the schedule's phase, so the cheap fix costs the property this ADR just established.

`plex-home once` runs a single `run_cycle` and exits. It is deliberately a **separate short-lived process** rather than a signal that wakes the daemon early: the two share state only through `pin_history.json`, which `run_cycle` already loads and saves per cycle, so a forced refresh is visible to the daemon's next cycle while the daemon's sleep — and therefore the fixed daily schedule — is untouched. Waking the daemon early via SIGHUP or a trigger file was the alternative; both would have re-based the next cycle on the moment of the refresh, reintroducing the drift.

Two processes sharing the history file means a cycle has to be atomic against another cycle: `load_history` at the top and `save_history` at the bottom are a read-modify-write, so overlapping runs would each start from the same history and the second writer would erase the first's updates, letting a collection re-pin inside its repeat block. `run_cycle` therefore holds an exclusive `flock` on `.plex-home.lock` (beside the config, like the history) for its whole duration. A `once` that arrives mid-cycle **waits** rather than being rejected — a refresh the operator asked for should happen, just not concurrently.

This keeps the ADR-0005 split intact: `once` is not a new imperative surface, it is the declarative daemon's own cycle run one extra time.
