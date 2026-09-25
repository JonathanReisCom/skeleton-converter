"""Fixture-free tests: the `.tres` export must parse back AND load in Godot.

Our own parser agreeing with our own writer proves nothing about the engine
(see AGENTS.md: "a passing round-trip test does not prove the .tscn loads").
These tests do both:

* the emitted text is read back through the repo's own Godot resource parser
  (``in_godot.parse_tscn`` — the same ``[ext_resource]``/``[sub_resource]``
  text grammar a ``.tres`` uses), asserting the AnimationLibrary, its
  sub-resources, and their track paths;
* when a Godot binary is present (``GODOT_BIN`` or the macOS app), the file is
  loaded headless as a real resource and asserted to be an ``AnimationLibrary``
  with the expected animations, tracks, and bone rest table — including that
  the library is actually installable on an ``AnimationPlayer``.

A ``.tres`` cannot carry a node graph (see ``src/out_tres.py``), so the engine
assertions are about the resource payload: animations and ``metadata/bones``.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from src.in_godot import parse_tscn, parse_value
from src.model import Bone, Key, Skeleton
from src.out_tres import write_tres_resource

DEFAULT_GODOT = "/Applications/Godot.app/Contents/MacOS/Godot"


def _godot_binary() -> str | None:
    candidate = os.environ.get("GODOT_BIN") or DEFAULT_GODOT
    if Path(candidate).exists():
        return candidate
    return shutil.which("godot") or shutil.which("godot4")


GODOT = _godot_binary()

needs_godot = pytest.mark.skipif(
    GODOT is None, reason="no Godot binary (set GODOT_BIN to run the engine check)"
)


def _rig() -> Skeleton:
    """Three bones (one with a bind `rest` differing from its pose), an
    animation name invalid as a Godot resource id, and a bezier rotate key."""
    model = Skeleton()
    model.texture_path = "res://rig.png"
    root = Bone(name="root", parent=None, position=[0.0, 0.0], rotation_deg=0.0)
    root.length, root.path = 10.0, "root"
    arm = Bone(name="arm", parent="root", position=[10.0, 0.0], rotation_deg=30.0)
    arm.length, arm.path = 8.0, "root/arm"
    # Bind pose differs from the node pose: both must survive the export.
    arm.rest = ([10.0, 0.0], 0.0, (1.0, 1.0))
    hand = Bone(name="hand", parent="arm", position=[8.0, 0.0], rotation_deg=-15.0)
    hand.length, hand.path = 0.0, "root/arm/hand"
    hand.inherit = "onlyTranslation"
    model.bones = [root, arm, hand]
    model.by_name = {bone.name: bone for bone in model.bones}
    # Hyphen: invalid in a Godot sub_resource id, valid as a library key.
    model.animations = {
        "walk-cycle": {
            "arm": {"rotate": [
                Key(time=0.0, angle=30.0, curve=[0.1, 30.0, 0.9, 90.0]),
                Key(time=1.0, angle=90.0),
            ]},
            "hand": {"translate": [
                Key(time=0.0, x=0.0, y=0.0), Key(time=1.0, x=2.0, y=-3.0),
            ]},
        },
    }
    return model


def test_tres_parses_back_through_the_repo_parser(tmp_path):
    """The AnimationLibrary and its Animation tracks survive the text grammar."""
    out = tmp_path / "rig.tres"
    write_tres_resource(_rig(), str(out))
    text = out.read_text(encoding="utf-8")

    assert text.startswith('[gd_resource type="AnimationLibrary" load_steps=2 '
                           'format=3]'), text.splitlines()[0]

    # A .tres' root resource lives in [resource], which parse_tscn does not
    # read (it walks [sub_resource]/[node]); the library map is parsed with
    # parse_tscn's own value parser, on the block the writer emits.
    data_start = text.index("_data = {")
    data_end = text.index("\n}", data_start) + 2
    # The real animation name is the library key; the id is sanitized.
    assert parse_value(text[data_start + len("_data = "):data_end]) == {
        "walk-cycle": 'SubResource("Animation_walk_cycle")'}

    scene = parse_tscn(str(out))
    animation = scene["sub_resources"]["Animation_walk_cycle"]
    assert animation["type"] == "Animation"
    assert animation["props"]["tracks/0/path"] == \
        "Sprite2D/Skeleton2D/root/arm:rotation_degrees"
    assert animation["props"]["tracks/0/type"] == "bezier"
    assert animation["props"]["tracks/0/keys"]["times"] == [0.0, 1.0]
    # Bezier points are [value, in_t, in_v, out_t, out_v] per key.
    assert animation["props"]["tracks/0/keys"]["points"][0] == 30.0
    assert len(animation["props"]["tracks/0/keys"]["points"]) == 10
    assert animation["props"]["tracks/1/path"] == \
        "Sprite2D/Skeleton2D/root/arm/hand:position"
    assert animation["props"]["tracks/1/keys"]["values"] == [[0.0, 0.0], [2.0, -3.0]]
    assert animation["props"]["length"] == 1.0

    # The bind pose and the inherit mode travel as resource metadata: a .tres
    # cannot hold the Bone2D node that would carry them as properties.
    assert 'metadata/bones = [' in text
    for bone in _rig().bones:
        assert f'&"{bone.name}"' in text


PROBE = """extends SceneTree

