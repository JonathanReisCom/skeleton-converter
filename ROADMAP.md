# Roadmap

## Current

- [x] Godot `.tscn` → Spine JSON
- [x] Spine JSON → Godot `.tscn`
- [x] Numeric round-trip validation (bone positions, mesh vertices, animations)
- [x] Weighted mesh attachments with per-vertex bone weights
- [x] Region attachments as quads
- [x] Spine `inherit` modes emulated (effective local transform)
- [x] Atlas page size read from PNG IHDR
- [x] Bezier curves baked into the runtime's 10-point table
- [x] CLI with `--from` / `--to` format dispatch
- [x] Output as a self-contained bundle (`-o <dir> --name <stem>`): json +
      atlas + texture + a browser viewer, or a `.tscn` + texture
- [x] Browser viewer for Spine output (`view` command, `index.html`, autoplay)
- [x] Generated scenes load in the real engine (root node, sanitized resource
      ids, resolved texture, working animation tracks)
- [x] Import test in Spine Editor — verified with the 4.3.26 Trial: the JSON
      imports and the rig renders. Images need a one-time `Tree → Images → Path`
      step per project (Editor design, not a converter bug), and the Trial's
      `-i -o -r` CLI opens the GUI without writing a `.spine` file.
- [x] IK and path constraints baked into animation keys — ported from the
      official runtime (`src/constraints.py`), validated to <0.005 units
      against it on the official hero rig across 7 animations

## Planned

### Known gaps

- [x] ~~Spine `scale` animation tracks are dropped~~ — FIXED: `scale` timelines
      are read into `Key.scale` (absolute local scale, a missing x/y means 1
      per the runtime's `readTimeline2` default), written as `:scale` value
      tracks or per-axis `:scale:x`/`:scale:y` bezier tracks, and read back.
      Engine-verified on the hero's `run-from fall`: 44 bones x 6 sample times
      agree with the runtime to 1.7e-4 (the residual is the runtime's 10-step
      bezier table). Victim to keep in mind: `head-turn`'s stepped x=-1 flip now
      reaches the rig
- [ ] Transform and physics constraints are not baked (IK and path are) — see
      `unsupported_constraints()`; the bones they drive keep their animated
      values. Reachability, measured against the local corpora: **transform
      (follow)** constraints are stateless — alien 3, mix-and-match 17, chibi 1,
      celestial-circus 3 — so the IK/path bake pattern applies and the numeric
      harness can verify them; **physics** jiggle constraints carry a stateful
      simulation (celestial-circus 30, cloud-pot 26) and need the runtime's own
      solver ported (`PhysicsConstraint.js`) plus time-stepped verification, so
      the cost is a port, not a formula
- [ ] Godot-side FK drift: a converted `.tscn` accumulates ~0.19 units per
      bone along a chain (~0.42 after four), independent of constraints. It
      predates the constraint work and is the largest remaining error source
- [x] ~~Unweighted mesh attachments land offset~~ — FIXED: the region and
      unweighted branches pre-mirrored Y before the polygon conversion
      mirrored it again (double flip); both now store spine-space points.
      Caught by the mesh parity gate (`src/mesh_parity.py`), which re-derives
      every skin attachment's world vertices with the ported runtime and
      gates geometry, UV containment, and equipping — `hero` and the
      template dummy both run at 0 violations
- [x] ~~Constraint-driven bones place setup meshes by plain FK~~ — FIXED:
      attachment worlds now come from the runtime solver (`constraint_worlds`):
      setup pose + constraints + skin gating. The hero's
      thigh2/foot2/shin2 drifted up to 1 unit without it
- [x] ~~Skin attachments whose slot has no setup attachment are rendered by the
      Godot leg but never drawn by the Spine runtime~~ — FIXED: equipping now
      mirrors the runtime (slot setup attachment + attachment timelines), and
      **every** skin entry is carried with `Attachment.slot` + `metadata/slot`,
      so the Godot viewer switches variants per slot exactly like the Spine
      viewer (verified on the template dummy: identical option lists, and
      `Eye_laugh` / `cloakObject_03` render identically in both panes)
- [x] ~~Attachment timelines (a slot changing attachment mid-animation) are not
      converted~~ — FIXED: `slots.<slot>.attachment` becomes one **discrete**
      boolean `visible` track per `Polygon2D` of the slot (linear interpolation
      blends false->true as a float, and any non-zero blend reads as visible, so
      a prop appeared a whole segment early), plus a synthetic `t=0` key holding
      the setup state (Godot holds a value track's first key backwards; the
      runtime draws the setup attachment before the first key). Engine-verified
      against the official alien's `death`: 352 slot samples across four
      animations, 350 exact — the two remaining differ only when sampling
      *exactly* on a key time, where Godot applies the key at its time and the
      runtime's timeline search applies it strictly after (one-sample boundary
      convention, not a conversion error)
