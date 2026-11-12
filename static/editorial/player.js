/* One audio, one HLS instance, two control surfaces. DEV private endpoints only. */
(() => {
  const audio = document.querySelector('#editorial-audio');
  if (!audio || audio.dataset.initialized) return;
  audio.dataset.initialized = 'true';
  const enabled = audio.dataset.enabled === 'true';
  const defaultCover = '/static/editorial/digger-logo-transparent.png';
  const coverSource = url => !url || url.endsWith('/static/digger-icon.png') ? defaultCover : url;
  function setCover(el, src) {
    if (el.getAttribute('src') === src || el.dataset.coverRequest === src) return;
    el.dataset.coverRequest = src;
    el.src = src;
    if (src.startsWith('/preview-radio/tracks/')) {
      fetch(src, {credentials:'same-origin'}).then(response => {
        if (el.dataset.coverRequest === src && new URL(response.url).pathname === '/static/digger-icon.png') {
          el.src = defaultCover;
          el.alt = 'Digger Radio — pochette indisponible';
        }
      }).catch(() => {});
    }
  }
  const stream = '/preview-radio/hls/stream.m3u8';
  let hls = null, attached = false, available = false, fetching = false;
  let intent = enabled, attempted = false, blocked = false, denied = false;
  let state = enabled ? 'checking' : 'login', data = {}, retryTimer = null, retryReason = null, epoch = 0;
  let lastAdvance = performance.now(), lastTime = NaN, failures = 0, joinPending = false;
  let frozen = null, lastMetadataRequest = null, lastMetadataResponse = null;
  const events = [], eventCounts = {};
  let invalidSince=null, invalidSamples=0, recoveredClocks=0, lastSeek=null;
  // A live media position is measured in seconds, never DBL_MAX. This ceiling
  // also rejects overflowing/indefinite native TimeRanges (over 31 years).
  const mediaSecond = value => Number.isFinite(value) && value >= 0 && value < 1e9;
  function mediaRanges(value) {
    const result=[];
    try { for(let i=0;i<(value?.length || 0);i++) {
      const start=value.start(i),end=value.end(i);
      if(mediaSecond(start) && mediaSecond(end) && end>start)result.push([start,end]);
    }} catch (_) {}
    return result;
  }
  function position() {
    const value=audio.currentTime,seekable=mediaRanges(audio.seekable),buffered=mediaRanges(audio.buffered);
    const contains=ranges=>ranges.some(([start,end])=>value>=start-.6 && value<=end+.6);
    const valid=mediaSecond(value) && (!seekable.length || contains(seekable) || contains(buffered));
    return {value,valid,seekable,buffered,reason:!mediaSecond(value)?'invalid-media-time':valid?null:'outside-media-ranges'};
  }
  function samplePosition() {
    const p=position();
    if(!p.valid) { invalidSamples++;if(invalidSince===null)invalidSince=performance.now();lastTime=NaN; }
    else if(invalidSince!==null) { recoveredClocks++;invalidSince=null;lastAdvance=performance.now();lastTime=NaN; }
    return p;
  }
  function snapshot() {
    const ranges = value => Array.from({length:value?.length || 0},(_,i)=>[value.start(i),value.end(i)]);
    return {at:new Date().toISOString(),state,available,intent,attached,transport:hls?'hls.js':'native',
      currentTime:audio.currentTime,paused:audio.paused,readyState:audio.readyState,networkState:audio.networkState,
      error:audio.error?.code || null,buffered:ranges(audio.buffered),seekable:ranges(audio.seekable),
      audibleAt:clock(),clockProbe:probeClock(),frozen:frozen?{at:frozen.at,mediaTime:frozen.mediaTime}:null,
      metadataRequest:lastMetadataRequest,metadataResponse:lastMetadataResponse,
      metadata:{basis:data.metadata_basis,status:data.metadata_status,at:data.metadata_at,
        start:data.playback_started_at,end:data.playback_ended_at,publishedStart:data.published_start,
        publishedEnd:data.published_end,trackId:data.track_id,type:data.type}};
  }
  function note(event, detail) { eventCounts[event]=(eventCounts[event]||0)+1;events.push({...snapshot(),event,detail}); if(events.length>400)events.shift(); }
  const diagnosticStarted=performance.now();
  window.diggerRadioDiagnostics = () => ({version:'safari-sentinel-2026-11-11',userAgent:navigator.userAgent,...snapshot(),
    measurement:{elapsedSeconds:(performance.now()-diagnosticStarted)/1000,
      attachments:eventCounts.attach||0,detachments:eventCounts.detach||0,stalledNotifications:eventCounts.stalled||0,
      lastProgressAgeSeconds:position().valid?(performance.now()-lastAdvance)/1000:null,
      progressObservable:position().valid,invalidMediaSamples:invalidSamples,recoveredClocks,
      invalidMediaSeconds:invalidSince===null?0:(performance.now()-invalidSince)/1000,lastSeek},events:[...events]});
  window.diggerRadioClockDiagnostic = () => {
    const d=window.diggerRadioDiagnostics();
    return {version:d.version,userAgent:d.userAgent,at:d.at,state:d.state,transport:d.transport,
      paused:d.paused,currentTime:d.currentTime,audibleAt:d.audibleAt,clockProbe:d.clockProbe,
      frozen:d.frozen,metadata:d.metadata,metadataRequest:d.metadataRequest,
      metadataResponse:d.metadataResponse,buffered:d.buffered,seekable:d.seekable,
      measurement:d.measurement};
  };
  function cancelRetry() { if(retryTimer!==null)clearTimeout(retryTimer);retryTimer=null;retryReason=null; }
  function advancing() {
    const p=samplePosition();
    if(p.valid && (!Number.isFinite(lastTime) || p.value>lastTime+.05)) {
      lastTime=audio.currentTime;lastAdvance=performance.now();failures=0;cancelRetry();
      if(intent && !audio.paused && state!=='playing'){state='playing';render();}
    }
  }
  const texts = {checking:'Vérification du flux…', login:'Connectez-vous avec un compte de test pour écouter la PREVIEW.',
    unavailable:'Flux momentanément indisponible.', loading:'Chargement du direct…', reconnecting:'Reconnexion au direct…',
    blocked:'Activer le son', paused:'En pause · reprendre rejoint le direct.', playing:'Vous écoutez le direct privé.',
    muted:'Son coupé', unsupported:'HLS non supporté par ce navigateur.', expired:'Session expirée · reconnectez-vous.'};
  const all = selector => document.querySelectorAll(selector);
  const setText = (selector, text) => all(selector).forEach(el => { el.textContent = text; });
  function probeClock() {
    const result={mediaTime:audio.currentTime,nativeMethod:typeof audio.getStartDate,nativeOrigin:null,
      nativeValueType:null,nativeError:null,hlsAt:null,computedAt:null,source:'unavailable',
      position:position(),publishedStart:data.published_start,publishedEnd:data.published_end};
    try {
      const date = hls?.playingDate;
      if (position().valid && Number.isFinite(date?.getTime())) {result.hlsAt=date.getTime()/1000;result.computedAt=result.hlsAt;result.source='hls.js';}
      if (audio.getStartDate) {
        const native=audio.getStartDate();result.nativeValueType=Object.prototype.toString.call(native);
        const origin=native?.getTime?.()/1000;
        result.nativeOrigin=Number.isFinite(origin)?origin:null;
        if(result.computedAt===null && Number.isFinite(origin) && origin>0 && position().valid){
          result.computedAt=origin+audio.currentTime;result.source='native-getStartDate';
        }
      }
    } catch (error) { result.nativeError=error.name || 'clock-error'; }
    return result;
  }
  function clock() {
    if(frozen)return frozen.at;
    return probeClock().computedAt ?? NaN;
  }
  function displayedData() {
    if(frozen)return frozen.data;
    if(available) {
      const at=clock();
      // AVFoundation rounds getStartDate's origin to whole seconds. Near an
      // output boundary, prefer a short waiting state to the adjacent item.
      const uncertainty=hls?0:.6;
      const outside = data.metadata_basis==='audible-clock' &&
        (!Number.isFinite(at) || at-uncertainty<data.playback_started_at ||
         (Number.isFinite(data.playback_ended_at) && at+uncertainty>=data.playback_ended_at));
      if(!Number.isFinite(at) || outside || data.metadata_basis==='published-live-edge')
        return {...data,track_id:null,type:null,title:'Synchronisation du direct…',artist:null,
          cover_url:null,timing_available:false};
    }
    return data;
  }
  function format(value) {
    return Number.isFinite(value) ? `${Math.floor(Math.max(0,value)/60).toString().padStart(2,'0')}:${Math.floor(Math.max(0,value)%60).toString().padStart(2,'0')}` : '--:--';
  }
  function render() {
    const muted = audio.muted || audio.volume === 0;
    const audible = state === 'playing' && !audio.paused;
    const label = audible && muted ? texts.muted : texts[state];
    setText('[data-status], [data-mini-status]', label || texts.unavailable);
    setText('[data-live]', available ? 'ON AIR' : 'DIRECT INDISPONIBLE');
    document.querySelector('.live-card').dataset.available = String(available);
    const current=displayedData();
    setText('[data-title]', current.title || 'En attente du direct');
    setText('[data-artist]', current.type === 'jingle' ? 'Identifiant de station' : current.artist || 'Digger Radio');
    setText('.now-info .eyebrow', current.type === 'jingle' ? 'JINGLE · DIGGER RADIO' : 'À L’ANTENNE');
    all('[data-cover]').forEach(el => {
      const src = coverSource(current.cover_url);
      setCover(el, src);
      el.alt = src !== defaultCover ? `Pochette de ${current.title || 'la sélection'}` : 'Digger Radio — pochette indisponible';
    });
    all('[data-track-link]').forEach(el => {
      if (current.track_id) { el.href = `/morceaux/${current.track_id}`; el.setAttribute('aria-label',`Fiche de ${current.title}`); }
      else el.removeAttribute('href');
    });
    all('[data-play]').forEach(el => {
      el.disabled = !enabled || denied || (!available && audio.paused);
      el.textContent = blocked ? 'Activer le son' : !audio.paused ? 'Ⅱ' : '▶';
      el.classList.toggle('needs-gesture', blocked);
      el.setAttribute('aria-label', blocked ? 'Activer le son' : !audio.paused ? 'Mettre en pause' : 'Rejoindre le direct');
      el.setAttribute('aria-pressed',String(!audio.paused));
    });
    all('[data-mute]').forEach(el => { el.textContent = muted ? 'Muet' : 'Son'; el.setAttribute('aria-pressed',String(muted)); el.setAttribute('aria-label',muted ? 'Activer le son' : 'Couper le son'); });
    all('[data-volume]').forEach(el => { el.value = String(Math.round(audio.volume*100)); });
    const next = available && Number.isInteger(data.next_track_id);
    all('[data-next-empty]').forEach(el => { el.hidden = next; });
    all('[data-next-link]').forEach(el => { el.hidden = !next; if (next) el.href = `/morceaux/${data.next_track_id}`; else el.removeAttribute('href'); });
    setText('[data-next-title]', next ? data.next_title || 'Titre non renseigné' : '');
    setText('[data-next-artist]', next ? data.next_artist || '' : '');
    all('[data-next-cover]').forEach(el => { if (next) { const src=coverSource(data.next_cover_url); setCover(el, src); } });
    renderHistory();
    progress();
  }
  function renderHistory() {
    const history = Array.isArray(data.history) ? data.history : [];
    all('[data-history-empty]').forEach(el => { el.hidden = available && history.length > 0; el.textContent = data.history_available ? 'Les premiers passages apparaîtront après leur diffusion.' : 'L’historique d’antenne n’est pas encore disponible.'; });
    all('[data-history]').forEach(container => {
      const signature=JSON.stringify([available,history]);
      if(container.dataset.historySignature===signature)return;
      container.dataset.historySignature=signature;
      container.replaceChildren();
      if (!available) return;
      for (const item of history) {
        const row = document.createElement(item.type === 'track' ? 'a' : 'div');
        row.className = 'broadcast-item';
        if (item.type === 'track' && Number.isInteger(item.track_id)) { row.href = '/morceaux/' + item.track_id; row.dataset.nav = ''; }
        if (item.type === 'track' && item.cover_url) { const image = document.createElement('img'); image.src = item.cover_url; image.alt = ''; image.loading = 'lazy'; row.append(image); }
        const info = document.createElement('div');
        const title = document.createElement('strong'); title.textContent = item.title || 'Digger Radio'; info.append(title);
        const artist = document.createElement('span'); artist.textContent = item.type === 'jingle' ? 'Jingle · identifiant de station' : item.artist || ''; info.append(artist); row.append(info);
        if (Number.isFinite(item.started_at)) { const time = document.createElement('time'); const date = new Date(item.started_at*1000); time.dateTime = date.toISOString(); time.textContent = date.toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'}); time.title = 'Début dans le flux HLS · heure locale'; row.append(time); }
        container.append(row);
      }
    });
  }
  function progress() {
    const current=displayedData();
    const elapsed = current.timing_available === true ? clock()-current.playback_started_at : NaN;
    const duration = Number(current.duration);
    const valid = available && Number.isFinite(duration) && duration > 0 && Number.isFinite(elapsed);
    const value = valid ? Math.max(0, Math.min(duration,elapsed)) : NaN;
    setText('[data-elapsed]',format(value));
    setText('[data-duration], [data-remaining]',duration>0 && Number.isFinite(duration) && current.type ? format(duration) : '--:--');
    all('[data-progress]').forEach(el => { el.style.width = valid ? `${value/duration*100}%` : '0%'; });
  }
  function joinLive() {
    const ranges=mediaRanges(audio.seekable);
    if(!ranges.length)return;
    const [start,end]=ranges[ranges.length-1];
    const target=mediaSecond(hls?.liveSyncPosition)?hls.liveSyncPosition:end-12;
    // Do not turn a native indefinite seekable endpoint into a DBL_MAX seek.
    // Seeking is only requested on initial join or explicit resume, never by polling.
    if(!mediaSecond(target) || target<start || target>end)return;
    try {
      audio.currentTime=target;
      lastSeek={target,readback:audio.currentTime,validReadback:position().valid};
      note('join-live',lastSeek);
    } catch(error) { lastSeek={target,error:error.name};note('join-live-failed',lastSeek); }
  }
  function detach() {
    note('detach');
    epoch++;
    lastTime=NaN;joinPending=false;
    frozen=null;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
    audio.pause();
    if (hls) hls.destroy();
    hls = null;
    if (attached) { audio.removeAttribute('src'); audio.load(); }
    attached = false;
  }
  function attach() {
    if (attached) return true;
    const nativeSupported = audio.canPlayType('application/vnd.apple.mpegurl');
    // Native HLS needs its playback-date API for honest metadata alignment.
    // Chromium may advertise HLS without that clock; use the existing MSE adapter there.
    if (nativeSupported && typeof audio.getStartDate === 'function') audio.src = stream;
    else if (window.Hls?.isSupported()) {
      hls = new window.Hls({liveSyncDurationCount:3,liveMaxLatencyDurationCount:6,lowLatencyMode:false});
      hls.loadSource(stream);
      hls.attachMedia(audio);
      hls.on(window.Hls.Events.ERROR, (_, detail) => { if (detail.fatal) retry(); });
    } else if (nativeSupported) {
      audio.src = stream; // Listening still works; metadata stays explicitly unsynchronized.
    } else { state='unsupported'; intent=false; render(); return false; }
    attached = true;lastAdvance=performance.now();lastTime=NaN;joinPending=true;note('attach');
    return true;
  }
  async function play() {
    if (!available || !enabled || denied) return;
    intent = true;
    if(frozen)epoch++;
    frozen=null;
    blocked = false;
    state = 'loading';
    if (!attach()) return;
    const token = epoch;
    if(!joinPending)joinLive();
    lastAdvance=performance.now();lastTime=audio.currentTime;
    render();
    try { await audio.play(); }
    catch (error) {
      if (token !== epoch || !intent) return;
      if (error.name === 'NotAllowedError') { blocked=true; intent=false; state='blocked'; render(); }
      else retry();
    }
  }
  function retry(reason='media-error') {
    if (!intent || !available || denied || retryTimer!==null) return;
    retryReason=reason;
    state='reconnecting';note('retry-scheduled',String(reason));render();
    const delay=Math.min(30000,3000*2**Math.min(failures++,4));
    retryTimer=setTimeout(() => {
      retryTimer=null;retryReason=null;
      // Re-sample the media clock: Safari may throttle timeupdate in background tabs.
      if(reason==='no-progress') {
        advancing();
        if(!position().valid) {note('watchdog-clock-unobservable');return;}
      }
      // A recovered native buffer must never be thrown away by a stale timer.
      if(reason==='no-progress' && performance.now()-lastAdvance<12000)return;
      detach(); if(intent && available && !denied)play();
    },delay);
  }
  setInterval(()=>{
    if(attached && intent && !audio.paused)advancing();
    if(attached && intent && !hls && !position().valid) {
      if(retryReason==='no-progress')cancelRetry();
      return; // An unreadable clock cannot prove that audible playback froze.
    }
    if(attached && intent && !denied && !blocked && !audio.seeking && performance.now()-lastAdvance>=15000)retry('no-progress');
  },1000);
  async function refresh() {
    if (!enabled || denied || fetching) return;
    fetching=true;
    const controller = new AbortController();
    const timeout=setTimeout(() => controller.abort(),8000);
    try {
      samplePosition();
      const at = attached ? clock() : NaN;
      const requestEpoch=epoch;
      lastMetadataRequest={at:Number.isFinite(at)?at:null,paused:audio.paused,probe:probeClock()};
      const response = await fetch('/preview-radio/now-playing'+(Number.isFinite(at) ? '?at='+encodeURIComponent(at) : ''),
        {credentials:'same-origin',cache:'no-store',signal:controller.signal});
      lastMetadataResponse={status:response.status};
      if (response.status===401 || response.status===403) {
        denied=true; intent=false; available=false; data={}; detach(); state='expired'; render(); return;
      }
      if (!response.ok) throw new Error('Metadata unavailable');
      const result=await response.json();
      lastMetadataResponse={status:response.status,basis:result.metadata_basis,reason:result.metadata_status,
        at:result.metadata_at,start:result.playback_started_at,end:result.playback_ended_at,
        publishedStart:result.published_start,publishedEnd:result.published_end,
        nextBasis:result.next_metadata_basis,receivedAt:probeClock().computedAt};
      const wasAvailable=available;
      available=result.available===true;
      // A response sampled before pause/resume must not overwrite the new
      // listener position. Health/authentication still apply immediately.
      if(requestEpoch===epoch)data=available ? result : {};
      note('metadata',result.metadata_status || result.metadata_basis);
      if (!available) {
        note('health-unavailable',result.status);
        if(result.status==='inactive'||result.status==='failed')detach();
        state=audio.paused?'unavailable':'checking';
      }
      else if (!attempted) { attempted=true; if (intent) play(); }
      else if (!wasAvailable && intent && (audio.paused || !attached)) play();
      else if(intent && !audio.paused && audio.readyState>=3)state='playing';
      else if (!intent && !blocked) state='paused';
      render();
    } catch (_) {
      note('metadata-error');
      available=false;data={};state=audio.paused?'unavailable':'checking';render();
    } finally { clearTimeout(timeout); fetching=false; }
  }
  document.addEventListener('click',event => {
    if (event.target.closest('[data-play]')) {
      if (denied || !enabled || (!available && audio.paused)) return;
      if (!audio.paused || state==='loading' || state==='reconnecting') {
        pauseListener();
      } else play();
    }
    if (event.target.closest('[data-mute]')) { audio.muted=!(audio.muted || audio.volume===0); if (!audio.muted && !audio.volume) audio.volume=.8; render(); }
  });
  document.addEventListener('input',event => { if (event.target.matches('[data-volume]')) { audio.volume=Number(event.target.value)/100; if (audio.volume>0) audio.muted=false; render(); } });
  document.addEventListener('error',event => { if (event.target.matches?.('[data-cover], [data-next-cover], .broadcast-item img') && !event.target.src.endsWith(defaultCover)) event.target.src=defaultCover; },true);
  audio.addEventListener('playing',() => {
    lastAdvance=performance.now();lastTime=audio.currentTime;cancelRetry();note('playing'); if (!intent || !available || denied) { audio.pause(); return; } state='playing'; blocked=false; render(); });
  audio.addEventListener('waiting',() => { if (intent) { state='loading'; render(); } });
  // stalled describes network loading, not a fatal error: native live HLS may
  // raise it between playlist updates while its buffered audio is still advancing.
  audio.addEventListener('stalled',()=>note('stalled'));
  audio.addEventListener('error',()=>{note('media-error');retry('media-error');});
  function pauseListener() {
    // Keep this exact media session and the last audibly proved metadata.
    // Only the listener is paused; the global radio and its programming run on.
    const at=clock();
    if(!frozen)frozen={at,mediaTime:audio.currentTime,data:{...displayedData()}};
    epoch++;intent=false;blocked=false;cancelRetry();
    audio.pause();state='paused';note('listener-paused');render();
  }
  audio.addEventListener('pause',() => {
    if(available && state==='playing' && intent){pauseListener();}
    else if(available && !intent){state='paused';render();}
  });
  audio.addEventListener('volumechange',render);
  audio.addEventListener('loadedmetadata',() => { if(intent && joinPending){joinPending=false;joinLive();}note('loadedmetadata'); });
  audio.addEventListener('timeupdate',()=>{
    advancing();progress();
    // Do not keep an old title across an emitted boundary while a metadata
    // response is in flight. Refresh the view, never the media source.
    if(data.metadata_basis==='audible-clock' && displayedData()!==data){render();refresh();}
  });
  for(const event of ['loadstart','emptied','waiting','pause','seeking','seeked'])audio.addEventListener(event,()=>note(event));
  const diagnostic=document.querySelector('#radio-diagnostics');
  if(diagnostic){
    setInterval(()=>{if(diagnostic.open){const d=window.diggerRadioDiagnostics();diagnostic.querySelector('pre').textContent=JSON.stringify({version:d.version,transport:d.transport,state:d.state,currentTime:d.currentTime,audibleAt:d.audibleAt,clockProbe:d.clockProbe,frozen:d.frozen,metadata:d.metadata,metadataRequest:d.metadataRequest,metadataResponse:d.metadataResponse,buffered:d.buffered,measurement:d.measurement},null,2);}},2000);
    function download(value,name){
      const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
      const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    diagnostic.querySelector('button').addEventListener('click',()=>download(window.diggerRadioDiagnostics(),'digger-radio-safari.json'));
    diagnostic.querySelector('[data-export-clock]')?.addEventListener('click',()=>download(window.diggerRadioClockDiagnostic(),'digger-radio-horloge.json'));
  }
  window.addEventListener('editorial:render',render);
  audio.volume=.8;
  render(); refresh(); setInterval(refresh,2000);
})();
