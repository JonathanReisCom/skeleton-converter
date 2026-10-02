---
name: skeleton-converter
description: Convert 2D skeletal animation rigs between engine formats via a canonical model — currently Godot Skeleton2D scenes (.tscn) and Spine JSON, with more formats planned. Use when porting a rig between supported engines, importing a Spine export into Godot or vice versa, verifying a converted skeleton reproduces the original pose, or debugging silent coordinate/skinning bugs in converted animations. Triggers on "convert godot to spine", "convert spine to godot", "port skeleton between engines", "spine json from godot", "godot scene from spine", "validate skeletal animation round-trip".
---

# Skeleton Converter

A conversion hub for 2D skeletal animation rigs: one canonical in-memory model,  
one importer and one exporter per format. Currently Godot 4 `Skeleton2D` scenes  
(.tscn) ⇄ Spine JSON; DragonBones and LoongBones are planned adapters. Not  
affiliated with Esoteric Software.

## Install

Clone the public repo and run from its root — `python3 -m src.cli` needs the
repo as the working directory:

```bash
git clone https://github.com/JonathanReisCom/skeleton-converter
cd skeleton-converter

# Godot scene -> Spine bundle (out/animation.json + .atlas + .png + index.html)
python3 -m src.cli convert --to spine path/to/player.tscn -o out --name animation

# Spine JSON -> Godot scene (out/animation.tscn + the page image)
python3 -m src.cli convert --to godot path/to/hero.json -o out --name animation

# Anything -> SkelForm bundle (out/animation.skf: armature + embedded pages)
python3 -m src.cli convert --to skelform path/to/hero.json -o out --name animation

# Upload, convert, compare in the browser (detects the source format)
python3 -m src.studio            # http://localhost:8090 (make studio / studio-stop)
# jobs land in tmp/studio/; the page's `remove` button deletes one

# Numeric diff between two files of the same format
python3 -m src.cli compare --format godot rig_a.tscn rig_b.tscn
python3 -m src.cli compare --format spine a.json b.json
```

Requires `python3` 3.10+ and nothing else — no install step, no runtime
dependencies. Optional extras: the Godot 4 binary (`GODOT_BIN` env var)
enables the Spine→Godot web preview (the studio's Godot pane and the
`preview/` folder a `--to godot` conversion writes); `node` plus one
`npm install` inside `validation/` enables the dev-only Spine-runtime
validation harness. None of these are needed for the conversions themselves.

`make studio` starts the same server. In the studio the FIRST pane plays the
file you uploaded and every pane after it plays one converted output — Spine,
Godot (the real engine, WASM) or SkelForm (its own web player on the written
`.skf`), one pane per format, source first. A rig is never converted into the
format it arrived in, and only a side that cannot be built falls back to the
Spine leg, labelled `via-spine`.

Send a Spine rig's `.atlas` and page image with its `.json`: the studio says so
in the job's notes (which stay on the page, with a link to the comparison) and
marks that pane `not loaded` — a pane typed by its *content*, never by folder
name, so it is still driven as Spine and the other pane keeps playing. Choose
the rig's **folder** (or drop it) to bring its companions along: a browser only
sees the files it was handed, unlike the CLI, which scans the JSON's folder.

`make` holds only `studio`, `studio-stop` and `test`. Conversions are
`python3 -m src.cli convert` (the install commands above; it runs on disk, so
it can scan the input's folder) or the studio (the browser path, one pane per
format).

## Workflow

