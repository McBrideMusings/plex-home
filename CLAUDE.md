# Plex Home — Plex Collection Pinner

Plex Home is a Python daemon that automatically rotates which collections are "pinned" (promoted to the home screen) on a Plex Media Server. On each cycle it resolves an ordered list of home-screen *slots* — each slot is either a fixed collection or a *pick* that selects the first eligible collection from a list of named groups — then fully manages the home screen: it pins the resolved set, unpins every other promoted collection, and reorders the pinned hubs to match slot order. It logs to stdout and optionally POSTs a per-cycle summary to a webhook.

The code is a `plex_home` package under `src/`, with the test suite in `tests/`.

## Running

The package exposes one console command, `plex-home`, with subcommands: `run` (the daemon), a live-control CLI (`list`, `pin`, `unpin`, `move`), and `simulate` (a dry run). A global `--config` flag (default `config.yaml`) supplies the Plex connection for every subcommand. Without installing, the equivalent is `python -m plex_home` (with `src/` on `PYTHONPATH`).

### Daemon
```
pip install -e .
plex-home run [--config config.yaml]
```
`run` reloads the config at the start of every cycle (so edits take effect without a restart), reconciles the home screen to the config, then sleeps `interval_minutes`. It runs forever; interrupt with Ctrl-C — SIGINT triggers a clean shutdown after the current sleep. Logging goes to stdout only (there is no log file).

### CLI — live home-screen control (ADR-0005)
`list` / `pin` / `unpin` / `move` operate on the live Plex home screen **imperatively**, independent of the config. The home screen is treated as a per-library, indexed list of pinned hubs (system and collection hubs alike). A running daemon reconciles the home back to the config, so CLI changes are ephemeral against it.
```
plex-home list  [--library NAME] [--available] [--json]
plex-home pin   "Title" [--library NAME] [--to K | --to-top] [--dry-run]
plex-home unpin <index|Title> --library NAME [--dry-run]
plex-home move  <index|Title> --library NAME (--to K | --up [N] | --down [N] | --to-top | --to-bottom) [--dry-run]
```
`--dry-run` prints the intended operation without calling Plex. Index targets are per-library, so `--library` is required for index-based `unpin`/`move`; title targets auto-resolve across configured libraries (error on collision). Plex drops a fresh `pin` at a Plex-determined position (observed mid-list), so use `--to K` for explicit placement.

### `simulate` — dry-run the rotation forward in time
`simulate` reads the live Plex collections (read-only) once, then walks the resolver forward over simulated cycles — advancing a clock by `cadence.interval_minutes`, threading the repeat-block history in memory, recording pins at simulated time — and writes a text report. It never calls the pin/unpin/order writers, so it cannot change the real home screen. Use it to preview the rotation and verify the interval/repeat-block behaviour before running the daemon.
```
plex-home simulate [--days N] [--start YYYY-MM-DD] [--seed N] [--out report.txt]
```
`--days` (default 7) sets the span (cycle count = `days*1440 / interval_minutes`). `--start` (default now, UTC) seeds the clock — set it inside a group's `date` window to exercise seasonal groups. `--seed` (default 0) fixes the RNG so pick slots are reproducible. The report has a TIMELINE (per-cycle pins in slot order, plus empty-pick notes), a PIN FREQUENCY table, and a REPEAT-BLOCK VERIFICATION section that PASS/FAILs each pick collection against its effective `repeat_block_hours` (fixed slots re-pin every cycle by design and are not checked).

### Docker
The container installs the package and runs the daemon (`CMD ["plex-home", "run"]`). Mount the YAML config; read logs via `docker logs` (stdout, no file):
```
docker build -t plex-home .
docker run -d \
  -v $(pwd)/config.yaml:/app/config.yaml \
  plex-home
```

## Configuration (`config.yaml`)

All runtime behaviour is controlled by a YAML config (default path `config.yaml`, overridable with the global `--config` flag). It is reloaded at the start of every cycle, so changes take effect without a restart. Parsed and validated by `config.py`, which raises `ConfigError` with a human-readable message on any problem.

