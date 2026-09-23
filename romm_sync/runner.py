import logging
import platform

from .romm_client import RomMAuthError, RomMClient
from .state import SyncState
from .sync_saves import run_save_sync
from .sync_states import run_state_sync

log = logging.getLogger("romm_sync")


def _report(label, s):
    log.info(
        "%s: %d uploaded, %d downloaded, %d conflicts, %d unchanged, "
        "%d unmatched (skipped), %d errors",
        label, s.uploaded, s.downloaded, s.conflicts, s.no_op,
        s.skipped_unmatched, s.errors,
    )


def perform_sync(cfg, *, dry_run=False, saves_only=False, states_only=False):
    """One full two-way sync cycle. Returns 0 on success, 1 on any error."""
    if dry_run:
        log.info("DRY RUN: nothing will be uploaded, downloaded or written")

    client = RomMClient(cfg.url, cfg.token)
    try:
        client.verify_auth()
        sync_state = SyncState.load(cfg.state_file)
        if not sync_state.device_id:
            if dry_run:
                log.info("[dry-run] would register device %r", cfg.device_name)
                sync_state.device_id = "dry-run"
            else:
                sync_state.device_id = client.register_device(
                    cfg.device_name, platform.system()
                )
                sync_state.save()
                log.info("Registered device %s", sync_state.device_id)

        roms = client.get_all_roms()
        log.info("RomM library: %d ROMs", len(roms))

        errors = 0
        if not states_only:
            s = run_save_sync(
                client, sync_state.device_id, cfg.saves_dir, roms,
                cfg.conflict_policy, dry_run, cfg.backup_count,
            )
            _report("Saves", s)
            errors += s.errors
        if not saves_only:
            s = run_state_sync(
                client, cfg.states_dir, roms, sync_state,
                cfg.conflict_policy, dry_run, cfg.backup_count,
            )
            _report("States", s)
            errors += s.errors
            if not dry_run:
                sync_state.save()
    except RomMAuthError as exc:
        log.error("%s", exc)
        return 1
    except Exception:
        log.exception("Sync aborted")
        return 1
    return 1 if errors else 0