- [ ] A native Godot scene's `Sprite2D` position offset is not representable
      in Spine (which has no scene-level offset — only bones). Measured victim:
      the official hero demo's `Sprite2D.position = (0, -15)`; the converted
      Spine rig sits ~15 units higher than the original in side-by-side
      previews. Fix shape: emit a root bone carrying the offset (changes the
      bone count, breaking one-to-one bone comparisons)
- [x] ~~Bezier curves do not survive the Godot leg~~ — FIXED: `out_godot`
      emits `TYPE_BEZIER` tracks (points = [value, in_t, in_v, out_t, out_v]
      per key, handles offset from their key) and `in_godot` rebuilds the
      spine-space curve. All 97 curve segments of the hero round-trip
      verbatim (worst delta 0.0). Note: spine-core evaluates the curve as 9
      linear segments (`setBezier` pre-samples), Godot solves the cubic
      exactly — a ≤0.13° difference between engines on a hard curve is
      inherent to the runtimes, not a conversion error
- [ ] A `skin: true` path constraint does not survive the round trip. Under a
      skin that does not equip it, the source runtime leaves the constraint's
      bones inactive at (0,0); the converted rig drops the constraint, so those
      bones stay active at their setup/animated transforms (measured: hero
      morningstar chain 150–300 units in all 12 animations). Same root cause as
      the bake: Godot has no path-constraint equivalent

### Godot side

- [ ] Mesh deformation tracks (per-vertex animation from `Polygon2D`) —
      BLOCKED ON DATA: no qualified Godot sample animates a `polygon` /
      `internal_vertex_count` track, so there is nothing to compare against
- [ ] `SkeletonModification2D` stacks (IK, jiggle, look-at) — bake before
      converting. The pattern is real in the corpus (gdquest goblin, papereaven
      player/level scenes), but verification needs the modification's own solver
      run in-engine per frame; the existing pose probe samples one seek, so the
      harness grows before the converter does
- [x] ~~Export to Godot `.tres` resource~~ — DONE: `src/out_tres.py` +
      `--to tres` writes an `AnimationLibrary` resource per rig (animations plus
      the bone list as metadata); verified by loading it in headless Godot 4.7.1,
      not only by parsing

### Spine side

- [ ] Sequences support — BLOCKED ON DATA: zero attachments with a `sequence`
      key across the 24 Spine JSONs in the local corpora, so there is nothing to
      verify a converter against
- [ ] `noScale` / `noScaleOrReflection` inherit modes — BLOCKED ON DATA: no
      bone in the local corpora declares either mode. (See the item below about
      what it falls back to today:)
      normal inheritance)
- [ ] JSON → `.skel` binary writer — reachable with a real verification path:
      the pinned runtime ships `SkeletonBinary.js`, so a written `.skel` can be
      loaded by the same runtime the numeric gates already use

### DragonBones

- [ ] Importer (`.json` v3/v4 → canonical model)
- [ ] Exporter (canonical model → `.json`)

### LoongBones

- [ ] Importer — uses the DragonBones format, so this may be an alias rather
      than a new adapter (verify format version differences first)

### Distribution

- [ ] Godot editor plugin (right-click `.tscn` → export to Spine)
- [x] ~~PyPI package~~ — DONE: `pyproject.toml` (PEP 621, stdlib-only, console
      script `skeleton-converter`); verified by building a wheel, installing it
      in a throwaway venv and running a real conversion from the installed copy
- [x] CI: GitHub Actions running the fixture-free suite on Python 3.10-3.13
      (`.github/workflows/tests.yml`)
