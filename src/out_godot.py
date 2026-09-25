"""Write the canonical model as a Godot 4 Skeleton2D scene (.tscn)."""

from __future__ import annotations

import math
from pathlib import Path

from .model import Skeleton, godot_transform2d, invert, multiply
from .model import group_triangles


def _render_tscn(bone_nodes, polygon_nodes, animation_resources, animation_refs,
                 texture_path, extra_textures=None):
    lines = ['[gd_scene format=3]', ""]
    lines.append(f'[ext_resource type="Texture2D" path="{texture_path}" id="1"]')
    # Multi-page rigs: one Texture2D ext_resource per atlas page; Polygon2Ds
    # reference the page their region was packed into.
    for resource_id, page_name in (extra_textures or []):
        lines.append(f'[ext_resource type="Texture2D" path="res://{page_name}" '
                     f'id="{resource_id}"]')
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
    bone_nodes = _emit_bones(model)
    # Distinct atlas pages get their own Texture2D; single-page rigs (or rigs
    # where every attachment shares one page) keep the default texture. In
    # multi-page mode the first page maps to the default id "1".
    page_ids: dict = {}
    pages = sorted({a.page for a in model.attachments if a.page})
    if len(pages) > 1:
        page_ids = {page: str(index + 2) for index, page in enumerate(pages)}
        page_ids[pages[0]] = "1"
    polygon_nodes = _emit_attachments(model, texture_path, page_ids)
    animation_resources, animation_refs = _emit_animations(model)
    content = _render_tscn(bone_nodes, polygon_nodes, animation_resources,
                           animation_refs, texture_path,
                           extra_textures=[(rid, page) for page, rid in
                                           page_ids.items() if rid != "1"])
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# bones
# ---------------------------------------------------------------------------



def _emit_bones(model):
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
        # rest comes from the model's bind pose when the godot->spine leg
        # carried one (scenes whose rest differs from the node pose); the
        # rotation in the rest drives the skinning basis and must NOT be the
        # node pose's rotation.
        r_pos, r_rot, r_scale = bone.rest or (
            bone.position, bone.rotation_deg, bone.scale)
        r_cos, r_sin = math.cos(math.radians(r_rot)), math.sin(math.radians(r_rot))
        props.append(
            "rest = Transform2D({}, {}, {}, {}, {}, {})".format(
                round(r_cos * r_scale[0], 6), round(r_sin * r_scale[0], 6),
                round(-r_sin * r_scale[1], 6), round(r_cos * r_scale[1], 6),
                round(r_pos[0], 6), round(r_pos[1], 6),
            )
        )
        if bone.inherit != "normal":
            props.append(f'metadata/spine_inherit = "{bone.inherit}"')
        nodes.append({"name": bone.name, "type": "Bone2D", "parent": parent_path, "props": props})
    return nodes


# ---------------------------------------------------------------------------
# attachments as Polygon2D
# ---------------------------------------------------------------------------


def _attachment_node_names(model):
    """Attachment -> Polygon2D node name, in emission order.

    Node name = the skin entry name verbatim: the viewer lists these as
    the attachment options, so mangling case (`Eye_Anger` -> `Eye_anger`)
    would make the Godot pane offer different-looking options than the
    Spine pane. Godot rejects a few characters in node names; replace them
    rather than dropping the name. Entries sharing a name across slots are
    deduplicated — the owning slot travels as ``metadata/slot``, never as
    part of the name.
    """
    used_names = set()
    names = []
    for att in model.attachments:
        node_name = att.name
        for bad, repl in ((".", "_"), ("/", "_"), (":", "_"), ("@", "_"),
                          ('"', "_"), ("%", "_")):
            node_name = node_name.replace(bad, repl)
        base_name = node_name
        suffix = 2
        while node_name in used_names:
            node_name = "%s_%d" % (base_name, suffix)
            suffix += 1
        used_names.add(node_name)
        names.append((att, node_name))
    return names

