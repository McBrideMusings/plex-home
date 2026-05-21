# Group-scoped global overrides

`repeat_block_hours` and `min_items_for_pinning` are global defaults that any group can override locally. This covers cases like date-specific groups (e.g. a birthday collection) that should pin on every run regardless of recency, or niche groups where the global item-count threshold is too strict.

A group-level `repeat_block_hours: 0` bypasses the recency check only — date, time, label, and name constraints on that group still apply normally.

## Consequences

Any group with `repeat_block_hours: 0` will resolve to the same collection on every consecutive run as long as its other constraints are met. This is intentional for "always pin this on this date" use cases.
