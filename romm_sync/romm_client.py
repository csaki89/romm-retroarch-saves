"""Minimal RomM 5.2.0 API client (saves/states sync only)."""
import socket
from pathlib import Path
from urllib.parse import urljoin

import requests

from . import __version__

CLIENT_NAME = "romm-retroarch-sync-cli"


class RomMAuthError(RuntimeError):
    pass


class SaveConflict(RuntimeError):
    """409 from POST /api/saves: the slot changed since this device's last sync."""


class RomMClient:
    def __init__(self, base_url, token, timeout=30):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": f"{CLIENT_NAME}/{__version__}",
            }
        )

    def _url(self, path):
        return urljoin(self.base_url, path)

    @staticmethod
    def exchange_pair_code(base_url, code, timeout=15):
        resp = requests.post(
            urljoin(base_url.rstrip("/") + "/", "api/client-tokens/exchange"),
            json={"code": code.strip()},
            timeout=timeout,
        )
        if resp.status_code not in (200, 201):
            raise RomMAuthError(
                f"Pairing failed: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        raw = resp.json().get("raw_token")
        if not raw:
            raise RomMAuthError("Pairing response contained no raw_token")
        return raw

    def verify_auth(self):
        resp = self.session.get(
            self._url("api/roms"), params={"limit": 1}, timeout=self.timeout
        )
        if resp.status_code in (401, 403):
            raise RomMAuthError(
                f"RomM rejected the token (HTTP {resp.status_code}); "
                "re-run `romm-sync pair` and check the token scopes."
            )
        resp.raise_for_status()

    def register_device(self, name, platform_name):
        resp = self.session.post(
            self._url("api/devices"),
            json={
                "name": name,
                "platform": platform_name,
                "client": CLIENT_NAME,
                "client_version": __version__,
                "hostname": socket.gethostname(),
                "allow_existing": True,
                "allow_duplicate": False,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        device_id = data.get("device_id") or data.get("id")
        if not device_id:
            raise RuntimeError(f"No device_id in device registration response: {data}")
        return device_id

    def get_all_roms(self, page_size=500):
        roms, offset = [], 0
        while True:
            resp = self.session.get(
                self._url("api/roms"),
                params={
                    "limit": page_size,
                    "offset": offset,
                    "with_files": "true",
                    "fields": "id,fs_name_no_ext,files,platform_slug",
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            roms.extend(items)
            offset += len(items)
            if not items or offset >= data.get("total", 0):
                return roms

    # saves: server-side negotiate engine

    def negotiate_sync(self, device_id, saves):
        resp = self.session.post(
            self._url("api/sync/negotiate"),
            json={"device_id": device_id, "saves": saves},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["session_id"], data.get("operations", [])

    def complete_sync_session(self, session_id, completed, failed):
        resp = self.session.post(
            self._url(f"api/sync/sessions/{session_id}/complete"),
            json={"operations_completed": completed, "operations_failed": failed},
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def upload_save(
        self,
        rom_id,
        path,
        *,
        emulator,
        device_id,
        session_id,
        slot,
        overwrite=False,
        autocleanup_limit=10,
    ):
        params = {
            "rom_id": rom_id,
            "device_id": device_id,
            "session_id": session_id,
            "slot": slot,
            "overwrite": "true" if overwrite else "false",
            "autocleanup": "true",
            "autocleanup_limit": autocleanup_limit,
        }
        if emulator:
            params["emulator"] = emulator
        with open(path, "rb") as fh:
            resp = self.session.post(
                self._url("api/saves"),
                params=params,
                files={"saveFile": (Path(path).name, fh, "application/octet-stream")},
                timeout=120,
            )
        if resp.status_code == 409:
            raise SaveConflict(resp.text[:200])
        resp.raise_for_status()

    def download_save(self, save_id, device_id, session_id, target):
        resp = self.session.get(
            self._url(f"api/saves/{save_id}/content"),
            params={
                "device_id": device_id,
                "optimistic": "true",
                "session_id": session_id,
            },
            timeout=120,
            stream=True,
        )
        resp.raise_for_status()
        _write_stream(resp, target)

    # states: plain CRUD, no negotiate/hash/device tracking in 5.2.0

    def get_all_states(self):
        resp = self.session.get(self._url("api/states"), timeout=60)
        resp.raise_for_status()
        return resp.json()

    def upload_state(self, rom_id, path, *, emulator):
        params = {"rom_id": rom_id}
        if emulator:
            params["emulator"] = emulator
        with open(path, "rb") as fh:
            resp = self.session.post(
                self._url("api/states"),
                params=params,
                files={"stateFile": (Path(path).name, fh, "application/octet-stream")},
                timeout=120,
            )
        resp.raise_for_status()
        return resp.json()

    def download_state(self, state_id, target):
        resp = self.session.get(
            self._url(f"api/states/{state_id}/content"), timeout=120, stream=True
        )
        resp.raise_for_status()
        _write_stream(resp, target)


def _write_stream(resp, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(65536):
            if chunk:
                f.write(chunk)
    tmp.replace(target)
