"""Round-trip: Godot .tscn → model → Godot .tscn → model, verify numerically."""
import math
from pathlib import Path
import pytest

FIXTURE = Path("tests/fixtures/player.tscn")

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="fixture not present"
)
from src.in_godot import read_godot_skeleton
from src.out_godot import write_godot_scene
from src.model import godot_world_transforms


def test_rest_positions_match_after_roundtrip(tmp_path):
    original = read_godot_skeleton(str(FIXTURE))
    out_path = str(tmp_path / "roundtrip.tscn")
    write_godot_scene(original, out_path, "res://gBot.png")
    reloaded = read_godot_skeleton(out_path)

    assert len(reloaded.bones) == len(original.bones)
    world_original = godot_world_transforms(original)
    world_reloaded = godot_world_transforms(reloaded)
    worst = 0.0
    for name in world_original:
        if name not in world_reloaded:
            continue
        ox, oy = world_original[name][4], world_original[name][5]
        rx, ry = world_reloaded[name][4], world_reloaded[name][5]
        d = math.hypot(ox - rx, oy - ry)
        worst = max(worst, d)
    assert worst < 0.01, f"worst bone deviation: {worst}"


def test_animations_survive_roundtrip(tmp_path):
    original = read_godot_skeleton(str(FIXTURE))
    out_path = str(tmp_path / "roundtrip.tscn")
    write_godot_scene(original, out_path, "res://gBot.png")
    reloaded = read_godot_skeleton(out_path)
    assert set(reloaded.animations) == set(original.animations)
    for anim_name in original.animations:
        assert anim_name in reloaded.animations
