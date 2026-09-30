"""SkelForm writer: canonical model -> `armature.json` (or a `.skf` bundle).

The inverse of :mod:`in_skelform`: see that module's docstring for the format
facts and their sources. Conventions that matter here:

- SkelForm is Y-up / CCW-positive, so bone positions are mirrored back
  (``y -> -y``) and rotations converted to radians with the sign flipped.
- Animation values are **absolute** field targets keyed on integer frames
  (``frame = time * fps``), each ``element`` (``PositionX``, ``PositionY``,
  ``Rotation``, ``ScaleX``, ``ScaleY``, ``Hidden``) carrying its own keyframe
  list; ``next_kf`` links each key to the next key of the same bone+element.
- A segment's curve lives on the **next** keyframe as two normalized handles
  (``start_handle`` / ``end_handle``), while the model stores absolute bezier
  control points, so the conversion is a scale-and-offset. Stepped segments
  become the ``Snap`` preset (handles with y == 999).
- A visual is either a mesh (``vertices`` + ``indices`` + ``binds``) or a
  texture rect (no vertices at all, drawn centered on its bone). A four-vertex
  quad whose UVs form a rectangle is written as the latter, since that is what
  it is.

SkelForm's runtime-only concepts (inverse kinematics, physics, tints, texture
swaps, path binds) have no canonical representation yet: they are reported
through ``model.notes`` when a reader saw them, and this writer emits nothing
for them.
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

from .png import read_png_size
from . import png
from .model import Skeleton, godot_world_transforms, invert, transform

DEFAULT_FPS = 60.0
SNAP_HANDLE = {"x": 999.0, "y": 999.0}
LINEAR_HANDLES = ((1.0 / 3.0, 1.0 / 3.0), (2.0 / 3.0, 2.0 / 3.0))
READ_ME = (
    "Exported by skeleton-converter (https://github.com/JonathanReisCom/"
    "skeleton-converter).\n\n"
    "armature.json is the rig; see https://skelform.org/dev-docs/ for the\n"
    "runtime contract.\n"
)


def _vec2(x: float, y: float) -> dict:
    return {"x": round(float(x), 6), "y": round(float(y), 6)}


# Which quadruple of a two-axis curve belongs to each element: SkelForm
# animates one axis per element, so a translate/scale track's second quadruple
# must go to the second element.
_ELEMENT_AXIS = {"PositionX": 0, "PositionY": 1, "ScaleX": 0, "ScaleY": 1}



def _vec2i(x: float, y: float) -> dict:
    """A Vec2 the editor types as integers.

    `Texture.offset`/`size` and `TexAtlas.size` are `Vec2i`-style fields in the
    editor's own model: writing 1165.0 where it expects an i32 makes
    `serde_json::from_value::<Root>()` fail, and the app unwraps that error —
    the whole file is rejected with "invalid type: floating point `1165.0`,
    expected i32". The runtime divides these by the atlas size, so whole
    numbers lose nothing.
    """
    return {"x": int(round(x)), "y": int(round(y))}

def _field_map(element: str):
    """The rig's key value -> SkelForm's raw field value for one element."""
    if element == "Rotation":
        # SkelForm stores radians, with the screen's Y-up handedness.
        return lambda value: -math.radians(value)
    if element == "PositionY":
        return lambda value: -value
    return lambda value: value

def _curve_to_field(curve, element: str):
    """The element's quadruple, in SkelForm's own value space.

    The rig keeps control points in the key's value space and carries one
    quadruple per value axis; an element animates a single axis, so its own
    quadruple is selected first and its value components then take the same
    transform the key values take.
    """
    if not isinstance(curve, (list, tuple)):
        return curve
    axis = _ELEMENT_AXIS.get(element)
    if axis is not None:
        if len(curve) < axis * 4 + 4:
            return None
        curve = curve[axis * 4:(axis + 1) * 4]
    value_map = _field_map(element)
    return tuple(value if index % 2 == 0 else value_map(value)
                 for index, value in enumerate(curve))


def _handles_from_curve(curve, time0: float, time1: float,
                        value0: float, value1: float) -> tuple:
    """A canonical segment curve -> SkelForm's normalized handles."""
    if curve == "stepped":
        return SNAP_HANDLE, SNAP_HANDLE
    if isinstance(curve, (list, tuple)) and len(curve) >= 4:
        dt = time1 - time0
        dv = value1 - value0
        if dt:
            start = ((curve[0] - time0) / dt,
                     (curve[1] - value0) / dv if dv else 0.0)
            end = ((curve[2] - time0) / dt,
                   (curve[3] - value0) / dv if dv else 0.0)
            return (_vec2(*start), _vec2(*end))
    return (_vec2(*LINEAR_HANDLES[0]), _vec2(*LINEAR_HANDLES[1]))


def _preset_for(curve, time0: float, time1: float,
                value0: float, value1: float) -> str:
    if curve == "stepped":
        return "Snap"
    if isinstance(curve, (list, tuple)):
        start, end = _handles_from_curve(curve, time0, time1, value0, value1)
        for name, (preset_start, preset_end) in (
                ("Linear", LINEAR_HANDLES),
                ("SineIn", ((0.5, 0.0), (1.0, 1.0))),
                ("SineOut", ((1.0 / 3.0, 1.0), (2.0 / 3.0, 1.0))),
                ("SineInOut", ((0.5, 0.0), (0.5, 1.0)))):
            if (abs(start["x"] - preset_start[0]) < 1e-6
                    and abs(start["y"] - preset_start[1]) < 1e-6
                    and abs(end["x"] - preset_end[0]) < 1e-6
                    and abs(end["y"] - preset_end[1]) < 1e-6):
                return name
        return "Custom"
    return "Linear"


def _is_region(attachment) -> bool:
    """A four-vertex quad with rectangular UVs is SkelForm's texture rect.

    Only when it is not actually skinned: SkelForm draws a rect centered on its
    bone, ignoring weights, so a quad bound to several bones (or with partial
    weights) must stay a mesh.
    """
    if len(attachment.polygon) != 4 or len(attachment.uv) != 4:
        return False
    bindings = attachment.weights or []
    owner = attachment.slot or attachment.name
    if len(bindings) > 1:
        return False
    if bindings and (bindings[0][0] != owner
                     or any(abs(w - 1.0) > 1e-6 for w in bindings[0][1])):
        return False
    xs = sorted({round(u, 6) for u, _ in attachment.uv})
    ys = sorted({round(v, 6) for _, v in attachment.uv})
    if len(xs) != 2 or len(ys) != 2:
        return False
    width = xs[1] - xs[0]
    height = ys[1] - ys[0]
    if width <= 0 or height <= 0:
        return False
    # A region the atlas packed rotated has its uv span swapped against the
    # quad it describes (Spine's `rotate:`). Both orders describe the same
    # rect, and the runtime draws a region from the style's rect anyway — so
    # insisting on one order turned every rotated region into a baked mesh
    # carrying the quad's ROTATED orientation, which renders as a stretched
    # strip (the editor's own files keep those visuals geometry-free).
    def close(a: float, b: float) -> bool:
        # The quad is built from cosines, so its half-extents land a hair off
        # the uv span: compare with a tolerance scaled to the numbers.
        return abs(abs(a) - b) <= max(1e-3, abs(b) * 1e-4)

    def fits(a: float, b: float) -> bool:
        return ((close(a, width / 2.0) and close(b, height / 2.0))
                or (close(a, height / 2.0) and close(b, width / 2.0)))
    return all(fits(p[0], p[1]) for p in attachment.polygon)


def _textures_and_atlases(model) -> tuple:
    """Attachment UVs -> SkelForm styles + atlas list."""
    textures = []
    atlases = []
    for attachment in model.attachments:
        uv = attachment.uv or []
        if not uv:
            # Keep the list ALIGNED with model.attachments: callers index it
            # by the attachment's index, and skipping entries shifted every
            # texture onto the wrong attachment (the hero's meshes sampled
            # another sprite's rect).
            textures.append(None)
            continue
        xs = [u for u, _ in uv]
        ys = [v for _, v in uv]
        offset = (min(xs), min(ys))
        size = (max(xs) - offset[0], max(ys) - offset[1])
        page = attachment.page or (model.texture_path.rsplit("/", 1)[-1]
                                   or "atlas0.png")
        if page not in atlases:
            atlases.append(page)
        textures.append({
            "name": attachment.name,
            "offset": _vec2i(offset[0], offset[1]),
            "size": _vec2i(size[0], size[1]),
            "atlas_idx": atlases.index(page),
        })
    return textures, atlases


def _outranks(current, candidate) -> bool:
    """Whether ``candidate`` should take the bone from ``current``.

    Two attachments can land on the same bone — a slot whose name is not a
    bone falls back to the bone it is weighted to, and an effect (a burst, a
    splat) is often weighted to the very bone it must not replace. The rig
    keeps one visual per bone, so the tie goes to the attachment whose slot IS
    the bone (the natural slot-to-bone mapping), then to the equipped one.
    """
    if (candidate.name == candidate.slot) != (current.name == current.slot):
        return candidate.name == candidate.slot
    if candidate.setup != current.setup:
        return candidate.setup
    if candidate.equipped != current.equipped:
        return candidate.equipped
    return False


def _dominant_bone(attachment, id_by_name: dict) -> str | None:
    """The bone an attachment hangs off, by summed vertex weight.

    ``None`` when it has no weighted bone this rig knows — a path bind, or a
    rig whose weights name bones that were renamed.
    """
    totals: dict = {}
    for bone_name, weights in attachment.weights or []:
        if bone_name in id_by_name:
            totals[bone_name] = totals.get(bone_name, 0.0) + sum(weights)
    if not totals:
        return None
    return max(totals, key=totals.get)


