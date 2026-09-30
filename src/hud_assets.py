"""The shared HUD a viewer needs beside it.

The three browser viewers share one stylesheet and one script (see
``static/hud.js``), and an emitted pane folder is served on its own — it cannot
link back to this repo — so every writer copies the pair next to the
``index.html`` it writes. A viewer that finds them missing says so instead of
rendering an empty pane.
"""

from __future__ import annotations

import shutil
from pathlib import Path

STATIC = Path(__file__).parent / "static"
ASSETS = ("hud.css", "hud.js")


def read(name: str) -> bytes:
    """One asset's bytes, for a caller that serves it live (the studio)."""
    return (STATIC / name).read_bytes()


def copy(dest_dir: Path | str) -> list[str]:
    """Copy the HUD assets into ``dest_dir``; return the names written."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        shutil.copy2(STATIC / name, dest / name)
    return list(ASSETS)
