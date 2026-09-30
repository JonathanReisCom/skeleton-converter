# Roadmap

Which formats exist, what they emit and what their licences oblige — the survey
this roadmap draws on: [docs/2d-skeletal-animation-landscape.md](docs/2d-skeletal-animation-landscape.md).

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
- [x] Browser viewer for SkelForm output: the bundle ships an `index.html` that
      plays the written `.skf` through SkelForm's own web player
      (`skelform-js` + the player's `api.js`, both pinned by commit — the
      player API is globals with no version handshake). The comparison shell
      drives it exactly like the Godot pane (`previewPlay`/`previewFreeze`), so
      a Spine-vs-SkelForm comparison runs both panes on one clock
- [x] Studio (`python3 -m src.studio`, `make studio`): the browser uploads a
      rig, `src/detect.py` sniffs the format (the two JSON flavours share their
      extension), there is no target to choose, and the result is the
      side-by-side comparator over the **original** source and one pane per
      converted format. Formats with no browser runtime (SkelForm, `.tres`) replay
      through the Spine leg, labelled `via-spine` in the pane folder
- [x] Generated scenes load in the real engine (root node, sanitized resource
      ids, resolved texture, working animation tracks)
- [x] Import test in Spine Editor — verified with the 4.3.26 Trial: the JSON
      imports and the rig renders. Images need a one-time `Tree → Images → Path`
      step per project (Editor design, not a converter bug), and the Trial's
      `-i -o -r` CLI opens the GUI without writing a `.spine` file.
- [x] IK and path constraints baked into animation keys — ported from the
      official runtime (`src/constraints.py`), validated to <0.005 units
      against it on the official hero rig across 7 animations

## SkelForm support (in progress)

- [x] Reader + writer: `src/in_skelform.py` / `src/out_skelform.py`, registered
      as `skelform`. Bones (with duplicate-name disambiguation), region and
      mesh visuals (weights, per-triangle groups, z-order), position/rotation/
      scale channels with the file's own curves, pivot folding (translation as
      the node position, rotation/scale into the vertices), and bone `hidden`
      -> attachment-visibility timelines. Exact round trip measured on the two
      official samples: 32/32 and 61/61 bones, 25/25 and 21/21 attachments,
      bone world transforms identical, geometry/UV within 3e-4.
- [x] **Ground-truth harness**: `validation/verify_skelform.py` compares a
      converted rig against `validation/skelform_semantics.py`, a port of the
      runtime's `Animate` / `Construct` / `inheritance` / `inherit_vert`. Both
      official samples PASS; every remaining divergence is classified as one of
      the two known approximations below.
- [x] **What the official web player does with a multi-page archive**: two
      runtime contracts found by reading `api.js` at the pinned commit and then
      watching a real 4-page rig (the user's `custom assets`, 2004x1881 pages)
      draw. (1) `skfReadFile` declares `let atlasIdx = 0` INSIDE its member
      loop, so its `atlasIdx++` is dead code and every page image overwrites
      `atlases[0]`: the page NUMBER is iteration order, not the member name —
      the writer now emits `armature.json` first and the pages in `atlases`
      order, and the pane completes the pages the loader missed (JSZip, already
      loaded) so a multi-page rig textures correctly (`atlas gl,gl,gl,gl`).
      (2) `SkfDraw` scales+offsets a **bone** as `pos*scale + position` but
      Y-negates a **vertex** first and offsets it by `-position.y`; a fit that
      mixes both spaces (or that re-measures after the loop has written the
      camera into the positions it reads) throws the rig out of the canvas.
      Both fixed in `template_skelform_viewer.html`, with the member order
      pinned by a test and the fit's inputs published in `body.dataset.fit`.
- [x] **Two runtime contracts that made the SkelForm pane lie**: the pane's own
      camera offset (the vertex pass computes `-y*scale - position.y`, so
      centring needs `-(centerY*scale + height/2)`; with the bone formula the
      whole rig is drawn above the canvas while every number looks healthy) and
      the animation array's **frame order + per-channel `next_kf`** (grouped by
      channel instead, the runtime breaks out at the first larger frame and the
      rig plays its setup pose). Measured on the user's 4-page Spine rig: with
      both fixed, every bone world rotation matches the Spine pane exactly
      (R_Hand 3.804 ≡ -2.479 mod 2π, Body 1.579, Head 1.843).
