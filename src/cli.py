"""Command-line interface for skeleton-converter.

Thin by design: argparse over the bundle seam, plus progress printing. All
conversion, comparison, and bundle choreography lives in bundle.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bundle, registry


def _step(text: str) -> None:
    """One progress line, so the user sees what is happening and with what."""
    print(f"--> {text}")


def _cmd_convert(args: argparse.Namespace) -> int:
    try:
        result = bundle.convert(args.input, args.from_format, args.to_format,
                                args.output, name=args.name, atlas=args.atlas,
                                texture=args.texture, godot_bin=args.godot,
                                step=_step)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    verdict = bundle.compare(args.input_a, args.input_b, args.format)
    print(f"bones compared: {verdict.bones}")
    print(f"worst bone deviation: {verdict.worst_deviation:.6f} "
          f"({verdict.worst_bone})")
    print("RESULT:", "PASS" if verdict.passed else "FAIL")
    return 0 if verdict.passed else 1


def _cmd_view(args: argparse.Namespace) -> int:
    out = bundle.view(args.skeleton)
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
    convert_parser.set_defaults(func=_cmd_convert)

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--format", choices=list(registry.READERS), required=True)
    compare_parser.add_argument("input_a")
    compare_parser.add_argument("input_b")
    compare_parser.set_defaults(func=_cmd_compare)

    view_parser = sub.add_parser("view")
    view_parser.add_argument("skeleton", help="Spine JSON to preview")
    view_parser.set_defaults(func=_cmd_view)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
