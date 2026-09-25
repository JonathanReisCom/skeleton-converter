"""Write the canonical model as a Godot 4 ``.tres`` resource.

Why a ``.tres`` and what it can hold
------------------------------------
``.tres`` is Godot's *Resource* container: it serializes exactly one Resource
(plus its sub-resources). ``Skeleton2D``, ``Bone2D``, ``Polygon2D`` and
``AnimationPlayer`` are **Nodes** (``Object``, not ``Resource``), so a node
graph cannot be stored in a ``.tres`` — that is what ``.tscn`` is for. Godot
itself splits the same way in real projects: the scene holds the nodes, and the
``AnimationLibrary`` lives in its own ``.tres``/``.res`` beside it, added with
``AnimationPlayer.add_animation_library()``.

What this writer therefore emits, and what it deliberately cannot:

* **Animations — complete.** Every animation, verbatim, is the same
  ``[sub_resource type="Animation"]`` block ``out_godot`` embeds in the
  ``.tscn`` (one source of truth for the Godot animation grammar, including
  the bezier handle solve). The root ``[resource]`` is the ``AnimationLibrary``
  that maps the real animation names to those sub-resources.
* **Bones — rest data only, as resource metadata.** Node properties have no
  resource equivalent, so each bone's rest table (name, parent, skeleton
  path, local position/rotation/scale, length, bind ``rest`` Transform2D, and
  the Spine inherit mode) travels as ``metadata/bones`` on the resource —
  engine-loadable and readable via ``get_meta("bones")``, not a node tree.
* **Attachment geometry, textures, and the node graph — not representable.**
  ``Polygon2D`` vertices/UVs/weights and the ``Texture2D`` reference are scene
  data; a ``.tres`` has nowhere to put them without inventing a resource class
  (which would need a shipped ``.gd`` script). They remain in the ``.tscn``
  produced by ``--to godot``.

Track paths still address the scene path (``Sprite2D/Skeleton2D/<bone>:...``),
so this resource is meant to be added to that scene::

    var library := load("res://<name>.tres") as AnimationLibrary
    $AnimationPlayer.add_animation_library("", library)
"""

from __future__ import annotations

import json
from pathlib import Path

from .model import Skeleton, godot_transform2d
# Deliberate reuse of the scene writer's animation emitter: it owns the Godot
# animation grammar (track layout, bezier handles, sanitized resource ids), and
# a copy here would drift from it. Private in name only — same package.
from .out_godot import _emit_animations


def _quote(text: str) -> str:
    """A Godot string literal. ``json.dumps`` escaping is a subset Godot reads."""
    return json.dumps(str(text))


def _bone_literal(bone) -> str:
    """One bone's rest data as a Godot Dictionary literal.

    ``rest`` falls back to the node pose exactly like ``out_godot`` does (Godot
    skins with ``pose * rest^-1``), so the bind basis survives even when the
    model carries no separate rest.
    """
    rest_pos, rest_rot, rest_scale = bone.rest or (
        bone.position, bone.rotation_deg, bone.scale)
    rest = godot_transform2d(rest_pos, rest_rot, rest_scale)
    fields = [
        ("name", f"&{_quote(bone.name)}"),
        ("parent", f"&{_quote(bone.parent or '')}"),
        ("path", f"&{_quote(bone.path)}"),
        ("position", f"Vector2({round(bone.position[0], 6)}, "
                     f"{round(bone.position[1], 6)})"),
        ("rotation_degrees", f"{round(bone.rotation_deg, 6)}"),
        ("scale", f"Vector2({round(bone.scale[0], 6)}, "
                  f"{round(bone.scale[1], 6)})"),
        ("length", f"{round(bone.length, 6)}"),
        ("inherit", f"&{_quote(bone.inherit)}"),
        ("rest", "Transform2D(%s)" % ", ".join(
            f"{round(value, 6)}" for value in rest)),
    ]
    body = "".join(f'\n"{key}": {value},' for key, value in fields)
    return "{" + body + "\n}"


def _render_tres(animation_resources, animation_refs, model) -> str:
    load_steps = len(animation_resources) + 1
    lines = [f'[gd_resource type="AnimationLibrary" load_steps={load_steps} '
             f'format=3]', ""]
    for entry in animation_resources:
        lines.extend(entry["lines"])
        lines.append("")
    lines.append("[resource]")
    lines.append("_data = {")
    for anim_name, resource_id in animation_refs:
        lines.append(f'&"{anim_name}": SubResource("{resource_id}"),')
    lines.append("}")
    if model.bones:
        lines.append("metadata/bones = [%s]" % ",\n".join(
            _bone_literal(bone) for bone in model.bones))
    lines.append("")
    return "\n".join(lines)


def write_tres_resource(model: Skeleton, output_path: str, **kwargs) -> None:
    """Emit a ``.tres`` AnimationLibrary: every animation, plus the bone rest
    table as ``metadata/bones``. See the module docstring for the gaps."""
    animation_resources, animation_refs = _emit_animations(model)
    content = _render_tres(animation_resources, animation_refs, model)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