- [x] **Attachment placement for a Spine-sourced rig**: the writer converts
      each point into the owning bone's local frame (re-adding the anchor's
      translation before applying the bone's inverse), and the reader undoes it
      exactly. Measured on the user's rig at a frozen instant, per piece, both
      panes now agree: Body [-183, 161, 198, 779] on both sides, Head
      [-333, 745, 217, 1299] on both, hands within 4 units, feet within 13 (the
      documented per-axis approximation).
- [ ] **Texture regions for a Spine-sourced rig**: the pieces sit and animate
      where the Spine pane has them, but sample the wrong artwork — the visual's
      region/UVs written into the style need the same reconciliation the
      geometry got. Next: compare one region's uv bbox in both formats.
- [ ] **Inverse kinematics** (FABRIK / Arc, with `Clockwise` /
      `CounterClockwise` constraints and `mimic_target`): the runtime solves it
      per frame in `Construct()`, so this needs the same bake treatment the
      Spine IK leg got (`src/constraints.py`)
- [ ] **Physics** (sway / bounce / damping): a runtime simulation fed by
      `global_*` accumulators, not keyframes
- [ ] **Per-axis key times** (measured: `_skellina` 161 and `_skellington` 692
      classified divergences, worst ~30 units / ~1.5% mid-segment). The model
      keeps one key list per track, so an axis keyed on its own is resampled.
      Fix shape: per-axis channels in the model (`translate_x`/`translate_y`),
      which both Godot (`position:x`/`position:y` tracks) and Spine
      (`translatex`/`translatey` timelines) can express natively
- [ ] **Non-TRS inheritance**: with a non-uniform animated scale the runtime's
      transform differs from the model's TRS composition (rotations add, scales
      multiply, then the offset rotates). Fix shape: bake the affected subtrees
      to keys, the way `src/constraints.py` bakes constraints
- [ ] **Path binds** (`is_path`): vertices dragged along a path instead of
      weighted; 4 attachments in the `_skellington` sample
- [ ] **Tint and `Texture` swap timelines**
- [x] **Bundle/CLI wiring for the Spine target**: `--from skelform --to spine`
      unpacks the `.skf`'s atlas pages, writes the Spine bundle and its viewer.
      Verified cross-engine (Spine runtime reading the written JSON vs the
      SkelForm runtime semantics): `_skellington` 0.0028 worst bone deviation,
      `_skellina` 0.58 — the latter being the two runtimes' different curve
      evaluation on zeroed handles, not a conversion error. No NaN in either.
- [x] ~~Bundle/CLI wiring for `--to skelform` and `--from skelform --to godot`~~ —
      FIXED, and the whole reader/writer grid works with it: `convert` resolves
      the rig's pages through one capability (`extract_assets` for a reader
      whose images live inside the input, else files beside the rig or its
      atlas), so no target names a source format. The `skelform` target writes
      one `.skf` with the pages embedded, ships a viewer that plays that
      archive through SkelForm's own web player, and picks the frame rate that costs
      least (`_skelform_timing`), reporting the residual shift; the Godot
      target copies the page it references, which used to be missing entirely
      for a SkelForm source. Measured after wiring: `spine→skelform` and
      `godot→skelform` value Δ 0.000029 at shared times (0 frame collisions,
      sub-frame shift reported as a note), `skelform→godot` worst non-IK bone
      17.4 units on the documented per-axis class; CI converts
      spine→godot→spine and spine→skelform→godot over the synthesized rig

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
- [x] ~~The rig's curve space was Spine's~~ — FIXED: `Key.curve` control points
      now live in the key's own value space (one quadruple per value axis),
      which every reader produces and every writer maps through the same
      transform it uses for values. Before, the hub's curves were stored in
      the Spine offset space: `in_godot` shifted them into it, `in_spine` left
      them as read, `in_skelform`/`out_skelform` carried setup-offset plumbing,
      and `constraints.py` sampled a Spine-space curve against Godot-space
      keys. Neutral for the Godot/Spine legs by construction — the `hero`'s
      converted JSON is byte-identical to the pre-change output, curve widths
      included — and it deletes a per-format conversion from three modules.
