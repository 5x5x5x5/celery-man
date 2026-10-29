// Scene runtime: pins time and randomness, loads scene.js, calls setup once and draw per frame.
"use strict";
(() => {
  const FACES = { serif: "Source Serif 4", sans: "Inter", mono: "JetBrains Mono", display: "Archivo Black" };
  // FontFace, not a <style> @font-face: the CSP has no style-src, so inline styles are blocked
  for (const [file, family] of Object.entries(FACES)) {
    document.fonts.add(new FontFace(family, `url(${(window.__FONTS || {})[file] || "fonts/" + file + ".ttf"})`, file === "display" ? {} : { weight: "100 900" }));
  }
  const realNow = performance.now.bind(performance); // captured before pinning, for the frame limit
  let now = 0;
  Date.now = () => now;
  performance.now = () => now;

  function mulberry32(a) {
    return () => {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const canvas = document.getElementById("c");
  const ctx = canvas.getContext("2d");
  let env, limitMs;

  window.__init = async (p) => {
    canvas.width = p.width;
    canvas.height = p.height;
    limitMs = p.frame_limit_ms;
    const random = mulberry32(p.seed);
    Math.random = random;
    const look = { ...p.look, typeface: FACES[p.look.typeface] || FACES.serif };
    env = { params: p.params, look, width: p.width, height: p.height, fps: p.fps, duration: p.duration, random, __realNow: realNow };
    if (typeof window.setup !== "function" || typeof window.draw !== "function") { // the preview inlines the scene
      await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "scene.js";
        s.onload = resolve;
        s.onerror = () => reject(new Error("scene.js failed to load"));
        document.body.appendChild(s);
      });
    }
    if (typeof window.setup !== "function" || typeof window.draw !== "function") {
      throw new Error("scene must define setup(env) and draw(ctx, f, env)");
    }
    await Promise.all([...document.fonts].map((face) => face.load()));
    await document.fonts.ready;
    await window.setup(env);
  };

  window.__draw = (f) => {
    now = f.song_t * 1000;
    window.draw(ctx, f, env);
  };

  window.__frame = (i, f) => {
    const t0 = realNow();
    window.__draw(f);
    const ms = realNow() - t0;
    if (ms > limitMs) throw new Error(`frame ${i} took ${Math.round(ms)} ms`);
    return canvas.toDataURL("image/jpeg", 0.92);
  };
})();
