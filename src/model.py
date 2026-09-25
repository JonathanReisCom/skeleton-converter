"""Canonical in-memory model of a 2D skeletal rig.

This is the hub of the converter: every format adapter reads into this model and
every adapter writes from it. It carries no format-specific data.

Conventions
-----------
The model is stored in **Godot space**: Y-down, rotation clockwise-positive,
local transforms relative to the parent bone. Format adapters handle the
mirroring to/from their own space (e.g. Spine is Y-up, CCW-positive).

Matrices are 2D affines stored as flat tuples ``(a, b, c, d, tx, ty)``
representing::

    [ a  b  tx ]
    [ c  d  ty ]
    [ 0  0   1 ]

so ``transform(m, p)`` returns ``(a·px + b·py + tx, c·px + d·py + ty)``.

Godot's ``Transform2D(xx, xy, yx, yy, ox, oy)`` file literal is the *transpose*
of this tuple: ``(a, c, b, d, tx, ty)``. Getting this backwards shears every
skinned vertex — see the README's coordinate contract.
"""

from __future__ import annotations


import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 2D affine matrices (row-major [a, b, c, d, tx, ty])
# ---------------------------------------------------------------------------

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(left: tuple, right: tuple) -> tuple:
    """Compose two affines: result applies ``right`` first, then ``left``."""
    a1, b1, c1, d1, tx1, ty1 = left
    a2, b2, c2, d2, tx2, ty2 = right
    return (
        a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
        a1 * tx2 + b1 * ty2 + tx1, c1 * tx2 + d1 * ty2 + ty1,
    )


def transform(matrix: tuple, point: tuple) -> tuple:
    a, b, c, d, tx, ty = matrix
    return (a * point[0] + b * point[1] + tx, c * point[0] + d * point[1] + ty)


def invert(matrix: tuple) -> tuple:
    """Inverse of [a b; c d] + translation."""
    a, b, c, d, tx, ty = matrix
    det = a * d - b * c
    if abs(det) < 1e-12:
        return IDENTITY
    inv = 1.0 / det
    return (
        d * inv, -b * inv,
        -c * inv, a * inv,
        (b * ty - d * tx) * inv,
        (c * tx - a * ty) * inv,
    )


def compose(position: tuple, rotation_deg: float, scale: tuple = (1.0, 1.0)) -> tuple:
    """A standard-convention matrix: T(position) · R(rotation) · S(scale)."""
    radians = math.radians(rotation_deg)
    cos, sin = math.cos(radians), math.sin(radians)
    return (
        cos * scale[0], -sin * scale[1],
        sin * scale[0], cos * scale[1],
        position[0], position[1],
    )


def mirror_matrix(matrix: tuple) -> tuple:
    """F·M·F with F = diag(1,-1): flips input and output Y."""
    a, b, c, d, tx, ty = matrix
    return (a, -b, -c, d, tx, -ty)


def mirror_point(point: tuple) -> tuple:
    return (point[0], -point[1])


def godot_transform2d(position: tuple, rotation_deg: float, scale: tuple = (1.0, 1.0)) -> tuple:
    """A Godot ``Transform2D`` literal for a local transform.

    Godot's column convention differs from ``compose``: its columns are
    x = (cos, sin) and y = (-sin, cos) — see the engine's own scenes. Using the
    other convention silently shears every skinned vertex, because Godot skins
    with ``accum · rest.inverse()``.
    """
    radians = math.radians(rotation_deg)
    cos, sin = math.cos(radians), math.sin(radians)
    return (
        cos * scale[0], sin * scale[0],
        -sin * scale[1], cos * scale[1],
        position[0], position[1],
    )


def godot_matrix_from_standard(m: tuple) -> tuple:
    """Convert a standard-convention matrix to Godot's file-literal order.

    Standard row-major (a, b, c, d, tx, ty) maps to Godot's
    (xx, xy, yx, yy, ox, oy) as (a, c, b, d, tx, ty) — a transpose.
    """
    return (m[0], m[2], m[1], m[3], m[4], m[5])


# ---------------------------------------------------------------------------
# Canonical model
# ---------------------------------------------------------------------------


