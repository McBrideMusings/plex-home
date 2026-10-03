"""CLI subcommand parsing, dispatch, and output for the home-screen tool (ADR-0005).

Defines the ``list`` / ``pin`` / ``unpin`` / ``move`` commands over the imperative
``hubs`` layer, and ``tags`` over plex-db-ex's snapshot (``tagsource``). ``run`` (the
daemon) and ``once`` (a single forced reconcile) are parsed here but dispatched in
``main``, which owns the cycle they both drive.
"""
from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path

from plexapi.server import PlexServer

from . import hubs
from . import simulate
from . import tagsource
from .config import Config, load_config
from .hubs import HubError
from .plex_client import fetch_collections

log = logging.getLogger("plex_home")


#: Rows ``tags`` prints as text when ``--limit`` is not given. ``--json`` has no
#: default cap, since a program reading it wants every row.
TEXT_ROW_LIMIT = 50


def _positive_int(value: str) -> int:
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plex-home", description="Plex Home — Plex collection pinner")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML config (default: config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Run the pinning daemon (reconciles home to config each cycle)")
    sub.add_parser(
        "once",
        help="Reconcile the home to the config once and exit (leaves a running daemon's schedule alone)",
    )

    p_list = sub.add_parser("list", help="List the pinned home-screen hubs per library")
    p_list.add_argument("--library", help="Limit to one library")
    p_list.add_argument("--available", action="store_true", help="Also list unpinned collections")
    p_list.add_argument("--json", action="store_true", dest="as_json", help="Machine-readable output")

    p_pin = sub.add_parser("pin", help="Pin a collection to the home screen")
    p_pin.add_argument("title", help="Collection title to pin")
    p_pin.add_argument("--library", help="Library the collection is in (auto-resolved if unambiguous)")
    place = p_pin.add_mutually_exclusive_group()
    place.add_argument("--to", type=int, dest="to_index", metavar="K", help="Place at index K after pinning")
    place.add_argument("--to-top", action="store_true", help="Place at the top after pinning")
    p_pin.add_argument("--dry-run", action="store_true", help="Print the operation without calling Plex")

    p_unpin = sub.add_parser("unpin", help="Unpin a hub, addressed by index or title")
    p_unpin.add_argument("target", help="Pinned-hub index (per --library) or exact title")
    p_unpin.add_argument("--library", help="Library (required when target is an index)")
    p_unpin.add_argument("--dry-run", action="store_true", help="Print the operation without calling Plex")

    p_move = sub.add_parser("move", help="Move a pinned hub within a library")
    p_move.add_argument("target", help="Pinned-hub index (per --library) or exact title")
    p_move.add_argument("--library", help="Library (required when target is an index)")
    dest = p_move.add_mutually_exclusive_group(required=True)
    dest.add_argument("--to", type=int, dest="to_index", metavar="K", help="Move to index K")
    dest.add_argument("--up", type=int, nargs="?", const=1, metavar="N", help="Move up N (default 1)")
    dest.add_argument("--down", type=int, nargs="?", const=1, metavar="N", help="Move down N (default 1)")
    dest.add_argument("--to-top", action="store_true", help="Move to the top")
    dest.add_argument("--to-bottom", action="store_true", help="Move to the bottom")
    p_move.add_argument("--dry-run", action="store_true", help="Print the operation without calling Plex")

    p_sim = sub.add_parser(
        "simulate",
        help="Dry-run the pinner forward in time and write a report (reads Plex, never writes)",
    )
    p_sim.add_argument("--days", type=float, default=7.0, help="Days to simulate (default: 7)")
    p_sim.add_argument("--start", help="Sim start as YYYY-MM-DD or ISO 8601, read in cadence.timezone (default: now)")
    p_sim.add_argument("--seed", type=int, default=0, help="RNG seed for reproducible picks (default: 0)")
    p_sim.add_argument("--out", default="simulation_report.txt", help="Report file to write (default: simulation_report.txt)")

    p_tags = sub.add_parser(
        "tags",
        help="Query plex-db-ex's tag and watch data (reads the snapshot, never Plex)",
    )
    tags_sub = p_tags.add_subparsers(dest="tags_command", required=True)
    t_query = tags_sub.add_parser("query", help="Titles carrying every given tag")
    t_query.add_argument("tags", nargs="+", help="Tag(s), any spelling plex-db-ex recorded")
    t_query.add_argument("--kind", choices=tagsource.KINDS, help="Limit to movies or shows")
    t_related = tags_sub.add_parser("related", help="Tags most often carried alongside a tag")
    t_related.add_argument("tag")
    t_related.add_argument("--kind", choices=tagsource.KINDS, required=True)
    t_plays = tags_sub.add_parser("plays", help="Most-played titles (episodes rolled up to shows)")
    t_plays.add_argument("--min-seconds", type=int, default=0, help="Drop plays known to be shorter (default: 0)")
    for p in (t_query, t_related, t_plays):
        p.add_argument("--limit", type=_positive_int,
                       help=f"Rows to print (default: {TEXT_ROW_LIMIT} as text, all with --json)")
        p.add_argument("--json", action="store_true", dest="as_json", help="Machine-readable output")

    return parser


