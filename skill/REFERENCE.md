# Coordinate contract

Getting this wrong is the entire risk of the conversion, so it is stated once
and relied on everywhere — by the converter, the tests, and the validation
scripts. Nearly every conversion failure is one of these traps.

## Axes and mirroring

Godot 2D is **Y-down**; Spine is **Y-up**. Mirroring with `F = diag(1, -1)`
maps the whole tree consistently:

- `spine.x = godot.x`
- `spine.y = -godot.y`
- `spine.rotation = -godot.rotation`
- local vertex Y negated

## Animation values: absolute vs offset

Godot `Animation` keys hold **absolute** local transforms; Spine `rotate` and
`translate` keys are **offsets from the setup pose**. Converting Godot→Spine
subtracts the bone's setup value; Spine→Godot adds it back.

## Attachment timelines

The runtime draws a slot's **setup attachment** until a timeline key applies;
after that, whichever entry the timeline names (`None` hides the slot). The
Godot leg mirrors this with one discrete boolean `visible` track per entry
node and a static `visible` equal to `setup` — never to "drawn at some point",
or a comparison frame before the first key shows a prop the runtime hides. A
timeline starting after `t=0` needs a synthetic `t=0` key holding the setup
state, because Godot holds a value track's first key backwards. Sampling
exactly on a key time can differ by one sample: Godot applies the key at its
time, the runtime strictly after it.

## Scale timelines

Absolute local scale in both spaces, no mirroring; a Spine key without `x`/`y`
means 1. Emitted as `:scale` value tracks (Vector2) or per-axis `:scale:x` /
`:scale:y` bezier tracks, and read back through the same union.

## UV spaces

Godot stores attachment UVs in **texture pixels**; Spine mesh `uvs` are
**normalized within the region**. The converter rescales them using the UV
bounds, which are also what the emitted `.atlas` uses as region rects. Atlas
page size is read from the PNG's IHDR — a wrong declared size silently
mis-samples every region.

## Polygon2D geometry

Geometry is `polygon[i] + offset`, with the node's `position` applied as a
transform on top; both must be honoured or the attachment drifts (the demo
rig's Chin relies on `offset`). Trailing `internal_vertex_count` vertices are
dropped **only** when `polygons` is empty — an explicit index list draws them.

`Polygon2D.bones` paths are resolved against the `Skeleton2D` and dropped
silently when they do not resolve, so they must be the full path
(`root/hip/body`), not the bare bone name.

## Weighted meshes

Godot `Polygon2D` weights are per-bone `PackedFloat32Array`s; Spine weighted
meshes store `[boneCount, (boneIndex, localX, localY, weight) ...]` per vertex,
where the local coordinates are **relative to each influencing bone** — so the
converter runs forward kinematics to place them.

**Mesh vertices go in skeleton space, not bone space**: at rest a vertex
already sits at its rest world position, which is what the skinning matrices
expect. Emitting bone-local coordinates (with a node `position` compensating)
looks plausible and renders nothing.

## Transform2D column order

Godot's `Transform2D` uses its own column convention: `x = (cos, sin)` and
`y = (-sin, cos)` — the opposite of the right-handed convention used elsewhere
in the converter. Emitting the wrong one shears every skinned vertex, because
Godot skins with `accum · rest.inverse()`. The engine's own scenes are the
reference (`rest = Transform2D(0.3366, 0.9416, -0.9416, 0.3366, ...)` for a
70.3° bone). In `.tscn` files the constructor argument order is
`(x.x, x.y, y.x, y.y, o.x, o.y)` — transposed relative to standard row-major.

## Bone rest vs node pose

Godot binds skinned meshes to the Bone2D **`rest`** property, which can differ
from the node pose (rests with identity rotations while node rotations carry
the rig's angles). Mesh locals must be stored against the **global rest
chain**, not the node-pose worlds — using the setup bakes each bone's setup
rotation into the mesh and reads as a permanently tilted limb while the bone
numbers all check out. `rest` is not representable in Spine's own format, so
it survives the round trip as Godot-value extension fields on each bone
(`restX/restY/restRotation/restScaleX/restScaleY`); real Spine runtimes ignore
unknown keys.

## Region quads and atlas

Region quads need the runtime's corner order — bottom-left, top-left,
top-right, bottom-right in Spine's Y-up local space (see
`RegionAttachment.computeUVs`). Any other winding mirrors the quad.

Region UVs come from the `.atlas`, not the region size: the region's pixels
live at its page rect, and the **region** name can differ from the **slot**
name (`upper-arm2` vs `upperarm2`). Regions with `rotate: 90` store pixels
transposed and the runtime permutes the UV axes — copying that permutation is
what keeps a rotated piece from rendering sideways.

## inherit modes

Spine `inherit` modes are emulated. A bone with `noRotationOrReflection` (the
hero's feet) does not follow the plain `parent · local` product Godot computes,
so the converter solves the **effective local** from the real world matrices
(`parent_world⁻¹ · bone_world`) — at setup and per frame in animations.
Skipping this points those bones the wrong way, which reads as feet flipped
upside down. The original mode is kept as `metadata/spine_inherit`.

## Curves

Spine beziers are not Godot easing modes. The runtime precomputes each curve
into a 10-point table by forward differencing (`Timeline.setBezier`) and
interpolates linearly between table points; the converter emits exactly those
points as linear Godot keys, so the sampled pose matches the runtime. The
curve's control values live in the same space as the key values and receive the
same mirroring/offset transform — skipping that is the mistake that makes legs
drift.
