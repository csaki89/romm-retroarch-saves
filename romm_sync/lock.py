"""Single-instance lock: an exclusive flock on a PID file.

The kernel drops the lock when the process dies for any reason (crash,
SIGKILL), so a stale PID file never blocks a restart. The file is never
deleted, which avoids the classic unlink/create race.
"""
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows (development/tests only)
    fcntl = None
    import msvcrt


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self._fh = None

    def acquire(self):
        """True if we now hold the lock, False if another process does."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")  # no truncation before we own the lock
        try:
            self._lock(fh)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._fh = fh
        return True

    def release(self):
        if self._fh is None:
            return
        try:
            self._unlock(self._fh)
        finally:
            self._fh.close()
            self._fh = None

    def holder_pid(self):
        """PID recorded by the current holder, or None if unreadable."""
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _lock(fh):
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    @staticmethod
    def _unlock(fh):
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_UN)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
