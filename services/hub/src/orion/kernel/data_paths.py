"""Mutable Hub paths in a source or registered package installation."""
from pathlib import Path
from services.shared.runtime_layout import data_root


def hub_data():
    return data_root(Path(__file__).resolve().parents[5]) / 'services/hub/data'
