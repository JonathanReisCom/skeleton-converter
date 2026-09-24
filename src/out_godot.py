"""Write the canonical model as a Godot 4 Skeleton2D scene (.tscn)."""

from __future__ import annotations

import math
from pathlib import Path

from .model import Skeleton, godot_transform2d, invert, multiply
from .out_spine import group_triangles


def _render_tscn(bone_nodes, polygon_nodes, animation_resources, animation_refs, texture_path):
    lines = ['[gd_scene format=3]', ""]
    lines.append(f'[ext_resource type="Texture2D" path="{texture_path}" id="1"]')
    lines.append("")
    for entry in animation_resources:
        lines.extend(entry["lines"])
        lines.append("")
    lines.append('[sub_resource type="AnimationLibrary" id="AnimationLibrary_1"]')
    lines.append("_data = {")
    for anim_name, resource_id in animation_refs:
        lines.append(f'&"{anim_name}": SubResource("{resource_id}"),')
    lines.append("}")
    lines.append("")
    # Root container: the track paths and external samplers address bones as
    # "Sprite2D/Skeleton2D/<bone>" relative to the scene root, so the root must
    # be a container node, not Sprite2D itself. Without it every track path
    # dangles ("parent path has vanished") and the scene instantiates empty.
    lines.append('[node name="SkeletonRoot" type="Node2D"]')
    lines.append('[node name="Sprite2D" type="Node2D" parent="."]')
    lines.append('[node name="Skeleton2D" type="Skeleton2D" parent="Sprite2D"]')
    for node in bone_nodes:
        lines.append(f'[node name="{node["name"]}" type="Bone2D" parent="{node["parent"]}"]')
        lines.extend(node["props"])
        lines.append("")
    lines.append('[node name="Polygons" type="Node2D" parent="Sprite2D"]')
    lines.append("")
    for node in polygon_nodes:
        lines.append(f'[node name="{node["name"]}" type="Polygon2D" parent="{node["parent"]}"]')
        lines.extend(node["props"])
        lines.append("")
    lines.append('[node name="AnimationPlayer" type="AnimationPlayer" parent="."]')
    lines.append('libraries/ = SubResource("AnimationLibrary_1")')
    lines.append("")
    return "\n".join(lines)


def write_godot_scene(model: Skeleton, output_path: str, texture_path: str, **kwargs) -> None:
    """Emit a .tscn with SkeletonRoot → Sprite2D → Skeleton2D → bones and
    Polygons → Polygon2D per attachment, plus an AnimationPlayer."""
    world = _godot_rest_worlds(model)
    bone_nodes = _emit_bones(model, world)
    polygon_nodes = _emit_attachments(model, world, texture_path)
    animation_resources, animation_refs = _emit_animations(model)
    content = _render_tscn(bone_nodes, polygon_nodes, animation_resources,
                           animation_refs, texture_path)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# bones
# ---------------------------------------------------------------------------


def _godot_rest_worlds(model) -> dict:
    """bone name → world matrix at rest (product of local transforms)."""
    world = {}
    for bone in model.bones:
        parent = world.get(bone.parent, (1, 0, 0, 1, 0, 0))
        cos, sin = math.cos(math.radians(bone.rotation_deg)), math.sin(math.radians(bone.rotation_deg))
        sx, sy = bone.scale
        world[bone.name] = multiply(parent, (
            cos * sx, -sin * sx,
            sin * sy, cos * sy,
            bone.position[0], bone.position[1],
        ))
    return world


