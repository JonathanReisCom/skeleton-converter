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
