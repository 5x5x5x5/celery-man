import hashlib
import http.server
import json
import os
import socket
import subprocess
import threading
import time

import pytest

from celery_man import animate
from celery_man.animate import ChromiumRenderer, FakeRenderer, RenderJob, SceneError
from celery_man.assemble import probe_duration
from celery_man.generate import FPS, H, W

GOOD = """
function setup(env) {}
function draw(ctx, f, env) {
  ctx.fillStyle = env.look.background;
  ctx.fillRect(0, 0, env.width, env.height);
  ctx.fillStyle = env.look.colors[0];
  ctx.fillRect(0, 0, env.width * f.level, 40);
}
"""


def job(source: str = GOOD, n: int = 13) -> RenderJob:
    frames = [{"t": i / FPS, "song_t": 10 + i / FPS, "progress": i / n, "level": i / n, "beat": 0, "on_beat": i == 0} for i in range(n)]
    look = {"background": "#f4efe6", "colors": ["#1d1b18"], "typeface": "serif"}
    return RenderJob("s1", source, {}, look, frames, FPS, W, H, seed=7)


def count_frames(path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,width,height",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    s = json.loads(out)["streams"][0]
    assert (s["width"], s["height"]) == (W, H)
    return int(s["nb_read_frames"])


def decoded(path) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True
    ).stdout


