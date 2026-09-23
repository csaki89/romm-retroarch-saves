import argparse
import logging
import platform
import sys
from pathlib import Path

from . import __version__, config as cfgmod
from .logging_setup import setup_logging
from .romm_client import RomMAuthError, RomMClient
from .state import SyncState
from .sync_saves import run_save_sync
from .sync_states import run_state_sync

log = logging.getLogger("romm_sync")


def cmd_pair(args):
    path = Path(args.config).expanduser() if args.config else cfgmod.DEFAULT_CONFIG_PATH
    try:
        token = RomMClient.exchange_pair_code(args.url, args.code)
    except Exception as exc:
        print(f"Pairing failed: {exc}", file=sys.stderr)
        return 1
    cfgmod.write_token(path, args.url, token, args.device_name)
    print(f"Paired. Token saved to {path}")
    return 0


def _report(label, s):
    log.info(
        "%s: %d uploaded, %d downloaded, %d conflicts, %d unchanged, "
        "%d unmatched (skipped), %d errors",
        label, s.uploaded, s.downloaded, s.conflicts, s.no_op,
        s.skipped_unmatched, s.errors,
    )


def cmd_sync(args):
    try:
        cfg = cfgmod.load_config(args.config)
    except cfgmod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    setup_logging(cfg.log_file, cfg.log_level, args.verbose)
    if args.dry_run:
        log.info("DRY RUN: nothing will be uploaded, downloaded or written")

    client = RomMClient(cfg.url, cfg.token)
    try:
        client.verify_auth()
        sync_state = SyncState.load(cfg.state_file)
        if not sync_state.device_id:
            if args.dry_run:
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
        if not args.states_only:
            s = run_save_sync(
                client, sync_state.device_id, cfg.saves_dir, roms,
                cfg.conflict_policy, args.dry_run,
            )
            _report("Saves", s)
            errors += s.errors
        if not args.saves_only:
            s = run_state_sync(
                client, cfg.states_dir, roms, sync_state,
                cfg.conflict_policy, args.dry_run,
            )
            _report("States", s)
            errors += s.errors
            if not args.dry_run:
                sync_state.save()
    except RomMAuthError as exc:
        log.error("%s", exc)
        return 1
    except Exception:
        log.exception("Sync aborted")
        return 1
    return 1 if errors else 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="romm-sync",
        description="Two-way sync of RetroArch saves and states with RomM.",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    pair = sub.add_parser("pair", help="exchange a RomM pairing code for an API token")
    pair.add_argument("--url", required=True)
    pair.add_argument("--code", required=True)
    pair.add_argument("--device-name")
    pair.add_argument("--config", help=f"default: {cfgmod.DEFAULT_CONFIG_PATH}")
    pair.set_defaults(func=cmd_pair)

    sync = sub.add_parser("sync", help="run one sync cycle")
    sync.add_argument("--config", help=f"default: {cfgmod.DEFAULT_CONFIG_PATH}")
    sync.add_argument("--dry-run", action="store_true",
                      help="log what would happen, change nothing")
    sync.add_argument("--saves-only", action="store_true")
    sync.add_argument("--states-only", action="store_true")
    sync.add_argument("-v", "--verbose", action="store_true")
    sync.set_defaults(func=cmd_sync)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if getattr(args, "saves_only", False) and args.states_only:
        print("--saves-only and --states-only are mutually exclusive", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
