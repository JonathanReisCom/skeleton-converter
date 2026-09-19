"""Godot → Spine → verify the JSON carries the same bones."""
import json
from pathlib import Path
import pytest

FIXTURE = Path("tests/fixtures/player.tscn")

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="fixture not present"
)
from src.in_godot import read_godot_skeleton
from src.out_spine import write_spine_json


def test_bones_present_in_spine_json():
    model = read_godot_skeleton(str(FIXTURE))
    spine = write_spine_json(model, output_path='/tmp/test_cross_convert.json')
    assert len(spine["bones"]) == len(model.bones)
    assert len(spine["slots"]) == len(model.attachments)
    assert len(spine["animations"]) == len(model.animations)