1. **Convert** with the command for your direction (Install section).
2. **Keep the bundle together**: `-o` is a directory and `--name` is the output
   stem (defaulting to the input's name). `--to spine` writes four files sharing
   that stem — `<name>.json`, `<name>.atlas`, `<name>.png`, `index.html`. The
   texture is copied and named after `--name` (never after the source PNG). The
   atlas declares the image name, so renaming or splitting them breaks loading.
   `--to godot` writes `<name>.tscn` plus the page image (`--texture` keeps a
   specific `res://` path instead). `--to skelform` writes one `<name>.skf`
   archive carrying the armature and the pages, plus a viewer that plays it
   with SkelForm's own web player; it stores key times as integer
   frames, so the chosen fps (60 unless a finer grid avoids a key collision,
   `--fps` to force one) is reported with what it cost.
3. **Validate numerically** — never by eye:

   ```bash
   bash validation/validate-roundtrip.sh <godot-project-dir> <scene.tscn> \
     <spine.json> <animation> <time> [sprite-scale]
   ```

   Prints worst bone deviation and `PASS`/`FAIL` (threshold 0.01 units — the
   same threshold applies to `compare` and the mesh gate). A deviation ≥ 0.01
   is a bug — never widen the tolerance. A FAIL is not automatically a
   converter bug: Spine rigs commonly use constraints the converter bakes
   (IK, path) that other engines re-derive differently; read the constraint
   targets before concluding.

   **The constraint bake MERGES channels, it does not replace them.** The
   solver returns the bones a constraint drives with the channels it produces
   (`rotate`/`translate`); assigning that dict over the source's dropped every
   other channel the rig carried. The hero's `head-turn` expresses the turn as
   `scale: x=-1` on the head — a flip, not a rotation — so both converted
   panes played a rig whose head never turned while the Spine pane turned it.
   Measured symptom, before the fix: the channel was absent from the model, so
   no scale track reached Godot and the engine's head linear stayed
   determinant-positive at every time; after it, `head-turn` t=0.6 gives
   det = -1.000, and the `.skf` carries `frame 3 → head ScaleX = -1`.

   Two gates exist because bones can pass while the skin is wrong:

   - **Bone gate** (`compare` / `validate-roundtrip.sh`): samples bone world
     transforms on both sides and takes the worst translation deviation.
   - **Mesh parity gate** (`src/mesh_parity.py`): re-derives every skin
     attachment's world vertices with the ported Spine runtime (the same
     solver `src/constraints.py` uses) and checks three things — geometry
     (runtime world vertices vs the model's polygon through its anchor), UV
     containment (every region-backed UV lands inside its region rect on the
     region's own page), and coverage (every equipped skin entry converted,
     equipped/unequipped flags exactly as the runtime would draw). Empty
     violation list = full parity. The round-trip tests run it on the
     synthesized rig, so a skin bug fails CI even though the bones match.
4. **If FAIL**: read the coordinate contract in
   [README.md](../README.md#the-coordinate-contract) —
   nearly every failure is one of the traps documented there.
5. **Preview the result** without the Spine Editor — `convert --to spine`
   writes a complete bundle (`<name>.json`, `<name>.atlas`, `<name>.png`, and
   `index.html`). Serve the folder and open the root:

   ```bash
   python3 -m http.server 8000 --directory /path/to/out
   # open http://localhost:8000/ — autoplays the first animation
   ```

   The shell `fetch`es sibling files, so it needs HTTP (`file://` is blocked).
   Its `spine-webgl` version is pinned to the 4.2 line on purpose: the shell
   drives the `SpineCanvas` app API that 4.3 removed, and a 4.3 runtime loads
   the rig without error then renders nothing. Do not bump it.

   Anything that serves a preview goes through `NoCacheHandler`
   (`src/devserver.py`) — a static server that sends `Cache-Control: no-store`
   headers; the studio builds its own handler on top of it.
   Plain `http.server` serves stale bundles after an in-place rebuild (Chrome
   heuristically caches on Last-Modified), which looks like your new rig is
   not loading.

## Side-by-side compare (browser)

The studio is the compare: upload a rig and it serves a `compare.html` holding
every pane (source first, one per converted format) — rendered from the current
templates on every request, so the chrome follows the code while the conversion
in the job folder stays as the converter wrote it. Equivalent by hand, for bundles that are already on disk — they must
sit under one parent (one origin so the shell can reach into every iframe):

```bash
python3 -c "from src.compare_out import emit_compare; \
emit_compare('<bundle-dir-a>', '<bundle-dir-b>')"
python3 -m src.devserver 8083 <parent-dir>
```

The page embeds each bundle's own `index.html` as a typed pane (Godot WASM
and/or Spine WebGL — any mix) and adds a shared control bar:

- **Master clock**: the two engines free-run on independent clocks, so live
  playback drifts. The shell owns the time and seeks BOTH panes to the same
  `t` every animation frame — deterministic comparison, not a race.
- **Freeze control**: one button freezes all panes at the same time (`t=`).
- **Playback speed**: one control multiplying the clock every pane is seeked
  from. Because the panes do not own their clocks while compared, a single
  factor keeps them frame-aligned — three runtimes each scaling their own delta
  would be three answers to "how fast".
- **Shared track buttons**: switching the animation switches both panes; the
  panes' own HUD buttons are bridged so no pane can end up on a different
  animation from the rest.
- **Alignment grid + crosshair**: toggleable overlay so a feature at the same
  screen position in both panes sits on the same line.

Semantics worth knowing when debugging a frozen compare:

- **Switching animation while frozen must re-freeze.** `setAnimation`
  replaces the frozen entry with a fresh `timeScale=1` one; the shell
  re-applies the freeze (`timeScale=0`, `trackTime=freezeTime`) or the Spine
  pane keeps animating while the Godot pane waits. It then samples the NEW
  animation at the current freeze time — seeking to 0.0 would show the setup
  pose on one pane and mid-stride on the other.
- **Zero-duration/one-shot animations hold the last key** — no loop-wrap
  interpolation. A pose sampled past the duration is the held final key, not
  a wrapped early frame.
- **Speed is applied at whichever clock owns the pane.** Compared, the shell's
  clock is multiplied (one `t`, seeked everywhere). Standalone, each pane scales
  its own mechanism: Spine's `AnimationState.timeScale`, Godot's
  `AnimationPlayer.speed_scale`, and — since the SkelForm runtime exposes no
  scale — a clock the viewer integrates and pushes through `previewFreeze`. A
  step change multiplies the interval, never the elapsed time, so the rig never
  jumps forward when the control is pressed.
- **Absent track = setup pose**, on both sides: the Spine runtime applies
  nothing for a track an animation does not touch, and the Godot wrapper
  mirrors that. A "wrong limb" under one animation often means one engine
  carries a residue the other does not.
- **Framing measures drawn vertices, never bone positions.** A runtime frames
  what it draws — spine's `getBounds` walks attachments. A converted rig's
  weapon and chain bones hang past the artwork: including `Bone2D.global_position`
  stretched the box to x=196 where the drawn rig ends at 91, pushing the camera
  52 units right and leaving the character off centre **on X only**, because
  those bones stick out sideways. Vertices use Godot's own skinning rule
  (`pose * global_rest⁻¹`, the same basis `model.py` states for every exporter).
- **A skinned `Polygon2D` keeps bone/weight PAIRS in `bones`** —
  `[path, weights, path, weights, …]`, one `PackedFloat32Array` per bone. There
  is **no** `weights` property: reading `poly.weights` is a runtime error that
  aborts `_ready` and leaves the pane black with no visible cause. Checked by
  running the engine headless, not by reading the docs.

## Attachment explorer (every pane)

**`rest` is the skinning basis, so it must be the frame the polygons are in.**
Godot draws a skinned polygon as `pose * rest^-1 * point`. A Spine-sourced
rig's polygons come from the constraint SOLVER's setup, so `rest` has to be
that solved setup — emitting the raw local applied `solved * raw^-1` on top and
put the hero's `thigh1`/`shin1`/`foot1` **8–9 units** off (measured engine vs
runtime: 8.02 / 9.28 / 7.67, and 0.000 after the fix). It also inflated the
pane's framing box from 330.12 to 338.76 units and moved its centre by 4.3,
which drew the Godot rig 2.6% smaller than the panes beside it — visible as the
foot sitting off the grid line. The godot->spine leg keeps its bind pose
instead: there the bind IS the polygon's frame.