var failures := 0

func check(label: String, ok: bool) -> void:
	print("%s %s" % ["OK" if ok else "FAIL", label])
	if not ok:
		failures += 1

func _init():
	var library = load("res://rig.tres")
	check("loads", library != null)
	if library == null:
		quit(1)
		return
	check("class AnimationLibrary", library.get_class() == "AnimationLibrary")
	var names: PackedStringArray = library.get_animation_list()
	check("animation list", names.size() == 1 and names[0] == &"walk-cycle")
	var player := AnimationPlayer.new()
	root.add_child(player)
	player.add_animation_library("", library)
	check("playable by AnimationPlayer", player.has_animation("walk-cycle"))
	var walk: Animation = library.get_animation("walk-cycle")
	check("track count", walk.get_track_count() == 2)
	check("rotate track path", walk.track_get_path(0) ==
			NodePath("Sprite2D/Skeleton2D/root/arm:rotation_degrees"))
	check("translate track path", walk.track_get_path(1) ==
			NodePath("Sprite2D/Skeleton2D/root/arm/hand:position"))
	var bones: Array = library.get_meta("bones", [])
	check("bone count", bones.size() == 3)
	if bones.size() == 3:
		var root_bone: Dictionary = bones[0]
		var arm: Dictionary = bones[1]
		var hand: Dictionary = bones[2]
		check("bone names", [root_bone["name"], arm["name"], hand["name"]] ==
				[&"root", &"arm", &"hand"])
		check("bone parents", [root_bone["parent"], arm["parent"], hand["parent"]] ==
				[&"", &"root", &"arm"])
		check("arm node rotation is the pose",
				is_equal_approx(arm["rotation_degrees"], 30.0))
		check("arm bind rest origin", arm["rest"].origin == Vector2(10, 0))
		# The bind rest (0 deg) must survive as a value distinct from the pose
		# (30 deg): Godot skins with pose * rest^-1.
		check("arm bind rest rotation", is_zero_approx(arm["rest"].get_rotation()))
		check("arm length", is_equal_approx(arm["length"], 8.0))
		check("hand skeleton path", hand["path"] == &"root/arm/hand")
		check("hand inherit", hand["inherit"] == &"onlyTranslation")
	print("FAILURES=%d" % failures)
	quit(failures)
"""


@needs_godot
def test_tres_loads_in_headless_godot(tmp_path):
    """The real engine must load the resource and find the animations in it."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.godot").write_text(
        '[application]\nconfig/name="tres-check"\n'
        'config/features=PackedStringArray("4.4")\n', encoding="utf-8")
    (project / "probe.gd").write_text(PROBE, encoding="utf-8")
    write_tres_resource(_rig(), str(project / "rig.tres"))

    result = subprocess.run(
        [GODOT, "--headless", "--path", str(project), "--script",
         "res://probe.gd"],
        capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    # The probe fails its own checks and exits nonzero; a Godot "ERROR" line
    # (rejected property, bad id, unparsable resource) is a load failure too.
    assert result.returncode == 0, output
    assert "ERROR" not in output, output
    assert "FAIL " not in output, output
    assert "FAILURES=0" in output, output
    assert output.count("OK ") >= 14, output