def _visual_for(attachment, rect: dict | None, id_by_name: dict,
                bone_worlds=None, source_rect=None) -> dict:
    """One attachment -> one SkelForm visual."""
    # Every visual carries the whole field set, including the pivot and the
    # init* mirrors the editor writes: SkelForm's own players read
    # ``visual.pivot_pos.x`` while drawing (and a region visual has no
    # geometry to hint that it needs one). A region without the pivots made the
    # web player throw inside its draw loop — the canvas then stops at
    # whatever had been drawn and the rig looks empty.
    visual = {
        "tex": attachment.name,
        "init_tex": attachment.name,
        "zindex": 0,
        "init_zindex": 0,
        "pivot_pos": _vec2(0.0, 0.0),
        "pivot_rot": 0.0,
        "pivot_scale": _vec2(1.0, 1.0),
    }
    if rect is None:
        return visual
    if not attachment.mesh and _is_region(attachment):
        # A texture rect carries no geometry of its own: the runtime draws it
        # from the style, centred on the bone. Its offset travels as
        # `pivot_pos * tex.size` (`SkfDraw`/`bone_vertices`), and the model
        # keeps that offset as the attachment's `position` — the exact inverse
        # of the reader's `_pivot_shift`. Dropping it leaves every region
        # centred on its joint instead of where it belongs.
        # A region is drawn as a rect centred on the bone plus
        # `rotate(pivot_pos * tex.size, bone.rot * left) * bone.scale` — that is
        # where the attachment's own offset lives (the editor's renderer and the
        # web player's `final_pivot`/`SkfDraw` both read it; only the player's
        # *region* branch ignores it). The model keeps the offset as
        # `position`, so put it back as the fraction of the texture rect the
        # format expects. Leaving it at 0 parks every sprite on its joint,
        # which reads as "attached to the wrong bone".
        tex_w = float((rect.get("size") or {}).get("x", 0.0))
        tex_h = float((rect.get("size") or {}).get("y", 0.0))
        if tex_w and tex_h:
            visual["pivot_pos"] = _vec2(attachment.position[0] / tex_w,
                                        -attachment.position[1] / tex_h)
        return visual
    offset = (rect["offset"]["x"], rect["offset"]["y"])
    size = (rect["size"]["x"], rect["size"]["y"])
    # Mesh vertices already include the attachment offset in their target
    # world before the bind inverse. `pivot_pos` would add that offset again;
    # it belongs only to geometry-free region visuals.
    vertices = []

    owner_name = attachment.slot or attachment.name
    if owner_name not in id_by_name:
        owner_name = _dominant_bone(attachment, id_by_name)

    def bind_state(index: int):
        """The affine map the runtime's SEQUENTIAL bind blend applies.

        `constructVerts` starts the point at the OWNING bone's transform and
        then moves it a weighted step towards each bind, in order:

            p0  = F_own · init
            p_i = (1 - w_i) · p_(i-1) + w_i · F_i · init

        Expanded, each bind contributes with a coefficient of ``w_i`` scaled by
        the weights that come AFTER it, and the owner gets the product of all
        the remaining weights — it is not a plain weighted average of the
        matrices. The reverse-order average this used to be is exact for a
        single bind (w = 1 cancels every other term) and wrong for every
        multi-bind mesh: the hero's head and cape sat 43 and 69 units off at
        setup, and the viewer framed the rig around them. Returns (A, b) with
        p_out = A·p_init + b.
        """
        worlds = attachment.bind_worlds or bone_worlds or {}

        def frame(world):
            # bind_worlds come from the Spine reader in SPINE space (y-up),
            # which is the same frame the SkelForm file draws in: use them raw.
            # The godot fallback lives in the model's y-down frame and needs
            # the mirror conjugation.
            if attachment.bind_worlds:
                return tuple(world)
            return (world[0], -world[1], -world[2], world[3], world[4],
                    -world[5])

        binds = []
        for bone_name, ws in attachment.weights or []:
            world = worlds.get(bone_name)
            weight = ws[index] if index < len(ws) else 0.0
            if world is None or not weight:
                continue
            binds.append((frame(world), float(weight)))

        terms = []
        tail = 1.0
        for world, weight in reversed(binds):
            terms.append((world, weight * tail))
            tail *= (1.0 - weight)
        owner_world = worlds.get(owner_name)
        if owner_world is not None:
            terms.append((frame(owner_world), tail))
        if not terms:
            return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (0.0, 0.0)

        # A carries the linear part only (its own translation stays 0) and b
        # the translation, which is the split `local_point` inverts.
        A = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        b = [0.0, 0.0]
        for world, coefficient in terms:
            for i in range(4):
                A[i] += coefficient * world[i]
            for i in range(2):
                b[i] += coefficient * world[4 + i]
        return tuple(A), tuple(b)

    mirror = (1.0, 0.0, 0.0, -1.0, 0.0, 0.0)

    def local_point(index: int):
        # polygon + position = world (the model's invariant), in MODEL space.
        # The runtime's sequential blend maps the stored file point to
        # A·p + b; solving p = A^-1·(mirror·world - b) states the point whose
        # draw lands back on that world.
        A, b = bind_state(index)
        world_m = transform(mirror, (attachment.polygon[index][0] + attachment.position[0],
                                     attachment.polygon[index][1] + attachment.position[1]))
        return transform(invert(A), (world_m[0] - b[0], world_m[1] - b[1]))

    locals_ = [local_point(i) for i in range(len(attachment.polygon))]
    for index, point in enumerate(attachment.polygon):
        point = locals_[index]
        u, v = attachment.uv[index] if index < len(attachment.uv) else (0.0, 0.0)
        # The uv comes from the source's own corner positions, normalised to
        # the rect the reader measured them against. It is exact whatever the
        # quad's frame in this file is (a mesh quad written in a rotated bone's
        # frame is not axis-aligned here), which is why it is never recomputed
        # from the written geometry.
        base = source_rect or (offset[0], offset[1], size[0], size[1])
        a = (u - base[0]) / base[2] if base[2] else 0.0
        b = (v - base[1]) / base[3] if base[3] else 0.0
        if attachment.uv_rotation == 90:
            # `rotate: 90` means the page packed this sprite a quarter turn off,
            # and the rewrite turned the PIXELS back upright
            # (`png.rotate_quarter(clockwise=False)` maps a source pixel
            # (column, row) to (row, W-1-column)). A point at (a, b) in the
            # packed rect therefore lands at (b, 1 - a) of the sprite's own
            # rect, so the vertex that sampled (a, b) now samples (b, 1 - a).
            # Verified against the rewritten page pixel by pixel: the transform
            # is a pure quarter turn with no mirroring.
            u_norm, v_norm = b, 1.0 - a
        else:
            # `rotate: 270` (and 180) are left alone: the runtime's region path
            # only compensates 90 (`RegionAttachment.updateRegion`), and the
            # reader maps everything else as unrotated, so pixels and uv agree
            # by staying put. ponytail: add cases here if a sample ever packs
            # one and the reader learns to encode it.
            u_norm, v_norm = a, b
        vertices.append({
            "id": index,
            "pos": _vec2(point[0], point[1]),
            "uv": _vec2(u_norm, v_norm),
            "init_pos": _vec2(point[0], point[1]),
        })
    visual["vertices"] = vertices
    # SkelForm stores one flat triangle list per visual.
    visual["indices"] = [int(index) for group in attachment.polygons or []
                         for index in group]
    binds = []
    # The bind comes from the attachment's own weights. These meshes carry
    # their helper bones crossed on purpose (`L_Hand`'s mesh weights R_Hand),
    # and the Spine runtime draws them exactly that way — rebinding each visual
    # to the bone sharing its slot name put the whole rig a quarter turn over.
    for bone_name, weights in attachment.weights or []:
        bone_id = id_by_name.get(bone_name, -1)
        entries = [{"id": index, "weight": round(float(weight), 6)}
                   for index, weight in enumerate(weights) if weight]
        if entries and bone_id != -1:
            binds.append({"bone_id": bone_id, "is_path": False, "verts": entries})
    visual["binds"] = binds
    return visual


