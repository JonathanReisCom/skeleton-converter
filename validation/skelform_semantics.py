"""A port of SkelForm's runtime semantics, used only to verify conversions.

Ported from the project's sources, not from its prose docs (the same rule the
Spine leg follows — see AGENTS.md, "port solvers from the runtime"):

- ``Animate`` / ``get_field_value`` / ``interp`` from ``src/utils.rs`` and
  ``src/shared.rs``: field values per frame, straight/stepped/bezier segments.
- ``Construct`` / ``reset_inheritance`` / ``inheritance`` from ``src/utils.rs``
  (and the dev-docs pseudocode): world transforms for a frame, ``ikRots``
  empty (inverse kinematics is NOT modelled here — callers must exclude the
  bones an IK family drives).
- ``construct_verts`` / ``inherit_vert`` / the pivot term from ``Draw`` and
  ``src/renderer.rs``: a visual's vertices in world space.

Everything here stays in SkelForm's own space (Y-up, CCW-positive, radians) so
the comparison against a converted rig is done by mirroring once, in the
caller.
"""

from __future__ import annotations

import json
import math
import zipfile


def _cubic(t, p1, p2):
    u = 1.0 - t
    return 3.0 * u * u * t * p1 + 3.0 * u * t * t * p2 + t * t * t


def _cubic_d(t, p1, p2):
    u = 1.0 - t
    return 3.0 * u * u * p1 + 6.0 * u * t * (p2 - p1) + 3.0 * t * t * (1.0 - p2)


def interp(current, maximum, start_val, end_val, start_handle, end_handle):
    """``utils::interp`` — bezier with Newton-Raphson, or a snapped hold."""
    if start_handle["y"] == 999.0 and end_handle["y"] == 999.0:
        return start_val
    if maximum == 0 or current >= maximum:
        return end_val
    initial = current / maximum
    t = initial
    for _ in range(5):
        x = _cubic(t, start_handle["x"], end_handle["x"])
        dx = _cubic_d(t, start_handle["x"], end_handle["x"])
        if abs(dx) < 1e-5:
            break
        t -= (x - initial) / dx
        t = min(1.0, max(0.0, t))
    return start_val + (end_val - start_val) * _cubic(t, start_handle["y"], end_handle["y"])


def field_value(armature, animation, bone_id, element, frame, default):
    """``get_field_value``: the value of one bone+element at ``frame``."""
    keyframes = animation["keyframes"]
    prev = next((i for i in range(len(keyframes) - 1, -1, -1)
                 if keyframes[i]["frame"] <= frame
                 and keyframes[i]["bone_id"] == bone_id
                 and keyframes[i]["element"] == element), None)
    following = next((i for i, kf in enumerate(keyframes)
                      if kf["frame"] > frame
                      and kf["bone_id"] == bone_id
                      and kf["element"] == element), None)
    if prev is None:
        prev = following
    if following is None:
        following = prev
    if prev is None and following is None:
        return default
    return interp(frame - keyframes[prev]["frame"],
                  keyframes[following]["frame"] - keyframes[prev]["frame"],
                  keyframes[prev]["value"], keyframes[following]["value"],
                  keyframes[following]["start_handle"],
                  keyframes[following]["end_handle"])


def locals_at(armature, animation, frame):
    """``Animate``: every bone's local fields at ``frame`` (SkelForm space)."""
    bones = [dict(bone) for bone in armature["bones"]]
    for bone in bones:
        bone["pos"] = dict(bone["init_pos"])
        bone["rot"] = float(bone["init_rot"])
        bone["scale"] = dict(bone["init_scale"])
        for element, field, axis in (("PositionX", "pos", "x"),
                                     ("PositionY", "pos", "y"),
                                     ("Rotation", "rot", None),
                                     ("ScaleX", "scale", "x"),
                                     ("ScaleY", "scale", "y")):
            default = bone["rot"] if field == "rot" else bone[field][axis]
            value = field_value(armature, animation, bone["id"], element,
                                frame, default)
            if field == "rot":
                bone["rot"] = value
            else:
                bone[field][axis] = value
    return bones


def per_axis_key_bones(armature):
    """Bones whose X and Y channels are keyed at *different* frames.

    The canonical model keeps one key list per track (both components share
    every key), so such a bone can only be represented by resampling the other
    axis — an approximation the harness reports instead of hiding.
    """
    affected = set()
    for animation in armature.get("animations") or []:
        frames = {}
        for keyframe in animation.get("keyframes") or []:
            frames.setdefault((keyframe["bone_id"], keyframe["element"]),
                              []).append(keyframe["frame"])
        for bone in armature["bones"]:
            for axis in ("Position", "Scale"):
                first = sorted(frames.get((bone["id"], axis + "X")) or [])
                second = sorted(frames.get((bone["id"], axis + "Y")) or [])
                # Affected when the two axes are keyed differently — including
                # the common case of only ONE axis being keyed at all (the
                # runtime then scales/translates the other axis by its
                # neighbour's curve, which a combined track cannot express).
                if (first != second) and (first or second):
                    affected.add(bone["id"])
    return affected


