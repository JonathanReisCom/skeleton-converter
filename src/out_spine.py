"""Write the canonical model as Spine JSON + .atlas."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .model import (
    Attachment, Skeleton,
    compose, godot_transform2d, godot_rest_worlds, invert, mirror_point, mirror_world, multiply, transform,
    godot_world_transforms,
)


def triangulate(polygons: list, vertex_count: int) -> list:
    triangles = []
    groups = [polygons] if polygons and isinstance(polygons[0], int) else (polygons or [])
    for group in groups:
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
        rel = texture_path[len("res://"):]
        scene_dir = Path(scene_path).resolve().parent
        # res:// paths are relative to a project root. Candidates: a real
        # Godot project (project.godot marks it), the scene's own folder, or
        # any parent that has the full relative path (a preview folder keeps
        # referenced resources at res://player/gBot.png one level up).
        for candidate in [scene_dir, *scene_dir.parents]:
            if (candidate / "project.godot").exists() or (candidate / rel).exists():
                resolved = candidate / rel
                if resolved.exists():
                    return str(resolved)
        # Demos often author res:// paths against their project root, which
        # the converted tree does not reproduce (res://player/gBot.png next
        # to the scene). Fall back to the bare filename around the scene.
        flat = scene_dir / Path(rel).name
        return str(flat) if flat.exists() else None
    return texture_path


def render_atlas(model: Skeleton, image_name: str, image_path: str | None = None) -> str:
    """Render atlas text. Page size from the PNG IHDR when image_path resolves."""
    size = read_png_size(image_path) if image_path else None
    width, height = size if size else (1024, 1024)
    lines = [image_name, f"\tsize: {width}, {height}", "\tfilter: Linear, Linear"]
    for att in model.attachments:
        uv = att.uv
        if not uv:
            continue
        min_x = min(p[0] for p in uv)
        min_y = min(p[1] for p in uv)
        span_x = (max(p[0] for p in uv) - min_x) or 1.0
        span_y = (max(p[1] for p in uv) - min_y) or 1.0
        lines.append(att.name)
        lines.append("\tbounds: %d, %d, %d, %d" % (
            int(round(min_x)), int(round(min_y)),
            int(round(span_x)), int(round(span_y)),
        ))
    return "\n".join(lines) + "\n"


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



# ---------------------------------------------------------------------------
# Spine JSON writer
# ---------------------------------------------------------------------------


def write_spine_json(model: Skeleton, output_path: str,
                     image_name: str | None = None,
                     image_path: str | None = None) -> dict:
    world = godot_world_transforms(model)
    bone_index = {bone.name: index for index, bone in enumerate(model.bones)}
    output = Path(output_path)
    image_name = image_name or "image.png"

    spine = {
        "skeleton": {
            "spine": "4.3.26",
            "x": 0, "y": 0, "width": 0, "height": 0,
            "images": "./images/",
        },
        "bones": [],
        "slots": [],
        "skins": [{"name": "default", "attachments": {}}],
        "animations": {},
    }
    # Overwrite the placeholder with real setup-pose bounds so runtimes and the
    # Spine Editor frame the skeleton correctly (width/height are hints).
    _model_world = world

    for bone in model.bones:
        entry = {"name": bone.name}
        if bone.parent:
            entry["parent"] = bone.parent
        entry["x"] = round(bone.position[0], 4)
        entry["y"] = round(-bone.position[1], 4)
        entry["rotation"] = round(-bone.rotation_deg, 4)
        # Bind pose (Bone2D rest) travels as extension fields: real Spine
        # runtimes ignore unknown keys, and out_godot reads them back so the
        # Godot round trip keeps rest != node pose intact (Godot values —
        # y-down, degrees).
        if bone.rest is not None:
            entry["restX"] = round(bone.rest[0][0], 4)
            entry["restY"] = round(bone.rest[0][1], 4)
            entry["restRotation"] = round(bone.rest[1], 4)
            entry["restScaleX"] = round(bone.rest[2][0], 4)
            entry["restScaleY"] = round(bone.rest[2][1], 4)
        if bone.length:
            entry["length"] = round(bone.length, 4)
        if abs(bone.scale[0] - 1.0) > 1e-6 or abs(bone.scale[1] - 1.0) > 1e-6:
            entry["scaleX"] = round(bone.scale[0], 4)
            entry["scaleY"] = round(bone.scale[1], 4)
        spine["bones"].append(entry)

    for att in model.attachments:
        polygon = att.polygon
        uv = att.uv
        weights = att.weights
        if not uv or not polygon:
            # A path (or other non-rendered) attachment has no UVs and cannot
            # be written as a mesh; writing it would crash or draw garbage.
            model.notes.append(f"preview: skipped attachment {att.name!r} (no UVs)")
            continue
        polygon_world = compose(att.position, 0.0)
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
            "triangles": triangulate(att.polygons, len(polygon)),
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
            # Spine-space bone worlds: the model is Godot space (y-down) and
            # the JSON is Spine space (y-up) — the runtime will reconstruct
            # vertex world = Σ w · spine_bone · local, so `local` must be the
            # inverse of the SPINE bone transform applied to the mirrored
            # point. Computing it with the Godot transform and mirroring the
            # result mixes conventions for rotated bones and scatters the
            # rig (the gBot demo rendered headless pieces for months).
            # Mesh bind basis: Godot skins vertices with pose * rest^-1, so
            # the JSON local must be stored against the bone's GLOBAL REST
            # (not the setup pose) — otherwise every skinned mesh carries the
            # bone's setup rotation as a spurious offset (the demo's head
            # rendered 16.5° off). rest_world is godot space; mirror_world
            # conjugates it into Spine space.
            spine_rest = {bone_name: mirror_world(transform_matrix)
                          for bone_name, transform_matrix in godot_rest_worlds(model).items()}
            for vertex_index, vertex in enumerate(polygon):
                world_point = transform(
                    polygon_world, (vertex[0] + att.offset[0], vertex[1] + att.offset[1])
                )
                spine_point = mirror_point(world_point)
                entries = []
                for bone_name, weight_list in weights:
                    if bone_name not in bone_index_by_name or vertex_index >= len(weight_list):
                        continue
                    weight = weight_list[vertex_index]
                    if weight <= 0:
                        continue
                    local = transform(invert(spine_rest[bone_name]), spine_point)
                    entries.append((bone_name, local, weight))
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
                    (vertex[0] + att.offset[0], vertex[1] + att.offset[1]),
                ))
            ]

        # Group by owning slot: multiple attachments (variants) may share a
        # slot; the slot lists one setup attachment (the equipped pick) and
        # the default skin carries every entry so runtimes/viewers can
        # switch between them.
        slot_name = att.slot or att.name
        if not any(s["name"] == slot_name for s in spine["slots"]):
            spine["slots"].append({
                "name": slot_name,
                "bone": host,
                "attachment": att.name if att.equipped else "",
            })
        elif att.equipped:
            next(s for s in spine["slots"]
                 if s["name"] == slot_name)["attachment"] = att.name
        spine["skins"][0]["attachments"].setdefault(slot_name, {})[att.name] = entry

    # A slot without a setup attachment must omit the key entirely — an
    # empty string is not a valid attachment reference.
    for slot in spine["slots"]:
        if not slot.get("attachment"):
            slot.pop("attachment", None)

    for anim_name, tracks in model.animations.items():
        animation = {"bones": {}}
        for bone_name, props in tracks.items():
            bone = next((b for b in model.bones if b.name == bone_name), None)

            bone_tracks = {}

            def _clamp_first_last(keys: list, value_key: str) -> list:
                """Reproduce Godot's AnimationPlayer edge behaviour: before the
                first key the engine extrapolates the FIRST REAL SEGMENT
                (key1 -> key2) backwards: value(t) = key1 + slope*(t1 - t),
                slope = (key2 - key1)/(t2 - t1) — verified against the engine
                (idle chest/head and fall legs land exactly on this line). The
                Spine runtime instead holds nothing outside the keyed range
                (flat setup pose), which diverges from Godot for any track
                whose first key is not at t=0. Insert an extrapolated key at
                t=0 so both runtimes sample the same ramp."""
                if not keys:
                    return keys
                out = list(keys)
                if out[0]["time"] > 0.0:
                    k1, k2 = out[0], out[1] if len(out) > 1 else out[0]
                    first = dict(k1)
                    first["time"] = 0.0
                    if k2 is not k1:
                        t1, t2 = k1["time"], k2["time"]
                        span = t2 - t1
                        if span > 0:
                            for field in ("value", "x", "y"):
                                if field in k1 and field in k2:
                                    slope = (k2[field] - k1[field]) / span
                                    first[field] = round(
                                        k1[field] + slope * (t1 - 0.0), 6)
                    first.pop("curve", None)
                    out.insert(0, first)
                return out

            if props.get("rotate"):
                setup_rotation = bone.rotation_deg if bone else 0.0
                bone_tracks["rotate"] = _clamp_first_last([
                    {"time": round(k.time, 6),
                     "value": round(-(k.angle - setup_rotation), 6),
                     **({"curve": [round(c, 6) for c in k.curve]}
                        if k.curve else {})}
                    for k in props["rotate"]
                ], "value")
            if props.get("translate"):
                setup_position = bone.position if bone else (0.0, 0.0)
                bone_tracks["translate"] = _clamp_first_last([
                    {
                        "time": round(k.time, 6),
                        "x": round(k.x - setup_position[0], 6),
                        "y": round(-(k.y - setup_position[1]), 6),
                        **({"curve": [round(c, 6) for c in k.curve]}
                           if k.curve else {}),
                    }
                    for k in props["translate"]
                ], "value")
            if props.get("scale"):
                # Absolute local scale, no axis mirroring: the Godot values
                # ARE the spine values.
                bone_tracks["scale"] = _clamp_first_last([
                    {
                        "time": round(k.time, 6),
                        "x": round(k.scale[0], 6),
                        "y": round(k.scale[1], 6),
                        **({"curve": [round(c, 6) for c in k.curve]}
                           if k.curve else {}),
                    }
                    for k in props["scale"]
                ], "value")
            if bone_tracks:
                animation["bones"][bone_name] = bone_tracks
        slot_tracks = model.slot_timelines.get(anim_name) or {}
        if slot_tracks:
            animation["slots"] = {
                slot_name: {
                    "attachment": [
                        {"time": round(key["time"], 6),
                         **({"name": key["attachment"]}
                            if key["attachment"] else {})}
                        for key in keys
                    ]
                }
                for slot_name, keys in slot_tracks.items()
            }
        spine["animations"][anim_name] = animation

    # Atlas is named after the output JSON (not the source texture) so the
    # Spine Editor's exact-string atlas lookup always matches: <json>.atlas
    # declares <image_name>, which must equal the file inside ./images/.
    # Real setup-pose bounds from attachment vertices in world space; the Spine
    # Editor and viewers use them to frame the skeleton.
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for att in model.attachments:
        if not att.equipped:
            continue  # setup-pose bounds only count what the rig draws
        poly_world = world.get(att.name) or (world.get(model.bones[0].name) if model.bones else None)
        if not poly_world:
            continue
        for v in att.polygon:
            p = transform(poly_world, (v[0] + att.offset[0], v[1] + att.offset[1]))
            min_x = min(min_x, p[0]); max_x = max(max_x, p[0])
            min_y = min(min_y, p[1]); max_y = max(max_y, p[1])
    if min_x <= max_x:
        spine["skeleton"]["x"] = round(min_x, 2)
        spine["skeleton"]["y"] = round(-max_y, 2)
        spine["skeleton"]["width"] = round(max_x - min_x, 2) or 1
        spine["skeleton"]["height"] = round(max_y - min_y, 2) or 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".atlas").write_text(
        render_atlas(model, image_name, image_path), encoding="utf-8")
    Path(output_path).write_text(json.dumps(spine, indent=2), encoding="utf-8")
    return spine
