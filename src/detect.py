"""Sniff an uploaded file's rig format before handing it to a reader.

The loader cannot dispatch on the extension alone: Spine JSON and a bare
SkelForm ``armature.json`` are both ``.json``, and a ``.skf`` bundle is just a
renamed ZIP. A wrong guess does not fail loudly — it converts the *wrong rig*,
or converts garbage into a plausible-looking scene. So every decision here is
made from file content (JSON keys, container magic, Godot header), never from
the name; the extension only narrows which content check is worth running.

Companions that travel with a rig (``.atlas``, ``.png``, ``.import``) are
deliberately unrecognized: they are inputs to a reader, not rigs themselves,
and reporting one as the rig would be the same class of silent wrong guess.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

# A Godot scene/resource header, at the very start of the text (a UTF-8 BOM and
# leading whitespace are tolerated; nothing else may precede it).
_GODOT_SCENE = "[gd_scene"
_GODOT_RESOURCE = "[gd_resource"
_HEAD_BYTES = 256
_ARMATURE = "armature.json"


def _head(source: Path) -> str:
    """Decode the first bytes of ``source``; "" when it cannot be read."""
    try:
        raw = source.open("rb").read(_HEAD_BYTES)
    except OSError:
        return ""
    return raw.decode("utf-8-sig", "replace").lstrip()


def _bundle_names(source: Path) -> list[str] | None:
    """Member names of a ZIP ``source``; None when it is not a ZIP archive."""
    try:
        if not zipfile.is_zipfile(source):
            return None
        with zipfile.ZipFile(source) as bundle:
            return bundle.namelist()
    except (OSError, zipfile.BadZipFile):
        return None


def _sniff_json(source: Path) -> str | None:
    """Tell the two JSON-based formats apart by their top-level keys."""
    try:
        raw = source.read_bytes()
        data = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # SkelForm only: `visuals` / `styles` / `img_format` are its vocabulary.
    if "visuals" in data or "img_format" in data or "styles" in data:
        return "skelform"
    if "skeleton" in data:
        return "spine"
    if "bones" in data and "animations" in data:
        return "spine"
    return None


def detect_format(path: str | Path) -> str | None:
    """Return the rig format of ``path``, or None when it is not a rig.

    Never raises: a missing file, a directory, a corrupt archive or invalid
    JSON all come back as None so the caller can report "unrecognized" instead
    of crashing on a user's upload.
    """
    source = Path(path)
    if not source.is_file():
        return None
    suffix = source.suffix.lower()

    if suffix == ".tscn":
        return "godot" if _head(source).startswith(_GODOT_SCENE) else None
    if suffix == ".tres":
        return "tres" if _head(source).startswith(_GODOT_RESOURCE) else None
    # A `.zip` holding `armature.json` is a `.skf` bundle someone renamed.
    if suffix in (".skf", ".zip"):
        names = _bundle_names(source)
        return "skelform" if names and _ARMATURE in names else None
    if suffix == ".json":
        return _sniff_json(source)
    return None


def detect_rig(paths: list) -> tuple[str, str] | None:
    """Pick the rig out of a multi-file upload; None when none is a rig.

    Uploads arrive as a flat list (a Spine JSON beside its ``.atlas`` and page
    PNG, or a lone ``.skf``), so the first path ``detect_format`` recognizes is
    the rig and the rest are companions.
    """
    for path in paths or []:
        fmt = detect_format(path)
        if fmt is not None:
            return str(path), fmt
    return None


def describe(path: str | Path) -> str:
    """One line for a browser status line; never raises, never verbose."""
    source = Path(path)
    fmt = detect_format(source)
    if fmt is None:
        return f"unrecognized: {source.name}"
    if fmt == "tres":
        # The `.tres` writer exists, its reader does not — say so here because
        # the UI has nowhere else to explain why a "detected" file is unusable.
        return f"tres: {source.name} (no reader, writer only)"
    if fmt == "skelform" and source.suffix.lower() != ".json":
        names = _bundle_names(source)
        if names is not None:
            return f"skelform: {source.name} ({len(names)} entries)"
    return f"{fmt}: {source.name}"