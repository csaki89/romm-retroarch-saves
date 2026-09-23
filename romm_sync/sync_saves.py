"""Two-way battery-save sync through RomM's /api/sync/negotiate engine.

The server pairs saves on (rom_id, slot) and decides upload / download /
conflict / no_op from its own per-device records, so we only send the full
local inventory and execute what comes back.
"""
import logging
import re
from dataclasses import dataclass
from functools import partial

from .backup import backup_existing
from .common import iso_from_mtime, pick_winner
from .hashing import compute_content_hash
from .matcher import build_rom_index
from .retroarch_fs import download_target, scan_saves
from .romm_client import SaveConflict

log = logging.getLogger(__name__)

SLOT = "autosave"  # the slot other RomM clients (grout, muOS...) use too
AUTOCLEANUP_LIMIT = 10

# Same format the server appends to slotted uploads (endpoints/saves.py).
_TAG = re.compile(r" \[\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\]")


@dataclass
class Summary:
    uploaded: int = 0
    downloaded: int = 0
    conflicts: int = 0
    no_op: int = 0
    skipped_unmatched: int = 0
    errors: int = 0


def strip_datetime_tag(server_name):
    """'Game [2026-01-02_03-04-05].srm' -> 'Game.srm'."""
    return _TAG.sub("", server_name)


def build_inventory(local_files, rom_index):
    """Return ({rom_id: (LocalFile, payload)}, unmatched_count)."""
    by_rom, unmatched = {}, 0
    for lf in local_files:
        rom_id = rom_index.get(lf.stem)
        if rom_id is None:
            log.warning("No ROM matches save %s; skipped", lf.path)
            unmatched += 1
            continue
        if rom_id in by_rom and by_rom[rom_id][0].mtime >= lf.mtime:
            log.warning(
                "Duplicate local saves for ROM %s: keeping %s, ignoring %s",
                rom_id,
                by_rom[rom_id][0].path,
                lf.path,
            )
            continue
        if rom_id in by_rom:
            log.warning(
                "Duplicate local saves for ROM %s: keeping %s, ignoring %s",
                rom_id,
                lf.path,
                by_rom[rom_id][0].path,
            )
        by_rom[rom_id] = (
            lf,
            {
                "rom_id": rom_id,
                "file_name": lf.path.name,
                "slot": SLOT,
                "emulator": lf.emulator,
                "content_hash": compute_content_hash(lf.path),
                "updated_at": iso_from_mtime(lf.mtime),
                "file_size_bytes": lf.size,
            },
        )
    return by_rom, unmatched


def run_save_sync(
    client, device_id, saves_dir, roms, conflict_policy, dry_run, backup_count=3
):
    summary = Summary()
    backup = partial(backup_existing, keep=backup_count)
    local_files = scan_saves(saves_dir)
    layout_sorted = any(lf.emulator for lf in local_files)
    by_rom, summary.skipped_unmatched = build_inventory(
        local_files, build_rom_index(roms)
    )

    if dry_run:
        # Negotiate itself creates a server-side session, so a dry run stops here.
        for rom_id, (lf, _) in by_rom.items():
            log.info("[dry-run] would negotiate save %s (ROM %s)", lf.path.name, rom_id)
        log.info(
            "[dry-run] %d local save(s) would be sent to /api/sync/negotiate; "
            "the server-side decisions are only known on a real run",
            len(by_rom),
        )
        return summary

    session_id, operations = client.negotiate_sync(
        device_id, [payload for _, payload in by_rom.values()]
    )
    completed = failed = 0

    def upload(rom_id, lf, overwrite):
        client.upload_save(
            rom_id,
            lf.path,
            emulator=lf.emulator,
            device_id=device_id,
            session_id=session_id,
            slot=SLOT,
            overwrite=overwrite,
            autocleanup_limit=AUTOCLEANUP_LIMIT,
        )

    for op in operations:
        action, rom_id = op.get("action"), op.get("rom_id")
        entry = by_rom.get(rom_id)
        lf = entry[0] if entry else None
        try:
            if action == "no_op":
                summary.no_op += 1
            elif action == "upload":
                if lf is None:
                    raise RuntimeError(f"upload requested for unknown ROM {rom_id}")
                upload(rom_id, lf, overwrite=False)
                summary.uploaded += 1
                completed += 1
                log.info("Uploaded save %s (ROM %s): %s", lf.path.name, rom_id, op.get("reason"))
            elif action == "download":
                name = strip_datetime_tag(op["file_name"])
                target = lf.path if lf else download_target(
                    saves_dir, layout_sorted, op.get("emulator"), name
                )
                client.download_save(
                    op["save_id"], device_id, session_id, target, before_replace=backup
                )
                summary.downloaded += 1
                completed += 1
                log.info("Downloaded save -> %s (ROM %s): %s", target, rom_id, op.get("reason"))
            elif action == "conflict":
                summary.conflicts += 1
                winner = pick_winner(
                    conflict_policy, lf.mtime if lf else 0, op.get("server_updated_at")
                )
                log.warning(
                    "Save conflict ROM %s (%s): policy=%s -> keeping %s",
                    rom_id,
                    op.get("reason"),
                    conflict_policy,
                    winner.upper(),
                )
                if winner == "local" and lf:
                    upload(rom_id, lf, overwrite=True)
                else:
                    target = lf.path if lf else download_target(
                        saves_dir,
                        layout_sorted,
                        op.get("emulator"),
                        strip_datetime_tag(op["file_name"]),
                    )
                    client.download_save(
                        op["save_id"], device_id, session_id, target, before_replace=backup
                    )
                completed += 1
            else:
                log.warning("Unknown negotiate action %r: %s", action, op)
        except SaveConflict as exc:
            log.warning("Upload rejected (409) for ROM %s, retrying next run: %s", rom_id, exc)
            summary.errors += 1
            failed += 1
        except Exception:
            log.exception("Save operation failed: %s", op)
            summary.errors += 1
            failed += 1

    try:
        client.complete_sync_session(session_id, completed, failed)
    except Exception:
        log.exception("Could not close sync session %s", session_id)
        summary.errors += 1
    return summary
