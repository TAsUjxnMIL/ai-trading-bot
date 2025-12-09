# Zentrale Logging Funktionen für alle anderen Module
# src/utils/logger.py
import logging
import sys

# Einfacher globaler Logger für dein Projekt
logger = logging.getLogger("trading_bot")
logger.setLevel(logging.INFO)

# Nur einmal Handler hinzufügen (sonst doppelte Logs bei reload)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