### Top-level keys

| Key | Type | Purpose |
|-----|------|---------|
| `plex_url` | string (required) | Base URL of the Plex server (e.g. `http://192.168.1.x:32400`) |
| `plex_token` | string (required) | Plex authentication token |
| `library_names` | list of strings (required) | Libraries to manage (e.g. `[Movies, TV Shows]`) |
| `cadence` | mapping (required) | Timing and global defaults — see below |
| `groups` | mapping (required) | Named collection groups that `pick` slots draw from — see below |
| `home` | mapping (required) | Library name → ordered home-screen slots — see below |
| `webhook_url` | string (optional) | POSTed a per-cycle summary of pinned titles; omit to disable |

### `cadence`

| Key | Default | Purpose |
|-----|---------|---------|
| `interval_minutes` | — (required, > 0) | Minutes between cycles |
| `repeat_block_hours` | `24` | Hours a pinned collection is blocked from being re-pinned |
| `min_items_for_pinning` | `10` | Collections with fewer items are skipped |
| `mirror_recommended` | `false` | When `true`, resolved pins are also force-promoted to Library Recommended so the Recommended tab mirrors Home; default leaves each pin's Recommended flag as-is |

### `groups`

A mapping of group name → group definition. Each group scopes a set of collections and, optionally, when the group is eligible. A `pick` slot resolves to the first eligible group in its list. A group carries **no** `library` field — the library it filters against is the `home` section that references it, so one group may be reused under more than one library (ADR-0006).

| Key | Type | Purpose |
|-----|------|---------|
| `date` | `MM-DD/MM-DD` (optional) | Date window the group is eligible; cross-year ranges (e.g. `12-26/01-03`) supported |
| `time` | `HH:MM-HH:MM` (optional) | Time-of-day window the group is eligible |
| `include_labels` | list (optional) | Restrict to collections carrying any of these Plex labels |
| `include_collections` | list (optional) | Restrict to these collection titles |
| `exclude_labels` | list (optional) | Drop collections carrying any of these labels |
| `exclude_collections` | list (optional) | Drop these collection titles |
| `repeat_block_hours` | inherits `cadence` | Per-group override of the recency block |
| `min_items_for_pinning` | inherits `cadence` | Per-group override of the min-items threshold |

**How the four filter keys combine** (`eligibility.py:matches_group_membership`). The two `*_labels` keys are exact, case-sensitive strings. The two `*_collections` keys are **title specs** (see below): each entry is exact by default, or a `glob:`/`re:` pattern, and may embed `{YEAR}`/`{MONTH}`/`{WEEK}`/`{DAY}` variables.

1. The two `include_*` keys are **OR'd**, not AND'd: a collection is in scope if it carries **any** listed label **or** its title is in `include_collections`. Listing both widens the candidate set.
2. Omitting both includes means **the whole library** the referencing `home` section names.
3. `exclude_*` applies after, and **always wins** — an excluded label or title is dropped even when an include named it explicitly.
4. Min-items is applied alongside, from the group override or `cadence`.

Filters are a **pick-slot concept only**. A fixed slot pins its title with no label, exclude, or min-items check, so excluding a title in a group does not stop a fixed slot elsewhere from pinning it. Labels are read off the collection object (`collection.labels`), not from the genres or labels of the items inside it.

### Title specs — patterns and variables (`matching.py`)

Any place a config value names a **collection title** — a fixed slot's `collection`, a group's `include_collections`/`exclude_collections` — accepts a *title spec* rather than a bare literal. `matching.py` owns this. A spec has two independent, composable parts:

- **Match mode**, chosen by a leading sigil: bare = **exact** (string equality — the historical behaviour), `glob:` = shell-style wildcard (`glob:Marvel *`, matched with `fnmatchcase`), `re:` = regular expression (`re:Oscars Death Race \d{4}`). All three match the **whole** title; glob is case-sensitive; a bad `re:` pattern fails fast with `ConfigError` at config load.
- **Variables** `{YEAR}` (`%Y`), `{MONTH}` (`%m`), `{WEEK}` (ISO week `%V`), `{DAY}` (`%d`), expanded against the current UTC time **once at config load** (`config._expand_templates`). Because the daemon reloads config every cycle, `Oscars Death Race {YEAR}` re-resolves to the current year each cycle with no extra machinery. Variables and sigils compose (`re:Oscars {YEAR}`); expansion runs before the regex compiles, so `{YEAR}` is never mistaken for a regex quantifier.

**Fixed slots vs pick-slot filters differ on multi-match**, because a fixed slot pins exactly one hub while a group filter is a set test. An exact fixed slot pins its title **blindly** (no existence check — unchanged). A **pattern** fixed slot matches against the library's live collection titles and takes the **first by title sort** (deterministic; e.g. glob picks the *oldest* year), or pins nothing that cycle if none match — never an error. In group filters a pattern simply matches every title it fits. `simulate` re-expands variables against its **simulated** clock each cycle (it loads config raw via `load_config(..., expand=False)` and calls `config.expand_templates` per cycle), so a dated simulation renders the year it is pretending to be and titles track across a year boundary within one run.

### `home` slots

A mapping of **library name → ordered list of slots**. Every key must be one of `library_names`, or the config fails to load. Within a library, the list order is the home-screen order its pinned hubs are moved into. Each slot is exactly one of:

- **Fixed slot** — `{collection: "<title>"}` — always pins the managed hub with that title. The title is a **title spec** (exact, `glob:`, or `re:`, with `{YEAR}`-style variables — see "Title specs" above); an exact spec pins blindly, a pattern spec takes the first live-collection title by sort. The title matches **any** managed hub — a user collection *or* a built-in Plex system hub (e.g. `collection: Recently Added Movies`). On a title collision the **collection wins** — a system hub is matched only when no collection in the library carries the title (ADR-0007). The `collection:` key name is historical; it accepts system hubs too.
- **Pick slot** — `{pick: [<group>, ...]}` — walks the groups in order and stops at the first one that has an eligible, non-repeat-blocked, not-already-used collection, then pins one of that group's collections (filtered against *this* section's library) chosen at random. Every group name referenced must be defined under `groups`, or the config fails to load. Pick slots are **collections-only** — their label / min-items filters don't apply to system hubs.

Hubs are de-duplicated across all slots and libraries, so the same hub never occupies two slots.

**Order is per-library, not global.** Plex groups promoted collections by library and exposes no cross-library home order (that's the account's pinned-source order, set manually in Plex). The tool orders *within* each library block only — you cannot place a TV collection above a Movies one. The resolver emits one flat list grouped by library (mapping order), which the per-section ordering layer honors within each block (ADR-0006).

### Example

```yaml
plex_url: http://192.168.1.x:32400
plex_token: xxxxxxxxxxxx
library_names: [Movies, TV Shows]

cadence:
  interval_minutes: 180
  repeat_block_hours: 12
  min_items_for_pinning: 10

webhook_url: https://discord.com/api/webhooks/...   # optional

groups:
  halloween:
    date: 10-01/10-31
    include_labels: [Horror]
  staff-picks:
    include_collections: [A24, Studio Ghibli]
  prestige-drama:
    include_labels: [Prestige]

home:
  Movies:
    - collection: Trending Movies       # fixed slot — always this collection
    - pick: [halloween, staff-picks]    # first eligible group wins
  TV Shows:
    - collection: Trending TV
    - pick: [prestige-drama]
```

## Key files

All modules live in `src/plex_home/`; all tests in `tests/`.