def _shelf_pack(sprites: list, minimum_width: int) -> tuple:
    """Lay sprites out in rows. Returns the page size and the new boxes.

    Deliberately dumb: rotating a packed sprite changes its aspect ratio, so
    the old rectangles cannot be reused, and a real packer (the editor vendors
    one) is a lot of machinery to place a handful of quads. Rows waste some
    page, which is free here — the page travels inside the archive either way.
    """
    width = max([minimum_width] + [w for _, w, _ in sprites])
    height = 0
    x = y = row_height = 0
    boxes = []
    for index, w, h in sprites:
        if x + w > width and x > 0:
            y += row_height
            x = 0
            row_height = 0
        boxes.append((index, x, y, w, h))
        x += w
        row_height = max(row_height, h)
    height = y + row_height
    return width, max(height, 1), boxes


def _single_upright_page(model, textures: list, page_paths: list) -> tuple:
    """Rewrite every sprite into ONE page, each upright.

    Two reasons, both about what the players do with pages:

    * Spine's atlas packs a region with `rotate: 90` and its runtime spins the
      sprite back while sampling. SkelForm's runtimes have no such flag — they
      walk a rectangle linearly — so a sprite packed that way draws on its side.
      The pixels move instead: each sprite is cropped out upright.
    * `SkfDraw` batches by page and, when the page changes, flushes the batch
      with the NEW page's texture (`atlases[tex.atlas_idx]` where `tex` is the
      incoming one) — so every piece gathered before a switch is drawn with the
      wrong sheet. One page removes the switch entirely.

    Returns the page bytes, the source rectangles the uv were measured against,
    and the new page size. Nothing is touched when there is a single page and
    no rotated region: a plain atlas ships byte for byte.
    """
    rotated = {index for index, attachment in enumerate(model.attachments)
               if getattr(attachment, "uv_rotation", 0) == 90}
    if not rotated and len(page_paths) <= 1:
        return None, {}, {}

    decoded = {}
    sprites = []
    sources = {}
    for index, texture in enumerate(textures):
        if texture is None:
            continue
        # Pages match by ORDER, the same way the bundle writes them: the model's
        # page name is the source file's, but a hand-written armature may name
        # it anything.
        slot = texture["atlas_idx"]
        source = page_paths[slot] if 0 <= slot < len(page_paths) else None
        if source is None or not Path(source).exists():
            # No pixels reachable for this entry. Mark it and leave it OUT of
            # the style below: `atlas_idx` still names a page the rewritten
            # bundle no longer writes, and the runtime reads
            # `atlases[tex.atlas_idx].texture` — an undefined atlas there is a
            # TypeError that kills the whole pane, not just the piece. A
            # skipped visual is what a missing page should cost.
            texture["missing"] = True
            continue
        if slot not in decoded:
            try:
                decoded[slot] = png.read_png(Path(source).read_bytes())
            except Exception:
                texture["missing"] = True
                continue
        page_w, page_h, rgba = decoded[slot]
        box = (int(texture["offset"]["x"]), int(texture["offset"]["y"]),
               int(texture["size"]["x"]), int(texture["size"]["y"]))
        piece = png.crop(rgba, page_w, box)
        piece_w, piece_h = box[2], box[3]
        # The model's uv live in the SOURCE page, so the rect they were
        # measured against has to survive the rewrite.
        sources[index] = (box[0], box[1], box[2], box[3])
        if index in rotated:
            # `rotate: 90` means the packer turned the sprite a quarter
            # clockwise, so a counter-clockwise turn puts it back.
            # rotate_quarter returns (width, height, pixels) — the dimensions
            # swap, and reading them in the other order blits rows of one
            # sprite over a buffer sized for another.
            piece_w, piece_h, piece = png.rotate_quarter(
                piece, piece_w, piece_h, clockwise=False)
        sprites.append((index, piece_w, piece_h, piece))

    minimum = max([size[0] for size in decoded.values()] or [1])
    width, height, boxes = _shelf_pack(
        [(index, w, h) for index, w, h, _ in sprites], minimum)
    packed = bytearray(width * height * 4)
    for (index, x, y, w, h), (_, _, _, piece) in zip(boxes, sprites):
        for row in range(h):
            target = ((y + row) * width + x) * 4
            packed[target:target + w * 4] = piece[row * w * 4:(row + 1) * w * 4]
        textures[index]["offset"] = _vec2i(x, y)
        textures[index]["size"] = _vec2i(w, h)
        textures[index]["atlas_idx"] = 0
    return png.write_png(width, height, bytes(packed)), sources, (width, height)


