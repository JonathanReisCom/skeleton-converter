"""CI gates that never touch tests/fixtures/: both directions plus mesh parity.

``tests/fixtures/`` holds third-party sample rigs and is deliberately not
committed, so the fixture-based numeric round-trips (``test_roundtrip_godot``,
``test_cross_convert``) skip in CI — the strongest gates never ran. These gates
synthesize their own rig (``tests/ci_rig.py``) and assert the same contracts:

- Spine JSON -> model -> ``.tscn`` -> model, numerically;
- ``.tscn`` -> model -> Spine JSON -> model, the same way;
- ``src.mesh_parity`` over the synthesized rig — the geometric gate the numeric
  round-trip cannot replace, because bones can agree to 0.000000 while the skin
  is wrong.

Tolerance is the project's: < 0.01 units on every bone and vertex (AGENTS.md,
"Never trust a conversion by inspection").
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from src.in_godot import read_godot_skeleton
from src.in_spine import read_skeleton
from src.mesh_parity import mesh_parity
from src.model import godot_world_transforms
from src.out_godot import write_godot_scene
from src.out_spine import write_spine_json
from tests.ci_rig import PAGE, STEM, write_spine_export

TOLERANCE = 1e-2

# Channels the .tscn leg carries today, and so the ones these gates assert.
# Spine scale tracks are a ROADMAP item ("Spine `scale` animation tracks are
# dropped"): the synthesized rig still carries one — real exports do, and no
# reader may choke on the key — but it is not asserted here.
COMPARED_CHANNELS = ("rotate", "translate")


def _spine_model(directory: Path):
    """The synthesized rig on disk, read through the Spine adapter."""
    json_path = write_spine_export(directory)
    return read_skeleton(str(json_path), str(directory / f"{STEM}.atlas"))


def _godot_model(directory: Path):
    """spine -> model -> .tscn -> model: returns (source, reloaded)."""
    source = _spine_model(directory)
    scene = directory / f"{STEM}.tscn"
    write_godot_scene(source, str(scene), texture_path=f"res://{PAGE}")
    return source, read_godot_skeleton(str(scene))


def _key(att) -> tuple:
    return ((att.slot or att.name).lower(), att.name.lower())


def _skeleton_points(model) -> dict:
    """(slot, attachment) -> skeleton-space vertices (node-local + position)."""
    return {
        _key(att): [(point[0] + att.position[0] + att.offset[0],
                     point[1] + att.position[1] + att.offset[1])
                    for point in att.polygon]
        for att in model.attachments
    }


def _equipped(model) -> dict:
    return {_key(att): att.equipped for att in model.attachments}


def _animation_values(model) -> dict:
    """(animation, bone, channel) -> [(time, value), ...] in Godot-space units."""
    values = {}
    for animation, tracks in model.animations.items():
        for bone, props in tracks.items():
            for channel, keys in props.items():
                if channel not in COMPARED_CHANNELS:
                    continue
                values[(animation, bone, channel)] = [
                    (key.time, key.angle if channel == "rotate"
                     else (key.x, key.y))
                    for key in keys
                ]
    return values


def _worst_bone(source, reloaded) -> float:
    """Worst absolute difference across every world matrix entry."""
    first = godot_world_transforms(source)
    second = godot_world_transforms(reloaded)
    assert set(first) == set(second), sorted(set(first) ^ set(second))
    return max(
        abs(first[name][index] - second[name][index])
        for name in first for index in range(6)
    )


def _assert_same_geometry(source, reloaded) -> None:
    first, second = _skeleton_points(source), _skeleton_points(reloaded)
    assert set(first) == set(second), sorted(set(first) ^ set(second))
    for key, points in first.items():
        assert len(points) == len(second[key]), (key, points, second[key])
        for index, point in enumerate(points):
            other = second[key][index]
            deviation = math.hypot(point[0] - other[0], point[1] - other[1])
            assert deviation < TOLERANCE, (key, index, point, other)


def _assert_same_animations(source, reloaded) -> None:
    first, second = _animation_values(source), _animation_values(reloaded)
    assert set(first) == set(second), sorted(set(first) ^ set(second))
    for key, keys in first.items():
        assert len(keys) == len(second[key]), (key, keys, second[key])
        for (time, value), (other_time, other_value) in zip(keys, second[key]):
            assert math.isclose(time, other_time, abs_tol=TOLERANCE), \
                (key, time, other_time)
            if isinstance(value, tuple):
                deviation = math.hypot(value[0] - other_value[0],
                                       value[1] - other_value[1])
                assert deviation < TOLERANCE, (key, value, other_value)
            else:
                assert math.isclose(value, other_value, abs_tol=TOLERANCE), \
                    (key, value, other_value)


def test_spine_to_godot_roundtrip_agrees_numerically(tmp_path):
    """Spine JSON -> model -> .tscn -> model keeps bones, skin, and keys."""
    source, reloaded = _godot_model(tmp_path)

    assert len(source.bones) == 3 and len(reloaded.bones) == 3
    assert _worst_bone(source, reloaded) < TOLERANCE
    _assert_same_geometry(source, reloaded)
    # The equipping flags are a drawing contract, not decoration: an attachment
    # the source never equips must stay hidden (Polygon2D visible=false).
    assert _equipped(source) == _equipped(reloaded)
    _assert_same_animations(source, reloaded)


def _back_to_spine(scene_model, directory: Path):
    """.tscn model -> Spine JSON -> model."""
    json_path = directory / "back.json"
    write_spine_json(scene_model, str(json_path), image_name=PAGE,
                     image_path=str(directory / PAGE))
    return read_skeleton(str(json_path), None)


def test_godot_to_spine_roundtrip_agrees_numerically(tmp_path):
    """.tscn -> model -> Spine JSON -> model keeps bones, keys, and the skin.

    The skin's inventory is asserted here (every attachment survives, with its
    slot and vertex count); its geometry is checked by
    ``test_godot_to_spine_keeps_attachment_geometry``, which records a known
    defect instead of passing over it.
    """
    _, scene_model = _godot_model(tmp_path)
    reloaded = _back_to_spine(scene_model, tmp_path)

    assert len(reloaded.bones) == len(scene_model.bones)
    assert _worst_bone(scene_model, reloaded) < TOLERANCE
    first, second = _skeleton_points(scene_model), _skeleton_points(reloaded)
    assert set(first) == set(second), sorted(set(first) ^ set(second))
    for key, points in first.items():
        assert len(points) == len(second[key]), (key, points, second[key])
    _assert_same_animations(scene_model, reloaded)


# This gate found a real defect the moment it ran: ``out_spine`` stores mesh
# locals against ``mirror_world(godot_rest_worlds(model))``, and
# ``godot_rest_worlds`` was chaining Godot's Transform2D *literal* order
# (xx, xy, yx, yy) through ``multiply``, which takes a standard row-major
# matrix — so the inverted basis was the TRANSPOSE of the runtime's and every
# rotated bone landed its attachment geometry at twice its rest rotation (up
# to 8.64 units; the demo rig hides it because its Bone2D rests are identity,
# while this rig's rests carry the angles, like any scene our writer emits).
# Fixed in ``godot_rest_worlds`` (now compose-based every step). Keep this
# gate: it is the only thing in CI that covers the reverse leg's geometry.
def test_godot_to_spine_keeps_attachment_geometry(tmp_path):
    _, scene_model = _godot_model(tmp_path)
    _assert_same_geometry(scene_model, _back_to_spine(scene_model, tmp_path))


def test_mesh_parity_passes_on_the_synthesized_rig(tmp_path):
    """The geometric gate: every vertex, UV, and equipping flag, per skin."""
    json_path = write_spine_export(tmp_path)
    model = read_skeleton(str(json_path), str(tmp_path / f"{STEM}.atlas"))

    # A vacuous gate would pass over an empty rig.
    assert len(model.attachments) == 4
    assert not all(att.equipped for att in model.attachments)

    violations = mesh_parity(str(json_path), str(tmp_path / f"{STEM}.atlas"),
                             model)
    assert not violations, "\n".join(violations)


def test_mesh_parity_gate_catches_a_corrupted_vertex(tmp_path):
    """The gate must not be green by vacuity: a 1-unit vertex error fails it."""
    json_path = write_spine_export(tmp_path)
    atlas = str(tmp_path / f"{STEM}.atlas")
    model = read_skeleton(str(json_path), atlas)
    vertex = model.attachments[0].polygon[0]
    model.attachments[0].polygon[0] = (vertex[0] + 1.0, vertex[1])

    violations = mesh_parity(str(json_path), atlas, model)
    assert violations, "a 1-unit vertex error went undetected"
