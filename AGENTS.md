# AGENTS.md

Conventions for AI agents and humans working on this repository.

## What this project is

A conversion hub for 2D skeletal animation rigs: one canonical in-memory model,
one importer and one exporter per format. Currently: Godot Skeleton2D scenes
(.tscn) ⇄ Spine JSON; more formats planned (DragonBones, LoongBones).

## Language

- Code, comments, and identifiers: English.
- Commit messages: English, Conventional Commits (`feat(converter): ...`).
- README: English. Internal docs may be in Portuguese.

## Non-negotiables

- **The converter has zero third-party dependencies.** Python 3.10+ stdlib
  only. If a feature seems to require a package, it needs a stronger
  justification than convenience.
- **Never trust a conversion by inspection.** Every conversion claim must be
  backed by a numeric comparison: sample the same animation time on both sides
  and compare bone world positions. Threshold for PASS: < 0.01 units on every
  bone. Deviations smaller than that are rounding; anything larger is a bug.
- **Round-trip tests are the regression gate.** Every format pair must pass
  `A → model → A` with numeric tolerance before it can be committed. If a
  round-trip fails, the conversion is wrong — do not widen the tolerance.
- **The coordinate contract lives in `README.md`** and is the single source of
  truth for axis conventions, mirroring, absolute-vs-offset animation values,
  UV spaces, and the Godot `Transform2D` column order. Any change to those
  rules requires updating that section first.

## Architecture

Hub-and-spoke. A canonical in-memory model, one importer and one exporter per
format. Adding a format means writing two adapters, not N² converters.

```
src/
├── model.py       # canonical model + FK (forward kinematics) + inherit modes
├── in_godot.py    # .tscn reader → model
├── in_spine.py    # Spine JSON reader → model
├── out_godot.py   # model → .tscn writer
├── out_spine.py   # model → Spine JSON writer
├── registry.py    # format detection, convert dispatch
└── cli.py         # command-line interface
```

### Rule: no format-specific logic outside the adapters

Matrix conventions, mirroring, inherit-mode semantics, and bezier handling live
in `model.py` and are shared. Adapters only parse/serialize. If you find
yourself writing a mirror or a sign flip inside an adapter, move it to the
model layer — it is almost certainly a cross-format invariant, not a format
detail.

### Rule: don't vendor third-party runtimes

The optional validation harness depends on `@esotericsoftware/spine-core` via
npm. That dependency is dev-only, must never be imported by the converter, and
its license requires that each user obtain their own Spine Editor license. Keep
the converter stdlib-only so the product itself has no license friction.

### Rule: the viewer runtime is pinned, and its version is not the data version

`src/viewer_out.py` loads `spine-webgl` from the unpkg CDN at a **pinned**
version. The shell drives the `SpineCanvas` app API (`app.loadAssets`,
`app.renderer`, `app.assetManager`); spine-webgl 4.3 removed that API —
`SpineCanvas.prototype` there exposes only `clear()` and `dispose()`. A 4.3
runtime therefore loads the skeleton, logs no error, and renders **zero
pixels**: a silent failure that looks like a rig bug. Do not "align" the
runtime version with the `spine` data version emitted by `out_spine.py`
(4.3.26) — the two are independent, and a 4.2 runtime reads 4.3 data fine.
Verified: `4.2.120` renders the demo rig; `4.3.13` renders nothing.

### Rule: the atlas name does not mirror the JSON's name

`hero-pro.json` ships with `hero.atlas` — not `hero-pro.atlas`. Deriving the
atlas path by swapping the JSON's extension (a natural-looking shortcut) makes
the viewer request a file that does not exist and fail with a 404, even though
the skeleton itself is fine. Resolve the atlas by looking for a same-stem
sibling first, then the only `.atlas` in the folder, and bake the result into
the generated shell. The CLI does the same for `--from spine`; `?atlas=` in the
URL overrides it when a folder holds several rigs.

### Rule: a Godot scene does not run in a browser

`--to godot` writes a `.tscn` — a Godot scene. There is no web viewer for it,
and `python3 -m http.server` serving that folder renders nothing useful: the
browser is handed Godot scene text. Only `--to spine` produces a previewable
bundle (JSON + atlas + texture + `index.html`). To see a converted Godot rig,
load the `.tscn` in Godot or validate numerically; do not reach for the browser.

