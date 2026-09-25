"""Fixture-free tests: build a scene in-process and assert what Godot needs.

The round-trip tests in test_roundtrip_godot.py skip when tests/fixtures/ is
absent (third-party assets are deliberately not committed), which would leave
CI with nothing to check. These tests synthesize their own minimal rig, so they
run everywhere and still cover the failure modes that made generated scenes
unloadable: a phantom node in track paths, unsanitized resource ids, and a
texture path that does not exist.
"""
import json
import math
import re

from src.in_godot import read_godot_skeleton
from src.model import Attachment, Bone, Key, Skeleton
from src.out_godot import write_godot_scene
from src.out_spine import write_spine_json


def _rig() -> Skeleton:
    """A two-bone rig with an animation name Godot cannot use as a resource id."""
    model = Skeleton()
    model.texture_path = "res://rig.png"

    root = Bone(name="root", parent=None, position=[0.0, 0.0],
                rotation_deg=0.0, scale=[1.0, 1.0])
    root.length, root.path = 10.0, "root"
    child = Bone(name="arm", parent="root", position=[10.0, 0.0],
                 rotation_deg=30.0, scale=[1.0, 1.0])
    child.length, child.path = 8.0, "root/arm"
    model.bones = [root, child]
    model.by_name = {b.name: b for b in model.bones}
    # A hyphen and a space: both invalid in a Godot sub_resource id.
    model.animations = {
        "walk-cycle": {"arm": {"rotate": [
            Key(time=0.0, angle=30.0), Key(time=1.0, angle=90.0),
        ]}},
    }
    return model


def test_godot_scene_loads_where_the_engine_expects_it(tmp_path):
    """The scene root is a container and track paths resolve from it.

    Godot instantiates an empty scene (logging 'parent path has vanished') when
    a track addresses a node that does not exist, and our own reader then
    matches zero tracks — a green round-trip over a dead scene.
    """
    out = tmp_path / "rig.tscn"
    write_godot_scene(_rig(), str(out), texture_path="res://rig.png")
    text = out.read_text()

    assert '[node name="SkeletonRoot" type="Node2D"]' in text, "no scene root node"
    assert '[node name="Skeleton2D" type="Skeleton2D" parent="Sprite2D"]' in text

    # Every track path must name nodes the scene actually declares.
    declared = {m.group(1) for m in re.finditer(r'\[node name="([^"]+)"', text)}
    for path in re.findall(r'tracks/\d+/path = NodePath\("([^"]+)"\)', text):
        node_path = path.split(":")[0]
        assert node_path.split("/")[-1] in declared, f"dangling track path: {path}"


def test_animation_names_survive_while_resource_ids_are_sanitized(tmp_path):
    """Godot rejects ids outside [A-Za-z0-9_]; the real name must be kept."""
    out = tmp_path / "rig.tscn"
    write_godot_scene(_rig(), str(out), texture_path="res://rig.png")
    text = out.read_text()

    ids = re.findall(r'\[sub_resource type="Animation" id="([^"]+)"\]', text)
    assert ids, "no animation resource emitted"
    for resource_id in ids:
        assert re.fullmatch(r"[A-Za-z0-9_]+", resource_id), f"invalid id: {resource_id}"

    # The library key keeps the author-facing name so AnimationPlayer.play works.
    assert '&"walk-cycle": SubResource(' in text


def test_spine_bundle_names_the_image_the_atlas_declares(tmp_path):
    """The atlas's first line must equal the texture's file name.

    The runtime resolves the page as a sibling; a mismatch is a load failure,
    not a cosmetic wart.
    """
    out = tmp_path / "animation.json"
    write_spine_json(_rig(), str(out), image_name="animation.png")
    atlas = (tmp_path / "animation.atlas").read_text()

    assert atlas.splitlines()[0] == "animation.png"
    # No PNG beside it: the size falls back, but the page name is still exact.
    assert json.loads(out.read_text())["skeleton"]["spine"]


def test_rest_positions_round_trip_through_our_own_readers(tmp_path):
    """Writer and reader agree on the transform convention.

    This is the cheap invariant the fixture-based tests cannot check in CI: a
    sign error in the Transform2D column order shows up as a large deviation.
    """
    out = tmp_path / "rig.tscn"
    write_godot_scene(_rig(), str(out), texture_path="res://rig.png")
    reloaded = read_godot_skeleton(str(out))

    by_name = {b.name: b for b in reloaded.bones}
    assert set(by_name) == {"root", "arm"}

    arm = by_name["arm"]
    assert math.isclose(arm.position[0], 10.0, abs_tol=1e-4)
    assert math.isclose(arm.rotation_deg, 30.0, abs_tol=1e-4)


