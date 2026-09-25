"""Synthesized rig for the CI gates: no fixtures, no third-party assets.

``tests/fixtures/`` holds third-party sample rigs and is deliberately not
committed, so the fixture-based numeric round-trips skip in CI — the strongest
gates never ran. This module builds a small Spine export (JSON + ``.atlas`` +
page image) entirely in code, and is shared by two consumers:

- ``tests/test_ci_gates.py`` — both conversion directions plus the mesh-parity
  gate;
- ``.github/workflows/tests.yml`` — the CLI end-to-end step, through
  ``python3 -m tests.ci_rig build <dir>`` and
  ``python3 -m tests.ci_rig verify <godot-dir> <spine-dir>``.
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

STEM = "rig"
PAGE = f"{STEM}.png"
PAGE_SIZE = (8, 8)
# Every region shares one page; the atlas declares each by name so the reader
# resolves UVs and the mesh-parity gate can check containment.
REGION_BOUNDS = (0, 0, 8, 8)
REGION_NAMES = ("arm-base", "arm-glove", "torso-base", "torso-belt")


def spine_rig() -> dict:
    """Three bones, two slots with two default-skin entries each, one animation.

    ``torso-belt`` is deliberately equipped by nothing (its sibling is the
    slot's setup attachment), so the model's equipping flags are exercised —
    the mesh-parity gate fails when an unequipped entry is flagged as drawn.
    ``arm-glove`` is carried as a mesh (vertices + normalized UVs, no ``bones``
    key) while the rest are regions, so both attachment grammars pass through
    the gates.
    """
    return {
        "skeleton": {"spine": "4.2.33", "x": 0, "y": 0, "width": 64, "height": 64},
        "bones": [
            {"name": "root"},
            {"name": "torso", "parent": "root", "x": 5.0, "y": 2.0,
             "rotation": 10.0, "length": 20.0},
            {"name": "arm", "parent": "torso", "x": 20.0, "y": 0.0,
             "rotation": -25.0, "length": 12.0},
        ],
        "slots": [
            {"name": "torso", "bone": "torso", "attachment": "torso-base"},
            {"name": "arm", "bone": "arm", "attachment": "arm-base"},
        ],
        "skins": [{"name": "default", "attachments": {
            "torso": {
                "torso-base": {"type": "region", "x": 0.0, "y": 0.0,
                               "width": 24.0, "height": 8.0},
                "torso-belt": {"type": "region", "x": 2.0, "y": -1.0,
                               "width": 10.0, "height": 4.0, "rotation": 15.0},
            },
            "arm": {
                "arm-base": {"type": "region", "x": 6.0, "y": 0.0,
                             "width": 12.0, "height": 5.0},
                # Unweighted mesh: vertices in the slot bone's local space and
                # normalized UVs with no `bones` key — both readers classify it
                # by density (len(uvs)/2 == len(vertices)/2).
                "arm-glove": {"type": "mesh", "width": 6.0, "height": 6.0,
                              "vertices": [-3.0, -3.0, 3.0, -3.0,
                                           3.0, 3.0, -3.0, 3.0],
                              "uvs": [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0],
                              "triangles": [0, 1, 2, 0, 2, 3]},
            },
        }}],
        "animations": {
            "idle": {
                "bones": {
                    "torso": {
                        "rotate": [{"time": 0.0, "value": 0.0},
                                   {"time": 0.5, "value": 6.0}],
                        # Setup-pose scale travels (bone scaleX/scaleY); SCALE
                        # animation tracks do not — src/in_spine.py reads only
                        # the rotate/translate channels (see ROADMAP). Kept here
                        # because real exports carry them and neither reader may
                        # choke on the key.
                        "scale": [{"time": 0.0, "x": 1.0, "y": 1.0},
                                  {"time": 0.5, "x": 1.0, "y": 1.0}],
                    },
                    "arm": {
                        "rotate": [{"time": 0.0, "value": 0.0},
                                   {"time": 0.5, "value": -12.0}],
                        "translate": [{"time": 0.0, "x": 0.0, "y": 0.0},
                                      {"time": 0.5, "x": 2.0, "y": -1.0}],
                    },
                },
                "slots": {"arm": {"attachment": [{"time": 0.0,
                                                  "name": "arm-glove"}]}},
            },
        },
    }


def write_png(path: Path, size: tuple = PAGE_SIZE) -> None:
    """A flat RGBA PNG, so ``read_png_size`` has an IHDR to read."""
    width, height = size
    raw = b"".join(b"\x00" + bytes((200, 200, 200, 255)) * width
                   for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def atlas_text() -> str:
    """Compact Spine 4.x atlas: page line, page attributes, then each region."""
    x, y, width, height = REGION_BOUNDS
    lines = [PAGE, f"size: {PAGE_SIZE[0]},{PAGE_SIZE[1]}", "filter: Linear,Linear"]
    for name in REGION_NAMES:
        lines += [name, f"bounds: {x},{y},{width},{height}"]
    return "\n".join(lines) + "\n"


def write_spine_export(directory: Path) -> Path:
    """Write ``<dir>/rig.json`` + ``rig.atlas`` + ``rig.png``; return the JSON path."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_png(directory / PAGE)
    (directory / f"{STEM}.atlas").write_text(atlas_text(), encoding="utf-8")
    json_path = directory / f"{STEM}.json"
    json_path.write_text(json.dumps(spine_rig(), indent=2), encoding="utf-8")
    return json_path


def verify(godot_dir: Path, spine_dir: Path) -> int:
    """The CLI end-to-end contract: both directions wrote their bundle."""
    expected = [
        godot_dir / "output" / f"{STEM}.tscn",
        godot_dir / "output" / PAGE,
        spine_dir / "index.html",
        spine_dir / "output" / f"{STEM}.json",
        spine_dir / "output" / f"{STEM}.atlas",
        spine_dir / "output" / PAGE,
    ]
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        print("missing conversion artifacts: " + ", ".join(missing),
              file=sys.stderr)
        return 1
    print("CLI artifacts: " + ", ".join(str(path) for path in expected))
    return 0


def main(argv: list) -> int:
    if len(argv) >= 3 and argv[1] == "build":
        print(write_spine_export(Path(argv[2])))
        return 0
    if len(argv) >= 4 and argv[1] == "verify":
        return verify(Path(argv[2]), Path(argv[3]))
    print("usage: python3 -m tests.ci_rig build <dir> | "
          "verify <godot-dir> <spine-dir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
