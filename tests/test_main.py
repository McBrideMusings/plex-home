from datetime import datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
import pytest

from plex_home import main
from plex_home.config import ConfigError
from plex_home.resolver import ResolvedPin

NY = ZoneInfo("America/New_York")


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
    # A real tzinfo, not a mock: run_cycle and the scheduler both build a
    # datetime from it, which rejects anything that isn't a tzinfo subclass.
    cfg.cadence.timezone = NY
    return cfg


def patch_cycle_deps(resolved=None):
    """Patch every external call run_cycle makes; return the ExitStack-like dict of mocks."""
    resolved = resolved if resolved is not None else [ResolvedPin("Movies", "A"), ResolvedPin("Movies", "B")]
    pin_result = MagicMock()
    pin_result.history = {("Movies", "A"): "ts"}
    patches = {
        "connect": patch("plex_home.main.connect", return_value=MagicMock()),
        "fetch": patch("plex_home.main.fetch_collections", return_value={"Movies": []}),
        "load_history": patch("plex_home.main.load_history", return_value={}),
        "resolve": patch("plex_home.main.resolve_slots", return_value=resolved),
        "apply_pins": patch("plex_home.main.apply_pins", return_value=pin_result),
        "save_history": patch("plex_home.main.save_history"),
        "apply_order": patch("plex_home.main.apply_order"),
        "webhook": patch("plex_home.main.send_webhook", return_value=True),
    }
    started = {name: p.start() for name, p in patches.items()}
    return patches, started, pin_result


def test_run_cycle_wires_pipeline_in_order():
    resolved = [ResolvedPin("Movies", "A"), ResolvedPin("Movies", "B")]
    patches, m, pin_result = patch_cycle_deps(resolved=resolved)
    try:
        config = make_config(webhook_url="http://hook")
        result = main.run_cycle(config)
    finally:
        for p in patches.values():
            p.stop()
    assert result == resolved
    m["resolve"].assert_called_once()
    m["apply_pins"].assert_called_once()
    # history saved from the pin engine's returned history, to the path the
    # config resolved (beside the config file, never the working directory)
    m["save_history"].assert_called_once_with(pin_result.history, config.history_path)
    m["apply_order"].assert_called_once()
    m["webhook"].assert_called_once()
    assert m["webhook"].call_args.args[0] == "http://hook"
    # webhook receives plain titles, not the (library, title) pins
    assert m["webhook"].call_args.args[1] == ["A", "B"]


def test_run_cycle_skips_webhook_when_unconfigured():
    patches, m, _ = patch_cycle_deps()
    try:
        main.run_cycle(make_config(webhook_url=None))
    finally:
        for p in patches.values():
            p.stop()
    m["webhook"].assert_not_called()


def test_sigint_handler_stops_running():
    main._running = True
    main._handle_sigint(2, None)
    assert main._running is False


def test_interruptible_sleep_returns_early_when_stopped():
    main._running = False
    with patch("plex_home.main.time.sleep") as sleep:
        main._interruptible_sleep(10)
    sleep.assert_not_called()


def test_interruptible_sleep_ticks_when_running():
    main._running = True
    calls = {"n": 0}

    def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] >= 3:
            main._running = False

    with patch("plex_home.main.time.sleep", side_effect=fake_sleep):
        main._interruptible_sleep(60)
    assert calls["n"] == 3


def test_main_runs_one_cycle_then_exits():
    def stop_after(config):
        main._running = False
        return ["A"]

    with patch("plex_home.main.load_config", return_value=make_config()) as load, \
         patch("plex_home.main.run_cycle", side_effect=stop_after) as cycle, \
         patch("plex_home.main._interruptible_sleep"), \
         patch("plex_home.main.signal.signal"):
        rc = main.main(["run"])
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

    with patch("plex_home.main.load_config", side_effect=count_loads), \
         patch("plex_home.main.run_cycle", return_value=[]), \
         patch("plex_home.main._interruptible_sleep"), \
         patch("plex_home.main.signal.signal"):
        main.main(["run"])
    assert seen["n"] == 2


