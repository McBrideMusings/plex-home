from __future__ import annotations
import logging
from dataclasses import dataclass, field

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound, Unauthorized
import requests.exceptions

log = logging.getLogger(__name__)


@dataclass
class CollectionInfo:
    title: str
    item_count: int
    labels: list[str] = field(default_factory=list)


def connect(plex_url: str, plex_token: str) -> PlexServer:
    try:
        return PlexServer(plex_url, plex_token)
    except Unauthorized:
        raise ConnectionError(f"Plex auth failed — check plex_token (url: {plex_url})")
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(f"Could not connect to Plex at {plex_url}: {e}")
    except Exception as e:
        raise ConnectionError(f"Plex connection error ({type(e).__name__}): {e}")


def fetch_collections(plex: PlexServer, library_names: list[str]) -> dict[str, list[CollectionInfo]]:
    result: dict[str, list[CollectionInfo]] = {}
    for name in library_names:
        try:
            section = plex.library.section(name)
        except NotFound:
            log.warning("Library %r not found in Plex — skipping", name)
            continue
        except Exception as e:
            log.warning("Could not fetch library %r: %s — skipping", name, e)
            continue

        infos: list[CollectionInfo] = []
        for coll in section.collections():
            try:
                labels = [label.tag for label in (coll.labels or [])]
                infos.append(CollectionInfo(
                    title=coll.title,
                    item_count=coll.childCount,
                    labels=labels,
                ))
            except Exception as e:
                log.warning("Skipping collection in %r due to error: %s", name, e)
        result[name] = infos
        log.info("Fetched %d collections from library %r", len(infos), name)
    return result