def _emit_attachments(model, texture_path, page_ids=None):
    polygon_nodes = []
    for att, node_name in _attachment_node_names(model):
        polygon = att.polygon
        uv = att.uv
        weights = att.weights
        offset = att.position

        # Vertices are node-local and the node carries the attachment's
        # position — exactly the structure the source scene had (Godot applies
        # node position to skinned vertices, so the round trip must keep it).
        local_points = [(p[0], p[1]) for p in polygon]

        uv_points = [[v[0], v[1]] for v in uv]

        node_pos = att.position
        props = [
            f"position = Vector2({round(node_pos[0], 6)}, {round(node_pos[1], 6)})",
            f'texture = ExtResource("{page_ids.get(att.page, "1")}")',
            'skeleton = NodePath("../../Skeleton2D")',
            "polygon = PackedVector2Array(%s)" % ", ".join(
                f"{round(v, 6)}" for point in local_points for v in point
            ),
            "uv = PackedVector2Array(%s)" % ", ".join(
                f"{round(v, 6)}" for point in uv_points for v in point
            ),
        ]
        # Mirror the Spine runtime's SETUP state: only the slot's setup
        # attachment draws before any timeline applies. An attachment an
        # attachment timeline equips starts hidden and is switched on by its
        # visibility track — otherwise a comparison frame before the first
        # timeline key shows a prop the runtime does not draw.
        if not att.setup:
            props.append("visible = false")
        if att.polygons:
            groups = att.polygons
            if groups and isinstance(groups[0], int):
                # Flat triangle index list (spine `triangles`): emit ONE GROUP
                # PER TRIANGLE. Godot fan-triangulates each PackedInt32Array as
                # a single polygon — 192 indices in one group is a 192-gon whose
                # triangulation degenerates and the mesh silently draws nothing
                # (the hero's cape). A 3-index group IS a triangle.
                props.append(
                    "polygons = [%s]" % ", ".join(
                        "PackedInt32Array(%d, %d, %d)"
                        % (groups[i], groups[i + 1], groups[i + 2])
                        for i in range(0, len(groups) - 2, 3)
                    )
                )
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
                # The tscn bone reference is a NodePath relative to the
                # Skeleton2D the polygon is skinned to — the official demo
                # writes "Hip/Chest", never "../../Skeleton2D/Hip". A bare
                # leaf name does not resolve and Godot silently skips the
                # skinned polygon (the hero rendered nothing until this was
                # the skeleton-relative path).
                bone = model.by_name.get(bone_name)
                bone_path = bone.path if bone else bone_name
                parts.append(
                    '"%s", PackedFloat32Array(%s)'
                    % (bone_path, ", ".join(str(w) for w in weights_array))
                )
            props.append("bones = [%s]" % ", ".join(parts))
        else:
            host_path = model.bones[0].path if model.bones else ""
            parts = ['"%s", PackedFloat32Array(%s)' % (
                host_path, ", ".join("1" for _ in local_points)
            )]
            props.append("bones = [%s]" % ", ".join(parts))

        # The slot travels as metadata: multiple attachments share a slot
        # and the viewer switches between them by slot. Name = entry name.
        props.append("metadata/slot = \"%s\"" % (att.slot or att.name))
        # The node name is deduplicated and character-substituted; the skin
        # entry's real name travels as metadata so a round trip re-emits the
        # source name instead of the node's mangled one (`splat03_2`).
        props.append('metadata/attachment = "%s"' % att.name.replace('"', "_"))
        polygon_nodes.append({
            "name": node_name, "type": "Polygon2D",
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
        curve = keys[i].curve
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
                if any(k.curve for k in keys):
                    # Handle computation needs the key's value in spine offset
                    # space (where CurveTimeline control points live):
                    # angle = -(setup_spine + offset) and rotation_deg =
                    # -setup_spine, so offset = -angle + rotation_deg.
                    spine_values = [-k.angle + setup_rot for k in keys]
                    times = [k.time for k in keys]
                    out_h, in_h = _solve_handles(keys, times, spine_values, True)
                    tracks.append(("bezier", f"{base}:rotation_degrees",
                                   times, [k.angle for k in keys],
                                   out_h, in_h))
                else:
                    tracks.append(("value", f"{base}:rotation_degrees",
                                   [(k.time, k.angle) for k in keys]))
            if props.get("translate"):
                keys = props["translate"]
                base = f"Sprite2D/Skeleton2D/{bone_relative}:position"
                if any(k.curve for k in keys):
                    # x: spine = kx - setup_x; y mirrors: spine = -ky - setup_y.
                    # x: godot = spine + setup_x; y mirrors, so offset_y =
                    # -ky - setup_y_spine = -ky + position[1].
                    for axis, to_spine in (
                            (0, lambda k: k.x - setup_pos[0]),
                            (1, lambda k: -k.y + setup_pos[1])):
                        spine_values = [to_spine(k) for k in keys]
                        times = [k.time for k in keys]
                        out_h, in_h = _solve_handles(keys, times, spine_values,
                                                     axis == 1, axis=axis)
                        tracks.append(("bezier", f"{base}:{'xy'[axis]}",
                                       times, [k.x if axis == 0 else k.y for k in keys],
                                       out_h, in_h))
                else:
                    tracks.append(("value", base,
                                   [(k.time, (k.x, k.y)) for k in keys]))
            if props.get("scale"):
                keys = props["scale"]
                base = f"Sprite2D/Skeleton2D/{bone_relative}:scale"
                if any(k.curve for k in keys):
                    # Scale is 1:1 between the spaces (a magnitude has no
                    # direction), so spine values are the model values.
                    for axis in (0, 1):
                        spine_values = [k.scale[axis] for k in keys]
                        times = [k.time for k in keys]
                        out_h, in_h = _solve_handles(keys, times, spine_values,
                                                     False, axis=axis)
                        tracks.append(("bezier", f"{base}:{'xy'[axis]}",
                                       times,
                                       [k.scale[axis] for k in keys],
                                       out_h, in_h))
                else:
                    tracks.append(("value", base,
                                   [(k.time, (k.scale[0], k.scale[1]))
                                    for k in keys]))
        # Attachment timelines: a slot's drawn attachment changes over time.
        # Emitted as one boolean ``visible`` track per Polygon2D of that slot —
        # Godot's AnimationPlayer drives polygon visibility directly, which is
        # what the runtime's setAttachment does.
        slot_tracks = model.slot_timelines.get(anim_name) or {}
        if slot_tracks:
            names_by_slot: dict = {}
            for att, node_name in _attachment_node_names(model):
                names_by_slot.setdefault(att.slot or att.name, {})[att.name] = node_name
            setup_by_slot = {}
            for att, _node in _attachment_node_names(model):
                setup_by_slot.setdefault(att.slot or att.name, set())
                if att.setup:
                    setup_by_slot[att.slot or att.name].add(att.name)
            for slot_name, keys in slot_tracks.items():
                node_names = names_by_slot.get(slot_name) or {}
                for att_name, node_name in node_names.items():
                    key_list = [(k["time"], k["attachment"] == att_name)
                                for k in keys]
                    # Godot holds a value track's FIRST key backwards, while
                    # the Spine runtime draws the slot's setup attachment
                    # before the first timeline key — so a timeline starting
                    # after t=0 needs an explicit t=0 key or the prop appears
                    # early (the alien's death burst showed at t=0).
                    if key_list and key_list[0][0] > 0.0:
                        key_list.insert(
                            0, (0.0, att_name in setup_by_slot.get(slot_name, ())))
                    tracks.append(("visible",
                                   f"Sprite2D/Polygons/{node_name}:visible",
                                   key_list))
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
                lines.append(f"tracks/{track_index}/loop_wrap = false")
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
                elif isinstance(keys[0][1], bool):
                    # Attachment visibility: a discrete boolean track.
                    values = ", ".join(
                        "true" if v else "false" for _, v in keys)
                else:
                    values = ", ".join(
                        f"Vector2({round(v[0], 6)}, {round(v[1], 6)})" for _, v in keys)
                lines.append(f'tracks/{track_index}/type = "value"')
                lines.append(f"tracks/{track_index}/imported = false")
                lines.append(f"tracks/{track_index}/enabled = true")
                lines.append(f'tracks/{track_index}/path = NodePath("{_path}")')
                # Visibility is discrete: with linear interpolation the engine
                # blends false -> true as a float and any non-zero blend reads
                # as visible, so a prop appears a whole segment early (the
                # alien's death burst showed at t=0 and its splats at t=1.3).
                discrete = isinstance(keys[0][1], bool)
                lines.append(
                    f"tracks/{track_index}/interp = {0 if discrete else 1}")
                lines.append(f"tracks/{track_index}/loop_wrap = false")
                lines.append(
                    'tracks/%d/keys = {\n"times": PackedFloat32Array(%s),\n'
                    '"transitions": PackedFloat32Array(%s),\n"update": %d,\n'
                    '"values": [%s]\n}' % (track_index, times,
                                           ", ".join("1" for _ in keys),
                                           1 if discrete else 0, values))
        animation_resources.append({
            "id": resource_id, "lines": lines, "name": anim_name,
        })
        animation_refs.append((anim_name, resource_id))
    return animation_resources, animation_refs
