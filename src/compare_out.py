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

TEMPLATE_PATH = Path(__file__).parent / "template_compare.html"


def _kind(bundle: Path) -> str:
    """godot if the bundle holds a scene wrapper, spine if a JSON+atlas.
    Bundles keep artifacts under output/ (shells at the root), so check
    there too."""
    if any(bundle.glob("*.atlas")) or any(bundle.glob("output/*.atlas")):
        return "spine"
    return "godot"


def emit_compare(*bundles: Path) -> Path:
    """Write compare.html into the parent of both bundles; return its path.

    Accepts exactly 2 bundles (any mix of godot and spine), both under one
    parent folder. Panes are labeled and typed; controls drive each pane
    through its own protocol.
    """
    if len(bundles) != 2:
        raise ValueError("compare takes exactly 2 bundle folders")
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