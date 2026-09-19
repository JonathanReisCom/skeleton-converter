"""Bake Spine constraints (IK, path) into bone transforms.

Godot has no equivalent of Spine's constraints, so a rig that uses them cannot
be represented directly: the constraint-driven bones would simply keep their
setup pose and the rig would look wrong. This module runs the same solvers the
official runtime uses — ported from ``spine-core`` 4.2, ``IkConstraint`` and
``PathConstraint`` — and writes the resulting local transforms back as ordinary
animation keys, so the exported scene reproduces the constrained pose without
needing the constraints.

Scope, stated honestly:

- **IK, 1 and 2 bone** — ported in full, including ``softness``, ``stretch``,
  ``compress`` and non-uniform scale.
- **Path** — ported in full, including constant-speed arc sampling and the
  before/after extensions.
- **Transform and physics constraints are NOT baked.** They are reported, not
  silently dropped, so a caller can see what the output will not reproduce.
- Constraints run in the runtime's own order (the skeleton's ``updateCache``),
  not JSON declaration order — getting this wrong changes the result whenever
  two constraints share a bone.
"""

from __future__ import annotations

import math

from .model import spine_world_transforms

# ---------------------------------------------------------------------------
# Math helpers mirroring the runtime's MathUtils
# ---------------------------------------------------------------------------

RAD_DEG = 180.0 / math.pi
DEG_RAD = math.pi / 180.0
PI = math.pi
PI2 = math.pi * 2


def _signum(value: float) -> float:
    return 1.0 if value > 0 else (-1.0 if value < 0 else 0.0)


def _acos(value: float) -> float:
    return math.acos(max(-1.0, min(1.0, value)))


def _clamp_angle(angle: float) -> float:
    if angle > 180.0:
        return angle - 360.0
    if angle < -180.0:
        return angle + 360.0
    return angle


# ---------------------------------------------------------------------------
# Skeleton state used by the solvers
# ---------------------------------------------------------------------------


class BoneState:
    """The mutable per-bone state the runtime keeps while solving."""

    __slots__ = ("data", "name", "parent", "x", "y", "rotation", "scale_x",
                 "scale_y", "shear_x", "shear_y", "ax", "ay", "arotation",
                 "ascale_x", "ascale_y", "ashear_x", "ashear_y", "a", "b", "c",
                 "d", "world_x", "world_y", "inherit", "sorted", "active",
                 "length")

    def __init__(self, data: dict, parent: "BoneState | None"):
        self.data = data
        self.name = data["name"]
        self.parent = parent
        self.x = data.get("x", 0.0)
        self.y = data.get("y", 0.0)
        self.rotation = data.get("rotation", 0.0)
        self.scale_x = data.get("scaleX", 1.0)
        self.scale_y = data.get("scaleY", 1.0)
        self.shear_x = data.get("shearX", 0.0)
        self.shear_y = data.get("shearY", 0.0)
        self.length = data.get("length", 0.0)
        self.inherit = data.get("inherit", "normal")
        self.sorted = False
        self.active = True
        self.set_to_setup()

    def set_to_setup(self) -> None:
        self.ax = self.x
        self.ay = self.y
        self.arotation = self.rotation
        self.ascale_x = self.scale_x
        self.ascale_y = self.scale_y
        self.ashear_x = self.shear_x
        self.ashear_y = self.shear_y

    def update_world_transform_with(self, x, y, rotation, scale_x, scale_y,
                                    shear_x, shear_y) -> None:
        """Port of ``Bone.updateWorldTransformWith`` (normal inherit path)."""
        self.ax, self.ay = x, y
        self.arotation = rotation
        self.ascale_x, self.ascale_y = scale_x, scale_y
        self.ashear_x, self.ashear_y = shear_x, shear_y

        parent = self.parent
        if parent is None:
            rx = (rotation + shear_x) * DEG_RAD
            ry = (rotation + 90.0 + shear_y) * DEG_RAD
            self.a = math.cos(rx) * scale_x
            self.b = math.cos(ry) * scale_y
            self.c = math.sin(rx) * scale_x
            self.d = math.sin(ry) * scale_y
            self.world_x, self.world_y = x, y
            return

        pa, pb, pc, pd = parent.a, parent.b, parent.c, parent.d
        self.world_x = pa * x + pb * y + parent.world_x
        self.world_y = pc * x + pd * y + parent.world_y

        if self.inherit == "normal":
            rx = (rotation + shear_x) * DEG_RAD
            ry = (rotation + 90.0 + shear_y) * DEG_RAD
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            self.a = pa * la + pb * lc
            self.b = pa * lb + pb * ld
            self.c = pc * la + pd * lc
            self.d = pc * lb + pd * ld
        elif self.inherit == "onlyTranslation":
            rx = (rotation + shear_x) * DEG_RAD
            ry = (rotation + 90.0 + shear_y) * DEG_RAD
            self.a = math.cos(rx) * scale_x
            self.b = math.cos(ry) * scale_y
            self.c = math.sin(rx) * scale_x
            self.d = math.sin(ry) * scale_y
        elif self.inherit == "noRotationOrReflection":
            pa, pc = pa, pc
            total = pa * pa + pc * pc
            if total > 0.0001:
                factor = abs(pa * pd - pb * pc) / total
                pb2, pd2 = pc * factor, pa * factor
                prx = math.atan2(pc, pa) * RAD_DEG
            else:
                pa = pc = 0.0
                pb2, pd2 = pb, pd
                prx = 90.0 - math.atan2(pd, pb) * RAD_DEG
            rx = (rotation + shear_x - prx) * DEG_RAD
            ry = (rotation + shear_y - prx + 90.0) * DEG_RAD
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            self.a = pa * la - pb2 * lc
            self.b = pa * lb - pb2 * ld
            self.c = pc * la + pd2 * lc
            self.d = pc * lb + pd2 * ld
        else:
            rx = (rotation + shear_x) * DEG_RAD
            ry = (rotation + 90.0 + shear_y) * DEG_RAD
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            self.a = la
            self.b = lb
            self.c = lc
            self.d = ld

    def update_applied_transform(self) -> None:
        """Port of ``Bone.updateAppliedTransform`` (normal inherit)."""
        parent = self.parent
        if parent is None:
            self.arotation = math.atan2(self.c, self.a) * RAD_DEG
            self.ax, self.ay = self.world_x, self.world_y
            self.ascale_x = math.sqrt(self.a * self.a + self.c * self.c)
            self.ascale_y = math.sqrt(self.b * self.b + self.d * self.d)
            return
        pa, pb, pc, pd = parent.a, parent.b, parent.c, parent.d
        pid = pa * pd - pb * pc
        if pid == 0:
            self.ax = self.ay = self.arotation = 0.0
            self.ascale_x = self.ascale_y = 0.0
            return
        pid = 1.0 / pid
        # The runtime's ia/ib/ic/id are the inverse of the parent's linear part
        # with NO sign flip: ia = pd*pid, ib = pb*pid, ic = pc*pid, id = pa*pid.
        ia, ib = pd * pid, pb * pid
        ic, id_ = pc * pid, pa * pid
        dx = self.world_x - parent.world_x
        dy = self.world_y - parent.world_y
        self.ax = dx * ia - dy * ib
        self.ay = dy * id_ - dx * ic

        # Rotation is extracted from the parent-relative matrix (ra, rc), not
        # from atan2(c, a) — the latter is the WORLD angle and accumulates along
        # a chain, which is what made the hero's chain3+ drift several units.
        ra = ia * self.a - ib * self.c
        rb = ia * self.b - ib * self.d
        rc = id_ * self.c - ic * self.a
        rd = id_ * self.d - ic * self.b
        self.ashear_x = 0.0
        self.ascale_x = math.sqrt(ra * ra + rc * rc)
        if self.ascale_x > 0.0001:
            det = ra * rd - rb * rc
            self.ascale_y = det / self.ascale_x
            self.ashear_y = -math.atan2(ra * rb + rc * rd, det) * RAD_DEG
            self.arotation = math.atan2(rc, ra) * RAD_DEG
        else:
            self.ascale_y = 0.0
            self.ashear_y = 0.0
            self.arotation = 0.0


