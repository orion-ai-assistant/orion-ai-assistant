from __future__ import annotations

import logging
import sys

from log.logger import LOG_LEVEL_COLORS
from log.formatter import ColorFormatter, supports_color
from common.design_patterns.singleton import SingletonMeta


class SystemLogger(metaclass=SingletonMeta):
    """Owns the single configured logging.Logger instance for the process."""

    def __init__(self) -> None:
        system_logger = logging.getLogger("orion.system")
        system_logger.setLevel(logging.DEBUG)
        system_logger.propagate = False

        if not system_logger.handlers:
            handler = logging.StreamHandler(stream=sys.stdout)
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(
                ColorFormatter(use_color=supports_color(), level_colors=LOG_LEVEL_COLORS)
            )
            system_logger.addHandler(handler)

        self.logger = system_logger


def get_system_logger() -> logging.Logger:
    return SystemLogger().logger
