"""Emit a reusable HTML viewer shell that plays a Spine skeleton.

The HTML is a thin, generic shell: it loads spine-webgl from the CDN and reads
three sibling files — <name>.json, <name>.atlas, and the image the atlas
declares. No assets are embedded; regenerate nothing when the assets change.

Requires python3 stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

# Pinned to the 4.2 line on purpose: the shell drives the SpineCanvas *app* API
# (app.loadAssets / app.renderer / app.assetManager), which 4.3 removed —
# SpineCanvas.prototype there exposes only clear() and dispose(). Bumping this
# to a 4.3.x build loads the skeleton and reports no error, then renders zero
# pixels, so the breakage is silent. Unrelated to the data version written by
# out_spine.py (4.3.26): a 4.2 runtime reads 4.3 data fine.
SPINE_WEBGL_VERSION = "4.2.120"
RUNTIME_URL = f"https://unpkg.com/@esotericsoftware/spine-webgl@{SPINE_WEBGL_VERSION}/dist/iife/spine-webgl.js"

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>skeleton-converter viewer</title>
<style>
  html, body { margin: 0; height: 100%; background: #1e2129; overflow: hidden; }
  canvas { width: 100%; height: 100%; display: block; touch-action: none; }
  #hud {
    position: fixed; top: 12px; left: 12px; z-index: 2;
    font: 13px/1.5 system-ui, sans-serif; color: #dfe3ea;
    background: rgba(0,0,0,.55); border-radius: 8px; padding: 10px 12px;
  }
  #hud button {
    font: inherit; margin-right: 6px; margin-bottom: 4px;
    background: #3a4152; color: #dfe3ea; border: 0; border-radius: 6px;
    padding: 4px 10px; cursor: pointer;
  }
  #hud button.active { background: #4f8cff; color: #fff; }
  #error { color: #ff7b7b; white-space: pre-wrap; }
</style>
</head>
<body>
<canvas id="canvas"></canvas>
<div id="hud">
  <div><strong id="name">…</strong> — click a track to play</div>
  <div id="tracks"></div>
  <div id="error"></div>
</div>
<script src="__RUNTIME_URL__"></script>
<script>
"use strict";
// Skeleton: ?skeleton=<name>.json wins; otherwise the default baked into this
// shell at generation time. The first animation autoplays.
const SKELETON_URL = new URLSearchParams(location.search).get("skeleton")
  || "__DEFAULT_SKELETON__";
const ATLAS_URL = SKELETON_URL.replace(/\\.json$/, ".atlas");
const FIRST_ANIMATION = "__FIRST_ANIMATION__";

const canvas = document.getElementById("canvas");
const errorBox = document.getElementById("error");
const tracksBox = document.getElementById("tracks");
const nameBox = document.getElementById("name");
let state = null, skeleton = null, activeButton = null;

window.addEventListener("error", e => {
  errorBox.textContent += (e.message || e.error) + "\\n";
});

function showTracks(animStateData) {
  const names = animStateData.skeletonData.animations.map(a => a.name);
  names.forEach(name => {
    const b = document.createElement("button");
    b.textContent = name;
    b.onclick = () => setAnimation(name, b);
    tracksBox.appendChild(b);
    if (name === FIRST_ANIMATION) setAnimation(name, b);
  });
}

function setAnimation(name, button) {
  if (activeButton) activeButton.classList.remove("active");
  if (button) button.classList.add("active");
  activeButton = button;
  state.setAnimation(0, name, true);
}

new spine.SpineCanvas(canvas, {
  pathPrefix: "",
  webglConfig: { alpha: true },
  app: {
    loadAssets: (app) => {
      if (!SKELETON_URL || !SKELETON_URL.endsWith(".json")) {
        errorBox.textContent =
          "No skeleton found. Open with ?skeleton=<name>.json";
        return;
      }
      app.assetManager.loadTextureAtlas(ATLAS_URL);
      app.assetManager.loadJson(SKELETON_URL);
    },
    initialize: (app) => {
      const assetManager = app.assetManager;
      const atlas = assetManager.get(ATLAS_URL);
      const json = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas));
      skeleton = new spine.Skeleton(json.readSkeletonData(assetManager.get(SKELETON_URL)));
      nameBox.textContent = SKELETON_URL;

      state = new spine.AnimationState(new spine.AnimationStateData(skeleton.data));
      state.apply(skeleton);
      skeleton.update(0);
      skeleton.updateWorldTransform(spine.Physics.update);
      // Debug handles: the verification rule in AGENTS.md samples these from a
      // running page to prove the rig animates. Keep the names stable.
      window.__skeleton = skeleton;
      window.__state = state;
      window.__renderer = app.renderer;

      const camera = app.renderer.camera;
      // Frame the skeleton from its real setup-pose bounds. The ortho camera
      // multiplies the viewport by zoom; the max ratio fits both axes with margin.
      const offset = new spine.Vector2(), size = new spine.Vector2();
      skeleton.getBounds(offset, size);
      camera.position.set(offset.x + size.x / 2, offset.y + size.y / 2, 0);
      camera.zoom = Math.max(size.x / canvas.clientWidth, size.y / canvas.clientHeight) * 1.2;

      showTracks(state.data);
    },
    update: (app, delta) => {
      if (state) {
        state.update(delta);
        state.apply(skeleton);
      }
      if (skeleton) {
        // Skeleton.update(delta) only advances the clock (this.time += delta).
        // The pose stays frozen in the world until updateWorldTransform runs,
        // so without this the rig renders a static setup pose while the
        // animation state happily advances.
        skeleton.update(delta);
        skeleton.updateWorldTransform(spine.Physics.update);
      }
    },
    render: (app) => {
      if (!skeleton) return;
      app.renderer.resize(spine.ResizeMode.Expand);
      const gl = app.renderer.context.gl;
      gl.clearColor(0.118, 0.129, 0.161, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      app.renderer.begin();
      app.renderer.drawSkeleton(skeleton, true);
      app.renderer.end();
    },
    error: (app, errors) => {
      errorBox.textContent = Object.entries(errors)
        .map(([path, msg]) => path + ": " + msg).join("\\n");
    }
  }
});
</script>
</body>
</html>
"""


def emit_viewer(output_path: str, skeleton_json_path: str | None = None) -> str:
    """Write the reusable viewer shell.

    The shell loads ``<name>.json`` + ``<name>.atlas`` + the atlas image from
    its own directory (or a skeleton passed via ?skeleton= query). Nothing is
    embedded; when assets change, just refresh the page.
    """
    if skeleton_json_path:
        skeleton_json = json.loads(Path(skeleton_json_path).read_text(encoding="utf-8"))
        animations = list(skeleton_json.get("animations", {}).keys())
        first = animations[0] if animations else ""
        default_skeleton = Path(skeleton_json_path).name
    else:
        first = ""
        default_skeleton = ""
    html = (
        TEMPLATE
        .replace("__RUNTIME_URL__", RUNTIME_URL)
        .replace("__FIRST_ANIMATION__", first)
        .replace("__DEFAULT_SKELETON__", default_skeleton)
    )
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
