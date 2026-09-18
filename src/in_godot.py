"""Parse Godot 4 Skeleton2D scenes (.tscn) into the canonical model."""

from __future__ import annotations

import math
import re
from pathlib import Path

from .model import Attachment, Bone, Skeleton

# ---------------------------------------------------------------------------
# .tscn text parsing
# ---------------------------------------------------------------------------

VEC_RE = re.compile(r"Vector2\(([-\d.eE]+), ([-\d.eE]+)\)")
F32_RE = re.compile(r"PackedFloat32Array\(([^)]*)\)")
I32_RE = re.compile(r"PackedInt32Array\(([^)]*)\)")
VEC2_PACKED_RE = re.compile(r"PackedVector2Array\(([^)]*)\)", re.S)


def parse_vec(text: str):
    match = VEC_RE.fullmatch(text.strip())
    return [float(match.group(1)), float(match.group(2))] if match else None


def parse_float_array(text: str):
    match = F32_RE.fullmatch(text.strip())
    if not match:
        return None
    return [float(p) for p in match.group(1).replace("\n", " ").split(",") if p.strip()]


def parse_int_array(text: str):
    match = I32_RE.fullmatch(text.strip())
    if not match:
        return None
    return [int(p) for p in match.group(1).replace("\n", " ").split(",") if p.strip()]


def parse_packed_vec2(text: str):
    match = VEC2_PACKED_RE.match(text.strip())
    if not match:
        return None
    numbers = [float(p) for p in match.group(1).replace("\n", " ").split(",") if p.strip()]
    return [[numbers[i], numbers[i + 1]] for i in range(0, len(numbers) - 1, 2)]


def extract_balanced(text: str, start: int):
    """Body of the bracket group opening at ``start``, plus the index after it."""
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


def parse_value(raw: str):
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


def parse_animation_keys(raw: str) -> dict:
    """Godot 4 Animation ``keys`` dict -> {times, transitions, update, values}."""
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


def split_top(body: str) -> list:
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


def parse_tscn(path: str) -> dict:
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
# .tscn -> canonical model
# ---------------------------------------------------------------------------


def node_path(node: dict) -> str:
    return node["name"] if node["parent"] == "." else f'{node["parent"]}/{node["name"]}'


def parse_polygon_weights(raw) -> list:
    """Polygon2D ``bones`` property -> [(bone_name, weights per vertex)]."""
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


def read_godot_animation(animation: dict, prefix: str) -> dict:
    props = animation["props"]
    tracks = {}
    index = 0
    while f"tracks/{index}/type" in props:
        path = props.get(f"tracks/{index}/path", "")
        keys = props.get(f"tracks/{index}/keys") or {}
        node_path_value, _, property_name = str(path).partition(":")
        bone_name = (
            node_path_value[len(prefix):].split("/")[-1]
            if node_path_value.startswith(prefix) else None
        )
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


def read_godot_skeleton(tscn_path: str) -> Skeleton:
    scene = parse_tscn(tscn_path)
    by_path = {node_path(node): node for node in scene["nodes"]}

    skeleton_path = next(
        (p for p, n in by_path.items() if n["type"] == "Skeleton2D"), None
    )
    if skeleton_path is None:
        raise SystemExit("no Skeleton2D node found in scene")
    prefix = skeleton_path + "/"

    model = Skeleton()
    model.texture_path = next(
        (e["path"] for e in scene["ext_resources"] if e["type"] == "Texture2D"), ""
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
            _xx, xy, _yx, _yy, ox, oy = rest
            position = [ox, oy]
            rotation_deg = math.degrees(math.atan2(xy, xx))
        parent = "/".join(relative.split("/")[:-1]) or None
        bone = Bone(
            name=relative.split("/")[-1],
            parent=parent.split("/")[-1] if parent else None,
            position=position,
            rotation_deg=rotation_deg,
            scale=scale,
            inherit="normal",
        )
        bone.length = props.get("length", 0.0)
        bone.path = relative
        # Record the Spine inherit mode if the scene carries it as metadata
        # (emitted by the Spine->Godot direction for loss tracking).
        if "metadata/spine_inherit" in props:
            bone.inherit = props["metadata/spine_inherit"]
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
            "position": props.get("position", [0.0, 0.0]),
            "offset": props.get("offset", [0.0, 0.0]),
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
