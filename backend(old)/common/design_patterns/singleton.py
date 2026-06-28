from __future__ import annotations

import threading
from typing import Any


class SingletonMeta(type):
    """Thread-safe singleton metaclass for shared infrastructure objects."""

    _instances: dict[type, Any] = {}
    _lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        if cls in cls._instances:
            return cls._instances[cls]

        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super().__call__(*args, **kwargs)

        return cls._instances[cls]
