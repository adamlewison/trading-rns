import logging
import sys

from core.config import load_config


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    log_dir = load_config()["paths"]["log_dir"]
    fh = logging.FileHandler(log_dir / f"{name.replace('.', '_')}.log")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.propagate = False
    return logger