def _target_libraries(config: Config, library: str | None) -> list[str]:
    if library is None:
        return config.library_names
    if library not in config.library_names:
        raise HubError(f"Library {library!r} is not in library_names {config.library_names}")
    return [library]


def _resolve_pin_library(plex: PlexServer, config: Config, title: str, library: str | None) -> str:
    """Find which configured library holds a collection titled ``title``."""
    candidates = _target_libraries(config, library)
    hits = []
    for name in candidates:
        avail = hubs.list_available(plex, [name])[name]
        if title in avail or title in hubs.pinned_titles(plex, name):
            hits.append(name)
    if not hits:
        raise HubError(f"No collection titled {title!r} in {candidates}")
    if len(hits) > 1:
        raise HubError(f"Collection {title!r} exists in {hits} — disambiguate with --library")
    return hits[0]


def _resolve_target_library(plex: PlexServer, config: Config, target: str, library: str | None) -> str:
    """Find which configured library has a pinned hub matching ``target`` (title or index)."""
    if target.isdigit() and library is None:
        raise HubError("An index target needs --library (indices are per-library)")
    candidates = _target_libraries(config, library)
    if target.isdigit():
        return candidates[0]
    hits = [name for name in candidates
            if target in hubs.pinned_titles(plex, name)]
    if not hits:
        raise HubError(f"No pinned hub titled {target!r} in {candidates}")
    if len(hits) > 1:
        raise HubError(f"Pinned hub {target!r} exists in {hits} — disambiguate with --library")
    return hits[0]


def cmd_list(plex: PlexServer, config: Config, args) -> int:
    libs = _target_libraries(config, args.library)
    pinned = hubs.list_pinned(plex, libs, config)
    available = hubs.list_available(plex, libs) if args.available else {}

    if args.as_json:
        payload = {
            name: {
                "pinned": [
                    {"index": v.index, "title": v.title, "kind": v.kind, "config_managed": v.config_managed}
                    for v in pinned[name]
                ],
                **({"available": available[name]} if args.available else {}),
            }
            for name in libs
        }
        print(json.dumps(payload, indent=2))
        return 0

    for name in libs:
        views = pinned[name]
        print(f"\n{name} ({len(views)} pinned)")
        for v in views:
            if v.kind == "collection":
                tag = "collection, config" if v.config_managed else "collection, manual"
            else:
                tag = "system"
            print(f"  [{v.index}] {v.title}  ({tag})")
        if args.available:
            pool = available[name]
            print(f"  available ({len(pool)}): {', '.join(pool) if pool else '(none)'}")
    return 0


