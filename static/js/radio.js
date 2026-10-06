(() => {
  const audio = document.getElementById('player');
  const playBtn = document.getElementById('playBtn');
  const cover = document.getElementById('np-cover');
  const titleEl = document.getElementById('np-title');
  const artistEl = document.getElementById('np-artist');
  const progressFill = document.getElementById('progressFill');
  const volumeInput = document.getElementById('volumeInput');
  const SRC = '/hls/stream.m3u8';

  let hls = null;
  let isPlaying = false;

  function attach() {
    if (audio.canPlayType('application/vnd.apple.mpegurl')) {
      audio.src = SRC;
    } else if (window.Hls && Hls.isSupported()) {
      hls = new Hls({ liveSyncDurationCount:3, lowLatencyMode:false });
      hls.loadSource(SRC);
      hls.attachMedia(audio);
      hls.on(Hls.Events.ERROR, () => { titleEl.textContent = 'Flux indisponible'; });
    } else {
      titleEl.textContent = 'HLS non supporté';
    }
    audio.volume = 0.8;
    if (volumeInput) volumeInput.value = 80;
  }

  playBtn?.addEventListener('click', () => {
    if (audio.paused) { audio.play(); isPlaying = true; }
    else { audio.pause(); isPlaying = false; }
    updatePlayIcon();
  });

  function updatePlayIcon() {
    if (!playBtn) return;
    playBtn.textContent = isPlaying ? '⏸' : '▶';
  }

  audio?.addEventListener('play', () => { isPlaying = true; updatePlayIcon(); });
  audio?.addEventListener('pause', () => { isPlaying = false; updatePlayIcon(); });

  volumeInput?.addEventListener('input', e => { audio.volume = e.target.value / 100; });

  async function refresh() {
    try {
      const r = await fetch('/now-playing', {cache:'no-store'});
      const d = await r.json();
      if (!d.track_id) {
        titleEl.textContent = d.message || 'En attente du flux…';
        artistEl.textContent = '';
        return;
      }
      const pad = n => String(n).padStart(2,'0');
      const pos = Math.floor(d.elapsed || 0);
      const dur = Math.floor(d.duration || 0);
      const fmt = s => `${pad(Math.floor(s/60))}:${pad(s%60)}`;
      titleEl.textContent = d.title || '—';
      artistEl.textContent = d.artist ? `— ${d.artist}` : '';
      if (cover && d.track_id) {
        const newSrc = '/media/cover/' + d.track_id + '?t=' + Date.now();
        if (cover.dataset.current !== newSrc) {
          cover.src = newSrc;
          cover.dataset.current = newSrc;
        }
      }
      const pct = dur ? Math.min(100, (pos / dur) * 100) : 0;
      if (progressFill) progressFill.style.width = pct + '%';
    } catch(e){}
  }
  attach();
  refresh();
  setInterval(refresh, 5000);
  // initial play state
  setTimeout(() => { if (audio && audio.paused) { audio.play().catch(()=>{}); } }, 500);
})();
