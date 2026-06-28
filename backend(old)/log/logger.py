import logging
from enum import Enum
from typing import Callable, Union

from log.registry import _register_component, normalize_component


class LogLevel(int, Enum):
    DEBUG = 10
    STREAM = 15
    INFO = 20
    WARNING = 30
    ERROR = 40

    @property
    def name_str(self) -> str:
        return self.name.upper()


# Register all internal LogLevels with the standard logging module
# so %(levelname)s shows the name "STREAM" instead of "Level 15".
for level_member in LogLevel:
    logging.addLevelName(level_member.value, level_member.name)


LOG_LEVEL_COLORS = {
    LogLevel.DEBUG.name: "\033[94m",
    LogLevel.STREAM.name: "\033[95m",
    LogLevel.INFO.name: "\033[92m",
    LogLevel.WARNING.name: "\033[93m",
    LogLevel.ERROR.name: "\033[91m",
}


def _resolve_log_message(message: str | Callable[[], str]) -> str:
    if callable(message):
        return str(message())
    return str(message)


def _emit_system_log(level_enum: LogLevel, message: str | Callable[[], str], component: str, *args: object, **kwargs: object) -> None:
    from log.core import get_system_logger

    logger = get_system_logger()
    extra = {"component": component}
    text = _resolve_log_message(message)

    # All levels (including custom ones) are now handled uniformly with logger.log(level_value, message)
    logger.log(level_enum.value, text, *args, extra=extra, **kwargs)


def system_log(
    message: str | Callable[[], str],
    component: str = "general",
    level: Union[LogLevel, str] = LogLevel.INFO,
    *args: object,
    **kwargs: object,
) -> None:
    """Emit a log message if logging is enabled for this component."""
    from log.settings import components_to_dict, get_configured_logging, should_log

    component = normalize_component(component)

    if isinstance(level, LogLevel):
        level_enum = level
    else:
        normal = str(level).upper()
        level_enum = LogLevel[normal] if normal in LogLevel.__members__ else LogLevel.INFO

    if not should_log(level_enum):
        return

    config = get_configured_logging()
    components = components_to_dict(config.components)
    if not components.get(component, True):
        return

    _emit_system_log(level_enum, message, component, *args, **kwargs)


class Logger:
    """Lightweight logger API: logger.debug/info/warning/error"""

    def __init__(self, component: str = "general"):
        self.component = normalize_component(component)
        _register_component(self.component)

    def debug(self, message: str | Callable[[], str], *args: object, **kwargs: object) -> None:
        system_log(message, component=self.component, level=LogLevel.DEBUG, *args, **kwargs)

    def stream(self, message: str | Callable[[], str], *args: object, **kwargs: object) -> None:
        system_log(message, component=self.component, level=LogLevel.STREAM, *args, **kwargs)

    def info(self, message: str | Callable[[], str], *args: object, **kwargs: object) -> None:
        system_log(message, component=self.component, level=LogLevel.INFO, *args, **kwargs)

    def warning(self, message: str | Callable[[], str], *args: object, **kwargs: object) -> None:
        system_log(message, component=self.component, level=LogLevel.WARNING, *args, **kwargs)

    def error(self, message: str | Callable[[], str], *args: object, **kwargs: object) -> None:
        system_log(message, component=self.component, level=LogLevel.ERROR, *args, **kwargs)


def get_component_logger(component: str = "general") -> Logger:
    """Factory API for modules that prefer an explicit logger acquisition style."""
    return Logger(component=component)
