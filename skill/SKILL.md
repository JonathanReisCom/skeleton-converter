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

# Numeric diff between two files of the same format
python3 -m src.cli compare --format godot rig_a.tscn rig_b.tscn
python3 -m src.cli compare --format spine a.json b.json
```

Requires `python3` (3.10+; the converter itself has zero third-party
dependencies). Optional extras: the Godot 4 binary (`GODOT_BIN` env var)
enables the Spine→Godot web preview and `make godot-preview`; `node` plus one
`npm install` inside `validation/` enables the dev-only Spine-runtime
validation harness. None of these are needed for the conversions themselves.

`make` wraps the flows (paths come from `local.mk`, gitignored — see the
Makefile header): `make godot-to-spine`, `make spine-to-godot`,
`make godot-preview`, `make spine-preview`, `make compare`, `make test`.

## Workflow

1. **Convert** with the command for your direction (Install section).
2. **Keep the bundle together**: `-o` is a directory and `--name` is the output
   stem (defaulting to the input's name). `--to spine` writes four files sharing
   that stem — `<name>.json`, `<name>.atlas`, `<name>.png`, `index.html`. The
   texture is copied and named after `--name` (never after the source PNG). The
   atlas declares the image name, so renaming or splitting them breaks loading.
   `--to godot` writes `<name>.tscn` plus the page image (`--texture` keeps a
   specific `res://` path instead).
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
4. **If FAIL**: read the coordinate contract in [REFERENCE.md](REFERENCE.md) —
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

   All make preview targets serve through `python3 -m src.devserver <port>
   <dir>` — a static server that sends `Cache-Control: no-store` headers.
   Plain `http.server` serves stale bundles after an in-place rebuild (Chrome
   heuristically caches on Last-Modified), which looks like your new rig is
   not loading.

## Side-by-side compare (browser)

`make compare` (bundle dirs in `local.mk` as `COMPARE_GODOT` / `COMPARE_SPINE`,
or inline: `make compare COMPARE_GODOT=... COMPARE_SPINE=...`) builds one
`compare.html` in the **parent** folder of the two preview bundles and serves
that parent with the no-cache devserver:

```bash
make compare   # then open http://localhost:8083/compare.html
```

Equivalent by hand — both bundles must sit under one parent (one origin so the
shell can reach into both iframes):

```bash
python3 -c "from src.bundle import compare_page; \
compare_page('<godot-bundle-dir>', '<spine-bundle-dir>')"
python3 -m src.devserver 8083 <parent-dir>
```

The page embeds each bundle's own `index.html` as a typed pane (Godot WASM
and/or Spine WebGL — any mix) and adds a shared control bar:

- **Master clock**: the two engines free-run on independent clocks, so live
  playback drifts. The shell owns the time and seeks BOTH panes to the same
  `t` every animation frame — deterministic comparison, not a race.
- **Freeze control**: one button freezes all panes at the same time (`t=`).
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
- **Absent track = setup pose**, on both sides: the Spine runtime applies
  nothing for a track an animation does not touch, and the Godot wrapper
  mirrors that. A "wrong limb" under one animation often means one engine
  carries a residue the other does not.

## Attachment explorer (both panes)

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
- Zero third-party dependencies in the converter itself; the Spine runtime is
  dev-only validation tooling and must never be imported by it.

## Known limitations

- Godot `Polygon2D` mesh deformation tracks (per-vertex animation): not
  converted, only bone transform tracks.
- Godot `SkeletonModification2D` stacks (IK, jiggle, look-at): dropped — bake
  into keys in Godot before converting.

## Reference

The complete coordinate contract (mirroring, animation offset semantics, UV
spaces, weighted mesh layout, `Transform2D` column order, region quad winding,
`inherit` mode emulation, bezier baking) is in [REFERENCE.md](REFERENCE.md).
Read it before debugging any conversion mismatch.
