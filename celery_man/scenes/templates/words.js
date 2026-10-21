const col = (env, i) => env.look.colors[i % env.look.colors.length];
let words;
function setup(env) {
  words = env.params.text.split(" ").filter(Boolean);
  if (!words.length) words = ["\u25cf"]; // empty text: a plain dot
}
function draw(ctx, f, env) {
  const { width: w, height: h, look, params: p } = env;
  ctx.fillStyle = look.background;
  ctx.fillRect(0, 0, w, h);
  const step = p.every === "bar" ? f.bar : f.beat;
  const phase = p.every === "bar" ? f.bar_phase : f.beat_phase;
  const i = Math.max(step, 0) % words.length;
  const size = p.size * h * (1 + 0.08 * (1 - phase) + 0.1 * f.level);
  ctx.font = `${size}px "${look.typeface}"`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const fit = Math.min(1, (w * 0.9) / Math.max(ctx.measureText(words[i]).width, 1));
  ctx.font = `${size * fit}px "${look.typeface}"`;
  ctx.fillStyle = col(env, i);
  ctx.fillText(words[i], w / 2, h * { center: 0.5, lower: 0.72, upper: 0.28 }[p.position]);
}
