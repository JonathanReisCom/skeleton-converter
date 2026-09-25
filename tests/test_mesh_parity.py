"""Mesh parity gate tests (fixture-gated: sample rigs live in tmp/).

Unlike the bone-only round-trip gate, this pins the SKIN: every equipped
attachment's vertices must match the ported Spine runtime, UVs must land
inside their region on their own page, and equipping flags must mirror the
runtime's drawing rules.
"""

from pathlib import Path

from src.mesh_parity import mesh_parity

SAMPLES = [
    ("hero", Path("tmp/sample-spine-01/hero-pro.json"),
     Path("tmp/sample-spine-01/hero.atlas")),
    ("mannequin", Path("tmp/003/preview-spine-original/custom assets.json"),
     Path("tmp/003/preview-spine-original/custom assets.atlas")),
]


def test_skin_attachments_match_the_runtime():
    for name, json_path, atlas in SAMPLES:
        if not json_path.exists():
            continue
        violations = mesh_parity(str(json_path), str(atlas))
        assert not violations, f"{name}: {len(violations)} mesh parity violations:\n" + "\n".join(violations)