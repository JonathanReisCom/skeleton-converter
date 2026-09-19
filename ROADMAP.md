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

## Planned

### Godot side

- [ ] Mesh deformation tracks (per-vertex animation from `Polygon2D`)
- [ ] `SkeletonModification2D` stacks (IK, jiggle, look-at) — bake before converting
- [ ] Export to Godot `.tres` resource (alternative to `.tscn`)

### Spine side

- [ ] Constraints (IK, transform, path) — Godot side has no equivalent, but the
      reverse direction could map Spine constraints to `SkeletonModification2D`
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
- [ ] CI: GitHub Actions running round-trip tests on a matrix of Python versions
- [ ] Import test in Spine Editor (the single most important untested claim)
