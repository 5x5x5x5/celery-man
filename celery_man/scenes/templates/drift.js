const col = (env, i) => env.look.colors[i % env.look.colors.length];
let ps;
function setup(env) {
  ps = Array.from({ length: env.params.count }, () => ({ x: env.random() * env.width, y: env.random() * env.height }));
}
function field(x, y, t) {
  return Math.sin(x * 0.006 + t * 0.5) + Math.cos(y * 0.007 - t * 0.4) + Math.sin((x + y) * 0.004 + t * 0.2);
}
function draw(ctx, f, env) {
  const { width: w, height: h, look, params: p } = env;
  ctx.globalAlpha = f.t === 0 ? 1 : 1 - p.trail;
  ctx.fillStyle = look.background;
  ctx.fillRect(0, 0, w, h);
  ctx.globalAlpha = 1;
  const speed = (1.2 + 5 * f.level + 2 * (1 - f.beat_phase)) * (h / 544);
  ctx.lineWidth = Math.max(1.5, h / 300);
  ps.forEach((q, i) => {
    const a = field(q.x, q.y, f.song_t) * 1.6;
    const nx = q.x + Math.cos(a) * speed;
    const ny = q.y + Math.sin(a) * speed;
    ctx.strokeStyle = col(env, i);
    ctx.beginPath();
    ctx.moveTo(q.x, q.y);
    ctx.lineTo(nx, ny);
    ctx.stroke();
    q.x = (nx + w) % w;
    q.y = (ny + h) % h;
  });
}
