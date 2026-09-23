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

## Watch mode (RetroArch running for days)

A timer alone is unsafe when RetroArch keeps running: it would see half-written
saves, and a download would be overwritten when the running game writes its
save back on exit. `romm-sync watch` polls RetroArch's UDP command interface
(`GET_STATUS`, 127.0.0.1:55355) and runs a full two-way sync only while **no
content is loaded**:

| Event | Sync? |
|---|---|
| no answer -> answers (RetroArch started, nothing loaded) | yes |
| answers -> no answer for `failure_threshold` polls (stopped) | yes |
| PLAYING/PAUSED -> CONTENTLESS (left a game) | yes, immediately |
| CONTENTLESS, every `idle_sync_interval` (default 15 min) | yes |
| while PLAYING/PAUSED, or CONTENTLESS -> PLAYING | **never** |

Notes: PAUSED counts as loaded (the save is still in memory). The first poll
after `watch` starts syncs once if RetroArch is idle. A single lost UDP packet
is not treated as a stop. Only one sync runs at a time; a trigger arriving
during a sync is dropped (and logged). Just before each sync the status is
checked again and the sync is skipped if content has loaded in the meantime.
SIGTERM/SIGINT stop the loop and let a running sync finish first.

Requirement: `network_cmd_enable = "true"` in `retroarch.cfg` (Settings >
Network > Network Commands). `watch` verifies this at startup and exits with
code 2 and an explanation if it is off; it never edits the file. Edit it while
RetroArch is closed, because RetroArch rewrites its config on exit.

Settings live in the `[watch]` section (`enabled`, `poll_interval`,
`idle_sync_interval`, `failure_threshold`, `udp_host`, `udp_port`; see
`config.example.ini`). Run it with `romm-sync watch [-v] [--dry-run]`.

### supervisord (container without systemd)

`packaging/supervisor/romm-retroarch-watch.conf` is a supervisord program
template and `packaging/init.d/50-romm-sync.sh` installs it. Because the
container's writable layer is lost on recreation, keep both in your home
directory and run the script at every container start:

```sh
mkdir -p ~/init.d
cp packaging/init.d/50-romm-sync.sh packaging/supervisor/romm-retroarch-watch.conf ~/init.d/
chmod +x ~/init.d/50-romm-sync.sh
# as root, once now and then at every container start:
~/init.d/50-romm-sync.sh
```

The script fills in the user/home, writes
`/etc/supervisor/conf.d/romm-retroarch-watch.conf`, runs `supervisorctl reread`
and `update`, and is safe to run repeatedly. It assumes supervisord includes
`/etc/supervisor/conf.d/*.conf` and that the script runs as root; override
`SYNC_USER` / `SUPERVISOR_CONF_DIR` / `TEMPLATE` via environment variables if
your container differs. How your container runs scripts from `~/init.d` at
startup is specific to your setup and is not covered here. Logs:
`~/.local/state/romm-retroarch-sync/`.

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