def non_trs_bones(armature, animation, frame, tolerance=1e-6):
    """Bones whose inherited transform is NOT a standard TRS composition.

    ``inheritance()`` adds rotations and multiplies scales componentwise and
    then rotates the already-scaled offset by the parent's rotation. That
    equals ``parent_matrix * local_matrix`` only while the parent's scale is
    uniform (or the child's rotation is zero); with a non-uniform animated
    scale — common in these rigs — the two disagree, and a converter writing
    ordinary bone transforms cannot reproduce the runtime exactly.
    """
    bones = locals_at(armature, animation, frame)
    by_id = {bone["id"]: bone for bone in bones}
    from src.model import compose

    trs = {}
    inherited = {}
    non_trs = set()
    for bone in bones:
        parent_id = bone["parent_id"]
        if parent_id == -1:
            trs[bone["id"]] = compose((bone["pos"]["x"], bone["pos"]["y"]),
                                      bone["rot"],
                                      (bone["scale"]["x"], bone["scale"]["y"]))
            inherited[bone["id"]] = (bone["pos"]["x"], bone["pos"]["y"],
                                     bone["rot"], bone["scale"]["x"],
                                     bone["scale"]["y"])
            continue
        p_pos = (inherited[parent_id][0], inherited[parent_id][1])
        p_rot = inherited[parent_id][2]
        p_scale = (inherited[parent_id][3], inherited[parent_id][4])
        rot = -bone["rot"] if p_scale[0] < 0 else bone["rot"]
        rot += p_rot
        scale = (bone["scale"]["x"] * p_scale[0], bone["scale"]["y"] * p_scale[1])
        sx, sy = bone["pos"]["x"] * p_scale[0], bone["pos"]["y"] * p_scale[1]
        cos, sin = math.cos(p_rot), math.sin(p_rot)
        inherited[bone["id"]] = (sx * cos - sy * sin + p_pos[0],
                                 sx * sin + sy * cos + p_pos[1], rot,
                                 scale[0], scale[1])
        # The reference side is the model's own TRS composition, so this
        # classifies exactly what a converted rig can and cannot reproduce.
        from src.model import compose, multiply

        trs_matrix = multiply(
            trs[parent_id],
            compose((bone["pos"]["x"], bone["pos"]["y"]), bone["rot"],
                    (bone["scale"]["x"], bone["scale"]["y"])))
        trs[bone["id"]] = trs_matrix
        if (abs(trs_matrix[4] - inherited[bone["id"]][0]) > tolerance
                or abs(trs_matrix[5] - inherited[bone["id"]][1]) > tolerance):
            non_trs.add(bone["id"])
    return non_trs


def pose_bones(armature, animation, frame):
    """``Animate`` + ``Construct``: local fields, then world transforms.

    Returns ``{bone_id: {"pos", "rot", "scale"}}`` in SkelForm space, with
    rotations in radians. Bones driven by an IK family keep their animated
    values (this port does not solve IK).
    """
    bones = [dict(bone) for bone in armature["bones"]]
    for bone in bones:
        bone["pos"] = dict(bone["init_pos"])
        bone["rot"] = float(bone["init_rot"])
        bone["scale"] = dict(bone["init_scale"])
        for element, field, axis in (("PositionX", "pos", "x"),
                                     ("PositionY", "pos", "y"),
                                     ("Rotation", "rot", None),
                                     ("ScaleX", "scale", "x"),
                                     ("ScaleY", "scale", "y")):
            default = bone["rot"] if field == "rot" else bone[field][axis]
            value = field_value(armature, animation, bone["id"], element,
                                frame, default)
            if field == "rot":
                bone["rot"] = value
            else:
                bone[field][axis] = value

    by_id = {bone["id"]: bone for bone in bones}
    world = {}
    for bone in bones:
        pos = dict(bone["pos"])
        rot = bone["rot"]
        scale = dict(bone["scale"])
        parent_id = bone["parent_id"]
        if parent_id != -1:
            parent = world[parent_id]
            # ``inheritance()``: rot accumulates, scale multiplies, position is
            # scaled then rotated then offset by the parent's position.
            if parent["scale"]["x"] < 0:
                rot = -rot
            rot += parent["rot"]
            scale["x"] *= parent["scale"]["x"]
            scale["y"] *= parent["scale"]["y"]
            sx, sy = pos["x"] * parent["scale"]["x"], pos["y"] * parent["scale"]["y"]
            cos, sin = math.cos(parent["rot"]), math.sin(parent["rot"])
            pos = {"x": sx * cos - sy * sin + parent["pos"]["x"],
                   "y": sx * sin + sy * cos + parent["pos"]["y"]}
        world[bone["id"]] = {"pos": pos, "rot": rot, "scale": scale}
    return world


