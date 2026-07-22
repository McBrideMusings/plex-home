# plex-home

Declarative Plex home-screen manager. Defines an ordered list of slots; each slot resolves to a pinned collection hub at runtime. Fully manages the home screen — the config is the source of truth.

## Language

### Domain

- **Pin / Pinned** — Plex's own term for promoting a collection to the home screen. Use `pin`/`pinned` in code and docs; avoid `promote`/`promoted` (Plex's internal API field name).
- **Hub** — Plex's term for a home-screen row. A pinned collection becomes a hub. System hubs (Recently Added, Continue Watching, On Deck) are Plex-managed and distinct from user collection hubs.
- **Managed hub** — Any home-screen row Plex exposes per library via `section.managedHubs()` as a `ManagedHub`, whether a system hub or a user collection hub. All managed hubs support the same operations — `move`, `promoteHome`/`demoteHome` (pin/unpin), `promoteShared`/`demoteShared` — so the CLI treats system and collection hubs uniformly for list/move/pin/unpin. Only deletion is restricted (system hubs are not `deletable`), and the CLI does not delete.
- **Collection** — A user-created or library-auto-generated grouping of media items. Can be pinned as a hub. Distinct from system hubs.
- **Slot** — One position in the declared home screen layout. Either fixed (always resolves to a named collection) or a `pick:` (resolves via group priority).
- **Group** — A named pool of collections with optional constraints (date range, time range, label filters, name filters). Groups are referenced by slots. A group is *eligible* when all its constraints pass.
- **Pick** — A slot type that tries an ordered list of groups sequentially; the first eligible group that yields a collection fills the slot. If no group resolves, the slot is skipped.
- **Cycle** — One full run of the script: resolve all slots → pin resolved set → unpin anything not in resolved set → enforce hub ordering via Move Hub API.
- **Repeat block** — A recency window (`repeat_block_hours`) that prevents the same collection from being picked again within N hours. Bypassable per group with `repeat_block_hours: 0`.
- **Seasonal group** — A group with a `date:` constraint that makes it eligible only during a specific annual date range (e.g. Oct 1–31 for Halloween content). Previously called "special collections" in the original codebase — avoid that term.
- **Config-managed hub / Manual hub** — A pinned collection whose title is in the daemon's resolved config set is *config-managed*; one pinned but absent from the config (e.g. via the CLI or the Plex UI) is *manual*. The CLI's `list` computes this by comparing live hubs against the resolved config, not from any Plex label. A running daemon cycle reconciles the home screen back to the config, so manual pins are ephemeral against it.
- **Cross-library collection** — Two collections sharing a title across different libraries (e.g. a `Star Trek` collection in both Movies and TV Shows). Plex does **not** merge them: each pins as its own hub, keyed per library. There is no cross-library hub, so "one themed row spanning libraries" is not achievable by naming alone (verified — see below).

### Architecture

{Seeded on first run of `improve-codebase-architecture`.}

## Relationships

- A **slot** references one or more **groups** (via `pick:`) or a specific collection name directly.
- A **group** filters the **collection** pool from one library using labels and/or explicit names.
- A **cycle** resolves all slots, then calls the Plex API to pin and reorder the resulting hubs.

## Flagged ambiguities

- **"Special collections"** (original codebase term) — means date-range-gated priority collections. Replaced by the **seasonal group** pattern in this codebase. Do not use "special collections."
- ~~**Cross-library collections**~~ — resolved 2026-07-22, see "Verified Plex behaviour" below.

## Verified Plex behaviour

### Same-named collections across libraries do not merge (issue #1, verified 2026-07-22)

Tested live against the server by pinning the pre-existing `Star Trek` collection in **both** Movies (section 1) and TV Shows (section 2), then reading `/hubs/promoted`. Both collections were unpinned again afterwards, restoring the baseline.

**Result — two separate hubs, not one merged hub.** `/hubs/promoted` returned two entries, both titled `Star Trek`:

| Library | `hubIdentifier` | `key` |
|---------|-----------------|-------|
| Movies (section 1) | `custom.collection.1.47605.47605` | `/library/collections/47605/children` |
| TV Shows (section 2) | `custom.collection.2.146315.146315` | `/library/collections/146315/children` |

Hub identity embeds the **library section key** and the collection's **ratingKey**, so a collection hub is per-library by construction — a shared title is coincidental and carries no grouping meaning. `section.managedHubs()` agrees, exposing `custom.collection.<sectionKey>.<ratingKey>` per library.

**Consequences for this tool:**

- Cross-library themed grouping is **not** achievable by naming collections identically. It needs a different mechanism — a playlist, or accepting two separate hubs.
- Even pinning both, they cannot be made adjacent on the home screen: hub order is per-library (ADR-0006), so the two rows land in different library blocks and no cross-library ordering exists.
- Three titles already occur in more than one library on this server (`Star Trek`, `IMDb Popular`, `Streaming Collections`). Each is an independent hub, and each is blocked independently by repeat-block history, which is keyed `(library, title)` — consistent with this finding.