def _build_armature(model, fps: float, atlas_sizes: dict,
                    page_names: dict | None = None,
                    textures: list | None = None,
                    sources: dict | None = None,
                    page_sizes: dict | None = None,
                    page_list: list | None = None) -> dict:
    names = [bone.name for bone in model.bones]
    id_by_name = {name: index for index, name in enumerate(names)}
    bones = []
    for index, bone in enumerate(model.bones):
        parent_id = id_by_name.get(bone.parent, -1) if bone.parent else -1
        # The solved setup local when the rig has one: SkelForm has no
        # constraints, so the source's constraint-solved setup pose is what a
        # static frame must show (see model.Bone.setup_solved).
        solved = getattr(bone, "setup_solved", None)
        source_position, source_rotation, source_scale = (
            (solved[0], solved[1], solved[2]) if solved
            else (bone.position, bone.rotation_deg, bone.scale))
        position = (source_position[0], -source_position[1])
        rotation = -math.radians(source_rotation)
        bones.append({
            "id": index,
            "name": bone.name,
            "parent_id": parent_id,
            "pos": _vec2(*position),
            "scale": _vec2(*source_scale),
            "rot": round(rotation, 9),
            "init_pos": _vec2(*position),
            "init_rot": round(rotation, 9),
            "init_scale": _vec2(*bone.scale),
            "ik_family_id": -1,
            "physics_id": -1,
            "visuals_id": -1,
        })

    page_names = page_names or {}
    if textures is None:
        textures, pages = _textures_and_atlases(model)
    else:
        # The caller already rewrote the textures (and may have collapsed every
        # page into one), so its list wins over the model's.
        pages = page_list if page_list is not None else _textures_and_atlases(model)[1]
    # Owning bone -> its setup world: the visuals are written in that bone's
    # local frame (see _visual_for).
    bone_worlds = godot_world_transforms(model)
    visuals = []
    visual_by_slot = {}
    for index, attachment in enumerate(model.attachments):
        owner = attachment.slot or attachment.name
        visual = _visual_for(
            attachment,
            textures[index] if index < len(textures) else None,
            id_by_name, bone_worlds,
            (sources or {}).get(index))
        visuals.append(visual)
        slot = attachment.slot or attachment.name
        # A slot names a bone in SkelForm (the format has no slots), but the
        # other formats keep them apart: a Spine rig has slots whose names are
        # not bones at all, and skipping those left ten of the alien's bones
        # with no visual. Fall back to the bone the attachment is weighted to,
        # the same "dominant bone" rule out_spine uses for its host.
        if slot not in id_by_name:
            slot = _dominant_bone(attachment, id_by_name)
        if slot is not None and slot in id_by_name:
            # A bone points at exactly ONE visual, and the animation's Hidden
            # keys toggle that bone — so the visual has to be the attachment
            # the source DRAWS, not the first alternative it lists. Taking the
            # first hid most of a Spine-sourced rig: Spine lists slot
            # alternatives freely, and a slot's first entry is often not the
            # one equipped, which left the bone (and its whole visual) hidden.
            # Among equally equipped attachments the first one wins.
            chosen = visual_by_slot.get(slot)
            if chosen is None or _outranks(model.attachments[chosen], attachment):
                visual_by_slot[slot] = index
                bone = bones[id_by_name[slot]]
                bone["visuals_id"] = index
                # The SETUP pose's visibility, not "some animation draws it":
                # the runtime hides a slot whose setup attachment is absent
                # until an attachment timeline equips it, and those timelines
                # travel as Hidden keys. Using the union drew hair, capes and
                # effects the source leaves out, and inflated the pane's
                # framing box with them.
                bone["hidden"] = not attachment.setup
                bone["init_hidden"] = not attachment.setup
                # The editor draws a bone's texture from `bone.tex`, looked up
                # in the ACTIVE style (`anim_tex_of`), and its loader clears
                # `visuals_id` when `tex` is empty (`utils.rs`) — a file that
                # fills only `visuals[]` opens showing bare bones. The pivot
                # fields travel with it for the same reason: the editor's
                # renderer offsets a rect by `rotate(pivot_pos * tex.size, …)`,
                # which is where a region's placement lives.
                bone["tex"] = visual["tex"]
                bone["zindex"] = visual["zindex"]
                bone["pivot_pos"] = dict(visual["pivot_pos"])
                bone["pivot_rot"] = visual["pivot_rot"]
                bone["pivot_scale"] = dict(visual["pivot_scale"])
    # Draw order travels as zindex, increasing with the model's order (the
    # editor keeps the original in init_zindex, like it does for tex).
    for index, visual in enumerate(visuals):
        visual["zindex"] = index
        visual["init_zindex"] = index
        # The bone carries the same order the editor sorts by.
        for bone in bones:
            if bone.get("visuals_id") == index:
                bone["zindex"] = index
                break

    # Multiple visuals on one bone: SkelForm draws ONE visual per bone
    # (SkfDraw reads `bones[b].visuals_id`), but Spine rigs stack attachments
    # on the same bone — eyes, makeup, hair, capes. Each leftover attachment
    # gets its own child bone parented to its anchor bone (inheriting its
    # animated transform) and appended LAST, so it draws on top — matching the
    # source's slot order. Zero-area geometry (skin-gated weapon parts) stays
    # out: the runtime would draw it collapsed at the origin.
    # Ownership is read from the ARMATURE, not from `visual_by_slot`: an
    # attachment can be chosen for a bone and then displaced by a better
    # candidate (the one whose name IS the slot name outranks it), and the
    # stale set still listed the loser as owned. It then got no bone at all —
    # the visual stayed in the file, nothing drew it, and the slot map could
    # not offer it. This rig's `SupportObject_01` lost to `R_Hand` exactly that
    # way. Reading what the bones actually carry makes every attachment
    # reachable, including one added tomorrow.
    # `visuals_id` 0 is a VALID visual (the first one) and `x or -1` turns it
    # into -1, so the owner of visual 0 read as unowned. Every such attachment
    # then got a duplicate bone and the slot map dropped it — this rig's
    # `SupportObject_01` (the shield) was exactly that case. Test for an int and
    # compare, never for truthiness.
    owned_visuals = {bone["visuals_id"] for bone in bones
                     if isinstance(bone.get("visuals_id"), int)
                     and bone["visuals_id"] >= 0}
    for index, attachment in enumerate(model.attachments):
        if index in owned_visuals:
            continue
        span_x = max((p[0] for p in attachment.polygon), default=0.0) - \
            min((p[0] for p in attachment.polygon), default=0.0)
        span_y = max((p[1] for p in attachment.polygon), default=0.0) - \
            min((p[1] for p in attachment.polygon), default=0.0)
        if span_x <= 1e-6 and span_y <= 1e-6:
            continue
        anchor_name, best = None, 0.0
        for bone_name, ws in attachment.weights or []:
            total = sum(ws)
            if total > best and bone_name in id_by_name:
                best, anchor_name = total, bone_name
        if anchor_name is None:
            continue
        bone_id = len(bones)
        visual = visuals[index]
        # Names are made UNIQUE instead of skipped. An attachment may share its
        # name with a bone — this rig's `SupportObject_01` is both a bone and a
        # skin entry — and the old `attachment.name in id_by_name: continue`
        # dropped it entirely: the visual stayed in the file, nothing drew it,
        # and the slot map could not offer it, so the attachment was
        # unreachable for every reader and for the compare shell. A name is
        # never a reason to lose art; only zero-area geometry is.
        node_name = attachment.name
        suffix = 0
        while node_name in id_by_name:
            suffix += 1
            node_name = f"{attachment.name}__{suffix}"
        bones.append({
            "id": bone_id, "name": node_name,
            "parent_id": id_by_name[anchor_name],
            "pos": _vec2(0.0, 0.0), "scale": _vec2(1.0, 1.0), "rot": 0.0,
            "init_pos": _vec2(0.0, 0.0), "init_rot": 0.0,
            "init_scale": _vec2(1.0, 1.0),
            "ik_family_id": -1, "physics_id": -1,
            "visuals_id": index, "hidden": not attachment.setup,
            "init_hidden": not attachment.setup,
            "tex": visual["tex"], "zindex": visual["zindex"],
            "pivot_pos": dict(visual["pivot_pos"]),
            "pivot_rot": visual["pivot_rot"],
            "pivot_scale": dict(visual["pivot_scale"]),
        })
        # The UNIQUE name, so a later attachment with the same name gets its own
        # bone instead of resolving to this one.
        id_by_name[node_name] = bone_id

    atlases = []
    for page in pages:
        size = (page_sizes or {}).get(page) or atlas_sizes.get(page) or (0, 0)
        atlases.append({"filename": page_names.get(page, page),
                        "size": _vec2i(size[0], size[1])})

    animations = _build_animations(model, fps, id_by_name)
    return {
        "version": "0.8.0",
        "baked_ik": False,
        "img_format": "PNG",
        "clear_color": {"r": 0, "g": 0, "b": 0, "a": 0},
        "bones": bones,
        "animations": animations,
        "atlases": atlases,
        # `active` matters: the editor only draws textures from an active
        # style (`anim_tex_of` skips the rest), and a file that leaves every
        # style inactive opens showing bare bones and a "Unused textures" list.
        "styles": [{"id": 0, "name": "default", "active": True,
                    # `missing` entries are dropped, not carried: their page is
                    # not in the bundle, and the runtime dereferences
                    # `atlases[tex.atlas_idx]` while drawing.
                    "textures": [t for t in textures
                                 if t is not None and not t.get("missing")]}],
        "inverse_kinematics": [],
        "visuals": visuals,
        "physics": [],
    }


