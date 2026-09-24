"""Emit a reusable HTML viewer shell that plays a Spine skeleton.

The HTML is a thin, generic shell: it loads spine-webgl from the CDN and reads
three sibling files — <name>.json, <name>.atlas, and the image the atlas
declares. No assets are embedded; regenerate nothing when the assets change.

Requires python3 stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

# The HTML shell lives beside this module as a real file (template_spine_
# viewer.html), so editors and language tools lint the markup/JS directly
# instead of a python string.
TEMPLATE_PATH = Path(__file__).parent / "template_spine_viewer.html"

# Pinned to the 4.2 line on purpose: the shell drives the SpineCanvas *app* API
# (app.loadAssets / app.renderer / app.assetManager), which 4.3 removed —
# SpineCanvas.prototype there exposes only clear() and dispose(). Bumping this
# to a 4.3.x build loads the skeleton and reports no error, then renders zero
# pixels, so the breakage is silent. Unrelated to the data version written by
# out_spine.py (4.3.26): a 4.2 runtime reads 4.3 data fine.
SPINE_WEBGL_VERSION = "4.2.120"
RUNTIME_URL = f"https://unpkg.com/@esotericsoftware/spine-webgl@{SPINE_WEBGL_VERSION}/dist/iife/spine-webgl.js"

def _find_atlas(json_path: Path) -> str:
    """The .atlas to load for this skeleton.

    Its name does not always mirror the JSON's — the official hero rig ships
    hero-pro.json beside hero.atlas — so prefer a same-stem sibling, then the
    only .atlas in the folder.
    """
    sibling = json_path.with_suffix(".atlas")
    if sibling.exists():
        return sibling.name
    candidates = sorted(json_path.parent.glob("*.atlas"))
    return candidates[0].name if len(candidates) == 1 else ""


def emit_viewer(output_path: str, skeleton_json_path: str | None = None,
                skeleton_url: str | None = None,
                atlas_url: str | None = None) -> str:
    """Write the reusable viewer shell.

    The shell loads the skeleton JSON, its .atlas, and the atlas image from
    ``skeleton_url``/``atlas_url`` (defaults: the skeleton's own name beside
    the shell, or the skeleton named by ``skeleton_json_path``). Relative URLs
    like ``../animation.json`` are legal — the shell fetches them as given, so
    a preview folder can reference assets one level up instead of duplicating
    them. Nothing is embedded; when assets change, just refresh the page.
    """
    if skeleton_json_path:
        json_path = Path(skeleton_json_path)
        skeleton_json = json.loads(json_path.read_text(encoding="utf-8"))
        animations = list(skeleton_json.get("animations", {}).keys())
        first = animations[0] if animations else ""
        default_skeleton = skeleton_url or json_path.name
        default_atlas = atlas_url or _find_atlas(json_path)
    else:
        first = ""
        default_skeleton = ""
        default_atlas = ""
    html = (
        TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("__RUNTIME_URL__", RUNTIME_URL)
        .replace("__FIRST_ANIMATION__", first)
        .replace("__DEFAULT_SKELETON__", default_skeleton)
        .replace("__DEFAULT_ATLAS__", default_atlas)
    )
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
