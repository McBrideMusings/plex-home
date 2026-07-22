import pytest
import tempfile
import os
import yaml
from plex_home.config import load_config, ConfigError, FixedSlot, PickSlot


def write_yaml(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(data, f)
    f.close()
    return f.name


BASE = {
    "plex_url": "http://localhost:32400",
    "plex_token": "TOKEN",
    "library_names": ["Movies", "TV Shows"],
    "cadence": {"interval_minutes": 60},
    "home": {"Movies": [{"collection": "Recently Added"}]},
    "groups": {
        "movies": {},
    },
}


def merge(base: dict, overrides: dict) -> dict:
    import copy
    result = copy.deepcopy(base)
    result.update(overrides)
    return result


def test_valid_config_loads():
    path = write_yaml(BASE)
    try:
        cfg = load_config(path)
        assert cfg.plex_url == "http://localhost:32400"
        assert cfg.plex_token == "TOKEN"
        assert cfg.library_names == ["Movies", "TV Shows"]
        assert cfg.cadence.interval_minutes == 60
        assert cfg.cadence.repeat_block_hours == 24.0
        assert cfg.cadence.min_items_for_pinning == 10
        assert cfg.cadence.mirror_recommended is False
        assert list(cfg.home) == ["Movies"]
        assert isinstance(cfg.home["Movies"][0], FixedSlot)
        assert cfg.home["Movies"][0].collection == "Recently Added"
        assert "movies" in cfg.groups
    finally:
        os.unlink(path)


def test_pick_slot_resolves_group_names():
    data = merge(BASE, {
        "home": {"Movies": [{"collection": "Fixed"}, {"pick": ["movies"]}]},
    })
    path = write_yaml(data)
    try:
        cfg = load_config(path)
        assert isinstance(cfg.home["Movies"][1], PickSlot)
        assert cfg.home["Movies"][1].groups == ["movies"]
    finally:
        os.unlink(path)


def test_unknown_group_in_pick_raises():
    data = merge(BASE, {"home": {"Movies": [{"pick": ["nonexistent"]}]}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="unknown group 'nonexistent'"):
            load_config(path)
    finally:
        os.unlink(path)


def test_home_library_not_in_library_names_raises():
    data = merge(BASE, {"home": {"Concerts": [{"collection": "Fixed"}]}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="not in library_names"):
            load_config(path)
    finally:
        os.unlink(path)


def test_group_reused_across_libraries_allowed():
    data = merge(BASE, {
        "home": {
            "Movies": [{"pick": ["shared"]}],
            "TV Shows": [{"pick": ["shared"]}],
        },
        "groups": {"shared": {"include_labels": ["Featured"]}},
    })
    path = write_yaml(data)
    try:
        cfg = load_config(path)
        assert cfg.home["Movies"][0].groups == ["shared"]
        assert cfg.home["TV Shows"][0].groups == ["shared"]
    finally:
        os.unlink(path)


def test_empty_home_library_slot_list_raises():
    data = merge(BASE, {"home": {"Movies": []}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="non-empty list of slots"):
            load_config(path)
    finally:
        os.unlink(path)


def test_unknown_group_key_raises():
    data = merge(BASE, {"groups": {"movies": {"typo_key": True}}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="unknown key"):
            load_config(path)
    finally:
        os.unlink(path)


def test_stray_library_key_on_group_raises():
    # library was removed from groups (ADR-0006) — a leftover must not be a silent no-op
    data = merge(BASE, {"groups": {"movies": {"library": "Movies"}}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="unknown key"):
            load_config(path)
    finally:
        os.unlink(path)


def test_missing_required_field_raises():
    for field in ["plex_url", "plex_token", "library_names", "cadence", "home", "groups"]:
        data = {k: v for k, v in BASE.items() if k != field}
        path = write_yaml(data)
        try:
            with pytest.raises(ConfigError, match=f"'{field}'"):
                load_config(path)
        finally:
            os.unlink(path)


def test_malformed_date_raises():
    data = merge(BASE, {"groups": {"movies": {"date": "13-01/13-31"}}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="MM-DD/MM-DD"):
            load_config(path)
    finally:
        os.unlink(path)


def test_valid_date_range_accepted():
    data = merge(BASE, {"groups": {"movies": {"date": "10-01/10-31"}}})
    path = write_yaml(data)
    try:
        cfg = load_config(path)
        assert cfg.groups["movies"].date == "10-01/10-31"
    finally:
        os.unlink(path)


def test_year_boundary_date_accepted():
    data = merge(BASE, {"groups": {"movies": {"date": "12-26/01-03"}}})
    path = write_yaml(data)
    try:
        cfg = load_config(path)
        assert cfg.groups["movies"].date == "12-26/01-03"
    finally:
        os.unlink(path)


def test_malformed_time_raises():
    data = merge(BASE, {"groups": {"movies": {"time": "25:00-26:00"}}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="HH:MM-HH:MM"):
            load_config(path)
    finally:
        os.unlink(path)


def test_valid_time_range_accepted():
    data = merge(BASE, {"groups": {"movies": {"time": "22:00-05:00"}}})
    path = write_yaml(data)
    try:
        cfg = load_config(path)
        assert cfg.groups["movies"].time == "22:00-05:00"
    finally:
        os.unlink(path)


def test_negative_repeat_block_hours_raises():
    data = merge(BASE, {"cadence": {"interval_minutes": 60, "repeat_block_hours": -1}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="repeat_block_hours"):
            load_config(path)
    finally:
        os.unlink(path)


def test_negative_min_items_raises():
    data = merge(BASE, {"cadence": {"interval_minutes": 60, "min_items_for_pinning": -5}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="min_items_for_pinning"):
            load_config(path)
    finally:
        os.unlink(path)


def test_mirror_recommended_parsed_true():
    data = merge(BASE, {"cadence": {"interval_minutes": 60, "mirror_recommended": True}})
    path = write_yaml(data)
    try:
        assert load_config(path).cadence.mirror_recommended is True
    finally:
        os.unlink(path)


def test_mirror_recommended_non_bool_raises():
    data = merge(BASE, {"cadence": {"interval_minutes": 60, "mirror_recommended": "yes"}})
    path = write_yaml(data)
    try:
        with pytest.raises(ConfigError, match="mirror_recommended"):
            load_config(path)
    finally:
        os.unlink(path)


def test_per_group_overrides_accepted():
    data = merge(BASE, {
        "groups": {
            "movies": {
                "repeat_block_hours": 0,
                "min_items_for_pinning": 5,
            }
        }
    })
    path = write_yaml(data)
    try:
        cfg = load_config(path)
        assert cfg.groups["movies"].repeat_block_hours == 0.0
        assert cfg.groups["movies"].min_items_for_pinning == 5
    finally:
        os.unlink(path)


def test_file_not_found_raises():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/tmp/does_not_exist_xyz.yaml")


def test_no_plex_connection_during_load():
    data = merge(BASE, {"plex_url": "http://0.0.0.0:99999", "plex_token": "bad"})
    path = write_yaml(data)
    try:
        cfg = load_config(path)
        assert cfg.plex_url == "http://0.0.0.0:99999"
    finally:
        os.unlink(path)
