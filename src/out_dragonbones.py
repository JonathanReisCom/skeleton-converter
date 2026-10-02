"""Write the canonical model as DragonBones JSON: `<name>_ske.json` + `<name>_tex.json`.

DragonBones — and LoongBones, the same runtime under a new name — is the format
this converter's canonical model sits *closest* to. Its rig space is y-down with
a clockwise-positive rotation, exactly like Godot's, so this leg mirrors
nothing: a local transform travels verbatim. (`dragonBones.yDown = true` in the
runtime's own bundle, and `Transform.toMatrix` writes the same `(cos, sin, -sin,
cos)` block Godot's `Transform2D` does.)

Everything below is ported from the runtime's parser, never from documentation:

- `ObjectDataParser._parseTransform` — `skX`/`skY` are read as skew/rotation
  (the `rotate`/`skew` pair wins when present, so a file may carry either).
- `ObjectDataParser._parseGeometry` — a weighted mesh carries `slotPose` (the
  mesh's local frame → armature space) plus `bonePose` (7 floats per bone: its
  FILE index and its bind world matrix), and `weights` as
  `[count, (boneIndex, weight)…]` per vertex. The runtime bakes
  `bonePose⁻¹ · slotPose · vertex` per influence, so the bind basis must be the
  bone's REST world — the same basis `out_spine` writes.
- `ObjectDataParser._parseTweenFrame` — a frame's `duration` is in integer
  frames, its position is implicit (accumulated), and a **missing**
  `tweenEasing` means `-2`: a held segment, not a linear one. Every
  interpolated frame therefore carries `tweenEasing` or `curve`.
- `ObjectDataParser._parsePivot` — an image display's pivot defaults to
  `(0.5, 0.5)`, so this writer states `(0, 0)` explicitly and anchors the
  sprite's top-left.
- `PixiTextureAtlasData.renderTexture` — a SubTexture becomes a Pixi texture
  whose frame is the region rect; `MeshGeometry`'s own default UVs are
  `[0,0, 1,0, 1,1, 0,1]`, so mesh UVs are normalized against the display's
  SubTexture region, which is what this writer emits.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .curves import clamp_first_key
from .model import (
    Skeleton, compose, godot_matrix_from_standard, godot_rest_worlds,
    godot_world_transforms, invert, multiply, transform,
)
from .png import read_png_size

DEFAULT_FPS = 24
# DragonBones stores key times as integer frames. 24 is the format's own
# default; a finer grid is used only when it removes a collapse, because the
# residual shift of a few milliseconds is not worth a dense editor timeline.
FPS_CHOICES = (24, 30, 60, 120, 240)
# The runtime's `Transform.fromMatrix` splits a matrix with `atan(b/a)` and
# `atan(-c/d)`, treating a quarter-turn as the boundary where the other column
# is the reliable one. Ported verbatim so a decomposed transform reproduces the
# matrix the runtime rebuilds from it.
_QUARTER = math.pi / 4.0


def _matrix(m: tuple) -> list:
    """A model matrix (a, b, c, d, tx, ty — `x' = a·x + b·y`) as DragonBones.

    The runtime's `Matrix` multiplies as `x' = a·x + c·y + tx`, so its field
    order is the transpose of the model's — the same order Godot's file-literal
    `Transform2D` uses, which is why `godot_matrix_from_standard` is the whole
    conversion.
    """
    return list(godot_matrix_from_standard(m))


def _decompose(m: tuple) -> tuple:
    """(rotation_deg, skew_deg, (scale_x, scale_y), (x, y)) — the runtime's split.

    The runtime reads a matrix as `x' = a·x + c·y`, so the model's off-diagonal
    fields swap before the split — decomposing them in the model's own order
    turns a pure rotation into a skew.
    """
    a, b, c, d = m[0], m[2], m[1], m[3]
    tx, ty = m[4], m[5]
    rotation = math.atan(b / a) if a else math.copysign(math.pi / 2.0, b)
    skew_x = math.atan(-c / d) if d else math.copysign(math.pi / 2.0, -c)
    scale_x = (a / math.cos(rotation) if -_QUARTER < rotation < _QUARTER
               else b / math.sin(rotation))
    scale_y = (d / math.cos(skew_x) if -_QUARTER < skew_x < _QUARTER
               else -c / math.sin(skew_x))
    # `Transform.fromMatrix` restores a negative scale by flipping the column
    # and adding half a turn; a fresh Transform starts at scale 1.0, so the
    # "backup" the runtime compares against is positive here too.
    if scale_x < 0.0:
        scale_x = -scale_x
        rotation -= math.pi
    if scale_y < 0.0:
        scale_y = -scale_y
        skew_x -= math.pi
    return (math.degrees(rotation), math.degrees(skew_x - rotation),
            (scale_x, scale_y), (tx, ty))


def _transform(m: tuple) -> dict:
    """A local transform as the JSON carries it.

    `skX`/`skY` are the ABSOLUTE skew and rotation columns (the parser subtracts
    one from the other to recover the skew), so both must be present: a lone
    `skY` would leave `skX` at its 0 default and read back as `-rotation`.
    """
    rotation, skew, scale, position = _decompose(m)
    entry = {"x": round(position[0], 6), "y": round(position[1], 6),
             "skX": round(rotation + skew, 6), "skY": round(rotation, 6)}
    if abs(scale[0] - 1.0) > 1e-9:
        entry["scX"] = round(scale[0], 6)
    if abs(scale[1] - 1.0) > 1e-9:
        entry["scY"] = round(scale[1], 6)
    return entry


def _uv_rect(uv: list) -> tuple:
    us = [p[0] for p in uv]
    vs = [p[1] for p in uv]
    return min(us), min(vs), max(us), max(vs)


def triangulate(attachment) -> list:
    """Convex index groups -> a flat triangle list (a quad fans its own order)."""
    triangles = []
    for group in attachment.polygons or []:
        for index in range(1, len(group) - 1):
            triangles.extend([group[0], group[index], group[index + 1]])
    if not triangles:
        for index in range(1, len(attachment.polygon) - 1):
            triangles.extend([0, index, index + 1])
    return triangles


def _is_rigid(attachment) -> bool:
    """True when the attachment follows exactly one bone at full weight.

    An image display is placed by the slot's bone alone, so only a rigid quad
    reproduces it: a vertex split across bones (or a partial weight) is a
    skinned mesh, and writing it as a sprite would freeze the skin.
    """
    seen = set()
    for bone_name, vertex_weights in attachment.weights:
        for weight in vertex_weights:
            if weight <= 0.0:
                continue
            if abs(weight - 1.0) > 1e-6:
                return False
            seen.add(bone_name)
    return len(seen) == 1


def _has_deform(attachment) -> bool:
    """True when any animation moves this attachment's vertices.

    DragonBones' FFD timeline targets a MESH display by name; a sprite cannot
    carry per-vertex offsets, so a deformed quad must stay geometry.
    """
    return any(keys for keys in (attachment.deform or {}).values())


def _rect_display(attachment, host_world: tuple, world: tuple,
                  region: tuple) -> dict | None:
    """The image display for a plain rectangle, or None when it is not one.

    An image display is a Sprite: its size comes from the SubTexture rect, its
    anchor from `pivot` (stated as the top-left corner), and it is placed by the
    slot's matrix — `bone_world · display_transform`. A rectangle whose four
    corners are axis-aligned in the host bone's frame, sampled from a
    rectangular region, is exactly that; anything else (a rotated region in the
    atlas, a sheared quad, a skinned or deformed mesh) is written as geometry
    instead.
    """
    if len(attachment.polygon) != 4 or attachment.uv_rotation:
        return None
    if not _is_rigid(attachment) or _has_deform(attachment):
        return None
    left, top, right, bottom = region
    if right - left <= 0.0 or bottom - top <= 0.0:
        return None
    # Corner lookup by the UV each vertex carries: the sprite's local +x runs
    # along +u and its +y along +v, both from the region's top-left.
    def find(u: float, v: float) -> int | None:
        for index, point in enumerate(attachment.uv):
            if abs(point[0] - u) <= 1e-6 and abs(point[1] - v) <= 1e-6:
                return index
        return None

    corner = find(left, top)
    along_u = find(right, top)
    along_v = find(left, bottom)
    opposite = find(right, bottom)
    if None in (corner, along_u, along_v, opposite):
        return None
    inverse_host = invert(host_world)
    local = [transform(inverse_host,
                       transform(world, (point[0] + attachment.offset[0],
                                         point[1] + attachment.offset[1])))
             for point in attachment.polygon]
    origin, edge_u, edge_v = local[corner], local[along_u], local[along_v]
    width, height = right - left, bottom - top
    span_u = ((edge_u[0] - origin[0]) / width, (edge_u[1] - origin[1]) / width)
    span_v = ((edge_v[0] - origin[0]) / height, (edge_v[1] - origin[1]) / height)
    # The rectangle test, in the frame the display transform will live in: the
    # fourth corner must land where the two edges say, and the edges must be
    # perpendicular. A quad that fails either is still drawable — as a mesh.
    expected = (origin[0] + span_u[0] * width + span_v[0] * height,
                origin[1] + span_u[1] * width + span_v[1] * height)
    tolerance = 1e-4 * max(1.0, abs(width), abs(height))
    if (abs(expected[0] - local[opposite][0]) > tolerance
            or abs(expected[1] - local[opposite][1]) > tolerance):
        return None
    if abs(span_u[0] * span_v[0] + span_u[1] * span_v[1]) > 1e-6:
        return None
    matrix = (span_u[0], span_v[0], span_u[1], span_v[1], origin[0], origin[1])
    return {"transform": _transform(matrix),
            "pivot": {"x": 0.0, "y": 0.0},
            "width": round(width, 6), "height": round(height, 6)}


def _slot_host(attachments: list, model) -> str:
    """The bone a slot hangs from: the one carrying the most weight."""
    totals = {}
    for attachment in attachments:
        for bone_name, vertex_weights in attachment.weights:
            totals[bone_name] = totals.get(bone_name, 0.0) + sum(vertex_weights)
    if totals:
        return max(totals, key=totals.get)
    return model.bones[0].name if model.bones else ""


def _curve_field(curve, start: float, end: float, value0: float,
                 value1: float) -> list | None:
    """One axis of a Spine curve as the runtime's normalized easing curve.

    `_samplingEasingCurve` reads the array as a chain of cubic segments: each
    group of three (x, y) pairs is one segment's control points, the segment
    starts where the previous ended, and the last group's endpoint is `(1, 1)`
    when the array length is `3n + 1` — which is why a single eased segment is
    seven numbers, not six (the trailing one is the next segment's boundary).
    """
    if not isinstance(curve, (list, tuple)) or len(curve) < 4:
        return None
    span_t, span_v = end - start, value1 - value0
    if span_t <= 0.0:
        return None
    mapped = []
    for pair in (0, 2):
        x, y = curve[pair], curve[pair + 1]
        mapped.extend([round((x - start) / span_t, 6),
                       0.0 if span_v == 0.0 else round((y - value0) / span_v, 6)])
    if span_v == 0.0:
        # A constant segment eases nowhere; the runtime still samples it, so
        # the value column is irrelevant and a flat line is the honest shape.
        mapped[1] = mapped[3] = 0.0
    return mapped + [1.0, 1.0, 1.0]


def _tween(key, following, curve_of) -> dict:
    """The interpolation a frame declares.

    A missing `tweenEasing` IS `-2` (held), so a held frame states nothing —
    which is what the exporter writes and keeps the frames small. Every
    interpolated frame must state `0` or a curve, or it would read as held.
    """
    if key.curve == "stepped" or following is None:
        return {}
    curve = curve_of(key, following) if curve_of else None
    if curve:
        return {"curve": curve}
    return {"tweenEasing": 0}


def _entries(keys: list, fields: dict) -> list:
    """Canonical keys -> frames in seconds, with the extrapolated first key."""
    entries = []
    for key in keys:
        entry = {"time": round(key.time, 6)}
        for name, getter in fields.items():
            entry[name] = round(getter(key), 6)
        entry["_key"] = key
        entries.append(entry)
    return clamp_first_key(entries, tuple(fields))


def _frames(entries: list, fps: float, frame_count: int, curve_of=None) -> list:
    """Frames in seconds -> DragonBones frames (integer durations, no positions).

    Two keys closer together than one frame land on the SAME frame index, and a
    timeline may not have two frames at one position: the runtime interpolates
    over the segment's length, so a zero-length one divides by zero and every
    bone downstream of it reads NaN. The later key wins — the pose at the end of
    that frame is the one the rig holds — and the earlier one is dropped, which
    is the resolution the format has. `_frame_timing` reports how many keys that
    costs.
    """
    placed = []
    for entry in entries:
        frame = int(round(entry["time"] * fps))
        if placed and placed[-1][0] == frame:
            placed[-1] = (frame, entry)
        else:
            placed.append((frame, entry))
    frames = []
    for index, (frame, entry) in enumerate(placed):
        if index + 1 < len(placed):
            duration = max(0, placed[index + 1][0] - frame)
            following = placed[index + 1][1]
        else:
            duration = max(0, frame_count - frame)
            following = None
        frame_entry = {"duration": duration}
        frame_entry.update(_tween(entry["_key"], following, curve_of))
        frame_entry.update({name: value for name, value in entry.items()
                            if name != "time" and name != "_key"})
        frames.append(frame_entry)
    return frames


def _max_time(model, animation: str | None = None) -> float:
    """The last key of one animation (or of the whole rig) in seconds."""
    tracks = ([model.animations.get(animation)] if animation
              else model.animations.values())
    times = [key.time for entry in tracks if entry
             for channels in entry.values() for keys in channels.values()
             for key in keys]
    slot_tracks = ([model.slot_timelines.get(animation)] if animation
                   else model.slot_timelines.values())
    times += [key["time"] for entry in slot_tracks if entry
              for keys in entry.values() for key in keys]
    times += [key["time"] for attachment in model.attachments
              for name, keys in (attachment.deform or {}).items()
              if animation is None or name == animation for key in keys]
    return max(times) if times else 0.0


def _animation_frames(model, animation: str, fps: float) -> int:
    """How many frames an animation lasts, honouring a declared hold.

    A frame-based source loops for its declared length, and its last frame's
    `duration` is a trailing hold — inferring the length from the last key
    would shorten the loop. The longest of the two wins.
    """
    declared = model.animation_durations.get(animation) or 0.0
    return max(1, int(round(max(_max_time(model, animation), declared) * fps)))


def _curves_differ(keys: list, fields: tuple) -> bool:
    """True when two axes of a two-axis track carry different curves.

    DragonBones stores ONE easing per frame, so a track whose x and y ease
    differently cannot be written exactly; the writer falls back to a straight
    segment and the caller reports it.
    """
    for index, key in enumerate(keys[:-1]):
        curve = key.curve
        if curve == "stepped" or not isinstance(curve, (list, tuple)) or len(curve) != 8:
            continue
        if curve[0:4] != curve[4:8]:
            return True
    return False


def _bone_timeline(model, bone_name: str, props: dict, fps: float,
                   frame_count: int, notes: list, animation: str) -> dict:
    """One bone's tracks, as OFFSETS from the bone's setup transform.

    `Bone._updateGlobalTransformMatrix` composes a bone as
    `origin + offset + animationPose`, where `origin` IS the setup transform the
    file declares (`Bone.init`: `this.origin = this._boneData.transform`). A
    timeline value is therefore a delta, and scale is multiplicative — writing
    the absolute pose doubles the setup (the hero's hip landed at
    `-94.89 + -87.91`, 95 units below where the rig puts it).
    """
    bone = model.by_name.get(bone_name)
    setup_position = bone.position if bone else (0.0, 0.0)
    setup_rotation = bone.rotation_deg if bone else 0.0
    setup_scale = bone.scale if bone else (1.0, 1.0)
    timeline = {"name": bone_name}
    if props.get("rotate"):
        keys = props["rotate"]
        entries = _entries(keys, {"rotate": lambda k: k.angle - setup_rotation})
        deltas = [abs(keys[index + 1].angle - keys[index].angle)
                  for index in range(len(keys) - 1)]
        if any(delta > 180.0 for delta in deltas):
            notes.append(
                f"dragonbones: {animation}/{bone_name} turns more than half a "
                "revolution between two keys — the runtime always takes the "
                "short way around, so the poses match and the turn between "
                "them does not")
        timeline["rotateFrame"] = _frames(entries, fps, frame_count)
    if props.get("translate"):
        keys = props["translate"]
        shared = not _curves_differ(keys, ("x", "y"))
        curve_of = (lambda k, f: _curve_field(k.curve, k.time, f["time"],
                                              (k.x - setup_position[0],
                                               k.y - setup_position[1]),
                                              (f["x"], f["y"]))
                    if shared else None)
        if not shared:
            notes.append(f"dragonbones: {animation}/{bone_name} eases x and y "
                         "differently — one easing per frame is all the format "
                         "stores, so the segment is written straight")
        entries = _entries(keys, {"x": lambda k: k.x - setup_position[0],
                                  "y": lambda k: k.y - setup_position[1]})
        timeline["translateFrame"] = _frames(entries, fps, frame_count, curve_of)
    if props.get("scale"):
        keys = props["scale"]
        shared = not _curves_differ(keys, ("x", "y"))
        curve_of = (lambda k, f: _curve_field(k.curve, k.time, f["time"],
                                              (k.scale[0] / (setup_scale[0] or 1.0),
                                               k.scale[1] / (setup_scale[1] or 1.0)),
                                              (f["x"], f["y"]))
                    if shared else None)
        entries = _entries(keys, {
            "x": lambda k: k.scale[0] / (setup_scale[0] or 1.0),
            "y": lambda k: k.scale[1] / (setup_scale[1] or 1.0)})
        timeline["scaleFrame"] = _frames(entries, fps, frame_count, curve_of)
    return timeline


def render_texture_atlas(name: str, image_name: str, image_path: str | None,
                         regions: list) -> dict:
    """The `_tex.json` for one page: page size plus one SubTexture per region."""
    width = height = 0
    if image_path:
        size = read_png_size(image_path)
        if size:
            width, height = size
    return {
        "name": name,
        "imagePath": image_name,
        "width": width,
        "height": height,
        "SubTexture": regions,
    }


def write_dragonbones(model: Skeleton, output_path: str,
                      image_name: str | None = None,
                      image_path: str | None = None,
                      fps: float | None = None) -> dict:
    """Write `<name>_ske.json` + `<name>_tex.json`; return the data document."""
    output = Path(output_path)
    image_name = image_name or "image.png"
    fps = float(fps or model.frame_rate or DEFAULT_FPS)
    world = godot_world_transforms(model)
    rest = godot_rest_worlds(model)
    name = output.name[: -len("_ske.json")] if output.name.endswith("_ske.json") \
        else output.stem

    bones = []
    for bone in model.bones:
        entry = {"name": bone.name}
        if bone.parent:
            entry["parent"] = bone.parent
        if bone.length:
            entry["length"] = round(bone.length, 6)
        local = (bone.position[0], bone.position[1], bone.rotation_deg,
                 bone.scale[0], bone.scale[1])
        if any(abs(value - default) > 1e-9 for value, default in
               zip(local, (0.0, 0.0, 0.0, 1.0, 1.0))):
            # `skX` is the ABSOLUTE skew column: the parser recovers the skew
            # as `skX - skY`, so writing `skY` alone would read back as
            # `-rotation`. A bone has no skew, so the two are the rotation.
            entry["transform"] = {
                "x": round(bone.position[0], 6), "y": round(bone.position[1], 6),
                "skX": round(bone.rotation_deg, 6),
                "skY": round(bone.rotation_deg, 6),
            }
            if abs(bone.scale[0] - 1.0) > 1e-9:
                entry["transform"]["scX"] = round(bone.scale[0], 6)
            if abs(bone.scale[1] - 1.0) > 1e-9:
                entry["transform"]["scY"] = round(bone.scale[1], 6)
        bones.append(entry)

    slots = []
    skin_slots = []
    regions = []
    used_regions: set = set()
    slot_order = []
    by_slot: dict = {}
    for attachment in model.attachments:
        slot_name = attachment.slot or attachment.name
        if slot_name not in by_slot:
            by_slot[slot_name] = []
            slot_order.append(slot_name)
        by_slot[slot_name].append(attachment)

    def region_name(attachment) -> str:
        """A unique SubTexture name: two slots may carry the same entry name."""
        base = attachment.name
        candidate = base
        suffix = 2
        while candidate in used_regions:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used_regions.add(candidate)
        return candidate

    for slot_name in slot_order:
        entries = by_slot[slot_name]
        host = _slot_host(entries, model)
        host_world = world.get(host, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
        displays = []
        setup_index = -1
        for index, attachment in enumerate(entries):
            if attachment.setup:
                setup_index = index
            if not attachment.uv or not attachment.polygon:
                model.notes.append(
                    f"dragonbones: skipped {attachment.name!r} (no geometry)")
                displays.append(None)
                continue
            left, top, right, bottom = _uv_rect(attachment.uv)
            path = region_name(attachment)
            regions.append({
                "name": path,
                "x": round(left, 4), "y": round(top, 4),
                "width": round(right - left, 4),
                "height": round(bottom - top, 4),
            })
            attachment_world = compose(attachment.position, 0.0)
            display = _rect_display(attachment, host_world, attachment_world,
                                    (left, top, right, bottom))
            if display is not None:
                display.update({"type": "image", "name": attachment.name,
                                "path": path})
            else:
                display = _mesh_display(model, attachment, attachment_world,
                                        (left, top, right, bottom), rest, host)
                display["path"] = path
            displays.append(display)
        slots.append({
            "name": slot_name,
            "parent": host,
            "displayIndex": setup_index,
        })
        skin_slots.append({"name": slot_name,
                           "display": displays})

    animations = []
    for animation_name, tracks in model.animations.items():
        frame_count = _animation_frames(model, animation_name, fps)
        bone_timelines = []
        for bone_name, props in tracks.items():
            timeline = _bone_timeline(model, bone_name, props, fps, frame_count,
                                      model.notes, animation_name)
            if len(timeline) > 1:
                bone_timelines.append(timeline)
        slot_timelines = []
        for slot_name, keys in (model.slot_timelines.get(animation_name) or {}).items():
            entries = by_slot.get(slot_name) or []
            index_of = {att.name: index for index, att in enumerate(entries)
                        if att is not None}
            frames = []
            for index, key in enumerate(keys):
                wanted = key["attachment"]
                value = index_of.get(wanted, -1) if wanted else -1
                frame = int(round(key["time"] * fps))
                duration = (int(round(keys[index + 1]["time"] * fps)) - frame
                            if index + 1 < len(keys) else max(0, frame_count - frame))
                frames.append({"duration": max(0, duration), "value": value})
            slot_timelines.append({"name": slot_name, "displayFrame": frames})
        ffd_timelines = []
        for attachment in model.attachments:
            keys = (attachment.deform or {}).get(animation_name)
            if not keys:
                continue
            frames = []
            for index, key in enumerate(keys):
                frame = int(round(key["time"] * fps))
                duration = (int(round(keys[index + 1]["time"] * fps)) - frame
                            if index + 1 < len(keys) else max(0, frame_count - frame))
                entry = {"duration": max(0, duration), "offset": 0,
                         "vertices": [round(value, 6) for delta in key["delta"]
                                      for value in delta]}
                if key.get("curve") == "stepped":
                    entry["tweenEasing"] = -2
                elif key.get("curve"):
                    entry["tweenEasing"] = 0
                else:
                    entry["tweenEasing"] = 0
                frames.append(entry)
            ffd_timelines.append({
                "skin": "default",
                "slot": attachment.slot or attachment.name,
                "name": attachment.name,
                "frame": frames,
            })
        animation = {
            "duration": frame_count,
            "playTimes": 0,
            "name": animation_name,
            "bone": bone_timelines,
            "slot": slot_timelines,
        }
        if ffd_timelines:
            animation["ffd"] = ffd_timelines
        animations.append(animation)

    armature = {
        "type": "Armature",
        "frameRate": int(fps),
        "name": name,
        "bone": bones,
        "slot": slots,
        "skin": [{"name": "default", "slot": skin_slots}],
        "animation": animations,
    }
    document = {
        "frameRate": int(fps),
        "name": name,
        "version": "5.5",
        "compatibleVersion": "5.5",
        "armature": [armature],
    }

    atlas = render_texture_atlas(name, image_name, image_path, regions)
    output.parent.mkdir(parents=True, exist_ok=True)
    texture_path = output.with_name(
        output.name[: -len("_ske.json")] + "_tex.json"
        if output.name.endswith("_ske.json") else output.stem + "_tex.json")
    texture_path.write_text(json.dumps(atlas, indent=2), encoding="utf-8")
    output.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document


def _bind_world(attachment, bone_name: str, rest: dict) -> tuple:
    """The bone's bind world, in the frame this leg draws in.

    `Attachment.bind_worlds` comes from the SPINE reader in Spine space (y-up),
    the frame it baked the polygon out of — the same rule `out_skelform` states.
    This leg draws in the model's own y-down frame, so that world is conjugated
    by the y flip; the `rest` fallback is already in the model's frame. Getting
    this wrong is not subtle: every skinned mesh lands mirrored about the
    origin (the hero's thigh sat 232 units away).
    """
    if attachment.bind_worlds and bone_name in attachment.bind_worlds:
        world = attachment.bind_worlds[bone_name]
        return (world[0], -world[1], -world[2], world[3], world[4], -world[5])
    return rest.get(bone_name) or (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mesh_display(model, attachment, attachment_world: tuple, region: tuple,
                  rest: dict, host: str) -> dict:
    """A mesh display: `slotPose` + `bonePose` state the bind exactly.

    The runtime reconstructs each influence's local point as
    `bonePose⁻¹ · slotPose · vertex` and draws `Σ w · bone_world · local`, so
    the pair (slotPose, bonePose) is the whole skinning contract — the same
    algebra `out_spine` writes as per-bone locals. `bonePose` must list every
    bone the weights name (the parser resolves them by `indexOf`, and a bone
    missing from it indexes off the array).

    A rig whose weights are empty is bound rigidly to the host bone at weight
    1, which is what every reader in this repo produces and what the Godot leg
    writes for the same case.
    """
    bone_index = {bone.name: index for index, bone in enumerate(model.bones)}
    used = []
    for bone_name, _ in attachment.weights:
        if bone_name in bone_index and bone_name not in used:
            used.append(bone_name)
    rigid = not used
    if rigid and host in bone_index:
        used = [host]
    bone_pose = []
    for bone_name in used:
        bind = _bind_world(attachment, bone_name, rest)
        bone_pose.append(bone_index[bone_name])
        bone_pose.extend(_matrix(bind))
    weights = []
    for vertex in range(len(attachment.polygon)):
        influences = [(bone_name, vertex_weights[vertex])
                      for bone_name, vertex_weights in attachment.weights
                      if bone_name in bone_index and vertex < len(vertex_weights)
                      and vertex_weights[vertex] > 0.0]
        if rigid and used:
            influences = [(used[0], 1.0)]
        weights.append(len(influences))
        for bone_name, weight in influences:
            weights.extend([bone_index[bone_name], round(weight, 6)])
    left, top, right, bottom = region
    span_x = (right - left) or 1.0
    span_y = (bottom - top) or 1.0
    display = {
        "type": "mesh",
        "name": attachment.name,
        "path": attachment.name,
        "vertices": [round(value, 6) for point in attachment.polygon
                     for value in (point[0] + attachment.offset[0],
                                   point[1] + attachment.offset[1])],
        "uvs": [round(value, 6) for point in attachment.uv
                for value in ((point[0] - left) / span_x,
                              (point[1] - top) / span_y)],
        "triangles": triangulate(attachment),
        "slotPose": [round(value, 8) for value in _matrix(attachment_world)],
        "bonePose": bone_pose,
        "weights": weights,
    }
    return display