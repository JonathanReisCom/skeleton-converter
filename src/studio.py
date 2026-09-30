"""Local conversion studio: drop a rig, get every output side by side.

One stdlib server over the same ``bundle.convert`` the CLI uses. The browser
reads the chosen files and posts them as base64 JSON, so the server needs no
multipart parser; it writes them into a job folder, detects which of them is
the rig, converts it into EVERY other format, and emits the compare shell
(``compare_out``) over the source first and one pane per converted output after
it — the files for all of them are on disk either way.

    python3 -m src.studio [PORT] [--root DIR] [--godot BIN]

A job is a folder under the root, which is also the served directory:

    <root>/<job>/upload/                  what the browser sent
    <root>/<job>/1-source-<fmt>/          the source rig, playable in a browser
    <root>/<job>/<n>-target-<fmt>/        what each conversion wrote
    <root>/<job>/compare.html             source first, every output after it

The FIRST pane plays the ORIGINAL file — the Spine rig with its own atlas, a
SkelForm archive, or a Godot scene packed for the browser engine
(``godot_preview.preview_scene``) — and every pane after it plays what a
conversion wrote, each through the runtime that reads it (the Spine viewer,
SkelForm's own web player, the real engine in WASM). A format the rig is already
in is not converted into itself. Only a pane that cannot be built at all (a
Godot target with no engine binary to export with, or ``.tres``, which carries
animations only) replays through the Spine leg, and its folder name says
``via-spine`` so the pane never claims more than it shows.
"""

from __future__ import annotations

import base64
import http.server
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

from . import (bundle, compare_out, detect, hud_assets, registry,
               skelform_viewer_out, viewer_out)
from . import godot_preview
from .devserver import NoCacheHandler
from .godot_preview import ExportError, preview_scene

DEFAULT_PORT = 8090
DEFAULT_ROOT = Path("tmp/studio")
TEMPLATE = Path(__file__).parent / "template_studio.html"

# Every target here has a browser shell, and any that fails to build falls back
# to "spine" — the one leg every reader can produce and this repo can play.
PROXY = "spine"
# .tres carries animations, not a scene: there is nothing to look at, so the
# studio does not offer it (the CLI does).
TARGETS = ("spine", "godot", "skelform")
TARGET_HINTS = {
    "spine": "writes <name>.json + .atlas + the page + a viewer — the pane plays "
             "the Spine runtime reading what this leg wrote.",
    "godot": "writes <name>.tscn + the page and exports it for the browser — the "
             "pane plays the scene in the real engine (WASM).",
    "skelform": "writes one <name>.skf with its pages inside, and the pane "
                "plays that archive in SkelForm's own web player.",
}
COMPANION_SUFFIXES = (".atlas", ".atlas.txt", ".png", ".import", ".md")
MAX_UPLOAD = 64 * 1024 * 1024
MAX_BODY = 4 * MAX_UPLOAD


class StudioError(RuntimeError):
    """A problem the browser should see verbatim (bad upload, no reader…)."""


def _reload_src() -> None:
    """Reimport every `src.*` module except this one.

    A long-running studio otherwise serves conversions with whatever code the
    process was started with — the classic "I already fixed that" trap. Two
    passes settle the dependency order between the modules. The studio's own
    handler code is NOT reloaded: the running request class keeps the code it
    was started with, so `src/studio.py` edits still need a restart.
    """
    names = [name for name in sys.modules
             if name.startswith("src.") and name != __name__]
    for _ in range(2):
        for name in sorted(names):
            try:
                importlib.reload(sys.modules[name])
            except Exception as error:  # noqa: BLE001 — report, keep serving
                print(f"studio: hot reload of {name} failed: {error}")


def _slug(text: str) -> str:
    """A folder-safe stem; the job name is also a URL segment."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", text or "").strip("-._")
    return safe[:48] or "rig"


def _job_name(stem: str) -> str:
    """A job is one upload and every output, so its name carries no target."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(stem)}"


def _decode(name: str, data: str) -> bytes:
    """One uploaded file: base64 text with a browser-supplied name.

    The browser sends only the basename, and it is re-derived here anyway — a
    crafted name must never escape the job folder.
    """
    try:
        raw = base64.b64decode(data or "", validate=True)
    except (ValueError, TypeError) as error:
        raise StudioError(f"{name}: not valid base64 ({error})") from error
    if len(raw) > MAX_UPLOAD:
        raise StudioError(f"{name}: larger than the {MAX_UPLOAD // 1048576} MB limit")
    if not raw:
        raise StudioError(f"{name}: empty file")
    return raw