def _emit_bones(model, world):
    nodes = []
    node_by_bone = {}
    for bone in model.bones:
        parent_path = "Sprite2D/Skeleton2D"
        if bone.parent:
            parent_path = node_by_bone[bone.parent]
        node_by_bone[bone.name] = f"{parent_path}/{bone.name}"
        cos = math.cos(math.radians(bone.rotation_deg))
        sin = math.sin(math.radians(bone.rotation_deg))
        props = [
            f"position = Vector2({round(bone.position[0], 6)}, {round(bone.position[1], 6)})",
            f"rotation = {round(math.radians(bone.rotation_deg), 9)}",
        ]
        if bone.length:
            props += [
                "auto_calculate_length_and_angle = false",
                f"length = {round(bone.length, 6)}",
                f"bone_angle = {round(bone.rotation_deg, 6)}",
            ]
        # Godot's Transform2D stores columns: x = (cos, sin), y = (-sin, cos).
        props.append(
            "rest = Transform2D({}, {}, {}, {}, {}, {})".format(
                round(cos * bone.scale[0], 6), round(sin * bone.scale[0], 6),
                round(-sin * bone.scale[1], 6), round(cos * bone.scale[1], 6),
                round(bone.position[0], 6), round(bone.position[1], 6),
            )
        )
        if bone.inherit != "normal":
            props.append(f'metadata/spine_inherit = "{bone.inherit}"')
        nodes.append({"name": bone.name, "type": "Bone2D", "parent": parent_path, "props": props})
    return nodes


# ---------------------------------------------------------------------------
# attachments as Polygon2D
# ---------------------------------------------------------------------------


def _emit_attachments(model, world, texture_path):
    polygon_nodes = []
    for att in model.attachments:
        polygon = att["polygon"]
        uv = att["uv"]
        weights = att["weights"]
        offset = att["position"]

        # Compute each vertex's world position at rest, then convert to
        # bone-local coordinates so the skinning is correct.
        polygon_world = (1, 0, 0, 1, offset[0], offset[1])
        world_points = []
        for vertex in polygon:
            world_points.append((polygon_world[0] * vertex[0] + polygon_world[1] * vertex[1] + polygon_world[4],
                                 polygon_world[2] * vertex[0] + polygon_world[3] * vertex[1] + polygon_world[5]))

        # Spine weighted mesh: local coords relative to each influencing bone.
        # In Godot, vertices are in Polygon2D local space and weights reference
        # bones. We emit vertices in Polygon2D local space (= skeleton space
        # since Polygons node is at origin) with per-bone weights.
        local_points = [(p[0], -p[1]) for p in world_points]

        uv_points = [[v[0], v[1]] for v in uv]

        props = [
            "position = Vector2(0, 0)",
            f'texture = ExtResource("1")',
            'skeleton = NodePath("../../Skeleton2D")',
            "polygon = PackedVector2Array(%s)" % ", ".join(
                f"{round(v, 6)}" for point in local_points for v in point
            ),
            "uv = PackedVector2Array(%s)" % ", ".join(
                f"{round(v, 6)}" for point in uv_points for v in point
            ),
        ]
        if att.get("polygons"):
            groups = att["polygons"]
            if groups and isinstance(groups[0], int):
                # flat triangle index list: emit as one PackedInt32Array
                props.append("polygons = [PackedInt32Array(%s)]" % ", ".join(str(i) for i in groups))
            else:
                props.append(
                    "polygons = [%s]" % ", ".join(
                        "PackedInt32Array(%s)" % ", ".join(str(i) for i in group)
                        for group in groups
                    )
                )
        if weights:
            parts = []
            for bone_name, vertex_weights in weights:
                weights_array = [
                    round(w, 6) for w in vertex_weights
                ]
                parts.append(
                    '"%s", PackedFloat32Array(%s)'
                    % (bone_name, ", ".join(str(w) for w in weights_array))
                )
            props.append("bones = [%s]" % ", ".join(parts))
        else:
            host_path = model.bones[0].path if model.bones else ""
            parts = ['"%s", PackedFloat32Array(%s)' % (
                host_path, ", ".join("1" for _ in local_points)
            )]
            props.append("bones = [%s]" % ", ".join(parts))

        polygon_nodes.append({
            "name": att["name"].capitalize(), "type": "Polygon2D",
            "parent": "Sprite2D/Polygons", "props": props,
        })
    return polygon_nodes