**A skin is a variant of the WHOLE rig, and only the ACTIVE skin's bones draw.**
Spine flags each bone a skin owns with `"skin": true` (`skinRequired`);
`Skeleton.updateCache` starts those bones inactive and then activates the ones
the active skin lists *and their ancestors*. So the hero really does hide its
sword under `default` — the sword's bone belongs to the `weapon/sword` skin —
and the pinned runtime (`spine-webgl@4.2.120`) is where that was confirmed, not
the docs. Three things it cost:

- **An inactive bone still has a pose.** The solver used to leave its world at
  `(0, 0)` to mirror the runtime, which wrote the sword's region as a quad with
  all four corners at the origin: invisible in every converted pane *and*
  impossible to equip in a viewer. An inactive bone now takes plain FK, so its
  geometry is the rig's; `equipped` is the only thing the skin gates, and the
  panes' framing already skips `visible = false` polygons, so a weapon the
  source does not draw cannot inflate the box.
- **Converting "with the sword" means naming the skin**: `bundle.convert(skin=…)`,
  `--skin`, the studio's skin select (read in the browser from the picked JSON,
  so it is there before the first conversion) and the pane's own picker all set
  one. A name the rig does not have is refused with the list — falling back to
  another variant silently hands back a rig without the weapon.
- **The skin decides what draws, so the sidebar owns the row — and one row has
  to move every pane.** Inside a compare shell a pane hides its own HUD chrome,
  so the control lives in the shell's sidebar (`__skinRows`) and a pane opened
  alone keeps its own `skins` toggle. `?skin=` links a pane to one, and with
  nothing named a pane opens **equipped with the rig's FIRST skin** — file
  order, not the name `default`, resolved by `in_spine.skin_names_in` so the
  pane cannot open on a different variant than the conversion wrote. Three
  things make the row global:
  - the pane that HAS skins answers `__drawnBySlot()` — the runtime's own
    answer, bone activeness included, because under `default` the sword's slot
    still NAMES the sword and only the inactive bone stops it drawing;
  - the panes WITHOUT skins (Godot, SkelForm) follow through the per-slot
    control a slots row already uses, so one switch moves all three. Pushing
    into the skin-capable pane instead is a trap: a pushed `(none)` nulls a slot
    that the next `setSkin` cannot restore (`setSkin` only re-attaches what the
    OLD skin had);
  - the push is re-entrant by construction — each pane's pick pings
    `__attachmentsChanged`, which calls back in — and the SkelForm pane died of
    that loop (a blank canvas, no error). Hence the `applyingSkin` guard, and
    `setSlotsToSetupPose()` after `setSkinByName` so a switch is reversible.

