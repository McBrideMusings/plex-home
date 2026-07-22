import json
from unittest.mock import patch, MagicMock
import pytest

from plex_home import cli
from plex_home.cli import build_parser
from plex_home.hubs import HubView, HubError


def make_cfg(libraries=("Movies",)):
    cfg = MagicMock()
    cfg.library_names = list(libraries)
    return cfg


# --- parser ---

def test_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_pin_to_and_to_top_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["pin", "X", "--to", "1", "--to-top"])


def test_move_requires_a_destination():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["move", "X"])


def test_list_flags_parse():
    args = build_parser().parse_args(["list", "--library", "Movies", "--available", "--json"])
    assert args.command == "list" and args.library == "Movies"
    assert args.available is True and args.as_json is True


# --- cmd_list ---

def test_cmd_list_table(capsys):
    views = [
        HubView("Movies", 0, "Recently Added", "system", False),
        HubView("Movies", 1, "Halloween", "collection", True),
        HubView("Movies", 2, "Rando", "collection", False),
    ]
    with patch("plex_home.cli.hubs.list_pinned", return_value={"Movies": views}):
        args = build_parser().parse_args(["list"])
        rc = cli.cmd_list(MagicMock(), make_cfg(), args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[0] Recently Added  (system)" in out
    assert "[1] Halloween  (collection, config)" in out
    assert "[2] Rando  (collection, manual)" in out


def test_cmd_list_json(capsys):
    views = [HubView("Movies", 0, "Rec", "system", False)]
    with patch("plex_home.cli.hubs.list_pinned", return_value={"Movies": views}):
        args = build_parser().parse_args(["list", "--json"])
        cli.cmd_list(MagicMock(), make_cfg(), args)
    data = json.loads(capsys.readouterr().out)
    assert data["Movies"]["pinned"][0] == {
        "index": 0, "title": "Rec", "kind": "system", "config_managed": False
    }


def test_cmd_list_available_included(capsys):
    with patch("plex_home.cli.hubs.list_pinned", return_value={"Movies": []}), \
         patch("plex_home.cli.hubs.list_available", return_value={"Movies": ["A24", "Ghibli"]}):
        args = build_parser().parse_args(["list", "--available"])
        cli.cmd_list(MagicMock(), make_cfg(), args)
    assert "available (2): A24, Ghibli" in capsys.readouterr().out


# --- cmd_pin ---

def test_cmd_pin_to_top_sets_index_zero():
    with patch("plex_home.cli._resolve_pin_library", return_value="Movies"), \
         patch("plex_home.cli.hubs.pin") as pin:
        args = build_parser().parse_args(["pin", "Halloween", "--to-top"])
        cli.cmd_pin(MagicMock(), make_cfg(), args)
    assert pin.call_args.kwargs["to_index"] == 0


def test_cmd_pin_native_placement_passes_none():
    with patch("plex_home.cli._resolve_pin_library", return_value="Movies"), \
         patch("plex_home.cli.hubs.pin") as pin:
        args = build_parser().parse_args(["pin", "Halloween"])
        cli.cmd_pin(MagicMock(), make_cfg(), args)
    assert pin.call_args.kwargs["to_index"] is None


# --- cmd_move relative resolution ---

@pytest.mark.parametrize("argv,expected", [
    (["move", "X", "--to", "1"], 1),
    (["move", "X", "--up"], 1),        # from 2, up 1
    (["move", "X", "--up", "2"], 0),   # 2 - 2
    (["move", "X", "--up", "5"], 0),   # clamped to 0
    (["move", "X", "--down"], 3),      # 2 + 1
    (["move", "X", "--down", "9"], 4), # clamped to count-1
    (["move", "X", "--to-top"], 0),
    (["move", "X", "--to-bottom"], 4), # count - 1
])
def test_cmd_move_computes_index(argv, expected):
    with patch("plex_home.cli._resolve_target_library", return_value="Movies"), \
         patch("plex_home.cli.hubs.locate", return_value=(2, 5)), \
         patch("plex_home.cli.hubs.move") as mv:
        args = build_parser().parse_args(argv)
        cli.cmd_move(MagicMock(), make_cfg(), args)
    assert mv.call_args.args[3] == expected


# --- library resolution ---

def test_index_target_without_library_raises():
    with pytest.raises(HubError):
        cli._resolve_target_library(MagicMock(), make_cfg(), "2", None)


def test_resolve_target_library_finds_by_title():
    def pinned(plex, name):
        return {"Halloween"} if name == "Movies" else {"Other"}

    with patch("plex_home.cli.hubs.pinned_titles", side_effect=pinned):
        lib = cli._resolve_target_library(MagicMock(), make_cfg(("Movies", "TV Shows")), "Halloween", None)
    assert lib == "Movies"


def test_resolve_target_library_ambiguous_raises():
    def pinned(plex, name):
        return {"Dup"}

    with patch("plex_home.cli.hubs.pinned_titles", side_effect=pinned):
        with pytest.raises(HubError):
            cli._resolve_target_library(MagicMock(), make_cfg(("Movies", "TV Shows")), "Dup", None)
