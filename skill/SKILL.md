---
name: skeletonconverter
description: Convert 2D skeletal animation rigs between engine formats via a canonical model — currently Godot Skeleton2D scenes (.tscn) and Spine JSON, with more formats planned. Use when porting a rig between supported engines, importing a Spine export into Godot or vice versa, verifying a converted skeleton reproduces the original pose, or debugging silent coordinate/skinning bugs in converted animations. Triggers on "convert godot to spine", "convert spine to godot", "port skeleton between engines", "spine json from godot", "godot scene from spine", "validate skeletal animation round-trip".
---

# Skeleton Converter

A conversion hub for 2D skeletal animation rigs: one canonical in-memory model,  
one importer and one exporter per format. Currently Godot 4 `Skeleton2D` scenes  
(.tscn) ⇄ Spine JSON; DragonBones and LoongBones are planned adapters. Not  
affiliated with Esoteric Software.

## Quick start

```bash
cd <cloned repo>   # python -m needs the repo as working directory

# Godot scene -> Spine JSON (+ .atlas)
python3 -m src.cli convert --to spine path/to/player.tscn -o out.json

# Spine JSON -> Godot scene
python3 -m src.cli convert --to godot path/to/hero.json -o out.tscn

# Numeric diff between a Godot scene and a Spine JSON
python3 -m src.cli compare rig.tscn out.json
```

Requires: `python3` (3.10+, zero dependencies), `node`, and the Godot 4 binary  
(`GODOT_BIN` env var, default `/Applications/Godot.app/Contents/MacOS/Godot`).  
The Spine-side validation needs `@esotericsoftware/spine-core` — run  
`bun install` or `npm install` once in the cloned repo's `skill/` directory.

## Workflow

1. **Convert** with the command for your direction (Quick start).
2. **Check side files**: `--to spine` writes `out.atlas` beside the JSON — copy
  the source PNG beside it before loading in a runtime. `--to godot` needs  
   `--atlas` (sibling `.atlas` with the JSON's name is auto-detected) and  
   `--texture` for the image path.
3. **Validate numerically** — never by eye:
  ```bash
   bash validation/validate-roundtrip.sh <godot-project-dir> <scene.tscn> \
     <spine.json> <animation> <time> [sprite-scale]
  ```
   Prints worst bone deviation and `PASS`/`FAIL` (threshold 0.01 units). A  
   deviation ≥ 0.01 is a bug — never widen the tolerance.
4. **If FAIL**: read the coordinate contract in
  [REFERENCE.md](REFERENCE.md) — nearly every failure is one of the ten traps  
   documented there.

## Rules

- Never trust a conversion by inspection. Sample the same animation time on  
both sides and compare bone world positions (`compare` or  
`validate-roundtrip.sh`).
- Round-trip tests (`A → model → A`) are the regression gate. A failing  
round-trip means the conversion is wrong, not that the tolerance is tight.
- Zero third-party dependencies in the converter itself; the Spine runtime is  
dev-only validation tooling and must never be imported by it.

## Known limitations

- Godot `Polygon2D` mesh deformation tracks (per-vertex animation): not  
converted, only bone transform tracks.
- Godot `SkeletonModification2D` stacks (IK, jiggle, look-at): dropped — bake  
into keys in Godot before converting.
- Spine constraints (IK, transform, path, physics): not converted to Godot.

## Reference

The complete coordinate contract (mirroring, animation offset semantics, UV  
spaces, weighted mesh layout, `Transform2D` column order, region quad winding,  
`inherit` mode emulation, bezier baking) is in [REFERENCE.md](REFERENCE.md).  
Read it before debugging any conversion mismatch.
