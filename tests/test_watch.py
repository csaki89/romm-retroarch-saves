import socket
import threading

import pytest

from romm_sync import config as cfgmod
from romm_sync.config import WatchConfig
from romm_sync.watch import (
    State,
    StateMachine,
    Watcher,
    check_network_commands,
    parse_status,
    query_status,
)

C, P, PA = "CONTENTLESS", "PLAYING", "PAUSED"


def feed(machine, seq, start=0.0, step=1.0):
    """Feed statuses one second apart; return the list of triggers."""
    out, now = [], start
    for status in seq:
        out.append(machine.observe(status, now))
        now += step
    return out


# --- parsing / UDP ------------------------------------------------------------

def test_parse_status():
    assert parse_status("GET_STATUS CONTENTLESS") == C
    assert parse_status("GET_STATUS PLAYING snes,Game,crc32=1234abcd\n") == P
    assert parse_status("GET_STATUS PAUSED snes,Game") == PA
    assert parse_status("GET_STATUS SOMETHING") == "UNKNOWN"
    assert parse_status("GET_STATUS N/A") is None
    assert parse_status("") is None
    assert parse_status("garbage") is None


def _udp_server(reply):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))

    def serve():
        data, addr = sock.recvfrom(64)
        assert data == b"GET_STATUS"
        if reply is not None:
            sock.sendto(reply, addr)
        sock.close()

    threading.Thread(target=serve, daemon=True).start()
    return sock.getsockname()[1]


def test_query_status_over_udp():
    port = _udp_server(b"GET_STATUS PLAYING snes,Game,crc32=00000000")
    assert query_status("127.0.0.1", port, timeout=2) == P


def test_query_status_timeout_is_none():
    port = _udp_server(None)
    assert query_status("127.0.0.1", port, timeout=0.2) is None


# --- state machine: every transition ---------------------------------------

def test_startup_contentless_syncs_once_then_idle_interval():
    m = StateMachine(3, idle_interval=10)
    out = feed(m, [C] * 12)
    assert out[0] == "startup-idle"
    assert out[1:10] == [None] * 9
    assert out[10] == "idle-interval"     # 10s after the first sync
    assert out[11] is None


def test_startup_no_answer_no_sync_then_started():
    m = StateMachine(3, idle_interval=900)
    assert feed(m, [None, None, None, None]) == [None] * 4
    assert m.state.name == "DOWN"
    assert m.observe(C, 10) == "retroarch-started"          # 1: no answer -> answer


def test_started_with_content_is_blocked():
    m = StateMachine(3, 900)
    feed(m, [None] * 3)
    assert m.observe(P, 5) is None                            # PLAYING: never
    assert m.observe(C, 6) == "content-unloaded"              # 3: leaving the game


def test_stopped_only_after_threshold():
    m = StateMachine(3, 900)
    feed(m, [C])                                              # running, idle
    assert feed(m, [None, None], start=1) == [None, None]     # ambiguous
    assert m.observe(None, 3) == "retroarch-stopped"          # 2: third miss
    assert m.observe(None, 4) is None                         # only once
    assert m.observe(None, 5) is None


def test_single_lost_packet_is_not_a_stop():
    m = StateMachine(3, 900)
    feed(m, [C])
    assert feed(m, [None, None], start=1) == [None, None]
    assert m.observe(C, 3) is None                            # counter reset
    assert feed(m, [None, None], start=4) == [None, None]     # needs 3 again
    assert m.state.name == "IDLE"


def test_stop_while_playing_syncs_after_threshold():
    m = StateMachine(2, 900)
    feed(m, [P])
    assert m.observe(None, 1) is None
    assert m.observe(None, 2) == "retroarch-stopped"


def test_playing_to_contentless_is_immediate():
    m = StateMachine(3, 900)
    out = feed(m, [C, P, P, C])
    assert out == ["startup-idle", None, None, "content-unloaded"]


def test_paused_counts_as_loaded():
    m = StateMachine(3, 900)
    assert feed(m, [P, PA, PA, C]) == [None, None, None, "content-unloaded"]


def test_contentless_to_playing_never_syncs():
    m = StateMachine(3, idle_interval=1)
    out = feed(m, [C, P, P, P], step=100)                    # interval long past
    assert out[1:] == [None, None, None]


def test_no_idle_sync_while_playing_even_after_interval():
    m = StateMachine(3, idle_interval=10)
    feed(m, [C])
    assert feed(m, [P] * 50, start=1) == [None] * 50