def test_main_config_error_sleeps_and_retries_without_running_cycle():
    calls = {"n": 0}

    def raise_then_stop(path):
        calls["n"] += 1
        if calls["n"] >= 1:
            main._running = False
        raise ConfigError("bad config")

    with patch("plex_home.main.load_config", side_effect=raise_then_stop), \
         patch("plex_home.main.run_cycle") as cycle, \
         patch("plex_home.main._interruptible_sleep") as sleep, \
         patch("plex_home.main.signal.signal"):
        main.main(["run"])
    cycle.assert_not_called()
    sleep.assert_called_once_with(main.CONFIG_ERROR_RETRY_MINUTES * 60)


def test_main_cycle_error_does_not_crash_loop():
    def boom_then_stop(config):
        main._running = False
        raise RuntimeError("plex down")

    with patch("plex_home.main.load_config", return_value=make_config()), \
         patch("plex_home.main.run_cycle", side_effect=boom_then_stop), \
         patch("plex_home.main._interruptible_sleep") as sleep, \
         patch("plex_home.main.signal.signal"):
        rc = main.main(["run"])
    assert rc == 0
    # still slept to the next cycle boundary after the failed cycle
    sleep.assert_called_once()
    waited = sleep.call_args.args[0]
    assert 0 < waited <= 30 * 60


def test_main_sleeps_to_the_next_wall_clock_boundary():
    """The wait is to the next fixed daily time, not a flat interval from now."""
    def stop_after(config):
        main._running = False
        return []

    # 01:10 local with a 3h interval → the 03:00 boundary, i.e. 110 minutes.
    frozen = datetime(2026, 7, 24, 1, 10, tzinfo=NY)
    with patch("plex_home.main.load_config", return_value=make_config(interval=180)), \
         patch("plex_home.main.run_cycle", side_effect=stop_after), \
         patch("plex_home.main.datetime") as dt, \
         patch("plex_home.main._interruptible_sleep") as sleep, \
         patch("plex_home.main.signal.signal"):
        dt.now.return_value = frozen
        main.main(["run"])
    sleep.assert_called_once_with(110 * 60)


def test_once_runs_a_single_cycle_and_exits():
    with patch("plex_home.main.load_config", return_value=make_config()) as load, \
         patch("plex_home.main.run_cycle", return_value=["A"]) as cycle:
        rc = main.main(["once"])
    assert rc == 0
    load.assert_called_once()
    cycle.assert_called_once()


def test_once_returns_2_on_config_error():
    with patch("plex_home.main.load_config", side_effect=ConfigError("bad")), \
         patch("plex_home.main.run_cycle") as cycle:
        rc = main.main(["once"])
    assert rc == 2
    cycle.assert_not_called()


def test_once_returns_1_when_the_cycle_fails():
    with patch("plex_home.main.load_config", return_value=make_config()), \
         patch("plex_home.main.run_cycle", side_effect=RuntimeError("plex down")):
        rc = main.main(["once"])
    assert rc == 1


def test_main_dispatches_subcommand_to_handler():
    handler = MagicMock(return_value=0)
    with patch("plex_home.main.load_config", return_value=make_config()), \
         patch("plex_home.main.connect", return_value=MagicMock()), \
         patch("plex_home.cli.HANDLERS", {"list": handler}):
        rc = main.main(["list"])
    assert rc == 0
    handler.assert_called_once()


def test_main_command_config_error_returns_2():
    with patch("plex_home.main.load_config", side_effect=ConfigError("bad")):
        rc = main.main(["list"])
    assert rc == 2


def test_main_command_connect_error_returns_2():
    with patch("plex_home.main.load_config", return_value=make_config()), \
         patch("plex_home.main.connect", side_effect=RuntimeError("no plex")):
        rc = main.main(["list"])
    assert rc == 2


def test_main_command_hub_error_returns_1():
    from plex_home.hubs import HubError
    with patch("plex_home.main.load_config", return_value=make_config()), \
         patch("plex_home.main.connect", return_value=MagicMock()), \
         patch("plex_home.cli.HANDLERS", {"list": MagicMock(side_effect=HubError("boom"))}):
        rc = main.main(["list"])
    assert rc == 1
