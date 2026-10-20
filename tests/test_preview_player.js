// Safari native-HLS and hls.js preview behavior; no browser/network/media needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/radio.js'), 'utf8');

function fixture(native, loggedIn = true) {
  const elements = {};
  for (const id of ['player', 'playBtn', 'np-cover', 'np-title', 'np-artist', 'progressFill', 'volumeInput', 'player-status', 'next-track', 'next-title', 'next-artist', 'next-cover', 'track-elapsed', 'track-duration', 'track-remaining']) {
    elements[id] = {listeners: {}, attrs: {}, dataset: {}, style: {}, paused: true,
      addEventListener(event, fn) { this.listeners[event] = fn; },
      setAttribute(key, value) { this.attrs[key] = value; },
      getAttribute(key) { return key === 'src' ? this.src : this.attrs[key]; },
      removeAttribute(key) { delete this[key]; }};
  }
  const audio = elements.player;
  Object.assign(audio.dataset, {preview: 'true', liveEnabled: String(loggedIn),
    streamUrl: '/preview-radio/hls/stream.m3u8', metadataUrl: '/preview-radio/now-playing'});
  const calls = {play: 0, load: 0, fetch: [], cleared: []};
  audio.currentTime = 0;
  audio.getStartDate = () => new Date(NaN);
  audio.canPlayType = () => native ? 'probably' : '';
  audio.play = async () => { calls.play++; audio.paused = false; audio.listeners.play(); };
  audio.pause = () => { audio.paused = true; audio.listeners.pause(); };
  audio.load = () => { calls.load++; };
  const timers = [];
  const instances = [];
  class Hls {
    static isSupported() { return true; }
    static Events = {ERROR: 'error'};
    constructor() { instances.push(this); }
    loadSource(src) { this.src = src; }
    attachMedia() {}
    on(_, fn) { this.error = fn; }
    destroy() { this.destroyed = true; }
  }
  let state = {available: false, track_id: null, message: 'Radio PREVIEW arrêtée.'};
  let status = 200;
  let refresh;
  vm.runInNewContext(source, {document: {getElementById: id => elements[id]}, window: {Hls}, Hls,
    fetch: async (url, options) => {
      calls.fetch.push({url, options});
      return {ok: status === 200, status, json: async () => state};
    },
    setTimeout: fn => { timers.push(fn); return timers.length; },
    clearTimeout: id => calls.cleared.push(id),
    setInterval: fn => { refresh = fn; }});
  return {audio, elements, instances, timers, calls,
    refresh: async (next, code = 200) => { state = next; status = code; await refresh(); }};
}

