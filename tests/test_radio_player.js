// Browser API simulation only: no network, server, media or npm dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/radio.js'), 'utf8');

function fixture(native) {
  const elements = {};
  for (const id of ['player', 'playBtn', 'np-cover', 'np-title', 'np-artist', 'progressFill', 'volumeInput', 'player-status']) {
    elements[id] = {listeners: {}, attrs: {}, dataset: {}, style: {}, paused: true,
      addEventListener(event, fn) { this.listeners[event] = fn; },
      setAttribute(key, value) { this.attrs[key] = value; },
      getAttribute(key) { return key === 'src' ? this.src : this.attrs[key]; }};
  }
  const audio = elements.player;
  let calls = 0;
  audio.canPlayType = () => native ? 'probably' : '';
  audio.play = async () => { calls++; throw Error('Safari denied playback'); };
  audio.pause = () => { audio.paused = true; audio.listeners.pause(); };
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
  let state = {track_id: 1, title: 'Track', artist: 'Artist', duration: 10, elapsed: 5};
  let refresh;
  vm.runInNewContext(source, {document: {getElementById: id => elements[id]},
    window: {Hls}, Hls, fetch: async () => ({ok: true, json: async () => state}),
    setTimeout: fn => { timers.push(fn); return timers.length; },
    setInterval: fn => { refresh = fn; }});
  return {elements, audio, timers, instances, calls: () => calls,
    refresh: async next => { state = next; await refresh(); }};
}

(async () => {
  for (const native of [true, false]) {
    const f = fixture(native);
    await new Promise(setImmediate);
    assert.equal(f.calls(), 0, 'Safari starts only on user action');
    if (native) assert.equal(f.audio.src, '/hls/stream.m3u8');
    else assert.equal(f.instances[0].src, '/hls/stream.m3u8');
    f.elements.playBtn.listeners.click();
    await new Promise(setImmediate);
    assert.equal(f.elements.playBtn.attrs['aria-pressed'], 'false');
    assert.match(f.elements['player-status'].textContent, /réessayer/);
    const cover = f.elements['np-cover'].src;
    await f.refresh({track_id: 1, title: 'Track', duration: 10, elapsed: 20});
    assert.equal(f.elements['np-cover'].src, cover, 'no timestamp cache busting');
    assert.equal(f.elements.progressFill.style.width, '100%');
    await f.refresh({track_id: null});
    assert.equal(f.elements.progressFill.style.width, '0%');
    assert.equal(f.elements['np-cover'].src, '/static/digger-icon.png');
    if (native) { f.audio.listeners.error(); f.audio.listeners.error(); }
    else {
      f.instances[0].error(null, {fatal: false});
      assert.equal(f.timers.length, 0);
      f.instances[0].error(null, {fatal: true});
      f.instances[0].error(null, {fatal: true});
    }
    assert.equal(f.timers.length, 1, 'only one recovery timer');
    f.timers.shift()();
    if (!native) {
      assert.equal(f.instances[0].destroyed, true);
      assert.equal(f.instances.length, 2);
    }
    assert.equal(f.calls(), 1, 'recovery does not autoplay after denied playback');
  }
  console.log('OK: native HLS and hls.js player regressions');
})().catch(error => { console.error(error); process.exitCode = 1; });
