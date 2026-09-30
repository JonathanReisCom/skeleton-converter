"""Emit the SkelForm viewer shell: the `.skf` played by SkelForm's own player.

The pane loads the writer's own runtime from a CDN — `skelform-js` (the
generic JS runtime) and the player's `api.js`/`jszip.js` — and fetches the
`.skf` as a file, so what the pane shows is the archive this converter wrote
read by the format's own code, not a replay through another format.

Both are pinned by commit rather than by branch: the player's API is a set of
globals (`SkfInit`, `skfCanvases`) with no version handshake, so a change
upstream would break the pane silently — the same reason the Spine viewer pins
its 4.2 line. Pins were verified with a real render before being written here.

Requires python3 stdlib only.
"""

from __future__ import annotations

from pathlib import Path

from . import hud_assets

TEMPLATE_PATH = Path(__file__).parent / "template_skelform_viewer.html"

PLAYER_COMMIT = "cd7451daea40eec177e6687a4c9a89d523d782ea"
RUNTIME_COMMIT = "9bf4b6c14df5e3e59370606434161c4914d82e25"
PLAYER_BASE = f"https://cdn.jsdelivr.net/gh/Retropaint/skelform-web-player@{PLAYER_COMMIT}"
RUNTIME_BASE = f"https://cdn.jsdelivr.net/gh/Retropaint/skelform-js@{RUNTIME_COMMIT}"
JSZIP_URL = f"{PLAYER_BASE}/jszip.js"
RUNTIME_URL = f"{RUNTIME_BASE}/skelform-js.js"
API_URL = f"{PLAYER_BASE}/api.js"


def render_viewer(skf_url: str) -> str:
    """The SkelForm viewer shell as text (see ``render_viewer`` in viewer_out)."""
    return (TEMPLATE_PATH.read_text(encoding="utf-8")
            .replace("__CDN_JSZIP__", JSZIP_URL)
            .replace("__CDN_RUNTIME__", RUNTIME_URL)
            .replace("__CDN_API__", API_URL)
            .replace("__SKF_URL__", skf_url)
            .replace("__TITLE__", Path(skf_url).name))


def emit_viewer(output_path: str, skf_url: str) -> str:
    """Write the SkelForm viewer shell; it plays ``skf_url``.

    ``skf_url`` is fetched by the browser, so it is a path relative to the
    shell (``output/<name>.skf``) or an absolute URL — never a filesystem
    path. Nothing is embedded: regenerate nothing when the archive changes.
    """
    html = render_viewer(skf_url)
    Path(output_path).write_text(html, encoding="utf-8")
    # The pane is served on its own, so the shared HUD travels with it.
    hud_assets.copy(Path(output_path).parent)
    return output_path