**All three panes frame the rig with the same number** — `hud.fitScale`
(`src/static/hud.js`, `MARGIN = 0.85`), so a size difference in a comparison
is the rig's, never the pane's. The Spine and SkelForm viewers call it
directly; the Godot wrapper calls it through the JS bridge it already uses for
playback and falls back to the same formula when there is no bridge. Two
things that broke this:

- **`Camera2D.zoom` IS pixels per rig unit** (the camera divides the viewport
  by it), so `zoom = fitScale(...)`, not its reciprocal — the reciprocal is the
  Spine viewer's own convention and framed the rig at 38% of the pane.
- **Fit again on `size_changed`.** The browser resizes the canvas after the
  engine boots, so a camera fitted once held a scale for an aspect the pane no
  longer had; the box is measured once (setup pose, like the other two panes)
  and only the scale is recomputed.

The wrapper prints the result as `PREVIEW_CAM=` (viewport, zoom, box,
coverage) in every build, not only the web one — `coverage = box * zoom /
viewport` is how "the three panes frame alike" becomes a number, and the
limiting axis must read `0.85`. A headless run is the only place the harness
can read it, so keep that print unconditional and before the JS publish.

**Inside a compare shell the panes hide their own chrome** — the shell's left
sidebar is the control surface (tracks, master clock, freeze, grid, and one
attachment row per slot driving every pane), so a second set of buttons
driving the same thing would just be noise. The explorer travels with that
chrome: it is available when a pane is opened on its own (a bundle's
`index.html`, or `view`), not in the compare grid. To bring it back inside the
grid, drop `.embedded .hud-toggle` from the hide rule in `src/static/hud.css`.

