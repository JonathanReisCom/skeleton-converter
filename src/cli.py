"""Command-line interface for skeleton-converter."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import out_godot, out_spine, registry


def convert(args: argparse.Namespace) -> int:
    reader = registry.READERS[args.from_format]
    model = reader(args.input)
    output = Path(args.output)
    if args.to_format == "spine":
        image_path = out_spine.resolve_texture_path(model.texture_path, args.input)
        # One stem for the whole bundle: the atlas is named after the output
        # JSON, so the page image is too. Keeping the source texture's name
        # (gBot.png) beside animation.json/animation.atlas reads as a stray
        # file and makes the handoff ambiguous.
        image_name = (
            output.stem + Path(image_path).suffix if image_path else "image.png"
        )
        out_spine.write_spine_json(model, args.output, image_name=image_name,
                                   image_path=image_path)
        # Complete handoff: texture beside the JSON (the atlas names it) and a
        # reusable viewer shell, so the output folder is immediately servable.
        if image_path and Path(image_path) != output.parent / image_name:
            shutil.copy2(image_path, output.parent / image_name)
        from . import viewer_out
        viewer_path = viewer_out.emit_viewer(str(output.parent / "index.html"),
                                             skeleton_json_path=args.output)
        print(f"wrote {args.output} + {output.with_suffix('.atlas').name}")
        print(f"wrote {image_name} (texture) + {Path(viewer_path).name}")
        print("preview: python3 -m http.server --directory "
              f"{output.parent} then open http://localhost:8000/")
    else:
        texture = args.texture or model.texture_path or "res://image.png"
        out_godot.write_godot_scene(model, args.output, texture_path=texture)
        print(f"wrote {args.output}")
    print(f"{len(model.bones)} bones, {len(model.attachments)} attachments, "
          f"{len(model.animations)} animations")
    return 0


def compare(args: argparse.Namespace) -> int:
    reader = registry.READERS
    model_a = reader[args.format](args.input_a)
    model_b = reader[args.format](args.input_b)
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


def view(args: argparse.Namespace) -> int:
    from . import viewer_out
    out = viewer_out.emit_viewer(
        str(Path(args.skeleton).with_name("index.html")),
        skeleton_json_path=args.skeleton,
    )
    print(f"wrote {out}")
    print("preview: python3 -m http.server --directory "
          f"{Path(args.skeleton).parent} then open http://localhost:8000/")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="skeleton-converter",
        description="Convert 2D skeletal animation rigs between formats.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    convert_parser = sub.add_parser("convert")
    convert_parser.add_argument("--from", dest="from_format",
                                choices=list(registry.READERS), required=True)
    convert_parser.add_argument("--to", dest="to_format",
                                choices=list(registry.WRITERS), required=True)
    convert_parser.add_argument("input")
    convert_parser.add_argument("-o", "--output", required=True)
    convert_parser.add_argument("--atlas", default=None,
                                help=".atlas beside the input JSON (Spine→Godot)")
    convert_parser.add_argument("--texture", default=None,
                                help="texture path the Godot scene references")
    convert_parser.set_defaults(func=convert)

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--format", choices=list(registry.READERS), required=True)
    compare_parser.add_argument("input_a")
    compare_parser.add_argument("input_b")
    compare_parser.set_defaults(func=compare)

    view_parser = sub.add_parser("view")
    view_parser.add_argument("skeleton", help="Spine JSON to preview")
    view_parser.set_defaults(func=view)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
