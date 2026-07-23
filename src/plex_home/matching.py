"""Collection-title matching: exact, glob, or regex, with variable expansion.

A *title spec* is a config string that says how to match a collection title.
Bare strings are exact (the historical behaviour); a ``glob:`` or ``re:`` prefix
sigil opts into pattern matching. Before matching, ``{YEAR}``/``{MONTH}``/
``{WEEK}``/``{DAY}`` variables in the spec are expanded against the current time
(done once at config load — see ``config.py``), so an ``Oscars Death Race {YEAR}``
title resolves to the current year's collection each cycle.

The three modes all match the *whole* title: exact is string equality, glob is
``fnmatchcase`` (case-sensitive), regex is ``re.fullmatch``.
"""
from __future__ import annotations
import fnmatch
import re
from datetime import datetime

_GLOB_SIGIL = "glob:"
_REGEX_SIGIL = "re:"

# {TOKEN} -> strftime code, expanded against the current time at config load.
_VARIABLES = {
    "YEAR": "%Y",   # 2026
    "MONTH": "%m",  # 07 (zero-padded month)
    "WEEK": "%V",   # 30 (ISO week number, zero-padded)
    "DAY": "%d",    # 23 (zero-padded day of month)
}


def expand_variables(spec: str, now: datetime) -> str:
    """Replace ``{YEAR}``/``{MONTH}``/``{WEEK}``/``{DAY}`` with ``now``'s values."""
    for token, code in _VARIABLES.items():
        spec = spec.replace("{" + token + "}", now.strftime(code))
    return spec


def parse_spec(spec: str) -> tuple[str, str]:
    """Split a title spec into ``(mode, pattern)``.

    ``mode`` is ``"exact"``, ``"glob"``, or ``"regex"``; ``pattern`` is the spec
    with its sigil stripped. A bare spec (no sigil) is exact.
    """
    if spec.startswith(_GLOB_SIGIL):
        return "glob", spec[len(_GLOB_SIGIL):]
    if spec.startswith(_REGEX_SIGIL):
        return "regex", spec[len(_REGEX_SIGIL):]
    return "exact", spec


def title_matches(spec: str, title: str) -> bool:
    """Does ``title`` match ``spec``? Whole-title match in every mode.

    Callers pass specs sourced from config, whose ``re:`` patterns are validated
    to compile at config load, so this never raises on a well-formed config.
    """
    mode, pattern = parse_spec(spec)
    if mode == "exact":
        return title == pattern
    if mode == "glob":
        return fnmatch.fnmatchcase(title, pattern)
    return re.fullmatch(pattern, title) is not None
