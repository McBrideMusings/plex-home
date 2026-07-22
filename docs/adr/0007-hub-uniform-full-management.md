# Hub-uniform full management across all three visibility axes

ADR-0002 made the config the sole manager of the pinned home screen, but the daemon only ever touched **collections** and only the **home** axis. Plex's "Manage Recommendations" list is wider than that: it mixes user *collection* hubs with built-in *system* hubs (Recently Added, Top Rated, …), and each row carries three independent flags — Library Recommended (`promotedToRecommended`), Home (`promotedToOwnHome`), and Friends' Home (`promotedToSharedHome`). The old engine ignored system hubs and the Recommended flag entirely, so a home row the user actually wanted (Recently Added) could not be expressed in config, and the Recommended list accreted every collection ever pinned — nothing ever cleared it.

The config is now the complete truth for **everything promoted anywhere** in a managed library. Each cycle, over `section.managedHubs()` (system + collection rows uniformly), the daemon:

1. **Promotes resolved pins** to Home + Friends' Home. A resolved collection missing from the managed list (previously removed, or never promoted) is re-created from its collection visibility. Recommended is left as-is on resolved pins — clean-extras-only, not force-matched.
2. **Removes** every non-resolved **collection** from the Managed Recommendations list — `ManagedHub.remove()`, the `×` in the Plex UI (`DELETE /hubs/sections/{id}/manage/{identifier}`). This deletes the *recommendation record*, not the collection; the collection and its items are untouched. This is what keeps the list short instead of growing without bound.
3. **Demotes** every non-resolved **system hub** on all three axes. System hubs are `deletable=False` and cannot be removed, so they are demoted in place instead.

A fixed slot's `collection:` key matches **any** managed hub by title — collection or system. On a title collision the **collection wins** (a library can hold both a system `movie.recentlyreleased` and a collection both titled "Recently Released Movies"); a system hub is matched only when no collection carries the title. Pick slots stay collections-only: their label / min-items / eligibility filters are collection properties system hubs do not have.

Ordering uses move-to-top in reverse target order (`managed_hubs.realize_order`), the one Move Hub call Plex honors for system and curated ("… On Plex", "Recently Released") rows that reject an `after=` anchor.

The kind-agnostic primitives shared by the daemon engines (`pinning.py`, `ordering.py`) and the imperative CLI (`hubs.py`) live in `managed_hubs.py` so none of the three owns the others' internals.

Everything is keyed by `(library, title)`, so **two collections sharing an identical title in one library are not distinguishable** — `title_map` keeps one and the sweep un-manages the other. This is inherent to title-based addressing (the resolver, history, and CLI all key by title) and is accepted, not worked around; give collections distinct titles within a library. A collection and a *system* hub of the same title are fine — the collision rule resolves that in the collection's favor.

By default a resolved pin's Library Recommended flag is left as-is (clean-extras-only). Set `cadence.mirror_recommended: true` to also force-promote resolved pins to Recommended, so the Recommended tab mirrors Home exactly.

**Consequence:** the config defines everything promoted anywhere. A collection pinned to Friends' Home or Recommended only — but absent from the config — is removed on the next cycle. If something should appear anywhere on a managed library's home or recommendations, it belongs in the config. Global `/hubs/home` rows (Continue Watching, On Deck) are not section-managed and are never touched.
