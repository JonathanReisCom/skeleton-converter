"""Format registry and dispatch. Adding a format means writing two adapters.

A reader module may also expose ``extract_assets(path, dest) -> list[str]``
for a format whose images live *inside* the input (an archive, a bundle): the
bundle calls it once the output directory exists and uses the first returned
page as the rig's texture. Formats whose images sit beside the input need no
hook — the reader sets the model's texture path while reading.
"""

from __future__ import annotations

from . import (in_dragonbones, in_godot, in_skelform, in_spine, out_dragonbones,
               out_godot, out_skelform, out_spine, out_tres)

# Format name → module, for the optional capabilities a reader exposes next to
# its entry point (`extract_assets`, see the module docstring).
READER_MODULES = {
    "godot": in_godot,
    "spine": in_spine,
    "skelform": in_skelform,
    "dragonbones": in_dragonbones,
}

# Format name → (reader, writer)
READERS = {
    "godot": in_godot.read_godot_skeleton,
    "spine": in_spine.read_skeleton,
    "skelform": in_skelform.read_skeleton,
    "dragonbones": in_dragonbones.read_skeleton,
}
# `tres` is write-only: a .tres is a Resource file, and the readers read rig
# files (.tscn / Spine JSON), not resource containers.
WRITERS = {
    "spine": out_spine.write_spine_json,
    "godot": out_godot.write_godot_scene,
    "tres": out_tres.write_tres_resource,
    "skelform": out_skelform.write_skelform,
    "dragonbones": out_dragonbones.write_dragonbones,
}
