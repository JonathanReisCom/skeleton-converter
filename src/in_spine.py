"""Read Spine JSON + .atlas into the canonical model (Godot space).

The conversion mirrors Spine's Y-up to Godot's Y-down, resolves `inherit` modes
into effective local transforms, and converts region/mesh attachments into
polygon quads and weighted vertex lists in skeleton rest space.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .model import (
    Attachment, Bone, Skeleton,
    compose, invert, mirror_matrix, mirror_point, multiply, transform,
    spine_world_transforms,
)


def read_spine(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    data.setdefault("bones", [])
    data.setdefault("slots", [])
    data.setdefault("skins", [])
    data.setdefault("animations", {})
    return data


def read_atlas_regions(atlas_path: str | None) -> dict:
    """Region name -> {x, y, width, height, degrees, originalWidth, ...}."""
    regions = {}
    if not atlas_path or not Path(atlas_path).exists():
        return regions
    current = None
    for line in Path(atlas_path).read_text().splitlines():
        if not line.strip():
            current = None
            continue
        if line.startswith((" ", "\t")):
            if current is None:
                continue
            key, _, value = line.strip().partition(":")
            key = key.strip()
            value = value.strip()
            if key == "bounds":
                numbers = [int(float(p.strip())) for p in value.split(",") if p.strip()]
                if len(numbers) >= 4:
                    regions[current] = {
                        "x": numbers[0], "y": numbers[1],
                        "width": numbers[2], "height": numbers[3], "degrees": 0,
                        "originalWidth": numbers[2], "originalHeight": numbers[3],
                        "offsetX": 0, "offsetY": 0,
                    }
            elif key == "rotate" and current in regions:
                regions[current]["degrees"] = int(float(value))
            elif key == "offsets" and current in regions:
                offsets = [int(float(p.strip())) for p in value.split(",") if p.strip()]
                if len(offsets) >= 2:
                    regions[current]["offsetX"] = offsets[0]
                    regions[current]["offsetY"] = offsets[1]
            elif key == "orig" and current in regions:
                orig = [int(float(p.strip())) for p in value.split(",") if p.strip()]
                if len(orig) >= 2:
                    regions[current]["originalWidth"] = orig[0]
                    regions[current]["originalHeight"] = orig[1]
            continue
        current = line.strip()
    return regions


def resolve_image_for_atlas(atlas_path: str | None, texture_path: str) -> str:
    """Filesystem path of the PNG the atlas page names."""
    if atlas_path and Path(atlas_path).exists():
        for line in Path(atlas_path).read_text().splitlines():
            if line and not line.startswith((" ", "\t")):
                return str(Path(atlas_path).parent / line.strip())
    return texture_path


def read_png_size(path: str) -> tuple | None:
    """Width/height from a PNG's IHDR chunk."""
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


def spine_attachment_map(spine: dict) -> dict:
    """slot name -> (attachment name, attachment dict) from the default skin."""
    skins = spine["skins"]
    attachments = (
        skins[0]["attachments"] if isinstance(skins, list) else next(iter(skins.values()))
    )
    out = {}
    for slot_name, entries in attachments.items():
        for attachment_name, attachment in entries.items():
            out[slot_name] = (attachment_name, attachment)
            break
    return out


def region_uv_rect(region: dict, page_w: float, page_h: float) -> tuple:
    x, y = region["x"], region["y"]
    w, h = region["width"], region["height"]
    if region.get("degrees") == 90:
        return (x, y, x + h, y + w)
    return (x, y, x + w, y + h)


def region_corner_uvs(region: dict, page_w: float, page_h: float) -> list:
    left, top, right, bottom = region_uv_rect(region, page_w, page_h)
    if region.get("degrees") == 90:
        return [(right, bottom), (left, bottom), (left, top), (right, top)]
    return [(left, bottom), (left, top), (right, top), (right, bottom)]