def _write_uploads(job: Path, files: list) -> list:
    """Write the posted files into ``<job>/upload``; return their paths."""
    upload = job / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    written = []
    for entry in files:
        name = Path(str(entry.get("name", "file"))).name or "file"
        path = upload / name
        path.write_bytes(_decode(name, entry.get("data", "")))
        written.append(path)
    return written


def _copy_spine_pane(job: Path, index: int, rig: Path, uploads: list,
                     log: list) -> Path:
    """A pane for a Spine source: the ORIGINAL json, atlas and page.

    Nothing is converted — the pane is the file the user brought, so a
    difference in the comparison is the conversion's, not the pane's.
    """
    pane = job / f"{index}-source-{_slug(rig.stem)}-spine"
    output = pane / "output"
    output.mkdir(parents=True, exist_ok=True)
    companions = [p for p in uploads
                  if p != rig and p.name.endswith(COMPANION_SUFFIXES)]
    atlas = next((p for p in companions if ".atlas" in p.name), None)
    # Only the pages this rig names travel with it: copying every uploaded
    # image would drag unrelated files into the pane and texture nothing.
    pages = _atlas_pages(atlas) if atlas else set()
    for companion in companions:
        if companion == atlas or companion.name in pages:
            (output / companion.name).write_bytes(companion.read_bytes())
    target = output / rig.name
    target.write_bytes(rig.read_bytes())
    if atlas is None:
        log.append("note: no .atlas beside the source JSON — the pane may render "
                   "untextured (send the atlas and its page image with the rig)")
    elif pages - {p.name for p in companions}:
        log.append("note: the atlas names page(s) that were not uploaded: "
                   + ", ".join(sorted(pages - {p.name for p in companions})))
    viewer_out.emit_viewer(
        str(pane / "index.html"), skeleton_json_path=str(target),
        skeleton_url=f"output/{target.name}",
        atlas_url=f"output/{atlas.name}" if atlas else None)
    return pane


