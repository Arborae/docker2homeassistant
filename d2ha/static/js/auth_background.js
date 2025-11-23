// Auth animated tech background (login + wizard)
(() => {
  const canvas = document.getElementById('authCanvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let width = 0;
  let height = 0;
  let gradient;
  let particles = [];

  const palette = () => {
    const styles = getComputedStyle(document.body);
    return {
      bg1: styles.getPropertyValue('--auth-bg-1').trim() || '#0a1024',
      bg2: styles.getPropertyValue('--auth-bg-2').trim() || '#0f1b3f',
      point: styles.getPropertyValue('--auth-point').trim() || '#8ee8ff',
      line: styles.getPropertyValue('--auth-line').trim() || '#4ec9ff',
      glow: styles.getPropertyValue('--auth-glow').trim() || 'rgba(78,201,255,0.45)',
    };
  };

  const randomBetween = (min, max) => Math.random() * (max - min) + min;

  const createParticle = () => ({
    x: Math.random() * width,
    y: Math.random() * height,
    radius: randomBetween(1.2, 2.6),
    vx: randomBetween(-0.2, 0.2),
    vy: randomBetween(-0.2, 0.2),
  });

  const resize = () => {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const colors = palette();
    gradient = ctx.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, colors.bg1);
    gradient.addColorStop(1, colors.bg2);

    const targetCount = Math.max(40, Math.min(80, Math.floor(width / 18)));
    if (particles.length === 0) {
      particles = Array.from({ length: targetCount }, createParticle);
    } else if (particles.length < targetCount) {
      particles = particles.concat(Array.from({ length: targetCount - particles.length }, createParticle));
    } else if (particles.length > targetCount) {
      particles = particles.slice(0, targetCount);
    }
  };

  const drawBackground = () => {
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);
  };

  const drawConnections = () => {
    const { line, glow } = palette();
    const maxDistance = Math.min(180, Math.max(120, width * 0.12));
    for (let i = 0; i < particles.length; i += 1) {
      for (let j = i + 1; j < particles.length; j += 1) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.hypot(dx, dy);
        if (dist <= maxDistance) {
          const alpha = 1 - dist / maxDistance;
          ctx.strokeStyle = line;
          ctx.globalAlpha = alpha * 0.55;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
          ctx.globalAlpha = 1;
          if (alpha > 0.6) {
            ctx.shadowBlur = 10;
            ctx.shadowColor = glow;
            ctx.stroke();
            ctx.shadowBlur = 0;
          }
        }
      }
    }
  };

  const drawParticles = () => {
    const { point, glow } = palette();
    particles.forEach((p) => {
      ctx.beginPath();
      ctx.fillStyle = point;
      ctx.shadowBlur = 18;
      ctx.shadowColor = glow;
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    });
  };

  const updateParticles = () => {
    particles.forEach((p) => {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0 || p.x > width) p.vx *= -1;
      if (p.y < 0 || p.y > height) p.vy *= -1;
    });
  };

  const render = () => {
    drawBackground();
    drawConnections();
    drawParticles();
    updateParticles();
    requestAnimationFrame(render);
  };

  resize();
  window.addEventListener('resize', resize);
  render();
})();
