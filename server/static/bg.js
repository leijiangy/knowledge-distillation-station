/* 知识蒸馏站 —— 动态科技背景（画布层，随鼠标变化）
   绿色体系原样保留：点阵沿用原来的色值 rgba(47,122,61,.10) 与 24px 密度，只是从静态改为会动。
   三层叠加：
     ① 透视点阵：光标附近的点提亮放大，整体随鼠标做轻微视差
     ② 连络网：节点缓慢漂移、近邻连线；光标把附近节点牵住并连线，呼应「蒸馏 / 网络」题材
     ③ 光标辉光 + 斜向扫描带：让静态纸面有持续流动的响应感
   只铺在纸张之外的外围区域，透明度压低不抢正文；prefers-reduced-motion 时只画一帧静态图。 */
(() => {
  "use strict";

  const canvas = document.getElementById("bg-canvas");
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const GREEN = [47, 122, 61];    // 与 style.css 的 --green 同源，不引入新的绿色
  const TEAL = [51, 122, 110];
  const GOLD = [183, 134, 40];
  const rgba = (c, a) => `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`;

  const GRID = 24;          // 点阵间距（px）——与原来 CSS 点阵的 24px 一致
  const DOT = 2;            // 点的基准边长（px）
  const DOT_A = 0.10;       // 点的基准透明度——与原来 CSS 点阵的 .10 一致
  const PARALLAX = 22;      // 视差最大位移（px）
  const NEAR_R = 250;       // 点阵被光标提亮的半径
  const LINK = 132;         // 节点之间连线的最大距离
  const CURSOR_LINK = 210;  // 光标牵引半径
  const CURSOR_GLOW = 340;  // 光标辉光半径
  const EASE = 0.09;        // 光标缓动系数（越小越「沉」）

  let w = 0, h = 0;
  let nodes = [];
  let raf = 0, last = 0, running = false, resizeTimer = 0;
  const pointer = { x: 0, y: 0, tx: 0, ty: 0, placed: false };

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ---------- 尺寸与节点 ---------- */
  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(h * dpr));
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 光标未进入页面前，辉光停在偏上的视觉重心处
    if (!pointer.placed) {
      pointer.x = pointer.tx = w / 2;
      pointer.y = pointer.ty = h * 0.35;
      pointer.placed = true;
    }
    seed();
  }

  function seed() {
    const count = Math.max(38, Math.min(104, Math.round((w * h) / 26000)));
    nodes = [];
    for (let i = 0; i < count; i++) {
      nodes.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.22,
        vy: (Math.random() - 0.5) * 0.22,
        r: 1.1 + Math.random() * 1.3,
        teal: Math.random() < 0.34,
        gold: Math.random() < 0.07,
        pull: 0,
      });
    }
  }

  /* ---------- 每帧推进 ---------- */
  function step(dt) {
    const k = Math.min(dt / 16.67, 3);   // 掉帧时按比例补偿，但不放大到失控
    const follow = Math.min(1, EASE * k);

    for (const n of nodes) {
      n.x += n.vx * k;
      n.y += n.vy * k;
      // 越界回弹：留 20px 余量，避免节点贴着边缘成排
      if (n.x < -20) { n.x = -20; n.vx = Math.abs(n.vx); }
      else if (n.x > w + 20) { n.x = w + 20; n.vx = -Math.abs(n.vx); }
      if (n.y < -20) { n.y = -20; n.vy = Math.abs(n.vy); }
      else if (n.y > h + 20) { n.y = h + 20; n.vy = -Math.abs(n.vy); }
      n.pull = 0;
    }

    pointer.x += (pointer.tx - pointer.x) * follow;
    pointer.y += (pointer.ty - pointer.y) * follow;
  }

  /* ---------- ③ 光标辉光（最底层：先把背景点亮） ---------- */
  function drawGlow() {
    const g = ctx.createRadialGradient(pointer.x, pointer.y, 0, pointer.x, pointer.y, CURSOR_GLOW);
    g.addColorStop(0, "rgba(255, 255, 255, .40)");
    g.addColorStop(0.38, rgba(GREEN, 0.08));
    g.addColorStop(1, rgba(GREEN, 0));
    ctx.fillStyle = g;
    ctx.fillRect(pointer.x - CURSOR_GLOW, pointer.y - CURSOR_GLOW, CURSOR_GLOW * 2, CURSOR_GLOW * 2);
  }

  /* ---------- ③ 斜向扫描带（在辉光之上、结构之下） ---------- */
  function drawSweep(now) {
    const band = 300;
    const span = w + h + band * 2;
    const s = ((now % 11000) / 11000) * span - band;   // 沿 (1,1) 方向的投影坐标
    const g = ctx.createLinearGradient(s / 2, s / 2, (s + band * 2) / 2, (s + band * 2) / 2);
    g.addColorStop(0, "rgba(255, 255, 255, 0)");
    g.addColorStop(0.5, "rgba(255, 255, 255, .17)");
    g.addColorStop(1, "rgba(255, 255, 255, 0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  }

  /* ---------- ① 透视点阵 ---------- */
  function drawGrid() {
    const ox = (pointer.x - w / 2) / w;
    const oy = (pointer.y - h / 2) / h;
    const buckets = [[], [], [], [], [], []];   // 近点按提亮档位分批，避免逐点切换 fillStyle

    ctx.fillStyle = rgba(GREEN, DOT_A);   // 远点底色：整屏只设一次
    for (let gy = GRID / 2; gy < h + GRID; gy += GRID) {
      const depth = 0.35 + 0.85 * (gy / h);
      const y = gy + oy * PARALLAX * depth;
      for (let gx = GRID / 2; gx < w + GRID; gx += GRID) {
        const x = gx + ox * PARALLAX * depth;
        const dx = x - pointer.x;
        const dy = y - pointer.y;
        const near = Math.max(0, 1 - Math.sqrt(dx * dx + dy * dy) / NEAR_R);
        if (near < 0.04) { ctx.fillRect(x - DOT / 2, y - DOT / 2, DOT, DOT); continue; }
        buckets[Math.min(5, Math.floor(near * 6))].push(x, y, DOT + near * 2.4);
      }
    }

    for (let b = 0; b < buckets.length; b++) {
      const list = buckets[b];
      if (!list.length) continue;
      ctx.fillStyle = rgba(GREEN, DOT_A + ((b + 1) / 6) * 0.26);
      for (let i = 0; i < list.length; i += 3) {
        const size = list[i + 2];
        ctx.fillRect(list[i] - size / 2, list[i + 1] - size / 2, size, size);
      }
    }
  }

  /* ---------- ② 节点之间的近邻连线 ---------- */
  function drawLinks() {
    const L2 = LINK * LINK;
    ctx.lineWidth = 1;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > L2) continue;
        ctx.strokeStyle = rgba(GREEN, (1 - Math.sqrt(d2) / LINK) * 0.17);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
  }

  /* ---------- ② 光标牵引：把附近节点连到光标上 ---------- */
  function drawCursorLinks() {
    const R2 = CURSOR_LINK * CURSOR_LINK;
    for (const n of nodes) {
      const dx = n.x - pointer.x;
      const dy = n.y - pointer.y;
      const d2 = dx * dx + dy * dy;
      if (d2 > R2) continue;
      const t = 1 - Math.sqrt(d2) / CURSOR_LINK;
      n.pull = t;
      ctx.strokeStyle = rgba(n.gold ? GOLD : (n.teal ? TEAL : GREEN), t * 0.38);
      ctx.beginPath();
      ctx.moveTo(pointer.x, pointer.y);
      ctx.lineTo(n.x, n.y);
      ctx.stroke();
    }
  }

  /* ---------- ② 节点本体 ---------- */
  function drawNodes() {
    for (const n of nodes) {
      const color = n.gold ? GOLD : (n.teal ? TEAL : GREEN);
      ctx.fillStyle = rgba(color, 0.34 + n.pull * 0.5);
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r + n.pull * 1.6, 0, Math.PI * 2);
      ctx.fill();
      if (n.pull > 0.05) {
        ctx.strokeStyle = rgba(color, n.pull * 0.3);
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 5 + n.pull * 6, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }

  function render(now) {
    ctx.clearRect(0, 0, w, h);
    drawGlow();
    drawSweep(now);
    drawGrid();
    drawLinks();
    drawCursorLinks();
    drawNodes();
  }

  /* ---------- 主循环 ---------- */
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = last ? Math.min(now - last, 50) : 16.67;
    last = now;
    step(dt);
    render(now);
  }

  function start() {
    if (running || reduceMotion.matches) return;
    running = true;
    last = 0;
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    last = 0;
  }

  /* ---------- 事件 ---------- */
  window.addEventListener("pointermove", (e) => {
    pointer.tx = e.clientX;
    pointer.ty = e.clientY;
  }, { passive: true });

  // 光标离开窗口后，辉光回落到视觉重心，避免「卡」在边缘
  window.addEventListener("mouseout", (e) => {
    if (e.relatedTarget) return;
    pointer.tx = w / 2;
    pointer.ty = h * 0.35;
  });

  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      resize();
      if (!running) render(0);
    }, 160);
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else start();
  });

  if (reduceMotion.addEventListener) {
    reduceMotion.addEventListener("change", () => {
      if (reduceMotion.matches) { stop(); render(0); }
      else start();
    });
  }

  resize();
  if (reduceMotion.matches) render(0);   // 静态帧：保留构图，不做动画
  else start();
})();
