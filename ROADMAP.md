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

- [ ] Spine `scale` animation tracks are dropped (only `rotate`/`translate` are
      read). Measured victim on the hero: `head-turn` flips the head with a
      `stepped` scale key (x=-1) — the flip never reaches the converted rig
      and its children mirror to the wrong side (hair01 worldY sign flip,
      122-unit deviation). Rigs that animate scale deviate
- [ ] Transform and physics constraints are not baked (IK and path are) — see
      `unsupported_constraints()`; the bones they drive keep their animated
      values
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
- [ ] Attachment **timelines** (a slot changing attachment mid-animation) are
      still not converted: switching a variant in the Godot viewer shows the
      setup-pose geometry for the whole animation. Fix shape: emit the slot's
      attachment changes as `Polygon2D` visibility tracks on the same timeline
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
- [ ] Spine `scale` animation tracks are dropped (only `rotate`/`translate`
      are read). Measured victim on the hero: `head-turn` flips the head with
      a `stepped` scale key (x=-1) — the flip never reaches the converted rig
      and its children mirror to the wrong side (hair01 worldY sign flip,
      122-unit deviation). Rigs that animate scale deviate
- [ ] A `skin: true` path constraint does not survive the round trip. Under a
      skin that does not equip it, the source runtime leaves the constraint's
      bones inactive at (0,0); the converted rig drops the constraint, so those
      bones stay active at their setup/animated transforms (measured: hero
      morningstar chain 150–300 units in all 12 animations). Same root cause as
      the bake: Godot has no path-constraint equivalent

### Godot side

- [ ] Mesh deformation tracks (per-vertex animation from `Polygon2D`)
- [ ] `SkeletonModification2D` stacks (IK, jiggle, look-at) — bake before converting
- [ ] Export to Godot `.tres` resource (alternative to `.tscn`)

### Spine side

- [ ] Sequences support
- [ ] `noScale` / `noScaleOrReflection` inherit modes (currently fall back to
      normal inheritance)
- [ ] JSON → `.skel` binary writer

### DragonBones

- [ ] Importer (`.json` v3/v4 → canonical model)
- [ ] Exporter (canonical model → `.json`)

### LoongBones

- [ ] Importer — uses the DragonBones format, so this may be an alias rather
      than a new adapter (verify format version differences first)

### Distribution

- [ ] Godot editor plugin (right-click `.tscn` → export to Spine)
- [ ] PyPI package (`pip install skeleton-converter`)
- [x] CI: GitHub Actions running the fixture-free suite on Python 3.10-3.13
      (`.github/workflows/tests.yml`)