@dataclass
class Bone:
    """A bone in the hierarchy, stored in Godot-space local transform.

    ``position`` and ``rotation_deg`` are the local transform relative to the
    parent bone. ``inherit`` records the Spine inheritance mode — Godot has no
    equivalent, so format adapters that need the effective world transform use
    :func:`spine_world_transforms`.
    """
    name: str
    parent: str | None
    position: tuple
    rotation_deg: float
    scale: tuple = (1.0, 1.0)
    length: float = 0.0
    inherit: str = "normal"
    path: str = ""  # relative to Skeleton2D
    # Godot bind pose (Bone2D `rest`): position, rotation_deg, scale. Godot
    # skins meshes with pose * rest^-1, so when a scene's rest differs from
    # its node pose the mesh basis differs from the bone basis — both must
    # survive the round trip. None means rest == node pose (Spine-authored
    # rigs, where the two coincide).
    rest: tuple | None = None


@dataclass
class Attachment:
    """A drawable attached to a slot.

    ``polygon`` holds 2D vertices in the attachment's rest-space coordinates.
    ``uv`` holds texture pixel coordinates. ``weights`` is a list of
    ``(bone_name, weights_per_vertex)`` pairs; empty means unweighted.
    """
    name: str
    polygon: list
    uv: list
    polygons: list  # triangle groups as index lists
    weights: list
    position: tuple = (0.0, 0.0)  # Polygon2D node offset
    offset: tuple = (0.0, 0.0)    # Godot per-vertex offset property
    internal_vertices: int = 0


@dataclass
class Key:
    """One animation key in canonical (Godot-space absolute) values.

    ``angle`` is a rotate-key value in degrees (Godot rotation_degrees);
    ``x``/``y`` are translate-key values in Godot units (Y-down). Exactly one
    of those value groups is meaningful depending on the owning track kind.
    ``curve`` holds the spine-space bezier control points for the segment
    starting at this key — ``[x1, y1, x2, y2]`` for rotate, ``[cx1, cy1,
    cx2, cy2, cx3, cy3, cx4, cy4]`` for translate (time axis first, then one
    value axis per Godot value axis; see ``bezier_table_point``). Spine space
    is the curve's native space (CurveTimeline control points are absolute
    time/value in spine offsets); each writer maps it to its own
    interpolation value space.
    """
    time: float
    angle: float = 0.0
    x: float = 0.0
    y: float = 0.0
    curve: tuple | None = None


@dataclass
class Skeleton:
    """The canonical rig: bones in parent-before-child order, attachments, animations."""
    bones: list = field(default_factory=list)        # list[Bone], parents before children
    attachments: list = field(default_factory=list)  # list[Attachment]
    # name -> {bone_name: {"rotate": list[Key] | "translate": list[Key]}}
    animations: dict = field(default_factory=dict)
    texture_path: str = ""                           # res:// path from the source scene
    by_name: dict = field(default_factory=dict)      # bone name → Bone, populated by readers
    # Facts the reader learned that the caller should report rather than
    # rediscover: which atlas was used, which constraints were baked, what the
    # format could not represent. Free-form strings, printed by the CLI.
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Forward kinematics
# ---------------------------------------------------------------------------


def godot_world_transforms(model: Skeleton) -> dict:
    """Bone name -> 2D affine world matrix in Godot space (rest pose)."""
    world = {}
    for bone in model.bones:
        parent = world.get(bone.parent, IDENTITY)
        world[bone.name] = multiply(
            parent, compose(bone.position, bone.rotation_deg, bone.scale)
        )
    return world


