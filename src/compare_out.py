"""Emit the side-by-side compare shell: one HTML page embedding two preview
bundles as typed panes (Godot wasm and/or Spine viewer) with shared play and
freeze controls.

The shell is written into the PARENT folder of the bundles and served from
there, so every iframe loads its real index.html same-origin and the parent
may drive them through the handles they already publish (previewPlay/
previewFreeze for Godot wrappers, window.__state for Spine viewers). No
assets are copied or duplicated.

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


def emit_compare(*bundles: Path) -> Path:
    """Write compare.html into the parent folder of the bundles; return its path.

    Accepts two or more bundles (any mix of godot, spine and skelform), all
    under one parent folder. The stage lays the panes out across the width in
    the order given, and each is labeled and typed — the studio uses this to
    show the source first and one pane per converted output after it.
    """
    if len(bundles) < 2:
        raise ValueError("compare needs at least 2 bundle folders")
    dirs = [Path(b).resolve() for b in bundles]
    parent = dirs[0].parent
    for d in dirs:
        if d.parent != parent:
            raise ValueError(
                "compare needs all bundles under one parent folder "
                f"(got {[str(d) for d in dirs]})")
    panes = [{"label": d.name, "url": f"{d.name}/index.html",
              "kind": _kind(d)} for d in dirs]
    html = TEMPLATE_PATH.read_text(encoding="utf-8").replace(
        "__PANES__", json.dumps(panes).replace("</", "<\\/"))
    out = parent / "compare.html"
    out.write_text(html, encoding="utf-8")
    return out