"""Regenerate orion.ico from the Orion logo (developer tool; needs Pillow, the build does not).

    python scripts/release/launcher/make_icon.py

The emblem (without the wordmark) is cropped from installer/app/ui/favicon.png and stored as
PNG-in-ICO at every size Windows asks for. orion.ico is committed, so releases never need Pillow.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
SIZES = (16, 24, 32, 48, 64, 128, 256)

logo = Image.open(ROOT / 'installer/app/ui/favicon.png').convert('RGBA')
emblem = logo.crop((222, 150, 802, 730))  # square around the octagon, wordmark excluded
emblem.save(Path(__file__).with_name('orion.ico'), format='ICO', sizes=[(s, s) for s in SIZES])
