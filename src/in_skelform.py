"""SkelForm reader: a `.skf` bundle (or a bare `armature.json`) -> canonical model.

Format facts come from the project's own sources (checked 2026-09-28):

- Container: `.skf` is a ZIP holding `armature.json`, `editor.json`,
  `thumbnail.png`, `readme.md` and `atlasN.png`. Only `armature.json` and the
  atlas images matter here.
- Document: ``version``, ``baked_ik``, ``img_format``, ``clear_color``,
  ``bones[]``, ``animations[]``, ``atlases[]``, ``styles[]``,
  ``inverse_kinematics[]``, ``visuals[]``, ``physics[]``.
- Bone: ``id``, ``name``, ``parent_id`` (-1 for a root), ``pos{x,y}``,
  ``rot`` (**radians**), ``scale{x,y}``, ``init_*`` mirrors of the three,
  plus ``ik_family_id`` / ``physics_id`` / ``visuals_id``.
- Visual (one per bone at most): ``tex`` (a texture *name*, resolved through
  ``styles``), ``zindex``, ``tint``, and either nothing else (the whole
  texture rect is drawn) or a mesh: ``vertices[{id,pos,uv,init_pos}]``,
  ``indices`` (every 3 entries are one triangle) and ``binds``
  (per-vertex weights against bone ids, with an ``is_path`` flavour).
- Style: ``textures[{name, offset, size, atlas_idx}]`` — atlas rectangles in
  **pixels**; a visual's ``uv`` is normalized inside its own rectangle, so the
  page-space UV is ``offset + uv * size``. The runtime resolves a texture name
  by scanning styles in order, first match wins.
- Animation: ``name``, ``id``, ``fps``, ``keyframes[]``; one keyframe is
  ``{frame, bone_id, element, value, value_str?, start_handle{x,y},
  end_handle{x,y}, next_kf, handle_preset}``. ``element`` is one of
  ``PositionX``, ``PositionY``, ``Rotation``, ``ScaleX``, ``ScaleY``,
  ``Hidden``, ``Texture``, ``TintR/G/B/A``, ``IkConstraint``, ``MimicTarget``.
  Time is ``frame / fps``.
- Interpolation is a cubic bezier with p0 = 0 and p3 = 1 in *normalized*
  segment space, the handles belonging to the **next** keyframe, solved by
  Newton-Raphson on the time axis (ported verbatim in :func:`interp` below);
  the ``Snap`` preset (handles with y == 999) holds the start value, i.e. a
  stepped segment.

Space: SkelForm stores positions in a Y-up, CCW-positive space (the editor
negates Y when importing image coordinates, and its ``RotateVec2`` is the
standard CCW rotation), so this reader mirrors into the canonical Godot space
exactly like the Spine reader does: ``y -> -y`` and ``rot -> -degrees(rot)``.

Not carried by this reader (recorded in ``model.notes`` instead of silently
dropped): inverse kinematics, swarm/rope physics, tints, ``Texture`` swaps and
path binds. Bone ``hidden`` becomes an attachment-visibility timeline.
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

from .model import (Attachment, Bone, Key, Skeleton,
                    godot_world_transforms, transform)

# Frame rate used when an animation does not declare one.
DEFAULT_FPS = 60.0

SNAP_HANDLE_Y = 999.0


def _cubic_bezier(t: float, p1: float, p2: float) -> float:
    u = 1.0 - t
    return 3.0 * u * u * t * p1 + 3.0 * u * t * t * p2 + t * t * t


def _cubic_bezier_derivative(t: float, p1: float, p2: float) -> float:
    u = 1.0 - t
    return 3.0 * u * u * p1 + 6.0 * u * t * (p2 - p1) + 3.0 * t * t * (1.0 - p2)


def interp(current: float, maximum: float, start_val: float, end_val: float,
           start_handle: tuple, end_handle: tuple) -> float:
    """The runtime's ``interp()``, ported verbatim.

    ``current``/``maximum`` are frame offsets inside the segment; the handles
    are normalized (x = time fraction, y = value fraction).
    """
    if start_handle[1] == SNAP_HANDLE_Y and end_handle[1] == SNAP_HANDLE_Y:
        return start_val
    if maximum == 0 or current >= maximum:
        return end_val
    initial = current / maximum
    t = initial
    for _ in range(5):
        x = _cubic_bezier(t, start_handle[0], end_handle[0])
        dx = _cubic_bezier_derivative(t, start_handle[0], end_handle[0])
        if abs(dx) < 1e-5:
            break
        t -= (x - initial) / dx
        t = min(1.0, max(0.0, t))
    progress = _cubic_bezier(t, start_handle[1], end_handle[1])
    return start_val + (end_val - start_val) * progress


def handles_for(keyframe: dict) -> tuple:
    """The (start, end) normalized handles a segment ending at ``keyframe`` uses.

    The stored numbers are used verbatim: ``interp`` reads them directly, so a
    zeroed handle pair really is a cubic ease in the runtime (older exports
    leave ``Linear`` keyframes with zeros, and "fixing" them to a straight line
    here would silently disagree with every runtime playing the file). The
    preset table exists for the *writer*, which picks the preset matching the
    curve it emits.
    """
    start = keyframe.get("start_handle") or {}
    end = keyframe.get("end_handle") or {}
    return ((float(start.get("x", 0.0)), float(start.get("y", 0.0))),
            (float(end.get("x", 0.0)), float(end.get("y", 0.0))))


def curve_for(keyframe: dict, time0: float, time1: float,
              value0: float, value1: float):
    """One SkelForm segment -> the canonical key's ``curve``.

    The model stores bezier control points in **absolute time and value**
    (spine-space convention), while SkelForm normalizes them to the segment
    box, so this is a scale-and-offset, not a change of shape. ``Snap``
    segments become the model's ``"stepped"`` curve.
    """
    (h1x, h1y), (h2x, h2y) = handles_for(keyframe)
    if h1y == SNAP_HANDLE_Y and h2y == SNAP_HANDLE_Y:
        return "stepped"
    dt = time1 - time0
    dv = value1 - value0
    return (time0 + h1x * dt, value0 + h1y * dv,
            time0 + h2x * dt, value0 + h2y * dv)


def load_skelform(path: str) -> tuple:
    """Read a `.skf` or a bare `armature.json`; return (armature, images).

    ``images`` maps the atlas file names inside the bundle to their bytes, so a
    caller can write the pages next to a converted scene.
    """
    source = Path(path)
    if source.suffix.lower() == ".skf" or zipfile.is_zipfile(source):
        images = {}
        with zipfile.ZipFile(source) as bundle:
            armature = json.loads(bundle.read("armature.json"))
            for name in bundle.namelist():
                if name.lower().endswith((".png", ".jpg", ".jpeg")):
                    images[Path(name).name] = bundle.read(name)
        return armature, images
    return json.loads(source.read_text(encoding="utf-8")), {}


def extract_assets(path: str, out_dir: str) -> list:
    """Registry hook: write the bundle's atlas pages into ``out_dir``.

    A ``.skf`` carries its images inside the archive, so they are unpacked
    beside the output before a writer resolves the rig's texture.
    """
    armature, images = load_skelform(path)
    written = []
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for page in armature.get("atlases") or []:
        name = Path(page.get("filename", "")).name
        if not name or name not in images:
            continue
        target = destination / name
        target.write_bytes(images[name])
        written.append(str(target))
    return written


def _texture_rect(styles: list, tex_name: str) -> dict | None:
    """The atlas rectangle a texture name resolves to (styles scanned in order)."""
    for style in styles or []:
        for texture in style.get("textures") or []:
            if texture.get("name") == tex_name:
                return texture
    return None


def _rect_point(rect: dict, x: float, y: float) -> tuple:
    """A normalized point inside a texture -> atlas page pixels."""
    offset = rect.get("offset") or {}
    size = rect.get("size") or {}
    return (float(offset.get("x", 0.0)) + x * float(size.get("x", 0.0)),
            float(offset.get("y", 0.0)) + y * float(size.get("y", 0.0)))


def bone_name_map(armature: dict) -> dict:
    """Bone id -> the model-unique name.

    SkelForm lets two bones share a name (its own samples do: ``Right``,
    ``Left``, ``0``..``8``), while the canonical model keys bones by name. The
    repeats get a numeric suffix, and the caller reports the rename instead of
    silently merging two different bones into one.
    """
    mapping: dict = {}
    for bone in armature.get("bones") or []:
        original = bone["name"]
        candidate = original
        suffix = 2
        while candidate in mapping.values():
            candidate = f"{original}_{suffix}"
            suffix += 1
        mapping[bone["id"]] = candidate
    return mapping


def _ordered_bones(armature: dict) -> list:
    """Bones in parent-before-child order (the model's contract)."""
    bones = list(armature.get("bones") or [])
    by_id = {bone["id"]: bone for bone in bones}
    ordered: list = []
    seen: set = set()

    def visit(bone: dict) -> None:
        if bone["id"] in seen:
            return
        parent_id = bone.get("parent_id", -1)
        if parent_id != -1 and parent_id in by_id:
            visit(by_id[parent_id])
        seen.add(bone["id"])
        ordered.append(bone)

    for bone in bones:
        visit(bone)
    return ordered


def _pivot_shift(visual: dict, size: tuple) -> tuple:
    """The visual's pivot translation, in the owning bone's local space.

    ``Draw()`` adds ``rotate(pivot_pos * tex.size, bone.rot * left) *
    bone.scale`` in world space; dividing the bone transform back out leaves
    the plain local offset ``pivot_pos * size``.
    """
    pivot_pos = visual.get("pivot_pos") or {}
    return (float(pivot_pos.get("x", 0.0)) * size[0],
            float(pivot_pos.get("y", 0.0)) * size[1])


def _pivot_transform(visual: dict) -> tuple | None:
    """(rotation, scale) a visual applies to its vertices before the bone.

    ``inherit_vert`` scales by ``bone.scale * pivot_scale`` and rotates by
    ``bone.rot + pivot_rot``, so a pivot rotation only folds into the vertices
    when the bone's own scale is uniform: the two matrices do not commute
    otherwise.
    """
    scale = visual.get("pivot_scale") or {}
    rot = float(visual.get("pivot_rot", 0.0) or 0.0)
    pair = (float(scale.get("x", 1.0)), float(scale.get("y", 1.0)))
    if rot == 0.0 and pair == (1.0, 1.0):
        return None
    return rot, pair


def _apply_pivot(point: tuple, transform: tuple | None) -> tuple:
    """Fold a visual's pivot rotation/scale into one local vertex."""
    if transform is None:
        return point
    rot, (sx, sy) = transform
    x, y = point[0] * sx, point[1] * sy
    cos, sin = math.cos(rot), math.sin(rot)
    return (x * cos - y * sin, x * sin + y * cos)


def _attachment_from_visual(bone_name: str, visual: dict, rect: dict | None,
                            atlases: list, bone_names: dict,
                            bone_worlds: dict | None = None) -> tuple:
    """A bone's visual -> (attachment or None, had a path bind?).

    SkelForm keeps a visual's vertices in the OWNING BONE's local space: the
    runtime's ``constructVerts`` runs ``inheritVert(pos, bone, pivot_scale,
    pivot_rot)`` over every one. The model carries attachment points in world
    space up to the anchor bone's translation (see in_spine: it stores
    ``world - anchor_world.xy``), so the bone's transform is applied here and
    its translation taken back out — the exact inverse of what the writer does.
    """
    if rect is None:
        return None, False
    page = ""
    atlas_idx = int(rect.get("atlas_idx", 0) or 0)
    if 0 <= atlas_idx < len(atlases):
        page = Path(atlases[atlas_idx].get("filename", "")).name
    size = (float((rect.get("size") or {}).get("x", 0.0)),
            float((rect.get("size") or {}).get("y", 0.0)))
    vertices = visual.get("vertices") or []
    indices = visual.get("indices") or []
    path_binds = 0
    shift = _pivot_shift(visual, size)

    if vertices:
        # Mesh visual: vertices are bone-local (the runtime inherits them
        # through the binds; unbound ones inherit the owning bone), uv
        # normalized inside the texture rect.
        polygon = [((-1.0) ** 0 * float(v["pos"]["x"]), float(v["pos"]["y"]))
                   for v in vertices]
        uv = [_rect_point(rect, float(v["uv"]["x"]), float(v["uv"]["y"]))
              for v in vertices]
        vertex_index = {int(v["id"]): i for i, v in enumerate(vertices)
                        if "id" in v}
        weights = []
        for bind in visual.get("binds") or []:
            if bind.get("is_path"):
                # A path bind drags its vertices along a path instead of
                # weighting them; the runtime branches on `is_path`, and the
                # model has no path-bind concept.
                path_binds += 1
                continue
            owner = bone_names.get(int(bind.get("bone_id", -1)))
            if owner is None:
                continue
            per_vertex = [0.0] * len(polygon)
            for entry in bind.get("verts") or []:
                index = vertex_index.get(int(entry.get("id", -1)))
                if index is not None:
                    per_vertex[index] = float(entry.get("weight", 0.0))
            weights.append((owner, per_vertex))
        if not weights:
            # Unbound vertices inherit their owning bone directly
            # (`inherit_vert` applies the bone's transform to every vertex).
            weights = [(bone_name, [1.0] * len(polygon))]
        pivot = _pivot_transform(visual)
        # Pivot folds into the FILE-space vertex, then the point is mirrored
        # (y-up -> model frame) and pushed through the bind bones' blended
        # model world — the exact inverse of the writer's world->local
        # conversion, so the polygon lands back in the world space every other
        # reader stores. One mirror only: the writer's `(x, -y)` emit is this
        # reader's inverse, and mirroring again flips every y back.
        blended = []
        for index, point in enumerate(polygon):
            local = _apply_pivot(point, pivot)
            # Replay the runtime's bind blend over the stored point
            # (`constructVerts`), then mirror to the model's frame and take the
            # pivot/position offsets back out. The blend is SEQUENTIAL: the
            # point starts at the owning bone and each bind moves it a weighted
            # step, so every bind is scaled by the weights after it and the
            # owner by what is left over. This is the exact inverse of the
            # writer's `bind_state`; changing one without the other breaks the
            # round trip.
            def mirrored(world):
                # the file's bones live in the y-up frame; the model world is
                # y-down, so the file-space bone world is the mirror conjugate
                return (world[0], -world[1], -world[2], world[3], world[4],
                        -world[5])

            binds = []
            for owner_name, ws in weights:
                model_world = (bone_worlds or {}).get(owner_name)
                weight = ws[index] if index < len(ws) else 0.0
                if model_world is None or not weight:
                    continue
                binds.append((mirrored(model_world), float(weight)))
            terms = []
            tail = 1.0
            for world, weight in reversed(binds):
                terms.append((world, weight * tail))
                tail *= (1.0 - weight)
            owner_world = (bone_worlds or {}).get(bone_name)
            if owner_world is not None:
                terms.append((mirrored(owner_world), tail))

            A = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            b = [0.0, 0.0]
            if terms:
                A = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                for world, coefficient in terms:
                    for i in range(4):
                        A[i] += coefficient * world[i]
                    for i in range(2):
                        b[i] += coefficient * world[4 + i]
            world_file = transform(tuple(A), local)
            world_file = (world_file[0] + b[0], world_file[1] + b[1])
            blended.append((world_file[0] - shift[0], -world_file[1] - shift[1]))
        polygon = blended
        if indices and isinstance(indices[0], int):
            groups = [list(indices[i:i + 3]) for i in range(0, len(indices) - 2, 3)]
        else:
            groups = [list(group) for group in indices]
    else:
        # Region visual: the whole texture rect, centered on the bone (the
        # editor keeps a rect as +-size/2 — `utils::bone_meshes_edited`), with
        # the pivot rotation/scale folded in when it is expressible.
        half_x, half_y = size[0] / 2.0, size[1] / 2.0
        # SkelForm is Y-up: the rect corner that owns uv (0, 0) — the image's
        # top-left — is the one at +y.
        corners = [(-half_x, half_y), (half_x, half_y),
                   (half_x, -half_y), (-half_x, -half_y)]
        pivot = _pivot_transform(visual)
        polygon = [(x, -y) for x, y in
                   (_apply_pivot(corner, pivot) for corner in corners)]
        uv = [_rect_point(rect, 0.0, 0.0), _rect_point(rect, 1.0, 0.0),
              _rect_point(rect, 1.0, 1.0), _rect_point(rect, 0.0, 1.0)]
        groups = [[0, 1, 2], [0, 2, 3]]
        weights = [(bone_name, [1.0, 1.0, 1.0, 1.0])]

    attachment = Attachment(
        name=str(visual.get("tex") or bone_name),
        mesh=bool(vertices),
        slot=bone_name,
        polygon=polygon,
        uv=uv,
        polygons=groups,
        weights=weights,
        # The pivot translation is a plain local offset, which is exactly what
        # an attachment's node position is (mirrored into Godot space).
        position=(shift[0], -shift[1]),
        equipped=True,
        setup=True,
        page=page,
    )
    return attachment, path_binds > 0


def _element_keys(keyframes: list, bone_id: int, element: str,
                  fps: float) -> list:
    """One bone+element channel -> model keys (seconds, Godot-space values)."""
    channel = [kf for kf in keyframes
               if kf.get("bone_id") == bone_id and kf.get("element") == element]
    if not channel:
        return []
    channel.sort(key=lambda kf: kf.get("frame", 0))
    # A frame may be keyed more than once (the editor writes a closing key when
    # a segment restarts). The runtime's `get_prev_frame` takes the LAST key at
    # or before the frame, so the last one wins — and two keys at the same time
    # make a Spine bezier non-monotonic, which the runtime turns into NaN.
    collapsed = []
    for keyframe in channel:
        if collapsed and collapsed[-1].get("frame") == keyframe.get("frame"):
            collapsed[-1] = keyframe
            continue
        collapsed.append(keyframe)
    channel = collapsed
    keys = []
    for index, keyframe in enumerate(channel):
        time0 = float(keyframe.get("frame", 0)) / fps
        following = channel[index + 1] if index + 1 < len(channel) else None
        curve = None
        if following is not None:
            time1 = float(following.get("frame", 0)) / fps
            curve = curve_for(following, time0, time1,
                              float(keyframe.get("value", 0.0)),
                              float(following.get("value", 0.0)))
        keys.append((time0, float(keyframe.get("value", 0.0)), curve))
    return keys


def _axis_quad(curve, axis: int, time0: float, time1: float,
               value0: float, value1: float) -> tuple:
    """The 4 control numbers one axis of a segment uses.

    Multi-axis tracks follow the rig's layout (one quadruple per axis); a
    single-axis track holds just one quadruple. An axis without
    its own bezier contributes the straight-line controls, the same fallback
    ``in_godot`` uses when merging Godot's per-axis tracks.
    """
    if isinstance(curve, (list, tuple)) and len(curve) >= 8:
        quad = tuple(curve[axis * 4:(axis + 1) * 4])
        if any(quad):
            return quad
    elif isinstance(curve, (list, tuple)) and len(curve) >= 4 and axis == 0:
        return tuple(curve[:4])
    # A straight segment's control points sit a third of the way along both
    # axes (spine's Linear preset is the same shape); putting them on the
    # endpoint *values* instead would silently turn every fallback into an
    # ease-in-out curve.
    span, delta = time1 - time0, value1 - value0
    return (time0 + span / 3.0, value0 + delta / 3.0,
            time1 - span / 3.0, value1 - delta / 3.0)


def _curve_quad(curve, time0: float, time1: float,
                value0: float, value1: float) -> tuple:
    """The absolute control points of a single-axis segment (or ``None``)."""
    if curve == "stepped":
        return None
    if isinstance(curve, (list, tuple)) and len(curve) >= 4:
        return tuple(curve[:4])
    span, delta = time1 - time0, value1 - value0
    return (time0 + span / 3.0, value0 + delta / 3.0,
            time1 - span / 3.0, value1 - delta / 3.0)


def _sample(keys: list, time: float, axis: int = 0,
            default: float = 0.0) -> float:
    """Evaluate a channel at ``time`` (mirrors the runtime's segment search).

    A channel's curves are stored in the same space as its values (see
    :func:`_element_keys`), which is all the handle recovery needs.
    """
    if not keys:
        return default
    if time <= keys[0][0]:
        return keys[0][1]
    for index in range(len(keys) - 1):
        time0, value0, curve = keys[index]
        time1, value1, _ = keys[index + 1]
        if time <= time1:
            if curve == "stepped":
                return value0
            if dt := (time1 - time0):
                quad = _axis_quad(curve, axis, time0, time1, value0, value1)
                dv = value1 - value0
                start_handle = ((quad[0] - time0) / dt,
                                (quad[1] - value0) / dv if dv else 0.0)
                end_handle = ((quad[2] - time0) / dt,
                              (quad[3] - value0) / dv if dv else 0.0)
                return interp(time - time0, dt, value0, value1,
                              start_handle, end_handle)
            return value1
    return keys[-1][1]


def _merge_channels(primary: list, secondary: list,
                    default_primary: float = 0.0,
                    default_secondary: float = 0.0, axes: int = 2) -> list:
    """Two per-axis channels -> one canonical track keyed on the union of times.

    SkelForm animates ``PositionX`` and ``PositionY`` (and ``ScaleX`` /
    ``ScaleY``) as independent channels with independent key times, while the
    canonical model keeps one key list per track with both components. The
    union keeps every original key exact; the other axis is evaluated with the
    runtime's own interpolation between its keys.

    ``axes`` is the number of value axes the track has: two for translate and
    scale (one curve quadruple each), one for rotate — its curve is a single
    quadruple, and a second one would be read by a runtime as the wrong axis.
    """
    if not primary and not secondary:
        return []
    # An axis with no keys at all keeps its *setup* value; copying the other
    # axis's value into it would scale/translate it by whatever the animated
    # axis happens to do (`Root` animates ScaleY alone, and taking its 1.0367
    # as ScaleX too made every child's world matrix wrong).
    # Both single-axis paths still have to produce the model's two-axis curve
    # layout (one quadruple per axis): a lone quadruple makes a Spine reader
    # take the *second* axis's controls from beyond the array and the runtime
    # fills the bone with NaN. The axis without keys gets a straight quadruple
    # at its setup value.
    if not secondary:
        merged = []
        for index, (time, value, curve) in enumerate(primary):
            following = primary[index + 1] if index + 1 < len(primary) else None
            if following is None:
                merged.append((time, value, None, default_secondary))
                continue
            if curve == "stepped":
                merged.append((time, value, "stepped", default_secondary))
                continue
            first = (_curve_quad(curve, time, following[0], value, following[1])
                     if isinstance(curve, (list, tuple)) and len(curve) >= 4
                     else _curve_quad(None, time, following[0], value, following[1]))
            second = (_curve_quad(None, time, following[0], default_secondary,
                                  default_secondary) if axes == 2 else None)
            merged.append((time, value,
                           list(first) + list(second) if second else list(first),
                           default_secondary))
        return merged
    if not primary:
        merged = []
        for index, (time, value, curve) in enumerate(secondary):
            following = secondary[index + 1] if index + 1 < len(secondary) else None
            if following is None:
                merged.append((time, default_primary, None, value))
                continue
            if curve == "stepped":
                merged.append((time, default_primary, "stepped", value))
                continue
            first = _curve_quad(None, time, following[0], default_primary,
                                default_primary)
            second = (_curve_quad(curve, time, following[0], value, following[1])
                      if isinstance(curve, (list, tuple)) and len(curve) >= 4
                      else _curve_quad(None, time, following[0], value, following[1])) \
                if axes == 2 else None
            merged.append((time, default_primary,
                           list(first) + list(second) if second else list(first),
                           value))
        return merged
    if [t for t, _, _ in primary] == [t for t, _, _ in secondary]:
        # Same key times: both axes carry their own value and curve, so the
        # key keeps an axis quadruple per axis (the model's translate/scale
        # layout) — a single curve reused for both axes interpolates the
        # second axis with the first one's handles.
        merged = []
        for index, ((time, value, curve), (_, other, other_curve)) \
                in enumerate(zip(primary, secondary)):
            following = primary[index + 1] if index + 1 < len(primary) else None
            other_following = secondary[index + 1] \
                if index + 1 < len(secondary) else None
            if (following is None or other_following is None
                    or curve == "stepped" or other_curve == "stepped"):
                merged.append((time, value, None, other)
                              if curve == "stepped" or other_curve == "stepped"
                              else (time, value, curve, other))
                continue
            quad = _curve_quad(curve, time, following[0], value, following[1])
            other_quad = _curve_quad(other_curve, time, other_following[0],
                                     other, other_following[1])
            merged.append((time, value,
                           (list(quad) + list(other_quad)
                            if quad and other_quad else None), other))
        return merged
    times = sorted({t for t, _, _ in primary} | {t for t, _, _ in secondary})
    primary_by_time = {t: (v, c) for t, v, c in primary}
    merged = []
    for time in times:
        if time in primary_by_time:
            value, curve = primary_by_time[time]
        else:
            value = _sample(primary, time, default=default_primary)
            curve = None
        # A key inserted at a time only the other axis is keyed at: the segment
        # shape is no longer expressible once one axis is resampled, so it
        # becomes linear — the documented per-axis approximation. Emitting a
        # single-axis quadruple here would also break the two-axis curve layout
        # (and a Spine reader fills the bone with NaN when the second axis's
        # controls are missing).
        merged.append((time, value, None,
                       _sample(secondary, time, default=default_secondary)))
    return merged


def _map_axis(curve, axis: int, fn) -> tuple | None:
    """Apply a value-space mapping to one axis's control numbers."""
    if not isinstance(curve, (list, tuple)):
        return curve
    out = list(curve)
    if len(out) >= 8:
        base = axis * 4
        out[base + 1], out[base + 3] = fn(out[base + 1]), fn(out[base + 3])
    elif axis == 0:
        out[1], out[3] = fn(out[1]), fn(out[3])
    return tuple(out)


def _curve_to_key_space(curve, kind: str, axis: int,
                        mirror_value: bool) -> tuple | None:
    """A raw SkelForm curve -> the model's own value space.

    The rig keeps control points in the space of the key values they belong
    to, so the raw field values take the same transform the key values take:
    radians -> degrees and the Y-up mirror for the axes that carry one
    (``mirror_value`` is the file's handedness).
    """
    if not isinstance(curve, (list, tuple)):
        return curve
    if kind == "rotate":
        sign = -1.0 if mirror_value else 1.0
        return _map_axis(curve, 0,
                         lambda v: sign * math.degrees(v))
    if kind == "translate" and axis == 1 and mirror_value:
        return _map_axis(curve, 1, lambda v: -v)
    # x, scale: identical in both spaces (a magnitude has no direction).
    return curve


def _track_from_channels(primary: list, secondary: list, kind: str,
                         mirror_value: bool,
                         setup: tuple | None = None) -> list:
    """Merged channels -> model keys of the requested kind.

    ``setup`` is the rig's setup value for the channel, used as the default of
    an axis whose channel has no keys of its own.
    """
    setup_x, setup_y = (setup or (0.0, 0.0, 0.0))[:2]
    if kind == "scale":
        default_primary = default_secondary = 1.0
    else:
        default_primary, default_secondary = setup_x, setup_y
    merged = _merge_channels(primary, secondary, default_primary,
                             default_secondary,
                             axes=1 if kind == "rotate" else 2)
    keys = []
    for time, value, curve, other in merged:
        if kind == "rotate":
            angle = math.degrees(value)
            # The file stores a key as an OFFSET from the bone's own pose (the
            # runtime adds them); the model carries absolute values. Reading it
            # verbatim walked a round trip down by the setup angle every pass.
            angle = -angle if mirror_value else angle
            keys.append(Key(
                time=time,
                angle=angle,
                curve=_curve_to_key_space(curve, kind, 0, mirror_value)))
        elif kind == "translate":
            y = other if other is not None else 0.0
            mapped = _curve_to_key_space(curve, kind, 0, mirror_value)
            mapped = _curve_to_key_space(mapped, kind, 1, mirror_value)
            keys.append(Key(time=time, x=value,
                            y=-y if mirror_value else y, curve=mapped))
        else:  # scale: absolute magnitudes, identical in both spaces
            y = other if other is not None else value
            keys.append(Key(time=time, scale=(value, y), curve=curve))
    return keys


def read_skeleton(path: str, atlas_path: str | None = None, **_kwargs) -> Skeleton:
    """SkelForm `.skf`/`armature.json` -> canonical model."""
    armature, _images = load_skelform(path)
    model = Skeleton()
    # A .skf keeps its pages inside the archive: the bundle unpacks them and
    # points the model at the extracted file. A bare armature.json may sit
    # next to its images, so a sibling page is used when present.
    source = Path(path)
    if source.suffix.lower() != ".skf":
        for page in armature.get("atlases") or []:
            candidate = source.parent / Path(page.get("filename", "")).name
            if candidate.exists():
                model.texture_path = str(candidate)
                break

    styles = armature.get("styles") or []
    atlases = armature.get("atlases") or []
    bone_names = {bone["id"]: bone["name"] for bone in armature.get("bones") or []}

    unique_names = bone_name_map(armature)
    renamed = [f"{bone['name']} -> {unique_names[bone['id']]}"
               for bone in armature.get("bones") or []
               if unique_names[bone["id"]] != bone["name"]]

    for bone_data in _ordered_bones(armature):
        name = unique_names[bone_data["id"]]
        parent_id = bone_data.get("parent_id", -1)
        parent = unique_names.get(parent_id) if parent_id != -1 else None
        pos = bone_data.get("pos") or {}
        scale = bone_data.get("scale") or {}
        bone = Bone(
            name=name,
            parent=parent,
            # SkelForm is Y-up / CCW-positive; the model is Godot space.
            position=(float(pos.get("x", 0.0)), -float(pos.get("y", 0.0))),
            rotation_deg=-math.degrees(float(bone_data.get("rot", 0.0))),
            scale=(float(scale.get("x", 1.0)), float(scale.get("y", 1.0))),
        )
        bone.path = f"{model.by_name[parent].path}/{name}" if parent else name
        model.bones.append(bone)
        model.by_name[name] = bone

    # Attachments: one per bone that owns a visual. Draw order is the array
    # order in the emitted scene, so attachments are sorted by the visual's
    # zindex (stable) to reproduce SkelForm's stacking.
    # Bone setup worlds in model space: the visuals' local geometry is pushed
    # through these so the model keeps world-space attachments like every other
    # reader (see _attachment_from_visual).
    # Bone setup worlds (model space), the anchors the visuals hang from.
    bone_worlds = godot_world_transforms(model)

    renderables = []
    pivot_notes: set = set()
    path_binds: set = set()
    drawn_visuals = set()
    for bone_data in _ordered_bones(armature):
        visual_id = bone_data.get("visuals_id", -1)
        if visual_id == -1 or visual_id >= len(armature.get("visuals") or []):
            continue
        drawn_visuals.add(visual_id)
        visual = armature["visuals"][visual_id]
        rect = _texture_rect(styles, visual.get("tex", ""))
        if rect is None:
            model.notes.append(
                f"skelform: texture {visual.get('tex')!r} not found in any style")
            continue
        attachment, had_path_bind = _attachment_from_visual(
            unique_names[bone_data["id"]], visual, rect, atlases, unique_names,
            bone_worlds)
        if attachment is None:
            continue
        if had_path_bind:
            path_binds.add(bone_data["name"])
        # A pivot translation travels as the attachment's node position and a
        # pivot rotation/scale folds into its vertices, so only a pivot
        # *rotation* can be inexact: the runtime applies it after the bone's
        # own scale, and the two matrices commute only for a uniform scale.
        if float(visual.get("pivot_rot", 0.0) or 0.0) != 0.0:
            pivot_notes.add(bone_data["name"])
        if bone_data.get("hidden"):
            attachment.equipped = False
            attachment.setup = False
        renderables.append((int(visual.get("zindex", 0)), bone_data["id"], attachment))
    renderables.sort(key=lambda item: (item[0], item[1]))
    model.attachments = [item[2] for item in renderables]

    skipped_timelines = set()
    for animation in armature.get("animations") or []:
        name = animation.get("name") or str(animation.get("id", ""))
        fps = float(animation.get("fps") or DEFAULT_FPS)
        keyframes = animation.get("keyframes") or []
        tracks = {}
        bone_id_by_name = {b["name"]: b["id"] for b in armature.get("bones") or []}
        raw_by_id = {b["id"]: b for b in armature.get("bones") or []}
        for bone_data in model.bones:
            bone_id = bone_id_by_name.get(bone_data.name)
            if bone_id is None:
                continue
            rotate = _element_keys(keyframes, bone_id, "Rotation", fps)
            pos_x = _element_keys(keyframes, bone_id, "PositionX", fps)
            pos_y = _element_keys(keyframes, bone_id, "PositionY", fps)
            scale_x = _element_keys(keyframes, bone_id, "ScaleX", fps)
            scale_y = _element_keys(keyframes, bone_id, "ScaleY", fps)
            raw = raw_by_id.get(bone_id) or {}
            init_pos = raw.get("init_pos") or {}
            setup = (float(init_pos.get("x", 0.0)), float(init_pos.get("y", 0.0)),
                     float(raw.get("init_rot", 0.0) or 0.0))
            if rotate:
                tracks.setdefault(bone_data.name, {})["rotate"] = _track_from_channels(
                    rotate, None, "rotate", True, setup)
            if pos_x or pos_y:
                tracks.setdefault(bone_data.name, {})["translate"] = _track_from_channels(
                    pos_x, pos_y, "translate", True, setup)
            if scale_x or scale_y:
                tracks.setdefault(bone_data.name, {})["scale"] = _track_from_channels(
                    scale_x, scale_y, "scale", False, setup)
        if tracks:
            model.animations[name] = tracks

        # Bone visibility -> attachment visibility timeline (the model's
        # slot timelines: None hides the slot).
        attachment_by_slot = {att.slot: att.name for att in model.attachments}
        hidden_keys = {}
        for keyframe in keyframes:
            element = keyframe.get("element")
            if element in ("Texture", "TintR", "TintG", "TintB", "TintA",
                           "IkConstraint", "MimicTarget"):
                skipped_timelines.add(element)
                continue
            if element != "Hidden":
                continue
            slot = unique_names.get(keyframe.get("bone_id"))
            if slot not in attachment_by_slot:
                continue
            hidden_keys.setdefault(slot, []).append(
                (float(keyframe.get("frame", 0)) / fps,
                 float(keyframe.get("value", 0.0)) == 1.0))
        if hidden_keys:
            model.slot_timelines[name] = {
                slot: [{"time": time,
                        "attachment": None if hidden else attachment_by_slot[slot]}
                       for time, hidden in sorted(entries)]
                for slot, entries in hidden_keys.items()
            }

    if renamed:
        model.notes.append(
            "skelform: duplicate bone names disambiguated: " + ", ".join(renamed[:6])
            + (f" (+{len(renamed) - 6} more)" if len(renamed) > 6 else ""))
    if path_binds:
        model.notes.append(
            "skelform: path binds not converted on: "
            + ", ".join(sorted(path_binds)))
    if pivot_notes:
        model.notes.append(
            "skelform: pivot rotation folded into vertex positions (exact only "
            "while the bone's scale stays uniform) on: "
            + ", ".join(sorted(pivot_notes)))

    if armature.get("inverse_kinematics"):
        model.notes.append(
            f"skelform: {len(armature['inverse_kinematics'])} inverse-kinematics "
            "families not converted (the runtime solves them per frame)")
    if armature.get("physics"):
        model.notes.append(
            f"skelform: {len(armature['physics'])} physics entries not converted "
            "(runtime simulation: sway/bounce/damping)")
    if skipped_timelines:
        model.notes.append(
            "skelform: timelines not converted: "
            + ", ".join(sorted(skipped_timelines)))
    unused_visuals = len(armature.get("visuals") or []) - len(drawn_visuals)
    if unused_visuals:
        # SkelForm draws one visual per bone (`visuals_id`); any other visual
        # in the file is an alternative the editor keeps around, not a rig
        # attachment — a rig that carries per-slot alternatives read from the
        # other formats cannot express them here.
        model.notes.append(
            f"skelform: {unused_visuals} visual(s) in the file are not drawn "
            "by any bone (one visual per bone) and are not converted")
    return model
