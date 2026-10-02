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
import zipfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import out_godot, out_spine, registry

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


def _skelform_drawn_visuals(bundle: Path) -> int | None:
    """How many visuals the written archive hangs off a bone (``None`` if the
    archive cannot be read). One visual per bone is the format's own rule."""
    if bundle.suffix.lower() != ".skf":
        return None            # a bare armature.json: nothing to count
    try:
        with zipfile.ZipFile(bundle) as archive:
            armature = json.loads(archive.read("armature.json"))
    except Exception:
        # A diagnostic must never be the thing that fails a conversion.
        return None
    return len({bone["visuals_id"] for bone in armature.get("bones", [])
                if bone.get("visuals_id", -1) != -1})


@contextmanager
def _rig_pages(input_path: str, source: str, atlas_path: str | None, model):
    """The rig's atlas page images, first page first, for as long as it runs.

    A reader whose images live *inside* the input unpacks them through the
    registry hook, into a scratch directory that lives exactly as long as the
    pages are needed (they are a source for a copy or an archive member, not
    output). Otherwise the pages sit beside the rig file — beside the atlas
    when there is one — and their names come from the attachments, so the
    orchestrator needs no format knowledge.
    """
    hook = getattr(registry.READER_MODULES.get(source), "extract_assets", None)
    if hook is not None:
        with tempfile.TemporaryDirectory(prefix="skeleton-converter-") as scratch:
            yield hook(input_path, scratch)
        return
    base = Path(atlas_path).parent if atlas_path else Path(input_path).parent
    pages = [str(base / page) for page in sorted({a.page for a in model.attachments
                                                  if a.page})
             if (base / page).exists()]
    if not pages and model.texture_path:
        # A source can name its texture without placing it (a Godot scene
        # references res://<name>): the file sits beside the rig file.
        sibling = Path(input_path).parent / Path(model.texture_path).name
        if sibling.exists():
            pages = [str(sibling)]
    yield pages


# SkelForm stores key times as integer frame indices, so a rig authored on
# another time base moves its keys by up to half a frame and can even collapse
# two nearby keys into one. 60 is the format's own default; a finer grid is
# used only when it removes a collapse, because the residual shift of a few
# milliseconds is not worth a dense editor timeline.
SKELFORM_FPS = (60, 120, 240)


def _skelform_timing(model) -> tuple[float, str | None]:
    """Pick the frame rate and describe what it costs (``None`` if exact)."""
    channels = [keys for tracks in model.animations.values()
                for chans in tracks.values() for keys in chans.values()]
    times = [k.time for keys in channels for k in keys]
    if not times:
        return float(SKELFORM_FPS[0]), None
    best = None
    for fps in SKELFORM_FPS:
        collapses = 0
        for keys in channels:
            frames = [round(k.time * fps) for k in keys]
            collapses += len(frames) - len(set(frames))
        shift = max(abs(t - round(t * fps) / fps) for t in times)
        score = (collapses, shift)
        if best is None or score < best[0]:
            best = (score, fps, collapses, shift)
        if collapses == 0 and fps == SKELFORM_FPS[0]:
            break
    (_score, fps, collapses, shift) = best
    if not collapses and shift < 1e-6:
        return float(fps), None
    note = (f"SkelForm stores integer frames: {fps} fps, "
            f"{sum(1 for t in times if abs(t - round(t * fps) / fps) > 1e-6)} "
            f"of {len(times)} key times land off the grid "
            f"(worst {shift * 1000:.1f} ms)"
            + (f" and {collapses} keys collapse into a neighbour"
               if collapses else ""))
    return float(fps), note


