"""Emit the DragonBones viewer shell: the written `_ske.json` played by
DragonBones' own runtime.

The pane loads the format's own runtime from a CDN — DragonBonesJS' combined
core + Pixi 8 host bundle, which carries both the data parser and
`dragonBones.PixiFactory` — and fetches the three files a DragonBones bundle
holds (`<name>_ske.json`, `<name>_tex.json`, the page image), so what the pane
shows is the written bundle read by the format's own code, not a replay through
another format.

The runtime is pinned by commit rather than by branch: its API is a set of
globals (`dragonBones.PixiFactory.factory`, `armature.animation.play`) with no
version handshake, so a change upstream would break the pane silently — the same
reason the Spine viewer pins its 4.2 line. The pin was verified with a real
render before being written here.

Requires python3 stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import hud_assets

TEMPLATE_PATH = Path(__file__).parent / "template_dragonbones_viewer.html"

# DragonBonesJS master, pinned by commit.
RUNTIME_COMMIT = "64b6c69ae35777c2404be68c9192e2c56906079e"
# The 5.x host, not the 8.x one: a WEIGHTED mesh (every attachment this writer
# emits carries `slotPose`/`bonePose`/`weights`) is drawn from the vertices the
# runtime recomputes each frame, and the 8.x port leaves that buffer at the
# pose it was first built with — a frozen, scattered rig. Measured on the same
# bundle: with 5.x the skinned vertices track the model within 0.3 units at
# t=0.5s, with 8.x they never change. Pixi 5 is the line this runtime was
# written against and is still the one its Pixi demos build for.
RUNTIME_URL = (f"https://cdn.jsdelivr.net/gh/DragonBones/DragonBonesJS@{RUNTIME_COMMIT}"
               "/Pixi/5.x/out/dragonBones.js")
PIXI_URL = "https://cdn.jsdelivr.net/npm/pixi.js@5.3.12/dist/pixi.min.js"


def _js(value: str) -> str:
    """A python string as the inside of a JS double-quoted literal.

    The URLs land inside string literals in the template, and a bundle name is
    whatever the caller passed as ``--name``; ``json.dumps`` escapes a quote or
    a backslash that a bare ``str.replace`` would ship as broken script.
    """
    return json.dumps(value)[1:-1]


def render_viewer(ske_url: str, tex_url: str, page_url: str) -> str:
    """The DragonBones viewer shell as text, so a caller can serve it live.

    The three URLs are what the browser fetches, so they are paths relative to
    the shell (``output/<name>_ske.json``, ``output/<name>_tex.json``,
    ``output/<name>.png``) or absolute URLs — never filesystem paths. Nothing is
    embedded: regenerate the data and just refresh the page.
    """
    return (TEMPLATE_PATH.read_text(encoding="utf-8")
            .replace("__CDN_PIXI__", PIXI_URL)
            .replace("__CDN_RUNTIME__", RUNTIME_URL)
            .replace("__SKE_URL__", _js(ske_url))
            .replace("__TEX_URL__", _js(tex_url))
            .replace("__PAGE_URL__", _js(page_url))
            .replace("__TITLE__", _js(Path(ske_url).name)))


def emit_viewer(output_path: str, ske_url: str, tex_url: str, page_url: str) -> str:
    """Write the DragonBones viewer shell; it plays the three sibling files.

    Same contract as the other viewers: the shell is static, path-agnostic and
    embeds no data, and the shared HUD travels beside it because an emitted
    bundle folder is served on its own.
    """
    html = render_viewer(ske_url, tex_url, page_url)
    Path(output_path).write_text(html, encoding="utf-8")
    hud_assets.copy(Path(output_path).parent)
    return output_path