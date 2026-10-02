"""Constraint baking: IK and path solvers must match the official runtime.

Godot has no IK/path constraints, so src/constraints.py ports the solvers from
spine-core 4.2 and bakes their result into ordinary animation keys. These tests
pin the solver against numbers produced by the real runtime — a port that
drifts silently would produce a rig that loads and looks plausible while every
constrained bone is in the wrong place.

The reference values come from ``validation/constraint-reference.mjs``, which
loads a rig with the official runtime and prints the world transforms after
``updateWorldTransform``. Regenerate them by running that script; do not edit
them by hand.
"""
import math

from src.constraints import (ConstraintSolver, _sample_pose, bake_animation,
                             unsupported_constraints)


def _two_bone_ik_rig() -> dict:
    """A minimal 2-bone IK chain reaching for a target, in Spine's own schema."""
    return {
        "skeleton": {"spine": "4.2.22"},
        "bones": [
            {"name": "root"},
            {"name": "hip", "parent": "root", "x": 0, "y": 100, "length": 40},
            {"name": "thigh", "parent": "hip", "x": 0, "y": 0, "length": 40,
             "rotation": -80},
            {"name": "shin", "parent": "thigh", "x": 40, "y": 0, "length": 40,
             "rotation": 60},
            {"name": "foot", "parent": "shin", "x": 40, "y": 0, "length": 20,
             "rotation": 20},
            {"name": "target", "parent": "root", "x": 30, "y": 20},
        ],
        "ik": [{"name": "leg", "bones": ["thigh", "shin"], "target": "target",
                "bendPositive": True}],
        "skins": [{"name": "default"}],
        "animations": {},
    }


# spine-core 4.2, after updateWorldTransform(Physics.update).
_RUNTIME_WORLD = {
    "thigh": (-0.000002, 100.0),
    "shin": (14.044936, 62.546833),
    "foot": (28.089875, 25.093665),
}


def test_two_bone_ik_matches_the_runtime():
    """The solver reproduces the engine's IK placement."""
    solver = ConstraintSolver(_two_bone_ik_rig(), skin="default")
    solver.apply()

    for name, (expected_x, expected_y) in _RUNTIME_WORLD.items():
        state = solver.bones[name]
        deviation = math.hypot(state.world_x - expected_x, state.world_y - expected_y)
        assert deviation < 0.01, f"{name} deviates {deviation}"


def test_a_bone_below_the_constrained_chain_follows_it():
    """Descendants of an IK bone must move with it.

    The runtime walks every bone after the constraints run. Skipping that
    leaves a bone's world transform stale — the chain solves, but the foot
    below it stays where the setup pose put it.
    """
    solver = ConstraintSolver(_two_bone_ik_rig(), skin="default")
    solver.apply()

    # foot is a child of shin, so it must not still sit at its setup position.
    foot = solver.bones["foot"]
    setup_world = solver.bones["shin"].parent
    assert setup_world is not None
    expected_x, expected_y = _RUNTIME_WORLD["foot"]
    assert math.hypot(foot.world_x - expected_x, foot.world_y - expected_y) < 0.01


def test_ik_with_mix_zero_is_inactive():
    """A constraint with mix 0 must not move anything.

    The hero rig ships an IK constraint with ``mix: 0``; running it anyway
    rotates a bone the source engine leaves alone.
    """
    rig = _two_bone_ik_rig()
    rig["ik"][0]["mix"] = 0
    solver = ConstraintSolver(rig, skin="default")
    solver.apply()

    thigh = solver.bones["thigh"]
    assert math.isclose(thigh.arotation, -80.0, abs_tol=1e-6)


def test_skin_required_bone_is_inactive_and_keeps_no_baked_keys():
    """A bone the active skin deactivates is not DRAWN, but it still has a pose.

    The hero's weapons and chains are ``skin: true``; under the default skin
    the engine skips them, and its world transform stays where it was — a
    stale (0, 0) that is a runtime artifact, not rig data. The rig itself
    still says where the bone is (here: the chain hangs off ``hip``), and a
    converter has to carry that or the attachment is a degenerate quad no
    viewer can equip. Baking keys for it stays out: that WOULD animate what
    the source does not, which showed up as a 228-unit divergence.
    """
    rig = _two_bone_ik_rig()
    rig["bones"].append({"name": "chain1", "parent": "hip", "x": 8, "length": 8,
                         "skin": True})
    rig["skins"] = [{"name": "default"}, {"name": "weapon", "bones": []}]
    rig["animations"] = {"swing": {"bones": {}}}

    solver = ConstraintSolver(rig, skin="default")
    solver.apply()
    assert not solver.active["chain1"], "skin-required bone should be inactive"
    hip = solver.bones["hip"]
    chain = solver.bones["chain1"]
    assert math.isclose(chain.world_x, hip.world_x + 8.0, abs_tol=1e-9)
    assert math.isclose(chain.world_y, hip.world_y, abs_tol=1e-9)

    baked = bake_animation(rig, "swing", skin="default")
    assert "chain1" not in baked


