"""Command-line interface for skeleton-converter."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import in_spine, out_godot, out_spine, registry


def _step(text: str) -> None:
    """One progress line, so the user sees what is happening and with what."""
    print(f"--> {text}")


def convert(args: argparse.Namespace) -> int:
    reader = registry.READERS[args.from_format]
    # -o is a DIRECTORY: the output is a bundle (json + atlas + texture +
    # index.html for spine; a scene for godot), not a single file. The stem is
    # --name, defaulting to the input's stem, so `-o out/` alone does the
    # obvious thing instead of forcing a filename.
    out_dir = Path(args.output)
    if out_dir.suffix:
        print(f"error: -o takes a directory, not a file: {out_dir}", file=sys.stderr)
        print(f"       try: -o {out_dir.parent} --name {out_dir.stem}", file=sys.stderr)
        return 2
    name = args.name or Path(args.input).stem

    _step(f"reading {args.from_format}: {args.input}")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Spine input: the .atlas carries the page size and region rects. Without
    # it UVs are computed against a 1x1 page and every region samples wrong.
    # Accept an explicit --atlas, else auto-detect beside the input JSON.
    atlas_path = args.atlas
    if args.from_format == "spine" and not atlas_path:
        # The atlas name does not always mirror the JSON's (hero-pro.json ships
        # with hero.atlas), so fall back to the only .atlas beside the input.
        sibling = Path(args.input).with_suffix(".atlas")
        if sibling.exists():
            atlas_path = str(sibling)
        else:
            candidates = sorted(Path(args.input).parent.glob("*.atlas"))
            atlas_path = str(candidates[0]) if len(candidates) == 1 else None
    model = reader(args.input, atlas_path) if atlas_path else reader(args.input)

    _step(f"destination: {out_dir}")
    _step(f"name: {name}")
    for note in model.notes:
        _step(note)

    if args.to_format == "spine":
        _step(f"writing Spine bundle (json + atlas + texture + viewer)")
        output = out_dir / f"{name}.json"
        image_path = out_spine.resolve_texture_path(model.texture_path, args.input)
        # One stem for the whole bundle: the atlas is named after the output
        # JSON, so the page image is too.
        image_name = name + Path(image_path).suffix if image_path else "image.png"
        out_spine.write_spine_json(model, str(output), image_name=image_name,
                                   image_path=image_path)
        # Complete handoff: texture beside the JSON (the atlas names it).
        if image_path and Path(image_path) != out_dir / image_name:
            shutil.copy2(image_path, out_dir / image_name)
        # Browser preview: index.html at the output root, the bundle in
        # output/ — the shell fetches "output/<name>.json" with plain paths
        # (no ../), so any static server pointed at the output root works.
        bundle_dir = out_dir / "output"
        bundle_dir.mkdir(exist_ok=True)
        shutil.move(str(output), bundle_dir / output.name)
        shutil.move(str(output.with_suffix(".atlas")), bundle_dir / output.with_suffix(".atlas").name)
        shutil.move(str(out_dir / image_name), bundle_dir / image_name)
        from . import viewer_out
        moved_json = bundle_dir / output.name
        viewer_out.emit_viewer(str(out_dir / "index.html"),
                               skeleton_json_path=str(moved_json),
                               skeleton_url=f"output/{output.name}",
                               atlas_url=f"output/{output.with_suffix('.atlas').name}")
        _step(f"wrote index.html + output/{output.name}, "
              f"output/{output.with_suffix('.atlas').name}, output/{image_name}")
        _step(f"{len(model.bones)} bones, {len(model.attachments)} attachments, "
              f"{len(model.animations)} animations")
        _step("preview: python3 -m http.server --directory "
              f"{out_dir}   then open http://localhost:8000/")
    else:
        _step("writing Godot scene (.tscn + page image)")
        output = out_dir / f"{name}.tscn"
        # Spine→Godot: the atlas names the real page image. Resolve it the same
        # way the reader does, instead of falling back to res://image.png —
        # a scene referencing a texture that does not exist will not load.
        # Without an explicit --texture the image is copied beside the scene
        # under the --name stem, so the bundle reads as one unit (the same rule
        # the spine side follows); an explicit --texture is honoured as-is.
        image = in_spine.resolve_image_for_atlas(atlas_path, "")
        if args.texture:
            texture = args.texture
        elif image:
            texture = "res://" + name + Path(image).suffix
        else:
            texture = "res://image.png"
        out_godot.write_godot_scene(model, str(output), texture_path=texture)
        if image and not args.texture and Path(image).exists():
            dest = out_dir / (name + Path(image).suffix)
            if Path(image).resolve() != dest.resolve():
                shutil.copy2(image, dest)
        # Artifacts live in output/ next to the browser shell; the web preview
        # build relocates them into its own project/output/.
        artifacts = out_dir / "output"
        artifacts.mkdir(exist_ok=True)
        shutil.move(str(output), artifacts / output.name)
        if image and not args.texture and (out_dir / Path(image).name).exists():
            shutil.move(str(out_dir / Path(image).name),
                        artifacts / Path(image).name)
        wrote = f"output/{output.name}"
        if not args.texture and image:
            wrote += f", output/{name}{Path(image).suffix}"
        _step(f"wrote {wrote}")
        _step(f"texture: {texture}")
        # A .tscn cannot render in a browser — but the real engine can. Ship a
        # Godot web export (WASM) around the converted scene: a minimal wrapper
        # project loads the scene, plays its first animation, and frames it.
        # This renders the actual .tscn through actual Godot, not a re-export
        # through the canonical model.
        from . import godot_preview as build
        try:
            build.export_preview(out_dir, name, godot_bin=args.godot)
            _step(f"web preview: {out_dir / 'index.html'} "
                  "(serve the output folder with python3 -m http.server)")
        except build.ExportError as error:
            _step(f"web preview unavailable: {error}")
        _step(f"{len(model.bones)} bones, {len(model.attachments)} attachments, "
              f"{len(model.animations)} animations")
        _step(f"next: load {out_dir / 'output' / output.name} in Godot "
              "— a .tscn is a scene, not a web page")
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
    convert_parser.add_argument("-o", "--output", required=True,
                                help="output directory (created if missing)")
    convert_parser.add_argument("--name", default=None,
                                help="output stem; defaults to the input's name")
    convert_parser.add_argument("--atlas", default=None,
                                help=".atlas beside the input JSON (Spine→Godot)")
    convert_parser.add_argument("--texture", default=None,
                                help="texture path the Godot scene references")
    convert_parser.add_argument("--godot", default=None,
                                help="Godot binary for the web preview "
                                     "(default: GODOT_BIN env or the macOS app)")
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
