import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_file, level="INFO", verbose=False):
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else getattr(logging, level, logging.INFO))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    root.addHandler(console)

    try:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(fh)
    except OSError as exc:
        root.warning("Cannot write log file %s: %s (console only)", log_file, exc)
