# skeletonconverter

Convert 2D skeletal animation rigs between engines. Reads one format, writes
another, and proves the conversion numerically instead of trusting it by eye.

## Status

Working: Godot Skeleton2D scenes (.tscn) → Spine JSON. More formats planned via
a canonical in-memory model — see [Architecture](#architecture).

## Why

Spine exports a neutral JSON that many runtimes consume. Godot rigs are locked
in `.tscn`. The bridge lets a rig authored in either tool reach the other —
and because a silent coordinate mistake produces a rig that *looks* plausible
but is wrong, the tool validates conversions numerically against the real
engines instead of trusting inspection.

## Quick start

```bash
# Godot scene -> Spine JSON (writes out.json + out.atlas)
python3 skeletonconverter convert --to spine \
  path/to/player.tscn -o out.json

# Spine JSON -> Godot scene (requires the .atlas beside the input JSON)
python3 skeletonconverter convert --to godot \
  path/to/hero.json -o out.tscn --texture res://player/gBot.png
```

Zero third-party dependencies for the converter — Python 3.10+ stdlib only.

## The coordinate contract

Getting this wrong is the entire risk of skeletal conversion. These are the
rules, stated once, relied on everywhere. They were each discovered as a bug
that produced a plausible-looking but wrong rig.

### Axis convention

Godot 2D is **Y-down**; Spine is **Y-up**. Mirroring with `F = diag(1, -1)`
maps the whole tree consistently:

```
spine.x = godot.x
spine.y = -godot.y
spine.rotation = -godot.rotation
local vertex y negated
```

Proof sketch: with `F·M·F` applied per bone, `F·T(a,b)·F = T(a,-b)` and
`F·R(θ)·F = R(-θ)`, so the composed world matrix maps as
`W_godot = F·W_spine·F` and any point with local coords `F·p` lands at
`F·(world in Spine)`.

### Godot `Transform2D` column convention

Godot's own column order is `x = (cos, sin)` and `y = (-sin, cos)` — the
opposite of the right-handed convention a converter naturally writes. Using the
wrong one shears every skinned vertex, because Godot skins with
`accum · rest.inverse()`. The engine's own scenes are the reference:

```
rest = Transform2D(0.3366, 0.9416, -0.9416, 0.3366, ...)  # 70.3° bone
```

### Absolute vs offset animation values

Godot `Animation` tracks hold **absolute** local transforms. Spine `rotate` and
`translate` keys are **offsets from the bone's setup pose**. Converting
Godot→Spine subtracts the bone's setup value; Spine→Godot adds it back.
Skipping this makes the rig land in a plausible but wrong pose at every frame.

### Mesh vertices go in skeleton space, not bone space

Godot skins mesh vertices as `bone_world · rest.inverse() · vertex`, so at rest
a vertex must already sit at its rest world position in skeleton space. Emitting
bone-local coordinates (with a node `position` compensating) looks plausible
and renders nothing.

### `Polygon2D.bones` paths

Godot resolves the paths relative to the `Skeleton2D` and drops entries
silently when they do not resolve. They must be the full path
(`root/hip/body`), not the bare bone name.

### Region quad corner order

The runtime's `RegionAttachment.computeUVs` orders corners as bottom-left,
top-left, top-right, bottom-right in Spine's Y-up local space. Any other
winding mirrors the quad.

### Mesh UVs come from the atlas

Spine mesh `uvs` are normalized within the texture region, not the page. The
region's pixels live at its atlas rect — and the **region** name can differ
from the **slot** name (`upper-arm2` vs `upperarm2`). Regions with
`rotate: 90` store pixels transposed; the runtime permutes the UV axes, so the
converter must copy that permutation.

### Atlas page size

Godot stores UVs in texture pixels; the atlas page declares its own size. A
wrong declared size silently mis-samples every region. Read the real size from
the PNG's IHDR header, never guess.

### Bezier curves

Spine runtimes do not evaluate the cubic per frame: `Timeline.setBezier`
precomputes a 10-point table by forward differencing and interpolates linearly
between table points. To reproduce the runtime's curve exactly, emit those
table points as linear keys. The curve's control values live in the same space
as the key values and receive the same mirroring/offset transform.

### `Polygon2D` internal vertices

Godot's `internal_vertex_count` vertices are dropped only when `polygons` is
empty — an explicit index list draws them all.

### Spine `inherit` modes

A bone with `noRotationOrReflection` (common on feet) does not follow the
plain `parent · local` product Godot computes. The fix: solve the **effective
local** transform from the real world matrices (`parent_world⁻¹ · bone_world`)
— at setup and per frame in animations. Without this those bones point the
wrong way, which reads as feet flipped upside down.

## Architecture

Hub-and-spoke. A canonical in-memory model, one importer and one exporter per
format:

```
godot_in ──> model ──> spine_out
spine_in ──> model ──> godot_out
```

Adding a format means writing two adapters, not N² converters. Every adapter is
verified by round-trip: `A → model → A` with numeric tolerance, and `A → B → A`
cross-checks.

## The verification loop

Never trust a conversion by inspection. Sample the same animation time on both
sides and compare bone world positions numerically:

- **Godot side**: a `SceneTree` script that seeks the animation, freezes it,
  and prints each `Bone2D`'s world position.
- **Spine side**: the official runtime loading the converted JSON and printing
  the same values.

The threshold for PASS is < 0.01 units on every bone. Deviations smaller than
that are rounding; anything larger is a real bug.

## License

MIT for this tool's code. See [THIRD-PARTY.md](THIRD-PARTY.md) for notes on
third-party format terms.
