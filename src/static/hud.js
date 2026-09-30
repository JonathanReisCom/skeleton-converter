/* Shared HUD for the converter's browser viewers — see viewer-hud.css.
 *
 * Classic script (no modules, no build): a pane is a document served from an
 * emitted folder, and it must work with nothing but these two files beside its
 * index.html. Everything here is presentation and arithmetic; every viewer
 * keeps its own runtime glue (load, apply, render), because the runtimes have
 * nothing in common.
 *
 * A viewer that finds `window.hud` undefined is running from a bundle built
 * before this file existed — it should say so, not render an empty pane.
 */
window.hud = (() => {
  "use strict";

  // The margin both panes apply to the same box. Two viewers that disagree by
  // 0.001 frame identical geometry at different sizes, which is what the
  // comparison is for — so it lives here, once, and fitScale is the only
  // supported way to ask for it.
  const MARGIN = 0.85;

  // Inside a compare shell the PARENT owns the controls — one track bar, one
  // master clock, one freeze — so a pane's own chrome would be a second set of
  // buttons driving the same thing. A pane can tell it is embedded (its window
  // is not the top one) and hides its chrome; opened standalone, which is how a
  // bundle's index.html is meant to work, it keeps it. The nodes stay in the
  // DOM either way: the shell reads a pane's tracks from there and clicks them
  // to drive it.
  const EMBEDDED = window.self !== window.top;
  if (EMBEDDED) document.documentElement.classList.add("embedded");

  // CSS-pixel scale for a box that must fit in width x height with MARGIN to
  // spare. Spine's renderer scales the viewport DOWN (`zoom`), so its caller
  // passes `1 / (scale * dpr)` to the camera; SkelForm multiplies by `scale`
  // directly. Same number, different camera conventions — the convention stays
  // in the viewer, the number comes from here.
  function fitScale(spanX, spanY, width, height) {
    const sx = Math.max(spanX, 1e-3) / Math.max(width, 1);
    const sy = Math.max(spanY, 1e-3) / Math.max(height, 1);
    return MARGIN / Math.max(sx, sy);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Builds the pane chrome and returns its nodes. Everything else in this file
  // takes that object, so a viewer never queries the DOM for its own HUD.
  function mount({ title, note }) {
    const box = el("div", "hud");
    const titleRow = el("div", "title", "");
    const titleTag = el("b", null, title || "");
    titleRow.appendChild(titleTag);
    const noteRow = el("div", "note", note || "");
    const tracks = el("div", "tracks");
    const error = el("div", "error");
    box.append(titleRow, noteRow, tracks, error);
    document.body.appendChild(box);
    const nodes = { box, title: titleTag, note: noteRow, tracks, error };
    // The pane's kind is reported the same way in every viewer, so a compare
    // shell can type a pane by what it says it is when the folder name lies.
    box.dataset.kind = title || "";
    return nodes;
  }

  // The line under the title: the file this pane is playing, the clock, rig
  // counts — whatever that runtime can report.
  function setNote(nodes, text) {
    nodes.note.textContent = text || "";
  }

  function setTitle(nodes, text) {
    nodes.title.textContent = text || "";
  }

  // Rebuilds the track buttons. `names` is the animation list the runtime
  // actually loaded. Returns the buttons so a caller can drive them later.
  function tracks(nodes, names, onPick, active) {
    nodes.tracks.textContent = "";
    const buttons = new Map();
    (names || []).forEach((name) => {
      const button = el("button", null, name);
      button.onclick = () => {
        mark(nodes, name, buttons);
        onPick(name);
      };
      nodes.tracks.appendChild(button);
      buttons.set(name, button);
    });
    if (active !== undefined) mark(nodes, active, buttons);
    return buttons;
  }

  function mark(nodes, name, buttons) {
    const map = buttons || new Map([...nodes.tracks.querySelectorAll("button")]
      .map((b) => [b.textContent, b]));
    map.forEach((button, key) => button.classList.toggle("active", key === name));
  }

  // Reports a failure in the pane. Idempotent: the first reason wins, because
  // the first one is the specific one (a rig that shipped without an atlas
  // must not be buried under a later "atlas failed to load").
  function fail(nodes, message) {
    if (!nodes.error.textContent) nodes.error.textContent = message;
    nodes.error.hidden = false;
  }

  // Errors that happen outside any promise chain would otherwise disappear into
  // the console of an iframe nobody is looking at.
  function watchErrors(nodes) {
    window.addEventListener("error", (event) => {
      fail(nodes, String(event.message || event.error || "error"));
    });
    window.addEventListener("unhandledrejection", (event) => {
      const reason = event.reason;
      fail(nodes, String((reason && reason.message) || reason || "rejection"));
    });
  }

  // Attachment explorer: a toggle by the pane's top-right corner and a panel
  // under it. Rows come from the viewer, because only it knows how its runtime
  // lists a slot's attachments.
  function explorer(label) {
    const toggle = el("button", "hud-toggle", label || "attachments");
    toggle.title = "Toggle the attachment explorer";
    toggle.hidden = true;
    const panel = el("div", "hud-panel");
    panel.hidden = true;
    toggle.onclick = () => {
      panel.hidden = !panel.hidden;
      toggle.classList.toggle("active", !panel.hidden);
    };
    document.body.append(toggle, panel);
    return { toggle, panel };
  }

  // rows: [{ name, options: [string], value: string, onPick(value) }]
  function explorerRows(nodes, rows) {
    nodes.panel.textContent = "";
    (rows || []).forEach((row) => {
      const line = el("div", "row");
      const name = el("span", "row-name", row.name);
      name.title = row.name;
      const select = document.createElement("select");
      const none = el("option", null, "(none)");
      none.value = "";  // sem isto o "nenhum" vale a string "(none)"
      select.appendChild(none);
      (row.options || []).forEach((option) => {
        select.appendChild(el("option", null, option));
      });
      select.value = row.value || "";
      select.onchange = () => row.onPick(select.value);
      line.append(name, select);
      nodes.panel.appendChild(line);
    });
    nodes.toggle.hidden = false;
  }

  return { MARGIN, EMBEDDED, fitScale, mount, setTitle, setNote, tracks, mark,
           fail, watchErrors, explorer, explorerRows };
})();
