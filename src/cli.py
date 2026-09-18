"""Command-line interface for skeletonconverter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import registry


def convert(args: argparse.Namespace) -> int:
    reader = registry.READERS[args.from_format]
    writer = registry.WRITERS[args.to_format]
    model = reader(args.input)
    atlas_path = getattr(args, "atlas", None)
    if atlas_path is None:
        sibling = Path(args.input).with_suffix(".atlas")
        if sibling.exists():
            atlas_path = str(sibling)
    writer(model, args.output, atlas_path=atlas_path)
    print(f"wrote {args.output}: {len(model.bones)} bones, "
          f"{len(model.attachments)} attachments, {len(model.animations)} animations")
    return 0


def compare(args: argparse.Namespace) -> int:
    reader = registry.READERS
    model_a = reader[args.from_format](args.input_a)
    model_b = reader[args.from_format](args.input_b)
    # Compare rest pose positions
    from .model import godot_world_transforms
    world_a = godot_world_transforms(model_a)
    world_b = godot_world_transforms(model_b)
    worst = 0.0
    worst_bone = None
    for name in world_a:
        if name not in world_b:
            continue
        d = ((world_a[name][4] - world_b[name][4]) ** 2 +
             (world_a[name][5] - world_b[name][5]) ** 2) ** 0.5
        if d > worst:
            worst, worst_bone = d, name
    print(f"bones compared: {len(model_a.bones)}")
    print(f"worst bone deviation: {worst:.6f} ({worst_bone})")
    ok = worst < 0.01
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="skeletonconverter",
        description="Convert 2D skeletal animation rigs between formats.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    convert_parser = sub.add_parser("convert")
    convert_parser.add_argument("--from", dest="from_format", choices=list(registry.READERS), required=True)
    convert_parser.add_argument("--to", dest="to_format", choices=list(registry.WRITERS), required=True)
    convert_parser.add_argument("input")
    convert_parser.add_argument("-o", "--output", required=True)
    convert_parser.add_argument("--atlas", default=None,
                                help="Spine .atlas beside the JSON (Spine→Godot)")
    convert_parser.set_defaults(func=convert)

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--format", choices=list(registry.READERS), required=True)
    compare_parser.add_argument("input_a")
    compare_parser.add_argument("input_b")
    compare_parser.set_defaults(func=compare)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
