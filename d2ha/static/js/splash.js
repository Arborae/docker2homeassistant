(() => {
  const config = window.d2haSplashConfig || {};
  const targetUrl = config.targetUrl || '/login';
  const healthUrl = config.healthUrl || '/api/health';
  const progressFill = document.getElementById('progressFill');
  const progressLabel = document.getElementById('progressLabel');
  const page = document.querySelector('.splash-page');

  if (!progressFill || !progressLabel) return;

  let progress = 0;
  let backendReady = false;
  let finalizing = false;

  const updateProgress = (value) => {
    const clamped = Math.min(Math.max(value, 0), 100);
    progressFill.style.width = `${clamped}%`;
    progressLabel.textContent = `${Math.round(clamped)}%`;
  };

  const fadeOutAndRedirect = () => {
    if (finalizing) return;
    finalizing = true;
    progress = 100;
    updateProgress(progress);
    if (page) {
      page.classList.add('splash-fade');
    }
    setTimeout(() => {
      window.location.assign(targetUrl);
    }, 450);
  };

  const pollHealth = async () => {
    try {
      const response = await fetch(healthUrl, { cache: 'no-store' });
      if (response.ok) {
        const payload = await response.json();
        backendReady = Boolean(payload.ready);
      }
    } catch (err) {
      // ignore transient network errors during startup
    }
  };

  const animate = () => {
    if (backendReady) {
      progress += (100 - progress) * 0.12;
    } else {
      const ceiling = 92;
      progress += (ceiling - progress) * 0.08 + 0.15;
    }

    updateProgress(progress);

    if (backendReady && progress >= 99.5) {
      fadeOutAndRedirect();
      return;
    }

    requestAnimationFrame(animate);
  };

  setInterval(pollHealth, 850);
  pollHealth();
  animate();
})();
