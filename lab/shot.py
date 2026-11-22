#!/usr/bin/env python3
"""shot.py — a real picture of a workflow, taken in ComfyUI itself.

Opens the ComfyUI page in a headless browser, loads the workflow file the way
a user would, fits the whole graph into view, hides the menus and saves a
screenshot. What you get is what someone sees after opening the file: the
groups, the titled inputs and the notes with their text.

    python shot.py http://127.0.0.1:8188 workflow.json workflow.png

It refuses to save a picture in which ComfyUI would mark a node red (a model
file that is not installed), because that reads as a broken workflow.

Needs the `playwright` package with its Chromium (`playwright install chromium`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HIDE = """
.comfyui-body-top, .comfyui-body-left, .comfyui-body-right, .comfyui-body-bottom,
.side-tool-bar-container, .comfy-menu, .comfyui-menu, .p-toast, .p-dialog-mask, .graph-canvas-panel,
.litegraph.litecontextmenu, .comfy-missing-nodes, .actionbar, .actionbar-container, .minimap-main-container,
.p-splitter-gutter, .workflow-tabs-container, .bottom-panel, .selection-toolbox, .p-toolbar,
[class*="graph-canvas-menu"], [class*="toolbox"], [data-testid="graph-canvas-menu"] { display: none !important; }
"""
BOUNDS = """
const app = window.app || (window.comfyAPI && window.comfyAPI.app && window.comfyAPI.app.app);
const g = app.graph;
let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
const take = (x, y, w, h) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x + w); y1 = Math.max(y1, y + h); };
for (const n of (g._nodes || g.nodes || [])) take(n.pos[0], n.pos[1] - 30, n.size[0], n.size[1] + 30);
for (const gr of (g._groups || g.groups || [])) {
  const b = gr._bounding || gr.bounding || [gr.pos[0], gr.pos[1], gr.size[0], gr.size[1]];
  take(b[0], b[1], b[2], b[3]);
}
"""
LOAD = """
async (wf) => {
  const app0 = window.app || (window.comfyAPI && window.comfyAPI.app && window.comfyAPI.app.app);
  if (!app0) return {error: "ComfyUI app object not found on the page"};
  await app0.loadGraphData(wf, true, true);
  await new Promise(r => setTimeout(r, 1500));
  """ + BOUNDS + """
  if (!isFinite(x0)) return {error: "the workflow has no nodes"};
  // ComfyUI draws a node red when a file it selects is not installed, or the node type is
  // unknown. Find those before taking the picture: a red graph must never be saved.
  const red = [];
  const walk = (graph) => {
    for (const n of (graph._nodes || graph.nodes || [])) {
      if (n.has_errors || n.type === "MissingNode" || (n.constructor && n.constructor.type === undefined && !n.subgraph && !n.isSubgraphNode))
        ;
      for (const w of (n.widgets || [])) {
        const vals = w.options && w.options.values;
        if (w.type === "combo" && Array.isArray(vals) && vals.length && typeof w.value === "string" && !vals.includes(w.value))
          red.push((n.title || n.type) + ": " + w.value);
      }
      if (n.subgraph) walk(n.subgraph);
    }
  };
  walk(g);
  const unknown = (app.graph.extra && app.graph.extra.missing_nodes) || [];
  return {nodes: (g._nodes || g.nodes || []).length, w: x1 - x0, h: y1 - y0, red: red.concat(unknown)};
}
"""
FIT = """
async (pad) => {
  """ + BOUNDS + """
  // Only the graph belongs in the picture: drop pop-up messages, hide the editor's own
  // buttons (anything that is not part of a node's widget) and the frame-rate readout.
  document.querySelectorAll('.p-toast, .p-toast-message, [role="alert"], [role="status"], [role="dialog"]').forEach(e => e.remove());
  document.querySelectorAll('button, [role="button"], .p-button, .p-selectbutton').forEach(e => {
    if (!e.closest('.dom-widget')) e.style.visibility = 'hidden';
  });
  const c = app.canvas, W = window.innerWidth, H = window.innerHeight;
  c.show_info = false;
  const scale = Math.min((W - 2 * pad) / (x1 - x0), (H - 2 * pad) / (y1 - y0));
  c.ds.scale = scale;
  c.ds.offset[0] = -x0 + (W / scale - (x1 - x0)) / 2;
  c.ds.offset[1] = -y0 + (H / scale - (y1 - y0)) / 2;
  c.setDirty(true, true); c.draw(true, true);
  await new Promise(r => setTimeout(r, 1500));
  return scale;
}
"""


def shoot(url: str, workflow: Path, out: Path, zoom: float = 0.6, max_px: int = 7000,
          allow_red: bool = False) -> dict:
    """Screenshot the whole graph. ``zoom`` is how large nodes are drawn (1 = as in
    the editor at 100%); a very large graph is drawn smaller to stay under ``max_px``."""
    from playwright.sync_api import sync_playwright
    wf = json.loads(workflow.read_text(encoding="utf-8"))
    pad = 40
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1920, "height": 1200})
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("() => (window.app || (window.comfyAPI && window.comfyAPI.app && window.comfyAPI.app.app))"
                               " && document.querySelector('canvas')", timeout=90000)
        page.wait_for_timeout(2500)
        page.add_style_tag(content=HIDE)
        info = page.evaluate(LOAD, wf)
        if info.get("error"):
            browser.close()
            raise RuntimeError(info["error"])
        if info.get("red") and not allow_red:
            browser.close()
            raise RuntimeError("ComfyUI would draw these nodes red because the file is not installed here: "
                               + "; ".join(info["red"][:12]) + ". Take the picture while the models are on disk.")
        zoom = min(zoom, (max_px - 2 * pad) / info["w"], (max_px - 2 * pad) / info["h"])
        width = max(800, int(info["w"] * zoom) + 2 * pad)
        height = max(500, int(info["h"] * zoom) + 2 * pad)
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(800)
        info["scale"] = round(page.evaluate(FIT, pad), 3)
        info["size"] = [width, height]
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        page.screenshot(path=str(out), full_page=False)
        browser.close()
    return info


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    try:
        print(json.dumps(shoot(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]),
                               allow_red="--allow-red" in sys.argv)))
    except RuntimeError as e:
        sys.exit(f"No picture saved: {e}")