# ---------------------------------------------------------------------------
# animations
# ---------------------------------------------------------------------------


def _segment_handles(curve, time0: float, time1: float,
                     value0: float, value1: float, mirror: bool) -> tuple:
    """One spine segment -> Godot (out-handle, in-handle) value offsets.

    Spine control points are absolute time/value (CurveTimeline.setBezier);
    Godot handles are offsets from their key's time and value. ``mirror``
    flips the value axis (rotation and Y negate between the spaces), so a
    handle's value offset is negated with it.
    """
    dt = time1 - time0
    sign = -1.0 if mirror else 1.0
    if curve == "stepped":
        return (dt / 3.0, 0.0), (-dt / 3.0, 0.0)
    if isinstance(curve, (list, tuple)):
        cx1, cy1, cx2, cy2 = (float(c) for c in curve[:4])
        return ((cx1 - time0, sign * (cy1 - value0)),
                (cx2 - time1, sign * (cy2 - value1)))
    # Linear: a straight segment is its own handle pair.
    dv = (value1 - value0) * sign
    return (dt / 3.0, dv / 3.0), (-dt / 3.0, -dv / 3.0)


def _solve_handles(keys, times, spine_values, mirror: bool, axis: int = 0) -> tuple:
    """Godot out/in handle offsets for one axis track, in Godot value space.

    Translate curves carry 8 floats ([x1,y1,x2,y2] per segment); rotate uses
    4. ``axis`` selects which half a translate track reads.
    """
    n = len(keys)
    out_h = [(0.0, 0.0)] * n
    in_h = [(0.0, 0.0)] * n
    for i in range(n - 1):
        curve = keys[i].get("curve")
        if isinstance(curve, (list, tuple)) and len(curve) >= 8:
            curve = curve[axis * 4:(axis + 1) * 4]
        out, inn = _segment_handles(
            curve, times[i], times[i + 1],
            spine_values[i], spine_values[i + 1], mirror)
        out_h[i] = out
        in_h[i + 1] = inn
    return out_h, in_h


def _resource_id(anim_name: str) -> str:
    """Godot sub_resource ids accept only letters, digits, and underscores.

    Spine animation names are free-form ("head-turn", "crouch-from fall"), and
    using one raw in ``id=`` makes Godot reject the resource with "the scene
    unique ID must contain only letters, numbers, and underscores" — the scene
    then fails to instantiate. The animation's real name is preserved in the
    AnimationLibrary key; only the id is sanitized.
    """
    safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in anim_name)
    if not safe or safe[0].isdigit():
        safe = "anim_" + safe
    return f"Animation_{safe}"