def spine_world_transforms(spine: dict, pose: dict | None = None) -> dict:
    """Bone name -> affine world matrix in Spine space.

    Mirrors the runtime's ``BonePose.updateWorldTransform``, including the
    ``inherit`` modes — a bone with ``noRotationOrReflection`` does NOT take its
    parent's rotation, and composing it as if it did is what makes feet point
    the wrong way.

    ``pose`` optionally overrides local values:
    ``{bone name: (x, y, rotation_deg)}``.
    """
    world = {}
    for bone in spine["bones"]:
        name = bone["name"]
        local = pose.get(name) if pose else None
        x = local[0] if local else bone.get("x", 0.0)
        y = local[1] if local else bone.get("y", 0.0)
        rotation = local[2] if local else bone.get("rotation", 0.0)
        scale_x = bone.get("scaleX", 1.0)
        scale_y = bone.get("scaleY", 1.0)
        shear_x = bone.get("shearX", 0.0)
        shear_y = bone.get("shearY", 0.0)
        parent_name = bone.get("parent")
        if not parent_name:
            world[name] = compose((x, y), rotation, (scale_x, scale_y))
            continue
        parent = world[parent_name]
        pa, pb, pc, pd, ptx, pty = parent
        world_x = pa * x + pb * y + ptx
        world_y = pc * x + pd * y + pty
        inherit = bone.get("inherit", "normal")
        if inherit == "normal":
            rx = math.radians(rotation + shear_x)
            ry = math.radians(rotation + 90.0 + shear_y)
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            a, b = pa * la + pb * lc, pa * lb + pb * ld
            c, d = pc * la + pd * lc, pc * lb + pd * ld
        elif inherit == "onlyTranslation":
            rx = math.radians(rotation + shear_x)
            ry = math.radians(rotation + 90.0 + shear_y)
            a, b = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            c, d = math.sin(rx) * scale_x, math.sin(ry) * scale_y
        elif inherit == "noRotationOrReflection":
            # Only the parent's first column survives, renormalised; the parent
            # rotation is dropped entirely.
            pa2, pc2 = pa, pc
            total = pa2 * pa2 + pc2 * pc2
            if total > 1e-12:
                factor = abs(pa * pd - pb * pc) / total
                pb2, pd2 = pc2 * factor, pa2 * factor
                effective = rotation - math.degrees(math.atan2(pc2, pa2))
            else:
                pa2 = pc2 = 0.0
                pb2, pd2 = pb, pd
                effective = rotation - 90.0 + math.degrees(math.atan2(pd2, pb2))
            rx = math.radians(effective + shear_x)
            ry = math.radians(effective + shear_y + 90.0)
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            a = pa2 * la - pb2 * lc
            b = pa2 * lb - pb2 * ld
            c = pc2 * la + pd2 * lc
            d = pc2 * lb + pd2 * ld
        else:
            # noScale / noScaleOrReflection: keep the parent's rotation, drop
            # its scale, optionally preserving reflection.
            radians = math.radians(rotation)
            cos, sin = math.cos(radians), math.sin(radians)
            za = pa * cos + pb * sin
            zc = pc * cos + pd * sin
            magnitude = math.sqrt(za * za + zc * zc) or 1.0
            za, zc = za / magnitude, zc / magnitude
            zb, zd = -zc, za
            if inherit == "noScale" and (pa * pd - pb * pc < 0) != (scale_x < 0 != scale_y < 0):
                zb, zd = -zb, -zd
            rx = math.radians(shear_x)
            ry = math.radians(90.0 + shear_y)
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            a = za * la + zb * lc
            b = za * lb + zb * ld
            c = zc * la + zd * lc
            d = zc * lb + zd * ld
        world[name] = (a, b, c, d, world_x, world_y)
    return world


# --- shared geometry (used by exporters and the constraint port) ---


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



def group_triangles(triangles: list) -> list:
    return [
        [triangles[i], triangles[i + 1], triangles[i + 2]]
        for i in range(0, len(triangles) - 2, 3)
    ]

def godot_rest_worlds(model) -> dict:
    """bone name -> GLOBAL REST world in Godot space, chained from Bone2D
    rests (not node poses). Godot skins meshes with pose * global_rest^-1,
    so this — not the node-pose world — is the bind basis for mesh locals.
    Bones without an explicit rest fall back to their node pose (Spine
    authoring, where bind == setup)."""
    world = {}
    for bone in model.bones:
        pos, rot_deg, scale = bone.rest or (
            bone.position, bone.rotation_deg, bone.scale)
        parent = world.get(bone.parent, (1, 0, 0, 1, 0, 0))
        cos, sin = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
        sx, sy = scale
        world[bone.name] = multiply(parent, (
            cos * sx, sin * sx, -sin * sy, cos * sy, pos[0], pos[1]))
    return world


def mirror_world(world: tuple) -> tuple:
    """A Spine-space bone transform: conjugate the Godot one by the y flip
    (F·A·F⁻¹), mirroring translation and rotation columns."""
    a, b, c, d, tx, ty = world
    return (a, -b, -c, d, tx, -ty)



def spine_inverse(kind: str, axis: str | None, bone: Skeleton):
    """Godot track value -> spine offset: the inverse of each writer's affine
    map. Curve control points live in the offset space of the raw JSON values,
    which is what every writer maps through."""
    if kind == "rotate":
        # godot = rotation_deg - offset  (angle = -(setup_spine + offset))
        return lambda v: bone.rotation_deg - v
    if axis == "x":
        # godot = offset + setup_x
        return lambda v: v - bone.position[0]
    # godot = -(offset + setup_y) = -offset + position[1]
    return lambda v: bone.position[1] - v

