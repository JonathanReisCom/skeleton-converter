# 2D skeletal (bone) character animation — market landscape

Research date: **2026-09-28**. Every claim carries the primary source it came
from (official site, official docs/changelog, official repository metadata).
Search engines were down during this session (all providers failed), so all
evidence is from direct URL reads of first-party sources. Items that could not
be reached are marked `UNVERIFIED` instead of guessed.

Commercial terms (prices, tiers, revenue thresholds) are deliberately **not**
recorded here — this is a public repository. What matters for a converter is
capability: which formats a tool emits, which engines can play them, and what
its licence *obliges* you to do.

Status vocabulary: **active** (release/changelog within ~12 months),
**maintenance** (sporadic commits, no releases), **dead** (no release evidence
for years), **archived** (repo closed by its owner).

---

## 1. Authoring tools

"Runtimes" below means what can actually *play* the exported rig in a product.
Two shapes exist: a **language/library runtime** you embed yourself (e.g. JS,
C++, Go) and an **engine runtime** (a plugin/extension for Unity, Godot, …).
A tool with no runtime column entry ships video/images only.

| Tool | Vendor | Status | Skeletal data out | Runtimes available | Site |
|---|---|---|---|---|---|
| **Spine** | Esoteric Software | **Active** — 4.3.26 (2026-09-07); runtimes repo pushed 2026-09-25 | Spine JSON (documented spec) + Spine binary; exporting meshes/IK/physics requires the advanced editor plan (feature matrix on the vendor's own site) | **17 official runtimes**: C, C++, C#, Java/libGDX, Haxe, Flutter, Unity, Unreal, **Godot**, MonoGame/XNA, SDL, SFML, GLFW, iOS, Android, JS/TS. The JS/TS runtime ships WebGL, Canvas, **CanvasKit (Skia, incl. headless Node)**, three.js, web player + web components, PixiJS v7/v8, Phaser v3/v4, Construct 3 — **there is no official React Native module**: RN would need a Skia/WebGL bridge (the CanvasKit backend is the closest fit). Third-party runtimes: raylib, axmol, melonJS, Beef | <https://esotericsoftware.com/> |
| **Rive** | Rive | **Active** — changelog page read shows editor 0.8.2618 (2025-02-28); 2026 material references an AI authoring agent | `.riv` binary (documented, format v7) + **RML** (new XML text format, compiled by their CLI); `.riv` export requires a paid plan | **Web/JS-WASM, React, React Native, Flutter, Apple (iOS/macOS), Android, Unity, Unreal, C++** — all MIT-licensed; community runtimes listed for QtQuick, UWP and Compose Multiplatform. **No Godot runtime** | <https://rive.app/> |
| **Moho 14.4 "Side Quest"** | Lost Marble | **Active** — update released 2025-11-11 | **glTF/GLB** with skeleton, actions, Smart Bones → morph targets | No runtime of its own: the rig reaches **Unity, Unreal, Godot, Blender** as glTF, played by each engine's own importer | <https://moho.lostmarble.com/> |
| **Toon Boom Harmony 27** | Toon Boom | **Active** — release notes for 27; add-on "Ember" AI tools | Proprietary project; C++ SDK on the top plan | None — video/image sequences | <https://www.toonboom.com/products/harmony> |
| **Cartoon Animator 5** | Reallusion | **Active** — "5.3 NEW" on product page | Video/image sequences; `.ctactor`/`.ctBmotion` | None — video output. Windows only | <https://www.reallusion.com/cartoon-animator/> |
| **Live2D Cubism 5.1** | Live2D | **Active** — editor 5.1, downloads updated 2026-07 | `.moc3` + `model3.json`/`motion3.json`/`physics3.json`; **not bone-based** | Official SDKs: **Unity, Native, Web, Java, Unreal Engine, Cocos Creator** (+ MotionSync and After Effects plugins); **needs a Publication License to release** | <https://www.live2d.com/en/cubism/> |
| **Creature** | Kestrel Moon | Unclear/maintenance — site distributes the tool, no dated changelog found | Custom JSON + CreaturePack (FlatBuffers) | Unity, Unreal, cocos2d-x, HTML5/JS, Lua, AS3. **No Godot.** Runtimes Apache-2.0 | <https://creature.kestrelmoon.com/> |
| **Adobe Animate** | Adobe | **Maintenance mode** — official statement (page updated 2026-06-09): no new features planned | HTML5 Canvas/WebGL/SVG/video/sprite sheets | None — the output is a web/sprite format, not a rig a runtime plays | <https://www.adobe.com/products/animate.html> |
| **Adobe Character Animator** | Adobe | Active-ish — 26.0 (2026-01), "no new features" | Video/AE exchange only | None | <https://www.adobe.com/products/character-animator.html> |
| **Spriter** | BrashMonkey | **Dead** — site modified 2021-01-07, "Spriter 2" never shipped | SCML (XML, documented) | Community runtimes only (no maintained official one) | <https://brashmonkey.com/> |
| **After Effects + Duik Ángela** | RxLaboratory | Active — Duik 17.1.21, GPL-3 | Baked video/sprite sheets | None — rigging lives inside a compositor | <https://www.adobe.com/products/aftereffects.html + https://rxlaboratorio.org/rx-tool/duik/> |
| **LoongBones** | LoongBones (DragonBones successor) | **Active** — cloud SaaS with prompt-driven AI generation | Proprietary; vendor advertises export to "Unity, Cocos and more" | No runtime list published; **none confirmed for Godot** | <https://www.loongbones.app/> |
| **SkelForm** | Retropaint (community, editor GPL-3.0) | **Active** — repository pushed 2026-09-27; Rust desktop + web editor | `.skf` (a zip: documented `armature.json` + atlas PNGs + editor data); bones, keyframe animations with interpolation handles, mesh binds/weights, IK (FABRIK/Arc), physics (sway/bounce/damping), styles | Generic runtimes (Rust MIT, Go MIT) + engine runtimes: Unity, Ebitengine (MIT), Kraken (MIT), Macroquad (MIT), pygame, web player. **No Godot runtime** | <https://skelform.org/> |
| **DragonBones / DragonBonesJS** | DragonBones team | Maintenance — runtime repo pushed 2026-01-23, not archived, MIT; README now recommends LoongBones | Skeleton JSON + `tex.json` + atlas (no formal spec page) | JS runtime covers Egret, PixiJS, Phaser, Hilo, Cocos Creator; other language ports in the same org: C# (Unity), C++, and **Godot-DragonBones (MIT)** | <https://dragonbones.github.io/> |

Sources: <https://esotericsoftware.com/spine-changelog/archive>,
<https://api.github.com/repos/EsotericSoftware/spine-runtimes>,
<https://esotericsoftware.com/spine-purchase> (feature matrix),
<https://rive.app/docs/getting-started/introduction>,
<https://rive.app/docs/runtimes/advanced-topic/rml.md>,
<https://moho.lostmarble.com/blogs/news/free-update-export-to-video-game-engines-with-the-moho-14-4-side-quest>,
<https://www.toonboom.com/products/harmony>,
<https://www.reallusion.com/cartoon-animator/>,
<https://www.live2d.com/en/cubism/download/editor/>,
<https://www.live2d.com/en/sdk/about/>,
<https://www.live2d.com/en/sdk/license/>,
<https://creature.kestrelmoon.com/purchase.html>,
<https://helpx.adobe.com/animate/desktop/introduction-to-animate/whats-new.html>,
<https://helpx.adobe.com/adobe-character-animator/desktop/introduction/whats-new.html>,
<https://brashmonkey.com/>, <https://rxlaboratorio.org/rx-tool/duik/>,
<https://www.loongbones.app/>, <https://api.github.com/repos/DragonBones/DragonBonesJS>,
<https://api.github.com/orgs/DragonBones/repos>,
<https://raw.githubusercontent.com/DragonBones/DragonBonesJS/master/README.md>,
<https://skelform.org/>, <https://skelform.org/dev-docs/v0.8/file-specs.html>,
<https://skelform.org/dev-docs/v0.8/engine-runtimes.html>,
<https://api.github.com/repos/Retropaint/SkelForm>,
<https://api.github.com/users/Retropaint/repos>.

## 2. Engine-native paths (no external tool)

| Engine | Built-in 2D skeletal support | Evidence | Site |
|---|---|---|---|
| **Godot 4.7** | `Skeleton2D` + `Bone2D` + `Polygon2D` weight painting; IK/jiggle/look-at via `SkeletonModification2D` stack (CCDIK, FABRIK, TwoBoneIK, Jiggle, LookAt, PhysicalBones) — **marked Experimental** | <https://docs.godotengine.org/en/stable/tutorials/animation/2d_skeletons.html>, <https://docs.godotengine.org/en/stable/classes/class_skeletonmodification2d.html> | | <https://godotengine.org/>
| **Unity** | `com.unity.2d.animation` **17.0.1 (2026-09-05)**: Skinning Editor, Sprite Skin, IK Manager 2D (Limb/CCD/FABRIK), Sprite Swap; PSD Importer (`.psb`) builds rigged prefabs | <https://docs.unity3d.com/Packages/com.unity.2d.animation@17.0/changelog/CHANGELOG.html>, <https://docs.unity3d.com/Packages/com.unity.2d.psdimporter@9.0/manual/index.html> | | <https://unity.com/>
| Others | Spine/DragonBones runtimes rather than native rigs: PixiJS + Phaser via `pixi-spine`/DragonBones runtimes, Defold/GameMaker/PlayCanvas/Solar2D via Spine runtimes, Cocos via Spine/DragonBones | <https://esotericsoftware.com/spine-runtimes> | | <https://esotericsoftware.com/spine-runtimes>

## 3. AI in 2D character animation (what actually ships)

The load-bearing distinction: **nothing shipped today does "video → 2D bone rig
playable in an engine".** Video-driven products emit 3D skeleton data
(FBX/GLB/BVH) or pixels.

- **Animated Drawings (Meta FAIR)** (<https://github.com/facebookresearch/AnimatedDrawings>)
  — the only serious 2D attempt; repository is
  **archived** (`"archived": true`, `pushed_at` 2025-09-03), MIT, 12.8k stars.
  Outputs video/GIF; no engine bone export. <https://api.github.com/repos/facebookresearch/AnimatedDrawings>
- **Plask** (<https://plask.ai/>, video→3D), **DeepMotion**
  (<https://www.deepmotion.com/>), **Move.ai** (<https://move.ai/>), **Cascadeur**
  (<https://cascadeur.com/>, 3D, AI inbetweening/auto-posing) — all 3D only.
- **Adobe Character Animator** — internally a 2D puppet rig driven by webcam
  tracking, but exports video/AE, not bone data.
- **AI inside 2D rig tools**: LoongBones (<https://www.loongbones.app/>,
  "AI-generated animations from a prompt", cloud SaaS), Rive's **AI coding
  agent** (<https://rive.app/>, authoring assistant), Toon Boom **Ember**
  (<https://www.toonboom.com/products/harmony>, drawing/creative assist add-on).
  Spine's 4.3 changelog (4.3.07→4.3.26, May–Sep 2026) contains **no** AI features.

## 4. Interop formats that matter for a converter

| Format | Owner | Documented? | Notes |
|---|---|---|---|
| **Spine JSON** | Esoteric | Yes — attribute-by-attribute spec | Only publicly documented, actively versioned 2D bone interchange format with a live editor. Imports back into Spine ("interoperability with other tools") |
| **Spine binary (`.skel`)** | Esoteric | Yes — documented binary spec | Varints/strings table, per-version reader required |
| **Godot `.tscn` / `AnimationLibrary`** | Godot (MIT) | Yes (engine docs; text format) | `AnimationLibrary` is a container of `Animation` resources — the natural carrier for converted clips |
| **RML (XML) → `.riv`** | Rive | Yes (docs + CLI `rive schema`) | **New**: a *text* representation of a Rive file explicitly designed so coding agents can author Rive content, compiled by the Rive CLI into `.riv`. A realistic machine-writable target, unlike the binary |
| **glTF/GLB** | Khronos | Yes | Now used by Moho to carry **2D bone rigs + actions** into engines (Smart Bones → morph targets) |
| **SkelForm `.skf` / `armature.json`** | SkelForm project (GPL-3 editor, MIT runtimes) | Yes — full file spec + runtime API in the dev docs | A zip holding JSON (bones, keyframes, meshes/binds, IK, physics) beside atlas PNGs. Written by a free editor, so it is a realistic machine-writable target — no Godot runtime exists, a converter would have to emit `.tscn` |
| **DragonBones JSON** | DragonBones | De-facto (runtime source) | `ske.json` + `tex.json` + atlas |
| **Live2D `.moc3`/`model3.json`** | Live2D | Partial | Parameter/deformer model, **not bone-based**; release-time licence required |
| **SCML (Spriter)** | BrashMonkey | Yes | Legacy; tool dead |


## 5. What changed in the last ~12 months

- **Spine 4.3** (2026): new **Sliders** constraint (apply an animation as a
  constraint, optionally bone-driven) and Transform-constraint extensions. The
  4.3 JSON also moved all constraints into a unified top-level `constraints`
  array with a `type` per entry, and added attachment types such as
  `clipping`. <https://esotericsoftware.com/blog/Spine-4.3-released>
- **Rive**: RML + CLI + AI agent — the toolchain is being repositioned for
  text/agent workflows.
- **Moho 14.4** (2025-11-11): first mainstream 2D rig tool to export **glTF**
  with skeleton, actions and morph targets for Unity/Unreal/Godot/Blender.
- **LoongBones** replaced DragonBones as the recommended editor for the
  DragonBones runtime, moving it to a cloud SaaS with AI generation.
- **Adobe Animate** is officially in maintenance mode; **Spriter** is dead.
- **Unity** keeps investing in the native path (2D Animation 17.0.1, Sep 2026);
  **Godot** keeps its 2D skeleton stack but still marks modifications
  "Experimental".
