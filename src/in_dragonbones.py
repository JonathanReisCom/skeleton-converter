"""Read DragonBones JSON (`_ske.json` + `_tex.json`) into the canonical model.

The runtime is the contract (DragonBonesJS `ObjectDataParser`), not the
documentation:

- `_parseTransform` reads `rotate`/`skew` when the file carries them and
  `skX`/`skY` otherwise, where `skX` is the ABSOLUTE skew column and `skY` the
  rotation — the recovered skew is `skX - skY`.
- `_parseGeometry` packs a weighted mesh as `slotPose` (mesh-local -> armature
  space) plus `bonePose` (7 floats per bone: its FILE index and its bind world)
  and `weights` as `[count, (boneIndex, weight)…]` per vertex. The runtime bakes
  `bonePose⁻¹ · slotPose · vertex` per influence; the model wants the same
  geometry expressed against the bind worlds, so the vertex is taken to armature
  space and the bind matrices travel with the attachment.
- `_parseTweenFrame` accumulates a frame's position from the previous
  `duration`s and treats a missing `tweenEasing` as `-2` — a HELD segment.
- `Slot._updateDisplayData` sizes an image display from its SubTexture region
  and anchors it by `pivot` (default 0.5, 0.5), so the sprite's rect is
  `display_transform` applied to the region's corners.

Coordinates need no mirroring anywhere: DragonBones is y-down with a
clockwise-positive rotation (`dragonBones.yDown = true`), the same space the
canonical model uses.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .model import (Attachment, Bone, Key, Skeleton, godot_world_transforms,
                    multiply, transform)

DEFAULT_FPS = 24.0


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _matrix(raw: list, offset: int = 0) -> tuple:
    """DragonBones `Matrix` fields (a, b, c, d, tx, ty) -> the model's order.

    The runtime multiplies as `x' = a·x + c·y + tx`; the model's `transform`
    reads `x' = a·x + b·y + tx`, so the two off-diagonal fields swap.
    """
    a, b, c, d, tx, ty = (_number(raw[offset + index]) for index in range(6))
    return (a, c, b, d, tx, ty)


def _ordered_bones(raw_bones: list) -> list:
    """Parent-before-child, keeping the file's order among siblings."""
    by_name = {bone.get("name", ""): bone for bone in raw_bones}
    children: dict = {}
    roots = []
    for bone in raw_bones:
        parent = bone.get("parent") or ""
        if parent and parent in by_name:
            children.setdefault(parent, []).append(bone)
        else:
            roots.append(bone)
    ordered = []

    def visit(bone) -> None:
        ordered.append(bone)
        for child in children.get(bone.get("name", ""), []):
            visit(child)

    for root in roots:
        visit(root)
    return ordered


def load_dragonbones(path: str) -> tuple:
    """The data document and the armature it plays (the first one)."""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    armatures = data.get("armature") or []
    if not armatures:
        raise ValueError(f"{source.name}: no armature in the file")
    return data, armatures[0]


def find_texture_atlas(path: str) -> str | None:
    """The `_tex.json` that belongs to a `_ske.json`.

    DragonBones exports `<name>_ske.json` beside `<name>_tex.json`; a renamed
    file (or a `_ske` suffix the exporter did not use) falls back to the only
    texture atlas next to it, the same way the Spine leg resolves its `.atlas`.
    """
    source = Path(path)
    stem = source.stem
    for candidate_stem in (stem[: -len("_ske")] if stem.endswith("_ske") else stem,
                           stem):
        sibling = source.parent / f"{candidate_stem}_tex.json"
        if sibling.exists():
            return str(sibling)
    candidates = sorted(source.parent.glob("*_tex.json"))
    return str(candidates[0]) if len(candidates) == 1 else None


