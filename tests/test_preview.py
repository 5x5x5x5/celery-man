from html.parser import HTMLParser

import pytest

from celery_man.project import DEFAULT_LOOK, write_json
from celery_man.web.preview import preview_html
from tests.test_api_core import decided
from tests.test_api_decisions import U, client

CSP = "default-src 'none'; script-src 'unsafe-inline'; font-src data:; img-src data:; style-src 'unsafe-inline'"


def test_preview_escapes_script_close():
    html = preview_html("window.x='</script><script>alert(1)</script>'", {}, DEFAULT_LOOK, 1)
    tags = []

    class P(HTMLParser):
        handle_starttag = lambda self, tag, attrs: tags.append(tag)

    P().feed(html)  # a real parser: the scene text must not open a second script element
    assert "</script><script>alert(1)" not in html and tags.count("script") == html.count("</script>")


def test_preview_csp_blocks_network():
    html = preview_html("", {}, DEFAULT_LOOK, 1)
    assert CSP in html and "connect-src" not in html


def test_frames_route_counts(tmp_path, song):
    c = client(decided(tmp_path, song).root)
    r = c.get(f"{U}/frames", params={"start": 0, "end": 2.5})
    assert r.status_code == 200 and len(r.json()) == round(2.5 * 24)


def test_preview_route_template_and_candidate(tmp_path, song):
    p = decided(tmp_path, song)
    c = client(p.root)
    scene = {"template": "rings", "params": {}}
    s = next(x["shot"] for x in c.get(f"{U}/state").json()["shots"] if x["shot"]["id"] == "s01")
    cand = p.root / "candidates" / "shots" / "s01.json"
    cand.parent.mkdir(parents=True, exist_ok=True)
    write_json(cand, {"items": [{"id": "c1", "shot": s | {"kind": "animation", "scene": {"file": "scenes/x.js", "params": {}}},
                                 "code": "window.mine=1", "still": None, "error": None},
                                {"id": "c2", "shot": s | {"kind": "animation", "scene": scene}, "code": None, "still": None, "error": None}]})  # fmt: skip
    r = c.get(f"{U}/preview", params={"sid": "s01", "candidate": "c1"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and "window.mine=1" in r.text
    assert "function draw" in c.get(f"{U}/preview", params={"sid": "s01", "candidate": "c2"}).text
    assert c.get(f"{U}/preview", params={"sid": "s01", "candidate": "nope"}).status_code == 422
    assert c.get(f"{U}/preview", params={"sid": "s01"}).status_code == 422  # a footage shot has no scene


FETCHING = """
function setup(env) {}
function draw(ctx, f, env) {
  fetch("http://example.invalid/x").catch(() => {});
  ctx.fillStyle = "#ff0000";
  ctx.fillRect(0, 0, env.width, env.height);
}
"""


@pytest.mark.slow
def test_preview_draws_and_fetch_blocked():
    from playwright.sync_api import sync_playwright

    html = preview_html(FETCHING, {}, DEFAULT_LOOK, 1)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.set_content(html)
        page.evaluate("""() => { window.__msgs = []; window.parent.postMessage = (m) => window.__msgs.push(m); }""")
        page.evaluate("""async () => {
            window.dispatchEvent(new MessageEvent("message", {data: {type: "init", payload: {width: 64, height: 36, fps: 24, duration: 1}}}));
            await new Promise(r => setTimeout(r, 500));
            window.dispatchEvent(new MessageEvent("message", {data: {type: "frame", i: 0, f: {t: 0, song_t: 0, progress: 0, level: 0.5}}}));
            await new Promise(r => setTimeout(r, 500));
        }""")
        red = page.evaluate("document.getElementById('c').getContext('2d').getImageData(1, 1, 1, 1).data[0]")
        msgs = page.evaluate("window.__msgs")
        b.close()
    assert red == 255
    assert any(m["type"] == "error" and "blocked" in m["message"] for m in msgs), msgs
