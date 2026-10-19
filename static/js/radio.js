(() => {
  const audio = document.getElementById('player');
  if (!audio) return;
  const playBtn = document.getElementById('playBtn');
  const cover = document.getElementById('np-cover');
  const titleEl = document.getElementById('np-title');
  const artistEl = document.getElementById('np-artist');
  const progressFill = document.getElementById('progressFill');
  const volumeInput = document.getElementById('volumeInput');
  const status = document.getElementById('player-status');
  const SRC = audio.dataset.streamUrl || '/hls/stream.m3u8';
  const FALLBACK = '/static/digger-icon.png';
  let hls = null;
  let retryTimer = null;
  let wantsPlayback = false;

  function message(text) {
    if (status) status.textContent = text;
  }

  function updatePlayIcon() {
    if (!playBtn) return;
    const playing = !audio.paused;
    playBtn.textContent = playing ? '⏸' : '▶';
    playBtn.setAttribute('aria-pressed', String(playing));
    playBtn.setAttribute('aria-label', playing ? 'Mettre en pause' : 'Écouter le direct');
  }

  async function play() {
    try {
      await audio.play();
      message('Lecture du direct.');
    } catch (_) {
      wantsPlayback = false;
      message('Lecture indisponible. Appuyez sur lecture pour réessayer.');
    }
    updatePlayIcon();
  }

  function retry() {
    message('Flux indisponible — nouvelle tentative…');
    if (retryTimer !== null) return;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (hls) { hls.destroy(); hls = null; }
      attach();
      if (wantsPlayback) play();
    }, 3000);
  }

  function attach() {
    if (audio.canPlayType('application/vnd.apple.mpegurl')) {
      audio.src = SRC;
    } else if (window.Hls && Hls.isSupported()) {
      hls = new Hls({ liveSyncDurationCount: 3, lowLatencyMode: false });
      hls.loadSource(SRC);
      hls.attachMedia(audio);
      hls.on(Hls.Events.ERROR, (_, data) => { if (data.fatal) retry(); });
    } else {
      message('HLS non supporté par ce navigateur.');
    }
  }

  playBtn?.addEventListener('click', () => {
    if (audio.paused) {
      wantsPlayback = true;
      play();
    } else {
      wantsPlayback = false;
      audio.pause();
    }
  });
  audio.addEventListener('play', updatePlayIcon);
  audio.addEventListener('pause', updatePlayIcon);
  audio.addEventListener('error', retry);
  volumeInput?.addEventListener('input', event => { audio.volume = event.target.value / 100; });
  audio.volume = 0.8;
  if (cover) cover.addEventListener('error', () => {
    if (cover.getAttribute('src') !== FALLBACK) cover.src = FALLBACK;
  });

  async function refresh() {
    try {
      const response = await fetch('/now-playing', { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      if (!data.track_id) {
        titleEl.textContent = data.message || 'En attente du flux…';
        artistEl.textContent = '';
        if (progressFill) progressFill.style.width = '0%';
        if (cover) { cover.src = FALLBACK; delete cover.dataset.current; }
        return;
      }
      titleEl.textContent = data.title || '—';
      artistEl.textContent = data.artist ? `— ${data.artist}` : '';
      const source = '/media/cover/' + data.track_id;
      if (cover && cover.dataset.current !== source) {
        cover.src = source;
        cover.dataset.current = source;
      }
      const duration = Number(data.duration) || 0;
      const elapsed = Math.max(0, Number(data.elapsed) || 0);
      const percent = duration > 0 ? Math.min(100, elapsed / duration * 100) : 0;
      if (progressFill) progressFill.style.width = percent + '%';
    } catch (_) { /* A failed metadata request must not interrupt audio. */ }
  }

  attach();
  updatePlayIcon();
  refresh();
  setInterval(refresh, 5000);
})();