def read_texture_atlas(path: str | None) -> dict:
    """SubTexture name -> (x, y, width, height), plus the page image name."""
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    regions = {}
    for entry in data.get("SubTexture") or []:
        name = entry.get("name")
        if not name:
            continue
        regions[name] = (_number(entry.get("x")), _number(entry.get("y")),
                         _number(entry.get("width")), _number(entry.get("height")))
    return {"regions": regions, "image": data.get("imagePath") or "",
            "name": data.get("name") or ""}


def _region_for(regions: dict, display: dict) -> tuple | None:
    name = display.get("path") or display.get("name") or ""
    return regions.get(name)


def _display_geometry(display: dict, bone_world: tuple, region: tuple | None,
                      names: list, notes: list) -> tuple | None:
    """(world points, page-pixel uvs, weights) for one display.

    The bind basis is NOT returned: this format's `bonePose` states the same
    worlds the file's own bone transforms do, and the model's `godot_rest_worlds`
    already reconstructs those — a reader that also shipped them would make a
    writer depend on which format the rig came from.
    """
    kind = display.get("type") or "image"
    if kind == "image":
        if region is None:
            return None
        matrix = _local_matrix(display.get("transform"))
        world = multiply(bone_world, matrix)
        left, top, width, height = region
        # The corner order the model carries elsewhere (see in_spine): bottom
        # left, top left, top right, bottom right, each paired with its own UV.
        # A reader that walked the sprite's own order would be equally correct
        # and would still make a cross-format geometry comparison report a
        # deviation on every region — the vertices are the same four, permuted.
        corners = ((0.0, height), (0.0, 0.0), (width, 0.0), (width, height))
        points = [transform(world, corner) for corner in corners]
        uvs = [(left + corner[0], top + corner[1]) for corner in corners]
        return points, uvs, []
    if kind != "mesh":
        notes.append(f"dragonbones: display type {kind!r} has no geometry here")
        return None
    vertices = display.get("vertices") or []
    if not vertices:
        return None
    uvs_raw = display.get("uvs") or []
    left, top, width, height = region if region else (0.0, 0.0, 1.0, 1.0)
    slot_pose = _matrix(display.get("slotPose") or [1, 0, 0, 1, 0, 0])
    points = [transform(slot_pose, (vertices[i], vertices[i + 1]))
              for i in range(0, len(vertices) - 1, 2)]
    count = len(points)
    uvs = []
    for index in range(count):
        if (index * 2 + 1) < len(uvs_raw):
            uvs.append((left + _number(uvs_raw[index * 2]) * width,
                        top + _number(uvs_raw[index * 2 + 1]) * height))
        else:
            uvs.append((left, top))
    weights = []
    raw_weights = display.get("weights") or []
    if raw_weights:
        cursor = 0
        per_bone: dict = {}
        for vertex in range(count):
            if cursor >= len(raw_weights):
                break
            bone_count = int(_number(raw_weights[cursor]))
            cursor += 1
            for _ in range(bone_count):
                if cursor + 1 >= len(raw_weights):
                    break
                bone_index = int(_number(raw_weights[cursor]))
                weight = _number(raw_weights[cursor + 1])
                cursor += 2
                if 0 <= bone_index < len(names):
                    per_bone.setdefault(names[bone_index], [0.0] * count)[vertex] = weight
        weights = list(per_bone.items())
    return points, uvs, weights


def _local_matrix(raw: dict | None) -> tuple:
    """A display/bone `transform` object as a model matrix."""
    raw = raw or {}
    x = _number(raw.get("x"))
    y = _number(raw.get("y"))
    if "rotate" in raw or "skew" in raw:
        rotation = math.radians(_number(raw.get("rotate")))
        skew = math.radians(_number(raw.get("skew")))
    else:
        rotation = math.radians(_number(raw.get("skY")))
        skew = math.radians(_number(raw.get("skX"))) - rotation
    scale_x = _number(raw.get("scX"), 1.0)
    scale_y = _number(raw.get("scY"), 1.0)
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    cos_s, sin_s = math.cos(rotation + skew), math.sin(rotation + skew)
    return (cos_r * scale_x, -sin_s * scale_y,
            sin_r * scale_x, cos_s * scale_y, x, y)


