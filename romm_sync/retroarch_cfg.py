"""Reading settings from retroarch.cfg."""
import re
from pathlib import Path


def find_retroarch_cfg(explicit, saves_dir):
    """The configured retroarch.cfg, else the one next to the saves dir, else
    the usual locations (Flatpak, ~/.config/retroarch). None if not found."""
    if explicit:
        explicit = Path(explicit)
        return explicit if explicit.is_file() else None
    candidates = [
        Path(saves_dir).parent / "retroarch.cfg",
        Path("~/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg").expanduser(),
        Path("~/.config/retroarch/retroarch.cfg").expanduser(),
    ]
    return next((c for c in candidates if c.is_file()), None)


def cfg_value(text, key):
    matches = re.findall(
        rf'^\s*{re.escape(key)}\s*=\s*"?([^"\r\n]*?)"?\s*$', text, re.MULTILINE
    )
    return matches[-1] if matches else None


def _flag(text, key):
    value = cfg_value(text, key)
    if value is None:
        return None
    return value.strip().lower() == "true"


def sort_layout(cfg_path):
    """(saves_sorted, states_sorted) from sort_savefiles_enable and
    sort_savestates_enable (sorted = saves/<core>/ subfolders). A value is
    None when the cfg or its key cannot be read."""
    if not cfg_path:
        return None, None
    try:
        text = Path(cfg_path).read_text(errors="replace")
    except OSError:
        return None, None
    return (
        _flag(text, "sort_savefiles_enable"),
        _flag(text, "sort_savestates_enable"),
    )
