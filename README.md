# skeleton-converter

A conversion hub for 2D skeletal animation rigs. One canonical in-memory model,
one importer and one exporter per format — currently Godot Skeleton2D scenes
(.tscn), Spine JSON and DragonBones JSON (which is what LoongBones reads and
writes). Every conversion is proven numerically instead of trusted by eye.

## Status

Working today: Godot Skeleton2D scenes (.tscn) ⇄ Spine JSON in both
directions, DragonBones JSON (`_ske.json` + `_tex.json`) in both directions,
anything → SkelForm (`.skf`), everything proven by numeric round-trip
validation — plus a browser studio that converts an upload into every other
format and plays the results side by side. Remaining gaps are listed in
[ROADMAP.md](ROADMAP.md).

## Why

Rigs are locked in each tool's format: Godot in `.tscn`, Spine in its JSON,
DragonBones in its own schema. The hub lets a rig authored in any supported
tool reach any other — add a format and it talks to all existing ones, not
just the next one. And because a silent coordinate mistake produces a rig that
*looks* plausible but is wrong, every conversion is validated numerically
against the real engines instead of trusting inspection.

## Quick start

Run from the repo root — `python3 -m src.cli` resolves `src` relative to the
current directory, so it fails with `No module named 'src'` anywhere else:

```bash
cd /path/to/skeleton-converter

# Godot scene -> Spine bundle: out/animation.json + .atlas + .png + index.html
python3 -m src.cli convert --to spine \
  path/to/player.tscn -o out --name animation

# Spine JSON -> Godot scene: out/animation.tscn + the page image
python3 -m src.cli convert --to godot \
  path/to/hero.json -o out --name animation

# Anything -> SkelForm bundle: out/animation.skf (armature + its pages)
python3 -m src.cli convert --to skelform \
  path/to/hero.json -o out --name animation

# Spine JSON -> DragonBones bundle: out/animation_ske.json + _tex.json + page
# (the three files LoongBones imports as "DragonBones Data Files")
python3 -m src.cli convert --to dragonbones \
  path/to/hero.json -o out --name animation
```

`-o` is a **directory** (created if missing) and `--name` is the output stem;
omit `--name` and it defaults to the input file's name (`player.tscn` →
`player.json`). One name drives the whole bundle: for `--to spine` that is
`animation.json`, `animation.atlas`, `animation.png`, and `index.html`; for
`--to godot` it is `animation.tscn` plus the page image renamed to
`animation.png`. The atlas and the scene both reference the image by name, so
the files must stay together and keep those names. Pass `--texture` to keep a
specific `res://` path instead of the copied image.

