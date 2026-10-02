"""DragonBones round trip: `_ske.json` -> model -> `_ske.json`, verified numerically.

Fixture-free by necessity: the repository ships no third-party rigs, so this
builds a small armature in code and asserts what the runtime would observe —
bone world transforms, mesh geometry, UVs, weights and the sampled value of
every animation channel. The document follows the runtime's parser
(`ObjectDataParser`) rather than the exporter's habits: `skX`/`skY` are the
absolute skew and rotation columns, a weighted mesh carries `slotPose` +
`bonePose`, and frames carry integer durations.
"""
import json
import math

from src.in_dragonbones import read_skeleton
from src.model import compose, godot_world_transforms, transform
from src.out_dragonbones import write_dragonbones

TOLERANCE = 0.01
ARM_ANGLE = 30.0


def _tex() -> dict:
    return {
        "name": "rig",
        "imagePath": "rig.png",
        "width": 64,
        "height": 64,
        "SubTexture": [
            {"name": "body", "x": 0, "y": 0, "width": 16, "height": 8},
            {"name": "arm", "x": 16, "y": 0, "width": 8, "height": 8},
        ],
    }


def _skeleton() -> dict:
    """Two bones, a rotated image display and a mesh skinned to the arm bone."""
    arm_world = [math.cos(math.radians(ARM_ANGLE)),
                 math.sin(math.radians(ARM_ANGLE)),
                 -math.sin(math.radians(ARM_ANGLE)),
                 math.cos(math.radians(ARM_ANGLE)), 20.0, 10.0]
    return {
        "frameRate": 30,
        "name": "rig",
        "version": "5.5",
        "compatibleVersion": "5.5",
        "armature": [{
            "type": "Armature",
            "frameRate": 30,
            "name": "rig",
            "bone": [
                {"name": "root"},
                {"name": "arm", "parent": "root",
                 "transform": {"x": 20.0, "y": 10.0,
                               "skX": ARM_ANGLE, "skY": ARM_ANGLE}},
            ],
            "slot": [
                {"name": "body", "parent": "root", "displayIndex": 0},
                {"name": "arm", "parent": "arm", "displayIndex": 0},
            ],
            "skin": [{"name": "default", "slot": [
                {"name": "body", "display": [{
                    "type": "image", "name": "body", "path": "body",
                    "pivot": {"x": 0.0, "y": 0.0},
                    "transform": {"x": 4.0, "y": 2.0, "skX": 15.0, "skY": 15.0},
                }]},
                {"name": "arm", "display": [{
                    "type": "mesh", "name": "arm-mesh", "path": "arm",
                    "vertices": [-4.0, -4.0, 4.0, -4.0, 4.0, 4.0, -4.0, 4.0],
                    "uvs": [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0],
                    "triangles": [0, 1, 2, 0, 2, 3],
                    "slotPose": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
                    "bonePose": [1] + arm_world,
                    "weights": [1, 1, 1.0, 1, 1, 1.0, 1, 1, 1.0, 1, 1, 1.0],
                }]},
            ]}],
            "animation": [{
                "duration": 30,
                "playTimes": 0,
                "name": "idle",
                "bone": [{
                    "name": "arm",
                    "rotateFrame": [
                        {"duration": 15, "rotate": 10.0, "tweenEasing": 0},
                        {"duration": 15, "rotate": 40.0},
                    ],
                }],
                "slot": [{"name": "body",
                          "displayFrame": [{"duration": 30, "value": 0}]}],
            }],
        }],
    }


def _write(directory, name="rig", document=None) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}_tex.json").write_text(json.dumps(_tex()), encoding="utf-8")
    path = directory / f"{name}_ske.json"
    path.write_text(json.dumps(document or _skeleton()), encoding="utf-8")
    return str(path)


def _worlds(model) -> dict:
    return godot_world_transforms(model)


