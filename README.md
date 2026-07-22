# Plex Home — Plex Collection Pinner

Plex Home is a Python daemon that automatically manages which collections are **pinned** (promoted to the home screen) on a Plex Media Server.

Every cycle it resolves an ordered list of home-screen **slots** — each slot is either a fixed hub (a collection *or* a built-in Plex system hub) or a **pick** that chooses the first eligible collection from a list of named groups — then fully manages the home screen: it promotes the resolved set, sweeps every other promoted hub (removing stray collections from the Managed Recommendations list, demoting stray system hubs), and reorders the pinned hubs to match slot order. It logs to stdout and optionally POSTs a per-cycle summary to a webhook.

It also ships an imperative CLI (`list` / `pin` / `unpin` / `move`) for driving the live home screen by hand, independent of the config.

## How it works

- **Ordered slots, not a random pool.** You declare the home screen as an ordered list. Slot 1 is the first hub, slot 2 the second, and so on — Plex Home moves the pinned hubs into exactly that order each cycle.
- **Fixed or pick per slot.** A fixed slot always pins one named hub — a collection or a Plex system hub (e.g. `Recently Added Movies`), matched by title (collection wins on a title collision). A pick slot walks a priority list of groups and stops at the first group that has an eligible, non-repeat-blocked, not-already-used collection, then pins one of that group's collections at random (picks are collections-only).
- **Groups scope collections and when they apply.** A group targets one library and can be gated by a date window (e.g. `10-01/10-31` for October), a time-of-day window, label filters, and collection include/exclude lists.
- **Repeat blocking.** A pinned collection is blocked from re-pinning for `repeat_block_hours` so the home screen stays varied.
- **Sole manager across all three visibility axes.** The config is the complete truth for Home, Friends' Home, and Library Recommended. Any hub not in the resolved set is swept each cycle — a stray collection is removed from the Managed Recommendations list (the `×` in Plex; it drops the recommendation record, never the collection itself), a stray system hub is demoted. This keeps the Recommended list from growing without bound. See [ADR-0007](docs/adr/0007-hub-uniform-full-management.md).

## Install

```
pip install -e .
```

This registers the `plex-home` console command. Without installing, the equivalent is `python -m plex_home` with `src/` on `PYTHONPATH`.

## Running the daemon

```
plex-home run [--config config.yaml]
```

`run` reloads the config at the start of every cycle (edits take effect without a restart), reconciles the home screen to the config, then sleeps `interval_minutes`. It runs forever; Ctrl-C (SIGINT) triggers a clean shutdown after the current sleep. Logging is stdout only — no log file.

## CLI — live home-screen control

`list` / `pin` / `unpin` / `move` operate on the live Plex home screen imperatively, independent of the config. The home screen is treated as a per-library, indexed list of pinned hubs (system and collection hubs alike). A running daemon reconciles the home back to the config, so CLI changes are ephemeral against it.

```
plex-home list  [--library NAME] [--available] [--json]
plex-home pin   "Title" [--library NAME] [--to K | --to-top] [--dry-run]
plex-home unpin <index|Title> --library NAME [--dry-run]
plex-home move  <index|Title> --library NAME (--to K | --up [N] | --down [N] | --to-top | --to-bottom) [--dry-run]
```

`--dry-run` prints the intended operation without calling Plex. Index targets are per-library, so `--library` is required for index-based `unpin`/`move`; title targets auto-resolve across configured libraries (error on collision).

## Configuration

All runtime behaviour is a YAML config (default `config.yaml`, overridable with the global `--config` flag), reloaded each cycle.

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

| Key | Purpose |
|-----|---------|
| `plex_url`, `plex_token` | Plex server URL and auth token (required) |
| `library_names` | Libraries to manage (required) |
| `cadence` | `interval_minutes` (required), `repeat_block_hours` (default 24), `min_items_for_pinning` (default 10) |
| `groups` | Named collection groups a `pick` slot draws from — each with optional `date`/`time`/`include_labels`/`include_collections`/`exclude_labels`/`exclude_collections` and per-group cadence overrides. A group carries **no** library; its library is the `home` section that references it (so the same group may be reused under more than one library). |
| `home` | Mapping of **library name → ordered list of slots**; each slot is `{collection: "<title>"}` (any managed hub by title — collection or system) or `{pick: [<group>, ...]}` (collections only). Every key must be one of `library_names`. |
| `webhook_url` | Optional; POSTs a per-cycle summary of pinned titles |

**Home order is per-library, not global.** Plex renders promoted collections grouped by library, and exposes no way to reorder the library blocks themselves (that's your account's pinned-source order, set by hand in Plex). So the list under each `home` library sets the order *within that library's block only*; you cannot lift a TV collection above a Movies one. See [ADR-0006](docs/adr/0006-per-library-home-mapping.md).

> Never share your Plex token. Keep real credentials out of committed files.

## Docker

```
docker build -t plex-home .
docker run -d \
  -v $(pwd)/config.yaml:/app/config.yaml \
  plex-home
```

The image installs the package and runs `plex-home run`. Logs go to stdout — read them with `docker logs`.

## Development

```
pip install -e .
.venv/bin/pytest        # pyproject sets pythonpath=src, so no install is needed to run tests
```

Modules live in `src/plex_home/`; tests in `tests/`. See `CLAUDE.md` for the module map and design notes, and `docs/adr/` for architecture decisions.

## Acknowledgments

Plex Home builds on [ColleXions](https://github.com/jl94x4/ColleXions) by jl94x4, and the PlexAPI library and open-source community.

## License

MIT — see `LICENSE`.
