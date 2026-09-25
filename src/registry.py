"""Format registry and dispatch. Adding a format means writing two adapters."""

from __future__ import annotations

from . import in_godot, in_spine, out_godot, out_spine, out_tres

# Format name → (reader, writer)
READERS = {"godot": in_godot.read_godot_skeleton, "spine": in_spine.read_skeleton}
# `tres` is write-only: a .tres is a Resource file, and the readers read rig
# files (.tscn / Spine JSON), not resource containers.
WRITERS = {
    "spine": out_spine.write_spine_json,
    "godot": out_godot.write_godot_scene,
    "tres": out_tres.write_tres_resource,
}
