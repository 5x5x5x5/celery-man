const col = (env, i) => env.look.colors[i % env.look.colors.length];
function setup(env) {}
function draw(ctx, f, env) {
  const { width: w, height: h, look, params: p } = env;
  ctx.fillStyle = look.background;
  ctx.fillRect(0, 0, w, h);
  const s = w / p.cols;
  const rows = Math.ceil(h / s);
  const oy = (h - rows * s) / 2;
  const flash = Math.pow(1 - f.beat_phase, 2) * p.pulse;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < p.cols; c++) {
      const k = r * p.cols + c;
      // each tile flashes on its own beat so the grid ripples instead of blinking whole
      const on = (f.beat + k) % 4 === 0 ? flash : 0;
      const side = s * (0.3 + 0.35 * f.level + 0.3 * on);
      ctx.save();
      ctx.translate((c + 0.5) * s, oy + (r + 0.5) * s);
      ctx.rotate(f.mid * Math.PI * 0.5 + f.t * 0.2);
      ctx.fillStyle = col(env, k);
      ctx.globalAlpha = 0.35 + 0.65 * Math.min(1, on + f.mid);
      ctx.fillRect(-side / 2, -side / 2, side, side);
      ctx.restore();
    }
  }
}