class ConstraintSolver:
    """Applies IK and path constraints in the runtime's own order."""

    def __init__(self, spine: dict, skin: str | None = None, pose: dict | None = None):
        self.spine = spine
        # Local values sampled from an animation: {bone: (x, y, rotation_deg)}.
        # Constraints are evaluated against the animated pose, not the setup
        # pose — the hero's head is both animated and IK-driven.
        self.pose = pose or {}
        # Which skin is active decides whether a `skin: true` constraint runs.
        # Default to the rig's first skin, matching the runtime's usual setup.
        if skin is None:
            skins = spine.get("skins", []) or []
            skin = skins[0].get("name") if skins and isinstance(skins[0], dict) else None
        self.skin = skin
        self.bones: dict[str, BoneState] = {}
        for data in spine["bones"]:
            parent = self.bones.get(data.get("parent"))
            state = BoneState(data, parent)
            local = self.pose.get(data["name"])
            if local:
                state.x, state.y, state.rotation = local[0], local[1], local[2]
                state.set_to_setup()
            self.bones[data["name"]] = state
        self.ik = spine.get("ik", []) or []
        self.path = spine.get("path", []) or []
        self.unsupported = {
            "transform": spine.get("transform", []) or [],
            "physics": [b["name"] for b in spine["bones"] if b.get("physics")],
        }
        self._order = self._constraint_order()

    # -- ordering (port of Skeleton.updateCache) ---------------------------

    def _constraint_order(self) -> list:
        """Constraints in the order the runtime applies them.

        The runtime walks ``order`` 0,1,2,... and picks the matching IK,
        transform, then path constraint. Declaration order is not the order.
        """
        total = len(self.ik) + len(self.path)
        order = []
        for index in range(total):
            for constraint in self.ik:
                if constraint.get("order", 0) == index:
                    order.append(("ik", constraint))
                    break
            else:
                for constraint in self.path:
                    if constraint.get("order", 0) == index:
                        order.append(("path", constraint))
                        break
        return order

    # -- public API --------------------------------------------------------

    def apply(self) -> None:
        """Run every constraint, updating the bone world transforms."""
        # The runtime builds its updateCache from ACTIVE bones only, so a bone
        # deactivated by the skin is never transformed and keeps (0, 0). Skip
        # them here too, or the export would place bones the source engine
        # leaves at the origin.
        self.active = {name: self._bone_active(st) for name, st in self.bones.items()}

        for state in self.bones.values():
            if not self.active[state.name]:
                state.world_x = state.world_y = 0.0
                state.a = state.b = state.c = state.d = 0.0
                continue
            state.set_to_setup()
            self._reset_world(state)

        for kind, constraint in self._order:
            if not self._is_active(constraint):
                continue
            if kind == "ik":
                self._apply_ik(constraint)
            else:
                self._apply_path(constraint)

        # The runtime's updateCache ends by walking every bone (Bone.update), but
        # a path constraint has ALREADY called updateAppliedTransform() on its
        # own bones — re-propagating them would recompute world from stale
        # parents and undo the placement (the hero's chains drifted up to 4.5
        # units, growing along the chain). Only bones the path did not settle
        # are re-propagated; that is what catches IK descendants like foot1.
        settled = set()
        for kind, constraint in self._order:
            if kind == "path" and self._is_active(constraint):
                settled.update(constraint.get("bones") or [])
        for state in self.bones.values():
            if not self.active[state.name] or state.name in settled:
                continue
            state.update_world_transform_with(
                state.ax, state.ay, state.arotation, state.ascale_x,
                state.ascale_y, state.ashear_x, state.ashear_y,
            )

    def _bone_active(self, state: BoneState) -> bool:
        """Whether the runtime considers this bone active under the skin.

        A bone with ``skin: true`` is deactivated unless the active skin (or a
        skin it inherits from) lists it. Inactive bones are not transformed at
        all, so the hero's chain bones read ``(0, 0)`` under the ``default``
        skin while the morningstar weapon is not equipped.
        """
        if not state.data.get("skin", False):
            return True
        for skin in self.spine.get("skins", []) or []:
            if not isinstance(skin, dict) or skin.get("name") != self.skin:
                continue
            return state.name in (skin.get("bones") or [])
        return False

    def _is_active(self, constraint: dict) -> bool:
        """Whether the runtime would run this constraint for the active skin.

        The JSON does not list constraints per skin; the runtime derives the
        association while parsing, linking a ``skin: true`` constraint to the
        skin whose bones reference it. So the check is on the constraint's
        ``skin`` flag plus whether the active skin owns the constrained bones —
        requiring an explicit ``skin.constraints`` list (which never exists in
        the file) silently disabled the hero's path constraint.
        """
        if not constraint.get("skin", False):
            return True
        bones = constraint.get("bones") or []
        for skin in self.spine.get("skins", []) or []:
            if not isinstance(skin, dict) or skin.get("name") != self.skin:
                continue
            owned = skin.get("bones") or []
            if any(name in owned for name in bones):
                return True
            # Some exporters do write the list; honour it when present.
            for entry in skin.get("constraints") or []:
                if entry == constraint.get("name"):
                    return True
        return False

    def _reset_world(self, state: BoneState) -> None:
        """Propagate the setup pose so world matrices exist before solving."""
        state.update_world_transform_with(
            state.ax, state.ay, state.arotation, state.ascale_x,
            state.ascale_y, state.ashear_x, state.ashear_y,
        )

    def local_poses(self) -> dict:
        """Bone name -> (x, y, rotation_deg) after constraints ran."""
        out = {}
        for name, state in self.bones.items():
            out[name] = (state.ax, state.ay, state.arotation)
        return out

    # -- IK ----------------------------------------------------------------

    def _apply_ik(self, data: dict) -> None:
        if data.get("mix", 1) == 0:
            return
        bones = [self.bones[n] for n in data["bones"] if n in self.bones]
        target = self.bones.get(data.get("target"))
        if not bones or target is None:
            return
        bend = 1.0 if data.get("bendPositive", True) else -1.0
        mix = data.get("mix", 1.0)
        softness = data.get("softness", 0.0)
        if len(bones) == 1:
            self._apply_ik1(bones[0], target.world_x, target.world_y,
                            bool(data.get("compress", False)),
                            bool(data.get("stretch", False)),
                            bool(data.get("uniform", False)), mix)
        elif len(bones) == 2:
            self._apply_ik2(bones[0], bones[1], target.world_x, target.world_y,
                            bend, bool(data.get("stretch", False)),
                            bool(data.get("uniform", False)), softness, mix)

    def _apply_ik1(self, bone: BoneState, target_x: float, target_y: float,
                   compress: bool, stretch: bool, uniform: bool,
                   alpha: float) -> None:
        parent = bone.parent
        if parent is None:
            return
        pa, pb, pc, pd = parent.a, parent.b, parent.c, parent.d
        rotation_ik = -bone.ashear_x - bone.arotation
        tx = ty = 0.0
        if bone.inherit == "onlyTranslation":
            tx = target_x - bone.world_x
            ty = target_y - bone.world_y
        elif bone.inherit == "noRotationOrReflection":
            s = abs(pa * pd - pb * pc) / max(0.0001, pa * pa + pc * pc)
            sa, sc = pa, pc
            pb = -sc * s
            pd = sa * s
            rotation_ik += math.atan2(sc, sa) * RAD_DEG
            x, y = target_x - parent.world_x, target_y - parent.world_y
            det = pa * pd - pb * pc
            if abs(det) <= 0.0001:
                tx = ty = 0.0
            else:
                tx = (x * pd - y * pb) / det - bone.ax
                ty = (y * pa - x * pc) / det - bone.ay
        else:
            x, y = target_x - parent.world_x, target_y - parent.world_y
            det = pa * pd - pb * pc
            if abs(det) <= 0.0001:
                tx = ty = 0.0
            else:
                tx = (x * pd - y * pb) / det - bone.ax
                ty = (y * pa - x * pc) / det - bone.ay

        rotation_ik += math.atan2(ty, tx) * RAD_DEG
        if bone.ascale_x < 0:
            rotation_ik += 180.0
        rotation_ik = _clamp_angle(rotation_ik)

        sx, sy = bone.ascale_x, bone.ascale_y
        if compress or stretch:
            if bone.inherit in ("noScale", "noScaleOrReflection"):
                tx = target_x - bone.world_x
                ty = target_y - bone.world_y
            length = bone.data.get("length", 0.0) * sx
            if length > 0.0001:
                dd = tx * tx + ty * ty
                if (compress and dd < length * length) or (stretch and dd > length * length):
                    scale = (math.sqrt(dd) / length - 1.0) * alpha + 1.0
                    sx *= scale
                    if uniform:
                        sy *= scale
        bone.update_world_transform_with(
            bone.ax, bone.ay, bone.arotation + rotation_ik * alpha,
            sx, sy, bone.ashear_x, bone.ashear_y,
        )

    def _apply_ik2(self, parent: BoneState, child: BoneState, target_x: float,
                   target_y: float, bend_dir: float, stretch: bool,
                   uniform: bool, softness: float, alpha: float) -> None:
        if parent.inherit != "normal" or child.inherit != "normal":
            return
        px, py = parent.ax, parent.ay
        psx, psy = parent.ascale_x, parent.ascale_y
        sx, sy = psx, psy
        csx = child.ascale_x
        os1 = os2 = s2 = 0.0
        if psx < 0:
            psx = -psx
            os1 = 180.0
            s2 = -1.0
        else:
            os1 = 0.0
            s2 = 1.0
        if psy < 0:
            psy = -psy
            s2 = -s2
        if csx < 0:
            csx = -csx
            os2 = 180.0
        else:
            os2 = 0.0

        cx, cy = child.ax, 0.0
        a, b, c, d = parent.a, parent.b, parent.c, parent.d
        uniform_scale = abs(psx - psy) <= 0.0001
        if not uniform_scale or stretch:
            cy = 0.0
            cwx = a * cx + parent.world_x
            cwy = c * cx + parent.world_y
        else:
            cy = child.ay
            cwx = a * cx + b * cy + parent.world_x
            cwy = c * cx + d * cy + parent.world_y

        pp = parent.parent
        if pp is None:
            return
        a, b, c, d = pp.a, pp.b, pp.c, pp.d
        det = a * d - b * c
        x, y = cwx - pp.world_x, cwy - pp.world_y
        det = 0.0 if abs(det) <= 0.0001 else 1.0 / det
        dx = (x * d - y * b) * det - px
        dy = (y * a - x * c) * det - py
        l1 = math.sqrt(dx * dx + dy * dy)
        l2 = child.data.get("length", 0.0) * csx

        if l1 < 0.0001:
            self._apply_ik1(parent, target_x, target_y, False, stretch, False, alpha)
            child.update_world_transform_with(
                cx, cy, 0.0, child.ascale_x, child.ascale_y,
                child.ashear_x, child.ashear_y,
            )
            return

        x = target_x - pp.world_x
        y = target_y - pp.world_y
        tx = (x * d - y * b) * det - px
        ty = (y * a - x * c) * det - py
        dd = tx * tx + ty * ty

        if softness != 0:
            softness *= psx * (csx + 1.0) * 0.5
            td = math.sqrt(dd)
            sd = td - l1 - l2 * psx + softness
            if sd > 0:
                p = min(1.0, sd / (softness * 2.0)) - 1.0
                p = (sd - softness * (1.0 - p * p)) / td
                tx -= p * tx
                ty -= p * ty
                dd = tx * tx + ty * ty

        if uniform_scale:
            l2 *= psx
            cos = (dd - l1 * l1 - l2 * l2) / (2.0 * l1 * l2)
            if cos < -1:
                cos = -1.0
                a2 = PI * bend_dir
            elif cos > 1:
                cos = 1.0
                a2 = 0.0
                if stretch:
                    scale = (math.sqrt(dd) / (l1 + l2) - 1.0) * alpha + 1.0
                    sx *= scale
                    if uniform:
                        sy *= scale
            else:
                a2 = _acos(cos) * bend_dir
            a = l1 + l2 * cos
            b = l2 * math.sin(a2)
            a1 = math.atan2(ty * a - tx * b, tx * a + ty * b)
        else:
            a = psx * l2
            b = psy * l2
            aa, bb = a * a, b * b
            ta = math.atan2(ty, tx)
            c = bb * l1 * l1 + aa * dd - aa * bb
            c1 = -2.0 * bb * l1
            c2 = bb - aa
            d = c1 * c1 - 4.0 * c2 * c
            if d >= 0:
                q = math.sqrt(d)
                if c1 < 0:
                    q = -q
                q = -(c1 + q) * 0.5
                r0 = q / c2 if c2 != 0 else 0.0
                r1 = c / q if q != 0 else 0.0
                r = r0 if abs(r0) < abs(r1) else r1
                r0 = dd - r * r
                if r0 >= 0:
                    y = math.sqrt(r0) * bend_dir
                    a1 = ta - math.atan2(y, r)
                    a2 = math.atan2(y / psy, (r - l1) / psx)
                else:
                    a1 = a2 = None
            else:
                a1 = a2 = None
            if a1 is None:
                min_angle = PI
                min_x = l1 - a
                min_dist = min_x * min_x
                min_y = 0.0
                max_angle = 0.0
                max_x = l1 + a
                max_dist = max_x * max_x
                max_y = 0.0
                c = -a * l1 / (aa - bb) if (aa - bb) != 0 else 2.0
                if -1 <= c <= 1:
                    c = _acos(c)
                    x = a * math.cos(c) + l1
                    y = b * math.sin(c)
                    d = x * x + y * y
                    if d < min_dist:
                        min_angle, min_dist, min_x, min_y = c, d, x, y
                    if d > max_dist:
                        max_angle, max_dist, max_x, max_y = c, d, x, y
                if dd <= (min_dist + max_dist) * 0.5:
                    a1 = ta - math.atan2(min_y * bend_dir, min_x)
                    a2 = min_angle * bend_dir
                else:
                    a1 = ta - math.atan2(max_y * bend_dir, max_x)
                    a2 = max_angle * bend_dir

        os_ = math.atan2(cy, cx) * s2
        rotation = parent.arotation
        a1 = (a1 - os_) * RAD_DEG + os1 - rotation
        a1 = _clamp_angle(a1)
        parent.update_world_transform_with(
            px, py, rotation + a1 * alpha, sx, sy, 0.0, 0.0,
        )
        rotation = child.arotation
        a2 = ((a2 + os_) * RAD_DEG - child.ashear_x) * s2 + os2 - rotation
        a2 = _clamp_angle(a2)
        child.update_world_transform_with(
            cx, cy, rotation + a2 * alpha, child.ascale_x, child.ascale_y,
            child.ashear_x, child.ashear_y,
        )

    # -- path --------------------------------------------------------------

    def _slot_bone(self, name: str) -> BoneState | None:
        """The bone a slot is attached to.

        A path constraint's ``target`` names a SLOT, not a bone — the slot holds
        the path attachment, and its host bone supplies the world transform the
        path is expressed in. Looking the name up as a bone silently fails and
        leaves the chains at the setup pose.
        """
        for slot in self.spine.get("slots", []) or []:
            if slot.get("name") == name:
                return self.bones.get(slot.get("bone"))
        return None

    def _apply_path(self, data: dict) -> None:
        target = self._slot_bone(data.get("target")) or self.bones.get(data.get("target"))
        if target is None:
            return
        attachment = self._path_attachment(data.get("target"))
        if attachment is None:
            return
        mix_rotate = data.get("mixRotate", 1.0)
        mix_x = data.get("mixX", 1.0)
        mix_y = data.get("mixY", 1.0)
        if mix_rotate == 0 and mix_x == 0 and mix_y == 0:
            return
        bones = [self.bones[n] for n in data["bones"] if n in self.bones]
        if not bones:
            return

        rotate_mode = data.get("rotateMode", "chain")
        tangents = rotate_mode == "tangent"
        scale = rotate_mode == "chainScale"
        spacing_mode = data.get("spacingMode", "length")
        spacing = data.get("spacing", 0.0)
        bone_count = len(bones)
        spaces_count = bone_count if tangents else bone_count + 1
        lengths = []
        spaces = [0.0] * spaces_count
        if spacing_mode == "percent":
            if scale:
                for i in range(spaces_count - 1):
                    bone = bones[i]
                    setup_length = bone.data.get("length", 0.0)
                    x, y = setup_length * bone.a, setup_length * bone.c
                    lengths.append(math.sqrt(x * x + y * y))
            for i in range(1, spaces_count):
                spaces[i] = spacing
        elif spacing_mode == "proportional":
            total = 0.0
            i = 0
            while i < spaces_count - 1:
                bone = bones[i]
                setup_length = bone.data.get("length", 0.0)
                if setup_length < 0.000001:
                    if scale:
                        lengths.append(0.0)
                    i += 1
                    spaces[i] = spacing
                else:
                    x, y = setup_length * bone.a, setup_length * bone.c
                    length = math.sqrt(x * x + y * y)
                    if scale:
                        lengths.append(length)
                    i += 1
                    spaces[i] = length
                    total += length
            if total > 0:
                total = spaces_count / total * spacing
                for i in range(1, spaces_count):
                    spaces[i] *= total
        else:
            length_spacing = spacing_mode == "length"
            i = 0
            while i < spaces_count - 1:
                bone = bones[i]
                setup_length = bone.data.get("length", 0.0)
                if setup_length < 0.000001:
                    if scale:
                        lengths.append(0.0)
                    i += 1
                    spaces[i] = spacing
                else:
                    x, y = setup_length * bone.a, setup_length * bone.c
                    length = math.sqrt(x * x + y * y)
                    if scale:
                        lengths.append(length)
                    i += 1
                    spaces[i] = ((setup_length + spacing) if length_spacing else spacing) * length / setup_length

        positions = self._compute_world_positions(
            attachment, data, spaces_count, tangents, spacing_mode, spaces,
        )
        bone_x, bone_y = positions[0], positions[1]
        offset_rotation = data.get("offsetRotation", 0.0)
        if offset_rotation == 0:
            tip = rotate_mode == "chain"
        else:
            tip = False
            p = target
            if p.a * p.d - p.b * p.c > 0:
                offset_rotation *= DEG_RAD
            else:
                offset_rotation *= -DEG_RAD

        p = 3
        for i in range(bone_count):
            bone = bones[i]
            bone.world_x += (bone_x - bone.world_x) * mix_x
            bone.world_y += (bone_y - bone.world_y) * mix_y
            x, y = positions[p], positions[p + 1]
            dx, dy = x - bone_x, y - bone_y
            if scale and i < len(lengths):
                length = lengths[i]
                if length != 0:
                    s = (math.sqrt(dx * dx + dy * dy) / length - 1.0) * mix_rotate + 1.0
                    bone.a *= s
                    bone.c *= s
            bone_x, bone_y = x, y
            if mix_rotate > 0:
                a, b, c, d = bone.a, bone.b, bone.c, bone.d
                if tangents:
                    r = positions[p - 1]
                elif i + 1 < spaces_count and spaces[i + 1] == 0:
                    r = positions[p + 2]
                else:
                    r = math.atan2(dy, dx)
                r -= math.atan2(c, a)
                if tip:
                    cos, sin = math.cos(r), math.sin(r)
                    length = bone.data.get("length", 0.0)
                    bone_x += (length * (cos * a - sin * c) - dx) * mix_rotate
                    bone_y += (length * (sin * a + cos * c) - dy) * mix_rotate
                else:
                    r += offset_rotation
                if r > PI:
                    r -= PI2
                elif r < -PI:
                    r += PI2
                r *= mix_rotate
                cos, sin = math.cos(r), math.sin(r)
                bone.a = cos * a - sin * c
                bone.b = cos * b - sin * d
                bone.c = sin * a + cos * c
                bone.d = sin * b + cos * d
            bone.update_applied_transform()
            p += 3

    def _path_attachment(self, slot_name: str) -> dict | None:
        for skin in self.spine.get("skins", []):
            attachments = skin.get("attachments", {}) if isinstance(skin, dict) else {}
            for name, att in attachments.get(slot_name, {}).items():
                if att.get("type") == "path":
                    return att
        return None

    def _path_world_vertices(self, attachment: dict, target: BoneState,
                             start: int = 2) -> list:
        """Port of ``PathAttachment.computeWorldVertices``.

        ``start`` is a VERTEX index, and the runtime always passes 2 — it skips
        the path's first vertex. That vertex is the start knot the control
        points are relative to, so including it shifts every sampled position
        by one segment (the hero's chains landed ~8 units off).
        """
        raw = attachment.get("vertices", [])
        out = []
        # `vertexCount` is NOT the number of vertices to read — the runtime
        # calls readVertices(map, path, vertexCount << 1) and then walks the
        # whole `vertices` array, treating vertexCount only as the resulting
        # worldVerticesLength. Looping `vertexCount` times stopped at 8 of the
        # path's 15 vertices, so every later Bezier segment was skipped.
        i = 0
        total = len(raw)
        while i < total:
            count = int(raw[i])
            i += 1
            if count == 0:
                x, y = raw[i], raw[i + 1]
                i += 2
                out.extend([target.a * x + target.b * y + target.world_x,
                            target.c * x + target.d * y + target.world_y])
            else:
                x = y = 0.0
                for _ in range(count):
                    bone_index = int(raw[i])
                    bx, by, weight = raw[i + 1], raw[i + 2], raw[i + 3]
                    i += 4
                    bone = self._bone_by_index(bone_index)
                    if bone is None:
                        continue
                    x += (bone.a * bx + bone.b * by + bone.world_x) * weight
                    y += (bone.c * bx + bone.d * by + bone.world_y) * weight
                out.extend([x, y])
        # Returns the FULL vertex list. The runtime's `start=2` slice applies
        # only when filling its world buffer; `worldVerticesLength` (and thus
        # the curve count) is derived from the unsliced list, so slicing here
        # would lose a curve.
        return out

    def _bone_by_index(self, index: int) -> BoneState | None:
        bones = self.spine["bones"]
        if 0 <= index < len(bones):
            return self.bones.get(bones[index]["name"])
        return None

    def _compute_world_positions(self, attachment, data, spaces_count,
                                 tangents, spacing_mode, spaces) -> list:
        """Port of ``PathConstraint.computeWorldPositions`` (constant speed)."""
        target = self._slot_bone(data.get("target")) or self.bones[data["target"]]
        position = data.get("position", 0.0)
        closed = bool(attachment.get("closed", False))
        # SkeletonJson defaults constantSpeed to TRUE (not false): the exporter
        # only writes the flag when it is off, so an absent key means the
        # accurate arc-length path, and assuming false silently samples the
        # wrong branch and lands every chain bone ~0.76 units off.
        constant_speed = bool(attachment.get("constantSpeed", True))
        # `worldVerticesLength` counts the FULL vertex list; the runtime slices
        # off the first vertex (start=2) only when filling `world`. Counting
        # curves from the already-sliced array loses one curve and samples the
        # wrong Bezier segment.
        world = self._path_world_vertices(attachment, target)
        vertices_length = len(world)
        out = [0.0] * (spaces_count * 3 + 2)

        curve_count = vertices_length // 6
        if not constant_speed:
            lengths = attachment.get("lengths", []) or []
            curve_count -= 1 if closed else 2
            path_length = lengths[curve_count] if curve_count < len(lengths) else 0.0
            if data.get("positionMode", "percent") == "percent":
                position *= path_length
            if spacing_mode == "percent":
                multiplier = path_length
            elif spacing_mode == "proportional":
                multiplier = path_length / spaces_count
            else:
                multiplier = 1.0
            prev_curve = -1
            for i in range(spaces_count):
                o = i * 3
                space = spaces[i] * multiplier
                position += space
                p = position
                if closed:
                    p %= path_length
                    if p < 0:
                        p += path_length
                    curve = 0
                elif p < 0:
                    self._add_before(p, world, 0, out, o)
                    continue
                elif p > path_length:
                    self._add_after(p - path_length, world, vertices_length - 4, out, o)
                    continue
                curve = 0
                while curve < curve_count:
                    length = lengths[curve]
                    if p <= length:
                        if curve == 0:
                            p /= length
                        else:
                            prev = lengths[curve - 1]
                            p = (p - prev) / (length - prev)
                        break
                    curve += 1
                self._add_curve_position(
                    p, world, curve, out, o,
                    tangents or (i > 0 and space == 0),
                )
            return out

        # constant speed: walk the curve in 8 segments per bezier
        if closed:
            vertices_length += 2
            world = world + world[:2]
        else:
            curve_count -= 1
            vertices_length -= 4
            # The runtime fills a fresh buffer of `verticesLength` floats from
            # source index 2 — i.e. drop the first vertex and keep the rest
            # contiguously. `world[2:]` then truncate is NOT the same: it
            # shortens the run by one vertex, skipping a Bezier segment.
            world = world[2:2 + vertices_length]
        curves = []
        path_length = 0.0
        x1, y1 = world[0], world[1]
        for i in range(curve_count):
            w = 2 + i * 6
            cx1, cy1 = world[w], world[w + 1]
            cx2, cy2 = world[w + 2], world[w + 3]
            x2, y2 = world[w + 4], world[w + 5]
            tmpx = (x1 - cx1 * 2 + cx2) * 0.1875
            tmpy = (y1 - cy1 * 2 + cy2) * 0.1875
            dddfx = ((cx1 - cx2) * 3 - x1 + x2) * 0.09375
            dddfy = ((cy1 - cy2) * 3 - y1 + y2) * 0.09375
            ddfx = tmpx * 2 + dddfx
            ddfy = tmpy * 2 + dddfy
            dfx = (cx1 - x1) * 0.75 + tmpx + dddfx * 0.16666667
            dfy = (cy1 - y1) * 0.75 + tmpy + dddfy * 0.16666667
            path_length += math.sqrt(dfx * dfx + dfy * dfy)
            dfx += ddfx
            dfy += ddfy
            ddfx += dddfx
            ddfy += dddfy
            path_length += math.sqrt(dfx * dfx + dfy * dfy)
            dfx += ddfx
            dfy += ddfy
            path_length += math.sqrt(dfx * dfx + dfy * dfy)
            dfx += ddfx + dddfx
            dfy += ddfy + dddfy
            path_length += math.sqrt(dfx * dfx + dfy * dfy)
            curves.append(path_length)
            x1, y1 = x2, y2

        if data.get("positionMode", "percent") == "percent":
            position *= path_length
        if spacing_mode == "percent":
            multiplier = path_length
        elif spacing_mode == "proportional":
            multiplier = path_length / spaces_count
        else:
            multiplier = 1.0

        for i in range(spaces_count):
            o = i * 3
            space = spaces[i] * multiplier
            position += space
            p = position
            if closed:
                p %= path_length
                if p < 0:
                    p += path_length
                curve = 0
            elif p < 0:
                self._add_before(p, world, 0, out, o)
                continue
            elif p > path_length:
                self._add_after(p - path_length, world, vertices_length - 4, out, o)
                continue
            curve = 0
            while curve < curve_count:
                length = curves[curve]
                if p <= length:
                    if curve == 0:
                        p /= length
                    else:
                        prev = curves[curve - 1]
                        p = (p - prev) / (length - prev)
                    break
                curve += 1
            self._add_curve_position(
                p, world, curve, out, o,
                tangents or (i > 0 and space == 0),
            )
        return out

    @staticmethod
    def _add_curve_position(p, world, curve, out, o, tangents) -> None:
        """Port of ``PathConstraint.addCurvePosition``.

        Evaluates the cubic Bezier at the normalised arc position ``p``. The
        runtime's forward-differencing elsewhere is only for measuring arc
        length; the position itself is this closed form.
        """
        w = curve * 6
        x1, y1 = world[w], world[w + 1]
        cx1, cy1 = world[w + 2], world[w + 3]
        cx2, cy2 = world[w + 4], world[w + 5]
        x2, y2 = world[w + 6], world[w + 7]

        if p == 0 or p != p:  # p == 0 or NaN
            out[o] = x1
            out[o + 1] = y1
            out[o + 2] = math.atan2(cy1 - y1, cx1 - x1)
            return

        tt = p * p
        ttt = tt * p
        u = 1.0 - p
        uu = u * u
        uuu = uu * u
        ut = u * p
        ut3 = ut * 3.0
        uut3 = u * ut3
        utt3 = ut3 * p
        out[o] = x1 * uuu + cx1 * uut3 + cx2 * utt3 + x2 * ttt
        out[o + 1] = y1 * uuu + cy1 * uut3 + cy2 * utt3 + y2 * ttt
        if tangents:
            if p < 0.001:
                out[o + 2] = math.atan2(cy1 - y1, cx1 - x1)
            else:
                out[o + 2] = math.atan2(
                    out[o + 1] - (y1 * uu + cy1 * ut * 2.0 + cy2 * tt),
                    out[o] - (x1 * uu + cx1 * ut * 2.0 + cx2 * tt),
                )

    @staticmethod
    def _add_before(position, world, index, out, o) -> None:
        x1, y1 = world[index], world[index + 1]
        x2, y2 = world[index + 2], world[index + 3]
        dx, dy = x2 - x1, y2 - y1
        length = math.sqrt(dx * dx + dy * dy)
        if length == 0:
            out[o] = x1
            out[o + 1] = y1
            return
        out[o] = x1 + dx / length * position
        out[o + 1] = y1 + dy / length * position
        out[o + 2] = math.atan2(dy, dx)

    @staticmethod
    def _add_after(position, world, index, out, o) -> None:
        x1, y1 = world[index], world[index + 1]
        x2, y2 = world[index + 2], world[index + 3]
        dx, dy = x2 - x1, y2 - y1
        length = math.sqrt(dx * dx + dy * dy)
        if length == 0:
            out[o] = x1
            out[o + 1] = y1
            return
        out[o] = x2 + dx / length * position
        out[o + 1] = y2 + dy / length * position
        out[o + 2] = math.atan2(dy, dx)