- [x] ~~SkelForm: one axis's curve used for the other~~ — FIXED: an element now
      takes its own quadruple, and the reader emits a single quadruple for
      rotate against two for translate/scale. Two bugs of one family, both
      invisible at key times: the second element was handed the first one's
      handles, and the curve was mapped once per element, which turned the
      second element's zeros into the `Linear` preset's thirds. Real `.skf`
      round trip after the fix: `_skellina` Δworld 0.0001, `_skellington`
      Δworld 0.00003 (frame quantization at 60 fps).
- [x] ~~The written `.skf` only loaded in our own reader~~ — FIXED, four
      writer bugs the browser pane found the moment it played the archive,
      none of them visible to the numeric round trips (our reader is
      forgiving, the official runtimes are not):
      1. pages were embedded under the rig's own name, and SkelForm's runtimes
         find a page by looking for "atlas" in the member name — the rig drew
         black silhouettes. They now ship as `atlas0.png`, `atlas1.png`;
      2. a bone pointed at its slot's **first** attachment instead of the
         equipped one, which hid most of a Spine-sourced rig (Spine lists
         alternatives freely);
      3. a slot whose name is not a bone left that bone with no visual at all
         (ten of the alien's bones) — the writer now falls back to the bone the
         attachment is weighted to, and a tie goes to the attachment whose slot
         IS the bone, so an effect weighted to the head cannot take the head's
         visual;
      4. region visuals were written without the pivot fields, and the player
         reads `visual.pivot_pos.x` while drawing — the draw loop threw and the
         canvas stopped at whatever had been drawn.
      Known refinements, not correctness: the pane's framing and its style
      activation still differ from the editor's view
- [x] ~~The Spine target crashed without a page image~~ — FIXED: the bundle
      step moved `<name>.png` even when no page had been written
      (`FileNotFoundError`), so any rig read without its page — an ordinary
      upload — failed after a successful write. The bundle now moves only what
      exists and reports "no page image found" instead
- [ ] **The Godot leg scatters multi-bone attachments** (found by the studio's
      visual comparison, not by a gate): the converted `.tscn` for the `hero`
      draws the armor pieces apart — on `walk` worse than on `attack` — while
      the Spine pane of the same rig is coherent, so the model and the Spine
      leg are right and the `Polygon2D` emission (or its bone binding) is
      wrong. It is invisible to the numeric gates (bones agree to 4e-5) and to
      `mesh_parity` (which validates the model, not the `.tscn`), and it is
      **pre-existing**: the emitted scene is byte-identical to the pre-refactor
      HEAD. Evidence: `tmp/hero-roundtrip-walk.png`
- [ ] **A native Godot scene can preview almost empty** (`preview_scene`): the
      `hero` demo's raw scene draws a single attachment in the wrapper. The
      wrapper frames authored vertices while a demo rig is posed by its own
      script/modifications, and the pane notes as much in its log. A pane that
      must show the *original* engine behaviour of a native scene needs the
      scene's own driver, not a wrapper. Evidence:
      `tmp/studio-raw-godot-direct.png`
- [x] ~~The hero's Spine preview shows white outlined boxes around the armor~~
      — NOT a conversion artifact: the **official** `hero-pro.json` export
      shows the same boxes in the same viewer, so they come from the rig and
      the viewer, not from this converter. Recorded so nobody chases it again
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
      curve. All 97 curve segments of the hero round-trip
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
- [x] ~~PyPI package~~ — DONE: `pyproject.toml` (PEP 621, install-free, console
      script `skeleton-converter`); verified by building a wheel, installing it
      in a throwaway venv and running a real conversion from the installed copy
- [x] CI: GitHub Actions running the fixture-free suite on Python 3.10-3.13
      (`.github/workflows/tests.yml`)
