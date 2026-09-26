"""Install shared native video tools for Hub (including remote vision use)."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    installer = Path(__file__).resolve().parents[2] / "scripts/install_ffmpeg.py"
    runpy.run_path(str(installer))["ensure_ffmpeg"]()
