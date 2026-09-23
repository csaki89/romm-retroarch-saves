"""Two-way save-state sync.

RomM 5.2.0 has no negotiate engine, hash or device tracking for states, so
the decision is made here from a local record (SyncState) of each state's
last-synced fingerprint: local size+mtime and the server's updated_at.
States are matched to the server by exact file name (the server does not
rename them).
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from functools import partial

from .backup import backup_existing
from .common import pick_winner
from .matcher import build_rom_index
from .retroarch_fs import download_target, scan_states

log = logging.getLogger(__name__)


@dataclass
class Summary:
    uploaded: int = 0
    downloaded: int = 0
    conflicts: int = 0
    no_op: int = 0
    skipped_unmatched: int = 0
    errors: int = 0


def run_state_sync(
    client, states_dir, roms, sync_state, conflict_policy, dry_run, backup_count=3
):
    summary = Summary()
    backup = partial(backup_existing, keep=backup_count)
    rom_index = build_rom_index(roms)
    local_files = scan_states(states_dir)
    layout_sorted = any(lf.emulator for lf in local_files)

    server = defaultdict(dict)  # rom_id -> file_name -> state row
    for row in client.get_all_states():
        server[row["rom_id"]][row["file_name"]] = row

    def upload(rom_id, lf):
        if dry_run:
            log.info("[dry-run] would upload state %s (ROM %s)", lf.path.name, rom_id)
        else:
            row = client.upload_state(rom_id, lf.path, emulator=lf.emulator)
            sync_state.states[str(lf.path)] = {
                "size": lf.size,
                "mtime": lf.mtime,
                "server_updated_at": row.get("updated_at"),
            }
            log.info("Uploaded state %s (ROM %s)", lf.path.name, rom_id)
        summary.uploaded += 1

    def download(rom_id, target, row):
        if dry_run:
            log.info("[dry-run] would download state -> %s (ROM %s)", target, rom_id)
            if backup_count > 0 and target.exists():
                log.info("[dry-run] would back up existing %s first", target.name)
        else:
            client.download_state(row["id"], target, before_replace=backup)
            st = target.stat()
            sync_state.states[str(target)] = {
                "size": st.st_size,
                "mtime": st.st_mtime,
                "server_updated_at": row.get("updated_at"),
            }
            log.info("Downloaded state -> %s (ROM %s)", target, rom_id)
        summary.downloaded += 1

    seen = set()
    for lf in local_files:
        rom_id = rom_index.get(lf.stem)
        if rom_id is None:
            log.warning("No ROM matches state %s; skipped", lf.path)
            summary.skipped_unmatched += 1
            continue
        seen.add((rom_id, lf.path.name))
        row = server.get(rom_id, {}).get(lf.path.name)
        try:
            if row is None:
                upload(rom_id, lf)
                continue

            known = sync_state.states.get(str(lf.path))
            server_ts = row.get("updated_at")
            local_changed = (
                known is None
                or known.get("size") != lf.size
                or known.get("mtime") != lf.mtime
            )
            server_changed = known is None or known.get("server_updated_at") != server_ts

            if not local_changed and not server_changed:
                summary.no_op += 1
            elif local_changed and not server_changed:
                upload(rom_id, lf)
            elif server_changed and not local_changed:
                download(rom_id, lf.path, row)
            else:
                summary.conflicts += 1
                winner = pick_winner(conflict_policy, lf.mtime, server_ts)
                why = "no earlier sync record" if known is None else "both sides changed"
                log.warning(
                    "State conflict %s (ROM %s, %s): policy=%s -> keeping %s",
                    lf.path.name, rom_id, why, conflict_policy, winner.upper(),
                )
                if winner == "local":
                    upload(rom_id, lf)
                else:
                    download(rom_id, lf.path, row)
        except Exception:
            log.exception("State operation failed for %s", lf.path)
            summary.errors += 1

    # Server states with no local file: pull them (new device / never fetched).
    known_rom_ids = set(rom_index.values())
    for rom_id, rows in server.items():
        for name, row in rows.items():
            if (rom_id, name) in seen or rom_id not in known_rom_ids:
                continue
            target = download_target(states_dir, layout_sorted, row.get("emulator"), name)
            known = sync_state.states.get(str(target))
            if known and known.get("server_updated_at") == row.get("updated_at"):
                # Synced before and since removed locally: don't resurrect it.
                summary.no_op += 1
                continue
            try:
                download(rom_id, target, row)
            except Exception:
                log.exception("State download failed for %s", target)
                summary.errors += 1
    return summary
