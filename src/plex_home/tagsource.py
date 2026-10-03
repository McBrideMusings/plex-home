"""Read-only access to plex-db-ex's published snapshot — the tag and watch data
rule-set generation samples from.

plex-db-ex has one writer, and its SQLite schema is its public API. Consumers open
the single-file snapshot ``plexdb publish`` writes, never the live store, and never
write to it. This module is plex-home's only door into that file: everything that
needs tags, co-tags or watch history reads through a ``TagSource``.

Two schema facts shape the queries:

- Keywords live in ``enrichment`` under ``namespace = 'keywords'``, normalized and
  stemmed (``Heists`` is stored as ``heist``). More than one source may list the same
  keyword on the same title, so every rollup reads ``DISTINCT item_id, value``. A
  caller's spelling goes through the same normalization before it is matched.
- A play's ``item_id`` is an episode for TV, so play counts roll episodes up to their
  show through ``items.show_item_id``. ``seconds_watched`` is null on a Plex-only
  deployment, where Plex records a play only once it is (nearly) finished, so a null
  counts as a play and only a known short watch falls under the floor.
"""
from __future__ import annotations
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Sequence

import snowballstemmer

log = logging.getLogger("plex_home")

#: Columns this reader queries, per table. Checked once at open, so a snapshot
#: from an incompatible plex-db-ex (e.g. before ``plays.seconds_watched``) fails
#: with one clear message instead of a stray ``no such column`` mid-query.
REQUIRED_COLUMNS = {
    "items": {"item_id", "type", "title", "year", "show_item_id"},
    "plex_items": {"rating_key", "item_id", "section_id"},
    "enrichment": {"item_id", "namespace", "value"},
    "plays": {"item_id", "seconds_watched"},
    "tag_network_edge": {"kind", "a", "b", "shared"},
}

KINDS = ("movie", "show")

_stemmer = snowballstemmer.stemmer("english")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_keyword(surface: str) -> str:
    """The stored form of a raw keyword spelling — a copy of plex-db-ex's
    ``plexdb.keywords.normalize_keyword``, which defines what ``enrichment``
    holds: lowercase, trim, ``-``/``_`` become spaces, whitespace collapses, and
    each word is Snowball-English stemmed."""
    text = surface.strip().lower().replace("-", " ").replace("_", " ")
    text = _WHITESPACE_RUN.sub(" ", text).strip()
    if not text:
        return ""
    return " ".join(_stemmer.stemWords(text.split(" ")))


class TagSourceError(Exception):
    pass


@dataclass(frozen=True)
class Title:
    item_id: str
    title: str
    year: Optional[int]
    kind: str
    #: Every place Plex keeps this title, as ``(section_id, rating_key)`` pairs —
    #: one title can sit in two sections. Callers filter by section and label by
    #: rating key.
    plex_keys: tuple[tuple[str, str], ...]


class TagSource(Protocol):
    def titles_for_tags(self, tags: Sequence[str], kind: Optional[str] = None) -> list[Title]: ...
    def tags_for_title(self, item_id: str) -> list[str]: ...
    def co_tags(self, tag: str, kind: str) -> list[tuple[str, int]]: ...
    def play_counts(self, min_seconds: int = 0) -> dict[str, int]: ...


