import configparser
import os
import socket
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SAVES_DIR = "~/.var/app/org.libretro.RetroArch/config/retroarch/saves"
DEFAULT_STATES_DIR = "~/.var/app/org.libretro.RetroArch/config/retroarch/states"

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("ROMM_SYNC_CONFIG")
    or Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    / "romm-retroarch-sync"
    / "config.ini"
).expanduser()

DEFAULT_STATE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    / "romm-retroarch-sync"
)

CONFLICT_POLICIES = ("newer", "local", "server")


class ConfigError(RuntimeError):
    pass


@dataclass
class WatchConfig:
    enabled: bool
    poll_interval: float
    idle_sync_interval: float
    failure_threshold: int
    udp_host: str
    udp_port: int
    retroarch_cfg: Path


@dataclass
class Config:
    path: Path
    url: str
    token: str
    device_name: str
    saves_dir: Path
    states_dir: Path
    conflict_policy: str
    backup_count: int
    log_file: Path
    log_level: str
    state_file: Path
    watch: WatchConfig


def _load_watch(parser):
    def num(key, default, cast, minimum):
        try:
            value = cast(parser.get("watch", key, fallback=default))
        except ValueError:
            raise ConfigError(f"[watch] {key} must be a number")
        if value < minimum:
            raise ConfigError(f"[watch] {key} must be >= {minimum}")
        return value

    try:
        enabled = parser.getboolean("watch", "enabled", fallback=True)
    except ValueError:
        raise ConfigError("[watch] enabled must be true or false")
    port = num("udp_port", "55355", int, 1)
    if port > 65535:
        raise ConfigError("[watch] udp_port must be <= 65535")
    cfg_path = parser.get("watch", "retroarch_cfg", fallback="").strip()
    return WatchConfig(
        enabled=enabled,
        poll_interval=num("poll_interval", "1", float, 0.1),
        idle_sync_interval=num("idle_sync_interval", "900", float, 1),
        failure_threshold=num("failure_threshold", "3", int, 1),
        udp_host=parser.get("watch", "udp_host", fallback="127.0.0.1").strip(),
        udp_port=port,
        retroarch_cfg=Path(cfg_path).expanduser() if cfg_path else None,
    )


def load_config(path=None):
    path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Run `romm-sync pair --url <romm-url> --code <pairing-code>` "
            "or create it from config.example.ini."
        )

    parser = configparser.ConfigParser()
    parser.read(path)

    def get(section, key, default=None):
        return parser.get(section, key, fallback=default)

    url = get("romm", "url")
    if not url:
        raise ConfigError(f"Missing [romm] url in {path}")
    token = get("romm", "token")
    if not token:
        raise ConfigError(
            f"Missing [romm] token in {path}. "
            f"Run `romm-sync pair --url {url} --code <pairing-code>`."
        )

    policy = get("sync", "conflict_policy", "newer").strip().lower()
    if policy not in CONFLICT_POLICIES:
        raise ConfigError(
            f"Invalid [sync] conflict_policy {policy!r}; use one of {CONFLICT_POLICIES}"
        )

    try:
        backup_count = int(get("sync", "backup_count", "3"))
        if backup_count < 0:
            raise ValueError
    except ValueError:
        raise ConfigError("[sync] backup_count must be an integer >= 0 (0 = off)")

    return Config(
        path=path,
        url=url.rstrip("/"),
        token=token.strip(),
        device_name=get("romm", "device_name") or socket.gethostname(),
        saves_dir=Path(get("retroarch", "saves_dir", DEFAULT_SAVES_DIR)).expanduser(),
        states_dir=Path(get("retroarch", "states_dir", DEFAULT_STATES_DIR)).expanduser(),
        conflict_policy=policy,
        backup_count=backup_count,
        log_file=Path(
            get("logging", "file", str(DEFAULT_STATE_DIR / "sync.log"))
        ).expanduser(),
        log_level=get("logging", "level", "INFO").strip().upper(),
        state_file=Path(
            get("state", "file", str(DEFAULT_STATE_DIR / "state.json"))
        ).expanduser(),
        watch=_load_watch(parser),
    )


def write_token(path, url, token, device_name=None):
    """Store url/token in the config, creating it with defaults if missing."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path)

    for section in ("romm", "retroarch", "sync", "logging"):
        if not parser.has_section(section):
            parser.add_section(section)

    parser.set("romm", "url", url.rstrip("/"))
    parser.set("romm", "token", token)
    if device_name:
        parser.set("romm", "device_name", device_name)
    if not parser.has_option("retroarch", "saves_dir"):
        parser.set("retroarch", "saves_dir", DEFAULT_SAVES_DIR)
    if not parser.has_option("retroarch", "states_dir"):
        parser.set("retroarch", "states_dir", DEFAULT_STATES_DIR)
    if not parser.has_option("sync", "conflict_policy"):
        parser.set("sync", "conflict_policy", "newer")
    if not parser.has_option("sync", "backup_count"):
        parser.set("sync", "backup_count", "3")
    if not parser.has_option("logging", "level"):
        parser.set("logging", "level", "INFO")

    with open(path, "w") as f:
        parser.write(f)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
