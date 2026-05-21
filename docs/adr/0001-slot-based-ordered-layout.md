# Slot-based ordered layout

The home screen is declared as an explicit ordered list of slots in the config. Each slot is either a fixed collection (always pinned in that position) or a `pick:` with an ordered list of groups (first eligible group wins; slot is skipped if none resolve). The Plex Move Hub API enforces the declared position of each pinned hub.

This replaces the prior "pin N random collections per library" model, which gave no ordering control. The slot model makes the home screen layout fully declarative — you see in the config exactly what will appear and in what order.

## Considered Options

- **N-per-library random:** simple but provides no ordering and no way to mix movies and TV in a specific sequence.
- **Slot-based:** more config to write upfront, but the config is the complete spec for the home screen. Ordering is guaranteed.
