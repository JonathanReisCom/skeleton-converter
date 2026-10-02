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

import json
import math
import zipfile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from pathlib import Path

from src import bundle
from src.in_godot import read_godot_skeleton
from src.in_dragonbones import read_skeleton as read_dragonbones
from src.in_skelform import read_skeleton as read_skelform
from src.in_spine import read_skeleton
from tests.ci_rig import PAGE_SIZE
from src.mesh_parity import mesh_parity
from src.model import godot_world_transforms
from src.out_godot import write_godot_scene
from src.out_spine import write_spine_json
from tests.ci_rig import PAGE, STEM, atlas_text, write_spine_export

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
    """Worst absolute difference across every world matrix entry.

    Compares over the SOURCE's bones: the writer may emit extra child bones
    for stacked attachments (eyes, capes), which have no source counterpart.
    """
    first = godot_world_transforms(source)
    second = godot_world_transforms(reloaded)
    assert set(source_names := [b.name for b in source.bones]) <= set(second)
    return max(
        abs(first[name][index] - second[name][index])
        for name in source_names for index in range(6)
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


def test_skelform_bone_draws_the_equipped_attachment(tmp_path):
    """The one visual a bone points at is the attachment the source DRAWS.

    A bone carries a single ``visuals_id`` and the Hidden keys toggle that
    bone, so pointing it at a slot's *first* alternative hides the rig
    whenever the source equips a different one — Spine lists alternatives
    freely and often equips a later entry. The fixture flips the source so the
    equipped attachment is the second, which is the case that failed.
    """
    json_path = write_spine_export(tmp_path)
    rig = json.loads(Path(json_path).read_text(encoding="utf-8"))
    rig["slots"][0]["attachment"] = "torso-belt"        # not the first entry
    Path(json_path).write_text(json.dumps(rig), encoding="utf-8")

    out = tmp_path / "flipped"
    bundle.convert(str(json_path), "spine", "skelform", str(out), name=STEM,
                   step=lambda _line: None)
    with zipfile.ZipFile(out / "output" / f"{STEM}.skf") as archive:
        armature = json.loads(archive.read("armature.json"))
    bone = next(b for b in armature["bones"] if b["name"] == "torso")
    visual = armature["visuals"][bone["visuals_id"]]
    assert visual["tex"] == "torso-belt", visual
    assert bone["hidden"] is False, bone


def test_bundle_without_a_page_image_is_still_a_bundle(tmp_path):
    """A rig read without its page must not crash the Spine target.

    The writer names a placeholder page when no image is found; the bundle
    step then moved a file nobody wrote (FileNotFoundError) — an upload
    without the page is an ordinary case, so the bundle is written and every
    file it reports must exist.
    """
    json_path = write_spine_export(tmp_path)
    (tmp_path / PAGE).unlink()          # the rig arrives without its page
    out = tmp_path / "bundle"
    result = bundle.convert(str(json_path), "spine", "spine", str(out),
                            name=STEM, step=lambda _line: None)

    assert (out / "output" / f"{STEM}.json").is_file()
    assert (out / "output" / f"{STEM}.atlas").is_file()
    assert any("no page image" in note for note in result.notes), result.notes
    missing = [str(path) for path in result.files if not path.exists()]
    assert not missing, missing


def test_rotated_atlas_region_reaches_the_vertex_uv(tmp_path):
    """A `rotate: 90` region's uv must survive the page rewrite.

    Spine's atlas packs a region a quarter turn off and its runtime spins the
    sprite back while sampling. SkelForm's runtimes have no such flag, so the
    writer turns the PIXELS upright instead (`png.rotate_quarter`, clockwise
    false: a source pixel (column, row) lands at (row, W-1-column)) and the uv
    has to follow — a vertex that sampled (a, b) of the packed rect samples
    (b, 1 - a) of the sprite's own rect.

    The fixture declares the mesh's region rotated in the atlas. Its normalized
    source corners are (0, 1), (0, 0), (1, 0), (1, 1), so the vertices must
    sample (1, 1), (0, 1), (0, 0), (1, 0). Recomputing the uv from the written
    geometry instead (which is what made unwrapped sprites draw squeezed and
    off their own rect) ends up a quarter turn from this.
    """
    json_path = write_spine_export(tmp_path)
    lines = atlas_text().splitlines()
    patched = []
    for line in lines:
        patched.append(line)
        if line.startswith("bounds:") and patched[-2] == "arm-glove":
            patched.append("rotate: 90")
    (tmp_path / f"{STEM}.atlas").write_text("\n".join(patched) + "\n",
                                            encoding="utf-8")

    out = tmp_path / "rotated"
    bundle.convert(str(json_path), "spine", "skelform", str(out), name=STEM,
                   step=lambda _line: None)
    with zipfile.ZipFile(out / "output" / f"{STEM}.skf") as archive:
        armature = json.loads(archive.read("armature.json"))
    visual = next(v for v in armature["visuals"] if v["tex"] == "arm-glove")
    assert [(round(vert["uv"]["x"], 6), round(vert["uv"]["y"], 6))
            for vert in visual["vertices"]] == \
        [(1.0, 1.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0)], visual["vertices"]


def test_a_rotated_region_is_written_with_its_faces(tmp_path):
    """A region quad the writer cannot draw as a texture rect needs triangles.

    SkelForm's region draw is axis-aligned against the bone and ignores the
    attachment's own `rotation`, so any region quad that is not a multiple of
    90 has to travel as geometry — and a Spine region attachment carries no
    `triangles` key (only meshes do). Emitting the four vertices without their
    two triangles produces a visual the runtime never rasterizes: the hero's
    arms, hands, legs and feet were all silently invisible, and the rig drew a
    head and a cape over nothing.

    The fixture's `torso-belt` is a region rotated 15 degrees, which is exactly
    that case. Every visual with vertices must carry faces.
    """
    json_path = write_spine_export(tmp_path)
    out = tmp_path / "quads"
    bundle.convert(str(json_path), "spine", "skelform", str(out), name=STEM,
                   step=lambda _line: None)
    with zipfile.ZipFile(out / "output" / f"{STEM}.skf") as archive:
        armature = json.loads(archive.read("armature.json"))
    faceless = [visual["tex"] for visual in armature["visuals"]
                if visual.get("vertices") and not visual.get("indices")]
    assert not faceless, f"visuals with vertices and no faces: {faceless}"


def test_a_region_quad_pairs_its_uv_with_the_right_corner(tmp_path):
    """Corner 0 of the quad samples the rect's BOTTOM-left, not its top-left.

    The runtime samples a region rect by walking its uv corners in the same
    order the geometry does (`RegionAttachment.updateRegion`: for `degrees` 0
    the first offset is `(localX, localY)` and its uv is `(u, v2)`, the rect's
    left/bottom). Pairing them one step off flips v, and every region the
    writer has to draw as geometry comes out upside down — the hero's hands,
    feet and limbs were all mirrored while the meshes beside them were right.
    """
    json_path = write_spine_export(tmp_path)
    out = tmp_path / "corners"
    bundle.convert(str(json_path), "spine", "skelform", str(out), name=STEM,
                   step=lambda _line: None)
    with zipfile.ZipFile(out / "output" / f"{STEM}.skf") as archive:
        armature = json.loads(archive.read("armature.json"))
    visual = next(v for v in armature["visuals"] if v["tex"] == "torso-base")
    assert [(round(vert["uv"]["x"], 6), round(vert["uv"]["y"], 6))
            for vert in visual["vertices"]] == \
        [(0.0, 1.0), (0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], visual["vertices"]


def test_a_slot_the_setup_pose_does_not_draw_starts_hidden(tmp_path):
    """The file's `hidden` is the SETUP pose's answer, not the animation's.

    A slot draws its setup attachment and nothing else until an attachment
    timeline equips another entry. The fixture's `arm` slot does exactly that:
    setup is `arm-base` and the idle animation equips `arm-glove`. Flagging
    every entry any animation ever equips as visible made the setup pose draw
    the glove — art the source leaves out, and a bigger framing box, which is
    why the two panes of the comparison never lined up.
    """
    json_path = write_spine_export(tmp_path)
    out = tmp_path / "hidden"
    bundle.convert(str(json_path), "spine", "skelform", str(out), name=STEM,
                   step=lambda _line: None)
    with zipfile.ZipFile(out / "output" / f"{STEM}.skf") as archive:
        armature = json.loads(archive.read("armature.json"))
    drawn = {bone.get("tex"): bone for bone in armature["bones"]
             if bone.get("tex")}
    assert drawn["arm-glove"]["hidden"] is True, drawn["arm-glove"]
    assert drawn["arm-glove"]["init_hidden"] is True, drawn["arm-glove"]
    assert drawn["arm-base"]["hidden"] is False, drawn["arm-base"]


def test_skelform_target_carries_the_rig_and_its_page(tmp_path):
    """Every wired direction that writes a bundle writes a loadable one.

    The SkelForm target is one archive, so besides the numeric round trip this
    asserts what makes the file loadable at all: the armature and the rig's
    page inside it. The page is found two different ways — beside the atlas
    for a Spine source, beside the scene for a Godot one — and a target that
    merely references a page without copying it produces a scene Godot cannot
    texture, so the Godot leg checks its own copy too.
    """
    source = _spine_model(tmp_path)
    out = tmp_path / "bundle"
    bundle.convert(str(tmp_path / f"{STEM}.json"), "spine", "skelform",
                   str(out), name=STEM)
    written = out / "output" / f"{STEM}.skf"
    with zipfile.ZipFile(written) as archive:
        members = archive.namelist()
        assert {"armature.json", "readme.md"} <= set(members)
        # SkelForm's runtimes find pages by looking for "atlas" in the member
        # name (its web player loads only those), so a page embedded under the
        # rig's own name is invisible to them and the rig draws untextured.
        pages = [name for name in members if name.endswith(".png")]
        assert pages and all("atlas" in name for name in pages), members
        armature = json.loads(archive.read("armature.json"))
        atlases = armature["atlases"]
        # Every visual carries the pivot fields, region or mesh: SkelForm's
        # players read them while drawing, and a region has no geometry to
        # hint that it needs one (its absence stopped the player's draw loop).
        for visual in armature["visuals"]:
            for field in ("pivot_pos", "pivot_rot", "pivot_scale", "init_tex"):
                assert field in visual, (field, visual)
        assert [a["filename"] for a in atlases] == pages, atlases

    # The bundle is playable: SkelForm's own web player, pinned by commit.
    shell = (out / "index.html").read_text(encoding="utf-8")
    assert f"output/{STEM}.skf" in shell
    assert "skelform-js@" in shell and "skelform-web-player@" in shell

    reloaded = read_skelform(str(written))
    # The writer may append child bones for stacked attachments (more visuals
    # than bones): the SOURCE's bones must all survive, in order, first; the
    # extras are the emitted helper bones.
    reloaded_names = [b.name for b in reloaded.bones]
    assert reloaded_names[:len(source.bones)] == [b.name for b in source.bones]
    assert _worst_bone(source, reloaded) < TOLERANCE
    _assert_same_animations(source, reloaded)
    # UVs are atlas pixels in this format: one outside the page means the
    # page or the atlas metadata the reader used is wrong.
    width, height = PAGE_SIZE
    for attachment in reloaded.attachments:
        for u, v in attachment.uv:
            assert 0.0 <= u <= width and 0.0 <= v <= height, \
                (attachment.name, u, v, PAGE_SIZE)

    # Godot source: the page lives beside the scene, and the scene the .skf
    # came from must get its own copy.
    _, scene_model = _godot_model(tmp_path)
    scene_out = tmp_path / "scene-bundle"
    bundle.convert(str(tmp_path / f"{STEM}.tscn"), "godot", "skelform",
                   str(scene_out), name=STEM)
    with zipfile.ZipFile(scene_out / "output" / f"{STEM}.skf") as archive:
        # The scene's page travels under the official atlas name (see above):
        # the rig's own file name would make the runtimes skip it.
        assert [n for n in archive.namelist() if n.endswith(".png")] == \
            ["atlas0.png"], archive.namelist()
    godot_out = tmp_path / "godot-bundle"
    bundle.convert(str(tmp_path / f"{STEM}.tscn"), "godot", "godot",
                   str(godot_out), name=STEM)
    assert (godot_out / "output" / PAGE).is_file()


def test_dragonbones_target_carries_the_rig_and_its_page(tmp_path):
    """The DragonBones leg writes a bundle its own runtime can play.

    Beyond the numeric round trip, this asserts what makes the bundle loadable:
    the data, the texture atlas and the page, with the atlas naming a region
    for every display (the runtime looks a display's texture up by `path`, and
    a mesh without one draws nothing). The shell has to link the format's own
    runtime, pinned by commit, because the pane's job is to show the file this
    leg wrote, read by the format's own code.
    """
    source = _spine_model(tmp_path)
    out = tmp_path / "db-bundle"
    bundle.convert(str(tmp_path / f"{STEM}.json"), "spine", "dragonbones",
                   str(out), name=STEM)
    data = out / "output" / f"{STEM}_ske.json"
    atlas = out / "output" / f"{STEM}_tex.json"
    page = out / "output" / PAGE
    assert data.is_file() and atlas.is_file() and page.is_file()

    document = json.loads(data.read_text(encoding="utf-8"))
    assert document["version"] == "5.5"
    assert document["armature"][0]["name"] == STEM
    armature = document["armature"][0]
    assert [bone["name"] for bone in armature["bone"]] \
        == [bone.name for bone in source.bones]
    assert len(armature["slot"]) == len({(a.slot or a.name)
                                         for a in source.attachments})
    # Every slot names the bone it hangs from, and a slot with no setup
    # attachment must say so: the parser's default is 0, which would draw the
    # first entry of a slot the source leaves empty.
    bone_names = {bone["name"] for bone in armature["bone"]}
    for slot in armature["slot"]:
        assert slot["parent"] in bone_names, slot
        assert slot["displayIndex"] >= -1, slot

    textures = {entry["name"]: entry
                for entry in json.loads(atlas.read_text(encoding="utf-8"))["SubTexture"]}
    for slot in armature["skin"][0]["slot"]:
        for display in slot["display"]:
            if not display:
                continue
            assert display["path"] in textures, display
            region = textures[display["path"]]
            assert region["width"] > 0 and region["height"] > 0, region
            if display["type"] == "image":
                # A sprite is drawn from the region rect: it must state its
                # pivot, because the parser's default is the centre (0.5, 0.5)
                # while the writer anchors the top-left corner.
                assert display["pivot"] == {"x": 0.0, "y": 0.0}, display
                assert display["transform"]["skY"] == display["transform"]["skX"], \
                    display
            else:
                assert display["type"] == "mesh", display
                # A weighted mesh is placed by the bind, so both matrices and
                # one weight list per vertex have to be there.
                assert len(display["slotPose"]) == 6, display
                assert len(display["bonePose"]) % 7 == 0, display
                # One `[count, (bone, weight)…]` group per vertex, in the
                # order the vertices come in (the parser walks it that way).
                weights, cursor, vertices = display["weights"], 0, 0
                while cursor < len(weights):
                    count = int(weights[cursor])
                    assert count >= 1, display
                    for _ in range(count):
                        assert 0 <= weights[cursor + 1] < len(armature["bone"]), display
                        assert weights[cursor + 2] > 0, display
                        cursor += 2
                    cursor += 1
                    vertices += 1
                assert vertices == len(display["vertices"]) // 2, display

    shell = (out / "index.html").read_text(encoding="utf-8")
    assert f"output/{STEM}_ske.json" in shell
    assert f"output/{STEM}_tex.json" in shell
    assert "DragonBonesJS@" in shell and "pixi.js@" in shell

    reloaded = read_dragonbones(str(data))
    assert _worst_bone(source, reloaded) < TOLERANCE
    _assert_same_geometry(source, reloaded)
    _assert_same_animations(source, reloaded)
    width, height = PAGE_SIZE
    for attachment in reloaded.attachments:
        for u, v in attachment.uv:
            assert 0.0 <= u <= width and 0.0 <= v <= height, \
                (attachment.name, u, v, PAGE_SIZE)

    # Godot source: the page lives beside the scene, and the bundle has to
    # carry a copy of it — a data file that merely names a page is unplayable.
    _godot_model(tmp_path)
    scene_out = tmp_path / "db-scene-bundle"
    bundle.convert(str(tmp_path / f"{STEM}.tscn"), "godot", "dragonbones",
                   str(scene_out), name=STEM)
    assert (scene_out / "output" / f"{STEM}_ske.json").is_file()
    assert (scene_out / "output" / PAGE).is_file()


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


def test_vertex_morph_reaches_godot_as_a_polygon_track(tmp_path):
    """A Spine `deform` becomes an animatable `polygon` array in the scene.

    The morph is per vertex and lives inside `attachments`; a conversion that
    drops it passes every bone check and still loses the face. The assert is the
    DELTA'S LENGTH, which the bone's rotation and scale cannot change: the
    fixture moves one vertex by (3, 4), so the emitted key must sit 5 units away
    from the base polygon at that vertex. A writer that swallows the delta (a
    zero weight, an offset read as a vertex index) leaves that length at zero.
    """
    json_path = write_spine_export(tmp_path)
    model = read_skeleton(str(json_path), str(tmp_path / f"{STEM}.atlas"))

    glove = next(att for att in model.attachments if att.name == "arm-glove")
    assert (glove.deform or {}).get("idle"), "the fixture's morph did not survive reading"

    out = tmp_path / "godot"
    write_godot_scene(model, str(out / f"{STEM}.tscn"),
                      str(tmp_path / PAGE))
    text = (out / f"{STEM}.tscn").read_text()

    at = text.find("arm-glove:polygon")
    assert at >= 0, "no polygon track was written for the morphed mesh"
    # Scan forward from the track's own path: the FIRST keys block in the file
    # belongs to another track, and matching it made this test read a Vector2
    # track's values. The window runs to the end of the file because a baked
    # morph track is as long as its animation (one key per frame), and a fixed
    # window dropped the closing brace and read no keys at all.
    block = text[at:]
    keys = re.search(r'"times": PackedFloat32Array\(([^)]*)\)'
                     r'[\s\S]*?"values": \[(.*?)\]\n\}', block)
    assert keys, "the polygon track carries no keys"
    times = [float(x) for x in keys.group(1).split(",")]
    arrays = re.findall(r"PackedVector2Array\(([^)]*)\)", keys.group(2))
    assert len(times) == len(arrays) >= 2, (times, arrays)

    points = [[float(v) for v in a.split(",")] for a in arrays]
    pairs = [[(p[i], p[i + 1]) for i in range(0, len(p), 2)] for p in points]
    base = pairs[times.index(min(times))]
    moved = pairs[times.index(max(times))]
    moved_length = max(
        ((moved[i][0] - base[i][0]) ** 2 + (moved[i][1] - base[i][1]) ** 2) ** 0.5
        for i in range(min(len(base), len(moved))))
    assert abs(moved_length - 5.0) < 1e-3, (
        f"the morph moved a vertex by {moved_length:.4f}, not 5")


def test_godot_pane_frames_with_the_shared_margin():
    """The Godot pane's fallback fit must use the HUD's own MARGIN.

    The Spine and SkelForm panes take their scale from `hud.fitScale`; the
    Godot wrapper lives in GDScript and calls the same function through the JS
    bridge, but its standalone/headless fallback re-states the margin. A
    hardcoded 1.2 there drew this pane's rig ~2% smaller than the two beside
    it, and the whole point of the comparison is that the three frame alike.
    """
    hud = (ROOT / "src" / "static" / "hud.js").read_text()
    wrapper = (ROOT / "src" / "godot_preview" / "main.gd").read_text()
    margin = re.search(r"const MARGIN = ([0-9.]+);", hud)
    assert margin, "hud.js no longer declares MARGIN"
    assert f"scale = {margin.group(1)} /" in wrapper, (
        f"the Godot wrapper's fallback fit does not use the HUD's "
        f"MARGIN ({margin.group(1)})")