def _element_entries(channels: dict, element: str) -> list:
    """One bone's channels -> the entries feeding a single SkelForm element."""
    value_map = _field_map(element)
    if element == "Rotation" and channels.get("rotate"):
        keys = [(k.time, k.angle, k.curve) for k in channels["rotate"]]
    elif element == "PositionX" and channels.get("translate"):
        keys = [(k.time, k.x, k.curve) for k in channels["translate"]]
    elif element == "PositionY" and channels.get("translate"):
        keys = [(k.time, k.y, k.curve) for k in channels["translate"]]
    elif element == "ScaleX" and channels.get("scale"):
        keys = [(k.time, (k.scale[0] if k.scale else 1.0), k.curve)
                for k in channels["scale"]]
    elif element == "ScaleY" and channels.get("scale"):
        keys = [(k.time, (k.scale[1] if k.scale else 1.0), k.curve)
                for k in channels["scale"]]
    else:
        return []
    return [{"time": time, "value": value_map(value),
             "curve": _curve_to_field(curve, element)}
            for time, value, curve in keys]


def _build_animations(model, fps: float, id_by_name: dict) -> list:
    animations = []
    for anim_index, (name, animation) in enumerate(model.animations.items()):
        keyframes = []
        for bone_index, bone in enumerate(model.bones):
            if bone.name not in animation:
                continue
            for element in ("PositionX", "PositionY", "Rotation", "ScaleX", "ScaleY"):
                # _element_entries already returns the element's own quadruple
                # in SkelForm's field space (handles are normalized against
                # those values), so nothing else may remap it here.
                entries = _element_entries(animation[bone.name], element)
                if not entries:
                    continue
                entries.sort(key=lambda item: item["time"])
                start_index = len(keyframes)
                for index, entry in enumerate(entries):
                    following = entries[index + 1] if index + 1 < len(entries) else None
                    keyframes.append({
                        "frame": int(round(entry["time"] * fps)),
                        "bone_id": bone_index,
                        "element": element,
                        "value": round(float(entry["value"]), 6),
                        "start_handle": _vec2(*LINEAR_HANDLES[0]),
                        "end_handle": _vec2(*LINEAR_HANDLES[1]),
                        # The last keyframe of a channel points at itself
                        # (the runtime also treats -1 that way).
                        "next_kf": (start_index + index + 1 if following
                                    else start_index + index),
                        "handle_preset": "Linear",
                    })
                # SkelForm stores the segment's handles on the keyframe that
                # ENDS it, while the model keeps them on the key that starts
                # it, so they are written one key forward.
                for index, entry in enumerate(entries[:-1]):
                    following = entries[index + 1]
                    target = keyframes[start_index + index + 1]
                    target["start_handle"], target["end_handle"] = _handles_from_curve(
                        entry["curve"], entry["time"], following["time"],
                        entry["value"], following["value"])
                    target["handle_preset"] = _preset_for(
                        entry["curve"], entry["time"], following["time"],
                        entry["value"], following["value"])
        # Attachment visibility -> Hidden keys (the model hides a slot by
        # naming no attachment).
        for slot, entries in (model.slot_timelines.get(name) or {}).items():
            if slot not in id_by_name:
                continue
            for entry in sorted(entries, key=lambda item: item["time"]):
                keyframes.append({
                    "frame": int(round(entry["time"] * fps)),
                    "bone_id": id_by_name[slot],
                    "element": "Hidden",
                    "value": 1.0 if entry.get("attachment") is None else 0.0,
                    "start_handle": _vec2(0.0, 0.0),
                    "end_handle": _vec2(0.0, 0.0),
                    "next_kf": len(keyframes),
                    "handle_preset": "Snap",
                })
        # The runtime walks this array with `if (kf.frame > frame) break;`, so
        # it has to be in frame order — and `next_kf` must point at the next
        # keyframe of the SAME channel, not at the array neighbour (the editor's
        # own files do exactly that, ending each chain with -1). Written grouped
        # by bone and element instead, every keyframe past the first is skipped
        # and the rig plays its setup pose: the animation is in the file and
        # nothing moves.
        keyframes.sort(key=lambda kf: (kf["frame"], kf["bone_id"],
                                       kf["element"]))
        following: dict = {}
        for index in range(len(keyframes) - 1, -1, -1):
            channel = (keyframes[index]["bone_id"], keyframes[index]["element"])
            keyframes[index]["next_kf"] = following.get(channel, -1)
            following[channel] = index

        animations.append({"name": name, "id": anim_index, "fps": int(fps),
                           "keyframes": keyframes})
    return animations


