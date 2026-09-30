"""Render the compare shell: one HTML page embedding N preview bundles as typed
panes (Godot wasm, Spine viewer, SkelForm player) with every control in a left
sidebar.

The shell is rendered, not written: the studio calls ``render_compare`` on each
request so the chrome follows the code while the conversions stay frozen. The
bundles must sit under one parent, because every pane URL is relative to it and
one origin is what lets the shell reach into the iframes and drive them through
the handles they publish (previewPlay/previewFreeze for the Godot wrapper and
the SkelForm player, window.__state for the Spine viewer).

Requires python3 stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import detect

TEMPLATE_PATH = Path(__file__).parent / "template_compare.html"


def _kind(bundle: Path) -> str:
    """Which runtime plays this bundle. Bundles keep artifacts under output/
    (their shell sits at the root), so check there too.

    A skeleton JSON is a Spine pane whether or not its atlas came along: a rig
    uploaded without its atlas still renders through the Spine viewer, and
    typing that pane "godot" would drive it with the wrong protocol and leave
    the shell waiting forever.
    """
    dirs = [bundle, bundle / "output"]
    if any(path for d in dirs for path in d.glob("*.atlas")):
        return "spine"
    if any(path for d in dirs for path in d.glob("*.skf")):
        return "skelform"
    for d in dirs:
        for candidate in sorted(d.glob("*.json")):
            if detect.detect_format(candidate) == "spine":
                return "spine"
    return "godot"


def render_compare(*bundles: Path) -> str:
    """The compare shell as text (see ``render_viewer`` in viewer_out)."""
    if len(bundles) < 2:
        raise ValueError("compare needs at least 2 bundle folders")
    panes = []
    for bundle in bundles:
        kind = _kind(bundle)
        panes.append({"label": bundle.name, "url": f"{bundle.name}/index.html",
                      "kind": kind})
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    # `</` closes the inline script it sits in, so escape it before injecting.
    return html.replace("__PANES__", json.dumps(panes).replace("</", "<\\/"))
