const col = (env, i) => env.look.colors[i % env.look.colors.length];
let ps, lastBar;
function setup(env) {
  ps = [];
  lastBar = -2;
}
function draw(ctx, f, env) {
  const { width: w, height: h, look, params: p } = env;
  if (f.on_downbeat && f.bar !== lastBar) {
    lastBar = f.bar;
    for (let i = 0; i < p.count; i++) {
      const a = env.random() * Math.PI * 2;
      const v = (0.15 + 0.5 * env.random()) * h;
      ps.push({ x: w / 2, y: h / 2, vx: Math.cos(a) * v, vy: Math.sin(a) * v, r: 0.5 + env.random(), life: 1, c: i });
    }
  }
  if (f.t === 0 && !ps.length) {
    // nothing has hit yet: seed a first burst so a shot starting mid-bar is not empty
    for (let i = 0; i < p.count; i++) {
      const a = env.random() * Math.PI * 2;
      const v = env.random() * 0.4 * h;
      ps.push({ x: w / 2, y: h / 2, vx: Math.cos(a) * v, vy: Math.sin(a) * v, r: 0.5 + env.random(), life: 1, c: i });
    }
  }
  ctx.fillStyle = look.background;
  ctx.fillRect(0, 0, w, h);
  const dt = 1 / env.fps;
  const size = h * (0.006 + 0.016 * f.low);
  ps = ps.filter((q) => q.life > 0);
  for (const q of ps) {
    q.vy += p.gravity * h * dt;
    q.x += q.vx * dt;
    q.y += q.vy * dt;
    q.life -= dt * 0.5;
    ctx.globalAlpha = Math.max(q.life, 0);
    ctx.fillStyle = col(env, q.c);
    ctx.beginPath();
    ctx.arc(q.x, q.y, size * q.r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}