def _slot_map(model: Skeleton, armature: dict) -> str:
    """Slot -> {attachment: the bone that draws it}, as JSON.

    Written beside the armature, never inside it: the armature's own format is
    the vendor's and extra keys there are a compatibility risk, while a member
    the runtimes do not know is ignored. Built from the armature rather than
    from the writer's loop so the two can never disagree about which bone
    ended up drawing an attachment.
    """
    # Same trap as above: visual 0 is valid, so it must not be filtered out by
    # a truthiness test — doing so hid the shield from every slot row.
    bone_of_visual = {bone["visuals_id"]: bone["name"]
                      for bone in armature.get("bones", [])
                      if isinstance(bone.get("visuals_id"), int)
                      and bone["visuals_id"] >= 0}
    slots: dict = {}
    for index, attachment in enumerate(model.attachments):
        bone = bone_of_visual.get(index)
        if bone is None:
            continue
        slot = attachment.slot or attachment.name
        slots.setdefault(slot, {})[attachment.name] = bone
    return json.dumps(slots, indent=2)


def _editor_json(armature: dict) -> str:
    """The editor's per-file state: fold flags per bone, active style."""
    bones = [{
        "folded": False,
        "ik_folded": False,
        "meshdef_folded": False,
        "effects_folded": False,
        "ik_disabled": False,
        "locked": False,
        "blacklist": [],
        "group_color": {"r": 0, "g": 0, "b": 0, "a": 0},
    } for _ in armature.get("bones", [])]
    styles = [{"active": index == 0}
              for index, _ in enumerate(armature.get("styles") or [])]
    return json.dumps({"bones": bones, "styles": styles})