def _triangles(display: dict, count: int) -> list:
    raw = display.get("triangles") or []
    groups = [[int(raw[i]), int(raw[i + 1]), int(raw[i + 2])]
              for i in range(0, len(raw) - 2, 3)]
    if groups:
        return groups
    return [[0, index, index + 1] for index in range(1, count - 1)]


def _curve_from_raw(raw, time0: float, time1: float, values0: tuple,
                    values1: tuple) -> tuple | None:
    """A DragonBones easing curve as the model's absolute-space control points.

    `_samplingEasingCurve` reads the array as a chain of cubic segments: three
    (x, y) pairs per segment, the segment starting where the previous ended,
    with the very first start at (0, 0) and the last end at (1, 1) — which is
    why a single eased segment is seven numbers. One curve eases every axis of
    the frame, so its time controls are shared and its value controls are
    scaled per axis; the model stores the controls in the key's own value
    space, so both are mapped back through the segment's endpoints.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    if len(raw) % 3 != 1:
        return None            # no (1, 1) terminator: not a curve this reader trusts
    span_t = time1 - time0
    if span_t <= 0.0:
        return None
    out = []
    for value0, value1 in zip(values0, values1):
        span_v = value1 - value0
        for pair in (0, 2):
            x = _number(raw[pair])
            y = _number(raw[pair + 1])
            out.extend([time0 + x * span_t,
                        value0 if span_v == 0.0 else value0 + y * span_v])
    return tuple(out)


def _frame_times(frames: list, fps: float, frame_count: int) -> list:
    """(time, duration) per frame; the last frame runs to the animation's end."""
    times = []
    position = 0
    for index, frame in enumerate(frames):
        duration = int(_number(frame.get("duration"), 1))
        if index == len(frames) - 1:
            # `_parseTimeline` clamps the LAST frame to what is left of the
            # animation, so a trailing `duration: 0` still covers the tail.
            duration = max(0, frame_count - position)
        times.append((position / fps, duration / fps))
        position += duration
    return times


def _tween_curve(frame: dict, time0: float, time1: float, values0: tuple,
                 values1: tuple, notes: list, where: str) -> object:
    """A frame's easing as the model stores it (None = linear, "stepped")."""
    if "curve" in frame:
        curve = _curve_from_raw(frame["curve"], time0, time1, values0, values1)
        if curve is not None:
            return curve
        notes.append(f"dragonbones: {where} has a curve this reader cannot "
                     "express — the segment is read as a straight line")
        return None
    easing = frame.get("tweenEasing", -2.0)
    easing = _number(easing, -2.0)
    if easing == -2.0:
        return "stepped"
    if easing == 0.0:
        return None
    notes.append(f"dragonbones: {where} uses the preset easing {easing:g} "
                 "(Quad in/out/in-out), which is not a cubic — the segment is "
                 "read as a straight line")
    return None


