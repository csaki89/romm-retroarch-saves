import logging

log = logging.getLogger(__name__)


def build_rom_index(roms):
    """Map filename stem -> rom_id.

    Uses each ROM's fs_name_no_ext and, for multi-file ROMs, each member
    file's stem. Stems shared by several ROMs are dropped (logged) rather
    than guessed.
    """
    candidates = {}

    def add(stem, rom_id):
        if stem:
            candidates.setdefault(stem, set()).add(rom_id)

    for rom in roms:
        rom_id = rom.get("id")
        if rom_id is None:
            continue
        add(rom.get("fs_name_no_ext"), rom_id)
        for member in rom.get("files") or []:
            name = member.get("file_name") or ""
            add(name.rsplit(".", 1)[0] if "." in name else name, rom_id)

    index = {}
    for stem, ids in candidates.items():
        if len(ids) == 1:
            index[stem] = next(iter(ids))
        else:
            log.warning(
                "Ambiguous name %r matches ROMs %s; files with this name are skipped",
                stem,
                sorted(ids),
            )
    return index
