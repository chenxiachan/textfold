// The page's moving parts, besides the lettering (ascii.js): the download for this computer, the clips that play when
// they are in view, and the copy buttons. With reduced motion the clips wait for a click.
(() => {
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const roots = [...document.querySelectorAll('[data-root]')];

  // ── the download for this computer: a Mac is taken for Apple silicon until the browser says Intel ──
  const ua = navigator.userAgent;
  const ipad = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
  const os = /Windows NT/.test(ua) ? 'win'
    : /Macintosh|Mac OS X/.test(ua) && !ipad && !/iPhone|iPod/.test(ua) ? 'mac-arm'
    : /Linux|X11|CrOS/.test(ua) && !/Android/.test(ua) ? 'py' : null;
  function pick(id) {
    for (const root of roots) {
      const c = JSON.parse(root.querySelector('script.cta-conf').textContent)[id];
      root.querySelectorAll('.card').forEach((card) => card.classList.toggle('is-you', card.dataset.os === id));
      if (!c) continue;
      const a = root.querySelector('[data-cta]');
      a.href = c.href;
      a.querySelector('[data-cta-t]').textContent = c.t;
      a.querySelector('[data-cta-s]').textContent = c.s;
    }
  }
  if (os) pick(os);
  if (os === 'mac-arm' && navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
    navigator.userAgentData.getHighEntropyValues(['architecture']).then((v) => { if (v.architecture === 'x86') pick('mac-x64'); }).catch(() => {});
  }

  // ── the clips: loaded and played when they come into view, paused when they leave ──
  const clips = [...document.querySelectorAll('.clips video, .ext-clip video')];
  if (still) clips.forEach((v) => { v.controls = true; v.preload = 'none'; v.src = v.dataset.src; });
  else if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((es) => es.forEach(({ target: v, isIntersecting }) => {
      if (isIntersecting) { if (!v.getAttribute('src')) v.src = v.dataset.src; v.play().catch(() => {}); } else v.pause();
    }), { threshold: 0.35 });
    clips.forEach((v) => io.observe(v));
  } else clips.forEach((v) => { v.src = v.dataset.src; v.autoplay = true; });

  // ── copy ──
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-copy]');
    if (!b || !navigator.clipboard || !navigator.clipboard.writeText) return;
    const was = b.textContent;
    navigator.clipboard.writeText(b.dataset.copy).then(() => { b.textContent = b.dataset.done; setTimeout(() => { b.textContent = was; }, 1600); }, () => {});
  });
})();
