import hashlib
import os

from romm_sync.common import pick_winner
from romm_sync.hashing import compute_content_hash
from romm_sync.matcher import build_rom_index
from romm_sync.retroarch_fs import scan_saves, scan_states, state_stem
from romm_sync.state import SyncState
from romm_sync.sync_saves import run_save_sync, strip_datetime_tag
from romm_sync.sync_states import run_state_sync


def test_hash_plain_file(tmp_path):
    f = tmp_path / "a.srm"
    f.write_bytes(b"hello")
    assert compute_content_hash(f) == hashlib.md5(b"hello").hexdigest()


def test_strip_datetime_tag():
    assert strip_datetime_tag("Game [2026-01-02_03-04-05].srm") == "Game.srm"
    assert strip_datetime_tag("Game (USA) [x].srm") == "Game (USA) [x].srm"


def test_state_stem():
    assert state_stem("Game.state") == "Game"
    assert state_stem("Game.state3") == "Game"
    assert state_stem("Game.state.auto") == "Game"
    assert state_stem("Game.state.bak") is None
    assert state_stem("Game.srm") is None


def test_rom_index_drops_ambiguous():
    roms = [
        {"id": 1, "fs_name_no_ext": "A"},
        {"id": 2, "fs_name_no_ext": "B", "files": [{"file_name": "B (Disc 1).cue"}]},
        {"id": 3, "fs_name_no_ext": "A"},
    ]
    idx = build_rom_index(roms)
    assert "A" not in idx
    assert idx["B"] == 2 and idx["B (Disc 1)"] == 2


def test_scan_layouts(tmp_path):
    (tmp_path / "Game.srm").write_bytes(b"1")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "Other.srm").write_bytes(b"2")
    files = {f.path.name: f.emulator for f in scan_saves(tmp_path)}
    assert files == {"Game.srm": None, "Other.srm": "core"}


def test_pick_winner():
    assert pick_winner("local", 0, "2030-01-01T00:00:00+00:00") == "local"
    assert pick_winner("server", 9e9, None) == "server"
    assert pick_winner("newer", 0, "2030-01-01T00:00:00Z") == "server"
    assert pick_winner("newer", 9e9, "2030-01-01T00:00:00Z") == "local"
    assert pick_winner("newer", 0, None) == "local"


class FakeClient:
    def __init__(self, operations=(), states=()):
        self.operations = list(operations)
        self.states = list(states)
        self.calls = []

    def negotiate_sync(self, device_id, saves):
        self.calls.append(("negotiate", saves))
        return 7, self.operations

    def upload_save(self, rom_id, path, **kw):
        self.calls.append(("upload_save", rom_id, kw["overwrite"]))

    def download_save(self, save_id, device_id, session_id, target):
        self.calls.append(("download_save", save_id, str(target)))

    def complete_sync_session(self, sid, ok, bad):
        self.calls.append(("complete", ok, bad))

    def get_all_states(self):
        return self.states

    def upload_state(self, rom_id, path, emulator):
        self.calls.append(("upload_state", rom_id, path.name))
        return {"id": 99, "updated_at": "2026-01-01T00:00:00Z"}

    def download_state(self, state_id, target):
        self.calls.append(("download_state", state_id))
        target.write_bytes(b"server")


ROMS = [{"id": 1, "fs_name_no_ext": "Game"}, {"id": 2, "fs_name_no_ext": "New"}]


def test_save_sync_ops(tmp_path):
    (tmp_path / "Game.srm").write_bytes(b"x")
    ops = [
        {"action": "upload", "rom_id": 1},
        {"action": "download", "rom_id": 2, "save_id": 5,
         "file_name": "New [2026-01-02_03-04-05].srm"},
    ]
    c = FakeClient(ops)
    s = run_save_sync(c, "dev", tmp_path, ROMS, "newer", dry_run=False)
    assert (s.uploaded, s.downloaded, s.errors) == (1, 1, 0)
    assert ("download_save", 5, str(tmp_path / "New.srm")) in c.calls
    assert ("complete", 2, 0) in c.calls


def test_save_conflict_policy_local_overwrites(tmp_path):
    (tmp_path / "Game.srm").write_bytes(b"x")
    c = FakeClient([{"action": "conflict", "rom_id": 1, "save_id": 5,
                     "file_name": "Game.srm", "server_updated_at": None}])
    run_save_sync(c, "dev", tmp_path, ROMS, "local", dry_run=False)
    assert ("upload_save", 1, True) in c.calls


def test_save_dry_run_touches_nothing(tmp_path):
    (tmp_path / "Game.srm").write_bytes(b"x")
    c = FakeClient()
    run_save_sync(c, "dev", tmp_path, ROMS, "newer", dry_run=True)
    assert c.calls == []


def test_state_sync_upload_download_and_dry_run(tmp_path):
    (tmp_path / "Game.state").write_bytes(b"local")
    server = [{"id": 10, "rom_id": 2, "file_name": "New.state",
               "updated_at": "2026-01-01T00:00:00Z", "emulator": None}]

    dry = FakeClient(states=server)
    st = SyncState(tmp_path / "st.json")
    s = run_state_sync(dry, tmp_path, ROMS, st, "newer", dry_run=True)
    assert (s.uploaded, s.downloaded) == (1, 1)
    assert dry.calls == [] and not (tmp_path / "New.state").exists() and st.states == {}

    real = FakeClient(states=server)
    s = run_state_sync(real, tmp_path, ROMS, st, "newer", dry_run=False)
    assert (s.uploaded, s.downloaded) == (1, 1)
    assert (tmp_path / "New.state").read_bytes() == b"server"

    again = FakeClient(states=[
        {"id": 99, "rom_id": 1, "file_name": "Game.state",
         "updated_at": "2026-01-01T00:00:00Z", "emulator": None}] + server)
    s = run_state_sync(again, tmp_path, ROMS, st, "newer", dry_run=False)
    assert s.no_op == 2 and s.uploaded == 0 and s.downloaded == 0
