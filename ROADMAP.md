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

## Planned

### Known gaps

- [ ] Spine `scale` animation tracks are dropped (only `rotate`/`translate` are
      read), so rigs that animate scale deviate from the source
- [ ] Spine constraints (IK, path, physics) are not converted, so the bones
      they drive (e.g. `chain1..8` under a path constraint, `shin*` under leg
      IK) deviate by design — see the tolerance rule in `AGENTS.md`

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