def bake_constraints(spine: dict) -> dict:
    """Bone name -> (x, y, rotation_deg) with IK and path constraints applied.

    Returns the constrained local pose in Spine space, ready to be written as
    animation keys. Bones the constraints do not touch keep their setup values.
    """
    solver = ConstraintSolver(spine)
    solver.apply()
    return solver.local_poses()


def _sample_pose(animation: dict, time: float, bones: dict) -> dict:
    """Local (x, y, rotation) for every bone at ``time``.

    Keys are linear here: Spine beziers were already baked into linear key
    tables (see README), so interpolating between neighbours reproduces the
    runtime's sample exactly.
    """
    pose = {}
    for bone_name, props in (animation.get("bones") or {}).items():
        setup = bones.get(bone_name) or {}
        x = setup.get("x", 0.0)
        y = setup.get("y", 0.0)
        rotation = setup.get("rotation", 0.0)

        rotate = props.get("rotate")
        if rotate:
            rotation = setup.get("rotation", 0.0) + _lerp_key(rotate, time, "value", 0.0)
        translate = props.get("translate")
        if translate:
            x = setup.get("x", 0.0) + _lerp_key(translate, time, "x", 0.0)
            y = setup.get("y", 0.0) + _lerp_key(translate, time, "y", 0.0)
        pose[bone_name] = (x, y, rotation)
    return pose


