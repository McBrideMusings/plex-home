# Per-library home mapping

`home` is a mapping of **library name → ordered list of slots**, not a single flat list. A group no longer carries a `library` field; the library a `pick` filters against is the `home` section that references it. The resolver walks the mapping in order and emits one flat list grouped by library, which the per-section ordering layer honors within each block.

```yaml
home:
  Movies:
    - collection: Trending Movies
    - pick: [halloween, staff-picks]
  TV Shows:
    - collection: Trending TV
    - pick: [prestige-drama]
```

## Why

Plex renders promoted collection hubs **grouped by library**, and there is **no cross-library home order to control**. This is a hard constraint of Plex, not a design choice:

- `ManagedHub.move` targets `/hubs/sections/{librarySectionID}/manage/{identifier}/move` — every managed-recommendation operation is scoped to one library section. There is no cross-section move.
- plexapi wraps no endpoint for reordering the library blocks themselves. The block order (Movies → TV → …) is the account's pinned-source order, set by hand in Plex Web.

The prior flat `home` list (ADR-0001) implied a single top-to-bottom home order the tool cannot deliver — a config that listed a TV collection between two Movies collections would silently render as the Movies block then the TV block. The mapping makes the schema tell the truth: you declare order *within* each library, and the library grouping is Plex's. This supersedes ADR-0001's suggestion that slots could "mix movies and TV in a specific sequence" — cross-library interleaving was never achievable.

## Consequences

- **Library name written once**, as the section key; a group's library is derived from placement instead of repeated on every group.
- **Groups are reusable across libraries** — a group is just a filter, applied against whichever section references it. No uniqueness check; reuse under two libraries is allowed by design (flexibility, no technical reason to forbid).
- `home` keys are validated against `library_names`; an unknown key is a config error.
- **Pin identity is `(library, title)`, not bare title.** Collection titles are only unique within a library, so `resolve_slots` now emits `list[ResolvedPin(library, title)]`, and pinning keys live collections, the promoted set, and the dedup by that pair. Two collections sharing a title in different libraries are managed independently — both can pin, and each blocks recency independently. Repeat-block history (`pin_history.json`) is keyed per-library, persisted as nested `{library: {title: timestamp}}`; a legacy flat file is ignored (disposable state). The webhook payload stays title-only (cosmetic).
- Ordering (`ordering.py`) was made genuinely per-section: `apply_order` now walks one library section at a time, resetting the Move Hub anchor to the top (`after=None`) at each library boundary. Previously it walked the flat list with a single anchor chain, which at each boundary anchored the first hub of the next library to a hub from the *previous* section — a foreign identifier the per-section move endpoint cannot honor. Harmless when every home slot lived in one library; the multi-library mapping makes it reachable, so it is fixed here.

## Considered Options

- **Flat list + per-group `library` (prior):** looked like it controlled a global home order it never could; misleading.
- **Fully nested groups under each library:** self-contained, zero ambiguity, but more verbose and blocks group reuse for no real gain.
- **Per-library mapping, shared top-level groups (chosen):** library named once, groups reusable, schema matches Plex's per-library reality.
