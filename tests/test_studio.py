"""The studio's HTTP contract: upload -> detect -> convert -> compare page.

These run the real handler over a loopback socket, because routing, the JSON
body and the job layout are exactly what the browser depends on — a handler
called in-process would not exercise the parts that break silently (a missing
route, a wrong content type, a pane the page cannot reach).

The rig is synthesized (``tests/ci_rig.py``), and the target is SkelForm: that
leg needs no Godot binary, so the test stays runnable in CI.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from src import studio
from tests.ci_rig import PAGE, STEM, write_spine_export


def _upload(directory: Path, *names: str) -> list:
    """The payload the browser builds: every chosen file, base64, by name."""
    files = []
    for name in names:
        raw = (directory / name).read_bytes()
        files.append({"name": name, "data": base64.b64encode(raw).decode("ascii")})
    return files


def _post(url: str, payload: dict, route: str = "/api/convert") -> tuple:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url + route, data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _get(url: str) -> tuple:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.status, response.read().decode("utf-8")


@pytest.fixture
def studio_server(tmp_path):
    """The studio on a loopback port, rooted in the test's tmp_path."""
    root = tmp_path / "jobs"
    root.mkdir()
    bound = type("BoundStudioHandler", (studio.StudioHandler,),
                 {"root": root, "godot_bin": None})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                partial(bound, directory=str(root)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_an_unknown_skin_is_refused_with_the_list(tmp_path):
    """A typo must not convert under a different variant.

    A weapon that lives in its own skin is neither drawn nor animated unless
    that skin is named, so silently falling back to `default` (or to whatever
    the first skin is) hands back a rig the user did not ask for — with no
    sword in it and no sign that anything was wrong.
    """
    rig = {
        "skeleton": {"spine": "4.2.33", "width": 64, "height": 64},
        "bones": [{"name": "root", "length": 10.0}],
        "slots": [{"name": "weapon", "bone": "root", "attachment": "sword"}],
        "skins": [{"name": "default"}, {"name": "weapon/sword"},
                  {"name": "weapon/morningstar"}],
        "animations": {},
    }
    payload = {
        "files": [{"name": "rig.json",
                   "data": base64.b64encode(json.dumps(rig).encode()).decode()}],
        "skin": "weapon/sord",
    }
    root = tmp_path / "jobs"
    root.mkdir()
    with pytest.raises(studio.StudioError, match="no skin named"):
        studio.convert_request(root, payload)


def test_page_lists_the_outputs_it_converts_to(tmp_path):
    """One upload builds every output, so the page offers no target to pick."""
    root = tmp_path / "jobs"
    root.mkdir()
    html = studio._render_page(root).decode("utf-8")

    assert "skeleton" in html and "studio" in html
    for target in studio.TARGETS:
        assert f'"name": "{target}"' in html
    # .tres carries animations, not a scene: nothing to compare, so it is not
    # built here even though the CLI writes it.
    assert '"tres"' not in html
    assert '"frames": true' in html, "the frame-based target must know about fps"
    # The page offers no target to pick: every output is listed (asserted
    # above) and there is no <select> to choose one from.
    assert 'id="target"' not in html


def test_upload_converts_into_every_other_format(studio_server, tmp_path):
    """One spine upload: the source pane, then a pane for every other format."""
    url, root = studio_server
    source = tmp_path / "rig"
    write_spine_export(source)

    # No target in the payload at all: the studio converts into everything.
    status, data = _post(url, {"files": _upload(source, f"{STEM}.json",
                                                f"{STEM}.atlas", PAGE)})
    assert status == 200, data
    assert data["detected"] == "spine"
    assert data["url"].endswith("/compare.html")
    assert any("detected spine" in line for line in data["log"])

    job = root / data["job"]
    # The source pane plays the original: the rig's own json, atlas and page.
    assert (job / data["source"] / "index.html").is_file()
    assert sorted(p.name for p in (job / data["source"] / "output").iterdir()) \
        == sorted([f"{STEM}.json", f"{STEM}.atlas", PAGE])
    # One pane per OTHER format, and never a copy of the source's own.
    assert {entry["target"] for entry in data["targets"]} == {"godot", "skelform"}
    # Source first, then the converted outputs, in the order the shell lists.
    assert data["panes"][0] == data["source"]
    assert len(data["panes"]) == 1 + len(data["targets"])
    for pane in data["panes"]:
        assert (job / pane / "index.html").is_file(), pane

    # SkelForm ships a web player, so its pane plays the written archive
    # through its own runtime — no replay through another format.
    skelform = next(e["pane"] for e in data["targets"] if e["target"] == "skelform")
    assert (job / skelform / "output" / f"{STEM}.skf").is_file()
    pane_html = (job / skelform / "index.html").read_text(encoding="utf-8")
    assert f"output/{STEM}.skf" in pane_html
    assert "cdn.jsdelivr.net/gh/Retropaint/skelform-js@" in pane_html, \
        "the pane must load a pinned runtime, not a branch"
    assert "cdn.jsdelivr.net/gh/Retropaint/skelform-web-player@" in pane_html

    # The shell is SERVED, not read off disk: the studio renders it per request
    # so it can follow the code without a reconversion.
    status, page = _get(url + data["url"])
    assert status == 200 and "compare" in page
    order = [page.index(pane) for pane in data["panes"]]
    assert order == sorted(order), "the shell must keep the source-first order"
    assert page.count('"kind": "skelform"') == 1

    # And the page now lists the job, so a previous comparison is one click away.
    _status, listing = _get(url + "/")
    assert data["job"] in listing and data["url"] in listing


def test_a_rig_without_its_atlas_still_compares_as_spine(studio_server, tmp_path):
    """JSON-only upload: the source pane is still a Spine pane.

    Its atlas never arrived, so it animates untextured and says why; typing it
    "godot" instead would drive it with the wrong protocol and leave the shell
    on "loading…" while the other pane renders fine.
    """
    url, root = studio_server
    source = tmp_path / "rig"
    write_spine_export(source)

    status, data = _post(url, {"files": _upload(source, f"{STEM}.json")})
    assert status == 200, data
    assert data["detected"] == "spine"
    assert any("no .atlas beside the source JSON" in line for line in data["log"]), \
        "a missing atlas has to be said out loud, not discovered in a 404"

    status, page = _get(url + data["url"])
    assert status == 200
    panels = json.loads(re.search(r"const PANES = (\[.*?\]);", page, re.S).group(1))
    assert panels[0]["kind"] == "spine", \
        "the source pane is typed from the rig itself, never from its neighbours"
    assert panels[0]["label"] == data["source"]


def test_uploaded_names_stay_inside_the_job_folder(studio_server, tmp_path):
    """A crafted file name must not write outside the upload folder."""
    url, root = studio_server
    source = tmp_path / "rig"
    write_spine_export(source)
    files = _upload(source, f"{STEM}.json")
    files.append({"name": "../../escape.png", "data": base64.b64encode(b"x").decode()})

    status, data = _post(url, {"files": files, "target": "spine"})
    assert status == 200, data
    outside = [p for p in root.rglob("escape.png")
               if "upload" not in p.parts]
    assert not outside, outside
    assert (root / data["job"] / "upload" / "escape.png").is_file()


def test_unrecognized_upload_is_refused_with_the_reason(studio_server, tmp_path):
    """A companion file alone is not a rig, and the page must say why."""
    url, _root = studio_server
    notes = tmp_path / "notes.txt"
    notes.write_text("not a rig", encoding="utf-8")

    status, data = _post(url, {"files": _upload(tmp_path, "notes.txt"),
                               "target": "spine"})
    assert status == 400
    assert "no rig" in data["error"] and "notes.txt" in data["error"]


def test_a_job_can_be_removed_from_the_page(studio_server, tmp_path):
    """The remove button's route: one job goes, the rest stay."""
    url, root = studio_server
    source = tmp_path / "rig"
    write_spine_export(source)
    status, data = _post(url, {"files": _upload(source, f"{STEM}.json",
                                                f"{STEM}.atlas", PAGE),
                               "target": "skelform"})
    assert status == 200, data
    keep = root / data["job"]
    assert keep.is_dir()

    # A second job, so "removes one" is not the same as "removes all".
    status, other = _post(url, {"files": _upload(source, f"{STEM}.json",
                                                 f"{STEM}.atlas", PAGE),
                                "target": "spine"})
    assert status == 200, other
    survivor = root / other["job"]

    _status, listing = _get(url + "/")
    assert listing.count("/api/delete") == 1, "the page must wire one route"

    status, deleted = _post(url, {"job": data["job"]}, route="/api/delete")
    assert status == 200 and deleted == {"deleted": data["job"]}
    assert not keep.exists() and survivor.is_dir()
    # Deleting one job must never take a sibling: the route answers per name.
    status, again = _post(url, {"job": data["job"]}, route="/api/delete")
    assert status == 400 and "no job" in again["error"]


def test_delete_refuses_anything_that_is_not_a_job(studio_server, tmp_path):
    """A crafted or missing name must not reach outside the studio root."""
    url, root = studio_server
    outside = tmp_path / "outside"
    outside.mkdir()

    for payload in ({"job": "../outside"}, {"job": ""}, {"job": "nope"},
                    {"job": str(outside)}):
        status, data = _post(url, payload, route="/api/delete")
        assert status == 400, (payload, status, data)
    assert outside.is_dir(), "nothing outside the root may be touched"


def test_a_posted_target_no_longer_changes_anything(studio_server, tmp_path):
    """The field is gone; a client still sending one gets the full set anyway."""
    url, _root = studio_server
    source = tmp_path / "rig"
    write_spine_export(source)

    status, data = _post(url, {"files": _upload(source, f"{STEM}.json"),
                               "target": "dragonbones"})
    assert status == 200, data
    assert {entry["target"] for entry in data["targets"]} == {"godot", "skelform"}


def test_a_refresh_serves_the_current_viewer_not_the_stored_copy(studio_server,
                                                                tmp_path):
    """The conversion is the artifact; the document that plays it is chrome.

    A job's index.html is written once and a browser refresh re-reads the
    server, so a viewer change has to reach an old job without re-converting.
    The stored copies are replaced with garbage here: what the server returns
    must come from the current templates and the current static files.
    """
    url, root = studio_server
    source = tmp_path / "rig"
    write_spine_export(source)
    status, data = _post(url, {"files": _upload(source, f"{STEM}.json",
                                                f"{STEM}.atlas", PAGE)})
    assert status == 200, data
    job = root / data["job"]
    panes = list(data["panes"])
    assert len(panes) >= 2
    for pane in panes:
        (job / pane / "index.html").write_text("STALE SNAPSHOT")

    for pane in panes:
        status, page = _get(f"{url}/{data['job']}/{pane}/index.html")
        assert status == 200, pane
        assert "STALE SNAPSHOT" not in page, pane
        assert 'src="hud.js"' in page, f"{pane}: viewer from the current template"

    status, compare = _get(url + data["url"])
    assert status == 200
    assert "STALE SNAPSHOT" not in compare
    assert '"kind": "spine"' in compare, "the compare shell is rendered too"

    status, css = _get(f"{url}/{data['job']}/{panes[0]}/hud.css")
    assert status == 200
    assert ".embedded" in css, "chrome assets are served live as well"


def test_the_live_viewer_finds_an_atlas_txt(tmp_path):
    """`.atlas.txt` is a supported spelling (Git-hosted rigs ship it), so the
    live render must not look only for `*.atlas`: a pane rendered without its
    atlas draws nothing and never publishes its slots, which reads as "the
    attachment controls are broken".
    """
    pane = tmp_path / "1-source-rig-spine"
    output = pane / "output"
    output.mkdir(parents=True)
    (output / "rig.json").write_text('{"skeleton": {"spine": "4.3.26"}, "bones": []}',
                                     encoding="utf-8")
    (output / "rig.atlas.txt").write_text("rig.png\nsize: 8, 8\n", encoding="utf-8")
    html = studio._render_pane(pane)
    assert html is not None
    assert "output/rig.atlas.txt" in html, "the atlas has to reach the viewer"