def _lerp_key(keys: list, time: float, field: str, default: float) -> float:
    """Sample one key channel at ``time``, honouring Spine bezier curves.

    Spine keys may carry a ``curve`` (two control points per axis). Ignoring it
    and interpolating linearly bakes a straight line where the source eases —
    the hero's head drifted 0.24 units mid-segment. The curve is expanded with
    the same 10-point forward-difference table the runtime uses
    (``bezier_table_point``), so the sampled pose matches it exactly.
    """
    if not keys:
        return default
    if time <= keys[0].get("time", 0.0):
        return keys[0].get(field, default)
    for index in range(1, len(keys)):
        previous, current = keys[index - 1], keys[index]
        start, end = previous.get("time", 0.0), current.get("time", 0.0)
        if time > end:
            continue
        if end <= start:
            return current.get(field, default)
        a, b = previous.get(field, default), current.get(field, default)
        curve = previous.get("curve")
        if not isinstance(curve, (list, tuple)) or len(curve) < 4:
            ratio = (time - start) / (end - start)
            return a + (b - a) * ratio
        # Spine stores one curve per axis: 4 values for rotate, 8 for
        # translate (x then y). Axis 0 is always the TIME curve; the value
        # curve is the axis matching the field. Sampling axis 0 for the value
        # returns the time component and eases the wrong way.
        axis = {"x": 0, "y": 1}.get(field, 0)
        table = [_bezier_table(curve, axis, step, start, end, a, b)
                 for step in range(11)]
        for step in range(10):
            x0, y0 = table[step]
            x1, y1 = table[step + 1]
            if time <= x1 or step == 9:
                span = x1 - x0
                ratio = 0.0 if span <= 0 else (time - x0) / span
                return y0 + (y1 - y0) * ratio
        return b
    return keys[-1].get(field, default)