def _atlas_pages(atlas: Path) -> set:
    """The page image names a .atlas declares.

    Page names sit at column 0; the region names below them are indented, so
    matching image suffixes on unindented lines picks the pages without
    parsing the whole format.
    """
    text = atlas.read_text(encoding="utf-8", errors="replace")
    return {line.strip() for line in text.splitlines()
            if line.strip() and not line[0].isspace()
            and Path(line.strip()).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")}


def _godot_pane(job: Path, index: int, rig: Path, name: str, godot_bin,
                log: list) -> Path | None:
    """A pane for a Godot source: the original scene, exported for the browser.

    Returns None when the export cannot run (no engine binary); the caller
    then falls back to the Spine leg rather than serving a pane that 404s.
    """
    pane = job / f"{index}-source-{_slug(name)}-godot"
    log.append("note: a native scene is packed exactly as it is — a demo that "
               "drives its rig from its own script or modifications may preview "
               "differently here than inside its own project")
    try:
        preview_scene(rig, pane, _slug(rig.stem), godot_bin=godot_bin)
    except ExportError as error:
        log.append(f"note: {error}; replaying this side through the Spine leg")
        return None
    return pane


def _proxy_pane(job: Path, index: int, role: str, rig: Path, source: str,
                name: str, godot_bin, log: list) -> Path:
    """A pane for a format with no browser runtime: the Spine leg of the rig."""
    pane = job / f"{index}-{role}-{_slug(name)}-{source}-via-{PROXY}"
    bundle.convert(str(rig), source, PROXY, str(pane), name=_slug(name),
                   godot_bin=godot_bin, step=log.append)
    return pane


def _copy_skelform_pane(job: Path, index: int, rig: Path, name: str) -> Path:
    """A pane for a SkelForm source: the uploaded archive, played as it is.

    The format's own web player reads the file, so this pane needs nothing
    else — no atlas, no pages, no conversion.
    """
    pane = job / f"{index}-source-{_slug(name)}-skelform"
    output = pane / "output"
    output.mkdir(parents=True, exist_ok=True)
    target = output / rig.name
    target.write_bytes(rig.read_bytes())
    skelform_viewer_out.emit_viewer(str(pane / "index.html"),
                                    f"output/{target.name}")
    return pane


def _source_pane(job: Path, rig: Path, fmt: str, uploads: list, name: str,
                 godot_bin, log: list) -> Path:
    if fmt == "spine":
        return _copy_spine_pane(job, 1, rig, uploads, log)
    if fmt == "skelform":
        return _copy_skelform_pane(job, 1, rig, name)
    if fmt == "godot":
        pane = _godot_pane(job, 1, rig, name, godot_bin, log)
        if pane is not None:
            return pane
        return _proxy_pane(job, 1, "source", rig, "godot", name, godot_bin, log)
    return _proxy_pane(job, 1, "source", rig, fmt, name, godot_bin, log)


def _target_pane(job: Path, index: int, rig: Path, fmt: str, target: str,
                 name: str, fps, godot_bin, log: list) -> Path:
    """What the conversion wrote, playable — or its Spine leg, labelled.

    A target with a browser shell (everything in ``TARGETS``) plays the file
    this conversion wrote. When the shell cannot be built — a Godot target with no
    engine binary to export with — the pane replays the *source* rig through
    the Spine leg instead, which still shows the rig on both sides and says so
    in its folder name.
    """
    pane = job / f"{index}-target-{target}"
    result = bundle.convert(str(rig), fmt, target, str(pane), name=_slug(name),
                            fps=fps, godot_bin=godot_bin, step=log.append)
    if result.previewable:
        return pane
    log.append(f"note: the {target} target wrote its files but no browser "
               "shell; replaying this side through the Spine leg")
    return _proxy_pane(job, index, "target", rig, fmt, name, godot_bin, log)


def delete_job(root: Path, payload: dict) -> dict:
    """Remove one job directory from the studio root.

    The name is a single path component that must already be a directory
    directly under the root, so a crafted name cannot reach anything else —
    and a symlinked job resolves outside the root, which the check catches.
    """
    name = str(payload.get("job") or "")
    if not name or name != Path(name).name or name.startswith("."):
        raise StudioError(f"{name!r} is not a job name")
    target = (root / name).resolve()
    if target.parent != root.resolve() or not target.is_dir():
        raise StudioError(f"no job {name!r} under the studio root")
    shutil.rmtree(target)
    # Deleting is destructive and irreversible, so it is written to the server
    # log by name: "why are my jobs gone" has to be answerable from the log.
    print(f"studio: removed job {name}", flush=True)
    return {"deleted": name}


def _jobs(root: Path) -> list:
    """Finished jobs, newest first, for the studio page."""
    found = []
    for page in root.glob("*/compare.html"):
        found.append((page.stat().st_mtime,
                      {"job": page.parent.name,
                       "url": f"/{page.parent.name}/compare.html",
                       "when": time.strftime("%Y-%m-%d %H:%M",
                                             time.localtime(page.stat().st_mtime))}))
    found.sort(key=lambda entry: entry[0], reverse=True)
    return [entry for _mtime, entry in found]


def convert_request(root: Path, payload: dict, godot_bin=None) -> dict:
    """One posted job: upload -> detect -> convert to EVERY other format.

    The comparison does not ask which output to build. One upload answers "how
    does each tool render this rig?" — the first pane plays the source as it
    arrived, and every pane after it plays one converted output — and the files
    for all of them are on disk either way.

    Raises :class:`StudioError` with a message the page shows as-is; a single
    target that fails is logged and left out, so one broken leg cannot take the
    whole comparison with it.
    """
    files = payload.get("files") or []
    if not files:
        raise StudioError("no files were sent")
    log: list = []
    job = root / _job_name(Path(str(files[0].get("name", "rig"))).stem)
    uploads = _write_uploads(job, files)
    log.append(f"job {job.name}: {len(uploads)} file(s) uploaded")

    found = detect.detect_rig([str(path) for path in uploads])
    if found is None:
        raise StudioError(
            f"no rig among the uploaded files ({detect.describe(str(uploads[0]))}) "
            "— a rig is a .tscn, a Spine .json (with its .atlas and page), or a .skf")
    rig_path, fmt = found
    if fmt not in registry.READERS:
        raise StudioError(f"{fmt} has no reader: the studio can read "
                          f"{', '.join(sorted(registry.READERS))}, and .tres is "
                          "write-only (use the CLI for that leg)")
    rig = Path(rig_path)
    log.append(f"detected {fmt}: {rig.name}")
    name = str(payload.get("name") or "").strip() or rig.stem
    fps = payload.get("fps")
    fps = float(fps) if fps else None

    log.append(f"--- source pane ({fmt}) ---")
    source = _source_pane(job, rig, fmt, uploads, name, godot_bin, log)
    panes = [source]
    converted = []
    index = 1
    for target in TARGETS:
        if target == fmt:
            # The source pane already IS this format: converting a rig to its
            # own format would produce a second copy of the same thing.
            continue
        index += 1
        log.append(f"--- {target} pane ---")
        try:
            pane = _target_pane(job, index, rig, fmt, target, name, fps,
                                godot_bin, log)
        except Exception as error:  # one leg failing is not the job failing
            log.append(f"note: the {target} conversion failed "
                       f"({type(error).__name__}: {error}) — this pane is left out")
            continue
        panes.append(pane)
        converted.append({"target": target, "pane": pane.name})

    log.append(f"--- compare: {' vs '.join(pane.name for pane in panes)} ---")
    page = compare_out.emit_compare(*panes)
    return {"job": job.name, "url": f"/{job.name}/{page.name}",
            "detected": fmt, "log": log, "source": source.name,
            "targets": converted, "panes": [pane.name for pane in panes]}


def _render_page(root: Path) -> bytes:
    targets = [{"name": name, "hint": TARGET_HINTS.get(name, ""),
                "frames": name == "skelform"} for name in TARGETS]
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("__TARGETS__", json.dumps(targets).replace("</", "<\\/"))
            .replace("__JOBS__", json.dumps(_jobs(root)).replace("</", "<\\/"))
            .replace("__MAX_UPLOAD__", str(MAX_UPLOAD)))
    return html.encode("utf-8")