A `.tscn` is a Godot scene: there is no browser preview for it. `--to spine`,
`--to skelform` and `--to dragonbones` each write a servable `index.html` — see
[Viewer](#viewer).

## Studio

```bash
python3 -m src.studio        # http://localhost:8090  (or `make studio`)
make studio-stop             # stop a studio left serving STUDIO_PORT
```

`make studio` never fights for the port: if one is already serving it prints the
URL and the two ways out (`make studio-stop`, or `make studio STUDIO_PORT=8644`).
`studio-stop` only kills a process that *is* a studio — another server on that
port is reported and left alone.

Drop a rig in the page and the studio detects the source format, converts it
into **every other format**, and opens the comparator over all of them — the
source in the first pane and one pane per converted output after it, the same
panes the compare shell emits, with a shared clock. The **folder** is
how a rig travels with everything it references: click *choose a folder* (or
drop the folder on the zone) and every file inside comes along, so a Spine
`.json` arrives with its `.atlas` and page image without hunting for them. A
picker of individual files hands over exactly what you marked and no more —
the CLI can scan the JSON's folder because it runs on disk; a
browser page cannot, it only sees the files it was given.

The panes are the point: the **first** pane plays the file you brought (the
Spine rig with its own atlas, the SkelForm archive, or the Godot scene packed
unmodified for the browser engine), and every pane after it plays what a
conversion wrote — the Spine runtime, real Godot, SkelForm's own web player on
the written `.skf`, or DragonBones' own runtime on the written `_ske.json`.
A rig is never converted into the format it arrived in.
Only when a pane cannot be built (a Godot target without an engine binary to
export with) does a side fall back to replaying through the Spine leg, and its
folder name then says `via-spine`.

Jobs land under `tmp/studio/` (`--root` moves that): each one keeps the upload,
every pane and its conversion, and the studio page lists the recent ones —
one row each, with a `remove` button that deletes that job (upload, panes and
page). Nothing prunes the folder otherwise, and a job that carried a Godot pane
weighs ~40 MB, so it is worth clearing once you are done with it.

A rig that arrived without something the comparison needs is reported where it
can be read: the job's notes stay on the page (with a link to the comparison)
instead of flashing past on the way to it. A Spine pane built from a JSON whose
atlas never came along says so in its own header and is marked `not loaded`,
while the other pane keeps playing — the shared clock skips the dead side
instead of waiting for it.

`--to skelform` writes one archive (`.skf`) holding the armature and a copy of
the rig's pages — what the SkelForm editor and its runtimes load — plus an
`index.html` that plays that archive in the browser through SkelForm's own web
player (runtime and player pinned by commit). That
format stores key times as **integer frames**, so its `fps` decides how exactly
the source's times survive: the converter picks 60 fps, moves to a finer grid
only when that stops two keys from collapsing into one, and reports on the
conversion line what the choice cost (`--fps` overrides it).

`--to dragonbones` writes `<name>_ske.json` (the rig), `<name>_tex.json` (the
texture atlas) and the page image, plus an `index.html` that plays them through
the format's own runtime (pinned by commit). DragonBones stores key times as
integer frames too — the same frame-rate rule and the same reporting apply — and
rigid rectangles become image displays while everything else becomes a weighted
mesh, so the file opens in LoongBones as a rig, not as a flattened picture.

The converter runs on a bare `python3` (3.10+) — no install step, no runtime
dependencies. Free and open-source dependencies are allowed where they earn
their place (see `AGENTS.md`); the runtime path stays install-free.

## Command line

`make` holds only what is not a page: `make studio`, `make studio-stop` and
`make test`. Every conversion is a `python3 -m src.cli convert` call (see
[Quick start](#quick-start)) — that is the disk-side path, writing into the
`-o` directory you name, which is what a project needs; the studio writes into
its own job folder under `tmp/studio/`. Both refuse a path they cannot write
and never touch anything outside the directory you gave them.

Every step is printed as it happens, including what the source rig contained:

```
--> converting Spine JSON -> Godot scene
--> reading spine: .../hero/export/hero-pro.json
--> destination: out
--> name: animation
--> atlas: .../hero/export/hero.atlas
--> constraints baked (skin 'default'): left-leg, look-constraint, right-leg
--> constraints skipped: 1 not active under this skin
--> baked bones: 9
--> writing Godot scene (.tscn + page image)
--> wrote animation.tscn, animation.png
--> texture: res://animation.png (copied beside the scene)
--> 44 bones, 30 attachments, 12 animations
--> next: load .../animation.tscn in Godot — a .tscn is a scene, not a web page
```

The `constraints baked` line matters: IK and path constraints are solved and
written into the keys, so seeing which ones ran (and which the active skin
skipped) tells you whether the output can reproduce the source.

## Viewer

Every conversion direction has a browser preview:

- **Godot→Spine** output: `view out.json` (or the convert step already emits
  `index.html`) — renders via the official `spine-webgl` runtime from the CDN.
- **→DragonBones** output: the convert step writes `index.html` beside the
  bundle and plays the written `_ske.json` + `_tex.json` through DragonBones'
  own runtime (the same code LoongBones ships, pinned by commit). The runtime
  is pinned to the **Pixi 5** line on purpose: the 8.x host never refreshes a
  weighted mesh's vertex buffer after the first pose, so a skinned rig renders
  frozen and scattered, while 5.x tracks the model within 0.3 units.
- **Any Godot scene**: drop the `.tscn`'s folder in the studio and its first
  pane packs the scene with its referenced resources and runs it in the browser
  through the real engine — no conversion needed to look at it.
- **Spine→Godot** output: the convert step builds a **web preview of the real
  Godot scene** — a WASM export of the actual `.tscn` running in the actual
  engine, in a `preview/` subfolder, wrapped by
  `template_godot_viewer.html` (same track-panel UI as the Spine viewer;
  the boot script publishes the animation list to the page via
  `JavaScriptBridge` and plays whatever track you click). No re-export
  through the canonical model; the browser plays the scene exactly as Godot
  would. Requires a Godot
  installation (`GODOT_BIN`, default the macOS app) and, once per machine,
  the web export templates (`Editor → Manage Export Templates`, or download
  the `.tpz` from the Godot release page and copy its `web_*` files into
  `~/Library/Application Support/Godot/export_templates/<version>/`).

Every Godot→Spine conversion can be previewed without the Spine Editor:

```bash
python3 -m src.cli view out.json
```

Writes `index.html` beside the JSON — a reusable shell that loads the sibling
`.json` + `.atlas` + image files, renders the rig via the official
`spine-webgl` runtime (loaded from the CDN), and autoplays the first animation.
Nothing is embedded: regenerate the JSON and just refresh the page.

Note: browsers block `fetch` of sibling files from `file://`, so serve the
folder once — `convert` prints the exact command:

```bash
python3 -m http.server --directory /path/to/out
```

Then open `http://localhost:8000/` — `index.html` is served automatically, no
query string. Use `?skeleton=other.json` only when the folder holds several rigs.

The `spine-webgl` version is pinned to the 4.2 line in `src/viewer_out.py`. Do
not bump it to 4.3: the shell drives the `SpineCanvas` app API, which 4.3
removed, and a 4.3 runtime loads the skeleton without error and renders nothing.

## Tests

```bash
python3 -m pip install pytest
python3 -m pytest tests/ -v
```

Two tiers:

- **Fixture-free** (`test_generated_output.py`) — builds a minimal rig in
  process and asserts what the engines require: the scene root exists, every
  track path resolves, resource ids are valid, the atlas names its image, and
  transforms survive our own reader. Runs everywhere, including CI.
- **Round-trip** (`test_roundtrip_godot.py`, `test_cross_convert.py`) — needs
  `tests/fixtures/player.tscn`, which is **not committed** (it is a
  third-party asset). Without it those tests skip, so a public clone has no
  third-party files and a green suite still means something.

CI runs the fixture-free tier on Python 3.10–3.13
(`.github/workflows/tests.yml`).

## The coordinate contract

Getting this wrong is the entire risk of skeletal conversion. These are the
rules, stated once, relied on everywhere. They were each discovered as a bug
that produced a plausible-looking but wrong rig.

### DragonBones values are offsets, not poses

DragonBones is the one target whose numbers are not the pose. The runtime
composes a bone as `origin + offset + animationPose`, and `origin` is the setup
transform the file declares (`Bone.init`: `this.origin = this._boneData.transform`)
— so every `translateFrame`/`rotateFrame` value is a DELTA from that bone's
setup, and `scaleFrame` multiplies it. Writing the absolute pose doubles the
setup: the hero's hip landed at `-94.89 + -87.91`, 95 units below where the rig
puts it, and every skinned mesh followed. The reader adds the setup back per
channel; the writer subtracts it.

The same leg needs no axis work: DragonBones is Y-down with a
clockwise-positive rotation (`dragonBones.yDown = true` in the runtime's own
bundle, and `Transform.toMatrix` writes the same `(cos, sin, -sin, cos)` block
Godot's `Transform2D` does), so a local transform travels verbatim.

Two more traps that cost real debugging:

- **A missing `tweenEasing` is `-2` (held), not linear.** `ObjectDataParser`
  defaults it to `-2`, so every interpolated frame must state `0` or carry a
  `curve`; the writer emits the default only when the segment really is held.
- **Two frames may not share a position.** A timeline frame's position is its
  integer index; two keys closer together than one frame collapse onto one, and
  a zero-length segment makes the runtime divide by zero and fill every bone
  downstream with NaN. The writer keeps the later key of a collapse and the
  bundle reports how many it cost.

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

Chaining a global rest world must stay in **one** convention: multiplying a
Godot-literal matrix through a standard-order `multiply` silently transposes the
basis, and every bone whose rest rotation is non-zero then lands its attachment
geometry at twice that rotation. Identity rests hide it completely — which is
why a synthesized gate with rotated rests had to exist before it surfaced
(`godot_rest_worlds` is compose-based now; callers that need the file-literal
order convert with `godot_matrix_from_standard`).

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

### Every skin entry is carried, keyed by slot

A slot's skin map holds *many* attachments (eye shapes, hair, props), not one.
The model keeps them all: `Attachment.slot` names the owner, `Attachment.name`
is the skin entry verbatim, and `Attachment.equipped` marks the one the runtime
draws at setup (the slot's setup attachment, or one an attachment timeline
equips). The Godot leg emits a `Polygon2D` per entry with `metadata/slot`, and
the viewer offers one select per slot — the same switching the Spine viewer
does. Two traps:

- **Case is data.** Mangled names (`Eye_Anger` → `Eye_anger`) make the Godot
  pane offer differently-spelled options than the Spine pane; the entry name is
  written as-is, with only Godot-illegal characters substituted.
- **Naming collisions.** Entries with the same name in different slots are
  deduplicated at the node level; the slot survives only as metadata, never as
  part of the node name.

### Attachment timelines mirror the runtime's slot state

The runtime draws a slot's **setup attachment** until an attachment timeline
key applies; after that, the entry the key names (`None` hides the slot). The
Godot leg mirrors that at three boundaries, each of which produced a wrong
frame when missed:

- **`setup` is not `equipped`.** `Attachment.setup` marks the slot's setup
  attachment (the static `Polygon2D` visibility), `Attachment.equipped` marks
  every entry the rig draws at some point. Using "equipped" for the static flag
  draws props the runtime hides before the first key.
- **A value track's first key is held backwards.** A timeline starting after
  `t=0` needs a synthetic `t=0` key holding the setup state, or the prop is
  visible from frame zero.
- **Discrete, never linear.** Linear interpolation blends `false` → `true` as a
  float and any non-zero blend reads as visible, so the prop appears a whole
  segment early (`interp = 0` / `update = 1` on those tracks).

Sampling *exactly* on a key time can differ by one sample: Godot applies a key
at its time, the runtime's timeline search applies it strictly after. Verified
against the official alien's `death`: 352 slot samples across four animations,
350 exact, the 2 remaining being that boundary.

### Scale timelines

Absolute local scale, identical in both spaces — a magnitude has no direction,
so there is no mirroring to get wrong. A Spine key that omits `x`/`y` means
**1**, not the previous key (the runtime's `readTimeline2` default). Emitted as
`:scale` value tracks or per-axis `:scale:x`/`:scale:y` bezier tracks.

### SkelForm: radians, absolute values, and curves that belong to the next key

SkelForm (`.skf`, a ZIP holding `armature.json` plus atlas pages) stores a
Y-up, CCW-positive space, so the reader mirrors exactly like the Spine leg
(`y -> -y`, `rot -> -degrees(rot)`). Three of its rules differ from every
other format here:

- **Rotation is in radians** in the file (`Animate` feeds it straight to
  `cos`/`sin`).
- **Animation values are absolute field targets** and each `element`
  (`PositionX`, `PositionY`, `Rotation`, `ScaleX`, `ScaleY`, `Hidden`) has its
  own keyframe list with its own times, keyed on integer frames
  (`time = frame / fps`). The model keeps one key list per track, so the two
  axes of a position or scale are merged on the union of their times.
- **A segment's bezier handles live on the keyframe that ENDS it**, normalized
  to the segment box, while the model keeps absolute control points on the key
  that starts it — the curve converts with a scale-and-offset, and stepped
  segments become the `Snap` preset (handles with `y == 999`).

A visual is either a texture rectangle (SkelForm stores it as *no vertices at
all*: the editor keeps a rect as `±size/2` around its bone, per
`utils::bone_meshes_edited`) or a skinned mesh (`vertices` + `indices` +
`binds`, where `binds[].bone_id` is a bone id, `binds[].verts[].id` a **vertex**
id, and an `is_path` bind drags vertices along a path). The model records which
one it was (`Attachment.mesh`) so a writer never collapses a skinned quad back
into a rectangle.

**The stored handles are used verbatim.** Older exports leave `Linear`
keyframes with zeroed handles; `interp` reads the numbers, so that really is a
cubic ease in every runtime playing the file. "Fixing" it to a straight line
here would silently disagree with the runtime. The preset table exists for the
*writer*, which picks the preset matching the curve it emits.

**Attachment points are world-space minus one translation.** Every reader here
stores an attachment's points as `world - anchor_world.xy` (the anchor being the
bone that carries most of its weight), so the bone's **rotation stays baked in**
and only the translation comes out. SkelForm's runtime instead applies the
owning bone's full transform to every vertex (`inheritVert`), so its files carry
bone-local points — and the two are not each other's inverse by translation
alone. Converting needs the bone as well: re-add the anchor's translation and
apply the bone's inverse on the way out, and the exact reverse on the way in.
Skipping that rotates every attachment by its bone's setup angle — the pieces
stay textured, keep animating, and land in the wrong place, which is the kind of
bug a rig without rotated bones never shows.

**An animation's keyframes are walked, not indexed.** `SkfGenericAnimate` runs
`for (k …) { if (kf.frame > frame) break; … }` over the file's `keyframes` array,
so that array must be **in frame order** and every entry's `next_kf` must point at
the next keyframe *of the same (bone, element)*, ending each chain with `-1` —
the editor's own files do exactly that. Written grouped by bone and element
instead (the natural way out of a model that keeps one key list per channel),
everything past the first keyframe is skipped: the file carries a complete
animation, the runtime reports the right frame, and the rig just plays its setup
pose. The keyframe *order* is as load-bearing as the values.

**How a bundle's pages are laid out is a runtime contract, and the official
player gets it wrong.** Its `skfReadFile` (`api.js`) declares `let atlasIdx = 0`
*inside* the member loop, so the `atlasIdx++` that follows is dead code: every
page image it finds overwrites `atlases[0]` and pages 1..n never get a texture.
Two consequences, both verified by reading that file and then watching a
multi-page rig draw:

- the page **number** comes from iteration order over the archive, not from the
  member name, so the writer emits `armature.json` first and the pages in the
  order `atlases` lists them (`atlas0.png`, `atlas1.png`, …). Any other order
  textures every attachment from the wrong image.
- the player can still only ever fill page 0, so this project's SkelForm pane
  reads the archive itself (JSZip, already loaded for the player) and fills in
  the pages the loader missed. Single-page rigs — the common case — are
  untouched.

The pane also has to speak the camera's terms exactly, and `SkfDraw` applies
them with two different Y conventions: a **bone** becomes `pos * scale +
position`, while a **vertex** — what a mesh visual actually draws — becomes
`-pos.y * scale - position.y`. Framing must use the *vertex* one (negated
offset) and must measure its box **before** the draw loop runs, while the
vertices still hold the constructed world positions unnegated. Get either half
wrong and the rig is drawn entirely above the canvas: the draws still happen
(hundreds of them, thousands of indices, `getError()` clean), so the pane looks
broken for no visible reason. Read the drawn box when this happens — that is
what found it.

Curves normally transfer verbatim between the two runtimes (both solve the same
cubic over the normalized segment), with two rules worth knowing:

- **A two-axis track's curve is one quadruple per axis** (8 numbers). A lone
  quadruple makes a Spine reader take the second axis's controls from beyond
  the array and the runtime fills the bone with NaN — the reader therefore
  always emits the full layout, giving an axis without keys a straight
  quadruple at its setup value.
- **A fully zeroed handle pair** (both value controls collapsed onto the
  segment's start) is *nearly* linear in SkelForm — its five Newton iterations
  do not fully converge — while Spine's pre-sampled table is closer to the
  exact straight line. Such a segment is written without a curve: the two
  runtimes then differ by ≤0.6 units on a 2048-unit rig, the same class of
  difference the Spine leg already documents for its own bezier table.

Two representability limits, both measured by
`validation/verify_skelform.py` against a port of the runtime's own semantics
(`validation/skelform_semantics.py`):

- **Per-axis key times.** SkelForm keys `PositionX` and `PositionY`
  independently, and often only *one* of them (a bone may animate `ScaleY`
  alone). The canonical model keeps one key list per track, so the other axis
  is resampled onto the union of times — exact at keys, approximate between
  them (worst measured: ~30 units on a 2048-unit rig, ~1.5%; 20.3 units on the
  `hero` `run` animation, whose `position:x` and `position:y` tracks peak at
  different frames).
- **Non-standard inheritance.** `inheritance()` adds rotations and multiplies
  scales componentwise before rotating the offset, which equals a standard TRS
  composition only while the parent's scale is uniform. With a non-uniform
  animated scale the two disagree; a rig that writes ordinary bone transforms
  cannot reproduce the runtime exactly there.

Pivot translation travels as the attachment's node `position`, and a pivot
rotation/scale is folded into the vertices (exact while the bone's own scale is
uniform — the two matrices do not commute otherwise). Not converted (reported
through the reader's notes): inverse kinematics, sway/bounce physics, tints,
`Texture` swaps and path binds.

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
          ┌─> out_spine
in_godot ─┼─> out_godot
in_spine ─┼─> out_skelform
in_skelform ─┴─> out_tres
```

Any reader pairs with any writer: `--from` and `--to` are independent, and the
CLI refuses a pair only when the format has no adapter. Every wired direction
is exercised by CI over a synthesized rig (`tests/ci_rig.py`).

Adding a format means writing two adapters, not N² converters. Every adapter is
verified by round-trip: `A → model → A` with numeric tolerance, and `A → B → A`
cross-checks.

Two rules keep the hub format-agnostic, and both exist because breaking them
produced wrong files:

- **A curve lives in the value space of the key it belongs to.** One quadruple
  `[t1, v1, t2, v2]` on a single-value track, one per value axis on a two-axis
  track — stored as `Key.curve`. Every reader produces it there and every
  writer maps it through the same transform it applies to the key values, so
  no format's convention becomes the rig's.
- **A two-axis track carries 8 numbers, and its two elements take one
  quadruple each.** Spine indexes the curve by axis, so a lone quadruple makes
  the runtime fill the bone with NaN, and an exporter that hands the same
  quadruple to both elements bends the second axis with the first one's shape
  — invisible at key times, wrong in between.

## The verification loop

Never trust a conversion by inspection. Sample the same animation time on both
sides and compare bone world positions numerically:

- **Godot side**: a `SceneTree` script that seeks the animation, freezes it,
  and prints each `Bone2D`'s world position.
- **Spine side**: the official runtime loading the converted JSON and printing
  the same values.

The threshold for PASS is < 0.01 units on every bone. Deviations smaller than
that are rounding; anything larger is a real bug.

## Watching a comparison

The compare page's sidebar carries every control: freeze every pane at the same
`t`, an alignment grid, the animation tracks, one attachment row per slot, and
the playback speed. Playback is driven by the shell's own clock — it seeks every
pane to the same time each frame — so changing the speed cannot desynchronise
the panes, and freezing always samples the same instant on all of them.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned features and next steps.

## License

MIT for this tool's code. See [THIRD-PARTY.md](THIRD-PARTY.md) for notes on
third-party format terms.