| File | Purpose |
|------|---------|
| `src/plex_home/config.py` | Config loader — parses and validates the YAML config into typed dataclasses |
| `src/plex_home/eligibility.py` | Group eligibility engine — evaluates date/time constraints and applies include/exclude/min-items filters (collection filters use `matching.title_matches`) |
| `src/plex_home/matching.py` | Title-spec matching — parses the `glob:`/`re:` sigil, expands `{YEAR}`/`{MONTH}`/`{WEEK}`/`{DAY}` variables, and whole-title matches (exact / `fnmatchcase` / `re.fullmatch`) |
| `src/plex_home/plex_client.py` | Plex data-access layer — connects to PlexServer, fetches collections per library as CollectionInfo objects |
| `src/plex_home/history.py` | Repeat-block history — tracks pinned collections with timestamps, answers "is this blocked?" |
| `src/plex_home/resolver.py` | Slot resolver — walks configured home slots, resolves fixed + pick slots (sequential group priority), dedups across slots |
| `src/plex_home/managed_hubs.py` | Shared kind-agnostic managed-hub primitives (ADR-0007) used by `pinning`/`ordering`/`hubs`: `is_collection`, `on_home`, `promoted_anywhere`, `title_map` (collection wins on title collision), `realize_order` (reverse move-to-top), `iter_library_hubs` (per-library fetch-and-skip for the writers) |
| `src/plex_home/pinning.py` | Pin engine — fully manages all three visibility axes (ADR-0007): promotes resolved pins (system + collection), `remove()`s non-resolved collections from the managed list, demotes non-resolved system hubs, updates repeat-block history |
| `src/plex_home/ordering.py` | Hub ordering — reorders each library's pinned hubs to match slot order via `managed_hubs.realize_order` (move-to-top, works for system + Plex-curated hubs) |
| `src/plex_home/webhook.py` | Optional webhook notifier — POSTs a per-cycle summary (pinned titles + timestamp) when `webhook_url` is configured; never raises |
| `src/plex_home/simulate.py` | Dry-run simulator (`simulate` subcommand) — advances a simulated clock over N days, re-runs the resolver each cycle against a read-only Plex snapshot, threads history in memory, and renders a text report (timeline + pin frequency + repeat-block PASS/FAIL). Never calls the writers |
| `src/plex_home/main.py` | Entrypoint logic — builds the subcommand parser and dispatches: `run` → the daemon loop (reload config each cycle, fetch → resolve → pin → order → webhook, sleep, clean SIGINT shutdown, per-cycle error retry); `list`/`pin`/`unpin`/`move` → the CLI handlers. Exposed as the `plex-home` console script and `python -m plex_home` |
| `src/plex_home/hubs.py` | Imperative managed-hub layer for the CLI (ADR-0005) — lists pinned hubs per library as an indexed order, and pins/unpins/moves them via `ManagedHub` (system + collection uniform); never touches the config |
| `src/plex_home/cli.py` | CLI subcommand parsing, library resolution, relative-move math, and table/JSON output for `list`/`pin`/`unpin`/`move` |
| `src/plex_home/__main__.py` | `python -m plex_home` shim — calls `main.main()` |
| `src/plex_home/__init__.py` | Package marker |
| `tests/test_config.py` | Pytest suite for config loader (run with `.venv/bin/pytest`) |
| `tests/test_history.py` | Pytest suite for repeat-block history |
| `tests/test_eligibility.py` | Pytest suite for group eligibility engine |
| `tests/test_matching.py` | Pytest suite for title-spec matching (sigils, variables, exact/glob/regex) |
| `tests/test_plex_client.py` | Pytest suite for Plex client (fully mocked) |
| `tests/test_resolver.py` | Pytest suite for slot resolver |
| `tests/test_managed_hubs.py` | Pytest suite for the shared managed-hub primitives (fully mocked) |
| `tests/test_pinning.py` | Pytest suite for pin engine — promote/remove/demote reconcile (fully mocked) |
| `tests/test_ordering.py` | Pytest suite for hub ordering (fully mocked) |
| `tests/test_webhook.py` | Pytest suite for the webhook notifier (fully mocked) |
| `tests/test_main.py` | Pytest suite for the main loop + run_cycle wiring + subcommand dispatch (fully mocked) |
| `tests/test_hubs.py` | Pytest suite for the managed-hub operations layer (fully mocked) |
| `tests/test_cli.py` | Pytest suite for CLI parsing, handlers, and output (fully mocked) |
| `tests/test_simulate.py` | Pytest suite for the dry-run simulator (determinism, cycle count, repeat-block verification, seasonal date windows) |
| `pyproject.toml` | Package metadata, runtime deps, `plex-home` console script, and pytest config (`pythonpath = src`) |
| `config.yaml` | Runtime YAML config (path overridable via the global `--config` flag) — user-provided, not committed with real credentials |
| `requirements.txt` | Runtime deps for `pip install -r` (`plexapi`, `requests`, `pyyaml`); mirrors the `dependencies` in `pyproject.toml` |
| `Dockerfile` | Container build (`python:3.12-slim-bullseye`) — `pip install .` then `CMD ["plex-home", "run"]` |
| `pin_history.json` | Auto-generated at runtime by `history.py`, **beside the config file** (`Config.history_path`, set by `load_config`); nested `{library: {title: last-pinned UTC timestamp}}` for recency blocking, keyed per-library so same-titled collections in different libraries block independently. Delete to reset |

