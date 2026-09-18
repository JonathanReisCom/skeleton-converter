#!/usr/bin/env python3
"""spine_godot_converter: bidirectional converter between Godot 4 Skeleton2D scenes
(.tscn) and Spine JSON skeleton exports.

Commands
--------
  convert --to spine  scene.tscn -o out.json
  convert --to godot  skeleton.json -o out.tscn
  compare             a.tscn b.json          # numeric round-trip validation

Coordinate mapping
------------------
Godot 2D is Y-down; Spine is Y-up. Mirroring with F = diag(1, -1) makes the
whole tree map consistently:

    spine.x = godot.x        spine.y = -godot.y
    spine.rotation = -godot.rotation        local vertex y negated

Proof sketch: with W_s = F·W_g·F, T(a,-b) = F·T(a,b)·F and F·R(θ) = R(-θ)·F,
a Spine bone world matrix equals F·(Godot bone world)·F, so a point with local
coords F·p lands at F·(Godot world position of p). Same pose, mirrored axis.
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# .tscn parsing
# ---------------------------------------------------------------------------

VEC_RE = re.compile(r"Vector2\(([-\d.eE]+), ([-\d.eE]+)\)")
F32_RE = re.compile(r"PackedFloat32Array\(([^)]*)\)")
I32_RE = re.compile(r"PackedInt32Array\(([^)]*)\)")
VEC2_PACKED_RE = re.compile(r"PackedVector2Array\(([^)]*)\)", re.S)


def parse_vec(text):
    match = VEC_RE.fullmatch(text.strip())
    return [float(match.group(1)), float(match.group(2))] if match else None


def parse_float_array(text):
    match = F32_RE.fullmatch(text.strip())
    if not match:
        return None
    return [float(p) for p in match.group(1).replace("\n", " ").split(",") if p.strip()]


def parse_int_array(text):
    match = I32_RE.fullmatch(text.strip())
    if not match:
        return None
    return [int(p) for p in match.group(1).replace("\n", " ").split(",") if p.strip()]


def parse_packed_vec2(text):
    match = VEC2_PACKED_RE.match(text.strip())
    if not match:
        return None
    numbers = [float(p) for p in match.group(1).replace("\n", " ").split(",") if p.strip()]
    return [[numbers[i], numbers[i + 1]] for i in range(0, len(numbers) - 1, 2)]


def extract_balanced(text, start):
    """Body of the bracket group opening at `start`, plus the index after it."""
    open_char = text[start]
    close_char = {"(": ")", "[": "]"}[open_char]
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    return text[start + 1:], len(text)


def parse_value(raw):
    raw = raw.strip()
    if raw.startswith("Vector2("):
        return parse_vec(raw)
    if raw.startswith("PackedFloat32Array("):
        return parse_float_array(raw)
    if raw.startswith("PackedInt32Array("):
        return parse_int_array(raw)
    if raw.startswith("PackedVector2Array("):
        return parse_packed_vec2(raw)
    if raw.startswith("NodePath("):
        return raw[len("NodePath("):-1].strip('"')
    if raw.startswith('&"') and raw.endswith('"'):
        return raw[2:-1]
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return float(raw) if any(c in raw for c in ".eE") else int(raw)
    except ValueError:
        pass
    if raw.startswith("["):
        out = []
        for piece in re.findall(
            r"PackedFloat32Array\([^)]*\)|PackedInt32Array\([^)]*\)|\"[^\"]*\"", raw, re.S
        ):
            if piece.startswith("PackedInt32Array"):
                out.append(parse_int_array(piece))
            elif piece.startswith("PackedFloat32Array"):
                out.append(parse_float_array(piece))
            else:
                out.append(piece.strip('"'))
        return out
    if raw.startswith("{"):
        out = {}
        for key, value in re.findall(r'&?"([^"]+)":\s*([^,\n]+)', raw):
            out[key] = value.strip()
        return out or raw
    return raw


def parse_animation_keys(raw):
    """Godot 4 Animation `keys` dict -> {times, transitions, update, values}."""
    out = {}
    for key in ("times", "transitions", "update"):
        match = re.search(r'"%s":\s*' % key, raw)
        if not match:
            continue
        rest = raw[match.end():].strip()
        if rest.startswith("PackedFloat32Array("):
            body, _ = extract_balanced(raw, match.end() + rest.find("("))
            out[key] = [float(p) for p in body.replace("\n", " ").split(",") if p.strip()]
        else:
            token = re.match(r"[^,\n}]+", rest)
            out[key] = parse_value(token.group(0).strip()) if token else None
    match = re.search(r'"values":\s*\[', raw)
    if match:
        body, _ = extract_balanced(raw, match.end() - 1)
        if "Vector2(" in body:
            out["values"] = [parse_vec(v) for v in re.findall(r"Vector2\([^)]*\)", body)]
        elif body.strip():
            out["values"] = [parse_value(v.strip()) for v in split_top(body)]
    return out


def split_top(body):
    depth = 0
    current = []
    out = []
    for char in body:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        out.append("".join(current))
    return out


def parse_tscn(path):
    text = Path(path).read_text()
    ext_resources = []
    sub_resources = {}
    nodes = []
    lines = text.splitlines()
    index = 0
    current = None
    while index < len(lines):
        line = lines[index].rstrip()
        index += 1
        if line.startswith("[ext_resource"):
            ext_resources.append(dict(re.findall(r'(\w+)="([^"]*)"', line)))
            continue
        if line.startswith("[sub_resource"):
            match = re.search(r'type="([^"]+)" id="([^"]+)"', line)
            if match:
                current = {"type": match.group(1), "props": {}}
                sub_resources[match.group(2)] = current
            else:
                current = None
            continue
        if line.startswith("[node"):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', line))
            current = {
                "name": attrs.get("name"),
                "type": attrs.get("type", ""),
                "parent": attrs.get("parent", ""),
                "props": {},
            }
            nodes.append(current)
            continue
        if line.startswith("["):
            current = None
            continue
        if current is None or "=" not in line:
            continue

        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("{") and not raw.endswith("}"):
            while index < len(lines) and lines[index - 1].rstrip() != "}":
                raw += "\n" + lines[index].rstrip()
                index += 1
        elif raw.startswith("[") and raw.count("[") != raw.count("]"):
            while index < len(lines) and raw.count("[") != raw.count("]"):
                raw += "\n" + lines[index].rstrip()
                index += 1
        elif raw.startswith("(") and not raw.endswith(")"):
            while index < len(lines) and not lines[index - 1].rstrip().endswith(")"):
                raw += "\n" + lines[index].rstrip()
                index += 1

        if key.endswith("keys") and raw.startswith("{"):
            current["props"][key] = parse_animation_keys(raw)
        else:
            current["props"][key] = parse_value(raw)

    return {"ext_resources": ext_resources, "sub_resources": sub_resources, "nodes": nodes}


# ---------------------------------------------------------------------------
# Small 2D affine helpers (row-major [a, b, c, d, tx, ty])
# ---------------------------------------------------------------------------

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(left, right):
    a1, b1, c1, d1, tx1, ty1 = left
    a2, b2, c2, d2, tx2, ty2 = right
    return (
        a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
        a1 * tx2 + b1 * ty2 + tx1, c1 * tx2 + d1 * ty2 + ty1,
    )


def transform(matrix, point):
    a, b, c, d, tx, ty = matrix
    return (a * point[0] + b * point[1] + tx, c * point[0] + d * point[1] + ty)


def invert(matrix):
    """Inverse of [a b; c d] + translation: A^-1 = 1/det [d -b; -c a], t' = -A^-1·t."""
    a, b, c, d, tx, ty = matrix
    det = a * d - b * c
    if abs(det) < 1e-12:
        return IDENTITY
    inv = 1.0 / det
    return (
        d * inv, -b * inv,
        -c * inv, a * inv,
        (b * ty - d * tx) * inv,
        (c * tx - a * ty) * inv,
    )


def godot_transform2d(position, rotation_deg, scale=(1.0, 1.0)):
    """A Godot Transform2D literal for a local transform.

    Godot's convention differs from the right-handed one used elsewhere in this
    file: its columns are x = (cos, sin) and y = (-sin, cos) (see the engine's
    own scenes, e.g. `rest = Transform2D(0.3366, 0.9416, -0.9416, 0.3366, ...)`
    for a 70.3° rotation). Using the other convention silently shears every
    skinned vertex, because Godot skins with `accum * rest.inverse()`.
    """
    radians = math.radians(rotation_deg)
    cos, sin = math.cos(radians), math.sin(radians)
    return (
        cos * scale[0], sin * scale[0],
        -sin * scale[1], cos * scale[1],
        position[0], position[1],
    )


