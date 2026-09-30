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
    Attachment, Bone, Key, Skeleton,
    compose, invert, multiply, transform,
)


def read_spine(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    data.setdefault("bones", [])
    data.setdefault("slots", [])
    data.setdefault("skins", [])
    data.setdefault("animations", {})
    return data


def read_atlas_regions(atlas_path: str | None) -> dict:
    """Region name -> {x, y, width, height, degrees, originalWidth, ...}.

    Handles both atlas layouts: the legacy one (attribute lines indented
    under the region name) and Spine 4.x's compact one (every line
    flush-left; keys like ``size:``/``bounds:`` carry a colon, region and
    page names do not).
    """
    regions = {}
    if not atlas_path or not Path(atlas_path).exists():
        return regions
    import re
    current = None
    page = None
    for line in Path(atlas_path).read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            current = None
            continue
        key, sep, value = stripped.partition(":")
        is_attr = bool(sep) and re.fullmatch(r"[a-zA-Z_]+", key.strip())
        if re.search(r"\.(png|jpg|webp)$", stripped, re.I):
            current = None  # page boundary
            page = stripped
            continue
        if is_attr:
            if current is None:
                continue
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
                        "page": page,
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
        current = stripped
    return regions


def constraint_worlds(spine: dict, skin: str | None = None) -> dict:
    """Bone name -> world matrix (spine space, y-up) from the RUNTIME solver:
    setup pose, constraints applied, skin gating. The runtime leaves bones a
    `skin: true` constraint deactivates at (0, 0); plain FK would place them
    at their setup transforms. This is the ground truth the converted meshes
    must match."""
    from . import constraints
    solver = constraints.ConstraintSolver(spine, skin=skin)
    solver.apply()
    return {
        name: (st.a, st.b, st.c, st.d, st.world_x, st.world_y)
        for name, st in solver.bones.items()
    }


def region_uv_rect(region: dict) -> tuple:
    x, y = region["x"], region["y"]
    w, h = region["width"], region["height"]
    if region.get("degrees") == 90:
        return (x, y, x + h, y + w)
    return (x, y, x + w, y + h)


def region_corner_uvs(region: dict) -> list:
    left, top, right, bottom = region_uv_rect(region)
    if region.get("degrees") == 90:
        return [(right, bottom), (left, bottom), (left, top), (right, top)]
    return [(left, bottom), (left, top), (right, top), (right, bottom)]


def region_uv_for_vertex(region: dict, u: float, v: float) -> tuple:
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


def _curve_in_key_space(curve, value_map, axis: int | None = None):
    """A Spine curve's value components -> the key's own value space.

    Spine control points are absolute time/value in the timeline's own space
    (offsets from setup for translate, degrees for rotate); the rig stores
    every curve in the space of the key values it belongs to, so a reader
    maps the value components through the same transform it applies to the
    values themselves. ``axis`` selects one quadruple of a two-axis track;
    ``None`` maps every quadruple, which is what rotate and scale need.
    """
    if not isinstance(curve, (list, tuple)):
        return curve
    out = list(curve)
    start = 0 if axis is None else axis * 4
    for base in range(start, len(out) - 3, 4):
        out[base + 1] = value_map(out[base + 1])
        out[base + 3] = value_map(out[base + 3])
    return tuple(out)


def _curve_two_axis(curve, map_x, map_y):
    """A two-axis curve with each quadruple mapped through its OWN transform.

    Translate curves carry one quadruple per value axis, and the two axes are
    converted differently (x keeps its sign and gains the setup offset, y
    flips), so one ``value_map`` cannot do both. Mapping only the y quadruple
    left the x control values in Spine units: the keys stayed exact and only
    the interpolation inside a segment drifted — a rig whose translation keys
    all agreed still walked its body and arms off centre mid-segment.

    Spine shares a single 4-float curve between both axes; expanding it keeps
    each axis in its own space instead of picking one axis's transform.
    """
    if not isinstance(curve, (list, tuple)):
        return curve
    out = list(curve)
    if len(out) >= 8:
        for base, mapper in ((0, map_x), (4, map_y)):
            out[base + 1] = mapper(out[base + 1])
            out[base + 3] = mapper(out[base + 3])
        return tuple(out)
    if len(out) >= 4:
        mapped = []
        for mapper in (map_x, map_y):
            quad = list(out[:4])
            quad[1] = mapper(quad[1])
            quad[3] = mapper(quad[3])
            mapped.extend(quad)
        return tuple(mapped)
    return tuple(out)


def read_skeleton(json_path: str, atlas_path: str | None = None,
                  skin: str | None = None) -> Skeleton:
    """Read Spine JSON + optional atlas into the canonical model (Godot space).

    ``skin`` selects which skin's constraints are baked; the rig's first skin
    is the default, matching the runtime.
    """
    spine = read_spine(json_path)
    atlas_regions = read_atlas_regions(atlas_path)
    model = Skeleton()
    model.texture_path = ""
    if atlas_path:
        model.notes.append(f"atlas: {atlas_path}")
    else:
        model.notes.append(
            "atlas: none — UVs computed against a 1x1 page (pass --atlas)"
        )

    # ---- bones: mirror to Godot space, solving effective locals for inherit
    from . import constraints
    # ONE map of the RUNTIME's setup worlds, for everything below: constraints
    # solved, inherit modes applied, skin-gated bones left at the origin. Bones,
    # attachment vertices and bind frames all read it, and it used to be
    # recomputed per attachment (a full solver run each time).
    worlds = constraint_worlds(spine, skin=skin)
    solved_names = constraints.resolved_setup_bones(spine)
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    bone_relative_path = {}
    for bone_data in spine["bones"]:
        name = bone_data["name"]
        parent_name = bone_data.get("parent")
        parent_world = worlds.get(parent_name, identity) if parent_name else identity
        if bone_data.get("inherit", "normal") != "normal":
            # Godot has no inherit modes: solve for the local that produces the
            # same world transform under normal inheritance.
            local = multiply(invert(parent_world), worlds[name])
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
        if name in solved_names:
            # Godot and SkelForm cannot run a constraint, so the bones one drives
            # — and those ignoring their parent's rotation — carry the SOLVED
            # setup local as well: the source's setup pose IS constraint-solved,
            # so without this a static scene stands in a pose the source never
            # shows (the hero's `thigh1` was 8.4 degrees off, the last of the
            # framing difference between the two panes). The raw local stays in
            # `position`/`rotation_deg`, which the Spine leg re-exports together
            # with the constraints and must not solve twice.
            local = multiply(invert(parent_world), worlds[name])
            bone.setup_solved = (
                (local[4], -local[5]),
                math.degrees(math.atan2(-local[2], local[0])),
                (math.hypot(local[0], local[2]), math.hypot(local[1], local[3])),
            )
        model.bones.append(bone)
        model.by_name[name] = bone

    # ---- attachments: convert region quads and weighted meshes to polygons
    # EVERY default-skin entry is carried, not just the setup pick: the Godot
    # leg must hold all variants so its viewer can switch attachments per
    # slot the same way the Spine viewer does.
    skins = spine["skins"]
    skin_attachments = (
        skins[0]["attachments"] if isinstance(skins, list) else next(iter(skins.values()))
    )
    # Mirror the runtime's ACTIVITY gate: a bone flagged `skin: true` (Spine's
    # skinRequired) starts inactive, and `Skeleton.updateCache` only activates
    # the ACTIVE skin's own bones and their ancestors. A slot on an inactive
    # bone draws nothing, so its entries are not equipped — the hero's `chain*`
    # bones are declared by the `weapon/morningstar` skin alone, and the
    # default-skin rig must not render the chain its JSON and atlas still
    # describe. Without this the converted rig draws weapons the Spine pane
    # never shows, and they stretch its framing box out with them.
    from . import constraints
    inactive = (set(bone["name"] for bone in spine["bones"])
                - constraints.active_bones(spine, skin))
    # Mirror the runtime's equipping: a skin entry is drawn only when it is
    # the slot's setup attachment or an attachment timeline equips it. The
    # Spine runtime never renders the rest; the Godot leg hides them.
    equipped_by_slot: dict[str, set] = {}
    setup_by_slot: dict[str, str | None] = {}
    for slot in spine["slots"]:
        setup = slot.get("attachment")
        setup_by_slot[slot["name"]] = setup
        names = {setup} if setup else set()
        for animation in spine.get("animations", {}).values():
            for key in (animation.get("slots", {})
                        .get(slot["name"], {}).get("attachment", [])):
                if key.get("name"):
                    names.add(key["name"])
        equipped_by_slot[slot["name"]] = names
    attachment_jobs = []
    for slot in spine["slots"]:
        entries = skin_attachments.get(slot["name"], {})
        for attachment_name, att in entries.items():
            attachment_jobs.append((slot, attachment_name, att))
    for slot, attachment_name, att in attachment_jobs:
        slot_name = slot["name"]
        host = slot["bone"]
        entry_name = att.get("name", attachment_name)
        is_equipped = entry_name in equipped_by_slot.get(slot_name, set()) \
            and host not in inactive
        uvs = att.get("uvs", [])
        width = att.get("width", 1.0) or 1.0
        height = att.get("height", 1.0) or 1.0
        vertices = att.get("vertices", [])
        bones = att.get("bones")
        triangles = att.get("triangles", [])

        region = atlas_regions.get(attachment_name) or atlas_regions.get(slot_name)
        if region:
            region_left, region_top, region_right, region_bottom = region_uv_rect(
                region
            )
        else:
            region_left, region_top = 0.0, 0.0
            region_right, region_bottom = width, height
        region_w = region_right - region_left
        region_h = region_bottom - region_top

        # Attachment worlds come from the RUNTIME solver (setup + constraints
        # + skin gating), not plain FK: bones a path/IK constraint drives sit
        # elsewhere at setup than plain FK computes — the hero's
        # thigh2/foot2/shin2 meshes drifted up to 1 unit without this.
        world = constraint_worlds(spine, skin=skin)
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
                # Spine space (y-up) — the polygon conversion below mirrors
                # once. Pre-mirroring here would double-flip Y.
                world_points.append((wp[0], wp[1]))
            # The uv must be the PER-CORNER pair the source's runtime samples
            # for the same corner (`RegionAttachment.updateRegion`): corner 0
            # is (left, bottom) of the packed rect, and the list walks the
            # corners the same way the geometry does. Pairing them any other
            # way — the old list started at (left, top) — flips v, so every
            # region the writer has to draw as geometry (any rotation that is
            # not a multiple of 90) came out upside down while the mesh path
            # beside it was correct. `region_corner_uvs` is that mapping, and
            # meshes already used it.
            if region:
                uv_points = [list(pair) for pair in
                             region_corner_uvs(region)]
            else:
                uv_points = [[0, height], [0, 0], [width, 0], [width, height]]
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
                        region, uvs[uv_index], uvs[uv_index + 1]
                    )))
                else:
                    uv_points.append([uvs[uv_index] * width, uvs[uv_index + 1] * height])
        else:
            # Unweighted mesh: vertices are in the host bone's local space.
            bone_world = world.get(host, (1, 0, 0, 1, 0, 0))
            for index in range(0, len(vertices) - 1, 2):
                wp = transform(bone_world, (vertices[index], vertices[index + 1]))
                # Spine space (y-up) — the polygon conversion below mirrors
                # once. Pre-mirroring here would double-flip Y (the template
                # dummy's unequipped-eye offset came from exactly this).
                world_points.append((wp[0], wp[1]))
            for uv_index in range(0, min(len(uvs), len(vertices)), 2):
                if region:
                    uv_points.append(list(region_uv_for_vertex(
                        region, uvs[uv_index], uvs[uv_index + 1]
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
        model.attachments.append(Attachment(
            # Keep the skin entry's exact case: the viewer lists attachment
            # names as-is, and options must match the source's spelling.
            name=entry_name,
            slot=slot_name,
            polygon=[(p[0] - anchor_world[4], -(p[1] - anchor_world[5]))
                     for p in world_points],
            uv=uv_points,
            # Spine's `triangles` is a soup; the rig keeps one group per
            # triangle (a writer that fans a group must never see 192 indices
            # as a single polygon).
            #
            # A region attachment has no `triangles` key — Spine only writes
            # them for meshes — but a quad IS two triangles. Without them the
            # writer's mesh path emits four vertices and NO faces, so the
            # runtime rasterizes nothing: the hero's limbs (every region whose
            # `rotation` is not a multiple of 90, which cannot collapse into a
            # texture rect) simply do not draw.
            polygons=([triangles[i:i + 3]
                       for i in range(0, len(triangles) - 2, 3)]
                      or ([[0, 1, 2], [0, 2, 3]] if len(world_points) == 4
                          else [])),
            # `constructVerts` starts every vertex at the OWNING bone's
            # transform (`inheritVert(init_pos, ownerBone)`) before the binds
            # move it, so the writer needs that world too. The owner is the
            # anchor the polygon is already keyed against, and it is often a
            # bone the source did not list weights for.
            bind_worlds={bn: world[bn] for bn in weights_by_bone
                         if bn in world}
            | {name: world[name] for name in (anchor, host) if name in world},
            weights=[
                (bn, [vw.get(i, 0.0) for i in range(len(world_points))])
                for bn, vw in weights_by_bone.items()
            ],
            position=(node_pos[0], node_pos[1]),
            equipped=is_equipped,
            # The runtime draws the slot's setup attachment when no timeline
            # has applied yet; a slot whose setup attachment is absent draws
            # nothing there — and neither does a slot on a bone the skin
            # deactivates. This single flag is what every leg asks ("does the
            # SETUP pose draw this?"): out_godot's initial `visible`,
            # out_spine's slot attachment and bounds, and out_skelform's
            # `hidden`/`init_hidden`. `equipped` answers the different question
            # of whether any animation ever draws the entry.
            setup=bool(setup_by_slot.get(slot_name))
            and entry_name == setup_by_slot.get(slot_name)
            and host not in inactive,
            uv_rotation=int(region.get("degrees", 0) or 0) if region else 0,
            page=region["page"] if region else "",
        ))

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
                    Key(time=k.get("time", 0.0),
                        angle=-(setup_rot + k.get("value", 0.0)),
                        curve=_curve_in_key_space(
                            k.get("curve"), lambda v: -(setup_rot + v)))
                    for k in props["rotate"]
                ]
            if props.get("translate"):
                tracks.setdefault(bone_name, {})["translate"] = [
                    Key(time=k.get("time", 0.0),
                        x=setup_pos[0] + k.get("x", 0.0),
                        y=-(setup_pos[1] + k.get("y", 0.0)),
                        curve=_curve_two_axis(
                            k.get("curve"),
                            lambda v: setup_pos[0] + v,
                            lambda v: -(setup_pos[1] + v)))
                    for k in props["translate"]
                ]
            if props.get("scale"):
                # Absolute local scale; a key that omits x/y means 1 (the
                # runtime's readTimeline2 default), not the previous key.
                tracks.setdefault(bone_name, {})["scale"] = [
                    Key(time=k.get("time", 0.0),
                        scale=(k.get("x", 1.0), k.get("y", 1.0)),
                        curve=k.get("curve"))
                    for k in props["scale"]
                ]
        model.animations[anim_name] = tracks
        # Attachment timelines: which attachment each slot draws over time
        # (a missing/nameless key hides the slot). Kept per animation, beside
        # the bone tracks.
        slots = animation.get("slots") or {}
        slot_tracks = {}
        for slot_name, channel in slots.items():
            keys = channel.get("attachment") or []
            if not keys:
                continue
            slot_tracks[slot_name] = [
                {"time": k.get("time", 0.0), "attachment": k.get("name")}
                for k in keys
            ]
        if slot_tracks:
            model.slot_timelines[anim_name] = slot_tracks

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
