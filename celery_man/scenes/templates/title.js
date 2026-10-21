function setup(env) {}
function draw(ctx, f, env) {
  const { width: w, height: h, look } = env;
  const p = { ...env.params, text: env.params.text || "\u25cf" }; // empty text: a plain dot
  ctx.fillStyle = look.background;
  ctx.fillRect(0, 0, w, h);
  const swell = f.on_downbeat ? 1 : Math.pow(1 - f.bar_phase, 3);
  const size = h * 0.16 * (1 + p.pulse * 0.15 * swell);
  ctx.font = `${size}px "${look.typeface}"`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const fit = Math.min(1, (w * 0.9) / Math.max(ctx.measureText(p.text).width, 1));
  ctx.font = `${size * fit}px "${look.typeface}"`;
  ctx.globalAlpha = p.fade > 0 ? Math.min(1, Math.max(0, (f.beat + f.beat_phase) / p.fade)) : 1;
  ctx.fillStyle = look.colors[0];
  ctx.fillText(p.text, w / 2, h / 2);
  ctx.globalAlpha = 1;
}
