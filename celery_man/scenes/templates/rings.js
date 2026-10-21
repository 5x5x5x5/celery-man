const col = (env, i) => { const n = env.look.colors.length; return env.look.colors[((i % n) + n) % n]; };
function setup(env) {}
function draw(ctx, f, env) {
  const { width: w, height: h, look, params: p } = env;
  ctx.fillStyle = look.background;
  ctx.fillRect(0, 0, w, h);
  const reach = Math.hypot(w, h) / 2 * (0.55 + 0.45 * f.level);
  ctx.lineWidth = p.thickness * h;
  for (let k = 0; k < p.count; k++) {
    const age = (k + f.beat_phase) / p.count; // ring k was born k beats ago
    ctx.globalAlpha = Math.pow(1 - age, 1.5);
    ctx.strokeStyle = col(env, f.beat - k);
    ctx.beginPath();
    ctx.arc(w / 2, h / 2, Math.max(age * reach, 0.1), 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}