def test_idle_sync_not_during_ambiguous_misses():
    m = StateMachine(3, idle_interval=5)
    feed(m, [C])
    assert m.observe(None, 100) is None                       # possible lost packet


def test_unknown_status_is_treated_as_loaded():
    m = StateMachine(3, 900)
    assert feed(m, ["UNKNOWN", C]) == [None, "content-unloaded"]


def test_mark_synced_resets_idle_timer():
    m = StateMachine(3, idle_interval=10)
    feed(m, [C])
    m.mark_synced(8.0)
    assert m.observe(C, 12.0) is None
    assert m.observe(C, 18.0) == "idle-interval"


# --- watcher: single-flight, guard, shutdown --------------------------------

def make_watcher(statuses, sync_fn, clock):
    cfg = WatchConfig(True, 1, 900, 3, "127.0.0.1", 55355, None)
    it = iter(statuses)
    return Watcher(cfg, sync_fn, query=lambda: next(it), clock=clock)


def test_watcher_runs_sync_for_trigger():
    calls = []
    w = make_watcher([C, C], lambda: calls.append(1) or 0, lambda: 0.0)
    w.tick()          # poll -> startup-idle; worker re-checks with 2nd status
    w.wait_for_sync()
    assert calls == [1]


def test_watcher_guard_skips_when_content_loaded_before_start():
    calls = []
    w = make_watcher([C, P], lambda: calls.append(1) or 0, lambda: 0.0)
    w.tick()
    w.wait_for_sync()
    assert calls == []


def test_second_trigger_dropped_while_sync_running():
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_sync():
        calls.append(1)
        started.set()
        release.wait(5)
        return 0

    # startup-idle, guard re-check, then playing->idle again while sync runs
    w = make_watcher([C, C, P, C], slow_sync, lambda: 0.0)
    w.tick()
    assert started.wait(5)
    w.tick()                       # PLAYING
    w.tick()                       # CONTENTLESS -> content-unloaded, dropped
    release.set()
    w.wait_for_sync()
    assert calls == [1]


def test_sync_exception_releases_lock_for_next_trigger():
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("x")

    w = make_watcher([C, C, P, C, C], boom, lambda: 0.0)
    w.tick()
    w.wait_for_sync()
    w.tick()
    w.tick()
    w.wait_for_sync()
    assert calls == [1, 1]


def test_run_stops_on_signal_and_waits_for_sync():
    release = threading.Event()
    done = []

    def sync():
        release.wait(5)
        done.append(1)
        return 0

    cfg = WatchConfig(True, 0.1, 900, 3, "127.0.0.1", 55355, None)
    w = Watcher(cfg, sync, query=lambda: C, clock=lambda: 0.0)
    t = threading.Thread(target=w.run)
    t.start()
    while w._worker is None:
        pass
    w.stop()
    release.set()
    t.join(5)
    assert not t.is_alive() and done == [1]


# --- retroarch.cfg check ------------------------------------------------------

def test_check_network_commands(tmp_path):
    f = tmp_path / "retroarch.cfg"
    f.write_text('foo = "1"\nnetwork_cmd_enable = "true"\nnetwork_cmd_port = "55355"\n')
    assert check_network_commands(f, 55355) is None
    assert "network_cmd_port" in check_network_commands(f, 4000)

    f.write_text('network_cmd_enable = "false"\n')
    assert "network_cmd_enable" in check_network_commands(f, 55355)

    f.write_text('video_driver = "gl"\n')
    assert "disabled" in check_network_commands(f, 55355)


# --- config -------------------------------------------------------------------

def _write_cfg(tmp_path, extra=""):
    p = tmp_path / "c.ini"
    p.write_text("[romm]\nurl = http://x\ntoken = t\n" + extra)
    return p


def test_watch_config_defaults_and_overrides(tmp_path):
    w = cfgmod.load_config(_write_cfg(tmp_path)).watch
    assert (w.enabled, w.poll_interval, w.idle_sync_interval) == (True, 1.0, 900.0)
    assert (w.failure_threshold, w.udp_host, w.udp_port) == (3, "127.0.0.1", 55355)

    w = cfgmod.load_config(_write_cfg(tmp_path, (
        "[watch]\nenabled = false\npoll_interval = 2\nidle_sync_interval = 60\n"
        "failure_threshold = 5\nudp_host = 10.0.0.2\nudp_port = 4000\n"))).watch
    assert (w.enabled, w.poll_interval, w.idle_sync_interval) == (False, 2.0, 60.0)
    assert (w.failure_threshold, w.udp_host, w.udp_port) == (5, "10.0.0.2", 4000)


