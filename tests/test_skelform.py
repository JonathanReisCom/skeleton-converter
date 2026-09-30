"""SkelForm round trip: armature -> model -> armature, verified numerically.

Fixture-free by necessity: the repository ships no third-party rigs (and the
official SkelForm samples live outside it), so this builds a small armature in
code and asserts what a runtime would observe — bone world transforms, mesh
geometry, and the sampled value of every animation channel.
"""
import json
import math
import zipfile

from src.in_skelform import read_skeleton
from src.model import godot_world_transforms
from src.out_skelform import write_skelform

TOLERANCE = 0.01


def _armature() -> dict:
    """A three-bone rig with a region, a mesh and animated position/rotation."""
    return {
        "version": "0.8.0",
        "baked_ik": False,
        "img_format": "PNG",
        "clear_color": {"r": 0, "g": 0, "b": 0, "a": 0},
        "bones": [
            {"id": 0, "name": "root", "parent_id": -1,
             "pos": {"x": 0.0, "y": 0.0}, "rot": 0.0,
             "scale": {"x": 1.0, "y": 1.0},
             "init_pos": {"x": 0.0, "y": 0.0}, "init_rot": 0.0,
             "init_scale": {"x": 1.0, "y": 1.0},
             "ik_family_id": -1, "physics_id": -1, "visuals_id": 0},
            {"id": 1, "name": "arm", "parent_id": 0,
             "pos": {"x": 20.0, "y": 30.0}, "rot": 0.5,
             "scale": {"x": 1.0, "y": 1.0},
             "init_pos": {"x": 20.0, "y": 30.0}, "init_rot": 0.5,
             "init_scale": {"x": 1.0, "y": 1.0},
             "ik_family_id": -1, "physics_id": -1, "visuals_id": 1},
        ],
        "animations": [{
            "name": "wave", "id": 0, "fps": 60,
            "keyframes": [
                {"frame": 0, "bone_id": 1, "element": "PositionX", "value": 20.0,
                 "start_handle": {"x": 1 / 3, "y": 1 / 3},
                 "end_handle": {"x": 2 / 3, "y": 2 / 3},
                 "next_kf": 1, "handle_preset": "Linear"},
                {"frame": 30, "bone_id": 1, "element": "PositionX", "value": 40.0,
                 "start_handle": {"x": 0.5, "y": 0.0},
                 "end_handle": {"x": 0.5, "y": 1.0},
                 "next_kf": 1, "handle_preset": "SineInOut"},
                {"frame": 0, "bone_id": 1, "element": "Rotation", "value": 0.5,
                 "start_handle": {"x": 1 / 3, "y": 1 / 3},
                 "end_handle": {"x": 2 / 3, "y": 2 / 3},
                 "next_kf": 3, "handle_preset": "Linear"},
                {"frame": 30, "bone_id": 1, "element": "Rotation", "value": 1.2,
                 "start_handle": {"x": 999.0, "y": 999.0},
                 "end_handle": {"x": 999.0, "y": 999.0},
                 "next_kf": 3, "handle_preset": "Snap"},
            ],
        }],
        "atlases": [{"filename": "atlas0.png", "size": {"x": 256, "y": 256}}],
        "styles": [{"id": 0, "name": "default", "textures": [
            {"name": "torso", "offset": {"x": 0, "y": 0},
             "size": {"x": 100, "y": 50}, "atlas_idx": 0},
            {"name": "arm-mesh", "offset": {"x": 120, "y": 10},
             "size": {"x": 60, "y": 40}, "atlas_idx": 0},
        ]}],
        "inverse_kinematics": [],
        "visuals": [
            {"tex": "torso", "zindex": 0, "pivot_pos": {"x": 0.0, "y": 0.0},
             "pivot_rot": 0.0, "pivot_scale": {"x": 1.0, "y": 1.0}},
            {"tex": "arm-mesh", "zindex": 1, "pivot_pos": {"x": 0.0, "y": 0.0},
             "pivot_rot": 0.0, "pivot_scale": {"x": 1.0, "y": 1.0},
             "vertices": [
                 {"id": 0, "pos": {"x": -30.0, "y": -20.0},
                  "uv": {"x": 0.0, "y": 0.0},
                  "init_pos": {"x": -30.0, "y": -20.0}},
                 {"id": 1, "pos": {"x": 0.0, "y": -20.0},
                  "uv": {"x": 0.5, "y": 0.0},
                  "init_pos": {"x": 0.0, "y": -20.0}},
                 {"id": 2, "pos": {"x": 30.0, "y": -20.0},
                  "uv": {"x": 1.0, "y": 0.0},
                  "init_pos": {"x": 30.0, "y": -20.0}},
                 {"id": 3, "pos": {"x": 30.0, "y": 20.0},
                  "uv": {"x": 1.0, "y": 1.0},
                  "init_pos": {"x": 30.0, "y": 20.0}},
                 {"id": 4, "pos": {"x": 0.0, "y": 20.0},
                  "uv": {"x": 0.5, "y": 1.0},
                  "init_pos": {"x": 0.0, "y": 20.0}},
                 {"id": 5, "pos": {"x": -30.0, "y": 20.0},
                  "uv": {"x": 0.0, "y": 1.0},
                  "init_pos": {"x": -30.0, "y": 20.0}},
             ],
             # Two bones share the mesh: it cannot collapse into a region.
             "indices": [0, 1, 2, 0, 2, 5, 2, 3, 4, 2, 4, 5],
             "binds": [
                 {"bone_id": 0, "is_path": False, "verts": [
                     {"id": 0, "weight": 0.5}, {"id": 5, "weight": 0.5}]},
                 {"bone_id": 1, "is_path": False, "verts": [
                     {"id": 1, "weight": 1.0}, {"id": 2, "weight": 1.0},
                     {"id": 3, "weight": 1.0}, {"id": 4, "weight": 1.0}]},
             ]},
        ],
        "physics": [],
    }


