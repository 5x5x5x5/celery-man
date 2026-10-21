const col = (env, i) => env.look.colors[i % env.look.colors.length];
function setup(env) {}
function draw(ctx, f, env) {
  const { width: w, height: h, look, params: p } = env;
  ctx.fillStyle = look.background;
  ctx.fillRect(0, 0, w, h);
  const vertical = p.orientation === "vertical";
  const span = vertical ? w : h;
  const room = vertical ? h : w;
  const slot = span / p.count;
  for (let i = 0; i < p.count; i++) {
    const x = i / (p.count - 1);
    // low on the left, high on the right, blended between
    const v = x < 0.5 ? f.low * (1 - 2 * x) + f.mid * 2 * x : f.mid * (2 - 2 * x) + f.high * (2 * x - 1);
    const len = room * (0.08 + 0.85 * v * (0.75 + 0.25 * Math.sin(i + f.beat)));
    ctx.fillStyle = col(env, i);
    if (vertical) ctx.fillRect(i * slot + slot * 0.1, room - len, slot * 0.8, len);
    else ctx.fillRect(0, i * slot + slot * 0.1, len, slot * 0.8);
  }
}