def _geometry(model) -> dict:
    """(world vertices, uvs, weights) per attachment, through its anchor."""
    out = {}
    for attachment in model.attachments:
        world = compose(attachment.position, 0.0)
        points = [transform(world, (point[0] + attachment.offset[0],
                                    point[1] + attachment.offset[1]))
                  for point in attachment.polygon]
        out[(attachment.slot, attachment.name)] = (
            points, [tuple(uv) for uv in attachment.uv],
            sorted((bone, tuple(round(w, 6) for w in weights))
                   for bone, weights in attachment.weights))
    return out


def test_the_round_trip_keeps_every_bone_where_the_source_put_it(tmp_path):
    source = read_skeleton(_write(tmp_path / "in"))
    again = read_skeleton(write_and_read(tmp_path, source))
    first, second = _worlds(source), _worlds(again)
    assert set(first) == set(second) == {"root", "arm"}
    for name in first:
        assert max(abs(a - b) for a, b in zip(first[name], second[name])) < TOLERANCE


def write_and_read(tmp_path, model) -> str:
    """Write ``model`` as a DragonBones bundle and read it back."""
    out = tmp_path / "out" / "rig_ske.json"
    write_dragonbones(model, str(out), image_name="rig.png",
                      image_path=str(tmp_path / "in" / "rig.png"))
    return str(out)


def test_the_round_trip_keeps_the_geometry_the_uvs_and_the_weights(tmp_path):
    source = read_skeleton(_write(tmp_path / "in"))
    again = read_skeleton(write_and_read(tmp_path, source))
    first, second = _geometry(source), _geometry(again)
    assert set(first) == set(second) == {("body", "body"), ("arm", "arm-mesh")}
    for key in first:
        points_a, uvs_a, weights_a = first[key]
        points_b, uvs_b, weights_b = second[key]
        assert len(points_a) == len(points_b), key
        # The corner order is the reader's, not the source's, so each point is
        # paired with its own UV before comparing: a writer that permuted the
        # quad would still pass a per-index comparison of the point list.
        pairs_a = sorted((round(p[0], 6), round(p[1], 6), round(u[0], 6), round(u[1], 6))
                         for p, u in zip(points_a, uvs_a))
        pairs_b = sorted((round(p[0], 6), round(p[1], 6), round(u[0], 6), round(u[1], 6))
                         for p, u in zip(points_b, uvs_b))
        for a, b in zip(pairs_a, pairs_b):
            assert max(abs(x - y) for x, y in zip(a, b)) < TOLERANCE, key
        assert weights_a == weights_b, key


def test_a_rotated_image_display_keeps_its_rotation(tmp_path):
    """The display transform is written in the bone's frame, transposed or not.

    A region whose source rotation is neither 0 nor 90 degrees is the case that
    catches a matrix written in the wrong field order: the quad still lands on
    its corners (both orders are valid matrices) but the sprite is drawn
    mirrored across the diagonal — 5 units off on this fixture.
    """
    source = read_skeleton(_write(tmp_path / "in"))
    body = next(a for a in source.attachments if a.name == "body")
    # The source rotates the sprite 15 degrees; the model carries that as a
    # rotated quad, so its corners are NOT axis-aligned in the bone's frame.
    xs = sorted(round(point[0], 6) for point in body.polygon)
    assert xs[0] != xs[1], "the fixture's quad must be rotated"
    again = read_skeleton(write_and_read(tmp_path, source))
    written = json.loads((tmp_path / "out" / "rig_ske.json").read_text(encoding="utf-8"))
    display = written["armature"][0]["skin"][0]["slot"][0]["display"][0]
    assert display["type"] == "image"
    assert display["pivot"] == {"x": 0.0, "y": 0.0}
    # The armature's own rotation is 30 degrees, the display's 15: the written
    # transform is the display's, in the bone's frame, so it reads back as 15.
    assert abs(display["transform"]["skY"] - 15.0) < 1e-6
    assert abs(display["transform"]["skX"] - 15.0) < 1e-6
    assert display["width"] == 16.0 and display["height"] == 8.0
    again_body = next(a for a in again.attachments if a.name == "body")
    assert max(abs(a - b) for a, b in
               zip(body.polygon[0], again_body.polygon[0])) < TOLERANCE