def _bezier_table(curve: list, axis: int, step: int, time1: float,
                  time2: float, value1: float, value2: float) -> tuple:
    """One entry of the runtime's 10-point bezier table.

    ``axis`` selects the VALUE curve (0 for x/rotate, 1 for y); the TIME curve
    is always axis 0 when a value axis exists. Returns (time, value).
    """
    from .out_spine import bezier_table_point
    time_point = bezier_table_point(curve, 0, step, time1, time2, value1, value2)
    if axis == 0:
        return time_point
    value_point = bezier_table_point(curve, axis, step, time1, time2, value1, value2)
    return (time_point[0], value_point[1])


def bake_animation(spine: dict, animation_name: str, skin: str | None = None) -> dict:
    """Bone name -> {rotate: [...], translate: [...]} with constraints baked.

    Samples the animation at every key time of the constrained bones and their
    descendants, solves the constraints at each sample, and returns the
    resulting local transforms as Spine-space keys. Only the bones a constraint
    (or its descendants) actually moves are returned; everything else keeps the
    keys the animation already had.
    """
    animation = (spine.get("animations") or {}).get(animation_name)
    if animation is None:
        return {}

    setup_bones = {b["name"]: b for b in spine["bones"]}
    children: dict[str, list] = {}
    for bone in spine["bones"]:
        parent = bone.get("parent")
        if parent:
            children.setdefault(parent, []).append(bone["name"])

    constrained = set()
    for constraint in (spine.get("ik") or []) + (spine.get("path") or []):
        constrained.update(constraint.get("bones") or [])

    # A constraint moves its own bones and everything hanging off them.
    affected = set()
    stack = list(constrained)
    while stack:
        name = stack.pop()
        if name in affected:
            continue
        affected.add(name)
        stack.extend(children.get(name, []))

    if not affected:
        return {}

    # Sample times: every key time in the animation PLUS a uniform grid over
    # the animation's span. A constraint is a nonlinear function of the pose,
    # so baking only at the source's key times loses the curvature between
    # them (the hero's shins drifted ~0.85 units mid-segment). The grid keeps
    # the error well inside the 0.01 tolerance without exploding the key count.
    times = {0.0}
    duration = 0.0
    for props in (animation.get("bones") or {}).values():
        for channel in ("rotate", "translate", "scale"):
            for key in props.get(channel) or []:
                time = key.get("time", 0.0)
                times.add(time)
                duration = max(duration, time)
    if duration > 0:
        steps = max(1, int(duration / 0.002))
        for index in range(steps + 1):
            times.add(duration * index / steps)
    times = sorted(times)

    samples = {name: {"rotate": [], "translate": []} for name in affected}
    inactive = set()
    for time in times:
        pose = _sample_pose(animation, time, setup_bones)
        solver = ConstraintSolver(spine, skin=skin, pose=pose)
        solver.apply()
        # A bone the active skin deactivates is not transformed by the runtime
        # at all, so emitting baked keys for it would ANIMATE what the source
        # engine leaves at rest — the hero's unequipped weapons and their
        # chains, which showed up as a 228-unit divergence.
        for name in affected:
            state = solver.bones.get(name)
            if state is None or not solver.active.get(name, True):
                inactive.add(name)
                continue
            setup = setup_bones.get(name) or {}
            # Emit offsets from setup, which is what Spine keys carry.
            samples[name]["rotate"].append(
                {"time": time, "value": state.arotation - setup.get("rotation", 0.0)}
            )
            samples[name]["translate"].append({
                "time": time,
                "x": state.ax - setup.get("x", 0.0),
                "y": state.ay - setup.get("y", 0.0),
            })

    for name in inactive:
        samples.pop(name, None)

    out = {}
    for name, channels in samples.items():
        entry = {}
        if channels["rotate"]:
            entry["rotate"] = _simplify(channels["rotate"], "value")
        if channels["translate"]:
            entry["translate"] = _simplify(channels["translate"], ("x", "y"))
        if entry:
            out[name] = entry
    return out