def compose(position, rotation_deg, scale=(1.0, 1.0)):
    radians = math.radians(rotation_deg)
    cos, sin = math.cos(radians), math.sin(radians)
    return (
        cos * scale[0], -sin * scale[1],
        sin * scale[0], cos * scale[1],
        position[0], position[1],
    )


# ---------------------------------------------------------------------------
# Godot scene -> intermediate skeleton
# ---------------------------------------------------------------------------

class Bone:
    def __init__(self, name, parent, position, rotation_deg, scale):
        self.name = name
        self.parent = parent
        self.position = position
        self.rotation_deg = rotation_deg
        self.scale = scale
        self.length = 0.0
        self.attachments = []


class GodotSkeleton:
    def __init__(self):
        self.bones = []            # in scene order (parents before children)
        self.by_name = {}
        self.attachments = []      # {name, bone, polygon, uv, weights, polygons}
        self.animations = {}       # name -> {bone: {'rotate': [...], 'translate': [...]}}
        self.texture_path = None


def node_path(node):
    return node["name"] if node["parent"] == "." else f'{node["parent"]}/{node["name"]}'


def read_godot_skeleton(tscn_path):
    scene = parse_tscn(tscn_path)
    by_path = {node_path(node): node for node in scene["nodes"]}

    skeleton_path = next((p for p, n in by_path.items() if n["type"] == "Skeleton2D"), None)
    if skeleton_path is None:
        raise SystemExit("no Skeleton2D node found in scene")
    prefix = skeleton_path + "/"

    model = GodotSkeleton()
    model.texture_path = next(
        (e["path"] for e in scene["ext_resources"] if e["type"] == "Texture2D"), None
    )

    for path, node in by_path.items():
        if node["type"] != "Bone2D" or not path.startswith(prefix):
            continue
        relative = path[len(prefix):]
        props = node["props"]
        position = props.get("position", [0.0, 0.0])
        rotation_deg = math.degrees(props.get("rotation", 0.0))
        scale = props.get("scale", [1.0, 1.0])
        rest = props.get("rest")
        if isinstance(rest, list) and len(rest) == 6:
            xx, xy, _yx, _yy, ox, oy = rest
            position = [ox, oy]
            rotation_deg = math.degrees(math.atan2(xy, xx))
        parent = "/".join(relative.split("/")[:-1]) or None
        bone = Bone(relative.split("/")[-1], parent.split("/")[-1] if parent else None,
                    position, rotation_deg, scale)
        bone.length = props.get("length", 0.0)
        bone.path = relative
        model.bones.append(bone)
        model.by_name[bone.name] = bone

    # Polygon2D attachments live in a sibling node of the skeleton
    polygons_path = skeleton_path.rsplit("/", 1)[0] + "/Polygons"
    for path, node in by_path.items():
        if node["type"] != "Polygon2D" or not path.startswith(polygons_path + "/"):
            continue
        props = node["props"]
        weights = parse_polygon_weights(props.get("bones", []))
        model.attachments.append({
            "name": path.rsplit("/", 1)[-1].lower().replace(" ", "-"),
            "polygon": props.get("polygon", []),
            "uv": props.get("uv") or props.get("polygon", []),
            "polygons": props.get("polygons", []),
            "weights": weights,
            # Godot applies the node's `position` as a transform and adds
            # `offset` to every vertex (`points[i] = polygon[i] + offset`).
            "position": props.get("position", [0.0, 0.0]),
            "offset": props.get("offset", [0.0, 0.0]),
            # Vertices past this count are internal (added by the editor's
            # triangulation) and are dropped only when `polygons` is empty.
            "internal_vertices": props.get("internal_vertex_count", 0),
        })

    library = next(
        (s for s in scene["sub_resources"].values() if s["type"] == "AnimationLibrary"), None
    )
    if library:
        for anim_name, reference in (library["props"].get("_data") or {}).items():
            match = re.match(r'SubResource\("([^"]+)"\)', str(reference))
            animation = scene["sub_resources"].get(match.group(1)) if match else None
            if animation and animation["type"] == "Animation":
                model.animations[anim_name] = read_godot_animation(animation, prefix)
    return model


def parse_polygon_weights(raw):
    """Polygon2D `bones` property -> [(bone_name, weights per vertex)]."""
    if not isinstance(raw, list):
        return []
    out = []
    index = 0
    while index + 1 < len(raw):
        bone_path = raw[index]
        weights = raw[index + 1] if isinstance(raw[index + 1], list) else []
        out.append((str(bone_path).split("/")[-1], weights))
        index += 2
    return out


def read_godot_animation(animation, prefix):
    props = animation["props"]
    tracks = {}
    index = 0
    while f"tracks/{index}/type" in props:
        path = props.get(f"tracks/{index}/path", "")
        keys = props.get(f"tracks/{index}/keys") or {}
        node_path_value, _, property_name = str(path).partition(":")
        bone_name = node_path_value[len(prefix):].split("/")[-1] if node_path_value.startswith(prefix) else None
        times = keys.get("times", [])
        values = keys.get("values", [])
        if bone_name and property_name == "rotation_degrees":
            tracks.setdefault(bone_name, {})["rotate"] = [
                {"time": float(t), "angle": float(v)} for t, v in zip(times, values)
            ]
        elif bone_name and property_name == "position":
            tracks.setdefault(bone_name, {})["translate"] = [
                {"time": float(t), "x": float(v[0]), "y": float(v[1])}
                for t, v in zip(times, values) if isinstance(v, list)
            ]
        index += 1
    return tracks


# ---------------------------------------------------------------------------
# Forward kinematics in Godot space
# ---------------------------------------------------------------------------

def godot_world_transforms(model):
    """bone name -> 2D affine world matrix in Godot space."""
    world = {}
    for bone in model.bones:
        parent = world.get(bone.parent, IDENTITY)
        world[bone.name] = multiply(parent, compose(bone.position, bone.rotation_deg, bone.scale))
    return world


def mirror_matrix(matrix):
    """F·M·F with F = diag(1,-1): flip input and output Y."""
    a, b, c, d, tx, ty = matrix
    return (a, -b, -c, d, tx, -ty)


def mirror_point(point):
    return (point[0], -point[1])


# ---------------------------------------------------------------------------
# Godot -> Spine
# ---------------------------------------------------------------------------