def test_a_mesh_skinned_to_two_bones_is_not_collapsed_into_a_sprite(tmp_path):
    """A sprite is placed by one bone; a skinned mesh must stay geometry."""
    document = _skeleton()
    mesh = document["armature"][0]["skin"][0]["slot"][1]["display"][0]
    arm_world = mesh["bonePose"][1:]
    mesh["bonePose"] = [1] + arm_world + [0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    # Two influences per vertex: bone 1 (the arm) and bone 0 (the root).
    mesh["weights"] = [2, 1, 0.5, 0, 0.5] * 4
    source = read_skeleton(_write(tmp_path / "in", document=document))
    write_dragonbones(source, str(tmp_path / "out" / "rig_ske.json"),
                      image_name="rig.png")
    written = json.loads((tmp_path / "out" / "rig_ske.json").read_text(encoding="utf-8"))
    display = written["armature"][0]["skin"][0]["slot"][1]["display"][0]
    assert display["type"] == "mesh", "a skinned mesh must not become a sprite"
    assert display["weights"][:5] == [2, 1, 0.5, 0, 0.5]
    assert len(display["bonePose"]) == 14


def test_the_animation_survives_as_frames_at_the_files_own_rate(tmp_path):
    source = read_skeleton(_write(tmp_path / "in"))
    write_dragonbones(source, str(tmp_path / "out" / "rig_ske.json"),
                      image_name="rig.png")
    written = json.loads((tmp_path / "out" / "rig_ske.json").read_text(encoding="utf-8"))
    assert written["frameRate"] == 30
    animation = written["armature"][0]["animation"][0]
    assert animation["name"] == "idle"
    assert animation["duration"] == 30
    frames = animation["bone"][0]["rotateFrame"]
    assert [frame["rotate"] for frame in frames] == [10.0, 40.0]
    # A frame's position is implicit (the durations accumulate), and a missing
    # `tweenEasing` means HELD — so the first frame must state one.
    assert frames[0]["tweenEasing"] == 0
    assert "tweenEasing" not in frames[1]
    # The display frame keeps the slot's setup display; the last frame's
    # duration is the rest of the animation (the parser clamps it anyway).
    assert animation["slot"][0]["displayFrame"] == [{"duration": 30, "value": 0}]


def test_a_timeline_value_is_an_offset_from_the_bones_setup(tmp_path):
    """The runtime composes a bone as `origin + offset + animationPose`.

    `Bone.init` stores the setup transform as the bone's `origin`
    (`this.origin = this._boneData.transform`) and
    `Bone._updateGlobalTransformMatrix` adds the animation pose to it, so a
    `rotateFrame` value of 10 on a bone whose setup is 30 puts the bone at 40.
    Reading the file's numbers as absolute — or writing absolute values into it
    — moves every animated bone by its own setup offset (the hero's hip landed
    95 units low, which is exactly its setup `y`).
    """
    source = read_skeleton(_write(tmp_path / "in"))
    assert abs(source.by_name["arm"].rotation_deg - ARM_ANGLE) < 1e-9
    again = read_skeleton(write_and_read(tmp_path, source))
    keys = again.animations["idle"]["arm"]["rotate"]
    assert [round(key.time, 6) for key in keys] == [0.0, 0.5]
    assert [round(key.angle, 6) for key in keys] == [ARM_ANGLE + 10.0, ARM_ANGLE + 40.0]
    # And the file itself still carries the offsets, not the absolute pose.
    written = json.loads((tmp_path / "out" / "rig_ske.json").read_text(encoding="utf-8"))
    frames = written["armature"][0]["animation"][0]["bone"][0]["rotateFrame"]
    assert [frame["rotate"] for frame in frames] == [10.0, 40.0]
    assert keys[0].curve is None            # tweenEasing 0 is a straight segment
    assert keys[1].curve == "stepped"       # a trailing frame holds
    assert again.slot_timelines["idle"]["body"] == [
        {"time": 0.0, "attachment": "body"}]