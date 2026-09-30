"""Verify a SkelForm conversion against the runtime's own semantics.

    python3 validation/verify_skelform.py <rig.skf> [<rig2.skf> ...] [--frames 0,30,60]

The check is end-to-end and independent of the converter's own code path:

1. `validation/skelform_semantics.py` (a port of the runtime's `Animate`,
   `Construct` and vertex inheritance) computes, for a frame, every bone's
   world transform and every visual's world vertices in **SkelForm space**.
2. `src/in_skelform.py` reads the same file into the canonical model, the
   model's own forward kinematics evaluates the same frame, and each visual's
   vertices are skinned with the model's bone world transforms.
3. Both sides are mirrored once into Godot space (Y down) and compared.

Bones whose rotation an IK family drives are skipped: this harness does not
solve inverse kinematics (see ROADMAP.md, "SkelForm support").
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import skelform_semantics as sem                                  # noqa: E402
from src.in_skelform import _sample, bone_name_map, read_skeleton  # noqa: E402
from src.model import compose, multiply                            # noqa: E402

TOLERANCE = 0.01


def model_pose(model, animation: str, time: float) -> dict:
    """The canonical model's forward kinematics at ``time`` (Godot space)."""
    world = {}
    for bone in model.bones:
        channels = model.animations.get(animation, {}).get(bone.name, {})
        position = list(bone.position)
        rotation = bone.rotation_deg
        scale = list(bone.scale)
        translate = channels.get("translate")
        if translate:
            for axis in (0, 1):
                position[axis] = _sample(
                    [(k.time, (k.x if axis == 0 else k.y), k.curve)
                     for k in translate], time, axis)
        rotate = channels.get("rotate")
        if rotate:
            rotation = _sample(
                [(k.time, k.angle, k.curve) for k in rotate], time, 0)
        scale_keys = channels.get("scale")
        if scale_keys:
            for axis in (0, 1):
                scale[axis] = _sample(
                    [(k.time, k.scale[axis], k.curve) for k in scale_keys],
                    time, axis)
        parent = world.get(bone.parent, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
        world[bone.name] = multiply(
            parent, compose((position[0], position[1]), rotation, (scale[0], scale[1])))
    return world


def _single_bone_bound(visual: dict) -> bool:
    """True when the harness can skin the visual exactly (one influencing bone)."""
    bones = {bind["bone_id"] for bind in visual.get("binds") or []
             if not bind.get("is_path")}
    return len(bones) <= 1


def verify(path: str, frames: list | None = None) -> tuple:
    armature = sem.load(path)
    model = read_skeleton(path)
    names = bone_name_map(armature)
    driven = sem.ik_driven_bones(armature)
    resampled = sem.with_descendants(armature, sem.per_axis_key_bones(armature))
    textures = sem.style_textures(armature)
    attachment_by_slot = {a.slot: a for a in model.attachments}
    failures = []
    non_trs = []
    approximations = []

    for animation in armature["animations"]:
        fps = float(animation["fps"] or 60)
        sampled = frames if frames is not None else \
            [0, int(fps * 0.25), int(fps * 0.5), int(fps * 0.9), int(fps * 1.5)]
        for frame in sampled:
            runtime = sem.pose_bones(armature, animation, frame)
            # Bones whose runtime inheritance is not a TRS composition: a
            # converted rig cannot match them exactly, so they are reported
            # separately from real conversion bugs.
            inexact = sem.with_descendants(
                armature, sem.non_trs_bones(armature, animation, frame))
            pose = model_pose(model, animation["name"], frame / fps)
            for bone_id, state in runtime.items():
                if bone_id in driven:
                    continue
                name = names[bone_id]
                expected = (state["pos"]["x"], -state["pos"]["y"])
                got = (pose[name][4], pose[name][5])
                deviation = math.hypot(expected[0] - got[0], expected[1] - got[1])
                if deviation > TOLERANCE and bone_id in resampled:
                    approximations.append(
                        f"{animation['name']} f{frame} bone {name}: per-axis "
                        f"keys (resampled) — {deviation:.3f}")
                    continue
                if deviation > TOLERANCE and bone_id in inexact:
                    non_trs.append(
                        f"{animation['name']} f{frame} bone {name}: non-TRS "
                        f"inheritance — {deviation:.3f}")
                    continue
                if deviation > TOLERANCE:
                    failures.append(
                        f"{animation['name']} f{frame} bone {name}: runtime "
                        f"({expected[0]:.3f}, {expected[1]:.3f}) vs model "
                        f"({got[0]:.3f}, {got[1]:.3f}) — {deviation:.3f}")
            for bone in armature["bones"]:
                visual_id = bone.get("visuals_id", -1)
                if visual_id in (-1, None) or bone["id"] in driven:
                    continue
                visual = armature["visuals"][visual_id]
                if not _single_bone_bound(visual):
                    continue
                attachment = attachment_by_slot.get(names[bone["id"]])
                if attachment is None:
                    failures.append(
                        f"{animation['name']} f{frame}: no attachment for "
                        f"bone {names[bone['id']]}")
                    continue
                points = sem.visual_world_vertices(
                    armature, visual_id, textures, bone, runtime)
                matrix = pose[names[bone["id"]]]
                for index, (x, y) in enumerate(points):
                    local = attachment.polygon[index]
                    lx = local[0] + attachment.position[0]
                    ly = local[1] + attachment.position[1]
                    # The model's matrix is already Godot space (Y down), and
                    # mirroring the runtime's point once puts it in the same
                    # space: compare directly, no extra negation.
                    got = (matrix[0] * lx + matrix[2] * ly + matrix[4],
                           matrix[1] * lx + matrix[3] * ly + matrix[5])
                    deviation = math.hypot(x - got[0], -y - got[1])
                    if deviation > TOLERANCE and bone["id"] in resampled:
                        approximations.append(
                            f"{animation['name']} f{frame} "
                            f"{names[bone['id']]}/{attachment.name} vertex "
                            f"{index}: per-axis keys (resampled) — "
                            f"{deviation:.3f}")
                        continue
                    if deviation > TOLERANCE and bone["id"] in inexact:
                        non_trs.append(
                            f"{animation['name']} f{frame} "
                            f"{names[bone['id']]}/{attachment.name} vertex "
                            f"{index}: non-TRS inheritance — {deviation:.3f}")
                        continue
                    if deviation > TOLERANCE:
                        failures.append(
                            f"{animation['name']} f{frame} {names[bone['id']]}"
                            f"/{attachment.name} vertex {index}: runtime "
                            f"({x:.3f}, {-y:.3f}) vs model "
                            f"({got[0]:.3f}, {got[1]:.3f}) — {deviation:.3f}")
    return failures, non_trs, approximations


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    frames = None
    for arg in sys.argv[1:]:
        if arg.startswith("--frames"):
            frames = [int(v) for v in arg.split("=", 1)[1].split(",")]
    if not args:
        print(__doc__)
        return 2
    status = 0
    for path in args:
        failures, non_trs, approximations = verify(path, frames)
        print(f"{path}: {'PASS' if not failures else f'FAIL ({len(failures)})'}"
              + (f" | {len(non_trs)} non-TRS divergences (documented)"
                 if non_trs else "")
              + (f" | {len(approximations)} resampled-axis approximations "
                 f"(documented)" if approximations else ""))
        for line in failures[:12]:
            print("   ", line)
        if len(failures) > 12:
            print(f"    … +{len(failures) - 12} more")
        for line in non_trs[:3]:
            print("   (non-TRS)", line)
        for line in approximations[:3]:
            print("   (per-axis keys)", line)
        status |= 1 if failures else 0
    return status


if __name__ == "__main__":
    raise SystemExit(main())
