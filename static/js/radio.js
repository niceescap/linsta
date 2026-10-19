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
  const nextBlock = document.getElementById('next-track');
  const nextTitle = document.getElementById('next-title');
  const nextArtist = document.getElementById('next-artist');
  const nextCover = document.getElementById('next-cover');
  function updateNext(data = {}) {
    if (!nextBlock) return;
    nextBlock.hidden = !data.next_track_id;
    if (nextTitle) nextTitle.textContent = data.next_title || '';
    if (nextArtist) nextArtist.textContent = data.next_artist || '';
    if (nextCover) nextCover.src = data.next_cover_url || FALLBACK;
  }
  const SRC = audio.dataset.streamUrl || '/hls/stream.m3u8';
  const liveEnabled = audio.dataset.liveEnabled !== 'false';
  const previewMode = audio.dataset.preview === 'true';
  const metadataURL = audio.dataset.metadataUrl || '/now-playing';
  const FALLBACK = '/static/digger-icon.png';
  let hls = null;
  let retryTimer = null;
  let wantsPlayback = false;
  let ready = !previewMode;
  let attached = false;
  let metadata = null;
  let fetching = false;
  let previewMessage = 'Radio PREVIEW en attente de son état…';

  function message(text) {
    if (status) status.textContent = text;
  }

  function updatePlayIcon() {
    if (!playBtn) return;
    const playing = !audio.paused;
    playBtn.textContent = playing ? '⏸' : '▶';
    playBtn.setAttribute('aria-pressed', String(playing));
    playBtn.setAttribute('aria-label', playing ? 'Mettre en pause' : previewMode ? 'Écouter la PREVIEW' : 'Écouter le direct');
  }

  async function play() {
    try {
      await audio.play();
      message(previewMode ? 'Lecture PREVIEW / TEST.' : 'Lecture du direct.');
    } catch (_) {
      wantsPlayback = false;
      message('Lecture indisponible. Appuyez sur lecture pour réessayer.');
    }
    updatePlayIcon();
  }

  function retry() {
    if (!liveEnabled || !ready || (previewMode && !wantsPlayback)) return;
    message('Flux indisponible — nouvelle tentative…');
    if (retryTimer !== null) return;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (!ready || (previewMode && !wantsPlayback)) return;
      if (hls) { hls.destroy(); hls = null; }
      attached = false;
      attach();
      if (wantsPlayback) play();
    }, 3000);
  }

  function attach() {
    if (attached) return;
    attached = true;
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
    if (!liveEnabled) return;
    if (previewMode && !ready) { message(previewMessage); return; }
    if (audio.paused) {
      wantsPlayback = true;
      if (previewMode) attach(); // Same user gesture as play(): Safari native HLS.
      play();
    } else {
      wantsPlayback = false;
      audio.pause();
    }
  });
  audio.addEventListener('play', updatePlayIcon);
  audio.addEventListener('pause', updatePlayIcon);
  audio.addEventListener('timeupdate', renderProgress);
  audio.addEventListener('seeked', () => { renderProgress(); if (previewMode) refresh(); });
  nextCover?.addEventListener('error', () => { if (nextCover.src !== FALLBACK) nextCover.src = FALLBACK; });
  audio.addEventListener('error', retry);
  volumeInput?.addEventListener('input', event => { audio.volume = event.target.value / 100; });
  audio.volume = 0.8;
  if (cover) cover.addEventListener('error', () => {
    if (cover.getAttribute('src') !== FALLBACK) cover.src = FALLBACK;
  });

  function playbackDate() {
    try {
      if (hls && hls.playingDate) {
        const date = hls.playingDate.getTime() / 1000;
        if (Number.isFinite(date)) return date;
      }
      if (typeof audio.getStartDate === 'function' && Number.isFinite(audio.currentTime)) {
        const start = audio.getStartDate().getTime() / 1000;
        if (Number.isFinite(start)) return start + audio.currentTime;
      }
    } catch (_) {}
    return null; // Never substitute the browser wall clock for audible time.
  }
  function formatTime(value) {
    if (!Number.isFinite(value)) return '--:--';
    const seconds = Math.floor(Math.max(0, value));
    return String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
  }
  function renderProgress() {
    const duration = metadata ? Number(metadata.duration) : NaN;
    const at = playbackDate();
    const known = !previewMode || (metadata?.timing_available === true && at !== null);
    const elapsed = known && metadata ? (previewMode ? at - metadata.playback_started_at : Number(metadata.elapsed)) : NaN;
    const valid = Number.isFinite(duration) && duration > 0;
    const clamped = valid && Number.isFinite(elapsed) ? Math.max(0, Math.min(duration, elapsed)) : NaN;
    for (const [id, value] of [['track-elapsed', clamped], ['track-duration', valid ? duration : NaN],
                              ['track-remaining', valid ? duration - clamped : NaN]]) {
      const element = document.getElementById(id);
      if (element) element.textContent = formatTime(value);
    }
    if (progressFill) progressFill.style.width = valid && Number.isFinite(clamped) ? clamped / duration * 100 + '%' : '0%';
  }

  async function refresh() {
    if ((previewMode && !liveEnabled) || fetching) return;
    fetching = true;
    try {
      const at = previewMode ? playbackDate() : null;
      const url = at === null ? metadataURL : metadataURL + '?at=' + encodeURIComponent(at);
      const response = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
      if (!response.ok) {
        if (previewMode) unavailable(response.status === 401 || response.status === 403
          ? 'Reconnectez-vous pour écouter la PREVIEW.' : 'État PREVIEW indisponible.');
        return;
      }
      const data = await response.json();
      if (previewMode) {
        ready = data.available === true;
        previewMessage = data.message || 'Radio PREVIEW arrêtée.';
        if (!ready) unavailable(previewMessage);
        else if (!wantsPlayback) message('Appuyez sur Play pour écouter la PREVIEW');
      }
      updateNext(previewMode && ready && data.track_id ? data : {});
      if (!data.track_id) {
        metadata = null;
        renderProgress();
        titleEl.textContent = data.message || 'En attente du flux…';
        artistEl.textContent = '';
        if (progressFill) progressFill.style.width = '0%';
        if (cover) { cover.src = FALLBACK; delete cover.dataset.current; }
        return;
      }
      titleEl.textContent = data.title || '—';
      artistEl.textContent = data.artist ? `— ${data.artist}` : '';
      const source = previewMode ? data.cover_url : '/media/cover/' + data.track_id;
      if (cover && cover.dataset.current !== source) {
        cover.src = source;
        cover.dataset.current = source;
      }
      metadata = data;
      renderProgress();
    } catch (_) {
      if (previewMode) unavailable('État PREVIEW indisponible.');
      // Preserve the historical public player's metadata-failure behavior.
    } finally { fetching = false; }
  }

  function unavailable(text) {
    metadata = null;
    renderProgress();
    updateNext();
    previewMessage = text;
    ready = false;
    wantsPlayback = false;
    if (retryTimer !== null) { clearTimeout(retryTimer); retryTimer = null; }
    if (attached) {
      audio.pause();
      if (hls) { hls.destroy(); hls = null; }
      audio.removeAttribute('src');
      audio.load();
      attached = false;
    }
    titleEl.textContent = text;
    artistEl.textContent = '';
    if (cover) { cover.src = FALLBACK; delete cover.dataset.current; }
    if (progressFill) progressFill.style.width = '0%';
    message(text);
    updatePlayIcon();
  }

  if (liveEnabled && !previewMode) {
    attach();
  } else if (!liveEnabled) {
    if (playBtn) playBtn.disabled = true;
    message(previewMode ? 'Connectez-vous avec un compte de test pour écouter la PREVIEW.' : 'Le direct n’est pas encore ouvert.');
  } else {
    message(previewMessage);
  }
  updatePlayIcon();
  refresh();
  setInterval(refresh, previewMode ? 1000 : 5000);
})();
