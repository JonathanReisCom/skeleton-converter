"""Mesh parity gate tests (fixture-gated: sample rigs live in tmp/).

Unlike the bone-only round-trip gate, this pins the SKIN: every equipped
attachment's vertices must match the ported Spine runtime, UVs must land
inside their region on their own page, and equipping flags must mirror the
runtime's drawing rules.
"""

from pathlib import Path

import json

from src.in_spine import _deform_pairs, read_skeleton
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

def test_an_entry_the_active_skin_leaves_inactive_still_has_geometry(tmp_path):
    """The hero's sword: `skin: true`, its own skin, and no atlas needed.

    Spine's runtime skips a bone the active skin leaves inactive, so its world
    transform stays where it was — the runtime's own ``(0, 0)``. Mirroring that
    literally wrote the attachment as a quad with all four corners at the
    origin: invisible in every converted pane and impossible to equip in a
    viewer. The bone's pose is rig data, so the geometry must not depend on
    which skin happens to be active — only ``equipped`` does.
    """
    rig = {
        "skeleton": {"spine": "4.2.33"},
        "bones": [
            {"name": "root"},
            {"name": "hand", "parent": "root", "x": 40.0, "y": -12.0,
             "rotation": 90.0, "length": 10.0},
            {"name": "weapon", "parent": "hand", "x": 15.0, "y": 1.0,
             "rotation": 77.0, "length": 60.0, "skin": True},
        ],
        "slots": [{"name": "weapon", "bone": "weapon", "attachment": "sword"}],
        "skins": [
            {"name": "default", "attachments": {"weapon": {
                "sword": {"type": "region", "x": 20.0, "y": 0.0,
                          "width": 60.0, "height": 12.0}}}},
            {"name": "armed", "bones": ["weapon"]},
        ],
        "animations": {},
    }
    path = tmp_path / "rig.json"
    path.write_text(json.dumps(rig), encoding="utf-8")

    def sword(skin):
        model = read_skeleton(str(path), skin=skin)
        return next(a for a in model.attachments if a.name == "sword")

    default, armed = sword(None), sword("armed")
    assert not default.equipped, "the sword draws only under its own skin"
    assert armed.equipped
    # Same geometry either way: the skin decides whether the entry DRAWS, not
    # where its vertices are — that is why equipping it in a viewer works.
    assert default.position == armed.position
    assert default.polygon == armed.polygon
    span = (max(p[0] for p in default.polygon)
            - min(p[0] for p in default.polygon))
    assert span > 1.0, "the region quad collapsed to a point"


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
