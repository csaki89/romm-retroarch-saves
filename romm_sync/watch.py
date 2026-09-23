"""`romm-sync watch`: run a full sync only while RetroArch holds no content.

RetroArch keeps the battery save in memory while a game is loaded and writes
it back on unload; a download during that time would be overwritten. The
RetroArch UDP command interface (GET_STATUS) tells us whether content is
loaded, so syncs are triggered only from states with nothing in memory.
"""
import enum
import logging
import re
import socket
import threading
import time
from pathlib import Path

log = logging.getLogger("romm_sync.watch")

LOOPBACK = ("127.0.0.1", "localhost", "::1")
RETRY_DELAY = 60.0  # seconds before retrying a sync that crashed


class State(enum.Enum):
    DOWN = "down"          # RetroArch not answering
    IDLE = "idle"          # answering, no content loaded (CONTENTLESS)
    LOADED = "loaded"      # content loaded (PLAYING / PAUSED): NEVER sync


class UnknownStatus(str):
    """"UNKNOWN" that remembers the raw reply, for diagnostics."""

    def __new__(cls, raw):
        obj = super().__new__(cls, "UNKNOWN")
        obj.raw = raw
        return obj


def parse_status(text):
    """GET_STATUS reply -> "CONTENTLESS" | "PLAYING" | "PAUSED" | "UNKNOWN",
    or None when it is not a usable reply."""
    tokens = text.strip().split(None, 2)
    if len(tokens) < 2 or tokens[0].upper() != "GET_STATUS":
        return None
    word = tokens[1].upper()
    if word == "N/A":
        return None
    if word in ("CONTENTLESS", "PLAYING", "PAUSED"):
        return word
    return UnknownStatus(text.strip())  # treated like loaded content (safe side)