def godot_to_spine(model, image_name=None):
    world = godot_world_transforms(model)
    bone_index = {bone.name: index for index, bone in enumerate(model.bones)}

    spine = {
        "skeleton": {
            "spine": "4.1.23",
            "x": 0, "y": 0, "width": 512, "height": 512,
            "images": "./images/",
        },
        "bones": [],
        "slots": [],
        "skins": [{"name": "default", "attachments": {}}],
        "animations": {},
    }

    for bone in model.bones:
        entry = {"name": bone.name}
        if bone.parent:
            entry["parent"] = bone.parent
        entry["x"] = round(bone.position[0], 4)
        entry["y"] = round(-bone.position[1], 4)
        entry["rotation"] = round(-bone.rotation_deg, 4)
        if bone.length:
            entry["length"] = round(bone.length, 4)
        if abs(bone.scale[0] - 1.0) > 1e-6 or abs(bone.scale[1] - 1.0) > 1e-6:
            entry["scaleX"] = round(bone.scale[0], 4)
            entry["scaleY"] = round(bone.scale[1], 4)
        spine["bones"].append(entry)

    for attachment in model.attachments:
        internal = attachment.get("internal_vertices", 0)
        polygon = attachment["polygon"]
        uv = attachment["uv"]
        # Godot excludes the internal vertices only when it must triangulate the
        # polygon itself (`polygons` empty); an explicit index list draws them all.
        if internal and not attachment["polygons"]:
            polygon = polygon[:len(polygon) - internal]
            uv = uv[:len(uv) - internal]
        weights = attachment["weights"]
        # Node transform (position) then per-vertex offset, matching Godot.
        polygon_world = compose(attachment["position"], 0.0)
        vertex_offset = attachment["offset"]
        uv_min_x = min(p[0] for p in uv)
        uv_min_y = min(p[1] for p in uv)
        span_x = (max(p[0] for p in uv) - uv_min_x) or 1.0
        span_y = (max(p[1] for p in uv) - uv_min_y) or 1.0

        # Which bone hosts the slot: the heaviest influence on the mesh.
        influence = {}
        for bone_name, weight_list in weights:
            total = sum(w for w in weight_list)
            if total > 0:
                influence[bone_name] = total
        host = max(influence, key=influence.get) if influence else model.bones[0].name

        entry = {
            "type": "mesh",
            "uvs": [],
            "triangles": triangulate(attachment["polygons"], len(polygon)),
            "width": round(span_x, 4),
            "height": round(span_y, 4),
        }
        for u, v in uv:
            entry["uvs"].extend([
                round((u - uv_min_x) / span_x, 6),
                round((v - uv_min_y) / span_y, 6),
            ])

        if weights:
            # Spine weighted mesh: per vertex [boneCount, (boneIndex, localX, localY, weight)...]
            # Local coordinates are relative to each influencing bone, in Spine
            # (mirrored) space, so compute the Godot world position first.
            bones_used = []
            for bone_name, _ in weights:
                if bone_name in bone_index and bone_name not in bones_used:
                    bones_used.append(bone_name)
            vertices = []
            for vertex_index, vertex in enumerate(polygon):
                world_point = transform(polygon_world, (vertex[0] + vertex_offset[0], vertex[1] + vertex_offset[1]))
                entries = []
                for bone_name, weight_list in weights:
                    if bone_name not in bone_index or vertex_index >= len(weight_list):
                        continue
                    weight = weight_list[vertex_index]
                    if weight <= 0:
                        continue
                    local = transform(invert(world[bone_name]), world_point)
                    entries.append((bone_name, mirror_point(local), weight))
                if not entries:
                    continue
                total = sum(w for _, _, w in entries)
                vertices.append(len(entries))
                for bone_name, local, weight in entries:
                    vertices.extend([
                        bone_index[bone_name],
                        round(local[0], 4),
                        round(local[1], 4),
                        round(weight / total, 6) if total else 1.0,
                    ])
            entry["bones"] = [bone_index[name] for name in bones_used]
            entry["vertices"] = vertices
        else:
            entry["vertices"] = [
                round(value, 4)
                for vertex in polygon
                for value in mirror_point(transform(polygon_world, (vertex[0] + vertex_offset[0], vertex[1] + vertex_offset[1])))
            ]

        spine["slots"].append({
            "name": attachment["name"],
            "bone": host,
            "attachment": attachment["name"],
        })
        spine["skins"][0]["attachments"].setdefault(attachment["name"], {})[attachment["name"]] = entry

    for anim_name, tracks in model.animations.items():
        animation = {"bones": {}}
        for bone_name, properties in tracks.items():
            bone = model.by_name.get(bone_name)
            bone_tracks = {}
            if properties.get("rotate"):
                # Spine rotate keys are offsets from the bone's setup rotation,
                # while Godot animates the absolute local rotation.
                setup_rotation = bone.rotation_deg if bone else 0.0
                bone_tracks["rotate"] = [
                    {"time": round(k["time"], 6), "value": round(-(k["angle"] - setup_rotation), 6)}
                    for k in properties["rotate"]
                ]
            if properties.get("translate"):
                # Same for translate: Godot's position is absolute, Spine's is
                # an offset from the setup position, mirrored on Y.
                setup_position = bone.position if bone else (0.0, 0.0)
                bone_tracks["translate"] = [
                    {
                        "time": round(k["time"], 6),
                        "x": round(k["x"] - setup_position[0], 6),
                        "y": round(-(k["y"] - setup_position[1]), 6),
                    }
                    for k in properties["translate"]
                ]
            if bone_tracks:
                animation["bones"][bone_name] = bone_tracks
        spine["animations"][anim_name] = animation

    return spine


def resolve_texture_path(texture_path, scene_path):
    """Turn a scene's `res://...` texture reference into a filesystem path."""
    if not texture_path:
        return None
    if texture_path.startswith("res://"):
        project_root = Path(scene_path).resolve().parent
        # Walk up until the directory holding project.godot
        for candidate in [project_root, *project_root.parents]:
            if (candidate / "project.godot").exists():
                return candidate / texture_path[len("res://"):]
        return None
    return Path(texture_path)


def read_png_size(path):
    """Width/height from a PNG's IHDR chunk, so the atlas declares the truth.

    Godot UVs are texture pixels and the atlas divides by the page size, so a
    wrong declared size silently mis-samples every region.
    """
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return (
        int.from_bytes(header[16:20], "big"),
        int.from_bytes(header[20:24], "big"),
    )


def emit_atlas(model, spine, atlas_path, image_name, image_path=None):
    """Write a Spine atlas whose region bounds come from the Polygon2D UV rects.

    Godot stores UVs in texture pixels; the atlas region is the rectangle they
    span, so region-u = pixel-u / texture size when the atlas is the full page.
    """
    size = read_png_size(image_path) if image_path else None
    width, height = size if size else (1024, 1024)
    lines = [image_name, f"\tsize: {width}, {height}", "\tfilter: Linear, Linear"]
    for attachment in model.attachments:
        uv = attachment["uv"]
        if not uv:
            continue
        min_x = min(p[0] for p in uv)
        min_y = min(p[1] for p in uv)
        span_x = (max(p[0] for p in uv) - min_x) or 1.0
        span_y = (max(p[1] for p in uv) - min_y) or 1.0
        lines.append(attachment["name"])
        lines.append(
            "\tbounds: %d, %d, %d, %d" % (
                int(round(min_x)), int(round(min_y)),
                int(round(span_x)), int(round(span_y)),
            )
        )
    Path(atlas_path).write_text("\n".join(lines) + "\n")
    return atlas_path


def triangulate(polygons, vertex_count):
    triangles = []
    for group in polygons or []:
        for index in range(1, len(group) - 1):
            triangles.extend([group[0], group[index], group[index + 1]])
    if not triangles:
        for index in range(1, vertex_count - 1):
            triangles.extend([0, index, index + 1])
    return triangles


# ---------------------------------------------------------------------------
# Spine -> Godot
# ---------------------------------------------------------------------------

def read_spine(path):
    data = json.loads(Path(path).read_text())
    data.setdefault("bones", [])
    data.setdefault("slots", [])
    data.setdefault("skins", [])
    data.setdefault("animations", {})
    return data


def spine_attachment_map(spine):
    """slot name -> (attachment name, attachment dict) from the default skin.

    The attachment name matters: it is the region name used to look up the
    atlas, and it often differs from the slot name (`upper-arm2` vs `upperarm2`).
    """
    skins = spine["skins"]
    attachments = skins[0]["attachments"] if isinstance(skins, list) else next(iter(skins.values()))
    out = {}
    for slot_name, entries in attachments.items():
        for attachment_name, attachment in entries.items():
            out[slot_name] = (attachment_name, attachment)
            break
    return out


def read_atlas_regions(atlas_path):
    """region name -> (x, y, width, height, degrees) from a Spine atlas.

    Godot's Polygon2D wants UVs in texture pixels, and a region's pixels live at
    its atlas rect — not at the texture origin.
    """
    regions = {}
    if not atlas_path or not Path(atlas_path).exists():
        return regions
    current = None
    for line in Path(atlas_path).read_text().splitlines():
        if not line.strip():
            current = None
            continue
        if line.startswith((" ", "\t")):
            if current is None:
                continue
            key, _, value = line.strip().partition(":")
            key = key.strip()
            value = value.strip()
            if key == "bounds":
                numbers = [int(float(p.strip())) for p in value.split(",") if p.strip()]
                if len(numbers) >= 4:
                    regions[current] = {
                        "x": numbers[0], "y": numbers[1],
                        "width": numbers[2], "height": numbers[3], "degrees": 0,
                        "originalWidth": numbers[2], "originalHeight": numbers[3],
                        "offsetX": 0, "offsetY": 0,
                    }
            elif key == "rotate" and current in regions:
                regions[current]["degrees"] = int(float(value))
            elif key == "offsets" and current in regions:
                offsets = [int(float(p.strip())) for p in value.split(",") if p.strip()]
                if len(offsets) >= 2:
                    regions[current]["offsetX"] = offsets[0]
                    regions[current]["offsetY"] = offsets[1]
            elif key == "orig" and current in regions:
                orig = [int(float(p.strip())) for p in value.split(",") if p.strip()]
                if len(orig) >= 2:
                    regions[current]["originalWidth"] = orig[0]
                    regions[current]["originalHeight"] = orig[1]
            continue
        # a line with no indentation starts a new region
        current = line.strip()
    return regions


def resolve_image_for_atlas(atlas_path, texture_path):
    """Filesystem path of the PNG the atlas page names, for page-size lookup."""
    if atlas_path and Path(atlas_path).exists():
        for line in Path(atlas_path).read_text().splitlines():
            if line and not line.startswith((" ", "\t")):
                return Path(atlas_path).parent / line.strip()
    return texture_path


def region_uv_rect(region, page_width, page_height):
    """Page-pixel UV rectangle of an atlas region, honouring 90° rotation.

    Mirrors TextureAtlasRegion's own u/v/u2/v2 computation: a region rotated 90°
    occupies (width, height) swapped in the page.
    """
    x, y = region["x"], region["y"]
    width, height = region["width"], region["height"]
    if region.get("degrees") == 90:
        return (x, y, x + height, y + width)
    return (x, y, x + width, y + height)


