import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging(level: str = "INFO") -> logging.Logger:
    os.makedirs("logs", exist_ok=True)
    logger = logging.getLogger("pve_node_monitor")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = RotatingFileHandler("logs/pve_monitor.log", maxBytes=3_000_000, backupCount=5)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger