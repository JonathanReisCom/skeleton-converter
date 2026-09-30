"""Mesh parity gate tests (fixture-gated: sample rigs live in tmp/).

Unlike the bone-only round-trip gate, this pins the SKIN: every equipped
attachment's vertices must match the ported Spine runtime, UVs must land
inside their region on their own page, and equipping flags must mirror the
runtime's drawing rules.
"""

from pathlib import Path

from src.in_spine import _deform_pairs
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

def test_weighted_deform_offsets_use_the_pair_buffer():
    """A weighted mesh's deform buffer drops bone index and weight.

    Spine flattens the bone entries' (x, y) pairs into the deform buffer, so
    `offset` counts 2 floats per entry while the raw layout costs 4. Reading
    the offset against the raw indices morphed the wrong vertices — hero's head
    moved vertices 1 and 9 while the runtime moved 4, 7, 21, 23 and 24 — and
    single-entry meshes hide it, because there the two indices coincide.
    """
    # Two vertices with two bone entries each: [count, idx, x, y, w] per entry.
    raw = [
        2, 5, 10.0, 20.0, 0.75, 6, 30.0, 40.0, 0.25,
        2, 5, 11.0, 21.0, 1.0, 6, 31.0, 41.0, 0.0,
    ]
    # Pairs run bone 5 then bone 6 for vertex 0, then the same for vertex 1, so
    # offset 2 is vertex 0's SECOND entry — bone 6, weight 0.25. The raw layout
    # would land on vertex 0's first entry instead (bone 5, weight 0.75).
    touched = _deform_pairs(raw, 2, [7.0, -3.0], True, "host")

    assert sorted(touched) == [0], touched
    assert touched[0] == [(7.0, -3.0, 6, 0.25)], touched[0]


def test_one_vertex_can_carry_two_touched_bones():
    """Each touched bone entry keeps its own delta and weight."""
    raw = [
        2, 5, 10.0, 20.0, 0.75, 6, 30.0, 40.0, 0.25,
    ]
    # Pairs 0-1 are bone 5's local, 2-3 are bone 6's.
    touched = _deform_pairs(raw, 0, [1.0, 2.0, 3.0, 4.0], True, "host")

    assert touched[0] == [(1.0, 2.0, 5, 0.75), (3.0, 4.0, 6, 0.25)], touched[0]