def region_corner_uvs(region, page_width, page_height):
    """Page-pixel UVs for a region attachment's four corners.

    Order matches the runtime's vertex order (bottom-left, top-left, top-right,
    bottom-right in Spine's Y-up local space). A 90° region stores its pixels
    transposed in the page, so the runtime permutes the corners; copying that
    permutation is what keeps rotated pieces from rendering sideways.
    """
    left, top, right, bottom = region_uv_rect(region, page_width, page_height)
    if region.get("degrees") == 90:
        return [
            (right, bottom),  # bottom-left
            (left, bottom),   # top-left
            (left, top),      # top-right
            (right, top),     # bottom-right
        ]
    return [
        (left, bottom),
        (left, top),
        (right, top),
        (right, bottom),
    ]


def region_uv_for_vertex(region, u, v, page_width, page_height):
    """Map one region-normalised UV to page pixels, honouring 90° rotation.

    Mirrors MeshAttachment.computeUVs, which transposes both axes for a 90°
    region (`u` comes from the region's v and vice versa).
    """
    original_width = region.get("originalWidth", region["width"])
    original_height = region.get("originalHeight", region["height"])
    offset_x = region.get("offsetX", 0)
    offset_y = region.get("offsetY", 0)
    if region.get("degrees") == 90:
        base_u = region["x"] - (original_height - offset_y - region["height"])
        base_v = region["y"] - (original_width - offset_x - region["width"])
        return (
            base_u + v * original_height,
            base_v + (1.0 - u) * original_width,
        )
    base_u = region["x"] - offset_x
    base_v = region["y"] - (original_height - offset_y - region["height"])
    return (
        base_u + u * original_width,
        base_v + v * original_height,
    )


def spine_world_transforms(spine, pose=None):
    """bone name -> affine world matrix in Spine space.

    Mirrors the runtime's BonePose.updateWorldTransform, including the `inherit`
    modes — a bone with `noRotationOrReflection` (used by the hero's feet) does
    NOT take its parent's rotation, and composing it as if it did is what makes
    those pieces point the wrong way.

    `pose` optionally overrides local values: {bone name: (x, y, rotation_deg)}.
    """
    bones = spine["bones"]
    by_name = {b["name"]: b for b in bones}
    world = {}
    for bone in bones:
        name = bone["name"]
        local = pose.get(name) if pose else None
        x = local[0] if local else bone.get("x", 0.0)
        y = local[1] if local else bone.get("y", 0.0)
        rotation = local[2] if local else bone.get("rotation", 0.0)
        scale_x = bone.get("scaleX", 1.0)
        scale_y = bone.get("scaleY", 1.0)
        shear_x = bone.get("shearX", 0.0)
        shear_y = bone.get("shearY", 0.0)
        parent_name = bone.get("parent")
        if not parent_name:
            world[name] = compose((x, y), rotation, (scale_x, scale_y))
            continue
        parent = world[parent_name]
        pa, pb, pc, pd, ptx, pty = parent
        world_x = pa * x + pb * y + ptx
        world_y = pc * x + pd * y + pty
        inherit = bone.get("inherit", "normal")
        if inherit == "normal":
            rx = math.radians(rotation + shear_x)
            ry = math.radians(rotation + 90.0 + shear_y)
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            a, b = pa * la + pb * lc, pa * lb + pb * ld
            c, d = pc * la + pd * lc, pc * lb + pd * ld
        elif inherit == "onlyTranslation":
            rx = math.radians(rotation + shear_x)
            ry = math.radians(rotation + 90.0 + shear_y)
            a, b = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            c, d = math.sin(rx) * scale_x, math.sin(ry) * scale_y
        elif inherit == "noRotationOrReflection":
            # Only the parent's first column survives, renormalised; the parent
            # rotation is dropped entirely.
            pa2, pc2 = pa, pc
            total = pa2 * pa2 + pc2 * pc2
            if total > 1e-12:
                factor = abs(pa * pd - pb * pc) / total
                pb2, pd2 = pc2 * factor, pa2 * factor
                effective = rotation - math.degrees(math.atan2(pc2, pa2))
            else:
                pa2 = pc2 = 0.0
                pb2, pd2 = pb, pd
                effective = rotation - 90.0 + math.degrees(math.atan2(pd2, pb2))
            rx = math.radians(effective + shear_x)
            ry = math.radians(effective + shear_y + 90.0)
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            a = pa2 * la - pb2 * lc
            b = pa2 * lb - pb2 * ld
            c = pc2 * la + pd2 * lc
            d = pc2 * lb + pd2 * ld
        else:
            # noScale / noScaleOrReflection: keep the parent's rotation, drop its
            # scale, optionally preserving reflection.
            radians = math.radians(rotation)
            cos, sin = math.cos(radians), math.sin(radians)
            za = pa * cos + pb * sin
            zc = pc * cos + pd * sin
            magnitude = math.sqrt(za * za + zc * zc) or 1.0
            za, zc = za / magnitude, zc / magnitude
            zb, zd = -zc, za
            if inherit == "noScale" and (pa * pd - pb * pc < 0) != (scale_x < 0 != scale_y < 0):
                zb, zd = -zb, -zd
            rx = math.radians(shear_x)
            ry = math.radians(90.0 + shear_y)
            la, lb = math.cos(rx) * scale_x, math.cos(ry) * scale_y
            lc, ld = math.sin(rx) * scale_x, math.sin(ry) * scale_y
            a = za * la + zb * lc
            b = za * lb + zb * ld
            c = zc * la + zd * lc
            d = zc * lb + zd * ld
        world[name] = (a, b, c, d, world_x, world_y)
    return world


def bezier_to_transition(curve):
    """Approximate a Spine bezier curve with Godot's eased interpolation.

    Spine `curve: [cx1, cy1, cx2, cy2, cx3, cy3, cx4, cy4]` is one or two cubic
    segments in normalised key space. Godot Animation `transitions` only offers
    its own easing curve per key, so map the Spine shape to the closest Godot
    mode instead of pretending the curve is linear:
      - a curve whose control points sit on the diagonal is linear (1)
      - a curve that starts flat is ease-in (2), ends flat is ease-out (3)
      - both flat is ease-in-out (0)
    """
    if not curve:
        return 1.0
    if isinstance(curve, str):
        # Spine also allows "stepped" / "linear" as named curves.
        return 0.0 if curve == "stepped" else 1.0
    values = [float(v) for v in curve]
    # Segment 1 control points: (x1,y1) and (x2,y2)
    if len(values) >= 4:
        x1, y1, x2, y2 = values[0], values[1], values[2], values[3]
    else:
        return 1.0
    start_flat = abs(y1) < 0.05
    end_flat = abs(y2 - 1.0) < 0.05 if x2 else False
    if start_flat and end_flat:
        return 0.0  # ease in-out
    if start_flat:
        return 2.0  # ease in
    if end_flat:
        return 3.0  # ease out
    return 1.0


def spine_curve_to_godot(curve):
    return bezier_to_transition(curve)


def cubic_bezier_point(u, p0, p1, p2, p3):
    """Evaluate a cubic bezier with absolute control values at parameter u."""
    mu = 1.0 - u
    return (
        3 * mu * mu * u * p1 + 3 * mu * u * u * p2 + u * u * u * p3
        if p0 == 0.0
        else mu * mu * mu * p0 + 3 * mu * mu * u * p1 + 3 * mu * u * u * p2 + u * u * u * p3
    )


def bezier_table_point(curve, axis, step, time1, time2, value1, value2):
    """One (time, value) point of the runtime's precomputed bezier table.

    Mirrors Timeline.setBezier: controls are absolute, the curve is sampled with
    the same forward-difference recurrence the runtime uses, so the emitted
    polyline matches it sample for sample.
    """
    if not isinstance(curve, (list, tuple)) or len(curve) < axis * 4 + 4:
        fraction = step / 10.0
        return (time1 + (time2 - time1) * fraction, value1 + (value2 - value1) * fraction)
    cx1, cy1, cx2, cy2 = (float(curve[axis * 4 + i]) for i in range(4))
    tmpx = (time1 - cx1 * 2 + cx2) * 0.03
    tmpy = (value1 - cy1 * 2 + cy2) * 0.03
    dddx = ((cx1 - cx2) * 3 - time1 + time2) * 6e-3
    dddy = ((cy1 - cy2) * 3 - value1 + value2) * 6e-3
    ddx = tmpx * 2 + dddx
    ddy = tmpy * 2 + dddy
    dx = (cx1 - time1) * 0.3 + tmpx + dddx * 0.16666667
    dy = (cy1 - value1) * 0.3 + tmpy + dddy * 0.16666667
    x = time1 + dx
    y = value1 + dy
    for _ in range(1, step):
        dx += ddx
        dy += ddy
        ddx += dddx
        ddy += dddy
        x += dx
        y += dy
    return (x, y)


