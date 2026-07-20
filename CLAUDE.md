# ColleXions — Plex Collection Pinner

ColleXions is a Python daemon that automatically rotates which collections are "pinned" (promoted to the home screen) on a Plex Media Server. On each cycle it resolves an ordered list of home-screen *slots* — each slot is either a fixed collection or a *pick* that selects the first eligible collection from a list of named groups — then fully manages the home screen: it pins the resolved set, unpins every other promoted collection, and reorders the pinned hubs to match slot order. It logs to stdout, optionally POSTs a per-cycle summary to a webhook, and tags every pinned collection with a fixed Plex label (`Pinned by ColleXions`) so they can be identified or filtered in the Plex UI.

## Running

### Bare Python
```
pip install -r requirements.txt
python main.py [config.yaml]
```
`main.py` is the entrypoint. The config path is a positional argument that defaults to `config.yaml`. The config is reloaded at the start of every cycle, so edits take effect without a restart. The loop runs forever; interrupt with Ctrl-C — SIGINT triggers a clean shutdown after the current sleep finishes. Logging goes to stdout only (there is no log file).

### Docker
The committed `Dockerfile` still `COPY`s and runs lowercase `collexions.py`, which no longer exists — **update its `COPY` and `CMD` to `main.py` before building.** The runnable entrypoint is `main.py`; mount the YAML config, and read logs via `docker logs` (the app logs to stdout, not a file):
```
docker build -t collexions .
docker run -d \
  -v $(pwd)/config.yaml:/app/config.yaml \
  collexions
```

## Configuration (`config.yaml`)

All runtime behaviour is controlled by a YAML config (default path `config.yaml`, overridable as the positional CLI argument). It is reloaded at the start of every cycle, so changes take effect without a restart. Parsed and validated by `config.py`, which raises `ConfigError` with a human-readable message on any problem.

### Top-level keys

| Key | Type | Purpose |
|-----|------|---------|
| `plex_url` | string (required) | Base URL of the Plex server (e.g. `http://192.168.1.x:32400`) |
| `plex_token` | string (required) | Plex authentication token |
| `library_names` | list of strings (required) | Libraries to manage (e.g. `[Movies, TV Shows]`) |
| `cadence` | mapping (required) | Timing and global defaults — see below |
| `groups` | mapping (required) | Named collection groups that `pick` slots draw from — see below |
| `home` | list of slots (required) | Ordered home-screen slots — see below |
| `webhook_url` | string (optional) | POSTed a per-cycle summary of pinned titles; omit to disable |

### `cadence`

| Key | Default | Purpose |
|-----|---------|---------|
| `interval_minutes` | — (required, > 0) | Minutes between cycles |
| `repeat_block_hours` | `24` | Hours a pinned collection is blocked from being re-pinned |
| `min_items_for_pinning` | `10` | Collections with fewer items are skipped |

### `groups`

A mapping of group name → group definition. Each group scopes a set of a library's collections and, optionally, when the group is eligible. A `pick` slot resolves to the first eligible group in its list.

| Key | Type | Purpose |
|-----|------|---------|
| `library` | string (required) | Which library the group's collections live in |
| `date` | `MM-DD/MM-DD` (optional) | Date window the group is eligible; cross-year ranges (e.g. `12-26/01-03`) supported |
| `time` | `HH:MM-HH:MM` (optional) | Time-of-day window the group is eligible |
| `include_labels` | list (optional) | Restrict to collections carrying any of these Plex labels |
| `include_collections` | list (optional) | Restrict to these collection titles |
| `exclude_labels` | list (optional) | Drop collections carrying any of these labels |
| `exclude_collections` | list (optional) | Drop these collection titles |
| `repeat_block_hours` | inherits `cadence` | Per-group override of the recency block |
| `min_items_for_pinning` | inherits `cadence` | Per-group override of the min-items threshold |

### `home` slots

An ordered list; the order is the home-screen order the pinned hubs are moved into. Each entry is exactly one of:

- **Fixed slot** — `{collection: "<title>"}` — always pins that exact collection.
- **Pick slot** — `{pick: [<group>, ...]}` — walks the groups in order and stops at the first one that has an eligible, non-repeat-blocked, not-already-used collection, then pins one of that group's collections chosen at random. Every group name referenced must be defined under `groups`, or the config fails to load.

