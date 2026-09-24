# skeleton-converter

A conversion hub for 2D skeletal animation rigs. One canonical in-memory model,
one importer and one exporter per format — currently Godot Skeleton2D scenes
(.tscn) and Spine JSON, with more formats planned. Every conversion is proven
numerically instead of trusted by eye.

## Status

Working today: Godot Skeleton2D scenes (.tscn) ⇄ Spine JSON, both directions,
numeric round-trip validated. Next: DragonBones and LoongBones adapters via the
same canonical model — see [ROADMAP.md](ROADMAP.md).

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
```

`-o` is a **directory** (created if missing) and `--name` is the output stem;
omit `--name` and it defaults to the input file's name (`player.tscn` →
`player.json`). One name drives the whole bundle: for `--to spine` that is
`animation.json`, `animation.atlas`, `animation.png`, and `index.html`; for
`--to godot` it is `animation.tscn` plus the page image renamed to
`animation.png`. The atlas and the scene both reference the image by name, so
the files must stay together and keep those names. Pass `--texture` to keep a
specific `res://` path instead of the copied image.

A `.tscn` is a Godot scene: there is no browser preview for it. Only `--to
spine` writes a servable `index.html` — see [Viewer](#viewer).

Zero third-party dependencies for the converter — Python 3.10+ stdlib only.

## Shortcuts

`make` wraps the two flows, so you do not retype paths or remember which
direction is previewable. Put your rig paths in `local.mk` (gitignored; copy
the lines from the Makefile header) and then:

```bash
make godot-to-spine    # .tscn -> bundle, then serves it at :8642
make spine-to-godot    # Spine JSON -> .tscn + page image
make test
```

Anything can be overridden per run:

```bash
make godot-to-spine GODOT_INPUT=path/to/player.tscn NAME=bot PORT=9000
```

Only `godot-to-spine` starts a server, because only that direction produces a
browser-viewable bundle; `spine-to-godot` prints where to load the scene
instead. Both refuse to run without an input path, and refuse to `rm -rf` an
output of `/`.

Every step is printed as it happens, including what the source rig contained:

```
--> clearing old output: ~/Desktop/convert-spine-to-godot
--> converting Spine JSON -> Godot scene
--> reading spine: .../hero/export/hero-pro.json
--> destination: ~/Desktop/convert-spine-to-godot
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
- **Any Godot scene** (`make godot-preview`): packs an existing `.tscn` with
  its referenced resources and runs it in the browser through the real engine.
  Uses the same `GODOT_INPUT`/`GODOT_OUT`/`GODOT_PORT` variables.
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
in_godot ──> model ──> out_spine
in_spine ──> model ──> out_godot
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

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned features and next steps.

## License

MIT for this tool's code. See [THIRD-PARTY.md](THIRD-PARTY.md) for notes on
third-party format terms.