def bezier_value(curve, axis, fraction, time1, time2, value1, value2):
    """Kept for callers that want a direct cubic evaluation (unused by the bake)."""
    if not curve or isinstance(curve, str) or not isinstance(curve, (list, tuple)):
        return value1 + (value2 - value1) * fraction
    offset = axis * 4
    if len(curve) < offset + 4:
        return value1 + (value2 - value1) * fraction
    cx1, cy1, cx2, cy2 = (float(curve[offset + i]) for i in range(4))
    target = time1 + (time2 - time1) * fraction
    low, high = 0.0, 1.0
    for _ in range(28):
        mid = (low + high) / 2.0
        x = cubic_bezier_point(mid, time1, cx1, cx2, time2)
        if x < target:
            low = mid
        else:
            high = mid
    return cubic_bezier_point((low + high) / 2.0, value1, cy1, cy2, value2)


def bake_curved_track(keys, curves, samples=10):
    """Expand bezier-interpolated intervals into the runtime's own polyline.

    Spine runtimes do not evaluate the cubic per frame: Timeline.setBezier
    precomputes a 10-point table by forward differencing and then interpolates
    linearly between table points. Emitting exactly those table points as Godot
    keys (linear interpolation) reproduces the runtime's curve sample for sample.
    """
    has_bezier = any(isinstance(c, (list, tuple)) and len(c) >= 4 for c in curves or [])
    if not has_bezier:
        return keys, [1.0] * len(keys)

    baked_keys = []
    baked_transitions = []
    for index, (time, value) in enumerate(keys):
        baked_keys.append((time, value))
        baked_transitions.append(1.0)
        curve = curves[index] if index < len(curves) else None
        if not isinstance(curve, (list, tuple)) or len(curve) < 4 or index + 1 >= len(keys):
            continue
        next_time, next_value = keys[index + 1]
        for step in range(1, samples):
            if isinstance(value, tuple):
                # Time comes from the first axis' curve; each axis carries its
                # own value curve.
                sample_time, _ = bezier_table_point(
                    curve, 0, step, time, next_time, value[0], next_value[0]
                )
                sample_value = tuple(
                    bezier_table_point(
                        curve, axis, step, time, next_time, value[axis], next_value[axis]
                    )[1]
                    for axis in range(len(value))
                )
            else:
                sample_time, sample_value = bezier_table_point(
                    curve, 0, step, time, next_time, value, next_value
                )
            baked_keys.append((round(sample_time, 6), sample_value))
            baked_transitions.append(1.0)
    return baked_keys, baked_transitions


def map_curve_to_godot(curve, axis, offset, negate):
    """Map one bezier segment's control values into Godot's value space.

    Spine control points live in the same space as the key values, so the same
    affine map applied to the values (offset, or offset plus negation for the
    mirrored axis) must be applied to them, or the baked curve drifts.
    """
    if not isinstance(curve, (list, tuple)):
        return curve
    offset_index = axis * 4
    if len(curve) < offset_index + 4:
        return curve
    mapped = list(curve)
    for index in (offset_index + 1, offset_index + 3):
        control = float(mapped[index])
        mapped[index] = -control + offset if negate else control + offset
    return mapped


def effective_local_samples(spine, bone_name, animation, times):
    """Sample a bone's effective local transform across `times` (Spine space).

    Builds a full pose per sample — every animated bone takes its interpolated
    value — recomputes the world transforms with the inherit rules, and solves
    the bone's local as `parent_world⁻¹ · bone_world`. That local is what Godot
    must store, because Godot has no inherit modes of its own.
    """
    bones_by_name = {b["name"]: b for b in spine["bones"]}
    bone = bones_by_name[bone_name]
    parent_name = bone.get("parent")
    if not parent_name:
        return []
    samples = []
    for time in times:
        pose = {}
        for other_name, other_props in animation.get("bones", {}).items():
            target = bones_by_name.get(other_name)
            if not target:
                continue
            x = target.get("x", 0.0)
            y = target.get("y", 0.0)
            rotation = target.get("rotation", 0.0)
            rotate_keys = [
                (k.get("time", 0.0), k.get("value", 0.0))
                for k in other_props.get("rotate", [])
            ]
            translate_keys = [
                (k.get("time", 0.0), (k.get("x", 0.0), k.get("y", 0.0)))
                for k in other_props.get("translate", [])
            ]
            if rotate_keys:
                rotation += sample_track(rotate_keys, time) or 0.0
            if translate_keys:
                offset = sample_track(translate_keys, time)
                if offset:
                    x += offset[0]
                    y += offset[1]
            pose[other_name] = (x, y, rotation)
        world = spine_world_transforms(spine, pose)
        samples.append((time, multiply(invert(world[parent_name]), world[bone_name])))
    return samples


def inherit_animation_tracks(spine, bone_name, properties, animation, node_relative):
    """Godot tracks for a Spine bone whose inherit mode breaks local products.

    Emits the effective local (mirrored into Godot space) as a rotation track and
    a position track, so the pose matches Spine at every key of the bone's own
    tracks and of every ancestor that moves it.
    """
    bones_by_name = {b["name"]: b for b in spine["bones"]}
    times = set()
    for kind in ("rotate", "translate", "scale"):
        for key in properties.get(kind, []):
            times.add(round(key.get("time", 0.0), 6))
    # Ancestor motion changes the effective local too.
    current = bones_by_name[bone_name].get("parent")
    while current:
        for kind in ("rotate", "translate", "scale"):
            for key in animation.get("bones", {}).get(current, {}).get(kind, []):
                times.add(round(key.get("time", 0.0), 6))
        current = bones_by_name[current].get("parent")
    times.add(0.0)
    times = sorted(times)

    samples = effective_local_samples(spine, bone_name, animation, times)
    if not samples:
        return []

    rotation_keys = []
    position_keys = []
    for time, local in samples:
        mirrored = mirror_matrix(local)
        rotation_keys.append((time, math.degrees(math.atan2(mirrored[2], mirrored[0]))))
        position_keys.append((time, (mirrored[4], mirrored[5])))

    return [
        ("rotation_degrees", rotation_keys,
         f"Sprite2D/Skeleton2D/{node_relative}:rotation_degrees",
         [None] * len(rotation_keys)),
        ("position", position_keys,
         f"Sprite2D/Skeleton2D/{node_relative}:position",
         [None] * len(position_keys)),
    ]