CONTENT_TYPES = {"hud.css": "text/css; charset=utf-8",
                "hud.js": "text/javascript; charset=utf-8"}


def _render_pane(pane: Path) -> str | None:
    """The viewer for what a pane folder holds, rendered from the current code.

    Which viewer is not recorded anywhere: the pane's own contents say it. A
    written archive is SkelForm's, an engine page next to it is Godot's, a
    skeleton JSON is Spine's.
    """
    output = pane / "output"
    skf = next(iter(sorted(output.glob("*.skf"))), None)
    if skf is not None:
        return skelform_viewer_out.render_viewer(f"output/{skf.name}")
    engine = pane / "engine.index.html"
    if engine.is_file():
        page = engine.read_text(encoding="utf-8")
        return godot_preview.render_shell(godot_preview._extract_boot_config(page),
                                          godot_preview._extract_runtime_url(page))
    rig = next((p for p in sorted(output.glob("*.json"))
                if detect.detect_format(p) == "spine"), None)
    if rig is None:
        return None
    # `.atlas` or `.atlas.txt` — the same rule the pane's own copy uses, or a
    # rig whose atlas carries the text extension renders untextured.
    atlas = next((p for p in sorted(output.iterdir()) if ".atlas" in p.name), None)
    return viewer_out.render_viewer(
        skeleton_json_path=str(rig), skeleton_url=f"output/{rig.name}",
        atlas_url=f"output/{atlas.name}" if atlas else None)


def _live_document(root: Path, route: str) -> tuple[bytes, str] | None:
    """(body, content type) for a job's VIEW documents, or None.

    A job folder holds two different things: what the conversion wrote — the
    artifact, which stays frozen — and the documents that play it, which are
    chrome. Chrome follows the code, so it is rendered on every request and
    never served as a snapshot: a browser refresh then shows the current
    viewer without re-converting anything. Standalone bundles (the CLI's
    output, served by any static server) keep the written copies, which is
    what makes them self-contained.
    """
    parts = [p for p in route.split("/") if p]
    if len(parts) < 2:
        return None
    job, rest = root / parts[0], parts[1:]
    if not (job / "upload").is_dir():
        return None
    name = rest[-1]
    if name not in ("index.html", "compare.html") and name not in hud_assets.ASSETS:
        return None
    _reload_src()
    if name in hud_assets.ASSETS:
        return hud_assets.read(name), CONTENT_TYPES[name]
    if name == "compare.html":
        panes = sorted(p for p in job.iterdir()
                       if p.is_dir() and p.name[:1].isdigit())
        if len(panes) < 2:
            return None
        return (compare_out.render_compare(*panes).encode("utf-8"),
                "text/html; charset=utf-8")
    pane = job / rest[0] if len(rest) > 1 else None
    if pane is None:
        return None
    html = _render_pane(pane)
    if html is None:
        return None
    return html.encode("utf-8"), "text/html; charset=utf-8"


