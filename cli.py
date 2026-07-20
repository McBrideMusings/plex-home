"""CLI subcommand parsing, dispatch, and output for the home-screen tool (ADR-0005).

Defines the ``list`` / ``pin`` / ``unpin`` / ``move`` commands over the imperative
``hubs`` layer. ``run`` (the daemon) is wired in ``main`` and dispatched from here.
"""
from __future__ import annotations
import argparse
import json
import logging

from plexapi.server import PlexServer

import hubs
from config import Config
from hubs import HubError

log = logging.getLogger("collexions")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collexions", description="ColleXions — Plex collection pinner")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML config (default: config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Run the pinning daemon (reconciles home to config each cycle)")

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
        pinned = {v.title for v in hubs.list_pinned(plex, [name], config)[name]}
        if title in avail or title in pinned:
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
            if any(v.title == target for v in hubs.list_pinned(plex, [name], config)[name])]
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


HANDLERS = {"list": cmd_list, "pin": cmd_pin, "unpin": cmd_unpin, "move": cmd_move}
