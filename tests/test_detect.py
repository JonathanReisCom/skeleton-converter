"""Content sniffing tests: every detection branch, on files built in tmp_path."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from src.detect import describe, detect_format, detect_rig


def _write(path: Path, data) -> Path:
    if isinstance(data, bytes):
        path.write_bytes(data)
    elif isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _spine() -> dict:
    return {
        "skeleton": {"spine": "4.2.120"},
        "bones": [{"name": "root"}, {"name": "hip", "parent": "root"}],
        "slots": [],
        "skins": {},
        "animations": {"walk": {"bones": {"hip": {}}}},
    }


def _armature() -> dict:
    return {
        "version": 1,
        "img_format": "png",
        "bones": [{"id": 0, "parent_id": -1, "init_pos": {"x": 0, "y": 0},
                   "visuals_id": 0, "ik_family_id": -1}],
        "visuals": [{"tex": "atlas0.png", "zindex": 0}],
        "styles": [{"name": "atlas0.png"}],
        "animations": [{"name": "idle", "fps": 30, "keyframes": []}],
    }


def _skf_bytes() -> bytes:
    """A real bundle: ZIP magic, `armature.json` member, a page image."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("armature.json", json.dumps(_armature()))
        bundle.writestr("atlas0.png", b"\x89PNG\r\n\x1a\n")
        bundle.writestr("readme.md", "skelform bundle")
    return buffer.getvalue()


def test_spine_json(tmp_path):
    path = _write(tmp_path / "hero.json", _spine())
    assert detect_format(path) == "spine"


def test_bare_armature_json_is_skelform(tmp_path):
    path = _write(tmp_path / "armature.json", _armature())
    assert detect_format(path) == "skelform"


def test_minimal_spine_without_skeleton_key(tmp_path):
    path = _write(tmp_path / "rig.json",
                  {"bones": [{"name": "root"}], "animations": {}})
    assert detect_format(path) == "spine"


def test_real_skf_bundle(tmp_path):
    path = _write(tmp_path / "rig.skf", _skf_bytes())
    assert path.read_bytes()[:4] == b"PK\x03\x04"
    assert detect_format(path) == "skelform"


def test_renamed_zip_bundle(tmp_path):
    path = _write(tmp_path / "rig.zip", _skf_bytes())
    assert detect_format(path) == "skelform"


def test_plain_zip_is_not_a_rig(tmp_path):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("notes.txt", "hello")
    assert detect_format(path) is None


def test_godot_scene(tmp_path):
    path = _write(tmp_path / "scene.tscn", "[gd_scene format=3]\n\n[node name=\"A\"]\n")
    assert detect_format(path) == "godot"


def test_godot_scene_with_bom_and_leading_space(tmp_path):
    path = _write(tmp_path / "scene.tscn",
                  "\ufeff  \n[gd_scene format=3]\n")
    assert detect_format(path) == "godot"


def test_tscn_without_godot_header(tmp_path):
    path = _write(tmp_path / "scene.tscn", "not a scene at all\n")
    assert detect_format(path) is None


def test_tres_resource(tmp_path):
    path = _write(tmp_path / "library.tres",
                  '[gd_resource type="AnimationLibrary" format=3]\n')
    assert detect_format(path) == "tres"


def test_companions_are_not_rigs(tmp_path):
    atlas = _write(tmp_path / "hero.atlas", "hero.png\nsize: 128,128\n")
    page = _write(tmp_path / "hero.png", b"\x89PNG\r\n\x1a\n")
    assert detect_format(atlas) is None
    assert detect_format(page) is None


def test_invalid_json_is_none(tmp_path):
    path = _write(tmp_path / "broken.json", "{not json,,,")
    assert detect_format(path) is None


def test_json_that_is_not_an_object_is_none(tmp_path):
    path = _write(tmp_path / "list.json", [1, 2, 3])
    assert detect_format(path) is None


def test_missing_path_is_none(tmp_path):
    assert detect_format(tmp_path / "nope.json") is None
    assert detect_format(tmp_path) is None  # a directory, not a file


def test_truncated_skf_is_none(tmp_path):
    partial = _skf_bytes()[:40]
    path = _write(tmp_path / "broken.skf", partial)
    assert detect_format(path) is None


def test_unknown_extension_is_none(tmp_path):
    path = _write(tmp_path / "notes.txt", "just notes\n")
    assert detect_format(path) is None


def test_detect_rig_picks_the_rig_out_of_an_upload(tmp_path):
    atlas = _write(tmp_path / "hero.atlas", "hero.png\n")
    page = _write(tmp_path / "hero.png", b"\x89PNG\r\n\x1a\n")
    rig = _write(tmp_path / "hero.json", _spine())
    assert detect_rig([str(atlas), str(page), str(rig)]) == (str(rig), "spine")


def test_detect_rig_with_only_companions_is_none(tmp_path):
    atlas = _write(tmp_path / "hero.atlas", "hero.png\n")
    page = _write(tmp_path / "hero.png", b"\x89PNG\r\n\x1a\n")
    assert detect_rig([str(atlas), str(page)]) is None
    assert detect_rig([]) is None


def test_describe_recognized_and_unrecognized(tmp_path):
    rig = _write(tmp_path / "hero.json", _spine())
    bundle = _write(tmp_path / "rig.skf", _skf_bytes())
    tres = _write(tmp_path / "library.tres",
                  '[gd_resource type="AnimationLibrary" format=3]\n')
    notes = _write(tmp_path / "notes.txt", "notes\n")

    assert describe(rig) == "spine: hero.json"
    assert describe(bundle) == "skelform: rig.skf (3 entries)"
    assert describe(tres) == "tres: library.tres (no reader, writer only)"
    assert describe(notes) == "unrecognized: notes.txt"
    assert describe(tmp_path / "ghost.json") == "unrecognized: ghost.json"