def test_bezier_curves_survive_the_godot_leg(tmp_path):
    """A curve key must come back from spine → godot → spine unchanged.

    The .tscn leg once flattened every bezier to linear (interp = 1), so a
    round trip silently lost 189 curve keys on the hero. The writer now emits
    TYPE_BEZIER tracks (points = [value, in_t, in_v, out_t, out_v] per key,
    handles offset from their key) and the reader rebuilds the spine-space
    curve from them. Control points live in the raw JSON's offset space, which
    is what every writer maps through — rot/y negate, x does not.
    """
    model = _rig()
    model.animations = {
        "a": {"arm": {
            # rotate: curve control points are absolute spine offsets.
            "rotate": [
                Key(time=0.0, angle=-10.0, curve=[0.15, 5.0, 0.35, 25.0]),
                Key(time=1.0, angle=-40.0),
            ],
            # translate: 8-float curve = [x1,y1,x2,y2] per segment.
            "translate": [
                Key(time=0.0, x=10.0, y=0.0,
                    curve=[0.15, 5.0, 0.35, 25.0, 0.2, -1.0, 0.4, -3.0]),
                Key(time=1.0, x=40.0, y=-30.0),
            ],
        }},
    }
    scene = tmp_path / "rig.tscn"
    write_godot_scene(model, str(scene), texture_path="res://rig.png")

    text = scene.read_text()
    assert 'type = "bezier"' in text, "no bezier track emitted"
    # handles live in the scene as offsets from their key's value
    assert '"points": PackedFloat32Array(' in text

    reloaded = read_godot_skeleton(str(scene))
    rt = tmp_path / "back.json"
    write_spine_json(reloaded, str(rt), image_name="back.png")
    back = json.loads(rt.read_text())["animations"]["a"]["bones"]["arm"]

    # write_spine_json emits offsets (value - setup), the input model carried
    # absolute model-space angles, so compare against the offsets instead.
    assert math.isclose(back["rotate"][0]["value"], 40.0, abs_tol=1e-6), back["rotate"][0]
    got = back["rotate"][0]["curve"]
    want = model.animations["a"]["arm"]["rotate"][0].curve
    assert all(math.isclose(x, y, abs_tol=1e-6) for x, y in zip(want, got)), \
        f"rotate curve changed: {want} -> {got}"
    got = back["translate"][0]["curve"]
    want = model.animations["a"]["arm"]["translate"][0].curve
    assert len(got) == 8 and all(
        math.isclose(x, y, abs_tol=1e-6) for x, y in zip(want, got)
    ), f"translate curve changed: {want} -> {got}"
    # offsets: x came back minus setup (10.0), y negated back to 0
    assert math.isclose(back["translate"][0]["x"], 0.0, abs_tol=1e-6)
    assert math.isclose(back["translate"][0]["y"], 0.0, abs_tol=1e-6)


def test_skinned_polygon_format_matches_what_godot_renders(tmp_path):
    """The .tscn has two silent render-killers; assert the writer avoids both.

    A skinned Polygon2D with a wrong bone reference or a merged triangle list
    renders NOTHING — no error, an invisible mesh. Both details were found by
    exporting the hero and diffing screenshots:
    - bone refs are NodePaths relative to the Skeleton2D ("root/hip/..."),
      like the official demo's "Hip/Chest", not bare leaf names;
    - each spine triangle is its own `polygons` group, because Godot
      fan-triangulates a group from its first index.
    """
    model = _rig()
    # Weighted mesh: 4 verts, one influencing bone with weight 1.
    model.attachments.append(Attachment(
        name="plate",
        polygon=[(10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0)],
        uv=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
        polygons=[0, 1, 2, 0, 2, 3],
        weights=[("arm", [1.0, 1.0, 1.0, 1.0])],
    ))
    scene = tmp_path / "rig.tscn"
    write_godot_scene(model, str(scene), texture_path="res://rig.png")
    text = scene.read_text()

    # Bone refs are skeleton-relative paths, resolvable from the skeleton node.
    assert '"root/arm", PackedFloat32Array(1.0, 1.0, 1.0, 1.0)' in text, \
        "bone reference must be the skeleton-relative path"
    # One group per triangle (2 triangles from the 6 flat indices).
    groups = re.findall(r'PackedInt32Array\((\d+), (\d+), (\d+)\)', text)
    assert groups == [("0", "1", "2"), ("0", "2", "3")], groups