def query_status(host, port, timeout=0.5):
    """One GET_STATUS round trip on a fresh socket; None on no/invalid answer."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(b"GET_STATUS", (host, port))
            data, _ = sock.recvfrom(4096)
    except OSError:  # includes timeout and ICMP "connection refused"
        return None
    return parse_status(data.decode("utf-8", errors="replace"))


# --- retroarch.cfg check ---------------------------------------------------

def find_retroarch_cfg(watch_cfg, saves_dir):
    if watch_cfg.retroarch_cfg:
        return watch_cfg.retroarch_cfg if watch_cfg.retroarch_cfg.is_file() else None
    candidates = [
        Path(saves_dir).parent / "retroarch.cfg",
        Path("~/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg").expanduser(),
        Path("~/.config/retroarch/retroarch.cfg").expanduser(),
    ]
    return next((c for c in candidates if c.is_file()), None)


def _cfg_value(text, key):
    matches = re.findall(
        rf'^\s*{re.escape(key)}\s*=\s*"?([^"\r\n]*?)"?\s*$', text, re.MULTILINE
    )
    return matches[-1] if matches else None


def check_network_commands(cfg_path, port):
    """Return an error message, or None if the UDP command interface is on."""
    text = Path(cfg_path).read_text(errors="replace")
    enable = _cfg_value(text, "network_cmd_enable")
    if enable is None or enable.strip().lower() != "true":
        return (
            f'RetroArch network commands are disabled: {cfg_path} has '
            f'network_cmd_enable = {enable!r}. Set network_cmd_enable = "true" '
            "(Settings > Network > Network Commands) and restart RetroArch. "
            "Edit the file only while RetroArch is closed: it rewrites its "
            "config on exit."
        )
    cfg_port = _cfg_value(text, "network_cmd_port")
    if cfg_port and cfg_port.strip() != str(port):
        return (
            f"{cfg_path} has network_cmd_port = {cfg_port!r} but [watch] "
            f"udp_port is {port}; make them match."
        )
    return None


# --- state machine ---------------------------------------------------------

class StateMachine:
    """Pure decision logic: feed it one poll result at a time.

    observe() returns the reason a sync should start now, or None.

    Sync triggers (nothing is held in memory in any of them):
      retroarch-started  no answer -> answer (and no content loaded yet)
      retroarch-stopped  answering -> no answer for `failure_threshold` polls
      content-unloaded   PLAYING/PAUSED -> CONTENTLESS
      idle-interval      CONTENTLESS for `idle_interval` seconds since last sync
      startup-idle       first poll after `watch` starts finds CONTENTLESS
    Never while content is loaded, and not on CONTENTLESS -> PLAYING.
    """

    def __init__(self, failure_threshold=3, idle_interval=900.0,
                 unknown_warn_interval=900.0):
        self.failure_threshold = failure_threshold
        self.idle_interval = idle_interval
        self.unknown_warn_interval = unknown_warn_interval
        self.state = None  # unknown until the first answer / confirmed outage
        self.fails = 0
        self.last_sync = None
        self._unknown_warned_at = None

    def _note_unknown(self, status, now):
        if status != "UNKNOWN":
            self._unknown_warned_at = None
            return
        last = self._unknown_warned_at
        if last is None or now - last >= self.unknown_warn_interval:
            self._unknown_warned_at = now
            log.warning(
                "RetroArch sent an unrecognized GET_STATUS reply: %r. Treating it "
                "as content loaded, so syncs stay blocked until a known status "
                "(CONTENTLESS/PLAYING/PAUSED) arrives.",
                getattr(status, "raw", "<raw reply unavailable>"),
            )

    def observe(self, status, now):
        if status is None:
            return self._no_answer()
        self.fails = 0
        self._note_unknown(status, now)
        prev = self.state
        self.state = State.IDLE if status == "CONTENTLESS" else State.LOADED

        if self.state is State.LOADED:
            if prev is State.IDLE:
                log.info("Content loaded (%s): sync blocked while playing", status)
            elif prev is State.DOWN:
                log.info("RetroArch started with content (%s): sync blocked", status)
            elif prev is None:
                log.info("RetroArch has content loaded (%s): sync blocked", status)
            return None

        if prev is State.LOADED:
            return self._fire("content-unloaded", now)
        if prev is State.DOWN:
            return self._fire("retroarch-started", now)
        if prev is None:
            return self._fire("startup-idle", now)
        if (
            self.last_sync is None
            or now - self.last_sync >= self.idle_interval
        ):
            return self._fire("idle-interval", now)
        return None

    def _no_answer(self):
        self.fails += 1
        if self.fails < self.failure_threshold or self.state is State.DOWN:
            return None
        prev, self.state = self.state, State.DOWN
        if prev is None:
            log.info("RetroArch is not running (no answer to GET_STATUS)")
            return None
        log.info("RetroArch stopped (%d polls without answer)", self.fails)
        return self._fire("retroarch-stopped", None)

    def _fire(self, reason, now):
        if now is not None:
            self.last_sync = now
        return reason

    def mark_synced(self, now):
        """A sync actually ran (even if it reported errors)."""
        self.last_sync = now

    def restore(self, last_sync):
        """A triggered sync never started: undo the clock reset of the trigger."""
        self.last_sync = last_sync

    def retry_soon(self, now, delay=RETRY_DELAY):
        """The sync crashed: due again after `delay`s, not a whole interval."""
        self.last_sync = now - self.idle_interval + delay


# --- watcher -----------------------------------------------------------------

class Watcher:
    def __init__(self, watch_cfg, sync_fn, *, query=None, clock=time.monotonic):
        self.cfg = watch_cfg
        self.sync_fn = sync_fn
        self.clock = clock
        self.query = query or (
            lambda: query_status(watch_cfg.udp_host, watch_cfg.udp_port)
        )
        self.machine = StateMachine(
            watch_cfg.failure_threshold, watch_cfg.idle_sync_interval
        )
        self.stop_event = threading.Event()
        self._sync_lock = threading.Lock()
        self._worker = None

    def tick(self):
        """One poll + decision. Never blocks on a running sync."""
        clock_before = self.machine.last_sync
        reason = self.machine.observe(self.query(), self.clock())
        if reason:
            self._trigger(reason, clock_before)

    def run(self):
        log.info(
            "Watching RetroArch at %s:%d (poll %.1fs, idle sync every %.0fs, "
            "stop after %d missed polls)",
            self.cfg.udp_host, self.cfg.udp_port, self.cfg.poll_interval,
            self.cfg.idle_sync_interval, self.cfg.failure_threshold,
        )
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Watch loop error")
            self.stop_event.wait(self.cfg.poll_interval)
        self.wait_for_sync()
        log.info("Watch stopped")

    def stop(self):
        self.stop_event.set()

    def wait_for_sync(self):
        worker = self._worker
        if worker and worker.is_alive():
            log.info("Waiting for the running sync to finish...")
            worker.join()

    def _trigger(self, reason, clock_before):
        if not self._sync_lock.acquire(blocking=False):
            log.warning("Sync trigger %r dropped: a sync is still running", reason)
            return
        log.info("Sync triggered by: %s", reason)
        self._worker = threading.Thread(
            target=self._sync_worker,
            args=(reason, clock_before),
            name="romm-sync",
            daemon=False,
        )
        self._worker.start()

    def _sync_worker(self, reason, clock_before):
        try:
            # Re-check right before starting: content may have loaded since.
            status = self.query()
            if status not in (None, "CONTENTLESS"):
                log.warning(
                    "Sync (%s) skipped: RetroArch reports %s just before start",
                    reason, status,
                )
                self.machine.restore(clock_before)  # no sync happened: keep the clock
                return
            try:
                rc = self.sync_fn()
            except Exception:
                log.exception("Sync (%s) crashed", reason)
                self.machine.retry_soon(self.clock())
                return
            self.machine.mark_synced(self.clock())
            log.info("Sync (%s) finished %s", reason, "OK" if rc == 0 else "WITH ERRORS")
        finally:
            self._sync_lock.release()
