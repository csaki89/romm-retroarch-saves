import json
from pathlib import Path


class SyncState:
    """Tool-managed bookkeeping: device_id and per-state-file fingerprints.

    Saves need none of this (RomM's negotiate keeps per-device sync state
    server-side); states have no such server support.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.device_id = None
        self.states = {}

    @classmethod
    def load(cls, path):
        state = cls(path)
        try:
            data = json.loads(state.path.read_text())
        except (OSError, ValueError):
            return state
        state.device_id = data.get("device_id")
        state.states = data.get("states", {})
        return state

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"device_id": self.device_id, "states": self.states},
                indent=2,
                sort_keys=True,
            )
        )
        tmp.replace(self.path)