(async () => {
  for (const native of [true, false]) {
    const anon = fixture(native, false);
    await new Promise(setImmediate);
    assert.equal(anon.calls.fetch.length, 0);
    assert.equal(anon.instances.length, 0);
    assert.equal(anon.elements.playBtn.disabled, true);
    assert.match(anon.elements['player-status'].textContent, /Connectez-vous/);

    const f = fixture(native);
    await new Promise(setImmediate);
    f.elements.playBtn.listeners.click();
    assert.equal(f.calls.play, 0);
    assert.equal(f.audio.src, undefined);
    assert.equal(f.instances.length, 0);
    assert.match(f.elements['player-status'].textContent, /arrêtée/);
    assert.equal(f.calls.fetch[0].url, '/preview-radio/now-playing');
    assert.equal(f.calls.fetch[0].options.credentials, 'same-origin');
    await f.refresh({available: true, track_id: 1, title: 'Private track', artist: 'Artist',
      next_track_id: 2, next_title: 'Next real', next_artist: 'Next artist', next_cover_url: '/preview-radio/tracks/2/cover', duration: 10, elapsed: 2, cover_url: '/preview-radio/tracks/1/cover'});
    assert.equal(f.audio.src, undefined, 'ready state never triggers automatic audio load');
    assert.equal(f.elements['track-elapsed'].textContent, '--:--', 'no fabricated wall-clock progress without HLS timing');
    assert.equal(f.elements['next-title'].textContent, 'Next real');
    assert.equal(f.elements['next-track'].hidden, false);
    assert.match(f.elements['player-status'].textContent, /Appuyez sur Play pour écouter/);
    f.elements.playBtn.listeners.click();
    await new Promise(setImmediate);
    assert.equal(f.calls.play, 1);
    assert.equal(f.elements.playBtn.attrs['aria-pressed'], 'true');
    if (native) assert.equal(f.audio.src, '/preview-radio/hls/stream.m3u8');
    else assert.equal(f.instances[0].src, '/preview-radio/hls/stream.m3u8');
    assert.equal(f.elements['np-cover'].src, '/preview-radio/tracks/1/cover');
    assert.match(f.elements['player-status'].textContent, /PREVIEW \/ TEST/);
    // The encoder can be on track 2 while this listener still hears track 1.
    if (native) f.audio.getStartDate = () => new Date(1000000);
    else f.instances[0].playingDate = new Date(1138000);
    f.audio.currentTime = 138;
    await f.refresh({available:true, track_id:1, title:'Audible', duration:339,
      timing_available:true, playback_started_at:1000, next_track_id:2});
    assert.equal(f.elements['track-elapsed'].textContent, '02:18');
    assert.equal(f.elements['track-duration'].textContent, '05:39');
    assert.equal(f.elements['track-remaining'].textContent, '-03:21');
    assert.ok(f.calls.fetch.at(-1).url.includes('?at=1138'));
    f.audio.listeners.timeupdate();
    assert.equal(f.elements['track-elapsed'].textContent, '02:18');
    f.elements.playBtn.listeners.click();
    assert.equal(f.audio.paused, true);
    f.elements.playBtn.listeners.click();
    await new Promise(setImmediate);
    if (native) f.audio.currentTime = 340;
    else f.instances[0].playingDate = new Date(1340000);
    await f.refresh({available: true, track_id: 2, title: 'Next real', artist: 'Next artist', next_track_id: 3, next_title: 'Third', duration:180, timing_available:true, playback_started_at:1339});
    assert.equal(f.elements['track-elapsed'].textContent, '00:01');
    assert.equal(f.elements['track-remaining'].textContent, '-02:59');
    assert.equal(f.elements['np-title'].textContent, 'Next real');
    assert.equal(f.elements['next-title'].textContent, 'Third');
    await f.refresh({available: true, track_id: 2, title: 'Next real'});
    assert.equal(f.elements['next-track'].hidden, true);
    assert.equal(f.elements['track-elapsed'].textContent, '--:--', 'legacy metadata has no audible timing');
    await f.refresh({available:true, track_id:null, type:'jingle', title:'Digger Radio', artist:'Station ID',
      duration:5.64, timing_available:true, playback_started_at:1339, cover_url:'/static/digger-icon.png',
      next_track_id:3, next_title:'Third', next_artist:'Artist'});
    assert.equal(f.elements['np-title'].textContent, 'Digger Radio');
    assert.equal(f.elements['np-artist'].textContent, '— Station ID');
    assert.equal(f.elements['next-track'].hidden, false);
    assert.equal(f.elements['next-title'].textContent, 'Third');
    assert.equal(f.elements['track-elapsed'].textContent, '00:01');
    if (native) f.audio.listeners.error();
    else f.instances[0].error(null, {fatal: true});
    assert.equal(f.timers.length, 1);
    await f.refresh({available: false, track_id: null, message: 'Radio PREVIEW arrêtée.'});
    assert.equal(f.audio.paused, true);
    assert.equal(f.audio.src, undefined);
    assert.equal(f.calls.load, 1);
    assert.equal(f.elements['track-elapsed'].textContent, '--:--');
    assert.equal(f.elements.progressFill.style.width, '0%');
    assert.equal(f.calls.cleared.length, 1);
    f.timers[0](); // Even a dispatched callback cannot restart a stopped stream.
    assert.equal(f.calls.play, 2);
    f.elements.playBtn.listeners.click();
    assert.equal(f.calls.play, 2);
    await f.refresh({}, 401);
    assert.match(f.elements['player-status'].textContent, /Reconnectez-vous/);
  }
  console.log('OK: native HLS/hls.js, audible timing and delay, track transition/reset, pause/resume, missing clock, private URLs and stop/auth recovery');
})().catch(error => { console.error(error); process.exitCode = 1; });
