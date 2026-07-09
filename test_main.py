from unittest.mock import patch, MagicMock
import pytest

import main
from config import ConfigError


@pytest.fixture(autouse=True)
def reset_running():
    main._running = True
    yield
    main._running = True


def make_config(webhook_url=None, interval=30):
    cfg = MagicMock()
    cfg.plex_url = "http://plex"
    cfg.plex_token = "tok"
    cfg.library_names = ["Movies"]
    cfg.webhook_url = webhook_url
    cfg.cadence.interval_minutes = interval
    return cfg


def patch_cycle_deps(resolved=None):
    """Patch every external call run_cycle makes; return the ExitStack-like dict of mocks."""
    resolved = resolved if resolved is not None else ["A", "B"]
    pin_result = MagicMock()
    pin_result.history = {"A": "ts"}
    patches = {
        "connect": patch("main.connect", return_value=MagicMock()),
        "fetch": patch("main.fetch_collections", return_value={"Movies": []}),
        "load_history": patch("main.load_history", return_value={}),
        "resolve": patch("main.resolve_slots", return_value=resolved),
        "apply_pins": patch("main.apply_pins", return_value=pin_result),
        "save_history": patch("main.save_history"),
        "apply_order": patch("main.apply_order"),
        "webhook": patch("main.send_webhook", return_value=True),
    }
    started = {name: p.start() for name, p in patches.items()}
    return patches, started, pin_result


def test_run_cycle_wires_pipeline_in_order():
    patches, m, pin_result = patch_cycle_deps(resolved=["A", "B"])
    try:
        config = make_config(webhook_url="http://hook")
        result = main.run_cycle(config)
    finally:
        for p in patches.values():
            p.stop()
    assert result == ["A", "B"]
    m["resolve"].assert_called_once()
    m["apply_pins"].assert_called_once()
    # history saved from the pin engine's returned history
    m["save_history"].assert_called_once_with(pin_result.history)
    m["apply_order"].assert_called_once()
    m["webhook"].assert_called_once()
    assert m["webhook"].call_args.args[0] == "http://hook"
    assert m["webhook"].call_args.args[1] == ["A", "B"]


def test_run_cycle_skips_webhook_when_unconfigured():
    patches, m, _ = patch_cycle_deps()
    try:
        main.run_cycle(make_config(webhook_url=None))
    finally:
        for p in patches.values():
            p.stop()
    m["webhook"].assert_not_called()


def test_run_cycle_passes_pinned_label():
    patches, m, _ = patch_cycle_deps(resolved=["A"])
    try:
        main.run_cycle(make_config())
    finally:
        for p in patches.values():
            p.stop()
    # apply_pins(plex, library_names, resolved, label, history) — label is 4th positional
    assert m["apply_pins"].call_args.args[3] == main.PINNED_LABEL


def test_sigint_handler_stops_running():
    main._running = True
    main._handle_sigint(2, None)
    assert main._running is False


def test_interruptible_sleep_returns_early_when_stopped():
    main._running = False
    with patch("main.time.sleep") as sleep:
        main._interruptible_sleep(10)
    sleep.assert_not_called()


def test_interruptible_sleep_ticks_when_running():
    main._running = True
    calls = {"n": 0}

    def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] >= 3:
            main._running = False

    with patch("main.time.sleep", side_effect=fake_sleep):
        main._interruptible_sleep(1)
    assert calls["n"] == 3


def test_main_runs_one_cycle_then_exits():
    def stop_after(config):
        main._running = False
        return ["A"]

    with patch("main.load_config", return_value=make_config()) as load, \
         patch("main.run_cycle", side_effect=stop_after) as cycle, \
         patch("main._interruptible_sleep"), \
         patch("main.signal.signal"):
        rc = main.main(["config.yaml"])
    assert rc == 0
    load.assert_called_once()
    cycle.assert_called_once()


def test_main_reloads_config_each_cycle():
    seen = {"n": 0}

    def count_loads(path):
        seen["n"] += 1
        if seen["n"] >= 2:
            main._running = False
        return make_config()

    with patch("main.load_config", side_effect=count_loads), \
         patch("main.run_cycle", return_value=[]), \
         patch("main._interruptible_sleep"), \
         patch("main.signal.signal"):
        main.main([])
    assert seen["n"] == 2


def test_main_config_error_sleeps_and_retries_without_running_cycle():
    calls = {"n": 0}

    def raise_then_stop(path):
        calls["n"] += 1
        if calls["n"] >= 1:
            main._running = False
        raise ConfigError("bad config")

    with patch("main.load_config", side_effect=raise_then_stop), \
         patch("main.run_cycle") as cycle, \
         patch("main._interruptible_sleep") as sleep, \
         patch("main.signal.signal"):
        main.main([])
    cycle.assert_not_called()
    sleep.assert_called_once_with(main.CONFIG_ERROR_RETRY_MINUTES)


def test_main_cycle_error_does_not_crash_loop():
    def boom_then_stop(config):
        main._running = False
        raise RuntimeError("plex down")

    with patch("main.load_config", return_value=make_config()), \
         patch("main.run_cycle", side_effect=boom_then_stop), \
         patch("main._interruptible_sleep") as sleep, \
         patch("main.signal.signal"):
        rc = main.main([])
    assert rc == 0
    # still slept the normal interval after the failed cycle
    sleep.assert_called_once_with(30)