def spine_to_godot(spine, texture_path="res://player/gBot.png", atlas_path=None):
    world = spine_world_transforms(spine)
    bone_index = {bone["name"]: index for index, bone in enumerate(spine["bones"])}
    attachments = spine_attachment_map(spine)
    atlas_regions = read_atlas_regions(atlas_path)
    page_size = read_png_size(resolve_image_for_atlas(atlas_path, texture_path))
    page_width, page_height = page_size if page_size else (1, 1)

    sub_resources = []
    nodes = []
    node_by_bone = {}
    bone_setup = {}
    # Bone name -> path relative to Skeleton2D, which is what Polygon2D `bones`
    # entries must use.
    bone_relative_path = {}

    # ---- bones
    # Godot composes bone worlds as a plain product of local transforms, so a
    # bone whose Spine `inherit` mode breaks that product must carry the
    # *effective* local transform (the one that reproduces its real world
    # position under the parent). Deriving it from the Spine world matrices
    # covers every inherit mode with one rule instead of special cases.
    spine_world = spine_world_transforms(spine)
    effective_locals = {}
    for bone in spine["bones"]:
        name = bone["name"]
        parent_name = bone.get("parent")
        if parent_name:
            effective_locals[name] = multiply(
                invert(spine_world[parent_name]), spine_world[name]
            )
        else:
            effective_locals[name] = spine_world[name]

    for bone in spine["bones"]:
        # Mirror into Godot space: F · M · F with F = diag(1, -1).
        mirrored = mirror_matrix(effective_locals[bone["name"]])
        # Godot writes Transform2D as (x.x, x.y, y.x, y.y, o.x, o.y) — the
        # transpose of this file's (a, b, c, d, tx, ty) convention.
        rest = (mirrored[0], mirrored[2], mirrored[1], mirrored[3],
                mirrored[4], mirrored[5])
        position = (rest[4], rest[5])
        rotation_deg = math.degrees(math.atan2(rest[1], rest[0]))
        parent_path = "Sprite2D/Skeleton2D"
        if bone.get("parent"):
            parent_path = node_by_bone[bone["parent"]]
        node_path_value = f"{parent_path}/{bone['name']}"
        node_by_bone[bone["name"]] = node_path_value
        bone_setup[bone["name"]] = {
            "x": position[0], "y": position[1], "rotation": rotation_deg,
        }
        parent_relative = bone_relative_path.get(bone.get("parent"), "")
        bone_relative_path[bone["name"]] = (
            f"{parent_relative}/{bone['name']}" if parent_relative else bone["name"]
        )
        props = [
            f"position = Vector2({round(position[0], 6)}, {round(position[1], 6)})",
            f"rotation = {round(math.radians(rotation_deg), 9)}",
        ]
        if bone.get("length"):
            props += [
                "auto_calculate_length_and_angle = false",
                f"length = {round(bone['length'], 6)}",
                f"bone_angle = {round(rotation_deg, 6)}",
            ]
        props.append(
            "rest = Transform2D({}, {}, {}, {}, {}, {})".format(
                *(round(v, 6) for v in rest)
            )
        )
        inherit = bone.get("inherit", "normal")
        if inherit != "normal":
            # Godot has no bone inheritance modes; record the loss so it is
            # visible in the scene rather than silently wrong.
            props.append(
                'metadata/spine_inherit = "%s"' % inherit
            )
        nodes.append({
            "name": bone["name"], "type": "Bone2D", "parent": parent_path, "props": props,
        })

    # ---- attachments as Polygon2D under a sibling "Polygons" node
    polygon_nodes = []
    for slot in spine["slots"]:
        slot_name = slot["name"]
        entry = attachments.get(slot_name)
        if not entry:
            continue
        attachment_name, attachment = entry
        host = slot["bone"]
        uvs = attachment.get("uvs", [])
        width = attachment.get("width", 1.0) or 1.0
        height = attachment.get("height", 1.0) or 1.0
        vertices = attachment.get("vertices", [])
        bones = attachment.get("bones")
        triangles = attachment.get("triangles", [])

        # Godot UVs are texture pixels. The region's pixels live at its atlas
        # rect, so resolve it by the attachment (region) name — which can differ
        # from the slot name.
        region = atlas_regions.get(attachment_name) or atlas_regions.get(slot_name)
        if region:
            region_left, region_top, region_right, region_bottom = region_uv_rect(
                region, page_width, page_height
            )
        else:
            region_left, region_top, region_right, region_bottom = 0.0, 0.0, width, height
        region_w = region_right - region_left
        region_h = region_bottom - region_top

        world_points = []
        uv_points = []
        weights_by_bone = {}

        if attachment.get("type", "region") == "region" and not vertices:
            # Spine region attachment: a quad at (x, y) rotated by `rotation`,
            # sized width x height. It hangs off one bone, so the Polygon2D can
            # carry the quad directly in that bone's local space with weight 1.
            quad = compose(
                (attachment.get("x", 0.0), attachment.get("y", 0.0)),
                attachment.get("rotation", 0.0),
                (attachment.get("scaleX", 1.0), attachment.get("scaleY", 1.0)),
            )
            half_w, half_h = width / 2.0, height / 2.0
            # Corner order must match RegionAttachment.computeUVs exactly:
            # bottom-left, top-left, top-right, bottom-right (Spine's Y-up local
            # space). Any other winding mirrors the quad.
            corners = [
                (-half_w, -half_h), (-half_w, half_h),
                (half_w, half_h), (half_w, -half_h),
            ]
            # The quad is authored in the host bone's local space; vertices must
            # reach skeleton space (what the skinning matrices expect at rest).
            bone_world = world.get(host, IDENTITY)
            local_points = [
                (transform(bone_world, transform(quad, corner))[0],
                 -transform(bone_world, transform(quad, corner))[1])
                for corner in corners
            ]
            # Corner order matches `corners` (Spine local Y-up: bottom-left,
            # top-left, top-right, bottom-right).
            if region:
                corner_uvs = region_corner_uvs(region, page_width, page_height)
            else:
                corner_uvs = [
                    (0.0, height), (0.0, 0.0), (width, 0.0), (width, height),
                ]
            polygon_props = [
                "position = Vector2(0, 0)",
                'texture = ExtResource("1")',
                'skeleton = NodePath("../../Skeleton2D")',
                "polygon = PackedVector2Array(%s)" % ", ".join(
                    f"{round(v, 6)}" for point in local_points for v in point
                ),
                "uv = PackedVector2Array(%s)" % ", ".join(
                    f"{round(v, 6)}" for point in corner_uvs for v in point
                ),
                "polygons = [PackedInt32Array(0, 1, 2, 3)]",
                'bones = ["%s", PackedFloat32Array(1, 1, 1, 1)]'
                % bone_relative_path.get(host, host),
            ]
            polygon_nodes.append({
                "name": slot_name.capitalize(), "type": "Polygon2D",
                "parent": "Sprite2D/Polygons", "props": polygon_props,
            })
            continue

        if bones:
            cursor = 0
            vertex_count = 0
            while cursor < len(vertices):
                bone_count = int(vertices[cursor])
                cursor += 1
                accumulated = (0.0, 0.0)
                for _ in range(bone_count):
                    bone_index_value = int(vertices[cursor])
                    local = (vertices[cursor + 1], vertices[cursor + 2])
                    weight = vertices[cursor + 3]
                    cursor += 4
                    bone_name = spine["bones"][bone_index_value]["name"]
                    bone_world = world[bone_name]
                    world_point = transform(bone_world, local)
                    accumulated = (
                        accumulated[0] + world_point[0] * weight,
                        accumulated[1] + world_point[1] * weight,
                    )
                    weights_by_bone.setdefault(bone_name, {})[vertex_count] = weight
                world_points.append(accumulated)
                vertex_count += 1
            for uv_index in range(0, min(len(uvs), vertex_count * 2), 2):
                if region:
                    uv_points.append(list(region_uv_for_vertex(
                        region, uvs[uv_index], uvs[uv_index + 1], page_width, page_height
                    )))
                else:
                    uv_points.append([
                        uvs[uv_index] * width, uvs[uv_index + 1] * height,
                    ])
        else:
            for index in range(0, len(vertices) - 1, 2):
                world_points.append((vertices[index], vertices[index + 1]))
            for uv_index in range(0, min(len(uvs), len(vertices)), 2):
                if region:
                    uv_points.append(list(region_uv_for_vertex(
                        region, uvs[uv_index], uvs[uv_index + 1], page_width, page_height
                    )))
                else:
                    uv_points.append([
                        uvs[uv_index] * width, uvs[uv_index + 1] * height,
                    ])

        # Godot skins mesh vertices as `bone_world * rest_inverse * vertex`, so
        # at rest the vertex must already sit at its rest world position in
        # skeleton space. Emit absolute positions (position = 0) to keep that
        # invariant obvious; the Y flip converts Spine's Y-up to Godot's Y-down.
        def to_godot_space(point):
            return (point[0], -point[1])

        origin = world.get(host, IDENTITY)
        local_points = [to_godot_space(p) for p in world_points]

        polygon_props = [
            "position = Vector2(0, 0)",
            f'texture = ExtResource("1")',
            'skeleton = NodePath("../../Skeleton2D")',
            "polygon = PackedVector2Array(%s)" % ", ".join(
                f"{round(v, 6)}" for point in local_points for v in point
            ),
            "uv = PackedVector2Array(%s)" % ", ".join(
                f"{round(v, 6)}" for point in uv_points for v in point
            ),
        ]
        if triangles:
            groups = group_triangles(triangles)
            polygon_props.append(
                "polygons = [%s]" % ", ".join(
                    "PackedInt32Array(%s)" % ", ".join(str(i) for i in group)
                    for group in groups
                )
            )
        if weights_by_bone:
            parts = []
            for bone_name, vertex_weights in weights_by_bone.items():
                weights = [
                    round(vertex_weights.get(index, 0.0), 6)
                    for index in range(len(local_points))
                ]
                # Godot resolves this path relative to the Skeleton2D and drops
                # the entry silently when it does not resolve, so it must be the
                # full bone path (`root/hip/body`), not the bare bone name.
                parts.append(
                    '"%s", PackedFloat32Array(%s)'
                    % (bone_relative_path.get(bone_name, bone_name),
                       ", ".join(str(w) for w in weights))
                )
            polygon_props.append("bones = [%s]" % ", ".join(parts))
        polygon_nodes.append({
            "name": slot_name.capitalize(), "type": "Polygon2D",
            "parent": "Sprite2D/Polygons", "props": polygon_props,
        })

    # ---- animations
    # For a bone whose inherit mode breaks Godot's plain local-product, the local
    # transform that reproduces its pose changes with the parent, so the animated
    # track must carry the effective local sampled per key rather than the raw
    # Spine value.
    inherit_bones = {
        bone["name"] for bone in spine["bones"]
        if bone.get("inherit", "normal") != "normal"
    }
    animation_refs = []
    for anim_index, (anim_name, animation) in enumerate(spine["animations"].items(), start=1):
        resource_id = f"Animation_{anim_name}"
        tracks = []
        for bone_name, properties in animation.get("bones", {}).items():
            bone_path_value = node_by_bone.get(bone_name)
            if not bone_path_value:
                continue
            relative = bone_path_value[len("Sprite2D/Skeleton2D/"):]
            node_path_value = f"Sprite2D/Skeleton2D/{relative}"
            # Spine keys are offsets from the setup pose; Godot animates the
            # absolute local transform, so add the bone's setup values back.
            setup = bone_setup.get(bone_name, {})
            setup_rotation = setup.get("rotation", 0.0)
            setup_position = (setup.get("x", 0.0), setup.get("y", 0.0))
            if bone_name in inherit_bones:
                tracks.extend(
                    inherit_animation_tracks(
                        spine, bone_name, properties, animation, relative
                    )
                )
                continue
            if properties.get("rotate"):
                tracks.append(("rotation_degrees", [
                    (round(k.get("time", 0.0), 6), setup_rotation - k.get("value", 0.0))
                    for k in properties["rotate"]
                ], f"Sprite2D/Skeleton2D/{relative}:rotation_degrees",
                    [map_curve_to_godot(k.get("curve"), 0, setup_rotation, True)
                     for k in properties["rotate"]]))
            if properties.get("translate"):
                tracks.append(("position", [
                    (
                        round(k.get("time", 0.0), 6),
                        (
                            setup_position[0] + k.get("x", 0.0),
                            setup_position[1] - k.get("y", 0.0),
                        ),
                    )
                    for k in properties["translate"]
                ], f"Sprite2D/Skeleton2D/{relative}:position",
                    [
                        [
                            *map_curve_to_godot(k.get("curve"), 0, setup_position[0], False)[0:4],
                            *map_curve_to_godot(k.get("curve"), 1, setup_position[1], True)[4:8],
                        ] if isinstance(k.get("curve"), (list, tuple)) and len(k["curve"]) >= 8
                        else map_curve_to_godot(k.get("curve"), 0, setup_position[0], False)
                        for k in properties["translate"]
                    ]))
        lines = [f'[sub_resource type="Animation" id="{resource_id}"]']
        length = 0.0
        for _kind, keys, _path, _curves in tracks:
            for time, _ in keys:
                length = max(length, time)
        lines.append(f"length = {round(length, 6)}")
        lines.append("loop_mode = 1")
        for track_index, (property_name, keys, track_path, curves) in enumerate(tracks):
            keys, curves = bake_curved_track(keys, curves)
            times = ", ".join(str(time) for time, _ in keys)
            transitions = ", ".join(str(c) for c in curves) if curves else ", ".join("1" for _ in keys)
            if property_name == "rotation_degrees":
                values = ", ".join(str(round(v, 6)) for _, v in keys)
            else:
                values = ", ".join(
                    f"Vector2({round(v[0], 6)}, {round(v[1], 6)})" for _, v in keys
                )
            lines.append(f'tracks/{track_index}/type = "value"')
            lines.append(f"tracks/{track_index}/imported = false")
            lines.append(f"tracks/{track_index}/enabled = true")
            lines.append(f'tracks/{track_index}/path = NodePath("{track_path}")')
            lines.append(f"tracks/{track_index}/interp = 1")
            lines.append(f"tracks/{track_index}/loop_wrap = true")
            lines.append(
                'tracks/%d/keys = {\n"times": PackedFloat32Array(%s),\n'
                '"transitions": PackedFloat32Array(%s),\n"update": 0,\n'
                '"values": [%s]\n}' % (track_index, times, transitions, values)
            )
        sub_resources.append({
            "id": resource_id, "lines": lines, "name": anim_name,
        })
        animation_refs.append((anim_name, resource_id))

    return render_tscn(spine, nodes, polygon_nodes, sub_resources, animation_refs, texture_path)


