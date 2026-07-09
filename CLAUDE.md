# ColleXions — Plex Collection Pinner

ColleXions is a Python daemon that automatically rotates which collections are "pinned" (promoted to the home screen) on a Plex Media Server. On each cycle it unpins all currently promoted collections, then selects new ones per library using a three-tier priority: date-gated special collections first, category-weighted randoms second, fully random fill third. It logs to stdout and a rolling file, optionally posts per-pin notifications to a Discord webhook, and tags every pinned collection with a configurable Plex label so they can be identified or filtered in the Plex UI.

## Running

### Bare Python
```
pip install -r requirements.txt
python ColleXions.py
```
`config.json` must exist alongside the script. The script loops forever; interrupt with Ctrl-C.

### Docker
```
docker build -t collexions .
docker run -d \
  -v $(pwd)/config.json:/app/config.json \
  -v $(pwd)/logs:/app/logs \
  collexions
```
The Dockerfile copies `collexions.py` (lowercase), so the file name casing matters on case-sensitive filesystems. The repo uses `ColleXions.py` (mixed case) — adjust the COPY line if needed.

## Configuration (`config.json`)

All runtime behaviour is controlled by `config.json`. It is reloaded at the start of every cycle, so changes take effect without a restart. Required keys:

| Key | Type | Purpose |
|-----|------|---------|
| `plex_url` | string | Base URL of the Plex server (e.g. `http://192.168.1.x:32400`) |
| `plex_token` | string | Plex authentication token |
| `library_names` | array | Libraries to manage (e.g. `["Movies", "TV Shows"]`) |
| `pinning_interval` | number | Minutes between cycles |
| `collexions_label` | string | Plex label applied to pinned collections; removed on unpin |
| `number_of_collections_to_pin` | object | Per-library pin limit: `{"Movies": 4, "TV Shows": 3}` |

Optional keys:

| Key | Default | Purpose |
|-----|---------|---------|
| `exclusion_list` | `[]` | Collection titles never pinned |
| `regex_exclusion_patterns` | `[]` | Regex patterns matched case-insensitively against titles |
| `repeat_block_hours` | `12` | Hours a non-special collection is blocked from being re-pinned |
| `min_items_for_pinning` | `10` | Collections with fewer items are skipped |
| `categories` | `{}` | Per-library category buckets; one random pick per category per cycle |
| `special_collections` | `[]` | Date-windowed collections always pinned when in range (MM-DD format) |
| `discord_webhook_url` | — | Optional Discord webhook for pin notifications |

### Categories
Each library key under `categories` contains named groups, each holding a list of collection titles. One title is randomly selected from each group per cycle. The `always_call` boolean (default `true`) controls whether each category is guaranteed to contribute a pick (`true`) or randomly skipped (`false`).

### Special collections
Special entries have `start_date`, `end_date` (both `MM-DD`), and `collection_names`. They are pinned with highest priority when the current date falls in the window. Cross-year ranges (e.g. Dec 26 – Jan 3) are supported. Special collections are never added to the recency-block history, so they can repeat every cycle within their window.

## Key files

| File | Purpose |
|------|---------|
| `config.py` | New config loader — parses and validates the YAML config into typed dataclasses |
| `eligibility.py` | Group eligibility engine — evaluates date/time constraints and applies include/exclude/min-items filters |
| `plex_client.py` | Plex data-access layer — connects to PlexServer, fetches collections per library as CollectionInfo objects |
| `history.py` | Repeat-block history — tracks pinned collections with timestamps, answers "is this blocked?" |
| `resolver.py` | Slot resolver — walks configured home slots, resolves fixed + pick slots (sequential group priority), dedups across slots |
| `pinning.py` | Pin/unpin engine — fully manages the home screen (ADR-0002): pins resolved collections, unpins everything else, updates repeat-block history |
| `test_config.py` | Pytest suite for config loader (run with `.venv/bin/pytest`) |
| `test_history.py` | Pytest suite for repeat-block history |
| `test_eligibility.py` | Pytest suite for group eligibility engine |
| `test_plex_client.py` | Pytest suite for Plex client (fully mocked) |
| `test_resolver.py` | Pytest suite for slot resolver |
| `test_pinning.py` | Pytest suite for pin/unpin engine (fully mocked) |
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
