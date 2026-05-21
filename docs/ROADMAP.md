# plex-home Roadmap

## Now

- [ ] YAML config loader and schema validator
- [ ] Group eligibility engine (date range, time range, label filters, name filters)
- [ ] Slot resolver (fixed slots, pick slots with sequential group priority)
- [ ] Repeat-block history (local JSON, global + per-group override)
- [ ] Min-items threshold (global + per-group override)
- [ ] Plex collection fetcher (per library, with label and name filtering)
- [ ] Pin/unpin engine (fully-managed: pin resolved set, unpin everything else)
- [ ] Hub ordering via Move Hub API

## Next

- [ ] Optional webhook notification on cycle completion
- [ ] Dry-run / preview mode (log what would be pinned without touching Plex)
- [ ] Config validation with human-readable errors (unknown group names, invalid date formats, etc.)

## Later

- [ ] Cross-library collection support (pending spike #1)
- [ ] Config templating for dynamic collection names (pending spike #2)

## Deferred