def group_triangles(triangles):
    """Group a flat triangle index list back into per-triangle polygons."""
    return [
        [triangles[i], triangles[i + 1], triangles[i + 2]]
        for i in range(0, len(triangles) - 2, 3)
    ]


def render_tscn(spine, bone_nodes, polygon_nodes, animations, animation_refs, texture_path):
    lines = ['[gd_scene format=3]', ""]
    lines.append(f'[ext_resource type="Texture2D" path="{texture_path}" id="1"]')
    lines.append("")
    for entry in animations:
        lines.extend(entry["lines"])
        lines.append("")
    lines.append('[sub_resource type="AnimationLibrary" id="AnimationLibrary_1"]')
    lines.append("_data = {")
    for anim_name, resource_id in animation_refs:
        lines.append(f'&"{anim_name}": SubResource("{resource_id}"),')
    lines.append("}")
    lines.append("")
    lines.append('[node name="SkeletonRoot" type="Node2D"]')
    lines.append('[node name="Sprite2D" type="Node2D" parent="."]')
    lines.append('[node name="Skeleton2D" type="Skeleton2D" parent="Sprite2D"]')
    for node in bone_nodes:
        lines.append(f'[node name="{node["name"]}" type="Bone2D" parent="{node["parent"]}"]')
        lines.extend(node["props"])
        lines.append("")
    lines.append('[node name="Polygons" type="Node2D" parent="Sprite2D"]')
    lines.append("")
    for node in polygon_nodes:
        lines.append(f'[node name="{node["name"]}" type="Polygon2D" parent="{node["parent"]}"]')
        lines.extend(node["props"])
        lines.append("")
    lines.append('[node name="AnimationPlayer" type="AnimationPlayer" parent="."]')
    lines.append('libraries/ = SubResource("AnimationLibrary_1")')
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comparison: numeric round-trip validation
# ---------------------------------------------------------------------------

def bone_rest_positions(model):
    world = godot_world_transforms(model)
    return {name: (matrix[4], matrix[5]) for name, matrix in world.items()}


def spine_bone_rest_positions(spine):
    world = spine_world_transforms(spine)
    return {name: (matrix[4], -matrix[5]) for name, matrix in world.items()}


def attachment_world_points(model, spine, tolerance_note):
    """Compare mesh vertex world positions (Godot space) between both models."""
    spine_world = spine_world_transforms(spine)
    attachments = spine_attachment_map(spine)
    godot_world = godot_world_transforms(model)

    results = {}
    for attachment in model.attachments:
        name = attachment["name"]
        slot = next((s for s in spine["slots"] if s["name"] == name), None)
        if not slot:
            continue
        entry = attachments.get(name)
        if not entry:
            continue
        _attachment_name, entry = entry

        # Godot side: world position of every polygon vertex (position then offset)
        internal = attachment.get("internal_vertices", 0)
        polygon = attachment["polygon"]
        if internal and not attachment["polygons"]:
            polygon = polygon[:len(polygon) - internal]
        polygon_world = compose(attachment["position"], 0.0)
        vertex_offset = attachment["offset"]
        godot_points = [
            transform(polygon_world, (v[0] + vertex_offset[0], v[1] + vertex_offset[1]))
            for v in polygon
        ]

        # Spine side: reconstruct world points from the weighted mesh
        spine_points = []
        vertices = entry.get("vertices", [])
        bones = entry.get("bones")
        if bones:
            cursor = 0
            while cursor < len(vertices):
                bone_count = int(vertices[cursor])
                cursor += 1
                accumulated = [0.0, 0.0]
                for _ in range(bone_count):
                    bone_index_value = int(vertices[cursor])
                    local = (vertices[cursor + 1], vertices[cursor + 2])
                    weight = vertices[cursor + 3]
                    cursor += 4
                    bone_name = spine["bones"][bone_index_value]["name"]
                    world_point = transform(spine_world[bone_name], local)
                    accumulated[0] += world_point[0] * weight
                    accumulated[1] += world_point[1] * weight
                spine_points.append((accumulated[0], -accumulated[1]))
        else:
            spine_points = [
                (vertices[i], -vertices[i + 1]) for i in range(0, len(vertices) - 1, 2)
            ]

        count = min(len(godot_points), len(spine_points))
        if not count:
            continue
        worst = max(
            math.dist(godot_points[i], spine_points[i]) for i in range(count)
        )
        results[name] = worst
    return results


def sample_track(keys, time):
    """Linear interpolation of a track at `time` (values may be float or tuple)."""
    if not keys:
        return None
    if time <= keys[0][0]:
        return keys[0][1]
    if time >= keys[-1][0]:
        return keys[-1][1]
    for index in range(len(keys) - 1):
        t0, v0 = keys[index]
        t1, v1 = keys[index + 1]
        if t0 <= time <= t1:
            span = (t1 - t0) or 1.0
            fraction = (time - t0) / span
            if isinstance(v0, tuple):
                return tuple(
                    v0[axis] + (v1[axis] - v0[axis]) * fraction
                    for axis in range(len(v0))
                )
            return v0 + (v1 - v0) * fraction
    return keys[-1][1]


