"""Cross-process lock so only one reconcile cycle runs at a time.

``run`` (the daemon) and ``once`` (a forced refresh) are separate processes that
share one ``pin_history.json``. A cycle is a read-modify-write of that file —
``load_history`` at the top, ``save_history`` at the bottom — so two overlapping
cycles would each read the same starting history and the second writer would
erase the first's updates, letting a collection re-pin inside its repeat block.
They would also both drive the Plex writers at once, racing on hub order.

An exclusive ``flock`` on a lock file beside the config makes the cycle atomic
against other processes using the same config directory. The wait is blocking on
purpose: a forced refresh that arrives mid-cycle should run *after* that cycle,
not be dropped, and a cycle is short enough that the daemon waiting on a manual
refresh is unnoticeable.
"""
from __future__ import annotations
import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("plex_home")


@contextmanager
def cycle_lock(lock_path: Path):
    """Hold an exclusive lock on ``lock_path`` for the duration of the block.

    The lock file is created if absent and never deleted — unlinking it would
    let a second process create a fresh inode and lock that instead, which locks
    nothing. The kernel drops the lock when the file object closes, including if
    the process is killed, so a crashed cycle cannot wedge the next one.
    """
    # Coerce first: open() accepts an integer as a *file descriptor*, so anything
    # that answers __index__ (a bare int, a test double) would silently lock an
    # already-open fd — stdout, say — instead of a file, and close it on exit.
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("Another cycle is in progress — waiting for it to finish")
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
