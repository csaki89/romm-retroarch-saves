import os
import subprocess
import sys
import time

import pytest

from romm_sync import __main__ as cli
from romm_sync.lock import InstanceLock

posix_only = pytest.mark.skipif(os.name != "posix", reason="PID readable only on POSIX")


def test_second_instance_cannot_acquire_until_released(tmp_path):
    path = tmp_path / "watch.pid"
    first, second = InstanceLock(path), InstanceLock(path)
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


@posix_only
def test_holder_pid_is_recorded(tmp_path):
    path = tmp_path / "watch.pid"
    first = InstanceLock(path)
    assert first.acquire()
    other = InstanceLock(path)
    assert not other.acquire()
    assert other.holder_pid() == os.getpid()
    first.release()


@posix_only
def test_failed_attempt_does_not_clobber_holder_pid_file(tmp_path):
    path = tmp_path / "watch.pid"
    first = InstanceLock(path)
    assert first.acquire()
    before = path.read_text()
    assert not InstanceLock(path).acquire()
    assert path.read_text() == before
    first.release()


def test_lock_is_freed_when_holder_is_killed(tmp_path):
    path = tmp_path / "watch.pid"
    code = (
        "import sys, time\n"
        "from romm_sync.lock import InstanceLock\n"
        "l = InstanceLock(sys.argv[1])\n"
        "assert l.acquire()\n"
        "print('locked', flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "locked"
        assert not InstanceLock(path).acquire()      # held by the child
    finally:
        proc.kill()                                  # crash: no cleanup runs
        proc.wait(10)

    deadline = time.time() + 5
    lock = InstanceLock(path)
    while not lock.acquire():
        assert time.time() < deadline, "lock was not released after the crash"
        time.sleep(0.05)
    lock.release()


def _config(tmp_path, enabled="true"):
    cfg = tmp_path / "c.ini"
    cfg.write_text(
        "[romm]\nurl = http://x\ntoken = t\n"
        f"[watch]\nenabled = {enabled}\n"
        f"[state]\nfile = {tmp_path / 'state' / 'state.json'}\n"
        f"[logging]\nfile = {tmp_path / 'sync.log'}\n"
    )
    return cfg


def test_watch_exits_zero_with_message_when_already_running(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_run_watch", lambda cfg, args: ran.append(1) or 0)
    holder = InstanceLock(tmp_path / "state" / "watch.pid")
    assert holder.acquire()

    rc = cli.main(["watch", "--config", str(_config(tmp_path))])

    holder.release()
    assert rc == 0
    assert ran == []                                  # did not start
    assert "already running" in (tmp_path / "sync.log").read_text()


def test_watch_takes_lock_runs_and_releases(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cfg, args):
        seen["locked"] = not InstanceLock(tmp_path / "state" / "watch.pid").acquire()
        return 0

    monkeypatch.setattr(cli, "_run_watch", fake_run)
    assert cli.main(["watch", "--config", str(_config(tmp_path))]) == 0
    assert seen["locked"]                             # held while running
    again = InstanceLock(tmp_path / "state" / "watch.pid")
    assert again.acquire()                            # released afterwards
    again.release()


def test_lock_released_when_watch_raises(tmp_path, monkeypatch):
    def boom(cfg, args):
        raise RuntimeError("x")

    monkeypatch.setattr(cli, "_run_watch", boom)
    with pytest.raises(RuntimeError):
        cli.main(["watch", "--config", str(_config(tmp_path))])
    again = InstanceLock(tmp_path / "state" / "watch.pid")
    assert again.acquire()
    again.release()
