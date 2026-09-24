"""Build web previews: a Godot web (WASM) export around a Godot scene. The
browser then renders the REAL .tscn through the REAL engine.

Two entry points share one core:
- ``export_preview`` — wraps a scene already produced by the converter
  (``out_dir/output/<name>.tscn``); texture references are rewritten.
- ``preview_scene`` — packs an arbitrary existing .tscn, copying the
  resources it references (script, textures) at their original res:// paths.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).parent
DEFAULT_GODOT = "/Applications/Godot.app/Contents/MacOS/Godot"


class ExportError(RuntimeError):
    pass


def _godot(godot_bin: str | None) -> str:
    godot = godot_bin or os.environ.get("GODOT_BIN") or DEFAULT_GODOT
    if not Path(godot_bin or DEFAULT_GODOT).exists():
        godot = shutil.which("godot") or shutil.which("godot4")
    if not godot:
        raise ExportError(
            "Godot not found — set GODOT_BIN to your Godot binary to build "
            "the web preview"
        )
    return godot


def _prepare_wrapper(project: Path, scene_res: str) -> None:
    """Assemble the wrapper project; the boot script loads ``scene_res``."""
    project.mkdir(parents=True)
    shutil.copy2(WRAPPER / "project.godot", project / "project.godot")
    shutil.copy2(WRAPPER / "boot.tscn", project / "boot.tscn")
    shutil.copy2(WRAPPER / "export_presets.cfg", project / "export_presets.cfg")
    main = (WRAPPER / "main.gd").read_text()
    (project / "main.gd").write_text(main.replace("__SCENE__", scene_res))


def _run_export(project: Path, out_dir: Path, godot: str) -> None:
    """Export the wrapper, replace the boilerplate html with our shell, and
    distribute: engine files to the output root, scene artifacts in output/."""
    build_dir = project / "build"
    build_dir.mkdir()
    result = subprocess.run(
        [godot, "--headless", "--export-release", "Web", "build/index.html"],
        cwd=project, capture_output=True, text=True, timeout=300,
    )
    generated = build_dir / "index.html"
    if not generated.exists():
        raise ExportError(
            "godot export failed:\n" + (result.stdout + result.stderr)[-2000:]
        )
    # Our shell replaces the engine's boilerplate: same boot config (extracted
    # from the generated index.html), our track panel and styling. Engine
    # files land at the output root — the shell fetches them with plain paths.
    html = generated.read_text(encoding="utf-8")
    config = _extract_boot_config(html)
    runtime_url = _extract_runtime_url(html)
    shell = (Path(__file__).parent.parent /
             "template_godot_viewer.html").read_text(encoding="utf-8")
    out_dir.joinpath("index.html").write_text(
        shell.replace("__GODOT_CONFIG__", config)
             .replace("__GODOT_RUNTIME_URL__", runtime_url),
        encoding="utf-8",
    )
    for build_file in list(build_dir.iterdir()):
        if build_file.name == "index.html":
            continue  # the boilerplate html; our shell replaces it
        shutil.move(str(build_file), str(out_dir / build_file.name))


def _finish(project: Path, out_dir: Path) -> None:
    # Engine files already moved to the output root; whatever the scene added
    # (output/ artifacts, resources at their own res:// subfolders) moves too,
    # preserving paths — res://player/gBot.png must stay res://player/gBot.png.
    wrapper_files = {"project.godot", "project.godot.uid", "export_presets.cfg",
                     "boot.tscn", "boot.tscn.uid", "main.gd", "main.gd.uid"}
    for item in list(project.iterdir()):
        if item.name in wrapper_files or item.name == ".godot":
            continue
        if item.is_dir():
            for inner in list(item.rglob("*")):
                if inner.is_file() and not inner.name.endswith(".import"):
                    dest = out_dir / item.name / inner.relative_to(item)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(inner), str(dest))
        else:
            shutil.move(str(item), str(out_dir / item.name))
    shutil.rmtree(project)


def export_preview(out_dir: Path, name: str, godot_bin: str | None = None) -> None:
    """Wrap a converter-produced scene (``out_dir/output/<name>.tscn``)."""
    scene = out_dir / "output" / f"{name}.tscn"
    if not scene.exists():
        raise ExportError(f"converted scene missing: {scene}")
    project = out_dir / ".preview-build"
    if project.exists():
        shutil.rmtree(project)
    _prepare_wrapper(project, f"output/{name}")

    # The scene is copied AS-IS — rewriting its ext_resources (a texture here,
    # a script there) once corrupted a whole scene (the script became the
    # texture). Referenced resources are copied at their original res:// paths:
    # res://animation.png resolves to output/animation.png inside the wrapper,
    # and references the wrapper cannot satisfy fall to Godot's missing-
    # resource handling (converted scenes reference only what exists here).
    project_output = project / "output"
    project_output.mkdir()
    shutil.copy2(scene, project_output / scene.name)
    scene_text = scene.read_text(encoding="utf-8")
    for referenced in re.findall(r'path="res://([^"]+)"', scene_text):
        candidate = out_dir / "output" / referenced
        if candidate.exists():
            dest = project / referenced
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, dest)

    _run_export(project, out_dir, _godot(godot_bin))
    _finish(project, out_dir)


def preview_scene(scene_path: str | Path, out_dir: Path, name: str,
                  godot_bin: str | None = None) -> None:
    """Pack an existing .tscn and its referenced resources for the browser.

    The scene goes to ``out_dir/output/<name>.tscn``; every ``ext_resource``
    it references is copied into the wrapper at its original res:// path, so
    raw editor scenes (which reference textures by their own subfolders) load
    unmodified. Resources the filesystem does not have are left to Godot's
    missing-resource handling.
    """
    scene_path = Path(scene_path)
    out_dir = Path(out_dir)
    if not scene_path.exists():
        raise ExportError(f"scene not found: {scene_path}")
    project = out_dir / ".preview-build"
    if project.exists():
        shutil.rmtree(project)
    _prepare_wrapper(project, f"output/{name}")

    project_output = project / "output"
    project_output.mkdir()
    shutil.copy2(scene_path, project_output / f"{name}.tscn")
    scene_text = scene_path.read_text(encoding="utf-8")
    scene_dir = scene_path.parent
    for referenced in re.findall(r'path="res://([^"]+)"', scene_text):
        for candidate in (scene_dir / referenced,
                          scene_dir / Path(referenced).name):
            if candidate.exists():
                dest = project / referenced
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, dest)
                break

    _run_export(project, out_dir, _godot(godot_bin))
    _finish(project, out_dir)


def _extract_boot_config(html: str) -> str:
    """The GODOT_CONFIG object literal the export template baked in."""
    match = re.search(r"const GODOT_CONFIG = (\{.*?\});", html)
    if not match:
        raise ExportError("GODOT_CONFIG not found in the generated index.html")
    return match.group(1)


def _extract_runtime_url(html: str) -> str:
    """The engine's JS entry (index.js) the boilerplate loads."""
    match = re.search(r'<script src="([^"]+\.js)"></script>', html)
    if not match:
        raise ExportError("engine runtime script not found in the generated index.html")
    return match.group(1)