def _simplify(keys: list, fields, tolerance: float = 0.001) -> list:
    """Drop keys that lie on the line between their neighbours.

    A dense sample grid is needed to capture a constraint's curvature, but most
    of the resulting keys are collinear and waste space — the hero baked to
    4.6 MB before this. This is Ramer-Douglas-Peucker per channel: keep a key
    only when removing it would move the curve by more than ``tolerance``,
    which is well inside the 0.01 validation threshold.
    """
    if isinstance(fields, str):
        fields = (fields,)
    if len(keys) <= 2:
        return keys

    def deviation(index: int, start: int, end: int) -> float:
        span = keys[end]["time"] - keys[start]["time"]
        worst = 0.0
        for field in fields:
            a = keys[start].get(field, 0.0)
            b = keys[end].get(field, 0.0)
            if span <= 0:
                expected = a
            else:
                ratio = (keys[index]["time"] - keys[start]["time"]) / span
                expected = a + (b - a) * ratio
            worst = max(worst, abs(keys[index].get(field, 0.0) - expected))
        return worst

    keep = [0, len(keys) - 1]
    stack = [(0, len(keys) - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        worst_index, worst = -1, tolerance
        for index in range(start + 1, end):
            value = deviation(index, start, end)
            if value > worst:
                worst_index, worst = index, value
        if worst_index >= 0:
            keep.append(worst_index)
            stack.append((start, worst_index))
            stack.append((worst_index, end))
    return [keys[index] for index in sorted(set(keep))]


def unsupported_constraints(spine: dict) -> list:
    """Constraint kinds this module cannot bake, for honest reporting."""
    out = []
    for constraint in spine.get("transform", []) or []:
        out.append(("transform", constraint.get("name", "?")))
    for bone in spine.get("bones", []):
        if bone.get("physics"):
            out.append(("physics", bone["name"]))
    return out
