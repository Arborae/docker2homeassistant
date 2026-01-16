// Simple animated tech-like background for the auth / landing page.
// No external dependencies.

(function () {
  const canvas = document.getElementById("authCanvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  let width = window.innerWidth;
  let height = window.innerHeight;

  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  canvas.style.width = width + "px";
  canvas.style.height = height + "px";
  ctx.scale(dpr, dpr);

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  window.addEventListener("resize", resize);

  const nodes = [];
  const NODE_COUNT = 40;

  for (let i = 0; i < NODE_COUNT; i++) {
    nodes.push({
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.35,
      vy: (Math.random() - 0.5) * 0.35,
    });
  }

  function step() {
    ctx.clearRect(0, 0, width, height);

    // Background gradient
    const g = ctx.createRadialGradient(
      width * 0.15,
      height * 0.1,
      0,
      width * 0.5,
      height * 0.8,
      Math.max(width, height)
    );
    // --accent-primary: #31c4ff (49, 196, 255)
    g.addColorStop(0, "rgba(49, 196, 255, 0.12)");
    // --bg-base: #0b1020 (11, 16, 32)
    g.addColorStop(0.4, "rgba(11, 16, 32, 0.95)");
    g.addColorStop(1, "rgba(5, 8, 16, 1)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, width, height);

    // Animated nodes + connections
    ctx.lineWidth = 1;

    for (let i = 0; i < NODE_COUNT; i++) {
      const n = nodes[i];
      n.x += n.vx;
      n.y += n.vy;

      if (n.x < 0 || n.x > width) n.vx *= -1;
      if (n.y < 0 || n.y > height) n.vy *= -1;

      ctx.beginPath();
      ctx.arc(n.x, n.y, 2.3, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(154, 167, 189, 0.5)"; // --muted #9aa7bd
      ctx.fill();
    }

    for (let i = 0; i < NODE_COUNT; i++) {
      for (let j = i + 1; j < NODE_COUNT; j++) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist2 = dx * dx + dy * dy;
        const maxDist = 220;
        if (dist2 < maxDist * maxDist) {
          const alpha = 1 - Math.sqrt(dist2) / maxDist;
          // --accent-primary: #31c4ff
          ctx.strokeStyle = "rgba(49, 196, 255," + (alpha * 0.5).toFixed(3) + ")";
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    requestAnimationFrame(step);
  }

  step();
})();
