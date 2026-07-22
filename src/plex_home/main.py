from __future__ import annotations
import logging
import signal
import time
from datetime import datetime, timezone

from . import cli
from .config import load_config, ConfigError, Config
from .plex_client import connect, fetch_collections
from .hubs import HubError
from .history import load_history, save_history
from .resolver import resolve_slots, ResolvedPin
from .pinning import apply_pins
from .ordering import apply_order
from .webhook import send_webhook

log = logging.getLogger("plex_home")

# How long to wait before retrying when the config file itself won't load.
CONFIG_ERROR_RETRY_MINUTES = 5

# Toggled to False by the SIGINT handler to break the main loop cleanly.
_running = True


def run_cycle(config: Config) -> list[ResolvedPin]:
    """Run one full pin cycle and return the resolved pins (library + title).

    Connects to Plex, fetches collections, resolves the configured slots, applies
    the pin/unpin engine, enforces hub ordering, and fires the webhook if one is
    configured. Persists the updated repeat-block history.
    """
    plex = connect(config.plex_url, config.plex_token)
    all_collections = fetch_collections(plex, config.library_names)
    history = load_history()
    now = datetime.now(tz=timezone.utc)

    resolved = resolve_slots(config, all_collections, history, now)
    log.info(
        "Resolved %d collection(s): %s",
        len(resolved),
        ", ".join(f"{p.title} ({p.library})" for p in resolved) or "(none)",
    )

    pin_result = apply_pins(
        plex, config.library_names, resolved, history,
        mirror_recommended=config.cadence.mirror_recommended,
    )
    save_history(pin_result.history)

    apply_order(plex, config.library_names, resolved)

    if config.webhook_url:
        send_webhook(config.webhook_url, [p.title for p in resolved], now)

    return resolved


def _handle_sigint(signum, frame) -> None:
    global _running
    _running = False
    log.info("SIGINT received — shutting down after the current sleep.")


def _interruptible_sleep(minutes: float) -> None:
    """Sleep for ``minutes``, waking early (within ~1s) if a shutdown is requested."""
    for _ in range(int(minutes * 60)):
        if not _running:
            return
        time.sleep(1)


def run_daemon(config_path: str) -> int:
    """Run the pinning daemon: reload config each cycle, reconcile, sleep, repeat."""
    signal.signal(signal.SIGINT, _handle_sigint)
    log.info("Plex Home starting — config: %s", config_path)

    while _running:
        # Reload config every cycle so edits take effect without a restart.
        try:
            config = load_config(config_path)
        except ConfigError as e:
            log.error("Config error: %s — retrying in %d min", e, CONFIG_ERROR_RETRY_MINUTES)
            _interruptible_sleep(CONFIG_ERROR_RETRY_MINUTES)
            continue

        try:
            resolved = run_cycle(config)
            log.info("Cycle complete — %d collection(s) pinned", len(resolved))
        except Exception as e:
            log.error("Cycle failed: %s — retrying next cycle", e)

        _interruptible_sleep(config.cadence.interval_minutes)

    log.info("Plex Home stopped cleanly.")
    return 0


def _run_command(args) -> int:
    """Load config, connect to Plex, and dispatch a one-shot CLI subcommand."""
    try:
        config = load_config(args.config)
    except ConfigError as e:
        log.error("Config error: %s", e)
        return 2
    try:
        plex = connect(config.plex_url, config.plex_token)
    except Exception as e:
        log.error("Could not connect to Plex: %s", e)
        return 2
    try:
        return cli.HANDLERS[args.command](plex, config, args)
    except HubError as e:
        log.error("%s", e)
        return 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = cli.build_parser().parse_args(argv)
    if args.command == "run":
        return run_daemon(args.config)
    return _run_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
