"""The scene preview document: runtime, fonts and scene code in one network-sealed page for a sandboxed iframe's srcdoc."""

import base64
import functools
import json

from celery_man.animate import SCENES

CSP = "default-src 'none'; script-src 'unsafe-inline'; font-src data:; img-src data:; style-src 'unsafe-inline'"

# Frames arrive as messages; errors (script, rejected promise, CSP block) go back to the parent.
BRIDGE = """
const send = (message) => parent.postMessage({ type: "error", message: String(message) }, "*");
addEventListener("error", (e) => send(e.message));
addEventListener("unhandledrejection", (e) => send((e.reason && e.reason.message) || e.reason));
addEventListener("securitypolicyviolation", (e) => send(`blocked ${e.violatedDirective}: ${e.blockedURI}`));
addEventListener("message", async (e) => {
  const m = e.data || {};
  try {
    if (m.type === "init") {
      await window.__init({ ...window.__PREVIEW, frame_limit_ms: Infinity, ...m.payload });
      parent.postMessage({ type: "ready" }, "*");
    } else if (m.type === "frame") window.__draw(m.f);
  } catch (err) {
    send(err.message || err);
  }
});
"""


@functools.cache
def _fonts() -> dict[str, str]:
    return {f.stem: "data:font/ttf;base64," + base64.b64encode(f.read_bytes()).decode() for f in sorted((SCENES / "fonts").glob("*.ttf"))}


def _inline(js: str) -> str:
    return js.replace("</", "<\\/")


def preview_html(source: str, params: dict, look: dict, seed: int) -> str:
    # the scene runs first so a syntax error is reported by the bridge's error handler
    pre = f"window.__FONTS = {json.dumps(_fonts())}; window.__PREVIEW = {json.dumps({'params': params, 'look': look, 'seed': seed})};"
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}"></head><body>'
        f'<canvas id="c"></canvas>'
        f"<script>{_inline(pre)}</script>"
        f"<script>{_inline(BRIDGE)}</script>"
        f"<script>{_inline((SCENES / 'runtime.js').read_text())}</script>"
        f"<script>{_inline(source)}</script>"
        f"</body></html>"
    )