def test_texture_rects_are_written_as_int32(tmp_path):
    """The editor types these as i32 and rejects a float outright.

    `styles[].textures[].offset`/`size` and `atlases[].size` are integers in the
    editor's own model. Writing 1165.0 makes its `serde_json::from_value::<Root>()`
    fail with "invalid type: floating point `1165.0`, expected i32", and the app
    unwraps that error and aborts — the whole rig is refused over a decimal
    point. The runtimes divide these by the atlas size, so nothing is lost.
    """
    from tests.ci_rig import write_png

    model = read_skeleton(_write(tmp_path, _armature()))
    page = tmp_path / "page.png"
    write_png(page)
    target = tmp_path / "ints.skf"
    write_skelform(model, str(target), atlas_paths=[str(page)])

    with zipfile.ZipFile(target) as bundle:
        armature = json.loads(bundle.read("armature.json"))
    rects = [texture for style in armature["styles"]
             for texture in style["textures"]]
    assert rects, "the rig must carry at least one texture rect"
    for rect in rects:
        for key in ("offset", "size"):
            assert all(isinstance(value, int) for value in rect[key].values()), \
                f"{key} must be int32: {rect[key]}"
    for atlas in armature["atlases"]:
        assert all(isinstance(value, int) for value in atlas["size"].values()), \
            f"atlas size must be int32: {atlas['size']}"