def read_skeleton(path: str, atlas_path: str | None = None, **_kwargs) -> Skeleton:
    """DragonBones `_ske.json` -> canonical model."""
    data, armature = load_dragonbones(path)
    model = Skeleton()
    notes = model.notes
    fps = _number(armature.get("frameRate") or data.get("frameRate"), DEFAULT_FPS)
    if fps <= 0.0:
        fps = DEFAULT_FPS
    model.frame_rate = fps
    if len(data.get("armature") or []) > 1:
        notes.append("dragonbones: the file carries "
                     f"{len(data['armature'])} armatures — the first one is read")
    atlas = read_texture_atlas(atlas_path or find_texture_atlas(path))
    regions = atlas.get("regions") or {}
    if atlas.get("image"):
        model.texture_path = str(Path(path).parent / atlas["image"])
    if not regions:
        notes.append("dragonbones: no texture atlas found — attachments are read "
                     "without UVs and the preview draws them untextured")

    raw_bones = armature.get("bone") or []
    names = [bone.get("name", "") for bone in raw_bones]
    for raw in _ordered_bones(raw_bones):
        transform_raw = raw.get("transform") or {}
        rotation = math.degrees(_local_rotation(transform_raw))
        skew = _skew_degrees(transform_raw)
        if abs(skew) > 1e-6:
            notes.append(f"dragonbones: bone {raw.get('name')!r} carries a "
                         f"{skew:.3f}° skew, which the model has no channel for")
        bone = Bone(
            name=raw.get("name", ""),
            parent=raw.get("parent") or None,
            position=(_number(transform_raw.get("x")),
                      _number(transform_raw.get("y"))),
            rotation_deg=rotation,
            scale=(_number(transform_raw.get("scX"), 1.0),
                   _number(transform_raw.get("scY"), 1.0)),
            length=_number(raw.get("length")),
        )
        bone.path = (f"{model.by_name[bone.parent].path}/{bone.name}"
                     if bone.parent in model.by_name else bone.name)
        model.bones.append(bone)
        model.by_name[bone.name] = bone
        for flag in ("inheritTranslation", "inheritRotation", "inheritScale",
                     "inheritReflection"):
            if raw.get(flag) is False:
                notes.append(f"dragonbones: bone {bone.name!r} sets {flag}=false, "
                             "which the canonical model does not carry")

    world = godot_world_transforms(model)

    slots = armature.get("slot") or []
    slot_bone = {slot.get("name", ""): (slot.get("parent") or "")
                 for slot in slots}
    setup_index = {slot.get("name", ""): int(_number(slot.get("displayIndex"), 0))
                   for slot in slots}
    for slot in slots:
        if slot.get("blendMode") not in (None, "normal"):
            notes.append(f"dragonbones: slot {slot.get('name')!r} blends with "
                         f"{slot.get('blendMode')!r}, which the model has no "
                         "channel for")
        if slot.get("color"):
            notes.append(f"dragonbones: slot {slot.get('name')!r} carries a "
                         "colour transform, which the model has no channel for")

    displays_by_slot: dict = {}
    for skin in armature.get("skin") or []:
        skin_name = skin.get("name") or "default"
        if skin_name != "default":
            notes.append(f"dragonbones: skin {skin_name!r} is not the default one "
                         "— only the default skin is read")
            continue
        for skin_slot in skin.get("slot") or []:
            name = skin_slot.get("name", "")
            displays_by_slot[name] = skin_slot.get("display") or []

    for slot in slots:
        slot_name = slot.get("name", "")
        host = slot_bone.get(slot_name) or ""
        if host not in world:
            notes.append(f"dragonbones: slot {slot_name!r} hangs from the missing "
                         f"bone {host!r} — skipped")
            continue
        host_world = world[host]
        for index, display in enumerate(displays_by_slot.get(slot_name) or []):
            if not display:
                continue
            region = _region_for(regions, display)
            if display.get("type") not in (None, "image", "mesh"):
                notes.append(f"dragonbones: skipped display "
                             f"{display.get('name')!r} of type "
                             f"{display.get('type')!r}")
                continue
            geometry = _display_geometry(display, host_world, region, names, notes)
            if geometry is None:
                notes.append(f"dragonbones: skipped display {display.get('name')!r} "
                             "(no geometry or no region)")
                continue
            points, uvs, weights = geometry
            totals = {}
            for bone_name, vertex_weights in weights:
                totals[bone_name] = totals.get(bone_name, 0.0) + sum(vertex_weights)
            anchor = max(totals, key=totals.get) if totals else host
            anchor_world = world.get(anchor, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
            if not weights:
                # Every attachment in this repo's model is bound: an unweighted
                # display follows its slot's bone rigidly, which is a weight of
                # 1 on that bone — the same thing the Godot leg writes.
                weights = [(host, [1.0] * len(points))]
            attachment = Attachment(
                name=display.get("name") or "",
                slot=slot_name,
                polygon=[(point[0] - anchor_world[4], point[1] - anchor_world[5])
                         for point in points],
                uv=[[u, v] for u, v in uvs],
                polygons=_triangles(display, len(points)),
                weights=weights,
                mesh=(display.get("type") == "mesh"),
                position=(anchor_world[4], anchor_world[5]),
                setup=(index == setup_index.get(slot_name, -1)),
                equipped=True,
            )
            model.attachments.append(attachment)

    by_slot: dict = {}
    for attachment in model.attachments:
        by_slot.setdefault(attachment.slot, {})[attachment.name] = attachment

    for animation in armature.get("animation") or []:
        name = animation.get("name") or "default"
        frame_count = int(_number(animation.get("duration")))
        tracks: dict = {}
        # A timeline value is an OFFSET from the bone's setup transform:
        # `Bone._updateGlobalTransformMatrix` composes a bone as
        # `origin + offset + animationPose` (and multiplies the scales), where
        # `origin` is the setup `Bone.init` stored. The model stores the
        # absolute local pose, so the setup is added back per channel here —
        # reading the file's own numbers would shift every animated bone by its
        # setup offset.
        def setup_of(bone_name: str):
            bone = model.by_name.get(bone_name)
            return ((bone.position if bone else (0.0, 0.0)),
                    (bone.rotation_deg if bone else 0.0),
                    (bone.scale if bone else (1.0, 1.0)))
        for timeline in animation.get("bone") or []:
            bone_name = timeline.get("name", "")
            props = tracks.setdefault(bone_name, {})
            if timeline.get("translateFrame"):
                frames = timeline["translateFrame"]
                times = _frame_times(frames, fps, frame_count)
                position, _rotation, _scale = setup_of(bone_name)
                keys = []
                for index, frame in enumerate(frames):
                    time, _ = times[index]
                    following = times[index + 1][0] if index + 1 < len(times) else time
                    values = (_number(frame.get("x")) + position[0],
                              _number(frame.get("y")) + position[1])
                    keys.append(Key(
                        time=time, x=values[0], y=values[1],
                        curve=_tween_curve(frame, time, following, values,
                                           _next_pair(frames, index),
                                           notes, f"{name}/{bone_name}/translate"),
                    ))
                props["translate"] = keys
            if timeline.get("rotateFrame"):
                frames = timeline["rotateFrame"]
                times = _frame_times(frames, fps, frame_count)
                _position, setup_rotation, _scale = setup_of(bone_name)
                keys = []
                for index, frame in enumerate(frames):
                    time, _ = times[index]
                    following = times[index + 1][0] if index + 1 < len(times) else time
                    angle = _number(frame.get("rotate")) + setup_rotation
                    if abs(_number(frame.get("skew"))) > 1e-6:
                        notes.append(f"dragonbones: {name}/{bone_name} animates a "
                                     "skew, which the model has no channel for")
                    values = (angle,)
                    following_angle = (_number(frames[index + 1].get("rotate"))
                                       if index + 1 < len(frames) else angle)
                    keys.append(Key(
                        time=time, angle=angle,
                        curve=_tween_curve(frame, time, following, values,
                                           (following_angle,),
                                           notes, f"{name}/{bone_name}/rotate"),
                    ))
                props["rotate"] = keys
            if timeline.get("scaleFrame"):
                frames = timeline["scaleFrame"]
                times = _frame_times(frames, fps, frame_count)
                _position, _rotation, setup_scale = setup_of(bone_name)
                keys = []
                for index, frame in enumerate(frames):
                    time, _ = times[index]
                    following = times[index + 1][0] if index + 1 < len(times) else time
                    values = (_number(frame.get("x"), 1.0) * setup_scale[0],
                              _number(frame.get("y"), 1.0) * setup_scale[1])
                    keys.append(Key(
                        time=time, scale=values,
                        curve=_tween_curve(frame, time, following, values,
                                           _next_pair(frames, index, 1.0),
                                           notes, f"{name}/{bone_name}/scale"),
                    ))
                props["scale"] = keys
        tracks = {bone_name: props for bone_name, props in tracks.items() if props}
        if tracks:
            model.animations[name] = tracks
        if frame_count > 0:
            model.animation_durations[name] = frame_count / fps

        slot_tracks: dict = {}
        for timeline in animation.get("slot") or []:
            slot_name = timeline.get("name", "")
            frames = timeline.get("displayFrame") or []
            if not frames:
                continue
            displays = displays_by_slot.get(slot_name) or []
            order = [display.get("name") if display else None for display in displays]
            times = _frame_times(frames, fps, frame_count)
            keys = []
            for index, frame in enumerate(frames):
                value = int(_number(frame.get("value", frame.get("displayIndex", 0))))
                wanted = order[value] if 0 <= value < len(order) else None
                keys.append({"time": times[index][0], "attachment": wanted})
            slot_tracks[slot_name] = keys
        if slot_tracks:
            model.slot_timelines[name] = slot_tracks

        for timeline in animation.get("ffd") or []:
            slot_name = timeline.get("slot", "")
            display_name = timeline.get("name", "")
            attachment = by_slot.get(slot_name, {}).get(display_name)
            if attachment is None:
                notes.append(f"dragonbones: {name} deforms {slot_name}/{display_name}, "
                             "which no display of this skin carries")
                continue
            frames = timeline.get("frame") or []
            times = _frame_times(frames, fps, frame_count)
            keys = []
            for index, frame in enumerate(frames):
                offset = int(_number(frame.get("offset")))
                raw = [_number(value) for value in (frame.get("vertices") or [])]
                delta = []
                for vertex in range(len(attachment.polygon)):
                    position = vertex * 2 - offset
                    delta.append((raw[position] if 0 <= position < len(raw) else 0.0,
                                  raw[position + 1] if 0 <= position + 1 < len(raw) else 0.0))
                keys.append({"time": times[index][0], "curve": None, "delta": delta})
            if keys:
                attachment.deform = attachment.deform or {}
                attachment.deform[name] = keys

        for key, label in (("ik", "IK constraints"), ("path", "path constraints"),
                           ("zOrder", "z-order timelines"), ("frame", "action frames")):
            if animation.get(key):
                notes.append(f"dragonbones: {name} carries {label}, which the "
                             "canonical model does not represent")

    for key, label in (("ik", "IK constraints"), ("path", "path constraints"),
                       ("defaultActions", "default actions")):
        if armature.get(key):
            notes.append(f"dragonbones: the armature carries {label}, which the "
                         "canonical model does not represent")
    return model


def _next_pair(frames: list, index: int, default: float = 0.0) -> tuple:
    if index + 1 < len(frames):
        following = frames[index + 1]
        return (_number(following.get("x"), default),
                _number(following.get("y"), default))
    return (default, default)


def _local_rotation(raw: dict) -> float:
    """Radians, following `_parseTransform`'s two accepted spellings."""
    if "rotate" in raw or "skew" in raw:
        return math.radians(_number(raw.get("rotate")))
    return math.radians(_number(raw.get("skY")))


def _skew_degrees(raw: dict) -> float:
    """The recovered skew: `skew`, or `skX - rotation` in the 4.x spelling."""
    if "rotate" in raw or "skew" in raw:
        return math.degrees(math.radians(_number(raw.get("skew"))))
    rotation = math.radians(_number(raw.get("skY")))
    return math.degrees(math.radians(_number(raw.get("skX"))) - rotation)