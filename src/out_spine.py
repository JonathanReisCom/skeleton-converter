"""Write the canonical model as Spine JSON + .atlas, and as a Godot .tscn scene."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .model import (
    Attachment, Skeleton,
    compose, godot_transform2d, invert, mirror_point, multiply, transform,
    godot_world_transforms,
)


def triangulate(polygons: list, vertex_count: int) -> list:
    triangles = []
    for group in polygons or []:
        for index in range(1, len(group) - 1):
            triangles.extend([group[0], group[index], group[index + 1]])
    if not triangles:
        for index in range(1, vertex_count - 1):
            triangles.extend([0, index, index + 1])
    return triangles


def read_png_size(path: str) -> tuple | None:
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return (
        int.from_bytes(header[16:20], "big"),
        int.from_bytes(header[20:24], "big"),
    )


def resolve_texture_path(texture_path: str, scene_path: str) -> str | None:
    if not texture_path:
        return None
    if texture_path.startswith("res://"):
        project_root = Path(scene_path).resolve().parent
        for candidate in [project_root, *project_root.parents]:
            if (candidate / "project.godot").exists():
                return str(candidate / texture_path[len("res://"):])
        return None
    return texture_path


def emit_atlas(model: Skeleton, atlas_path: str, image_name: str, image_path: str | None) -> str:
    size = read_png_size(image_path) if image_path else None
    width, height = size if size else (1024, 1024)
    lines = [image_name, f"\tsize: {width}, {height}", "\tfilter: Linear, Linear"]
    for att in model.attachments:
        uv = att["uv"]
        if not uv:
            continue
        min_x = min(p[0] for p in uv)
        min_y = min(p[1] for p in uv)
        span_x = (max(p[0] for p in uv) - min_x) or 1.0
        span_y = (max(p[1] for p in uv) - min_y) or 1.0
        lines.append(att["name"])
        lines.append("\tbounds: %d, %d, %d, %d" % (
            int(round(min_x)), int(round(min_y)),
            int(round(span_x)), int(round(span_y)),
        ))
    Path(atlas_path).write_text("\n".join(lines) + "\n")
    return atlas_path


# ---------------------------------------------------------------------------
# Godot .tscn writer
# ---------------------------------------------------------------------------


def render_tscn(model: Skeleton, bone_nodes: list, polygon_nodes: list,
                animations: list, animation_refs: list, texture_path: str) -> str:
    lines = ['[gd_scene format=3]', ""]
    lines.append(f'[ext_resource type="Texture2D" path="{texture_path}" id="1"]')
    lines.append("")
    for entry in animations:
        lines.extend(entry["lines"])
        lines.append("")
    lines.append('[sub_resource type="AnimationLibrary" id="AnimationLibrary_1"]')
    lines.append("_data = {")
    for anim_name, resource_id in animation_refs:
        lines.append(f'&"{anim_name}": SubResource("{resource_id}"),')
    lines.append("}")
    lines.append("")
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


def _emit_animation_resource(resource_id: str, tracks: list, length: float) -> list:
    lines = [f'[sub_resource type="Animation" id="{resource_id}"]']
    lines.append(f"length = {round(length, 6)}")
    lines.append("loop_mode = 1")
    for track_index, (property_name, keys, track_path, curves) in enumerate(tracks):
        keys, curves = _bake_track(keys, curves)
        times = ", ".join(str(time) for time, _ in keys)
        transitions = ", ".join(str(c) for c in curves) if curves else ", ".join("1" for _ in keys)
        if property_name == "rotation_degrees":
            values = ", ".join(str(round(v, 6)) for _, v in keys)
        else:
            values = ", ".join(
                f"Vector2({round(v[0], 6)}, {round(v[1], 6)})" for _, v in keys
            )
        lines.append(f'tracks/{track_index}/type = "value"')
        lines.append(f"tracks/{track_index}/imported = false")
        lines.append(f"tracks/{track_index}/enabled = true")
        lines.append(f'tracks/{track_index}/path = NodePath("{track_path}")')
        lines.append(f"tracks/{track_index}/interp = 1")
        lines.append(f"tracks/{track_index}/loop_wrap = true")
        lines.append(
            'tracks/%d/keys = {\n"times": PackedFloat32Array(%s),\n'
            '"transitions": PackedFloat32Array(%s),\n"update": 0,\n'
            '"values": [%s]\n}' % (track_index, times, transitions, values)
        )
    return lines


def _bake_track(keys: list, curves: list) -> tuple:
    """Expand bezier-interpolated intervals into dense linear keys."""
    has_bezier = any(isinstance(c, (list, tuple)) and len(c) >= 4 for c in curves or [])
    if not has_bezier:
        return keys, [1.0] * len(keys)
    baked_keys = []
    baked_transitions = []
    for index, (time, value) in enumerate(keys):
        baked_keys.append((time, value))
        baked_transitions.append(1.0)
        curve = curves[index] if index < len(curves) else None
        if not isinstance(curve, (list, tuple)) or len(curve) < 4 or index + 1 >= len(keys):
            continue
        next_time, next_value = keys[index + 1]
        samples = 10
        for step in range(1, samples):
            fraction = step / samples
            if isinstance(value, tuple):
                sample_time, _ = bezier_table_point(
                    curve, 0, step, time, next_time, value[0], next_value[0]
                )
                sample_value = tuple(
                    bezier_table_point(
                        curve, axis, step, time, next_time, value[axis], next_value[axis]
                    )[1]
                    for axis in range(len(value))
                )
            else:
                sample_time, sample_value = bezier_table_point(
                    curve, 0, step, time, next_time, value, next_value
                )
            baked_keys.append((round(sample_time, 6), sample_value))
            baked_transitions.append(1.0)
    return baked_keys, baked_transitions


def bezier_table_point(curve: list, axis: int, step: int,
                       time1: float, time2: float, value1: float, value2: float) -> tuple:
    if not isinstance(curve, (list, tuple)) or len(curve) < axis * 4 + 4:
        fraction = step / 10.0
        return (time1 + (time2 - time1) * fraction, value1 + (value2 - value1) * fraction)
    cx1, cy1, cx2, cy2 = (float(curve[axis * 4 + i]) for i in range(4))
    tmpx = (time1 - cx1 * 2 + cx2) * 0.03
    tmpy = (value1 - cy1 * 2 + cy2) * 0.03
    dddx = ((cx1 - cx2) * 3 - time1 + time2) * 6e-3
    dddy = ((cy1 - cy2) * 3 - value1 + value2) * 6e-3
    ddx = tmpx * 2 + dddx
    ddy = tmpy * 2 + dddy
    dx = (cx1 - time1) * 0.3 + tmpx + dddx * 0.16666667
    dy = (cy1 - value1) * 0.3 + tmpy + dddy * 0.16666667
    x = time1 + dx
    y = value1 + dy
    for _ in range(1, step):
        dx += ddx
        dy += ddy
        ddx += dddx
        ddy += dddy
        x += dx
        y += dy
    return (x, y)


def map_curve_to_godot(curve, axis: int, offset: float, negate: bool):
    """Map one bezier segment's control values into Godot's value space."""
    if not isinstance(curve, (list, tuple)):
        return curve
    offset_index = axis * 4
    if len(curve) < offset_index + 4:
        return curve
    mapped = list(curve)
    for index in (offset_index + 1, offset_index + 3):
        control = float(mapped[index])
        mapped[index] = -control + offset if negate else control + offset
    return mapped


def group_triangles(triangles: list) -> list:
    return [
        [triangles[i], triangles[i + 1], triangles[i + 2]]
        for i in range(0, len(triangles) - 2, 3)
    ]


# ---------------------------------------------------------------------------
# Spine JSON writer
# ---------------------------------------------------------------------------


def write_spine_json(model: Skeleton, output_path: str, image_name: str | None = None) -> dict:
    world = godot_world_transforms(model)
    bone_index = {bone.name: index for index, bone in enumerate(model.bones)}

    spine = {
        "skeleton": {
            "spine": "4.1.23",
            "x": 0, "y": 0, "width": 512, "height": 512,
            "images": "./images/",
        },
        "bones": [],
        "slots": [],
        "skins": [{"name": "default", "attachments": {}}],
        "animations": {},
    }

    for bone in model.bones:
        entry = {"name": bone.name}
        if bone.parent:
            entry["parent"] = bone.parent
        entry["x"] = round(bone.position[0], 4)
        entry["y"] = round(-bone.position[1], 4)
        entry["rotation"] = round(-bone.rotation_deg, 4)
        if bone.length:
            entry["length"] = round(bone.length, 4)
        if abs(bone.scale[0] - 1.0) > 1e-6 or abs(bone.scale[1] - 1.0) > 1e-6:
            entry["scaleX"] = round(bone.scale[0], 4)
            entry["scaleY"] = round(bone.scale[1], 4)
        spine["bones"].append(entry)

    for att in model.attachments:
        polygon = att["polygon"]
        uv = att["uv"]
        weights = att["weights"]
        polygon_world = compose(att["position"], 0.0)
        uv_min_x = min(p[0] for p in uv)
        uv_min_y = min(p[1] for p in uv)
        span_x = (max(p[0] for p in uv) - uv_min_x) or 1.0
        span_y = (max(p[1] for p in uv) - uv_min_y) or 1.0

        influence = {}
        for bone_name, weight_list in weights:
            total = sum(w for w in weight_list)
            if total > 0:
                influence[bone_name] = total
        host = max(influence, key=influence.get) if influence else model.bones[0].name

        entry = {
            "type": "mesh",
            "uvs": [],
            "triangles": triangulate(att["polygons"], len(polygon)),
            "width": round(span_x, 4),
            "height": round(span_y, 4),
        }
        for u, v in uv:
            entry["uvs"].extend([
                round((u - uv_min_x) / span_x, 6),
                round((v - uv_min_y) / span_y, 6),
            ])

        if weights:
            bone_index_by_name = {b.name: i for i, b in enumerate(model.bones)}
            bones_used = []
            for bone_name, _ in weights:
                if bone_name in bone_index_by_name and bone_name not in bones_used:
                    bones_used.append(bone_name)
            vertices = []
            for vertex_index, vertex in enumerate(polygon):
                world_point = transform(
                    polygon_world, (vertex[0] + att["offset"][0], vertex[1] + att["offset"][1])
                )
                entries = []
                for bone_name, weight_list in weights:
                    if bone_name not in bone_index_by_name or vertex_index >= len(weight_list):
                        continue
                    weight = weight_list[vertex_index]
                    if weight <= 0:
                        continue
                    local = transform(invert(world[bone_name]), world_point)
                    entries.append((bone_name, mirror_point(local), weight))
                if not entries:
                    continue
                total = sum(w for _, _, w in entries)
                vertices.append(len(entries))
                for bone_name, local, weight in entries:
                    vertices.extend([
                        bone_index_by_name[bone_name],
                        round(local[0], 4), round(local[1], 4),
                        round(weight / total, 6) if total else 1.0,
                    ])
            entry["bones"] = [bone_index_by_name[n] for n in bones_used]
            entry["vertices"] = vertices
        else:
            entry["vertices"] = [
                round(v, 4) for vertex in polygon
                for v in mirror_point(transform(
                    polygon_world,
                    (vertex[0] + att["offset"][0], vertex[1] + att["offset"][1]),
                ))
            ]

        spine["slots"].append({
            "name": att["name"],
            "bone": host,
            "attachment": att["name"],
        })
        spine["skins"][0]["attachments"].setdefault(att["name"], {})[att["name"]] = entry

    for anim_name, tracks in model.animations.items():
        animation = {"bones": {}}
        for bone_name, props in tracks.items():
            bone = next((b for b in model.bones if b.name == bone_name), None)
            bone_tracks = {}
            if props.get("rotate"):
                setup_rotation = bone.rotation_deg if bone else 0.0
                bone_tracks["rotate"] = [
                    {"time": round(k["time"], 6), "value": round(-(k["angle"] - setup_rotation), 6)}
                    for k in props["rotate"]
                ]
            if props.get("translate"):
                setup_position = bone.position if bone else (0.0, 0.0)
                bone_tracks["translate"] = [
                    {
                        "time": round(k["time"], 6),
                        "x": round(k["x"] - setup_position[0], 6),
                        "y": round(-(k["y"] - setup_position[1]), 6),
                    }
                    for k in props["translate"]
                ]
            if bone_tracks:
                animation["bones"][bone_name] = bone_tracks
        spine["animations"][anim_name] = animation

    return spine