class StudioHandler(NoCacheHandler):
    """The dev-server handler plus the studio API routes."""

    root: Path = DEFAULT_ROOT
    godot_bin: str | None = None

    def end_headers(self) -> None:  # noqa: N802 (http.server's interface)
        # A job is re-converted under the same name every time it is run, and a
        # pane fetches its .skf and pages by path. Without this the browser
        # happily serves the previous run's bytes, so a fixed conversion still
        # renders the old picture — the most confusing failure there is.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (http.server's interface)
        route = self.path.split("?")[0]
        if route in ("/", "/index.html"):
            body = _render_page(self.root)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Inside a job, the documents that PLAY the conversion are rendered
        # now; only what the conversion wrote is served as it was written.
        live = _live_document(self.root, route)
        if live is not None:
            body, content_type = live
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        route = self.path.split("?")[0]
        if route == "/api/convert":
            # Hot reload: a running studio always converts with the CURRENT
            # source — the converter modules are reimported per request, so an
            # edit lands on the next conversion without a server restart.
            # (Changes to studio.py itself still need a restart: the running
            # handler keeps the old code.)
            _reload_src()
        route = self.path.split("?")[0]
        if route not in ("/api/convert", "/api/delete"):
            self._json(404, {"error": f"no route {self.path}"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "upload too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as error:
            self._json(400, {"error": f"bad JSON body: {error}"})
            return
        try:
            if route == "/api/convert":
                self._json(200, convert_request(self.root, payload,
                                                godot_bin=self.godot_bin))
            else:
                self._json(200, delete_job(self.root, payload))
        except StudioError as error:
            self._json(400, {"error": str(error)})
        except Exception as error:  # the page shows this verbatim
            self._json(500, {"error": f"{type(error).__name__}: {error}"})


def stop(port: int) -> int:
    """Stop the studio serving ``port`` — a leftover with no terminal to Ctrl+C.

    Only a process that IS this module is killed: a port can be held by
    anything, and shooting down somebody's other dev server because it happens
    to sit on 8643 is not this tool's job.
    """
    try:
        listing = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                                 capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"error: cannot look up port {port} ({error}) — is lsof installed?",
              file=sys.stderr)
        return 1
    pids = [line.strip() for line in listing.stdout.split() if line.strip().isdigit()]
    if not pids:
        print(f"port {port} is free — nothing to stop")
        return 0
    ours = []
    for pid in pids:
        command = subprocess.run(["ps", "-p", pid, "-o", "command="],
                                 capture_output=True, text=True).stdout.strip()
        if "src.studio" in command:
            ours.append((pid, command))
        else:
            print(f"port {port} is held by something that is not a studio — "
                  f"not stopping pid {pid}: {command}\n"
                  f"    stop that process yourself, or serve the studio "
                  f"elsewhere: python3 -m src.studio {port + 1}",
                  file=sys.stderr)
    if not ours:
        return 1
    for pid, command in ours:
        os.kill(int(pid), signal.SIGTERM)
        print(f"stopped pid {pid} on port {port}: {command}")
    return 0


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = DEFAULT_ROOT
    godot_bin = None
    port = None
    stopping = False
    while argv:
        item = argv.pop(0)
        if item == "--stop":
            stopping = True
        elif item == "--root":
            root = Path(argv.pop(0))
        elif item == "--godot":
            godot_bin = argv.pop(0)
        elif item.isdigit():
            port = int(item)
        else:
            print("usage: python3 -m src.studio [PORT] [--root DIR] [--godot BIN] "
                  "[--stop]", file=sys.stderr)
            return 2
    if stopping:
        return stop(port or DEFAULT_PORT)
    port = port or DEFAULT_PORT
    root.mkdir(parents=True, exist_ok=True)
    bound = type("BoundStudioHandler", (StudioHandler,),
                 {"root": root.resolve(), "godot_bin": godot_bin})
    try:
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), partial(bound, directory=str(root)))
    except OSError as error:
        # The port is the whole address of this tool; a traceback here says
        # nothing the user needs, and the make target is not always in play.
        print(f"error: cannot serve on port {port} ({error}) — another studio "
              f"or another process holds it; stop it or pass another port",
              file=sys.stderr)
        return 1
    print(f"skeleton-converter studio on http://localhost:{port}/ "
          f"(jobs under {root}, no-cache)", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
