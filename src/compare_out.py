"""Emit the side-by-side compare shell: one HTML page embedding the two
preview bundles (Godot wasm + Spine viewer) in iframes with shared controls.

The shell is written into the PARENT folder of the two output bundles and
served from there, so both iframes load their real index.html same-origin
and the parent may drive them through the handles they already publish
(previewPlay/previewFreeze for the Godot wrapper, window.__state for the
Spine viewer). No assets are copied or duplicated.

Requires python3 stdlib only.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_PATH = Path(__file__).parent / "template_compare.html"


def emit_compare(godot_dir: Path, spine_dir: Path) -> Path:
    """Write compare.html into the parent of both bundles; return its path."""
    godot_dir = Path(godot_dir).resolve()
    spine_dir = Path(spine_dir).resolve()
    parent = godot_dir.parent
    if spine_dir.parent != parent:
        raise ValueError(
            f"compare needs both bundles under one parent folder "
            f"(got {godot_dir} and {spine_dir})")
    html = (TEMPLATE_PATH.read_text(encoding="utf-8")
            .replace("__GODOT_URL__", f"{godot_dir.name}/index.html")
            .replace("__SPINE_URL__", f"{spine_dir.name}/index.html"))
    out = parent / "compare.html"
    out.write_text(html, encoding="utf-8")
    return out