**A pane that does not publish `__attachmentRows` silently ignores the sidebar.**
That is how the SkelForm pane behaved: picking "(none)" for a slot hid it in the
Spine and Godot panes and left the SkelForm pane drawing it, which reads as a
conversion bug and is not one. The SkelForm leg has no slots to point at — the
writer puts each attachment on its own bone (`bones[i].visuals_id`) — so a row is
a bone carrying a visual, and hiding it is that bone's flag. Two things to know:

- **`hidden` is reset every frame** from `init_hidden`
  (`skelform-js.js`: `if (!(mask & FLAGS.Hidden)) bone.hidden = bone.init_hidden`),
  so a manual toggle must write BOTH or the next draw undoes it.
- **The hide flag is `hidden`, not `visible`, and getting it backwards is
  silent.** Passing the visible flag straight into a setter that stores `hidden`
  drew every attachment of the slot EXCEPT the chosen one, and `(none)` un-hid
  the whole slot — the exact opposite of intent, with no error anywhere. What
  caught it is the pane's own `drawing …` line (bone#visual_id per visible hand
  item): the pane's explorer panel is built ONCE, so its values are stale and
  it cannot answer "what is on screen right now". Keep that line.
- **A `visuals_id` of 0 is a real visual, and `x or -1` turns it into -1.** Every
  truthiness test on that field silently drops the FIRST attachment of a rig:
  its owner read as unowned, so the writer gave it a duplicate bone and the slot
  map left it out — the piece then existed in the file, nothing drew it, and no
  pane could offer it. This rig's `SupportObject_01` (the shield) was exactly
  that. Compare an int, never truthiness.
- **Ownership must be read from the armature, not from the intermediate pick.**
  An attachment can be chosen for a bone and then displaced by a better
  candidate; a set built during the choosing still lists the loser as owned, and
  the leftover pass skips it — leaving a visual with no bone at all. Read
  `bone["visuals_id"]` after the facts instead.
- **An attachment draws its `path`, not its name.** Spine's `path` lets a new
  entry reuse an existing region's art, which is the normal way to add a piece.
  Resolving the region by name only left such an entry without a page, and the
  bundle then carried a texture entry pointing at an atlas that does not exist —
  `atlases[tex.atlas_idx].texture` throws and takes the WHOLE pane down. A page
  that cannot be resolved must mark the entry missing and drop it from the
  style, so the runtime skips that piece and keeps drawing the rest.
- **Rows are named by SLOT, from a `slots.json` member the writer adds.** The
  armature only knows bones (`visuals_id`), so naming rows after the bone that
  draws a visual (`HandObject_01`) matched nothing: the shell fans a pick out by
  slot name, and `Solt : R_Hand` never reached this pane while every other pane
  drew it. The member must be written **after** the atlas pages — the web player
  numbers pages by ITERATION ORDER over the archive's members, so a member
  inserted before them shifts every index and a multi-page rig dies with
  `Cannot read properties of undefined (reading 'size')`.

A slot's skin map holds *many* attachments (eye shapes, hair, props), not one.
The model keeps them all — `Attachment.slot` names the owner, `Attachment.name`
is the skin entry verbatim, `Attachment.setup` marks the slot's setup attachment
(what the runtime draws before any timeline applies) and `Attachment.equipped`
marks every entry the rig draws at some point, setup included.
The Godot leg emits a `Polygon2D` per entry with `metadata/slot` carrying the
slot; the viewer and the compare shell offer **one `<select>` per slot**
listing every attachment of that slot; `(none)` hides the slot. The Godot pane
switches the same entries via the metadata — two traps:

- **Case is data.** Mangled names (`Eye_Anger` → `Eye_anger`) make the Godot
  pane offer differently-spelled options than the Spine pane; the entry name
  is written as-is, with only Godot-illegal characters substituted.
- **Naming collisions.** Entries with the same name in different slots are
  deduplicated at the node level; the slot survives only as metadata.

## Vertex morphes (Spine `deform` → Godot `polygon`)

Spine's per-vertex morph ("FFD") lives **inside `attachments`**, not as a
top-level `deform` key: `animations.<anim>.attachments.<skin>.<slot>.<entry>.deform`.
A rig can carry hundreds of keys (hero-pro: 197, across 5 meshes) and it is what
makes a face look as if it turned — the most visible difference a conversion can
have and still pass every bone check.

Three things the runtime's own code settles, each of which cost a wrong guess:

- **`offset` counts DEFORM FLOATS, and a weighted mesh's buffer is not its raw
  layout.** Spine ADDS the key's values to the buffer at `deform[offset …]`. For
  a plain-pair mesh the buffer *is* the raw array. For a weighted mesh it is the
  bone entries' `(x, y)` PAIRS, flattened — the bone index and the weight are
  dropped, so an entry costs 2 floats where the raw costs 4. Measured against the
  runtime: key `offset: 15` of hero's head morphs vertices 4, 7, 21, 23 and 24
  under the pair layout, and vertices 1 and 9 under the raw layout. Meshes with
  ONE entry per vertex (the eyes, the body, hero's whole `crouch`) hide the bug,
  because there the two indices coincide — only the head, with two entries on
  some vertices, exposes it.
- **A delta is a displacement: apply only the bone's LINEAR part, in the model's
  component order.** Running it through the full affine dragged the bone's
  translation onto every vertex — the head sat ~250 units from the origin. And
  the model's convention is `x' = a*x + b*y` (`model.transform`): writing
  `a*lx + c*ly` transposes the rotation, which left every morphed rig a few
  units off its own keys.
- **Both layouts need a weight.** Only registering the weight for weighted meshes
  left plain-pair meshes multiplying every delta by zero: four of five meshes
  came out with an all-zero morph and the rig looked untouched.

Godot expresses it as a **`polygon` (`PackedVector2Array`) value track** — the
engine animates the array and skins whatever it holds. Verified by rendering a
skinned `Polygon2D` with an animated polygon: 40×40 at t=0, 10×10 at t=0.5,
exactly the key values. Godot cannot interpolate a packed array, so the writer
**bakes** the track at `MORPH_BAKE_STEP` (1/60), re-evaluating the polygon on
each sample through the same bezier sampler the constraint solver uses
(`src/curves.sample_key`), and keeps the source's own key times verbatim — a
sample at `0.20000000000000004` leaves a query at `0.2` holding the previous key,
which showed up as every mesh sitting a step behind at its own key. A key at t=0
is inserted when the timeline starts later, because Godot holds a value track's
first key backwards.

**Parity is measured, not assumed** (`validation/deform_parity.py`, which needs
Godot and the runtime installed beside it): the engine plays the scene and dumps
its skinned vertices (`validation/dump-skin.gd`, skinning done by Godot itself so
no matrix convention is re-guessed in Python), the runtime reports
`computeWorldVertices` (`validation/mesh-vertices.mjs`), and the two are compared
in skeleton space. Current numbers: hero `attack` body/eyes/mouth **0.000** at
0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4; hero `crouch` body/eyes/mouth/mantles
**0.000** at 0, 0.25, 0.5, 0.75, 1.0; goblins `walk` dagger **≤ 0.08**.

**Known limit, not a deform bug:** hero `attack`'s `head` stops at 3.85 units.
Its worst vertices carry **two bone entries and a zero delta**, so the gap is
Godot's `Polygon2D` blend, not the morph: Godot computes
`Σ w·(pose·rest⁻¹)·(node + point)` while Spine computes `Σ w·W(t)·local`, and a
single point plus a node cannot represent a vertex whose bones have different
setup transforms. Removing the head's deform entirely drives its error to 0.000,
which is how the attribution was made.

**SkelForm cannot express it at all**: the runtime has no deform channel and
`visual.vertices` is static (`constructVerts` only applies binds). Reproducing a
morph there would mean packing each pose as separate art.

## Attachment timelines (converted)

A Spine `slots.<slot>.attachment` timeline becomes one discrete boolean
`visible` track per `Polygon2D` of that slot, so playback switches the drawn
entry exactly as the runtime's `setAttachment` does. Three things to know:

- **Discrete, not linear.** The tracks carry `interp = 0` / `update = 1`;
  with linear interpolation the engine blends `false` → `true` as a float and
  any non-zero blend reads as visible, so a prop appears a whole segment early.
- **A `t=0` key is mandatory.** Godot holds a value track's *first* key
  backwards in time, while the runtime draws the slot's **setup** attachment
  before the first timeline key — so a timeline starting after t=0 gets an
  explicit t=0 key with the setup state, or the prop shows from frame zero.
- **Key-time boundary differs by one sample.** Godot applies a key *at* its
  time; the runtime's timeline search applies it strictly after. Sampling
  exactly on a key time can disagree; sample a tick past it.

## Motion between keys (converted)

Spine curves are control points, and a two-axis (translate/scale) curve carries
**one quadruple per value axis** — each axis in its own value space. A single
`value_map` cannot convert both: x keeps its sign and gains the setup offset, y
flips. Converting only the y quadruple left the x control values in Spine units,
and the failure is quiet: **every key stayed exact to 4 decimals and only the
motion between keys drifted.** The hero's body and left arm walked tens of units
off centre mid-segment on `idle-from fall` (worst 35 against 0 at the keys)
while all sampled key times agreed.

So: **keys exact + segments wrong is a control-point space bug, not a value bug.**
Sample *between* keys when a rig "looks wrong but matches at key times", and
check `Key.curve`'s quadruples per axis rather than trusting that the key values
agree. A 4-float curve is shared by both axes in Spine, so it must be expanded
into an 8-float one with each axis mapped by its own transform.

## Scale tracks (converted)

Spine `scale` timelines are absolute local scale and map 1:1 to Godot's
`Bone2D.scale` (no axis mirroring: a magnitude has no direction). A Spine key
that omits `x`/`y` means **1**, not the previous key (the runtime's
`readTimeline2` default). Verified against the engine: 44 bones × 6 sample
times of the hero's `run-from fall` agree with the runtime to 1.7e-4 — the
residual is the runtime's 10-step bezier table, not a conversion error.

## Rules

- Never trust a conversion by inspection. Sample the same animation time on
  both sides and compare bone world positions numerically (`compare` or
  `validate-roundtrip.sh`), and run the mesh parity gate for skin questions.
- Round-trip tests (`A → model → A`) are the regression gate. A failing
  round-trip means the conversion is wrong, not that the tolerance is tight.
- No runtime dependencies in the converter: it must run on a bare `python3`.
  The Spine runtime is dev-only validation tooling and must never be imported
  by it. Everywhere else, dependencies follow the free-software rule in
  `AGENTS.md`.

## Known limitations

- Godot `Polygon2D` mesh deformation tracks (per-vertex animation): not
  converted, only bone transform tracks.
- Godot `SkeletonModification2D` stacks (IK, jiggle, look-at): dropped — bake
  into keys in Godot before converting.

## Reference

The complete coordinate contract (mirroring, animation offset semantics, UV
spaces, weighted mesh layout, `Transform2D` column order, region quad winding,
`inherit` mode emulation, bezier baking) is in
[README.md](../README.md#the-coordinate-contract).
Read it before debugging any conversion mismatch.