Collections are de-duplicated across slots, so the same collection never occupies two slots.

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
    library: Movies
    date: 10-01/10-31
    include_labels: [Horror]
  staff-picks:
    library: Movies
    include_collections: [A24, Studio Ghibli]

home:
  - collection: Trending Movies       # fixed slot — always this collection
  - pick: [halloween, staff-picks]    # first eligible group wins
```

## Key files

| File | Purpose |
|------|---------|
| `config.py` | New config loader — parses and validates the YAML config into typed dataclasses |
| `eligibility.py` | Group eligibility engine — evaluates date/time constraints and applies include/exclude/min-items filters |
| `plex_client.py` | Plex data-access layer — connects to PlexServer, fetches collections per library as CollectionInfo objects |
| `history.py` | Repeat-block history — tracks pinned collections with timestamps, answers "is this blocked?" |
| `resolver.py` | Slot resolver — walks configured home slots, resolves fixed + pick slots (sequential group priority), dedups across slots |
| `pinning.py` | Pin/unpin engine — fully manages the home screen (ADR-0002): pins resolved collections, unpins everything else, updates repeat-block history |
| `ordering.py` | Hub ordering — reorders home-screen managed hubs to match the resolved slot order via the Plex Move Hub API (`ManagedHub.move`) |
| `webhook.py` | Optional webhook notifier — POSTs a per-cycle summary (pinned titles + timestamp) when `webhook_url` is configured; never raises |
| `main.py` | Main loop + CLI entrypoint — reloads config each cycle, wires fetch → resolve → pin → order → webhook, sleeps `interval_minutes`, clean SIGINT shutdown, per-cycle error retry |
| `test_config.py` | Pytest suite for config loader (run with `.venv/bin/pytest`) |
| `test_history.py` | Pytest suite for repeat-block history |
| `test_eligibility.py` | Pytest suite for group eligibility engine |
| `test_plex_client.py` | Pytest suite for Plex client (fully mocked) |
| `test_resolver.py` | Pytest suite for slot resolver |
| `test_pinning.py` | Pytest suite for pin/unpin engine (fully mocked) |
| `test_ordering.py` | Pytest suite for hub ordering (fully mocked) |
| `test_webhook.py` | Pytest suite for the webhook notifier (fully mocked) |
| `test_main.py` | Pytest suite for the main loop + run_cycle wiring (fully mocked) |
| `ColleXions.py` | Original script (reference only — being superseded by the rewrite) |
| `config.json` | Runtime configuration (not committed with real credentials) |
| `requirements.txt` | Python dependencies (`plexapi`, `requests`, plus unused stubs) |
| `Dockerfile` | Container build; uses `python:3.12-slim-bullseye` |
| `collexions_template.xml` | Plex XML template (reference/documentation artifact, not used by the script) |
| `selected_collections.json` | Auto-generated at runtime; tracks pinning history for recency blocking |
| `logs/collexions.log` | Auto-generated at runtime; overwritten each process start (mode `'w'`) |

## Gotchas

- **Log file is truncated on each restart** — `FileHandler` opens with `mode='w'`. There is no log rotation; disk usage is bounded but history is lost.
- **`selected_collections.json` is mutable state** — delete it to reset the recency-block history. The script handles a missing or corrupt file gracefully.
- **Dockerfile COPY uses lowercase** (`collexions.py`) but the repo file is `ColleXions.py`. On Linux containers this will fail silently at build time if the casing doesn't match.
- **`requirements.txt` includes unused packages** (`Werkzeug`, `schedule`, `pyyaml`, `psutil`) — likely leftovers from earlier iterations. Only `plexapi` and `requests` are actually imported.
- **Config is validated on load** — missing `collexions_label` defaults to `'Pinned by Collexions'` with a warning; missing `plex_url`, `plex_token`, or `pinning_interval` causes `sys.exit(1)`.
- **`categories` config is mutated in place** — `select_from_categories` pops and re-inserts `always_call` from the dict. This is safe in the current single-threaded loop but fragile if the structure is ever shared.
- **Unpin scope is all promoted collections** — any collection promoted by other means (manually in Plex) will be unpinned each cycle unless its title is in `exclusion_list`.