def visual_world_vertices(armature, visuals_index, style_by_name, bone, world):
    """``construct_verts`` + the pivot term: a visual's vertices in world space.

    A visual without vertices is a texture rect: the editor keeps it as
    ``+-size/2`` centred on the bone (``utils::bone_meshes_edited``).
    """
    visual = armature["visuals"][visuals_index]
    texture = style_by_name.get(visual.get("tex"))
    if texture is None:
        return []
    size = (texture["size"]["x"], texture["size"]["y"])
    pivot_pos = visual.get("pivot_pos") or {"x": 0.0, "y": 0.0}
    pivot_scale = visual.get("pivot_scale") or {"x": 1.0, "y": 1.0}
    pivot_rot = float(visual.get("pivot_rot", 0.0) or 0.0)
    state = world[bone["id"]]

    if visual.get("vertices"):
        local = [(v["pos"]["x"], v["pos"]["y"]) for v in visual["vertices"]]
        # Bound vertices inherit the bind's bone first (weights are folded by
        # the caller); unbound ones inherit their own bone.
        weight_of = {}
        for bind in visual.get("binds") or []:
            if bind.get("is_path"):
                continue
            for entry in bind.get("verts") or []:
                weight_of[entry["id"]] = (bind["bone_id"], entry["weight"])
        points = []
        for index, vertex in enumerate(visual["vertices"]):
            bound = weight_of.get(vertex["id"])
            if bound and bound[1] < 1.0:
                # Partial weights blend two inheritances; kept simple here.
                points.append(_inherit(local[index], pivot_rot, pivot_scale,
                                       world[bound[0]]))
            else:
                owner = world[bound[0]] if bound else state
                points.append(_inherit(local[index], pivot_rot, pivot_scale, owner))
    else:
        half_x, half_y = size[0] / 2.0, size[1] / 2.0
        # SkelForm is Y-up, so the corner owning uv (0, 0) — the image's
        # top-left — sits at +y. Vertex i of a region therefore corresponds to
        # vertex i of the converted attachment.
        local = [(-half_x, half_y), (half_x, half_y),
                 (half_x, -half_y), (-half_x, -half_y)]
        points = [_inherit(point, pivot_rot, pivot_scale, state) for point in local]

    # The pivot translation is added in world space: rotate(pivot_pos * size,
    # bone.rot * left) * bone.scale  (renderer.rs `final_pivot`).
    left = -1.0 if state["scale"]["x"] < 0 else 1.0
    px, py = pivot_pos["x"] * size[0], pivot_pos["y"] * size[1]
    cos, sin = math.cos(state["rot"] * left), math.sin(state["rot"] * left)
    shift = ((px * cos - py * sin) * state["scale"]["x"],
             (px * sin + py * cos) * state["scale"]["y"])
    return [(x + shift[0], y + shift[1]) for x, y in points]


def _inherit(point, pivot_rot, pivot_scale, bone):
    """``inherit_vert``: scale, then rotate, then translate."""
    x = point[0] * bone["scale"]["x"] * pivot_scale["x"]
    y = point[1] * bone["scale"]["y"] * pivot_scale["y"]
    rot = bone["rot"] + pivot_rot
    cos, sin = math.cos(rot), math.sin(rot)
    return (x * cos - y * sin + bone["pos"]["x"],
            x * sin + y * cos + bone["pos"]["y"])


def style_textures(armature):
    """Texture name -> rect, scanning styles in order (first match wins)."""
    out = {}
    for style in armature.get("styles") or []:
        for texture in style.get("textures") or []:
            out.setdefault(texture["name"], texture)
    return out


def load(path):
    source = str(path)
    if source.lower().endswith(".skf") or zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as bundle:
            return json.loads(bundle.read("armature.json"))
    return json.loads(open(source, encoding="utf-8").read())


def with_descendants(armature, bone_ids):
    """The given bone ids plus everything below them.

    A parent's approximation (a resampled axis, a non-TRS inheritance) shows up
    in every descendant's world transform, so classification has to follow the
    hierarchy, not just look at the bone itself.
    """
    affected = set(bone_ids)
    changed = True
    while changed:
        changed = False
        for bone in armature["bones"]:
            if bone["id"] in affected or bone["parent_id"] not in affected:
                continue
            affected.add(bone["id"])
            changed = True
    return affected


def ik_driven_bones(armature):
    """Bone ids whose rotation (or a descendant's) the IK solver overrides."""
    driven = set()
    for family in armature.get("inverse_kinematics") or []:
        if family.get("target_id", -1) == -1:
            continue
        driven.update(family.get("bone_ids") or [])
    by_id = {bone["id"]: bone for bone in armature["bones"]}
    changed = True
    while changed:
        changed = False
        for bone in armature["bones"]:
            if bone["id"] in driven or bone["parent_id"] not in driven:
                continue
            driven.add(bone["id"])
            changed = True
    return driven
