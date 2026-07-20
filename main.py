from __future__ import annotations
import argparse
import logging
import signal
import time
from datetime import datetime, timezone

from config import load_config, ConfigError, Config
from plex_client import connect, fetch_collections
from history import load_history, save_history
from resolver import resolve_slots
from pinning import apply_pins
from ordering import apply_order
from webhook import send_webhook

log = logging.getLogger("collexions")

# How long to wait before retrying when the config file itself won't load.
CONFIG_ERROR_RETRY_MINUTES = 5

# Toggled to False by the SIGINT handler to break the main loop cleanly.
_running = True


def run_cycle(config: Config) -> list[str]:
    """Run one full pin cycle and return the resolved (pinned) collection titles.

    Connects to Plex, fetches collections, resolves the configured slots, applies
    the pin/unpin engine, enforces hub ordering, and fires the webhook if one is
    configured. Persists the updated repeat-block history.
    """
    plex = connect(config.plex_url, config.plex_token)
    all_collections = fetch_collections(plex, config.library_names)
    history = load_history()
    now = datetime.now(tz=timezone.utc)

    resolved = resolve_slots(config, all_collections, history, now)
    log.info("Resolved %d collection(s): %s", len(resolved), ", ".join(resolved) or "(none)")

    pin_result = apply_pins(plex, config.library_names, resolved, history)
    save_history(pin_result.history)

    apply_order(plex, config.library_names, resolved)

    if config.webhook_url:
        send_webhook(config.webhook_url, resolved, now)

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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="ColleXions — Plex collection pinner")
    parser.add_argument(
        "config",
        nargs="?",
        default="config.yaml",
        help="Path to the YAML config file (default: config.yaml)",
    )
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, _handle_sigint)
    log.info("ColleXions starting — config: %s", args.config)

    while _running:
        # Reload config every cycle so edits take effect without a restart.
        try:
            config = load_config(args.config)
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

    log.info("ColleXions stopped cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