class SnapshotTagSource:
    """``TagSource`` over a plex-db-ex snapshot file, opened read-only."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise TagSourceError(f"plex-db-ex snapshot not found: {self.path}")
        # mode=ro: the connection cannot write, and works from a read-only mount.
        self._db: Optional[sqlite3.Connection] = None
        try:
            self._db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
            missing = self._missing_columns()
        except sqlite3.Error as e:
            if self._db is not None:
                self._db.close()
            raise TagSourceError(
                f"plex-db-ex snapshot unreadable: {self.path}: {e} "
                f"(point plexdb_snapshot at the file `plexdb publish` writes, not the live store)"
            )
        if missing:
            self._db.close()
            raise TagSourceError(
                f"plex-db-ex snapshot {self.path} is incompatible, missing: {', '.join(missing)}"
            )
        log.info("Opened plex-db-ex snapshot %s (read-only, %d required tables present)",
                 self.path, len(REQUIRED_COLUMNS))

    def _missing_columns(self) -> list[str]:
        missing = []
        for table, columns in REQUIRED_COLUMNS.items():
            present = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            if not present:
                missing.append(f"table {table}")
            else:
                missing += [f"{table}.{c}" for c in sorted(columns - present)]
        return missing

    def _query(self, sql: str, params: Sequence = ()) -> list[tuple]:
        try:
            return self._db.execute(sql, params).fetchall()
        except sqlite3.Error as e:
            raise TagSourceError(f"plex-db-ex snapshot query failed: {self.path}: {e}")

    def close(self) -> None:
        self._db.close()

    def resolve_tag(self, tag: str) -> str:
        """Map a caller's spelling to the stored keyword (``"Heists"`` → ``"heist"``).

        A tag already stored verbatim wins, since a stored value is already
        stemmed and stemming it again can change it. Anything else goes through
        ``normalize_keyword``, the same transform plex-db-ex applies on write.
        """
        if self._query(
            "SELECT 1 FROM enrichment WHERE namespace = 'keywords' AND value = ? LIMIT 1", (tag,)
        ):
            return tag
        return normalize_keyword(tag)

    def titles_for_tags(self, tags: Sequence[str], kind: Optional[str] = None) -> list[Title]:
        """Titles Plex holds that carry **every** tag in ``tags``, sorted by title."""
        if not tags:
            raise TagSourceError("titles_for_tags needs at least one tag")
        if kind is not None and kind not in KINDS:
            raise TagSourceError(f"kind must be one of {KINDS}, got {kind!r}")
        resolved = sorted({self.resolve_tag(t) for t in tags})
        marks = ",".join("?" * len(resolved))
        sql = f"""
            SELECT i.item_id, i.title, i.year, i.type, p.section_id, p.rating_key
            FROM items i
            JOIN plex_items p ON p.item_id = i.item_id
            WHERE i.item_id IN (
                SELECT item_id FROM (
                    SELECT DISTINCT item_id, value FROM enrichment
                    WHERE namespace = 'keywords' AND value IN ({marks})
                )
                GROUP BY item_id HAVING COUNT(*) = ?
            )
            {"AND i.type = ?" if kind else ""}
            ORDER BY i.title, i.item_id, p.section_id, p.rating_key
        """
        params: list = [*resolved, len(resolved)] + ([kind] if kind else [])
        titles: dict[str, Title] = {}
        for item_id, title, year, type_, section_id, rating_key in self._query(sql, params):
            prior = titles.get(item_id)
            keys = (prior.plex_keys if prior else ()) + ((section_id, rating_key),)
            titles[item_id] = Title(item_id, title, year, type_, keys)
        result = list(titles.values())
        log.info("tagsource titles_for_tags tags=%s kind=%s -> %d title(s)", resolved, kind, len(result))
        return result

    def tags_for_title(self, item_id: str) -> list[str]:
        rows = self._query(
            "SELECT DISTINCT value FROM enrichment WHERE namespace = 'keywords' AND item_id = ? ORDER BY value",
            (item_id,),
        )
        log.info("tagsource tags_for_title item_id=%s -> %d tag(s)", item_id, len(rows))
        return [r[0] for r in rows]

    def co_tags(self, tag: str, kind: str) -> list[tuple[str, int]]:
        """Tags most often carried alongside ``tag``, as ``(tag, shared_titles)``,
        strongest first. Read from plex-db-ex's precomputed ``tag_network_edge``, which
        keeps only each tag's strongest co-tags."""
        if kind not in KINDS:
            raise TagSourceError(f"kind must be one of {KINDS}, got {kind!r}")
        stored = self.resolve_tag(tag)
        rows = self._query(
            """
            SELECT CASE WHEN a = ? THEN b ELSE a END AS other, shared
            FROM tag_network_edge
            WHERE kind = ? AND (a = ? OR b = ?)
            ORDER BY shared DESC, other
            """,
            (stored, kind, stored, stored),
        )
        log.info("tagsource co_tags tag=%s kind=%s -> %d co-tag(s)", stored, kind, len(rows))
        return [(other, shared) for other, shared in rows]

    def play_counts(self, min_seconds: int = 0) -> dict[str, int]:
        """Plays per title (episodes rolled up to their show), keyed by ``item_id``.

        An episode whose show has no ``items`` row keeps its own id, so every key
        returned is a real ``items`` row.

        A play whose ``seconds_watched`` is known and below ``min_seconds`` is
        dropped; a null ``seconds_watched`` (Plex-only history) always counts.
        """
        if min_seconds < 0:
            raise TagSourceError(f"min_seconds must be >= 0, got {min_seconds}")
        rows = self._query(
            """
            SELECT COALESCE(s.item_id, p.item_id) AS title_id, COUNT(*)
            FROM plays p
            JOIN items i ON i.item_id = p.item_id
            LEFT JOIN items s ON s.item_id = i.show_item_id
            WHERE p.seconds_watched IS NULL OR p.seconds_watched >= ?
            GROUP BY title_id
            """,
            (min_seconds,),
        )
        counts = {title_id: n for title_id, n in rows}
        log.info("tagsource play_counts min_seconds=%d -> %d title(s), %d play(s)",
                 min_seconds, len(counts), sum(counts.values()))
        return counts

    def describe(self, item_ids: Sequence[str]) -> dict[str, tuple[str, Optional[int], str]]:
        """``item_id`` → ``(title, year, kind)`` for display."""
        if not item_ids:
            return {}
        marks = ",".join("?" * len(item_ids))
        rows = self._query(
            f"SELECT item_id, title, year, type FROM items WHERE item_id IN ({marks})", list(item_ids)
        )
        return {item_id: (title, year, type_) for item_id, title, year, type_ in rows}
