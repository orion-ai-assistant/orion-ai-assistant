"""Mutable Hub paths in a source or registered package installation."""
from pathlib import Path
from services.shared.runtime_layout import data_root


def install_root():
    """The checkout or release folder; None in a container (/app/src/orion/...)."""
    parents = Path(__file__).resolve().parents
    return parents[5] if len(parents) > 5 else None


def hub_data():
    root = install_root()
    if root is None:
        # The container's /app is the Hub service folder itself.
        return Path(__file__).resolve().parents[3] / 'data'
    return data_root(root) / 'services/hub/data'
