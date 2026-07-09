# plex-home Roadmap

## Now

- [x] YAML config loader and schema validator
- [x] Group eligibility engine (date range, time range, label filters, name filters)
- [x] Slot resolver (fixed slots, pick slots with sequential group priority)
- [x] Repeat-block history (local JSON, global + per-group override)
- [x] Min-items threshold (global + per-group override)
- [x] Plex collection fetcher (per library, with label and name filtering)
- [x] Pin/unpin engine (fully-managed: pin resolved set, unpin everything else)
- [x] Hub ordering via Move Hub API
- [x] Main loop + CLI entrypoint (config reload per cycle, SIGINT shutdown, cycle-error retry)

## Next

- [x] Optional webhook notification on cycle completion
- [ ] Dry-run / preview mode (log what would be pinned without touching Plex)
- [ ] Config validation with human-readable errors (unknown group names, invalid date formats, etc.)

## Later

- [ ] Cross-library collection support (pending spike #1)
- [ ] Config templating for dynamic collection names (pending spike #2)

## Deferred
