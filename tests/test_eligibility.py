import pytest
from datetime import datetime, timezone
from plex_home.config import Group
from plex_home.plex_client import CollectionInfo
from plex_home import eligibility as e


def dt(month, day, hour=12, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def group(**kwargs) -> Group:
    defaults = dict(name="test", library="Movies")
    defaults.update(kwargs)
    return Group(**defaults)


def coll(title: str, item_count: int = 20, labels: list[str] | None = None) -> CollectionInfo:
    return CollectionInfo(title=title, item_count=item_count, labels=labels or [])


ALL = {"Movies": [coll("Halloween", 30, ["halloween"]), coll("Comedy", 25), coll("Thin", 3)]}


def test_date_ineligible_outside_range():
    g = group(date="10-01/10-31")
    assert not e.is_group_eligible(g, dt(11, 1))


def test_date_eligible_inside_range():
    g = group(date="10-01/10-31")
    assert e.is_group_eligible(g, dt(10, 15))


def test_date_boundary_start():
    g = group(date="10-01/10-31")
    assert e.is_group_eligible(g, dt(10, 1))


def test_date_boundary_end():
    g = group(date="10-01/10-31")
    assert e.is_group_eligible(g, dt(10, 31))


def test_date_year_boundary_dec31():
    g = group(date="12-26/01-03")
    assert e.is_group_eligible(g, dt(12, 31))


def test_date_year_boundary_jan2():
    g = group(date="12-26/01-03")
    assert e.is_group_eligible(g, dt(1, 2))


def test_date_year_boundary_outside():
    g = group(date="12-26/01-03")
    assert not e.is_group_eligible(g, dt(6, 15))


def test_time_eligible_inside():
    g = group(time="22:00-05:00")
    assert e.is_group_eligible(g, dt(1, 1, 23, 0))


def test_time_eligible_early_morning():
    g = group(time="22:00-05:00")
    assert e.is_group_eligible(g, dt(1, 1, 3, 0))


def test_time_ineligible_afternoon():
    g = group(time="22:00-05:00")
    assert not e.is_group_eligible(g, dt(1, 1, 14, 0))


def test_no_constraints_always_eligible():
    g = group()
    assert e.is_group_eligible(g, dt(6, 15, 14, 0))


def test_include_labels_union():
    g = group(include_labels=["halloween"])
    colls = [coll("Halloween", 30, ["halloween"]), coll("Comedy", 25)]
    result = e.filter_collections(g, colls, global_min_items=0)
    assert len(result) == 1
    assert result[0].title == "Halloween"


def test_include_collections_union():
    g = group(include_collections=["Comedy"])
    colls = [coll("Halloween", 30, ["halloween"]), coll("Comedy", 25)]
    result = e.filter_collections(g, colls, global_min_items=0)
    assert len(result) == 1
    assert result[0].title == "Comedy"


def test_include_labels_and_collections_union():
    g = group(include_labels=["halloween"], include_collections=["Comedy"])
    colls = [coll("Halloween", 30, ["halloween"]), coll("Comedy", 25), coll("Action", 20)]
    result = e.filter_collections(g, colls, global_min_items=0)
    titles = {c.title for c in result}
    assert titles == {"Halloween", "Comedy"}


def test_no_include_filters_all_pass():
    g = group()
    colls = [coll("A", 20), coll("B", 20)]
    result = e.filter_collections(g, colls, global_min_items=0)
    assert len(result) == 2


def test_exclude_labels_removes():
    g = group(exclude_labels=["adult"])
    colls = [coll("Naughty", 20, ["adult"]), coll("Comedy", 20)]
    result = e.filter_collections(g, colls, global_min_items=0)
    assert len(result) == 1
    assert result[0].title == "Comedy"


def test_exclude_collections_removes():
    g = group(exclude_collections=["Yellowstone"])
    colls = [coll("Yellowstone", 20), coll("Breaking Bad", 20)]
    result = e.filter_collections(g, colls, global_min_items=0)
    assert len(result) == 1
    assert result[0].title == "Breaking Bad"


def test_min_items_global():
    g = group()
    colls = [coll("Big", 20), coll("Thin", 3)]
    result = e.filter_collections(g, colls, global_min_items=10)
    assert len(result) == 1
    assert result[0].title == "Big"


def test_min_items_group_override():
    g = group(min_items_for_pinning=2)
    colls = [coll("Big", 20), coll("Thin", 3)]
    result = e.filter_collections(g, colls, global_min_items=10)
    assert len(result) == 2


def test_eligible_returns_none_when_ineligible():
    g = group(date="10-01/10-31")
    result = e.eligible_collections(g, ALL, global_min_items=0, now=dt(11, 1))
    assert result is None


def test_eligible_returns_empty_list_when_no_matches():
    g = group(include_labels=["nonexistent"])
    result = e.eligible_collections(g, ALL, global_min_items=0, now=dt(6, 1))
    assert result == []


def test_eligible_uses_correct_library():
    libs = {
        "Movies": [coll("Movie A", 20)],
        "TV Shows": [coll("Show A", 20)],
    }
    g = group(library="TV Shows")
    result = e.eligible_collections(g, libs, global_min_items=0, now=dt(6, 1))
    assert len(result) == 1
    assert result[0].title == "Show A"
