# Third-party formats and terms

This tool reads and writes data files, and today it ships no third-party code:
`src/` runs on a bare `python3`. Every dependency that reaches the runtime path
(`src/`) or a shipped artifact is recorded here with its license and what it
replaces — that is what makes the dependency rule in `AGENTS.md` checkable
instead of aspirational. The formats themselves are listed here for
transparency.

## Engine runtimes the generated viewers load

The viewers do not animate anything themselves. Each pane loads the real engine
for its format and drives it, so what a comparison shows is the vendor's
interpretation of the file, not ours — which is the whole point of comparing.
Nothing is vendored: the pages fetch these from a CDN at view time, so viewing
a bundle needs network access. The converter itself never does.

| engine | where it comes from | license |
|---|---|---|
| `@esotericsoftware/spine-webgl@4.2.120` | unpkg, fetched by the Spine pane | Spine Runtimes License Agreement |
| `Retropaint/skelform-js@9bf4b6c14df5e3e59370606434161c4914d82e25` | jsDelivr, fetched by the SkelForm pane | MIT |
| `Retropaint/skelform-web-player@cd7451daea40eec177e6687a4c9a89d523d782ea` (`api.js`, the loader and draw loop) | jsDelivr, fetched by the SkelForm pane | **none declared** — see below |
| `jszip.js` | jsDelivr, via the player | MIT (JSZip, Stuk) |
| Godot Engine (web export) | the official binary, exported into each bundle | MIT |
| `DragonBones/DragonBonesJS@64b6c69ae35777c2404be68c9192e2c56906079e` (`Pixi/5.x/out/dragonBones.js`) | jsDelivr, fetched by the DragonBones pane | MIT |
| `pixi.js@5.3.12` | jsDelivr, fetched by the DragonBones pane | MIT |

The Spine runtime is loaded from a CDN rather than vendored, so nothing is
redistributed here, but the [Spine Runtimes
License](https://esotericsoftware.com/spine-runtimes-license) governs its use
exactly as it does for the optional validation harness below.

**`skelform-web-player` declares no license.** Its repository has no LICENSE
file, which defaults to all rights reserved. The SkelForm pane loads its
`api.js` for the archive loader (`SkfInit`) and the per-frame draw loop
(`SkfNewFrame`); the runtime it drives (`skelform-js`) *is* MIT. Either the
author grants a license, or that pane needs its own loader and draw loop over
the MIT runtime alone. Recorded rather than hidden: an unlicensed dependency on
the shipped-viewer path is a real hole, not a detail.

## Godot `.tscn` scenes

Godot Engine is MIT-licensed. The `.tscn` format is documented at
https://docs.godotengine.org. Test fixtures converted from the Godot demo
projects carry the MIT license of those projects.

## Spine JSON format

Spine is a product of Esoteric Software LLC. The JSON format is documented
publicly at https://esotericsoftware.com/spine-json-format and this tool writes
to that format for interoperability.

This tool does **not** bundle or redistribute any Spine Runtimes code. Two
places touch it, both by fetching it, never by shipping it: the Spine pane's
page (above) and the optional validation harness (which depends on
`@esotericsoftware/spine-core`). The
[Spine Runtimes License Agreement](https://esotericsoftware.com/spine-runtimes-license)
applies to both: each user must obtain their own Spine Editor license and
redistribution must include the license notice.

The Spine name and logo are trademarks of Esoteric Software LLC. This tool is
not affiliated with, endorsed by, or sponsored by Esoteric Software.

## DragonBones format

DragonBones is open source (MIT): the runtime this tool's pane loads is
`DragonBonesJS`, the same code LoongBones ships, fetched from a CDN rather than
vendored. Pixi 5 is its renderer, also MIT. The file format itself has no
license — a written `_ske.json` is the user's own data.

The 5.x host is pinned deliberately: the 8.x port does not refresh a weighted
mesh's vertex buffer after the first pose, which renders a skinned rig frozen
and scattered. Measured on the same bundle, 5.x tracks the model within 0.3
units while 8.x never changes a vertex.
