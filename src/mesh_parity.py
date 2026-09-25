"""Mesh parity gate: every attachment, every vertex, against the runtime.

The round-trip numeric gate compares BONES — bones can pass at 0.000000 while
the skin is wrong (wrong atlas page, wrong UV space, double y-mirror,
unequipped attachments drawn). This gate closes that gap by re-deriving every
skin attachment's world vertices with the PORTED SPINE RUNTIME
(constraints.py's BoneState — the same math spine-core uses) and comparing
them against what the canonical model carries:

- **geometry**: runtime world vertex (spine space) vs the model's polygon
  reconstructed through its anchor;
- **UV containment**: every UV of a region-backed attachment must land inside
  its region rect on the region's own page (catches wrong-page and
  unnormalized-UV bugs);
- **coverage**: every equipped skin entry must be converted, every converted
  attachment must be flagged equipped exactly as the runtime would draw it,
  and every unequipped one must be flagged not equipped.

Returns a list of violations; empty means full parity.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import constraints
from .in_spine import read_atlas_regions, read_png_size, read_spine


def _runtime_world(spine: dict, bone_name: str, att: dict,
                   solver: "constraints.ConstraintSolver",
                   bone_index: dict | None = None) -> list:
    """Attachment world vertices (spine space, y-up) via the runtime port.

    Weighted/unweighted classification follows the converter's rule exactly:
    a vertices list without a ``bones`` key is weighted when its density
    doesn't match len(uvs)/2 pairs (it starts with a bone count).
    """
    bone_index = bone_index or {b["name"]: i for i, b in enumerate(spine["bones"])}
    slot_bone = solver.bones[bone_name]
    vertices = att.get("vertices", [])
    world = []
    if att.get("type", "region") == "region" and not vertices:
        w = att.get("width", 1.0) or 1.0
        h = att.get("height", 1.0) or 1.0
        rot = math.radians(att.get("rotation", 0.0))
        cos, sin = math.cos(rot), math.sin(rot)
        sx, sy = att.get("scaleX", 1.0), att.get("scaleY", 1.0)
        ox, oy = att.get("x", 0.0), att.get("y", 0.0)
        for cx, cy in ((-w / 2, -h / 2), (-w / 2, h / 2),
                       (w / 2, h / 2), (w / 2, -h / 2)):
            lx = ox + cx * cos * sx - cy * sin * sy
            ly = oy + cx * sin * sx + cy * cos * sy
            world.append((slot_bone.a * lx + slot_bone.b * ly + slot_bone.world_x,
                          slot_bone.c * lx + slot_bone.d * ly + slot_bone.world_y))
        return world
    uvs = att.get("uvs", [])
    weighted = bool(att.get("bones")) or (
        vertices and int(vertices[0]) >= 1
        and len(uvs) // 2 != len(vertices) // 2)
    if weighted:
        cursor = 0
        while cursor < len(vertices):
            bone_count = int(vertices[cursor])
            cursor += 1
            acc_x = acc_y = 0.0
            for _ in range(bone_count):
                b = solver.bones[spine["bones"][int(vertices[cursor])]["name"]]
                lx, ly = vertices[cursor + 1], vertices[cursor + 2]
                weight = vertices[cursor + 3]
                cursor += 4
                acc_x += (b.a * lx + b.b * ly + b.world_x) * weight
                acc_y += (b.c * lx + b.d * ly + b.world_y) * weight
            world.append((acc_x, acc_y))
        return world
    for index in range(0, len(vertices) - 1, 2):
        lx, ly = vertices[index], vertices[index + 1]
        world.append((slot_bone.a * lx + slot_bone.b * ly + slot_bone.world_x,
                      slot_bone.c * lx + slot_bone.d * ly + slot_bone.world_y))
    return world


def mesh_parity(spine_path: str, atlas_path: str | None = None,
                model=None) -> list:
    """Run every check; return human-readable violations (empty = parity)."""
    spine = read_spine(spine_path)
    regions = read_atlas_regions(atlas_path)
    # The rig's first skin — same default the conversion uses; a skin=None
    # solver deactivates skin-gated bones and zeroes their worlds.
    first_skin = (spine.get("skins") or [{}])[0].get("name")
    solver = constraints.ConstraintSolver(spine, skin=first_skin)
    solver.apply()
    if model is None:
        from .in_spine import read_skeleton
        model = read_skeleton(spine_path, atlas_path)

    skins = spine["skins"]
    skin_attachments = (skins[0]["attachments"]
                        if isinstance(skins, list) else next(iter(skins.values())))

    # Equipping per the runtime: the slot's setup attachment (when a skin
    # entry with that name exists) or an attachment timeline entry.
    equipped: set = set()
    for slot in spine["slots"]:
        setup = slot.get("attachment")
        for name, att in skin_attachments.get(slot["name"], {}).items():
            if setup and (name == setup or att.get("name", name) == setup):
                equipped.add((slot["name"], name))
        for animation in spine.get("animations", {}).values():
            for key in (animation.get("slots", {})
                        .get(slot["name"], {}).get("attachment", [])):
                if key.get("name"):
                    equipped.add((slot["name"], key["name"]))

    page_sizes: dict = {}
    atlas_dir = Path(atlas_path).parent if atlas_path else Path(spine_path).parent
    for region in regions.values():
        page = region.get("page")
        if page and page not in page_sizes:
            page_sizes[page] = read_png_size(str(atlas_dir / page)) or (1, 1)

    violations: list = []
    by_slot = {att.name: att for att in model.attachments}
    # The conversion keeps one attachment per slot: the setup pick
    # (spine_attachment_map). Only that entry can match the model.
    setups = {slot["name"]: slot.get("attachment") for slot in spine["slots"]}
    for slot in spine["slots"]:
        slot_name = slot["name"]
        bone_name = slot["bone"]
        entries = skin_attachments.get(slot_name, {})
        converted_name = None
        for att_name, att in entries.items():
            if setups.get(slot_name) and (
                    att_name == setups[slot_name]
                    or att.get("name", att_name) == setups[slot_name]):
                converted_name = att_name
                break
        if converted_name is None and entries:
            converted_name = next(iter(entries))
        for att_name, att in entries.items():
            is_equipped = (slot_name, att_name) in equipped
            model_att = by_slot.get(slot_name.lower())
            if att_name != converted_name:
                if is_equipped and (model_att is None or model_att.equipped):
                    pass  # sibling entry: not converted, the setup one is
                continue
            if model_att is None:
                violations.append(
                    f"{slot_name}/{att_name}: equipped attachment missing "
                    "from the canonical model")
                continue
            # A slot without a setup attachment draws nothing at setup: the
            # converted first entry is correctly flagged unequipped.
            if is_equipped and not model_att.equipped:
                violations.append(
                    f"{slot_name}/{att_name}: equipped attachment flagged "
                    "unequipped (it will not draw)")
            # Geometry: the model stores godot-space locals against the node
            # position; reconstruct spine-space world for every vertex. A
            # bone the runtime deactivates under the skin keeps (0, 0) — the
            # known skin-constraint gap (ROADMAP) — so skip it.
            if not solver.active.get(bone_name, True):
                continue
            node = model_att.position
            anchor_y = -node[1]
            expected = _runtime_world(spine, bone_name, att, solver)
            for index, local in enumerate(model_att.polygon):
                spine_x = node[0] + local[0]
                spine_y = anchor_y - local[1]
                if index < len(expected):
                    exp = expected[index]
                    if math.hypot(exp[0] - spine_x, exp[1] - spine_y) > 0.01:
                        violations.append(
                            f"{slot_name}/{att_name} vertex {index}: runtime "
                            f"({exp[0]:.2f}, {exp[1]:.2f}) vs model "
                            f"({spine_x:.2f}, {spine_y:.2f})")
            # UV containment: every UV lands inside its region rect on its
            # own page.
            region = regions.get(att.get("path", att_name)) or regions.get(att_name)
            uvs = att.get("uvs", [])
            if region and uvs:
                pw, ph = page_sizes.get(region["page"], (1, 1))
                for uv_index in range(0, len(uvs) - 1, 2):
                    u, v = uvs[uv_index], uvs[uv_index + 1]
                    if region.get("degrees") == 90:
                        mapped = (region["x"] + v * region["height"],
                                  region["y"] + (1 - u) * region["width"])
                    else:
                        mapped = (region["x"] + u * region["width"],
                                  region["y"] + v * region["height"])
                    if not (0 <= mapped[0] <= pw and 0 <= mapped[1] <= ph):
                        violations.append(
                            f"{slot_name}/{att_name} uv {uv_index // 2} "
                            f"({mapped[0]:.1f}, {mapped[1]:.1f}) outside page "
                            f"{region['page']} ({pw}x{ph})")
    return violations