## Gotchas

- **Logging is stdout-only** — `main.py` calls `logging.basicConfig` with no file handler, so there is no log file to rotate or truncate. Under Docker, read logs via `docker logs`.
- **No `timeout` on macOS** — macOS ships no `timeout` binary and GNU coreutils' `gtimeout` isn't installed by default, so `timeout 20 docker run …` dies with `command not found`. To time-bound a container run, bound it container-side: run detached and `docker stop` it (or install coreutils for `gtimeout`), rather than wrapping the run in a host-side `timeout`.
- **`pin_history.json` is mutable state, and it lives beside the config** — `history.py` rewrites it each cycle (nested `{library: {title: last-pinned UTC timestamp}}`, keyed per-library). Its path comes from `Config.history_path`, which `load_config` sets to the config file's own directory — *not* the process working directory, so where the daemon is launched from can't decide whether the history survives. `load_history`/`save_history` take the path as an argument; there is no module-level default. Delete the file to reset; a missing, corrupt, or legacy flat-format file is handled gracefully (starts fresh).
- **Tests need `src/` on the path** — `pyproject.toml` sets `pythonpath = ["src"]`, so `.venv/bin/pytest` imports `plex_home` without an install. Running pytest a different way (or importing the modules directly) requires `pip install -e .` or `PYTHONPATH=src` first.
- **Config errors don't crash the daemon** — `config.py` raises `ConfigError` on any invalid or missing field; `main.py` catches it, logs the message, and retries in 5 minutes (`CONFIG_ERROR_RETRY_MINUTES`) instead of exiting.
- **Same-titled collections in one library collide** — everything keys by `(library, title)`, so two collections with an identical title in the same library are indistinguishable: `managed_hubs.title_map` keeps one and the sweep un-manages the other (ADR-0007). Give collections distinct titles within a library. A collection and a *system* hub sharing a title is fine — the collection wins.
- **The config owns all three visibility axes** — ADR-0007 extends ADR-0002 from home-only to Home + Friends' Home + Library Recommended, across system *and* collection hubs. Each cycle any hub the config doesn't pin is swept: a **collection** is `remove()`d from the Managed Recommendations list entirely (the `×` in Plex — `DELETE …/manage/{id}`; it deletes the *recommendation record*, never the collection or its items), and a **system hub** (not removable) is demoted on all three axes. This is what keeps the Recommended list from accreting every collection ever pinned. A collection you pin to Friends'-Home-only or Recommended-only in the Plex UI is removed on the next cycle unless it's in the config. Global `/hubs/home` rows (Continue Watching, On Deck) are not section-managed and are never touched.
