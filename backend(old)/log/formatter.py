from __future__ import annotations

import colorsys
import hashlib
import logging
import sys
from typing import Optional


def supports_color() -> bool:
    return bool(sys.stdout.isatty())


class ColorFormatter(logging.Formatter):
    """Adds optional ANSI color to level name while keeping deterministic format."""

    _LOGGER_COLOR = "\033[96m"
    _TIMESTAMP_COLOR = "\033[90m"
    _RESET = "\033[0m"

    def __init__(self, use_color: bool, level_colors: Optional[dict[str, str]] = None):
        super().__init__(
            fmt="[%(logger_name)s] [%(asctime)s] [%(component)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self._use_color = use_color
        self._level_colors = level_colors or {}

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        timestamp = super().formatTime(record, datefmt)
        if self._use_color:
            return f"{self._TIMESTAMP_COLOR}{timestamp}{self._RESET}"
        return timestamp

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "component"):
            record.component = "general"  # type: ignore[attr-defined]

        if not hasattr(record, "logger_name"):
            record.logger_name = "Logger"  # type: ignore[attr-defined]

        original_levelname = record.levelname
        original_component = record.component
        original_logger_name = record.logger_name

        if self._use_color:
            color = self._level_colors.get(original_levelname, self._level_colors.get("INFO", ""))
            record.levelname = f"{color}{original_levelname}{self._RESET}"

            # Use stable hash so the same component keeps the same bright color.
            hash_val = int(hashlib.md5(original_component.encode("utf-8")).hexdigest(), 16)
            hue = (hash_val % 360) / 360.0
            r, g, b = colorsys.hls_to_rgb(hue, 0.6, 1.0)

            red, green, blue = int(r * 255), int(g * 255), int(b * 255)
            comp_color = f"\033[38;2;{red};{green};{blue}m"

            record.component = f"{comp_color}{original_component}{self._RESET}"
            record.logger_name = f"{self._LOGGER_COLOR}{original_logger_name}{self._RESET}"

        try:
            return super().format(record)
        finally:
            record.levelname = original_levelname
            record.component = original_component
            record.logger_name = original_logger_name
