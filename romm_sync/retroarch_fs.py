"""Scan RetroArch's saves/ and states/ directories.

Files are either flat (saves/Game.srm) or, with "sort by core" enabled, one
folder deep (saves/<core>/Game.srm); the core folder is reported as the
emulator.
"""
import re
from dataclasses import dataclass
from pathlib import Path

_STATE_AUTO = ".state.auto"
_STATE_NUMBERED = re.compile(r"^\.state\d+$")


@dataclass
class LocalFile:
    path: Path
    stem: str
    emulator: str
    size: int
    mtime: float


def _make(root, path, stem):
    rel = path.relative_to(root)
    st = path.stat()
    return LocalFile(
        path=path,
        stem=stem,
        emulator=rel.parts[0] if len(rel.parts) > 1 else None,
        size=st.st_size,
        mtime=st.st_mtime,
    )


def _files(root):
    root = Path(root)
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            yield root, path


def state_stem(name):
    """Content name of a RetroArch state file, or None if it isn't one."""
    if name.lower().endswith(_STATE_AUTO):
        return name[: -len(_STATE_AUTO)]
    suffix = Path(name).suffix.lower()
    if suffix == ".state" or _STATE_NUMBERED.match(suffix):
        return Path(name).stem
    return None


def scan_saves(saves_dir):
    return [_make(root, p, p.stem) for root, p in _files(saves_dir)]


def scan_states(states_dir):
    out = []
    for root, path in _files(states_dir):
        stem = state_stem(path.name)
        if stem is not None:
            out.append(_make(root, path, stem))
    return out


def download_target(root, layout_sorted, emulator, filename):
    """Where to place a file that has no local counterpart yet."""
    root = Path(root)
    if layout_sorted and emulator:
        return root / emulator / filename
    return root / filename