def test_pages_are_written_in_the_order_the_runtime_numbers_them(tmp_path):
    """The player's page counter is dead code, so member order decides.

    api.js's skfReadFile declares `let atlasIdx = 0` INSIDE the member loop, so
    its increment never runs and every page image overwrites `atlases[0]`. Its
    page NUMBER therefore comes from iteration order over the archive, not from
    the member name: write the pages in any other order and each attachment
    samples the wrong image (a black rig). The armature has to come first too —
    the same loop parses it on the way past.
    """
    from tests.ci_rig import write_png

    armature = _armature()
    armature["atlases"].append({"filename": "atlas1.png",
                                "size": {"x": 128, "y": 128}})
    # One attachment lives on the second page, so the rig genuinely spans two.
    armature["styles"][0]["textures"][1]["atlas_idx"] = 1
    model = read_skeleton(_write(tmp_path, armature))

    page0 = tmp_path / "atlas0.png"
    page1 = tmp_path / "atlas1.png"
    write_png(page0)
    write_png(page1)
    # Handed over in the wrong order on purpose: the writer must sort them by
    # the armature's own `atlases`, not by what the caller happened to pass.
    target = tmp_path / "two-pages.skf"
    write_skelform(model, str(target), atlas_paths=[str(page1), str(page0)])

    with zipfile.ZipFile(target) as bundle:
        members = [n for n in bundle.namelist() if not n.endswith("/")]
        written = json.loads(bundle.read("armature.json"))
    # `editor.json` rides along (the editor keeps its active style there) and
    # carries no "atlas" member name, so the player's page numbering is intact.
    # The two source pages collapse into one: `SkfDraw` flushes a batch with the
    # INCOMING page's texture when the page changes, so pieces gathered before a
    # switch would be drawn with the wrong sheet. One page removes the switch.
    assert members == ["armature.json", "readme.md", "editor.json", "atlas0.png"]
    assert len(written["atlases"]) == 1
    assert {texture["atlas_idx"] for texture in written["styles"][0]["textures"]} == {0}
    assert [page["filename"] for page in written["atlases"]] == ["atlas0.png"]


def _write(tmp_path, armature: dict, name: str = "rig") -> str:
    source = tmp_path / f"{name}.skf"
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("armature.json", json.dumps(armature))
    return str(source)


def _sample_channel(model, animation: str, bone: str, kind: str,
                    time: float, axis: int = 0) -> float:
    """Evaluate a model track the way a runtime would (linear/bezier/stepped).

    A track's curves are stored in the same value space as its keys, so a
    stale-space curve would only show up as a sampled-value error here.
    """
    from src.in_skelform import _sample

    skeleton_bone = next(b for b in model.bones if b.name == bone)
    keys = model.animations[animation][bone][kind]
    if kind == "rotate":
        entries = [(k.time, k.angle, k.curve) for k in keys]
    elif kind == "scale":
        entries = [(k.time, k.scale[axis], k.curve) for k in keys]
    else:
        entries = [(k.time, (k.x if axis == 0 else k.y), k.curve) for k in keys]
    return _sample(entries, time, axis)


def test_each_axis_of_a_two_axis_track_keeps_its_own_curve(tmp_path):
    """PositionX and PositionY are separate channels with separate handles.

    A writer that hands both elements the same quadruple (or maps the curve
    once per element) keeps every key value intact and still bends the path
    between keys, so only a mid-segment sample shows it. The rig also pins the
    curve layout: one quadruple per value axis on translate, a single
    quadruple on rotate — a runtime indexes the curve by axis.
    """
    armature = _armature()
    armature["animations"][0]["keyframes"] += [
        # Same frames as PositionX, opposite shape: a straight line on x and a
        # full ease-in-out on y, so mixing the two quadruples is measurable.
        {"frame": 0, "bone_id": 1, "element": "PositionY", "value": 30.0,
         "start_handle": {"x": 0.5, "y": 0.0},
         "end_handle": {"x": 0.5, "y": 1.0},
         "next_kf": 1, "handle_preset": "SineInOut"},
        {"frame": 30, "bone_id": 1, "element": "PositionY", "value": 60.0,
         "start_handle": {"x": 0.5, "y": 0.0},
         "end_handle": {"x": 0.5, "y": 1.0},
         "next_kf": 1, "handle_preset": "SineInOut"},
    ]
    model = read_skeleton(_write(tmp_path, armature))

    translate = model.animations["wave"]["arm"]["translate"][0].curve
    assert len(translate) == 8, translate
    assert translate[:4] != translate[4:8], translate
    rotate_curve = model.animations["wave"]["arm"]["rotate"][0].curve
    assert rotate_curve in (None, "stepped") or len(rotate_curve) == 4

    written = tmp_path / "axes.skf"
    write_skelform(model, str(written), fps=60.0)
    reloaded = read_skeleton(str(written))

    for time in (0.1, 0.2, 0.35):
        for axis in (0, 1):
            assert math.isclose(
                _sample_channel(reloaded, "wave", "arm", "translate", time, axis),
                _sample_channel(model, "wave", "arm", "translate", time, axis),
                abs_tol=TOLERANCE), (axis, time)