Because a scene written to a `.json` path looks servable and is not, `convert`
takes `-o` as a **directory** plus `--name` for the stem (defaulting to the
input's name) and rejects a `-o` that ends in a file extension.

### Rule: the output bundle shares one stem

`convert --to spine` writes four files that only work together: the JSON, the
`.atlas`, the page image, and `index.html`. All four take the **`--name`
stem** (or the input's name when `--name` is omitted), never the source
texture's name — `--name animation` yields
`animation.json`, `animation.atlas`, `animation.png`, `index.html`. `--to godot`
follows the same rule: `animation.tscn` plus the page image copied as
`animation.png` (pass `--texture` to keep a specific `res://` path instead).
The atlas declares the image name on its first line and the runtime resolves it
as a sibling, so a mismatched name is a load failure, not a cosmetic wart. The
Spine Editor's atlas lookup is an exact string match on `<json>.atlas` too.

Do not "preserve" the source texture name (`gBot.png`) beside
`animation.json`: it reads as a stray file and makes the handoff ambiguous.

### Rule: the viewer shell stays static and path-agnostic

The shell is generated HTML that `fetch`es sibling files. It must never embed
skeleton data, atlas, or image bytes, and it must never hardcode an absolute
path. Consequences to respect:

- Output filename is `index.html`, so `python3 -m http.server --directory
  <out>` serves the rig at `/` with no query string.
- The skeleton is selected by `?skeleton=<name>.json`, defaulting to the JSON
  name baked in at generation time. A folder with one rig needs no arguments.
- `file://` cannot `fetch` siblings, so the viewer only works over HTTP.
  Regenerating the JSON is the refresh path — no rebuild of the shell.

## Adding a format

1. Write the importer: format file → canonical model.
2. Write the exporter: canonical model → format file.
3. Add a fixture to `tests/fixtures/` (MIT or CC0 licensed only).
4. Write a round-trip test: `fixture → model → fixture` with numeric tolerance.
5. If the format has a live runtime, write a ground-truth sampler (like
   `pose-sample-godot.gd` or `pose-sample-spine.mjs`) and add it to the
   cross-engine validation.
6. Only then add the format to the registry.

## Verification discipline

The most common failure mode in this domain is a converter that produces a
rig that *looks* plausible but has silent coordinate bugs. The only defense is
numeric comparison against the real engine at sampled animation times. Reading
the code, checking field names, or eyeballing the output in an editor are all
insufficient.

### Rule: a rendered frame is not proof of animation

The viewer can render a correct-looking pose and be completely static. A
screenshot proves the rig loads and the texture binds — nothing more. Animation
must be verified by sampling **world** transforms at two moments and asserting
they differ:

```js
// In the running page: sample twice, ~1s apart, after starting an animation.
const pose = () => window.__skeleton.bones.map(b => [b.worldX, b.worldY]);
```

Two traps this catches:

- **`Skeleton.update(delta)` does not recompute the pose.** In the 4.2 runtime
  it is literally `this.time += delta`. Without an `updateWorldTransform` in
  the render loop, `trackTime` advances and local `rotation` changes while
  every `worldX`/`worldY` stays frozen — the rig renders its setup pose
  forever and nothing errors. The loop must call `skeleton.update(delta)` *and*
  `skeleton.updateWorldTransform(spine.Physics.update)`.
- **Sampling the wrong field.** `bone.a/b/c/d` and `worldX/worldY` are only
  written by `updateWorldTransform`; `bone.rotation` is the local pose written
  by `state.apply`. A probe reading local fields sees motion that the screen
  does not, and a probe reading world fields after the bug sees none.

Pick an animation with real movement (`walk`, `run`) — some animations such as
`fall` are nearly static by design and prove nothing.

### Rule: shortcuts live in the Makefile, not a JS task runner

`make` wraps the two conversion flows (`godot-to-spine`, `spine-to-godot`,
`test`). Do not add a `package.json` at the repo root or a pnpm/npm script to
drive Python: the converter's whole promise is "zero third-party
dependencies", and `validation/package.json` is the only Node manifest — it is
dev-only and must stay that way.

Two details the targets must keep:

- **`make` runs in the repo root**, which is exactly what `python3 -m src.cli`
  needs. That is why the shortcuts work from any directory while the raw
  command does not.
- **Only `godot-to-spine` serves.** It writes a previewable bundle; the
  Spine→Godot direction writes a `.tscn`, so its target prints where to load
  the scene rather than starting a server over a folder no browser can render.

Machine-specific paths belong in `local.mk` (gitignored), never in the
committed `Makefile`. Both targets refuse an empty input and refuse to
`rm -rf` an output of `/`.

**Progress output is a contract.** Every step prints as `--> <what>`, and the
CLI reports what it actually did with the source: the atlas it used, which
constraints it baked and which the active skin skipped, how many bones were
baked. That information is collected by the reader into `Skeleton.notes`, not
recomputed by the CLI — the reader is the only layer that knows. Do not add a
step that reports something the caller cannot verify (a bare "processing…"),
and do not duplicate a line the CLI already prints from the Makefile.

### Rule: port solvers from the runtime, never from its documentation

`src/constraints.py` bakes Spine's IK and path constraints by porting the
official solver (`spine-core` 4.2 `IkConstraint` / `PathConstraint`). Six bugs
came from guessing semantics instead of reading the code — each produced a
plausible rig with a silently wrong pose:

- **Defaults are not what they look like.** `SkeletonJson` reads
  `constantSpeed` with a default of **true**, `rotateMode` defaults to
  **chain** (not tangent), `positionMode` to **percent**, `spacingMode` to
  **length**. An absent key means the accurate branch.
- **`vertexCount` is not a loop bound.** `readVertices` is called with
  `vertexCount << 1` and then walks the *whole* `vertices` array; the path's
  15 vertices were read as 8, skipping every later Bezier segment.
- **`computeWorldVertices(start=2)` skips the first vertex**, and `start`
  counts *floats*, not vertices. `curveCount` is derived from the unsliced
  length, so slicing before counting loses a segment.
- **A constraint's `target` names a SLOT, not a bone** for path constraints.
- **`skin: true` means inactive, not "always on".** Both the constraint and
  the bones it drives are deactivated unless the active skin lists them; the
  runtime then leaves those bones at `(0, 0)`. Baking them anyway moved the
  hero's unequipped chains by 228 units.
- **`updateAppliedTransform` must not be re-run after a path constraint.** It
  extracts the local transform from world; re-propagating recomputes world from
  stale parents and undoes the placement.

The check that catches all of these is numeric comparison against the runtime
at the same pose, per skin:

```bash
node validation/constraint-reference.mjs <rig>.json <rig>.atlas [anim] [time]
# SPINE_SKIN=<name> selects the skin; the hero's path constraint only runs
# under "weapon/morningstar"
```

`tests/test_constraints.py` pins the solver to runtime-produced numbers, so a
drift fails CI without needing the Spine rig.

### Rule: a passing round-trip test does not prove the .tscn loads

Reading a generated `.tscn` back with our own parser only proves the parser and
the writer agree. It cannot catch a scene Godot refuses to instantiate. Four
bugs of exactly that class survived a green test suite:

- **Phantom node in track paths.** Tracks were emitted as
  `SkeletonRoot/Sprite2D/Skeleton2D/<bone>` while the writer emitted `Sprite2D`
  as the scene root — no `SkeletonRoot` node existed. Godot logged "parent path
  has vanished" per node and instantiated an empty scene; our reader silently
  matched nothing, so every animation came back with **zero** tracks. The root
  container node and the track prefix must agree.
- **Unsanitized sub_resource ids.** Spine animation names are free-form
  (`head-turn`, `crouch-from fall`). Used raw in `id=`, Godot rejects the
  resource with "the scene unique ID must contain only letters, numbers, and
  underscores" and the scene fails to load. Ids are sanitized; the real name
  stays in the AnimationLibrary key.
- **Texture path that does not exist.** Falling back to `res://image.png` when
  the atlas could not be resolved yields a scene referencing a missing texture
  — a parse error per attachment. Resolve the image from the atlas instead.
- **The `--atlas` flag was never passed to the reader.** UVs were then computed
  against a 1x1 page and every region sampled wrong, silently.

The check that catches all of them is loading the generated scene in the real
engine and asserting it instantiates with the expected node/bone count and
non-zero animation tracks:

```bash
# needs a Godot project dir; --import first so textures register
godot --headless --path <project> --script res://sample_pose.gd -- \
      res://<scene>.tscn <animation> <time>
```

An empty `POSE` list, or any `ERROR`/`SCRIPT ERROR` line, is a failure — even
when the round-trip test is green.

### Rule: know what the validation tolerance excludes

A FAIL is not automatically a converter bug. Spine rigs commonly use
constraints the converter does not implement (IK, path, physics), and the
influenced bones will deviate by design. Read the constraint targets before
concluding:

```bash
python3 -c "import json;d=json.load(open('<rig>.json'));print(d.get('ik'),d.get('path'))"
```

In the official `hero` sample, `left-leg`/`right-leg` drive `thigh*`/`shin*`,
`look-constraint` drives `head`, and a path constraint drives `chain1..8` —
exactly the bones that diverge (up to 226 units). Bones outside those chains
still differ by ~0.1–0.4, which is a real gap: Spine `scale` animation tracks
are dropped (see ROADMAP), and `inherit: noRotationOrReflection` is emulated.
Report which bones fail and why, rather than calling the whole conversion
wrong or widening the tolerance.

## Docs maintenance

Docs are not optional. When a change contradicts, invalidates, or extends what
the docs say, update them in the same commit:

- **`README.md` coordinate contract** — update when a coordinate rule, a
  mirroring convention, an interpolation behavior, or an attachment mapping
  changes. This is the most-read section; stale rules here produce wrong rigs.
- **`ROADMAP.md`** — check a planned item off when implemented; add new
  discoveries (bugs found, edge cases, format quirks) that represent future
  work.
- **`AGENTS.md`** — update when an architecture rule, a non-negotiable, or a
  "how to add a format" step changes.

If you find yourself writing a comment in code that explains a convention or
traps, check whether the coordinate contract already documents it. If it does
not, add it there instead — the code comment serves the current reader; the
contract serves every future reader and converter.

A change that contradicts the docs without updating them is an incomplete
change. If you are unsure whether a change warrants a doc update, it does.