def curve_deviation(left_keys, right_keys, samples=50, angular=False):
    """Worst difference between two tracks at the reference track's key times.

    Key counts differ legitimately (beziers are baked into dense linear keys), so
    compare values rather than key lists. Sampling happens AT the reference key
    times, not between them: both sides are exact at those instants, whereas
    linear interpolation between keys would ignore the bezier the reference
    track carries and report a difference that is not real.
    """
    if not left_keys or not right_keys:
        return 0.0
    start = max(left_keys[0][0], right_keys[0][0])
    end = min(left_keys[-1][0], right_keys[-1][0])
    if end <= start:
        return 0.0
    worst = 0.0
    # Iterate over the sparser track: its key times are exact on both sides,
    # whereas the denser one is a bake of those same instants.
    reference = left_keys if len(left_keys) <= len(right_keys) else right_keys
    for time, _value in reference:
        if time < start or time > end:
            continue
        left = sample_track(left_keys, time)
        right = sample_track(right_keys, time)
        if left is None or right is None:
            continue
        if isinstance(left, tuple):
            worst = max(
                worst,
                max(abs(left[axis] - right[axis]) for axis in range(len(left))),
            )
        elif angular:
            worst = max(worst, abs((left - right + 180.0) % 360.0 - 180.0))
        else:
            worst = max(worst, abs(left - right))
    return worst


def compare(tscn_path, spine_path):
    model = read_godot_skeleton(tscn_path)
    spine = read_spine(spine_path)
    # Which way this pair was produced: a Godot-authored scene converted to
    # Spine keeps the scene's absolute angles; a Spine export converted to Godot
    # stores the mirrored values. Detect by whether the scene's setup rotation
    # already matches the mirrored Spine setup.
    spine_bones = {b["name"]: b for b in spine["bones"]}
    spine_direction = "godot_to_spine"
    checked = 0
    for name, bone in model.by_name.items():
        if name not in spine_bones:
            continue
        scene_rotation = bone.rotation_deg
        mirrored = -spine_bones[name].get("rotation", 0.0)
        checked += 1
        if abs((scene_rotation - mirrored + 180.0) % 360.0 - 180.0) > 1.0:
            spine_direction = "spine_to_godot"
            break
    if not checked:
        spine_direction = "spine_to_godot"

    print("== bones (world rest position, Godot space) ==")
    godot_positions = bone_rest_positions(model)
    spine_positions = spine_bone_rest_positions(spine)
    worst_bone = 0.0
    missing = []
    for name, position in godot_positions.items():
        if name not in spine_positions:
            missing.append(name)
            continue
        deviation = math.dist(position, spine_positions[name])
        worst_bone = max(worst_bone, deviation)
        flag = "OK " if deviation < 1e-3 else "DIFF"
        print(f"  {flag} {name:16s} godot=({position[0]:8.3f},{position[1]:8.3f}) "
              f"spine=({spine_positions[name][0]:8.3f},{spine_positions[name][1]:8.3f}) "
              f"d={deviation:.5f}")
    if missing:
        print(f"  bones missing in spine: {missing}")

    print("\n== mesh vertices (world position, Godot space) ==")
    worst_mesh = 0.0
    for name, deviation in attachment_world_points(model, spine, None).items():
        worst_mesh = max(worst_mesh, deviation)
        flag = "OK " if deviation < 1e-3 else "DIFF"
        print(f"  {flag} {name:16s} max deviation = {deviation:.5f}")

    print("\n== animations ==")
    godot_anims = set(model.animations)
    spine_anims = set(spine["animations"])
    print(f"  godot only: {sorted(godot_anims - spine_anims)}")
    print(f"  spine only: {sorted(spine_anims - godot_anims)}")
    shared = sorted(godot_anims & spine_anims)
    worst_anim = 0.0
    for anim_name in shared:
        godot_tracks = model.animations[anim_name]
        spine_tracks = spine["animations"][anim_name].get("bones", {})
        deviation = 0.0
        for bone_name, properties in godot_tracks.items():
            if bone_name not in spine_tracks:
                continue
            # Bones with a Spine inherit mode hold the *effective* local in the
            # scene, which the raw keys cannot reproduce — the engine-level
            # validation (validate-roundtrip.sh) is what covers them.
            spine_bone = spine_bones.get(bone_name, {})
            if spine_bone.get("inherit", "normal") != "normal":
                continue
            setup = model.by_name.get(bone_name)
            setup_rotation = setup.rotation_deg if setup else 0.0
            setup_position = setup.position if setup else (0.0, 0.0)
            spine_setup = spine_bones.get(bone_name, {})
            spine_setup_rotation = spine_setup.get("rotation", 0.0)
            spine_setup_position = (
                spine_setup.get("x", 0.0), spine_setup.get("y", 0.0),
            )
            # Compare the curves themselves, sampled — key counts legitimately
            # differ because bezier segments are baked into dense linear keys.
            # The scene holds the converted tracks: Godot angles are the mirrored
            # absolute value, so re-derive them from the Spine JSON the same way
            # the converter does and diff against what the scene actually has.
            deviation = max(
                deviation,
                curve_deviation(
                    [(k["time"], k["angle"]) for k in properties.get("rotate", [])],
                    [(k.get("time", 0.0), setup_rotation - k.get("value", 0.0))
                     for k in spine_tracks[bone_name].get("rotate", [])],
                    angular=True,
                ),
                curve_deviation(
                    [(k["time"], (k["x"], k["y"])) for k in properties.get("translate", [])],
                    [(k.get("time", 0.0),
                      (setup_position[0] + k.get("x", 0.0),
                       setup_position[1] - k.get("y", 0.0)))
                     for k in spine_tracks[bone_name].get("translate", [])],
                ),
            ) if spine_direction == "godot_to_spine" else max(
                deviation,
                # Expected scene value mirrors the converter: Godot angle is the
                # negated absolute Spine angle.
                curve_deviation(
                    [(k.get("time", 0.0), -(spine_setup_rotation + k.get("value", 0.0)))
                     for k in spine_tracks[bone_name].get("rotate", [])],
                    [(k["time"], k["angle"]) for k in properties.get("rotate", [])],
                    angular=True,
                ),
                curve_deviation(
                    [(k.get("time", 0.0),
                      (spine_setup_position[0] + k.get("x", 0.0),
                       -(spine_setup_position[1] + k.get("y", 0.0))))
                     for k in spine_tracks[bone_name].get("translate", [])],
                    [(k["time"], (k["x"], k["y"])) for k in properties.get("translate", [])],
                ),
            )
        worst_anim = max(worst_anim, deviation)
        flag = "OK " if deviation < 1e-3 else "DIFF"
        print(f"  {flag} {anim_name:12s} max deviation = {deviation:.6f}")

    print("\n== summary ==")
    print(f"  bones        max deviation: {worst_bone:.6f}")
    print(f"  mesh verts   max deviation: {worst_mesh:.6f}")
    print(f"  animations   max deviation: {worst_anim:.6f}")
    ok = worst_bone < 1e-3 and worst_mesh < 1e-3 and worst_anim < 1e-3 and not missing
    print("  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert")
    convert.add_argument("--to", choices=["spine", "godot"], required=True)
    convert.add_argument("input")
    convert.add_argument("-o", "--output", required=True)
    convert.add_argument("--texture", default="res://player/gBot.png")
    convert.add_argument(
        "--atlas",
        default=None,
        help="Spine .atlas for the JSON being converted to Godot; without it "
             "region UVs cannot be placed on the texture page.",
    )

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("scene")
    compare_parser.add_argument("spine_json")

    args = parser.parse_args()

    if args.command == "convert":
        if args.to == "spine":
            model = read_godot_skeleton(args.input)
            spine = godot_to_spine(model)
            Path(args.output).write_text(json.dumps(spine, indent=1))
            atlas_path = Path(args.output).with_suffix(".atlas")
            image_name = Path(model.texture_path).name if model.texture_path else "texture.png"
            emit_atlas(model, spine, atlas_path, image_name,
                       resolve_texture_path(model.texture_path, args.input))
            print(f"wrote {args.output}: {len(spine['bones'])} bones, "
                  f"{len(spine['slots'])} slots, {len(spine['animations'])} animations")
            print(f"wrote {atlas_path}: {len(model.attachments)} regions from {image_name}")
        else:
            spine = read_spine(args.input)
            # Default to a sibling .atlas with the same stem, which is how Spine
            # exports ship (hero.json + hero.atlas).
            atlas_path = args.atlas
            if not atlas_path:
                sibling = Path(args.input).with_suffix(".atlas")
                if sibling.exists():
                    atlas_path = str(sibling)
            Path(args.output).write_text(
                spine_to_godot(spine, args.texture, atlas_path)
            )
            regions = len(read_atlas_regions(atlas_path))
            print(f"wrote {args.output}: {len(spine['bones'])} bones, "
                  f"{len(spine['slots'])} slots, {len(spine['animations'])} animations")
            print(f"atlas regions resolved: {regions} "
                  f"({'from ' + str(atlas_path) if atlas_path else 'none — UVs fall back to the region size'})")
    elif args.command == "compare":
        return compare(args.scene, args.spine_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