@pytest.mark.parametrize("extra", [
    "[watch]\nfailure_threshold = 0\n",
    "[watch]\npoll_interval = abc\n",
    "[watch]\nudp_port = 70000\n",
    "[watch]\nenabled = maybe\n",
])
def test_watch_config_rejects_bad_values(tmp_path, extra):
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load_config(_write_cfg(tmp_path, extra))


# --- clock handling around skipped / crashed / failed syncs --------------------

def _idle_watcher(statuses, sync_fn, clock):
    """Watcher already idle with last sync at t=0 and a 900s interval."""
    w = make_watcher(statuses, sync_fn, clock)
    w.machine.state = State.IDLE
    w.machine.last_sync = 0.0
    return w


def test_skipped_sync_does_not_reset_idle_clock():
    calls = []
    # idle-interval fires, but the pre-start re-check sees PLAYING
    w = _idle_watcher([C, P], lambda: calls.append(1) or 0, lambda: 1000.0)
    w.tick()
    w.wait_for_sync()
    assert calls == []
    assert w.machine.last_sync == 0.0          # clock untouched


def test_skipped_sync_is_retried_right_away_when_idle_again():
    calls = []
    w = _idle_watcher([C, P, C, C], lambda: calls.append(1) or 0, lambda: 1000.0)
    w.tick()                                   # trigger, re-check says PLAYING: skipped
    w.wait_for_sync()
    w.tick()                                   # still overdue -> triggers again
    w.wait_for_sync()
    assert calls == [1]


def test_crashed_sync_retries_soon_not_after_full_interval():
    def boom():
        raise RuntimeError("x")

    w = _idle_watcher([C, C], boom, lambda: 1000.0)
    w.tick()
    w.wait_for_sync()
    # due again 60s after the crash, not 900s
    assert w.machine.last_sync == 1000.0 - 900.0 + 60.0
    assert w.machine.observe(C, 1059.0) is None
    assert w.machine.observe(C, 1060.0) == "idle-interval"


def test_sync_that_ran_with_errors_resets_clock():
    w = _idle_watcher([C, C], lambda: 1, lambda: 1000.0)   # rc=1: ran, had errors
    w.tick()
    w.wait_for_sync()
    assert w.machine.last_sync == 1000.0


def test_successful_sync_resets_clock():
    w = _idle_watcher([C, C], lambda: 0, lambda: 1000.0)
    w.tick()
    w.wait_for_sync()
    assert w.machine.last_sync == 1000.0


# --- UNKNOWN status must not block silently -------------------------------------

def test_parse_status_keeps_raw_reply_for_unknown():
    s = parse_status("GET_STATUS WEIRD snes,Game")
    assert s == "UNKNOWN" and s.raw == "GET_STATUS WEIRD snes,Game"


def test_unknown_warns_first_time_with_raw_then_every_interval(caplog):
    m = StateMachine(3, 900, unknown_warn_interval=900)
    unknown = parse_status("GET_STATUS WEIRD snes,Game")
    caplog.set_level("WARNING", logger="romm_sync.watch")

    for t in range(0, 900, 60):                       # 15 minutes of polls
        assert m.observe(unknown, float(t)) is None   # blocks, never syncs
    warnings = [r for r in caplog.records if "unrecognized" in r.getMessage()]
    assert len(warnings) == 1                          # once, not per poll
    assert "GET_STATUS WEIRD snes,Game" in warnings[0].getMessage()

    m.observe(unknown, 900.0)                          # interval elapsed: again
    m.observe(unknown, 950.0)
    warnings = [r for r in caplog.records if "unrecognized" in r.getMessage()]
    assert len(warnings) == 2


def test_unknown_warning_rearms_after_known_status(caplog):
    m = StateMachine(3, 900, unknown_warn_interval=900)
    unknown = parse_status("GET_STATUS WEIRD")
    caplog.set_level("WARNING", logger="romm_sync.watch")
    m.observe(unknown, 0.0)
    m.observe(C, 1.0)                                  # known status arrives
    m.observe(unknown, 2.0)                            # unknown again: warn at once
    warnings = [r for r in caplog.records if "unrecognized" in r.getMessage()]
    assert len(warnings) == 2


def test_unknown_without_raw_still_warns(caplog):
    m = StateMachine(3, 900)
    caplog.set_level("WARNING", logger="romm_sync.watch")
    m.observe("UNKNOWN", 0.0)
    assert any("unrecognized" in r.getMessage() for r in caplog.records)
