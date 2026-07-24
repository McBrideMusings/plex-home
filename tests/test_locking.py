import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

from plex_home.locking import cycle_lock


def test_lock_file_is_created_and_kept(tmp_path):
    lock = tmp_path / "sub" / ".plex-home.lock"
    with cycle_lock(lock):
        assert lock.exists()
    # Kept after release — unlinking would let the next process lock a fresh
    # inode, which excludes nobody.
    assert lock.exists()


def test_lock_is_reentrant_across_sequential_uses(tmp_path):
    lock = tmp_path / ".plex-home.lock"
    for _ in range(3):
        with cycle_lock(lock):
            pass


def test_lock_releases_when_the_body_raises(tmp_path):
    lock = tmp_path / ".plex-home.lock"
    with pytest.raises(RuntimeError):
        with cycle_lock(lock):
            raise RuntimeError("cycle blew up")
    # A crashed cycle must not wedge the next one.
    with cycle_lock(lock):
        pass


def _hold_then_append(lock_path: str, out_path: str, hold: float, tag: str):
    from plex_home.locking import cycle_lock as inner_lock

    with inner_lock(Path(lock_path)):
        with open(out_path, "a") as f:
            f.write(f"{tag}-start\n")
        time.sleep(hold)
        with open(out_path, "a") as f:
            f.write(f"{tag}-end\n")


@pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX-only")
def test_second_process_waits_instead_of_interleaving(tmp_path):
    """The real reason the lock exists: two cycles must not overlap."""
    lock = tmp_path / ".plex-home.lock"
    out = tmp_path / "order.txt"

    ctx = multiprocessing.get_context("spawn")
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"

    a = ctx.Process(target=_hold_then_append, args=(str(lock), str(out), 0.4, "a"))
    a.start()
    time.sleep(0.1)
    b = ctx.Process(target=_hold_then_append, args=(str(lock), str(out), 0.0, "b"))
    b.start()
    a.join(10)
    b.join(10)

    lines = out.read_text().split()
    # b waited for a to finish rather than interleaving between its start and end.
    assert lines == ["a-start", "a-end", "b-start", "b-end"]


def test_open_does_not_treat_a_non_path_as_a_file_descriptor(tmp_path, capsys):
    """open() reads an __index__-able object as an fd; cycle_lock must coerce first."""
    class IndexablePath:
        def __init__(self, path):
            self._path = path

        def __fspath__(self):
            return str(self._path)

        def __index__(self):
            return 1  # stdout

    lock = IndexablePath(tmp_path / ".plex-home.lock")
    with cycle_lock(lock):
        print("stdout still works")
    assert (tmp_path / ".plex-home.lock").exists()
    assert "stdout still works" in capsys.readouterr().out
