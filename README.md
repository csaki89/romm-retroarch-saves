# romm-retroarch-saves

Headless two-way sync of RetroArch battery saves and save states with a
[RomM](https://github.com/rommapp/romm) server (developed against 5.2.0).
Saves and states only: no ROM downloads, BIOS or GUI. Only dependency:
`requests`. GPL-3.0-or-later; the RomM API usage was studied from
[Covin90/romm-retroarch-sync](https://github.com/Covin90/romm-retroarch-sync)
and checked against the RomM server source.

## Install

```sh
python3 -m venv ~/.venvs/romm-sync
~/.venvs/romm-sync/bin/pip install .
```

## Pair (once)

In the RomM web UI create a Client API Token with the scopes `roms.read`,
`assets.read`, `assets.write`, `devices.read`, `devices.write`, then start
pairing to get an 8-character code:

```sh
~/.venvs/romm-sync/bin/romm-sync pair --url https://romm.example.com --code ABCD1234
```

This writes `~/.config/romm-retroarch-sync/config.ini` (mode 600). See
`config.example.ini` for all options (directories, conflict policy, logging).

## Run

```sh
romm-sync sync --dry-run   # show what would happen, change nothing
romm-sync sync             # real sync; exit code 1 if anything failed
```

Options: `--saves-only`, `--states-only`, `--config PATH`, `-v`.
Logs go to the console and to `~/.local/state/romm-retroarch-sync/sync.log`.

Scheduling:

```sh
# systemd (user)
cp packaging/romm-retroarch-sync.* ~/.config/systemd/user/
systemctl --user enable --now romm-retroarch-sync.timer

# or cron
*/15 * * * * $HOME/.venvs/romm-sync/bin/romm-sync sync
```

## How it works

* **Matching**: a local file belongs to the ROM whose filename (without
  extension) equals the file's name (`Game.srm`, `Game.state1` -> `Game`).
  Unmatched or ambiguous names are skipped and logged, never guessed.
* **Saves** use RomM's `/api/sync/negotiate` engine (slot `autosave`, MD5
  content hash). The server decides upload/download/conflict per device.
* **States** have no negotiate/hash in RomM 5.2.0, so the tool compares local
  size+mtime and the server's `updated_at` with what it recorded at the last
  sync (`state.json`), matching states by exact file name.
* **Conflicts** (both sides changed) follow `conflict_policy`
  (`newer` default / `local` / `server`) and are logged as warnings.
* **Backups**: when a download would overwrite an existing local file, the
  old file is first renamed to `<name>.bak-<timestamp>` (next to it). The
  newest `backup_count` backups per file are kept (`[sync] backup_count`,
  default 3, `0` disables). `.bak-*` and `.part` files are never synced.
* **Safe writes**: downloads go to `<name>.part` and replace the target only
  after a complete, size-checked download; on failure the `.part` is deleted
  and the existing file is untouched. `state.json` is written the same way.
  The backup is made right before the final swap, so a failed download
  never moves your file away.
* **Dry run**: saves are only listed, because negotiating creates a
  server-side session; server decisions for saves show up on a real run.
  States get a full dry-run plan.
* Not synced: state screenshots, fuzzy region/multi-disc name matching.

## Development

```sh
pip install -e '.[dev]' && pytest
```