def write_skelform(model: Skeleton, output_path: str, fps: float = DEFAULT_FPS,
                   atlas_paths: list | None = None, **kwargs) -> None:
    """Write ``armature.json`` or a full ``.skf`` bundle.

    ``atlas_paths`` (optional) are the page images to embed: their PNG sizes
    fill the atlas metadata and their bytes are copied into the bundle, which
    is what makes the file loadable by SkelForm itself.

    Pages are embedded under the official ``atlas0.png``/``atlas1.png`` names,
    not under the rig's own page name. SkelForm's runtimes find a page by
    looking for "atlas" in the member name — its web player only loads members
    that match — so a bundle that copies `hero.png` in as `hero.png` loads its
    armature, draws the rig, and never applies a texture (every shape comes out
    black). The official sample bundles use the same naming.
    """
    atlas_sizes = {}
    for path in atlas_paths or []:
        size = read_png_size(str(path))
        if size:
            atlas_sizes[Path(path).name] = size
    textures, pages = _textures_and_atlases(model)
    pages = pages or [Path(p).name for p in atlas_paths or []]
    by_name = {Path(p).name: Path(p) for p in atlas_paths or []}
    # Each page of the model maps to its own file. The name is the reliable
    # link (the bundle may hand the pages over in a different order); the
    # positional fallback covers an armature that names its page something else.
    files = list(atlas_paths or [])
    page_files = [by_name.get(page) or (files[index] if index < len(files) else None)
                  for index, page in enumerate(pages)]
    merged, sources, merged_size = _single_upright_page(model, textures, page_files)
    if merged is not None:
        pages = [pages[0]]
        atlas_sizes = {pages[0]: merged_size}
    page_names = {page: f"atlas{index}.png" for index, page in enumerate(pages)}
    armature = _build_armature(model, fps, atlas_sizes, page_names, textures,
                               sources, {}, pages)
    payload = json.dumps(armature, indent=2)

    target = Path(output_path)
    if target.suffix.lower() == ".skf" or atlas_paths:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("armature.json", payload)
            bundle.writestr("readme.md", READ_ME)
            # The editor keeps its per-file state in `editor.json`, and that is
            # where a style is marked active — its loader overwrites the
            # armature's own flag with what it finds here (`utils.rs`). Without
            # the member the editor opens the rig with no active style, so no
            # texture resolves. The camera is left out on purpose: it is only a
            # starting view, and a wrong one would hide the rig.
            bundle.writestr("editor.json", _editor_json(armature))
            # The web player numbers the pages it finds by ITERATION ORDER over
            # the archive's members (`atlasIdx` in its skfReadFile), not by the
            # member name, and it reads armature.json on the way through. So the
            # armature goes first and the pages follow in the order `atlases`
            # lists them: written any other way, every attachment samples the
            # wrong page and the rig draws black.
            for index, page in enumerate(pages):
                if merged is not None and index == 0:
                    bundle.writestr("atlas0.png", merged)
                    continue
                source = by_name.get(page)
                if source is not None and source.exists():
                    bundle.writestr(f"atlas{index}.png", source.read_bytes())
            # Slot map, OURS to use: the armature only knows bones
            # (`visuals_id`), while a comparison shell addresses attachments by
            # SLOT name. Without it this pane offered rows named after the bone
            # that draws the visual (`HandObject_01`) while every other pane
            # offered the slot (`Solt : R_Hand`), so a pick in the sidebar
            # matched nothing and the pane silently kept drawing — the bug
            # where the source showed a sword and this pane did not.
            # Appended LAST on purpose: the web player numbers the atlas pages
            # it finds by ITERATION ORDER over the archive's members (the
            # writer note above), so a member inserted before them shifts every
            # index — a four-page rig then samples the wrong pages and dies
            # with `Cannot read properties of undefined (reading 'size')`.
            # A member after them cannot move an earlier one.
            bundle.writestr("slots.json", _slot_map(model, armature))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
