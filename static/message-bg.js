/**
 * Interactive iMessage-style bubble background using real messages from chat.db.
 */
(function () {
  const canvas = document.getElementById("msg-bg");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const bubbles = [];
  let messagePool = [];
  let width = 0;
  let height = 0;
  let dpr = 1;
  let mouseX = -9999;
  let mouseY = -9999;
  let rafId = 0;
  let anonymousMode = localStorage.getItem("anonymousMode") === "1";

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const STYLES = {
    sent: {
      fill: "rgba(0, 122, 255, 0.42)",
      stroke: "rgba(96, 165, 250, 0.35)",
      line: "rgba(191, 219, 254, 0.55)",
    },
    received: {
      fill: "rgba(58, 58, 60, 0.55)",
      stroke: "rgba(120, 125, 140, 0.35)",
      line: "rgba(180, 185, 198, 0.4)",
    },
    sms: {
      fill: "rgba(52, 199, 89, 0.38)",
      stroke: "rgba(74, 222, 128, 0.35)",
      line: "rgba(187, 247, 208, 0.5)",
    },
  };

  const FALLBACK = [
    { text: "hey", type: "received" },
    { text: "sounds good", type: "sent" },
    { text: "on my way", type: "sent" },
    { text: "lol", type: "received" },
    { text: "see you soon", type: "sent" },
  ];

  function maskText(text) {
    if (!anonymousMode) return text;
    const words = Math.max(2, Math.min(8, Math.ceil(text.length / 14)));
    return Array.from({ length: words }, () => "████").join(" ");
  }

  function pickMessage() {
    const pool = messagePool.length ? messagePool : FALLBACK;
    return pool[Math.floor(Math.random() * pool.length)];
  }

  function wrapLines(text, maxWidth) {
    ctx.font = "500 12px -apple-system, BlinkMacSystemFont, sans-serif";
    const display = maskText(text);
    const words = display.split(/\s+/);
    const lines = [];
    let line = "";
    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (ctx.measureText(test).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = test;
      }
    }
    if (line) lines.push(line);
    return lines.slice(0, 3);
  }

  function measureBubble(text, type) {
    const maxTextW = 170;
    const lines = wrapLines(text, maxTextW);
    ctx.font = "500 12px -apple-system, BlinkMacSystemFont, sans-serif";
    const textW = Math.min(
      maxTextW,
      Math.max(...lines.map(l => ctx.measureText(l).width), 24)
    );
    const padX = 14;
    const lineH = 16;
    const w = Math.max(64, textW + padX * 2);
    const h = Math.max(34, lines.length * lineH + 16);
    return { w, h, lines };
  }

  function createBubble(x, y, msg) {
    const source = msg || pickMessage();
    const type = source.type || (source.is_from_me ? "sent" : "received");
    const dims = measureBubble(source.text, type);
    return {
      x: x ?? Math.random() * width,
      y: y ?? Math.random() * height,
      w: dims.w,
      h: dims.h,
      text: source.text,
      lines: dims.lines,
      type,
      vx: (Math.random() - 0.5) * (reducedMotion ? 0.05 : 0.35),
      vy: (Math.random() - 0.5) * (reducedMotion ? 0.05 : 0.35),
      angle: (Math.random() - 0.5) * 0.25,
      va: (Math.random() - 0.5) * 0.004,
      opacity: 0.32 + Math.random() * 0.38,
    };
  }

  function bubbleCount() {
    return Math.min(50, Math.max(16, Math.floor((width * height) / 32000)));
  }

  function seedBubbles() {
    bubbles.length = 0;
    const n = bubbleCount();
    const pool = messagePool.length ? messagePool : FALLBACK;
    for (let i = 0; i < n; i++) {
      const msg = pool[i % pool.length];
      bubbles.push(createBubble(undefined, undefined, msg));
    }
  }

  async function loadMessages() {
    try {
      const res = await fetch("/api/background-messages?limit=70");
      const data = await res.json();
      if (data.messages && data.messages.length) {
        messagePool = data.messages;
        if (bubbles.length) {
          bubbles.forEach((b, i) => {
            const msg = messagePool[i % messagePool.length];
            const rebuilt = createBubble(b.x, b.y, msg);
            Object.assign(b, rebuilt, { vx: b.vx, vy: b.vy, angle: b.angle });
          });
        } else {
          seedBubbles();
        }
      }
    } catch (_) {
      /* keep fallback phrases */
    }
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    seedBubbles();
  }

  function roundRect(x, y, w, h, r) {
    if (ctx.roundRect) {
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, r);
      return;
    }
    const rad = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rad, y);
    ctx.arcTo(x + w, y, x + w, y + h, rad);
    ctx.arcTo(x + w, y + h, x, y + h, rad);
    ctx.arcTo(x, y + h, x, y, rad);
    ctx.arcTo(x, y, x + w, y, rad);
    ctx.closePath();
  }

  function drawBubble(b) {
    const style = STYLES[b.type] || STYLES.received;
    ctx.save();
    ctx.translate(b.x, b.y);
    ctx.rotate(b.angle);
    ctx.globalAlpha = b.opacity;

    const r = Math.min(18, b.h / 2);
    const x = -b.w / 2;
    const y = -b.h / 2;

    roundRect(x, y, b.w, b.h, r);
    ctx.fillStyle = style.fill;
    ctx.fill();
    ctx.strokeStyle = style.stroke;
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.font = "500 12px -apple-system, BlinkMacSystemFont, sans-serif";
    ctx.fillStyle = style.line;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.globalAlpha = b.opacity * 0.9;

    const lineH = 16;
    const startY = -((b.lines.length - 1) * lineH) / 2;
    b.lines.forEach((line, i) => {
      ctx.fillText(line, 0, startY + i * lineH);
    });

    ctx.restore();
  }

  function applyPointerForce(b) {
    const dx = b.x - mouseX;
    const dy = b.y - mouseY;
    const dist = Math.hypot(dx, dy);
    const radius = 140;
    if (dist > radius || dist < 1) return;

    const force = (1 - dist / radius) * 1.8;
    b.vx += (dx / dist) * force;
    b.vy += (dy / dist) * force;
    b.va += (Math.random() - 0.5) * force * 0.02;
  }

  function resolveBubbleCollision(a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const dist = Math.hypot(dx, dy);
    const minDist = (Math.max(a.w, a.h) + Math.max(b.w, b.h)) * 0.38;
    if (dist >= minDist || dist < 1) return;

    const nx = dx / dist;
    const ny = dy / dist;
    const overlap = minDist - dist;
    a.x -= nx * overlap * 0.5;
    a.y -= ny * overlap * 0.5;
    b.x += nx * overlap * 0.5;
    b.y += ny * overlap * 0.5;

    const push = 0.04;
    a.vx -= nx * push;
    a.vy -= ny * push;
    b.vx += nx * push;
    b.vy += ny * push;
  }

  function keepInBounds(b) {
    const pad = 20;
    const halfW = b.w / 2;
    const halfH = b.h / 2;
    if (b.x < halfW + pad) {
      b.x = halfW + pad;
      b.vx = Math.abs(b.vx) * 0.6;
    }
    if (b.x > width - halfW - pad) {
      b.x = width - halfW - pad;
      b.vx = -Math.abs(b.vx) * 0.6;
    }
    if (b.y < halfH + pad) {
      b.y = halfH + pad;
      b.vy = Math.abs(b.vy) * 0.6;
    }
    if (b.y > height - halfH - pad) {
      b.y = height - halfH - pad;
      b.vy = -Math.abs(b.vy) * 0.6;
    }
  }

  function refreshBubbleText() {
    for (const b of bubbles) {
      const dims = measureBubble(b.text, b.type);
      b.lines = dims.lines;
      b.w = dims.w;
      b.h = dims.h;
    }
  }

  function update() {
    const drift = reducedMotion ? 0.002 : 0.012;
    for (const b of bubbles) {
      if (!reducedMotion) applyPointerForce(b);

      b.vx += (Math.random() - 0.5) * drift;
      b.vy += (Math.random() - 0.5) * drift;
      b.vx *= 0.985;
      b.vy *= 0.985;
      b.va *= 0.98;

      const speedCap = reducedMotion ? 0.15 : 2.2;
      const speed = Math.hypot(b.vx, b.vy);
      if (speed > speedCap) {
        b.vx = (b.vx / speed) * speedCap;
        b.vy = (b.vy / speed) * speedCap;
      }

      b.x += b.vx;
      b.y += b.vy;
      b.angle += b.va;
      b.angle = Math.max(-0.4, Math.min(0.4, b.angle));

      keepInBounds(b);
    }

    for (let i = 0; i < bubbles.length; i++) {
      for (let j = i + 1; j < bubbles.length; j++) {
        resolveBubbleCollision(bubbles[i], bubbles[j]);
      }
    }
  }

  function drawBackground() {
    const grad = ctx.createLinearGradient(0, 0, 0, height);
    grad.addColorStop(0, "#0f1117");
    grad.addColorStop(0.5, "#10131c");
    grad.addColorStop(1, "#0a0c12");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, width, height);
  }

  function frame() {
    update();
    drawBackground();
    bubbles.sort((a, b) => a.opacity - b.opacity);
    for (const b of bubbles) drawBubble(b);
    rafId = requestAnimationFrame(frame);
  }

  async function dropMoreMessages() {
    const btn = document.getElementById("drop-messages-btn");
    if (btn) btn.disabled = true;

    let batch = [];
    try {
      const res = await fetch("/api/background-messages?limit=14");
      const data = await res.json();
      if (data.messages?.length) {
        batch = data.messages;
        messagePool = messagePool.concat(batch);
      }
    } catch (_) {
      /* use existing pool */
    }
    if (!batch.length) {
      batch = Array.from({ length: 10 }, () => pickMessage());
    }

    const count = Math.min(12, batch.length);
    for (let i = 0; i < count; i++) {
      const msg = batch[Math.floor(Math.random() * batch.length)];
      const b = createBubble(
        48 + Math.random() * Math.max(80, width - 96),
        -20 - Math.random() * 140 - i * 18,
        msg
      );
      b.vy = reducedMotion ? 0.4 : 1.8 + Math.random() * 2.2;
      b.vx = (Math.random() - 0.5) * 1.4;
      b.opacity = Math.min(0.75, b.opacity + 0.08);
      bubbles.push(b);
    }

    while (bubbles.length > 90) bubbles.shift();

    if (btn) btn.disabled = false;
  }

  function onPointerMove(e) {
    mouseX = e.clientX;
    mouseY = e.clientY;
  }

  function onPointerDown(e) {
    mouseX = e.clientX;
    mouseY = e.clientY;
    if (reducedMotion) return;

    for (const b of bubbles) {
      const dx = b.x - mouseX;
      const dy = b.y - mouseY;
      const dist = Math.hypot(dx, dy);
      if (dist < 160 && dist > 1) {
        const force = 3.5;
        b.vx += (dx / dist) * force;
        b.vy += (dy / dist) * force;
      }
    }

    if (bubbles.length < 65 && Math.random() < 0.35) {
      bubbles.push(createBubble(
        mouseX + (Math.random() - 0.5) * 40,
        mouseY + (Math.random() - 0.5) * 40
      ));
    }
  }

  window.addEventListener("resize", resize);
  window.addEventListener("pointermove", onPointerMove, { passive: true });
  window.addEventListener("pointerdown", onPointerDown, { passive: true });
  window.addEventListener("anonymous-mode-changed", (e) => {
    anonymousMode = !!e.detail?.enabled;
    refreshBubbleText();
  });

  const dropBtn = document.getElementById("drop-messages-btn");
  if (dropBtn) {
    dropBtn.addEventListener("click", () => dropMoreMessages());
  }

  resize();
  loadMessages();
  frame();

  window.addEventListener("beforeunload", () => cancelAnimationFrame(rafId));
})();