def _emit_animations(model):
    animation_resources = []
    animation_refs = []
    for anim_index, (anim_name, animation) in enumerate(model.animations.items(), start=1):
        resource_id = _resource_id(anim_name)
        tracks = []
        for bone_name, props in animation.items():
            bone = next((b for b in model.bones if b.name == bone_name), None)
            if bone is None:
                continue
            bone_relative = bone.path
            setup_rot = bone.rotation_deg
            setup_pos = bone.position
            if props.get("rotate"):
                keys = props["rotate"]
                base = f"Sprite2D/Skeleton2D/{bone_relative}"
                if any(k.get("curve") for k in keys):
                    # Handle computation needs the key's value in spine offset
                    # space (where CurveTimeline control points live):
                    # angle = -(setup_spine + offset) and rotation_deg =
                    # -setup_spine, so offset = -angle + rotation_deg.
                    spine_values = [-k["angle"] + setup_rot for k in keys]
                    times = [k["time"] for k in keys]
                    out_h, in_h = _solve_handles(keys, times, spine_values, True)
                    tracks.append(("bezier", f"{base}:rotation_degrees",
                                   times, [k["angle"] for k in keys],
                                   out_h, in_h))
                else:
                    tracks.append(("value", f"{base}:rotation_degrees",
                                   [(k["time"], k["angle"]) for k in keys]))
            if props.get("translate"):
                keys = props["translate"]
                base = f"Sprite2D/Skeleton2D/{bone_relative}:position"
                if any(k.get("curve") for k in keys):
                    # x: spine = kx - setup_x; y mirrors: spine = -ky - setup_y.
                    # x: godot = spine + setup_x; y mirrors, so offset_y =
                    # -ky - setup_y_spine = -ky + position[1].
                    for axis, to_spine in (
                            (0, lambda k: k["x"] - setup_pos[0]),
                            (1, lambda k: -k["y"] + setup_pos[1])):
                        spine_values = [to_spine(k) for k in keys]
                        times = [k["time"] for k in keys]
                        out_h, in_h = _solve_handles(keys, times, spine_values,
                                                     axis == 1, axis=axis)
                        tracks.append(("bezier", f"{base}:{'xy'[axis]}",
                                       times, [k["x" if axis == 0 else "y"] for k in keys],
                                       out_h, in_h))
                else:
                    tracks.append(("value", base,
                                   [(k["time"], (k["x"], k["y"])) for k in keys]))
        lines = [f'[sub_resource type="Animation" id="{resource_id}"]']
        length = 0.0
        for track in tracks:
            times = track[2] if track[0] == "bezier" else [t for t, _ in track[2]]
            length = max(length, max(times))
        lines.append(f"length = {round(length, 6)}")
        lines.append("loop_mode = 1")

        for track_index, track in enumerate(tracks):
            if track[0] == "bezier":
                _path, times, values, out_h, in_h = track[1:]
                points = []
                for i in range(len(times)):
                    points.append(round(values[i], 6))
                    points += [round(in_h[i][0], 6), round(in_h[i][1], 6),
                               round(out_h[i][0], 6), round(out_h[i][1], 6)]
                lines.append(f'tracks/{track_index}/type = "bezier"')
                lines.append(f"tracks/{track_index}/imported = false")
                lines.append(f"tracks/{track_index}/enabled = true")
                lines.append(f'tracks/{track_index}/path = NodePath("{_path}")')
                lines.append(f"tracks/{track_index}/interp = 1")
                lines.append(f"tracks/{track_index}/loop_wrap = true")
                lines.append(
                    'tracks/%d/keys = {\n"handle_modes": PackedInt32Array(%s),\n'
                    '"points": PackedFloat32Array(%s),\n'
                    '"times": PackedFloat32Array(%s)\n}' % (
                        track_index, ", ".join("0" for _ in times),
                        ", ".join(str(p) for p in points),
                        ", ".join(str(t) for t in times)))
            else:
                _, _path, keys = track
                times = ", ".join(str(t) for t, _ in keys)
                if _path.endswith("rotation_degrees"):
                    values = ", ".join(str(round(v, 6)) for _, v in keys)
                else:
                    values = ", ".join(
                        f"Vector2({round(v[0], 6)}, {round(v[1], 6)})" for _, v in keys)
                lines.append(f'tracks/{track_index}/type = "value"')
                lines.append(f"tracks/{track_index}/imported = false")
                lines.append(f"tracks/{track_index}/enabled = true")
                lines.append(f'tracks/{track_index}/path = NodePath("{_path}")')
                lines.append(f"tracks/{track_index}/interp = 1")
                lines.append(f"tracks/{track_index}/loop_wrap = true")
                lines.append(
                    'tracks/%d/keys = {\n"times": PackedFloat32Array(%s),\n'
                    '"transitions": PackedFloat32Array(%s),\n"update": 0,\n'
                    '"values": [%s]\n}' % (track_index, times,
                                           ", ".join("1" for _ in keys), values))
        animation_resources.append({
            "id": resource_id, "lines": lines, "name": anim_name,
        })
        animation_refs.append((anim_name, resource_id))
    return animation_resources, animation_refs