def _rotation_rig() -> dict:
    """A chain whose last bone refuses its parent's rotation.

    ``noRotationOrReflection`` builds the bone's world from its own rotation,
    cancelling the parent's instead of inheriting it — the hero's feet are the
    real-world case.
    """
    return {
        "skeleton": {"spine": "4.2.22"},
        "bones": [
            {"name": "root"},
            {"name": "shin", "parent": "root", "length": 40, "rotation": 30},
            {"name": "foot", "parent": "shin", "x": 40, "length": 20,
             "rotation": 10, "inherit": "noRotationOrReflection"},
        ],
        "skins": [{"name": "default"}],
        "animations": {"walk": {"bones": {
            "shin": {"rotate": [{"time": 0.0, "value": 0.0},
                                {"time": 1.0, "value": 70.0}]},
            "foot": {"rotate": [{"time": 0.0, "value": 0.0},
                                {"time": 1.0, "value": -25.0}]},
        }}},
    }


def _baked_value(baked: dict, name: str, time: float) -> float:
    """Sample a baked channel the way the runtime does: linear between keys."""
    keys = (baked.get(name) or {}).get("rotate") or []
    if not keys:
        return 0.0
    if time <= keys[0]["time"]:
        return keys[0]["value"]
    for before, after in zip(keys, keys[1:]):
        if time <= after["time"]:
            span = after["time"] - before["time"]
            if span <= 0:
                return after["value"]
            ratio = (time - before["time"]) / span
            return before["value"] + (after["value"] - before["value"]) * ratio
    return keys[-1]["value"]


def test_a_bone_that_ignores_its_parents_rotation_bakes_under_normal_inheritance():
    """The file has ONE inheritance mode, so the bake must cancel the parent.

    ``noRotationOrReflection`` gives the bone its own world rotation whatever
    the parent does, and the source's local carries exactly that. Stored
    verbatim, the file — which only has normal inheritance — adds the parent's
    rotation on top: the hero's feet rode a whole shin rotation (104.6 degrees)
    off the source, on every animated frame. The baked keys must reproduce the
    SOURCE's world rotation.
    """
    rig = _rotation_rig()
    setup = {bone["name"]: bone for bone in rig["bones"]}
    baked = bake_animation(rig, "walk", skin="default")
    assert "foot" in baked, baked

    for time in (0.0, 0.2, 0.5, 0.8, 1.0):
        pose = _sample_pose(rig["animations"]["walk"], time, setup)
        solver = ConstraintSolver(rig, skin="default", pose=pose)
        solver.apply()
        source = math.degrees(math.atan2(solver.bones["foot"].c,
                                         solver.bones["foot"].a))
        # The file accumulates its locals, one per bone; the frame mirrors y, so
        # the file's world rotation is the negative of the source-space locals
        # it stores summed along the chain. A bone the bake did not touch keeps
        # the animation's own key, which is why the pose is sampled too.
        ours = 0.0
        for name in ("root", "shin", "foot"):
            if name in baked:
                ours += setup[name].get("rotation", 0.0) \
                    + _baked_value(baked, name, time)
            else:
                ours += pose.get(name, (0.0, 0.0,
                                        setup[name].get("rotation", 0.0)))[2]
        off = ((source - ours + 540.0) % 360.0) - 180.0
        assert abs(off) < 0.5, f"t={time}: source {source:.3f} vs file {ours:.3f}"


def test_unsupported_constraints_are_reported():
    """Transform and physics constraints are not baked — say so, do not hide it."""
    rig = _two_bone_ik_rig()
    rig["transform"] = [{"name": "squash"}]
    rig["bones"].append({"name": "jiggle", "parent": "hip", "physics": {"inertia": 1}})

    reported = unsupported_constraints(rig)
    assert ("transform", "squash") in reported
    assert ("physics", "jiggle") in reported
