import glob
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

BAK_TAG = ".bak-"


def is_backup_or_temp(name):
    return BAK_TAG in name or name.endswith(".part")


def backup_existing(path, keep):
    """Rename an existing file to <name>.bak-<timestamp>, keeping the newest
    `keep` backups of it. keep <= 0 disables backups."""
    path = Path(path)
    if keep <= 0 or not path.exists():
        return None
    now = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    stamp += f"-{int((now % 1) * 1_000_000):06d}"
    bak = path.with_name(f"{path.name}{BAK_TAG}{stamp}")
    n = 0
    while bak.exists():  # never overwrite an existing backup
        n += 1
        bak = path.with_name(f"{path.name}{BAK_TAG}{stamp}-{n}")
    os.replace(path, bak)
    log.info("Backed up %s -> %s", path.name, bak.name)
    prune_backups(path, keep)
    return bak


def prune_backups(path, keep):
    path = Path(path)
    backups = sorted(path.parent.glob(glob.escape(path.name) + BAK_TAG + "*"))
    for old in backups[:-keep] if keep > 0 else []:
        try:
            old.unlink()
            log.info("Removed old backup %s", old.name)
        except OSError as exc:
            log.warning("Could not remove old backup %s: %s", old, exc)
