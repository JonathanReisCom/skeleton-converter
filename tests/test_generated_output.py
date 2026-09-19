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
from src.model import Bone, Skeleton
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
            {"time": 0.0, "angle": 30.0}, {"time": 1.0, "angle": 90.0},
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