def region_uv_for_vertex(region: dict, u: float, v: float, page_w: float, page_h: float) -> tuple:
    original_w = region.get("originalWidth", region["width"])
    original_h = region.get("originalHeight", region["height"])
    offset_x = region.get("offsetX", 0)
    offset_y = region.get("offsetY", 0)
    if region.get("degrees") == 90:
        base_u = region["x"] - (original_h - offset_y - region["height"])
        base_v = region["y"] - (original_w - offset_x - region["width"])
        return (base_u + v * original_h, base_v + (1.0 - u) * original_w)
    base_u = region["x"] - offset_x
    base_v = region["y"] - (original_h - offset_y - region["height"])
    return (base_u + u * original_w, base_v + v * original_h)


def _effective_local(spine: dict, bone_name: str, pose: dict | None) -> tuple:
    """Solve the local transform that yields the inherit-aware world."""
    world = spine_world_transforms(spine, pose)
    bone = next(b for b in spine["bones"] if b["name"] == bone_name)
    parent_name = bone.get("parent")
    if not parent_name:
        return world[bone_name]
    return multiply(invert(world[parent_name]), world[bone_name])


def read_skeleton(json_path: str, atlas_path: str | None = None,
                  skin: str | None = None) -> Skeleton:
    """Read Spine JSON + optional atlas into the canonical model (Godot space).

    ``skin`` selects which skin's constraints are baked; the rig's first skin
    is the default, matching the runtime.
    """
    spine = read_spine(json_path)
    atlas_regions = read_atlas_regions(atlas_path)
    page_size = read_png_size(
        resolve_image_for_atlas(atlas_path, json_path)
    )
    page_width, page_height = page_size if page_size else (1, 1)

    model = Skeleton()
    model.texture_path = ""
    if atlas_path:
        model.notes.append(f"atlas: {atlas_path}")
    else:
        model.notes.append(
            "atlas: none — UVs computed against a 1x1 page (pass --atlas)"
        )

    # ---- bones: mirror to Godot space, solving effective locals for inherit
    bone_relative_path = {}
    for bone_data in spine["bones"]:
        name = bone_data["name"]
        parent_name = bone_data.get("parent")
        if bone_data.get("inherit", "normal") != "normal":
            # Godot has no inherit modes: solve for the local that produces the
            # same world transform under normal inheritance.
            world = spine_world_transforms(spine)
            parent_world = world.get(parent_name, (1, 0, 0, 1, 0, 0)) if parent_name else (1, 0, 0, 1, 0, 0)
            local = multiply(invert(parent_world), world[name])
            position = (local[4], -local[5])
            rotation_deg = math.degrees(math.atan2(-local[2], local[0]))
        else:
            position = (bone_data.get("x", 0.0), -bone_data.get("y", 0.0))
            rotation_deg = -bone_data.get("rotation", 0.0)

        parent_relative = bone_relative_path.get(parent_name, "")
        bone_relative_path[name] = (
            f"{parent_relative}/{name}" if parent_relative else name
        )
        bone = Bone(
            name=name,
            parent=parent_name,
            position=position,
            rotation_deg=rotation_deg,
            scale=(bone_data.get("scaleX", 1.0), bone_data.get("scaleY", 1.0)),
            length=bone_data.get("length", 0.0),
            inherit=bone_data.get("inherit", "normal"),
            path=bone_relative_path[name],
        )
        # Bind pose carried by the godot->spine leg as extension fields
        # (Godot values, y-down): without it out_godot would emit rest ==
        # node pose and lose scenes whose rest differs from the pose.
        if "restX" in bone_data:
            bone.rest = (
                [bone_data["restX"], bone_data["restY"]],
                bone_data.get("restRotation", 0.0),
                (bone_data.get("restScaleX", 1.0), bone_data.get("restScaleY", 1.0)),
            )
        model.bones.append(bone)
        model.by_name[name] = bone

    # ---- attachments: convert region quads and weighted meshes to polygons
    attachments = spine_attachment_map(spine)
    for slot in spine["slots"]:
        slot_name = slot["name"]
        entry = attachments.get(slot_name)
        if not entry:
            continue
        attachment_name, att = entry
        host = slot["bone"]
        uvs = att.get("uvs", [])
        width = att.get("width", 1.0) or 1.0
        height = att.get("height", 1.0) or 1.0
        vertices = att.get("vertices", [])
        bones = att.get("bones")
        triangles = att.get("triangles", [])

        region = atlas_regions.get(attachment_name) or atlas_regions.get(slot_name)
        if region:
            region_left, region_top, region_right, region_bottom = region_uv_rect(
                region, page_width, page_height
            )
        else:
            region_left, region_top = 0.0, 0.0
            region_right, region_bottom = width, height
        region_w = region_right - region_left
        region_h = region_bottom - region_top

        world = spine_world_transforms(spine)
        world_points = []
        uv_points = []
        weights_by_bone = {}

        # Region attachment: a quad centred at (x, y) rotated by `rotation`.
        if att.get("type", "region") == "region" and not vertices:
            quad = compose(
                (att.get("x", 0.0), att.get("y", 0.0)),
                att.get("rotation", 0.0),
                (att.get("scaleX", 1.0), att.get("scaleY", 1.0)),
            )
            half_w, half_h = width / 2.0, height / 2.0
            corners = [
                (-half_w, -half_h), (-half_w, half_h),
                (half_w, half_h), (half_w, -half_h),
            ]
            bone_world = world.get(host, (1, 0, 0, 1, 0, 0))
            for corner in corners:
                wp = transform(bone_world, transform(quad, corner))
                world_points.append((wp[0], -wp[1]))
            if region:
                left, top, right, bottom = region_uv_rect(region, page_width, page_height)
                uv_points = [[left, top], [right, top], [right, bottom], [left, bottom]]
            else:
                uv_points = [[0, 0], [width, 0], [width, height], [0, height]]
            weights_by_bone[host] = {i: 1.0 for i in range(4)}
        elif bones or (vertices and isinstance(vertices[0], (int, float))
                       and int(vertices[0]) >= 1 and len(uvs) // 2 != len(vertices) // 2):
            # Weighted mesh. Spine 4.2 writes the `bones` key only when the
            # attachment defines its own weight list; a mesh without it still
            # carries weighted vertices ([boneCount, (idx, x, y, w)...]) — the
            # hero's cape is 621 floats that decode to 45 weighted vertices,
            # not 310 unweighted pairs. Distinguish by density: weighted
            # entries consume >= 5 floats per vertex, so vertex counts don't
            # match len(vertices)/2.
            cursor = 0
            vertex_count = 0
            while cursor < len(vertices):
                bone_count = int(vertices[cursor])
                cursor += 1
                accumulated = (0.0, 0.0)
                for _ in range(bone_count):
                    bone_idx = int(vertices[cursor])
                    local = (vertices[cursor + 1], vertices[cursor + 2])
                    weight = vertices[cursor + 3]
                    cursor += 4
                    bone_name = spine["bones"][bone_idx]["name"]
                    bone_world = world[bone_name]
                    world_point = transform(bone_world, local)
                    accumulated = (
                        accumulated[0] + world_point[0] * weight,
                        accumulated[1] + world_point[1] * weight,
                    )
                    weights_by_bone.setdefault(bone_name, {})[vertex_count] = weight
                world_points.append(accumulated)
                vertex_count += 1
            for uv_index in range(0, min(len(uvs), vertex_count * 2), 2):
                if region:
                    uv_points.append(list(region_uv_for_vertex(
                        region, uvs[uv_index], uvs[uv_index + 1], page_width, page_height
                    )))
                else:
                    uv_points.append([uvs[uv_index] * width, uvs[uv_index + 1] * height])
        else:
            # Unweighted mesh: vertices are in the host bone's local space.
            bone_world = world.get(host, (1, 0, 0, 1, 0, 0))
            for index in range(0, len(vertices) - 1, 2):
                wp = transform(bone_world, (vertices[index], vertices[index + 1]))
                world_points.append((wp[0], -wp[1]))
            for uv_index in range(0, min(len(uvs), len(vertices)), 2):
                if region:
                    uv_points.append(list(region_uv_for_vertex(
                        region, uvs[uv_index], uvs[uv_index + 1], page_width, page_height
                    )))
                else:
                    uv_points.append([uvs[uv_index] * width, uvs[uv_index + 1] * height])
            # Record the host so the polygon carries its slot's bone through
            # Godot and back: an empty weights list makes the Godot writer bind
            # the polygon to the root bone, and the round trip then re-emits
            # the slot on the wrong bone.
            weights_by_bone[host] = {i: 1.0 for i in range(len(world_points))}

        # The JSON stores vertex locals relative to each influencing bone (in
        # Spine space); Godot wants node-local vertices plus a node position.
        # The influencing bone with the largest total weight is the natural
        # node anchor: its position becomes the Polygon2D node position and
        # the weighted world points become node-local (both mirrored back to
        # Godot's y-down space).
        anchor = max(weights_by_bone.items(),
                     key=lambda item: sum(item[1].values()))[0] \
            if weights_by_bone else host
        anchor_world = world.get(anchor, (1, 0, 0, 1, 0, 0))
        # spine world is y-up; the Godot node position is y-down — mirror it.
        node_pos = (anchor_world[4], -anchor_world[5])
        model.attachments.append({
            "name": slot_name.lower(),
            "polygon": [(p[0] - anchor_world[4], -(p[1] - anchor_world[5]))
                        for p in world_points],
            "uv": uv_points,
            "polygons": triangles,
            "weights": [
                (bn, [vw.get(i, 0.0) for i in range(len(world_points))])
                for bn, vw in weights_by_bone.items()
            ],
            "position": [node_pos[0], node_pos[1]],
            "offset": (0.0, 0.0),
            "internal_vertices": 0,
        })

    # ---- animations: Spine offsets → Godot absolute values
    # Constraints are baked first: Godot has no IK/path constraints, so the
    # bones they drive must carry the solved transforms as ordinary keys or the
    # rig lands in its setup pose. See src/constraints.py.
    from . import constraints
    bone_relative = bone_relative_path
    baked_bones = set()
    for anim_name, animation in spine["animations"].items():
        bones = dict(animation.get("bones", {}))
        baked = constraints.bake_animation(spine, anim_name, skin=skin)
        baked_bones.update(baked)
        for bone_name, channels in baked.items():
            bones[bone_name] = channels
        tracks = {}
        for bone_name, props in bones.items():
            setup = next((b for b in spine["bones"] if b["name"] == bone_name), {})
            setup_rot = setup.get("rotation", 0.0)
            setup_pos = (setup.get("x", 0.0), setup.get("y", 0.0))
            if props.get("rotate"):
                tracks.setdefault(bone_name, {})["rotate"] = [
                    {"time": k.get("time", 0.0), "angle": -(setup_rot + k.get("value", 0.0)),
                     # Curve stays in spine space (absolute time/value control
                     # points, per CurveTimeline.setBezier). Each writer maps
                     # it to its own interpolation; see out_godot.
                     **({"curve": k["curve"]} if k.get("curve") is not None else {})}
                    for k in props["rotate"]
                ]
            if props.get("translate"):
                tracks.setdefault(bone_name, {})["translate"] = [
                    {"time": k.get("time", 0.0),
                     "x": setup_pos[0] + k.get("x", 0.0),
                     "y": -(setup_pos[1] + k.get("y", 0.0)),
                     **({"curve": k["curve"]} if k.get("curve") is not None else {})}
                    for k in props["translate"]
                ]
        model.animations[anim_name] = tracks

    # Report what the conversion could and could not carry, so the CLI can say
    # it out loud instead of leaving the user to guess from the output file.
    ik = spine.get("ik") or []
    path = spine.get("path") or []
    if ik or path:
        skin_name = skin or (spine.get("skins") or [{}])[0].get("name", "?")
        probe = constraints.ConstraintSolver(spine, skin=skin)
        active = [c["name"] for c in ik + path if probe._is_active(c)]
        model.notes.append(
            f"constraints baked (skin {skin_name!r}): "
            + (", ".join(active) if active else "none active")
        )
        if len(active) < len(ik) + len(path):
            model.notes.append(
                f"constraints skipped: {len(ik) + len(path) - len(active)} "
                "not active under this skin"
            )
    unsupported = constraints.unsupported_constraints(spine)
    if unsupported:
        kinds = sorted({kind for kind, _ in unsupported})
        model.notes.append(
            "not converted: " + ", ".join(kinds)
            + f" ({len(unsupported)} total) — bake them in Spine first"
        )
    if baked_bones:
        model.notes.append(f"baked bones: {len(baked_bones)}")

    return model
