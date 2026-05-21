import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from plexapi.exceptions import NotFound, Unauthorized
import requests.exceptions

import plex_client as pc


def make_label(tag: str) -> MagicMock:
    lbl = MagicMock()
    lbl.tag = tag
    return lbl


def make_collection(title: str, child_count: int, labels: list[str]) -> MagicMock:
    coll = MagicMock()
    coll.title = title
    coll.childCount = child_count
    coll.labels = [make_label(t) for t in labels]
    return coll


def make_section(collections: list[MagicMock]) -> MagicMock:
    section = MagicMock()
    section.collections.return_value = collections
    return section


def make_plex(sections: dict[str, MagicMock]) -> MagicMock:
    plex = MagicMock()
    def get_section(name):
        if name not in sections:
            raise NotFound(name)
        return sections[name]
    plex.library.section.side_effect = get_section
    return plex


def test_fetch_returns_dict_keyed_by_library():
    colls = [make_collection("Halloween Movies", 20, ["halloween"])]
    plex = make_plex({"Movies": make_section(colls)})
    result = pc.fetch_collections(plex, ["Movies"])
    assert "Movies" in result
    assert len(result["Movies"]) == 1


def test_collection_info_fields():
    colls = [make_collection("Spooky Picks", 15, ["halloween", "horror"])]
    plex = make_plex({"Movies": make_section(colls)})
    result = pc.fetch_collections(plex, ["Movies"])
    info = result["Movies"][0]
    assert info.title == "Spooky Picks"
    assert info.item_count == 15
    assert info.labels == ["halloween", "horror"]


def test_missing_library_skipped_not_crashed():
    plex = make_plex({})
    result = pc.fetch_collections(plex, ["NonExistent"])
    assert "NonExistent" not in result


def test_multiple_libraries():
    movies = make_section([make_collection("Action Films", 30, [])])
    tv = make_section([make_collection("Crime TV", 12, []), make_collection("Sci-Fi TV", 8, [])])
    plex = make_plex({"Movies": movies, "TV Shows": tv})
    result = pc.fetch_collections(plex, ["Movies", "TV Shows"])
    assert len(result["Movies"]) == 1
    assert len(result["TV Shows"]) == 2


def test_empty_labels_list():
    colls = [make_collection("No Labels", 5, [])]
    plex = make_plex({"Movies": make_section(colls)})
    result = pc.fetch_collections(plex, ["Movies"])
    assert result["Movies"][0].labels == []


def test_connect_raises_on_unauthorized():
    with patch("plex_client.PlexServer", side_effect=Unauthorized("bad token")):
        with pytest.raises(ConnectionError, match="auth failed"):
            pc.connect("http://localhost:32400", "badtoken")


def test_connect_raises_on_connection_error():
    with patch("plex_client.PlexServer", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(ConnectionError, match="Could not connect"):
            pc.connect("http://0.0.0.0:99", "token")


def test_connect_returns_server_on_success():
    mock_server = MagicMock()
    with patch("plex_client.PlexServer", return_value=mock_server):
        result = pc.connect("http://localhost:32400", "validtoken")
    assert result is mock_server


def test_none_labels_handled():
    coll = make_collection("No Labels", 10, [])
    coll.labels = None
    plex = make_plex({"Movies": make_section([coll])})
    result = pc.fetch_collections(plex, ["Movies"])
    assert result["Movies"][0].labels == []