def convert(input_path: str, source: str, target: str, out_dir: str,
            name: str | None = None, atlas: str | None = None,
            texture: str | None = None, godot_bin: str | None = None,
            fps: float | None = None, skin: str | None = None,
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
    # `skin` picks the rig's variant where the format has them. Spine marks the
    # bones a skin owns (`skin: true`) and its runtime draws them only while
    # that skin is ACTIVE, so a weapon that lives in its own skin is invisible
    # — and, worse, unbaked — unless the conversion names it. Readers without
    # skins are called without the argument.
    extra = {"skin": skin} if (source == "spine" and skin) else {}
    model = (reader(input_path, atlas_path, **extra) if atlas_path
             else reader(input_path, **extra))
    result.notes = list(model.notes)   # printed again below, once steps restart

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
        with _rig_pages(input_path, source, atlas_path, model) as pages:
            if not pages:
                result.notes.append(
                    f"{source}: no page image found — the bundle is written "
                    "without a texture, so a preview draws it untextured")
            if pages:
                model.texture_path = pages[0]
                step(f"atlas page: {', '.join(Path(p).name for p in pages)}")
                if len(pages) > 1:
                    model.notes.append(
                        f"{source}: multi-page atlas — the Spine leg writes "
                        "one page, so attachments on the other pages sample "
                        "the wrong pixels")
            else:
                model.notes.append(
                    f"{source}: no atlas page found inside the input")
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
            moved = []
            for file_name in (output.name,
                              output.with_suffix(".atlas").name, image_name):
                source_file = (bundle_dir / file_name if (bundle_dir /
                                                          file_name).exists()
                               else out / file_name)
                if not source_file.is_file():
                    # A rig read without its page image has no texture to
                    # ship; the JSON and the atlas still form a bundle, and
                    # moving a file nobody wrote is a crash, not a warning.
                    continue
                shutil.move(str(source_file), bundle_dir / file_name)
                moved.append(bundle_dir / file_name)
        moved_json = bundle_dir / output.name
        from . import viewer_out
        viewer_out.emit_viewer(str(out / "index.html"),
                               skeleton_json_path=str(moved_json),
                               skeleton_url=f"output/{output.name}",
                               atlas_url=f"output/{output.with_suffix('.atlas').name}")
        result.files = [out / "index.html", moved_json] + [
            path for path in moved if path.name != output.name]
        step(f"wrote index.html + output/{output.name}, "
             f"output/{output.with_suffix('.atlas').name}, output/{image_name}")
        step(f"{result.stats['bones']} bones, "
             f"{result.stats['attachments']} attachments, "
             f"{result.stats['animations']} animations")
        step("preview: python3 -m http.server --directory "
             f"{out}   then open http://localhost:8000/")
    elif target == "tres":
        step("writing Godot resource (.tres)")
        artifacts = out / "output"
        artifacts.mkdir(parents=True, exist_ok=True)
        output = artifacts / f"{name}.tres"
        registry.WRITERS["tres"](model, str(output))
        result.files = [output]
        step(f"wrote output/{output.name}")
        step(f"{result.stats['bones']} bones, "
             f"{result.stats['attachments']} attachments, "
             f"{result.stats['animations']} animations")
        step("next: load it in Godot and hand it to "
             "AnimationPlayer.add_animation_library — a .tres carries the "
             "animations, not the skeleton node graph")
    elif target == "skelform":
        step("writing SkelForm bundle (.skf: armature + embedded pages)")
        artifacts = out / "output"
        artifacts.mkdir(parents=True, exist_ok=True)
        output = artifacts / f"{name}.skf"
        if fps is None:
            fps, timing_note = _skelform_timing(model)
            if timing_note:
                result.notes.append(timing_note)
        else:
            timing_note = None
        with _rig_pages(input_path, source, atlas_path, model) as pages:
            registry.WRITERS["skelform"](model, str(output), fps=fps,
                                         atlas_paths=pages)
            page_count = len(pages)
        # The browser shell plays the archive through SkelForm's own web
        # runtime, so a SkelForm bundle is as previewable as a Spine one and
        # a comparison pane can show the written .skf instead of a replay.
        from . import skelform_viewer_out
        skelform_viewer_out.emit_viewer(str(out / "index.html"),
                                        f"output/{output.name}")
        result.previewable = True
        result.files = [out / "index.html", output]
        # One visual per bone: an attachment the source drew can end up with no
        # bone to hang off (extra slots sharing a bone, or alternatives). Count
        # it from what was written, so the note matches the file.
        drawn = _skelform_drawn_visuals(output)
        if drawn is not None and drawn < len(model.attachments):
            result.notes.append(
                f"skelform: one visual per bone — {len(model.attachments) - drawn} "
                f"of {len(model.attachments)} attachment(s) are in the archive but "
                "no bone draws them")
            step(result.notes[-1])
        step(f"wrote output/{output.name} at {fps:g} fps"
             + (f" with {page_count} embedded page(s)" if page_count else ""))
        if timing_note:
            step(timing_note)
        step(f"{result.stats['bones']} bones, "
             f"{result.stats['attachments']} attachments, "
             f"{result.stats['animations']} animations")
        step(f"web preview: {out / 'index.html'} (SkelForm's own web player)")
        step("next: open it in the SkelForm editor, or serve the folder and "
             "play it in the browser")
    else:
        step("writing Godot scene (.tscn + page image)")
        output = out / f"{name}.tscn"
        # The rig's first page is re-stemmed to the bundle name (the scene
        # references res://<name>.<ext>); every further page keeps its own
        # name, which is how the attachments reference it. A scene that
        # points at a texture nobody copied does not load, so the pages are
        # copied here, not merely referenced.
        with _rig_pages(input_path, source, atlas_path, model) as pages:
            if texture:
                texture_ref = texture
            elif pages:
                texture_ref = "res://" + name + Path(pages[0]).suffix
            else:
                texture_ref = "res://image.png"
            out_godot.write_godot_scene(model, str(output),
                                        texture_path=texture_ref)
            # The pages are copied while the resolver is still open (they may
            # live in a scratch directory): the first under the bundle stem,
            # every other under its own name, which is how the scene
            # references it.
            copied = []
            if pages and not texture:
                dest = out / (name + Path(pages[0]).suffix)
                if Path(pages[0]).resolve() != dest.resolve():
                    shutil.copy2(pages[0], dest)
                copied.append(dest)
            for page in pages[1:]:
                dest = out / Path(page).name
                if Path(page).resolve() != dest.resolve():
                    shutil.copy2(page, dest)
                copied.append(dest)
        # Artifacts live in output/ next to the browser shell; the web preview
        # build relocates them into its own project/output/.
        artifacts = out / "output"
        artifacts.mkdir(exist_ok=True)
        shutil.move(str(output), artifacts / output.name)
        for page_file in copied:
            if page_file.exists():
                shutil.move(str(page_file), artifacts / page_file.name)
        result.files = [artifacts / output.name]
        result.texture = texture_ref
        wrote = f"output/{output.name}"
        if copied:
            wrote += ", " + ", ".join(f"output/{c.name}" for c in copied)
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
