#!/usr/bin/env python3
"""Compare what the Godot scene draws against what the Spine runtime draws.

The Godot leg runs the scene with its AnimationPlayer and dumps the skinned
polygon vertices (`validation/dump-skin.gd`); the Spine leg runs the reference
runtime's `computeWorldVertices` (`validation/mesh-vertices.mjs`). Both sides
report the same vertices, so the difference is the conversion's deform error.

    python3 validation/deform_parity.py <skeleton.json> <atlas> <animation> \\
            --times 0,0.1,0.2 --meshes body,eyes

Requires Godot and the runtime installed under `validation/node_modules`
(`npm install` there).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GODOT = "/Applications/Godot.app/Contents/MacOS/Godot"
sys.path.insert(0, str(ROOT))

from src.in_spine import read_skeleton  # noqa: E402
from src.out_godot import write_godot_scene  # noqa: E402


def _parse_dump(text: str) -> dict:
    """Dumper output -> {bone: {"pose": t, "rest": t}}, {attachment: [(x, y)]}."""
    bones: dict = {}
    polys: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] in ("BONE", "REST"):
            vals = [float(v) for v in parts[2:8]]
            bones.setdefault(parts[1], {})[parts[0].lower()] = tuple(vals)
        elif parts[0] == "POLY":
            count = int(parts[4])
            coords = [float(v) for v in parts[5:5 + count * 2]]
            polys[parts[3]] = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
    return bones, polys


def godot_vertices(project: Path, scene: str, animation: str, time: float) -> dict:
    out = subprocess.run(
        [GODOT, "--headless", "--path", str(project), "--script", "res://dump_skin.gd",
         "--", f"res://{scene}", animation, str(time)],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"godot failed: {out.stderr[-2000:]}")
    return _parse_dump(out.stdout)[1]


def spine_vertices(json_path: Path, animation: str, time: float, mesh: str) -> list:
    # `mesh` names the ATTACHMENT; `slot/mesh` names both, for rigs where the
    # slot carries a different name (the goblins' dagger lives in
    # right-hand-item).
    slot, _, attachment = mesh.partition("/")
    out = subprocess.run(
        ["node", str(ROOT / "validation" / "mesh-vertices.mjs"), str(json_path),
         animation, str(time), slot, attachment or slot],
        cwd=ROOT / "validation", capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"runtime failed: {out.stderr[-800:]}")
    flat = json.loads(out.stdout)["vertices"]
    return [(flat[i], -flat[i + 1]) for i in range(0, len(flat), 2)]  # spine -> godot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("skeleton")
    parser.add_argument("atlas")
    parser.add_argument("animation")
    parser.add_argument("--times", default="0,0.1,0.2,0.3,0.4")
    parser.add_argument("--meshes", default="")
    parser.add_argument("--scene-name", default="rig.tscn")
    args = parser.parse_args()

    json_path = Path(args.skeleton).resolve()
    times = [float(t) for t in args.times.split(",")]
    meshes = [m for m in args.meshes.split(",") if m]

    model = read_skeleton(str(json_path), str(Path(args.atlas).resolve()))
    project = Path(tempfile.mkdtemp(prefix="deform-parity-"))
    try:
        write_godot_scene(model, str(project / args.scene_name), "res://atlas0.png")
        atlas_dir = Path(args.atlas).resolve().parent
        page = next((ln.strip() for ln in Path(args.atlas).read_text().splitlines()
                     if ln.strip()), "")
        page_png = atlas_dir / page
        if not page_png.exists():
            print(f"atlas page {page_png} not found", file=sys.stderr)
            return 2
        shutil.copy(page_png, project / "atlas0.png")
        shutil.copy(ROOT / "src" / "godot_preview" / "project.godot", project)
        shutil.copy(ROOT / "validation" / "dump-skin.gd", project / "dump_skin.gd")
        subprocess.run([GODOT, "--headless", "--path", str(project), "--import"],
                       capture_output=True, text=True)

        worst_all = 0.0
        for time in times:
            drawn = godot_vertices(project, args.scene_name, args.animation, time)
            for mesh in (meshes or sorted(drawn)):
                key = mesh.split("/")[-1]
                if key not in drawn:
                    print(f"t={time:<6} {mesh:<10} ausente no dump")
                    continue
                got = spine_vertices(json_path, args.animation, time, mesh)
                want = drawn[key]
                if len(got) != len(want):
                    print(f"t={time:<6} {mesh:<10} contagem difere: "
                          f"engine {len(want)} runtime {len(got)}")
                    continue
                err = max((abs(a[0] - b[0]) ** 2 + abs(a[1] - b[1]) ** 2) ** 0.5
                          for a, b in zip(want, got))
                worst_all = max(worst_all, err)
                flag = "ok" if err < 0.05 else "DIVERGE"
                print(f"t={time:<6} {mesh:<10} erro máx = {err:9.3f}  {flag}")
        print(f"\npior erro: {worst_all:.3f}")
    finally:
        shutil.rmtree(project, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