def last_frame_center(path) -> tuple[int, int, int]:
    raw = decoded(path)[-W * H * 3 :]
    i = (H // 2 * W + W // 2) * 3
    return raw[i], raw[i + 1], raw[i + 2]


def test_fake_renderer_frame_count_and_poster(tmp_path):
    r = FakeRenderer()
    out = r.render(job(n=30), tmp_path / "a.mp4")
    assert probe_duration(out) == pytest.approx(1.25, abs=0.05)
    assert count_frames(out) == 30
    assert out.with_suffix(".jpg").exists()
    with pytest.raises(SceneError):
        r.render(job("function draw() { throw new Error('x') }"), tmp_path / "b.mp4")
    assert len(r.jobs) == 2


def test_runtime_sha_is_stable():
    assert animate.runtime_sha() == animate.runtime_sha()
    assert len(animate.runtime_sha()) == 12


@pytest.mark.slow
def test_renders_exact_frames(tmp_path):
    out = ChromiumRenderer().render(job(n=13), tmp_path / "a.mp4")
    assert count_frames(out) == 13
    assert out.with_suffix(".jpg").exists()
    assert not out.with_suffix(".part.mp4").exists()


@pytest.mark.slow
def test_same_inputs_identical_frames(tmp_path):
    src = """
    function setup(env) {}
    function draw(ctx, f, env) {
      ctx.fillStyle = `rgb(${Math.random() * 255}, ${env.random() * 255}, ${(Date.now() + performance.now()) % 255})`;
      ctx.fillRect(Math.random() * env.width, env.random() * env.height, 200, 200);
    }
    """
    r = ChromiumRenderer()
    a, b = (decoded(r.render(job(src, n=8), tmp_path / f"{k}.mp4")) for k in "ab")
    assert len(a) == 8 * W * H * 3
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


@pytest.mark.slow
def test_throwing_scene_fails(tmp_path):
    src = "function setup(env) {}\nfunction draw(ctx, f, env) { if (f.t * env.fps > 4.5) throw new Error('nope'); }"
    with pytest.raises(SceneError, match="nope"):
        ChromiumRenderer().render(job(src, n=8), tmp_path / "a.mp4")
    assert not (tmp_path / "a.mp4").exists()


@pytest.mark.slow
def test_missing_globals_fails(tmp_path):
    with pytest.raises(SceneError, match=r"must define setup\(env\) and draw"):
        ChromiumRenderer().render(job("function draw(ctx, f, env) {}", n=3), tmp_path / "a.mp4")


@pytest.mark.slow
@pytest.mark.parametrize(
    "probe",
    [
        "fetch('https://example.com').then(() => { hit = 'red'; }, () => { hit = 'blue'; });",
        "const img = new Image(); img.onload = () => { hit = 'red'; }; img.onerror = () => { hit = 'blue'; }; img.src = 'http://example.com/x.png';",
    ],
)
def test_network_blocked(tmp_path, probe):
    src = f"""
    let hit = 'gray';
    function setup(env) {{ {probe} }}
    function draw(ctx, f, env) {{ ctx.fillStyle = hit; ctx.fillRect(0, 0, env.width, env.height); }}
    """
    try:
        out = ChromiumRenderer().render(job(src, n=13), tmp_path / "a.mp4")
    except SceneError:
        return
    r, g, b = last_frame_center(out)
    assert b > 200 and r < 50 and g < 50, (r, g, b)


@pytest.mark.slow
def test_infinite_loop_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(animate, "FRAME_LIMIT_S", 0.5)
    monkeypatch.setattr(animate, "STARTUP_S", 15)
    t0 = time.monotonic()
    with pytest.raises(SceneError, match="did not finish"):
        ChromiumRenderer().render(job("function setup(env) {}\nfunction draw() { while (true) {} }", n=4), tmp_path / "a.mp4")
    assert time.monotonic() - t0 < 25
    assert not (tmp_path / "a.mp4").exists()
    assert not (tmp_path / "a.part.mp4").exists()


@pytest.mark.slow
def test_slow_frame_fails(tmp_path):
    src = "function setup(env) {}\nfunction draw(ctx, f, env) { const t = env.__realNow(); while (env.__realNow() - t < 2500) {} }"
    with pytest.raises(SceneError, match=r"frame 0 took \d+ ms"):
        ChromiumRenderer().render(job(src, n=2), tmp_path / "a.mp4")


@pytest.mark.slow
def test_bundled_faces_load(tmp_path):
    src = """
    function setup(env) {
      if (env.look.typeface !== 'Source Serif 4') throw new Error('typeface ' + env.look.typeface);
    }
    function draw(ctx, f, env) {
      const width = (fam) => { ctx.font = `40px "${fam}"`; return ctx.measureText('Hamburgefonstiv').width; };
      for (const fam of ['Source Serif 4', 'Inter', 'JetBrains Mono', 'Archivo Black']) {
        if (width(fam) === width('missing face')) throw new Error('fallback for ' + fam);
      }
    }
    """
    ChromiumRenderer().render(job(src, n=1), tmp_path / "a.mp4")


@pytest.mark.slow
def test_popup_cannot_reach_network(tmp_path):
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/leak"
    src = f"""
    function setup(env) {{
      window.open('{url}?open');
      const a = document.createElement('a'); a.href = '{url}?blank'; a.target = '_blank'; document.body.appendChild(a); a.click();
      return new Promise(r => setTimeout(r, 1500));
    }}
    function draw(ctx, f, env) {{}}
    """
    try:
        ChromiumRenderer().render(job(src, n=2), tmp_path / "a.mp4")
    except SceneError:
        pass
    finally:
        srv.shutdown()
    assert hits == []


def test_interrupted_render_kills_child(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(animate.subprocess.Popen, "communicate", lambda self, timeout: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(animate.os, "killpg", lambda pid, sig: killed.append(pid) or os.kill(pid, sig))
    with pytest.raises(KeyboardInterrupt):
        ChromiumRenderer().render(job(n=2), tmp_path / "a.mp4")
    assert killed


@pytest.mark.parametrize(("code", "exc"), [(3, SceneError), (1, RuntimeError)])
def test_child_exit_code_separates_scene_from_renderer_failure(tmp_path, monkeypatch, code, exc):
    child = tmp_path / "child"
    child.write_text(f"#!/bin/sh\necho boom >&2\nexit {code}\n")
    child.chmod(0o755)
    monkeypatch.setattr(animate.sys, "executable", str(child))
    with pytest.raises(exc, match="boom") as e:
        ChromiumRenderer().render(job(n=2), tmp_path / "a.mp4")
    assert (e.type is SceneError) == (code == 3)


def test_main_exits_3_on_scene_error(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "job.json"
    spec.write_text(json.dumps(animate.dataclasses.asdict(job(n=1)) | {"frame_limit_ms": 1}))
    monkeypatch.setattr(animate, "render_in_browser", lambda *a: (_ for _ in ()).throw(SceneError("scene threw")))
    with pytest.raises(SystemExit) as e:
        animate.main([str(spec), str(tmp_path / "a.mp4")])
    assert e.value.code == animate.SCENE_EXIT and "scene threw" in capsys.readouterr().err


@pytest.mark.slow
def test_webrtc_cannot_send_udp(tmp_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)
    src = f"""
    async function setup(env) {{
      const pc = new RTCPeerConnection({{iceServers: [{{urls: 'stun:127.0.0.1:{sock.getsockname()[1]}'}}]}});
      pc.createDataChannel('x');
      await pc.setLocalDescription(await pc.createOffer());
      await new Promise(r => setTimeout(r, 1500));
    }}
    function draw(ctx, f, env) {{}}
    """
    try:
        ChromiumRenderer().render(job(src, n=2), tmp_path / "a.mp4")
    except SceneError:
        pass
    try:
        with pytest.raises(TimeoutError):
            sock.recvfrom(1024)
    finally:
        sock.close()
