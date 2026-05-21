# plex-home PRD

## Problem Statement

Managing the Plex home screen is a persistent frustration. The existing open-source tool (ColleXions) that this project builds from solves some of it — rotating collections randomly, blocking repeats — but leaves the hardest parts unsolved. You cannot control the order of pinned hubs. You cannot say "I want this slot to show one thing and that slot to show another." You cannot express "pin the Halloween collection every day in October but fall back to something else the rest of the year." The config is JSON with global inclusion/exclusion lists that don't compose well. The mental model is too far from the actual interface: a scrollable ordered list of rows on a screen.

## Solution

A declarative YAML config that maps directly to the home screen. The user defines an ordered list of slots; each slot is either a fixed collection (always appears there) or a `pick:` that tries a priority-ordered list of named groups. Groups are named pools of collections with optional date, time, label, and name constraints. The script resolves all slots on each run, pins the resolved set, unpins anything currently pinned that isn't in the resolved set, and calls the Plex Move Hub API to enforce the declared order. The config is the complete source of truth for the home screen.

## User Stories

1. As a user, I want to declare my home screen as an ordered list of slots in a config file, so that the layout I see in Plex matches exactly what I wrote.
2. As a user, I want to fix specific collections to specific positions (e.g. "Recently Added in Movies" always first), so that anchor rows never move.
3. As a user, I want to define random slots that draw from named groups of collections, so that the rotation is constrained to content I've curated rather than everything in the library.
4. As a user, I want groups to filter collections by Plex label, collection name, or both (unioned), so that I can categorize content either in Plex or in the config.
5. As a user, I want groups to be eligible only during a configured date range (e.g. Oct 1–31), so that seasonal content appears automatically without manual config changes.
6. As a user, I want groups to be eligible only during a configured time range (e.g. 22:00–05:00), so that late-night content only surfaces at appropriate hours.
7. As a user, I want a `pick:` slot to try groups in priority order and skip the slot entirely if none resolve, so that a temporary ineligibility never forces an inappropriate collection into that position.
8. As a user, I want a global repeat-block window that prevents any collection from appearing again within N hours, so that I don't see the same row two days running.
9. As a user, I want to override the repeat-block window per group (including setting it to 0), so that date-specific groups like a birthday collection always resolve to the same collection every run that day.
10. As a user, I want a global minimum-items threshold that excludes thin collections, so that I don't surface embarrassingly short rows, with the ability to override it per group.
11. As a user, I want the script to unpin everything not in the resolved slot set on each run, so that the home screen reflects the config exactly without manual cleanup.
12. As a user, I want hub ordering enforced after pinning, so that the Plex home screen row order matches the slot order in my config.
13. As a user, I want an optional webhook URL I can configure for cycle-completion notifications, so that I can integrate with whatever notification system I actually use.
14. As a user, I want the config to be YAML so that I can write comments and read nested structures without fighting JSON syntax.

## Implementation Decisions

- **Slot-based ordered layout** (ADR-0001): the `home:` key is a list; position in the list is position on the home screen. Fixed slots use `collection:`, random slots use `pick:` with a list of group names.
- **Fully-managed home screen** (ADR-0002): on each run, pin the resolved set and unpin all currently-pinned hubs not in that set. No concept of user-pinned hubs the script leaves alone — the config is total.
- **Group-scoped global overrides** (ADR-0003): `repeat_block_hours` and `min_items_for_pinning` are top-level globals; any group can override them locally. `repeat_block_hours: 0` bypasses only recency — date, time, label, and name constraints still apply.
- **No Plex label tracking for ownership**: the prior codebase used a Plex label to distinguish "our" pins from "user" pins. Dropped — fully-managed ownership makes the distinction irrelevant. Run history lives in a local JSON file.
- **Hub ordering via Move Hub API**: after pinning, call `PUT /hubs/{sectionId}/manage/{hubId}/move` to enforce the declared slot order. This is the key capability the prior tool lacked.
- **YAML config** (ADR-0004): over JSON for readability with nested mixed-shape structures and inline comments.
- **Group filtering union semantics**: `include_labels` and `include_collections` within a group are unioned — a collection is eligible if it matches any label OR is explicitly named. `exclude_labels` and `exclude_collections` apply independently (both must pass).

## Testing Decisions

- Test the slot resolver and group eligibility engine through their public interfaces (input: parsed config + mock collection list + current datetime → output: ordered list of collection names to pin). No Plex API calls in unit tests.
- Test date and time constraint evaluation with fixed clock inputs covering edge cases (boundary dates, midnight rollover, multi-day ranges spanning year boundaries like Dec 26–Jan 3).
- Test repeat-block logic: a collection recently pinned should be excluded; one outside the window should be eligible; one in a group with `repeat_block_hours: 0` should always be eligible.
- Test the fully-managed unpin behavior: collections in the current pinned set but not in the resolved slots should appear in the unpin list.
- Integration tests against a live Plex instance are the user's responsibility — the test suite does not require Plex credentials.

## Out of Scope

- Cross-library collection merging (tracked as a Post-MVP spike in issue #1)
- Config templating for dynamic collection names like `{YEAR}` (tracked as Post-MVP spike in issue #2)
- Multi-user Plex setups (single token, single home screen)
- VitePress documentation site (plain markdown only for now)

## Further Notes

- The `pick:` list within a slot is tried top-to-bottom; the first group that is currently eligible (passes date/time constraints) AND has at least one eligible collection (passes label/name filters, repeat-block, min-items) fills the slot. If no group resolves, the slot produces nothing — Plex home screen simply has one fewer row, no blank gap.
- The config draft lives at `~/.claude/plans/plex-home-config-syntax.yaml` as a reference for the YAML schema implementation.
- The Plex API reference is indexed at `https://developer.plex.tv/pms/` — Move Hub endpoint is `PUT /hubs/{sectionId}/manage/{hubId}/move`.