def cmd_pin(plex: PlexServer, config: Config, args) -> int:
    library = _resolve_pin_library(plex, config, args.title, args.library)
    to_index = 0 if args.to_top else args.to_index
    hubs.pin(plex, library, args.title, to_index=to_index, dry_run=args.dry_run)
    return 0


def cmd_unpin(plex: PlexServer, config: Config, args) -> int:
    library = _resolve_target_library(plex, config, args.target, args.library)
    hubs.unpin(plex, library, args.target, dry_run=args.dry_run)
    return 0


def cmd_move(plex: PlexServer, config: Config, args) -> int:
    library = _resolve_target_library(plex, config, args.target, args.library)
    current, count = hubs.locate(plex, library, args.target)
    if args.to_top:
        to_index = 0
    elif args.to_bottom:
        to_index = count - 1
    elif args.up is not None:
        to_index = max(0, current - args.up)
    elif args.down is not None:
        to_index = min(count - 1, current + args.down)
    else:
        to_index = args.to_index
    hubs.move(plex, library, args.target, to_index, dry_run=args.dry_run)
    return 0


def cmd_simulate(plex: PlexServer, config: Config, args) -> int:
    all_collections = fetch_collections(plex, config.library_names)
    # Reload raw (unexpanded) so the simulator can re-expand {YEAR}-style variables
    # at each simulated cycle instead of at wall-clock load time. The initial load
    # in main._run_command already validated the config (including any re: pattern).
    raw_config = load_config(args.config, expand=False)
    try:
        report = simulate.run_simulation(
            raw_config, all_collections, days=args.days, start=args.start, seed=args.seed
        )
    except ValueError as e:
        raise HubError(str(e))
    out = Path(args.out)
    out.write_text(report, encoding="utf-8")
    print(f"Wrote simulation report:\n{out.resolve()}")
    return 0


def cmd_tags(config: Config, args) -> int:
    """``tags query|related|plays`` — read plex-db-ex's snapshot; no Plex connection."""
    if config.plexdb_snapshot is None:
        raise tagsource.TagSourceError("No plexdb_snapshot set in the config")
    limit = args.limit or (None if args.as_json else TEXT_ROW_LIMIT)
    source = tagsource.SnapshotTagSource(config.plexdb_snapshot)
    try:
        if args.tags_command == "query":
            titles = source.titles_for_tags(args.tags, kind=args.kind)
            rows = [
                {"item_id": t.item_id, "title": t.title, "year": t.year, "kind": t.kind,
                 "plex_keys": [{"section_id": s, "rating_key": k} for s, k in t.plex_keys]}
                for t in titles
            ]
            text = [f"{r['title']} ({r['year']}) [{r['kind']}] "
                    f"{', '.join(k['rating_key'] for k in r['plex_keys'])}" for r in rows]
            total = len(rows)
        elif args.tags_command == "related":
            pairs = source.co_tags(args.tag, args.kind)
            rows = [{"tag": tag, "shared": shared} for tag, shared in pairs]
            text = [f"{r['shared']:>5}  {r['tag']}" for r in rows]
            total = len(rows)
        else:
            counts = source.play_counts(args.min_seconds)
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
            info = source.describe([item_id for item_id, _ in top])
            rows = [
                {"item_id": item_id, "title": info[item_id][0], "year": info[item_id][1],
                 "kind": info[item_id][2], "plays": n}
                for item_id, n in top
            ]
            text = [f"{r['plays']:>5}  {r['title']} ({r['year']}) [{r['kind']}]" for r in rows]
            total = len(counts)
    finally:
        source.close()

    if args.as_json:
        print(json.dumps({"total": total, "rows": rows[:limit]}, indent=2))
        return 0
    shown = text[:limit]
    print(f"{total} result(s){f', showing {len(shown)}' if len(shown) < total else ''}")
    for line in shown:
        print(f"  {line}")
    return 0


HANDLERS = {"list": cmd_list, "pin": cmd_pin, "unpin": cmd_unpin, "move": cmd_move, "simulate": cmd_simulate}
