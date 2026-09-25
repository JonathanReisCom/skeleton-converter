"""Output-bundle seam: convert, compare, and view as one interface.

Absorbs the choreography that used to live in the CLI: atlas/texture
resolution, output-bundle assembly (artifacts in ``output/``, shells at the
root), file moves, and result reporting. The CLI stays a thin parser over
these functions; the validation harness bypasses this module entirely and
reads the canonical model through ``registry`` — the seam is a door, not a
wall.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import in_spine, out_godot, out_spine, registry

StepFn = Callable[[str], None]


@dataclass
class ConvertResult:
    out_dir: Path
    name: str
    files: list[Path] = field(default_factory=list)   # bundle members, by name
    notes: list[str] = field(default_factory=list)    # reader-learned facts
    texture: str | None = None                        # texture the scene references
    previewable: bool = False                         # True iff index.html written
    stats: dict = field(default_factory=dict)


@dataclass
class CompareResult:
    bones: int
    worst_deviation: float
    worst_bone: str | None
    passed: bool


def _find_atlas(input_path: Path) -> str | None:
    """Spine atlas beside the input JSON. The atlas name does not always
    mirror the JSON's (hero-pro.json ships with hero.atlas), so fall back to
    the only .atlas beside the input. Spine also exports ``<name>.atlas.txt``
    for Git-friendly hosting; accept that suffix too."""
    for suffix in (".atlas", ".atlas.txt"):
        sibling = input_path.parent / (input_path.stem + suffix)
        if sibling.exists():
            return str(sibling)
    candidates = sorted(
        p for p in input_path.parent.glob("*.atlas*")
        if p.suffix in (".atlas", ".txt"))
    return str(candidates[0]) if len(candidates) == 1 else None


def convert(input_path: str, source: str, target: str, out_dir: str,
            name: str | None = None, atlas: str | None = None,
            texture: str | None = None, godot_bin: str | None = None,
            step: StepFn = print) -> ConvertResult:
    """Convert one rig file to a format bundle in ``out_dir``.

    - ``out_dir`` is a DIRECTORY: the output is a bundle, not a single file.
    - ``name`` is the bundle stem; it defaults to the input's stem and drives
      the JSON/scene, atlas, and page image names (one stem for the bundle).
    - ``notes`` is exactly the reader's note list; this layer never recomputes
      format facts.
    - The bundle layout is shells at the root (index.html) and artifacts under
      ``output/`` — documented behaviour preserved for existing served folders.
    """
    out = Path(out_dir)
    if out.suffix:
        raise ValueError(
            f"-o takes a directory, not a file: {out} "
            f"(try: {out.parent} --name {out.stem})")
    name = name or Path(input_path).stem
    result = ConvertResult(out_dir=out, name=name)

    step(f"reading {source}: {input_path}")
    reader = registry.READERS[source]
    atlas_path = atlas
    if source == "spine" and not atlas_path:
        atlas_path = _find_atlas(Path(input_path))
    model = (reader(input_path, atlas_path) if atlas_path
             else reader(input_path))
    result.notes = list(model.notes)

    step(f"destination: {out}")
    step(f"name: {name}")
    for note in result.notes:
        step(note)
    result.stats = {
        "bones": len(model.bones),
        "attachments": len(model.attachments),
        "animations": len(model.animations),
    }

    out.mkdir(parents=True, exist_ok=True)
    if target == "spine":
        result.previewable = True
        step("writing Spine bundle (json + atlas + texture + viewer)")
        output = out / f"{name}.json"
        image_path = out_spine.resolve_texture_path(model.texture_path,
                                                    input_path)
        image_name = (name + Path(image_path).suffix
                      if image_path else "image.png")
        out_spine.write_spine_json(model, str(output), image_name=image_name,
                                   image_path=image_path)
        if image_path and Path(image_path) != out / image_name:
            shutil.copy2(image_path, out / image_name)
        # Browser preview: index.html at the output root, the bundle in
        # output/ — the shell fetches "output/<name>.json" with plain paths
        # (no ../), so any static server pointed at the output root works.
        bundle_dir = out / "output"
        bundle_dir.mkdir(exist_ok=True)
        for file_name in (output.name,
                          output.with_suffix(".atlas").name, image_name):
            source_file = (bundle_dir / file_name if (bundle_dir /
                                                      file_name).exists()
                           else out / file_name)
            shutil.move(str(source_file), bundle_dir / file_name)
        moved_json = bundle_dir / output.name
        from . import viewer_out
        viewer_out.emit_viewer(str(out / "index.html"),
                               skeleton_json_path=str(moved_json),
                               skeleton_url=f"output/{output.name}",
                               atlas_url=f"output/{output.with_suffix('.atlas').name}")
        result.files = [
            out / "index.html",
            moved_json,
            bundle_dir / output.with_suffix(".atlas").name,
            bundle_dir / image_name,
        ]
        step(f"wrote index.html + output/{output.name}, "
             f"output/{output.with_suffix('.atlas').name}, output/{image_name}")
        step(f"{result.stats['bones']} bones, "
             f"{result.stats['attachments']} attachments, "
             f"{result.stats['animations']} animations")
        step("preview: python3 -m http.server --directory "
             f"{out}   then open http://localhost:8000/")
    else:
        step("writing Godot scene (.tscn + page image)")
        output = out / f"{name}.tscn"
        # Spine→Godot: the atlas names the real page image. Resolve it the
        # same way the reader does, instead of falling back to res://image.png
        # — a scene referencing a texture that does not exist will not load.
        image = in_spine.resolve_image_for_atlas(atlas_path, "")
        # Multi-page atlas: every attachment carries its page; copy every
        # page PNG beside the scene under its atlas name. The first page is
        # re-stemmed (res://<name>.<ext>) like the single-page case.
        rig_pages = sorted({a.page for a in model.attachments if a.page})
        if texture:
            texture_ref = texture
        elif rig_pages:
            first_page = Path(atlas_path).parent / rig_pages[0] \
                if atlas_path else None
            texture_ref = "res://" + (name + Path(rig_pages[0]).suffix)
            image = str(first_page) if first_page and first_page.exists() \
                else None
        elif image:
            texture_ref = "res://" + name + Path(image).suffix
        else:
            texture_ref = "res://image.png"
        out_godot.write_godot_scene(model, str(output), texture_path=texture_ref)
        # One stem for the whole bundle: the scene references
        # res://<name>.<ext>, so the texture is copied under the bundle stem
        # (the atlas may name the page differently, e.g. "custom assets.png"
        # for an "animation" bundle). Multi-page rigs reference every page by
        # its atlas name, so every page file is copied as-is.
        texture_file = None
        if image and not texture and Path(image).exists():
            texture_file = name + Path(image).suffix
            dest = out / texture_file
            if Path(image).resolve() != dest.resolve():
                shutil.copy2(image, dest)
        # Extra atlas pages: copied by their own names — the scene references
        # res://<page> for each.
        page_files = []
        if atlas_path and len(rig_pages) > 1:
            for page in rig_pages[1:]:
                source_file = Path(atlas_path).parent / page
                if source_file.exists():
                    shutil.copy2(source_file, out / page)
                    page_files.append(out / page)
        # Artifacts live in output/ next to the browser shell; the web preview
        # build relocates them into its own project/output/.
        artifacts = out / "output"
        artifacts.mkdir(exist_ok=True)
        shutil.move(str(output), artifacts / output.name)
        if texture_file and (out / texture_file).exists():
            shutil.move(str(out / texture_file), artifacts / texture_file)
        for page_file in page_files:
            if (out / page_file.name).exists():
                shutil.move(str(page_file), artifacts / page_file.name)
        result.files = [artifacts / output.name]
        result.texture = texture_ref
        wrote = f"output/{output.name}"
        if not texture and image:
            wrote += f", output/{name}{Path(image).suffix}"
        step(f"wrote {wrote}")
        step(f"texture: {texture_ref}")
        # A .tscn cannot render in a browser — but the real engine can. Ship a
        # Godot web export (WASM) around the converted scene: a minimal
        # wrapper project loads the scene, plays its first animation, and
        # frames it. This renders the actual .tscn through actual Godot, not a
        # re-export through the canonical model.
        from . import godot_preview as build
        try:
            build.export_preview(out, name, godot_bin=godot_bin)
            result.previewable = True
            step(f"web preview: {out / 'index.html'} "
                 "(serve the output folder with python3 -m http.server)")
        except build.ExportError as error:
            step(f"web preview unavailable: {error}")
        step(f"{result.stats['bones']} bones, "
             f"{result.stats['attachments']} attachments, "
             f"{result.stats['animations']} animations")
        step(f"next: load {artifacts / output.name} in Godot "
             "— a .tscn is a scene, not a web page")
    return result


def compare(input_a: str, input_b: str, fmt: str) -> CompareResult:
    """Numeric pose comparison: sample both models' bone world transforms and
    take the worst translation deviation. PASS < 0.01 units."""
    from .model import godot_world_transforms

    reader = registry.READERS[fmt]
    model_a = reader(input_a)
    model_b = reader(input_b)
    world_a = godot_world_transforms(model_a)
    world_b = godot_world_transforms(model_b)
    worst = 0.0
    worst_bone = None
    for bone_name in world_a:
        if bone_name not in world_b:
            continue
        d = ((world_a[bone_name][4] - world_b[bone_name][4]) ** 2 +
             (world_a[bone_name][5] - world_b[bone_name][5]) ** 2) ** 0.5
        if d > worst:
            worst, worst_bone = d, bone_name
    return CompareResult(bones=len(model_a.bones), worst_deviation=worst,
                         worst_bone=worst_bone, passed=worst < 0.01)


def view(skeleton_json: str) -> Path:
    """Write index.html beside a Spine JSON and return its path."""
    from . import viewer_out
    return Path(viewer_out.emit_viewer(
        str(Path(skeleton_json).with_name("index.html")),
        skeleton_json_path=skeleton_json,
    ))


def compare_page(godot_dir: str, spine_dir: str) -> Path:
    """Write compare.html into the parent of both preview bundles (one HTML
    page, typed panes with shared play/freeze controls); return its path.
    Serve the parent folder with any static server."""
    from .compare_out import emit_compare
    return Path(emit_compare(Path(godot_dir), Path(spine_dir)))


def spine_preview(skeleton_json: str, out_dir: str) -> Path:
    """Pack a Spine rig (JSON + .atlas + page image) into ``out_dir`` and
    emit a viewer index.html beside it; return the index.html path. The page
    image is resolved from the atlas's first line — the atlas names it, the
    JSON does not."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(skeleton_json)
    shutil.copy2(src, out / src.name)
    atlas = _find_atlas(src)
    if atlas:
        atlas_path = Path(atlas)
        # The viewer derives the atlas URL from the JSON's stem; a Git-hosted
        # `.atlas.txt` is copied renamed so that URL resolves.
        atlas_name = src.stem + ".atlas"
        shutil.copy2(atlas_path, out / atlas_name)
        # The atlas may hold several pages (page2, page3, ...): every line
        # naming a .png that exists beside the atlas is a page to copy.
        for line in atlas_path.read_text(encoding="utf-8").splitlines():
            page = line.strip()
            if page.endswith(".png") and (atlas_path.parent / page).exists():
                shutil.copy2(atlas_path.parent / page, out / page)
    return view(str(out / src.name))