def test_armature_round_trips_through_the_model(tmp_path):
    source = _write(tmp_path, _armature())
    model = read_skeleton(source)

    # What the runtime would see: world transforms, geometry, channels.
    assert [bone.name for bone in model.bones] == ["root", "arm"]
    worlds = godot_world_transforms(model)
    assert math.isclose(worlds["arm"][4], 20.0, abs_tol=TOLERANCE)
    assert math.isclose(worlds["arm"][5], -30.0, abs_tol=TOLERANCE)

    by_name = {a.name: a for a in model.attachments}
    assert set(by_name) == {"torso", "arm-mesh"}
    mesh = by_name["arm-mesh"]
    assert len(mesh.polygon) == 6
    assert len(mesh.polygons) == 4, "flat triangle list -> one group per triangle"
    assert [w[0] for w in mesh.weights] == ["root", "arm"]
    assert abs(sum(mesh.weights[0][1]) - 1.0) < 1e-6
    regions = [by_name["torso"]]
    assert regions[0].uv[0] == (0.0, 0.0) and regions[0].uv[2] == (100.0, 50.0)

    # Curves survive: the Snap segment holds its start value.
    assert _sample_channel(model, "wave", "arm", "rotate", 0.25) == \
        _sample_channel(model, "wave", "arm", "rotate", 0.05)

    written = tmp_path / "written.skf"
    write_skelform(model, str(written), fps=60.0)
    reloaded = read_skeleton(str(written))

    assert [bone.name for bone in reloaded.bones] == [bone.name for bone in model.bones]
    for name, world in godot_world_transforms(model).items():
        other = godot_world_transforms(reloaded)[name]
        for value, expected in zip(other, world):
            assert math.isclose(value, expected, abs_tol=TOLERANCE), (name, other, world)

    assert len(reloaded.attachments) == len(model.attachments)
    for original, copy in zip(model.attachments, reloaded.attachments):
        assert copy.name == original.name
        assert len(copy.polygon) == len(original.polygon)
        for point, expected in zip(copy.polygon, original.polygon):
            assert math.isclose(point[0], expected[0], abs_tol=TOLERANCE)
            assert math.isclose(point[1], expected[1], abs_tol=TOLERANCE)
        for uv, expected_uv in zip(copy.uv, original.uv):
            assert math.isclose(uv[0], expected_uv[0], abs_tol=TOLERANCE)
            assert math.isclose(uv[1], expected_uv[1], abs_tol=TOLERANCE)

    for time in (0.0, 0.1, 0.25, 0.49):
        assert math.isclose(_sample_channel(reloaded, "wave", "arm", "rotate", time),
                            _sample_channel(model, "wave", "arm", "rotate", time),
                            abs_tol=TOLERANCE), ("rotate", time)
        for axis in (0, 1):
            assert math.isclose(
                _sample_channel(reloaded, "wave", "arm", "translate", time, axis),
                _sample_channel(model, "wave", "arm", "translate", time, axis),
                abs_tol=TOLERANCE), ("translate", axis, time)
