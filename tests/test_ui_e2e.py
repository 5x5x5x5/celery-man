"""The whole tastemaker path in a real browser: the built front end over a live server, a fake generator and fake models."""

import hashlib
import json
import re
import socket
import threading
import time
import urllib.request

import pytest
import uvicorn

from celery_man import steps
from celery_man.generate import FakeGenerator
from celery_man.project import read_json
from celery_man.web.app import DIST, create_app
from tests.test_pipeline import text_chat, vision_chat

IDEAS = [
    {"kind": "animation", "action": "rings pulse", "template": "rings", "params": {}},
    {"kind": "footage", "action": "a person walks", "cast": ["A"], "place": "P", "framing": "wide shot"},
    {"kind": "footage", "action": "a person sits", "cast": ["A"], "place": "P", "framing": "close shot"},
]


def chat(system, user, images):
    return json.dumps(IDEAS) if "ideas for one shot" in system else text_chat(system, user, images)


def detect(song):
    """A steady 150 bpm pulse: three windows in the first 8 s (a sine has no beat to find)."""
    beats = [round(i * 0.4, 3) for i in range(101)]
    return {"duration": 40.0, "beats": beats, "downbeats": beats[::4], "tempo": [[0.0, 150.0]],
            "sections": [{"start": 0.0, "end": 16.0, "label": "1"}, {"start": 16.0, "end": 40.0, "label": "2"}],
            "confidence": 1.0, "song_sha": hashlib.sha256(song.read_bytes()).hexdigest()[:12]}  # fmt: skip


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(steps, "detect", detect)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(create_app(tmp_path, FakeGenerator, lambda p: (chat, vision_chat())), port=port, log_level="warning")
    )
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(10)


def wait_idle(url: str, timeout: float = 120) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = json.load(urllib.request.urlopen(f"{url}/api/projects/proj/state"))
        j = st["jobs"]
        if j["running"] is None and not j["queued"] and not j["free"]:
            return st
        time.sleep(0.2)
    raise AssertionError("jobs did not finish")


@pytest.mark.slow
@pytest.mark.skipif(not (DIST / "index.html").is_file(), reason="front end not built")
def test_tastemaker_walkthrough(tmp_path, song, server):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(30_000)
        expect.set_options(timeout=30_000)  # the first prompt check loads the tokenizer
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Song: a new project with both kinds
        page.goto(f"{server}/")
        page.get_by_role("button", name="New").click()
        page.get_by_label("Name").fill("proj")
        page.get_by_label("Song file").set_input_files(song)
        page.get_by_label("animation").check()
        page.get_by_role("button", name="Create").click()
        page.wait_for_url("**/#/p/proj/song")
        wave = page.locator(".wave")
        expect(wave.locator("canvas")).to_be_visible()
        expect(page.get_by_text("reading the song…")).to_have_count(0)

        # the range 0-8 s, dragged right to left on the waveform (40 s wide) and past its start
        box = wave.bounding_box()
        y = box["y"] + box["height"] / 2
        page.mouse.move(box["x"] + box["width"] * 8 / 40, y)
        page.mouse.down()
        page.mouse.move(box["x"] - 20, y, steps=5)
        page.mouse.up()
        _wait(lambda: read_json(tmp_path / "proj/project.json")["range"][0] == 0)
        assert 7.8 < read_json(tmp_path / "proj/project.json")["range"][1] < 8.2
        expect(page.get_by_text(re.compile(r"\b3 windows"))).to_be_visible()

        # Brief, by hand
        page.goto(f"{server}/#/p/proj/brief")
        page.get_by_role("button", name="Manual").click()
        page.get_by_label("Logline").fill("a person walks")
        for k, v in {"medium": "ink", "palette": "grey", "lighting": "soft", "camera": "static"}.items():
            page.get_by_label(k, exact=True).fill(v)
        for label, name, desc in (("Cast", "A", "a tall person"), ("Places", "P", "a room")):
            page.locator("fieldset", has=page.locator("legend", has_text=label)).get_by_role("button", name="Add").click()
            page.get_by_label(f"{label} name 1").fill(name)
            page.get_by_label(f"{label} description 1").fill(desc)
        page.get_by_role("button", name="Save brief").click()
        _wait(lambda: (tmp_path / "proj/brief.json").is_file())

        # Shots: s01 by hand, s02 from a suggestion, s03 left open
        page.goto(f"{server}/#/p/proj/shots")
        strip = page.locator(".shotstrip")
        strip.get_by_role("button", name="s01").click()
        page.get_by_role("button", name="Manual").click()
        page.get_by_label("Action").fill("a person walks")
        page.get_by_label("A", exact=True).check()
        page.get_by_label("Place").select_option("P")
        page.get_by_label("Framing").fill("wide shot")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(strip.get_by_role("button", name="s01")).to_contain_text("you")

        strip.get_by_role("button", name="s02").click()
        page.get_by_role("button", name="Suggestions").click()
        page.get_by_role("button", name="Suggest for this shot").click()
        card = page.locator("article.card", has_text="rings")
        card.get_by_role("button", name="Use this").click()
        expect(strip.get_by_role("button", name="s02")).to_contain_text("you")
        expect(strip.get_by_role("button", name="s03")).to_contain_text("open")

        # Keyframes: four stills for s01, approve one
        page.goto(f"{server}/#/p/proj/keyframes")
        kstrip = page.locator(".shotstrip")
        kstrip.get_by_role("button", name="s01").click()
        page.get_by_role("button", name="Suggestions").click()
        page.get_by_role("button", name="4 stills").click()
        page.get_by_role("button", name="Approve").first.click()
        expect(kstrip.get_by_role("button", name="s01")).to_contain_text("✓ you")

        # Continue: a round of takes for s01 and the render for s02
        with page.expect_response(lambda r: r.url.endswith("/continue")) as r:
            page.get_by_role("button", name="Continue").click()
        assert r.value.status == 202
        wait_idle(server, 300)
        assert read_json(tmp_path / "proj/renders.json")["s02"]["clip"]

        # Takes: an in-point of 0.5 s (12 frames) on the first take
        page.goto(f"{server}/#/p/proj/takes")
        page.locator(".shotstrip").get_by_role("button", name="s01").click()
        page.get_by_role("button", name="Suggestions").click()
        take = page.locator("article.takecard").first  # its slider shows once its clip's length is known
        slider = take.get_by_role("slider", name="In point")
        slider.focus()
        for _ in range(12):
            slider.press("ArrowRight")
        expect(slider).to_have_attribute("aria-valuenow", "0.5")
        take.get_by_role("button", name="Use").click()
        _wait(lambda: (read_json(tmp_path / "proj/picks.json") or {}).get("s01", {}).get("in") == 0.5)

        # Cut: assemble a draft; the open s03 needs a look
        page.goto(f"{server}/#/p/proj/cut")
        with page.expect_response(lambda r: r.url.endswith("/assemble")) as r:
            page.get_by_role("button", name="Assemble draft").click()
        assert r.value.status == 202
        wait_idle(server)
        assert (tmp_path / "proj/out/video.mp4").is_file()
        looks = page.locator("h3:has-text('Needs a look') ~ ul")
        expect(looks.get_by_role("link", name="s03")).to_be_visible()
        assert not errors
        browser.close()


def _wait(cond, timeout: float = 30) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.1)
