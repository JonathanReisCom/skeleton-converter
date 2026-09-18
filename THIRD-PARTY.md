# Third-party formats and terms

This tool reads and writes data files. It contains no third-party code and no
third-party runtimes. The formats themselves are listed here for transparency.

## Godot `.tscn` scenes

Godot Engine is MIT-licensed. The `.tscn` format is documented at
https://docs.godotengine.org. Test fixtures converted from the Godot demo
projects carry the MIT license of those projects.

## Spine JSON format

Spine is a product of Esoteric Software LLC. The JSON format is documented
publicly at https://esotericsoftware.com/spine-json-format and this tool writes
to that format for interoperability.

This tool does **not** bundle, redistribute, or integrate any Spine Runtimes
code. If you use the optional validation harness (which depends on
`@esotericsoftware/spine-core`), the
[Spine Runtimes License Agreement](https://esotericsoftware.com/spine-runtimes-license)
applies to that dependency: each user must obtain their own Spine Editor
license and redistribution must include the license notice.

The Spine name and logo are trademarks of Esoteric Software LLC. This tool is
not affiliated with, endorsed by, or sponsored by Esoteric Software.

## DragonBones format (planned)

DragonBones is open source. Runtime libraries are BSD-licensed.
