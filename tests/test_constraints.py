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

from src.constraints import ConstraintSolver, bake_animation, unsupported_constraints


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
    """A bone the active skin deactivates is not transformed by the runtime.

    The hero's weapons and chains are ``skin: true``; under the default skin
    the engine leaves them at (0, 0). Baking keys for them would animate what
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
    assert (solver.bones["chain1"].world_x, solver.bones["chain1"].world_y) == (0.0, 0.0)

    baked = bake_animation(rig, "swing", skin="default")
    assert "chain1" not in baked


def test_unsupported_constraints_are_reported():
    """Transform and physics constraints are not baked — say so, do not hide it."""
    rig = _two_bone_ik_rig()
    rig["transform"] = [{"name": "squash"}]
    rig["bones"].append({"name": "jiggle", "parent": "hip", "physics": {"inertia": 1}})

    reported = unsupported_constraints(rig)
    assert ("transform", "squash") in reported
    assert ("physics", "jiggle") in reported
