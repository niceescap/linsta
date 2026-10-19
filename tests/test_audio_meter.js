const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/audio-meter.js'), 'utf8');
function fixture({capture = true, context = true, splitter = true, broken = false, vendor = false} = {}) {
  const elements = Object.fromEntries(['root','left','right','leftLabel','rightChannel','status'].map(k => [k,{style:{},dataset:{}}]));
  let created = 0, captured = 0, stopped = 0;
  const frames = new Map(); let sequence = 0;
  const amplitudes = [.5, .05];
  const node = () => ({connect(){}, disconnect(){}});
  class Context {
    constructor(){created++; this.state='running'; this.destination={};}
    resume(){return Promise.resolve();}
    createMediaElementSource(){throw Error('Audible routing must never be changed');}
    createMediaStreamSource(){if(broken) throw Error('unsupported'); return node();}
    createGain(){return {...node(),gain:{value:1}};}
    createAnalyser(){ const index = this.count || 0; this.count=index+1; return {...node(), getFloatTimeDomainData(buffer){buffer.fill(amplitudes[index]);}}; }
  }
  if(splitter) Context.prototype.createChannelSplitter = node;
  const audio = {paused:true, ended:false, muted:false, volume:.8};
  if(capture) audio.captureStream = () => {captured++; const track={stop(){stopped++;}}; return {getAudioTracks:()=>[track],getTracks:()=>[track],addEventListener(){}};};
  const window = {requestAnimationFrame(fn){frames.set(++sequence,fn); return sequence;},cancelAnimationFrame(id){frames.delete(id);}};
  if(context) window[vendor?'webkitAudioContext':'AudioContext'] = Context;
  vm.runInNewContext(source,{window,Float32Array,Uint8Array,Math});
  const meter = window.DiggerAudioMeter(audio,elements);
  return {meter,audio,elements,amplitudes,frames, counts:()=>({created,captured,stopped}), frame(){const callbacks=[...frames.values()]; frames.clear();callbacks.forEach(fn=>fn(sequence*40));}};
}
(async()=>{
  const f=fixture();
  assert.deepEqual(f.counts(),{created:0,captured:0,stopped:0});
  f.meter.playing(); assert.equal(f.counts().created,0);
  f.meter.onGesture(); f.audio.paused=false; f.meter.playing(); await Promise.resolve();
  f.frame();
  const value = el=>Number(el.style.transform.match(/\((.*)\)/)[1]);
  assert.ok(value(f.elements.left)>value(f.elements.right));
  assert.ok(value(f.elements.right)>0);
  f.meter.pause(); assert.equal(value(f.elements.left),0);assert.equal(f.frames.size,0);
  f.audio.paused=true; f.meter.onGesture(); f.audio.paused=false; f.meter.playing(); f.frame();
  assert.equal(f.counts().created,1);assert.ok(value(f.elements.left)>0);
  f.audio.muted=true; f.meter.pause(); f.meter.playing();f.frame();assert.equal(value(f.elements.left),0);
  f.meter.reset();assert.equal(f.counts().stopped,1);assert.equal(f.frames.size,0);
  const global=fixture({splitter:false,vendor:true}); global.meter.onGesture(); global.audio.paused=false;global.meter.playing();
  assert.equal(global.elements.root.dataset.mode,'global');assert.equal(global.elements.rightChannel.hidden,true);
  for(const options of [{capture:false,vendor:true},{context:false},{broken:true}]) {
    const unsupported=fixture(options);
    assert.doesNotThrow(()=>unsupported.meter.onGesture());
    unsupported.audio.paused=false;
    assert.doesNotThrow(()=>unsupported.meter.playing());
    assert.equal(unsupported.audio.paused,false,'analysis cannot stop playback');
    assert.equal(unsupported.elements.root.dataset.mode,'unavailable');
  }
  console.log('OK: real stereo/global samples, gesture-only initialization, mute, pause/resume/reset, Safari fallback and non-blocking failures');
})().catch(error=>{console.error(error);process.exitCode=1;});
