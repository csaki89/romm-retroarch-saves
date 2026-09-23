import argparse
import logging
import signal
import sys
from pathlib import Path

from . import __version__, config as cfgmod
from .logging_setup import setup_logging
from .romm_client import RomMClient
from .runner import perform_sync

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


def cmd_sync(args):
    try:
        cfg = cfgmod.load_config(args.config)
    except cfgmod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    setup_logging(cfg.log_file, cfg.log_level, args.verbose)
    return perform_sync(
        cfg,
        dry_run=args.dry_run,
        saves_only=args.saves_only,
        states_only=args.states_only,
    )


def cmd_watch(args):
    from . import watch

    try:
        cfg = cfgmod.load_config(args.config)
    except cfgmod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    setup_logging(cfg.log_file, cfg.log_level, args.verbose)

    w = cfg.watch
    if not w.enabled:
        log.error("watch is disabled: set [watch] enabled = true in %s", cfg.path)
        return 2

    if w.udp_host in watch.LOOPBACK:
        cfg_path = watch.find_retroarch_cfg(w, cfg.saves_dir)
        if cfg_path is None:
            log.warning(
                "retroarch.cfg not found; cannot verify network_cmd_enable "
                "(set [watch] retroarch_cfg to check it)"
            )
        else:
            problem = watch.check_network_commands(cfg_path, w.udp_port)
            if problem:
                log.error("%s", problem)
                return 2
            log.info("network_cmd_enable is on in %s", cfg_path)

    watcher = watch.Watcher(
        w, lambda: perform_sync(cfg, dry_run=args.dry_run)
    )

    def _stop(signum, _frame):
        log.info("Received %s, shutting down", signal.Signals(signum).name)
        watcher.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    watcher.run()
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="romm-sync",
        description="Two-way sync of RetroArch saves and states with RomM.",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    default_cfg = f"default: {cfgmod.DEFAULT_CONFIG_PATH}"

    pair = sub.add_parser("pair", help="exchange a RomM pairing code for an API token")
    pair.add_argument("--url", required=True)
    pair.add_argument("--code", required=True)
    pair.add_argument("--device-name")
    pair.add_argument("--config", help=default_cfg)
    pair.set_defaults(func=cmd_pair)

    sync = sub.add_parser("sync", help="run one sync cycle")
    sync.add_argument("--config", help=default_cfg)
    sync.add_argument("--dry-run", action="store_true",
                      help="log what would happen, change nothing")
    sync.add_argument("--saves-only", action="store_true")
    sync.add_argument("--states-only", action="store_true")
    sync.add_argument("-v", "--verbose", action="store_true")
    sync.set_defaults(func=cmd_sync)

    watch = sub.add_parser(
        "watch",
        help="watch RetroArch and sync only while no content is loaded",
    )
    watch.add_argument("--config", help=default_cfg)
    watch.add_argument("--dry-run", action="store_true",
                       help="run the triggered syncs in dry-run mode")
    watch.add_argument("-v", "--verbose", action="store_true")
    watch.set_defaults(func=cmd_watch)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if getattr(args, "saves_only", False) and args.states_only:
        print("--saves-only and --states-only are mutually exclusive", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
