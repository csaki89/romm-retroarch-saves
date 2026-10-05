import logging
import platform

from .retroarch_cfg import find_retroarch_cfg, sort_layout
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
                # No device_id = fresh install: RomM would hand back the old
                # device with its sync records and treat every locally missing
                # save as deleted on purpose, so start from a clean slate.
                sync_state.device_id = client.register_device(
                    cfg.device_name, platform.system(), reset_syncs=True
                )
                sync_state.save()
                log.info("Registered device %s", sync_state.device_id)

        cfg_path = find_retroarch_cfg(cfg.watch.retroarch_cfg, cfg.saves_dir)
        saves_sorted, states_sorted = sort_layout(cfg_path)
        log.debug(
            "retroarch.cfg %s: sort_savefiles_enable=%s sort_savestates_enable=%s",
            cfg_path, saves_sorted, states_sorted,
        )

        roms = client.get_all_roms()
        log.info("RomM library: %d ROMs", len(roms))

        errors = 0
        if not states_only:
            s = run_save_sync(
                client, sync_state.device_id, cfg.saves_dir, roms,
                cfg.conflict_policy, dry_run, cfg.backup_count,
                layout_sorted=saves_sorted,
            )
            _report("Saves", s)
            errors += s.errors
        if not saves_only:
            s = run_state_sync(
                client, cfg.states_dir, roms, sync_state,
                cfg.conflict_policy, dry_run, cfg.backup_count,
                layout_sorted=states_sorted,
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

def reset_device(cfg):
    """Re-register with reset_syncs so RomM drops this device's sync records."""
    client = RomMClient(cfg.url, cfg.token)
    try:
        client.verify_auth()
        sync_state = SyncState.load(cfg.state_file)
        sync_state.device_id = client.register_device(
            cfg.device_name, platform.system(), reset_syncs=True
        )
        sync_state.save()
    except RomMAuthError as exc:
        log.error("%s", exc)
        return 1
    except Exception:
        log.exception("Device reset failed")
        return 1
    log.info("Device %s reset", sync_state.device_id)
    print(
        f"Device {sync_state.device_id} reset. The next sync will download "
        "saves that are missing locally."
    )
    return 0
