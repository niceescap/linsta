/* Supplemental deterministic Safari clock/DOM regression, not an audio test. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const base=1792630000,intervals=[],listeners={},requests=[],nodes=new Map(),documentListeners={};
 let validDate=true,loads=0,sourceWrites=0,now=1000;
 const timers=new Map();
 function pair(selector){
  if(!nodes.has(selector))nodes.set(selector,[0,1].map(()=>({dataset:{},style:{},
   getAttribute(k){return this[k]},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},
   classList:{toggle(){}},replaceChildren(){}})));
  return nodes.get(selector);
 }
 const audio={dataset:{enabled:'true'},paused:true,currentTime:30,readyState:4,networkState:2,
  seekable:{length:1,start:()=>0,end:()=>Number.MAX_VALUE},
  buffered:{length:1,start:()=>0,end:()=>90},canPlayType:()=> 'probably',
  getStartDate:()=>new Date(validDate?base*1000:NaN),
  addEventListener(n,fn){(listeners[n]||=[]).push(fn)},
  play:async()=>{audio.paused=false;emit('playing')},pause:()=>{if(!audio.paused){audio.paused=true;emit('pause')}},
  load:()=>loads++,removeAttribute(){}};
 Object.defineProperty(audio,'src',{get(){return '/preview-radio/hls/stream.m3u8'},set(){sourceWrites++}});
 function emit(n){for(const f of listeners[n]||[])f()}
 const payload=at=>{
  const t=at-base, jingle=t>=40&&t<45, following=t>=45;
  return {available:true,metadata_basis:Number.isFinite(at)?'audible-clock':'published-live-edge',
   metadata_status:'synchronized',metadata_at:at,type:jingle?'jingle':'track',
   track_id:jingle?null:following?2:1,title:jingle?'Jingle Digger Radio':following?'Morceau B':'Morceau A',
   artist:jingle?null:'Artiste réel',cover_url:jingle?null:'/preview-radio/tracks/'+(following?2:1)+'/cover',
   playback_started_at:base+(following?45:jingle?40:0),playback_ended_at:base+(following?90:jingle?45:40),
   duration:jingle?5:following?45:40,timing_available:true,next_track_id:2,next_title:'Morceau B',next_artist:'Artiste réel'};
 };
 const window={addEventListener(){}};
 vm.runInNewContext(fs.readFileSync('static/editorial/player.js','utf8'),{
  window,navigator:{userAgent:'Safari clock regression'},performance:{now:()=>now},AbortController,
  document:{querySelector:s=>s==='#editorial-audio'?audio:s==='.live-card'?{dataset:{}}:null,
   querySelectorAll:s=>s==='[data-history]'?[]:pair(s),addEventListener(n,fn){documentListeners[n]=fn}},
  fetch:async url=>{requests.push(url);const raw=new URL(url,'https://private.test').searchParams.get('at');
   return {ok:true,status:200,json:async()=>payload(raw===null?NaN:Number(raw))}},
  setInterval:(fn,ms)=>intervals.push({fn,ms}),setTimeout:(fn,ms)=>{const id=timers.size+1;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id)});
 const tick=()=>new Promise(setImmediate),refresh=intervals.find(i=>i.ms===2000).fn;
 await tick();emit('loadedmetadata');await refresh();
 assert.equal(audio.currentTime,30,'initial join must never seek to an indefinite MAX_VALUE range endpoint');
 const titles=()=>pair('[data-title]').map(n=>n.textContent);
 assert.deepEqual(titles(),['Morceau A','Morceau A']);
 assert(requests.filter(url=>url.startsWith('/preview-radio/now-playing')).at(-1).includes('at='+String(base+30)));
 assert(pair('[data-elapsed]').every(n=>n.textContent==='00:30'));
 assert(pair('[data-duration], [data-remaining]').every(n=>n.textContent==='00:40'));
 const click=()=>documentListeners.click({target:{closest:s=>s==='[data-play]'?{}:null}});
 click();assert(audio.paused);assert(titles().every(t=>t==='Morceau A'));
 audio.currentTime=80;await refresh();
 assert(pair('[data-elapsed]').every(n=>n.textContent==='00:30'),'pause must freeze the audible counter, never switch to the live edge');
 assert(titles().every(t=>t==='Morceau A'));
 assert(requests.filter(url=>url.startsWith('/preview-radio/now-playing')).at(-1).includes('at='+String(base+30)));
 audio.seekable={length:1,start:()=>0,end:()=>75};
 click();await tick();await refresh();
 assert.equal(audio.currentTime,63,'resume must rejoin current live edge without replacing the source');
 assert(titles().every(t=>t==='Morceau B'));
 assert(pair('[data-elapsed]').every(n=>n.textContent==='00:18'));
 assert(pair('[data-duration], [data-remaining]').every(n=>n.textContent==='00:45'));
 assert.equal(sourceWrites,1);assert.equal(loads,0);
 // Replay boundary cases using the same element; no new attachment.
 audio.currentTime=30;await refresh();
 audio.currentTime=40.1;emit('timeupdate');
 assert(titles().every(t=>t==='Synchronisation du direct…'),'old title must clear before the async boundary response');
 await tick();assert(titles().every(t=>t==='Synchronisation du direct…'),'native rounded clock must not guess a title at the boundary');
 audio.currentTime=40.7;await refresh();assert(titles().every(t=>t==='Jingle Digger Radio'));
 assert(pair('[data-artist]').every(n=>n.textContent==='Identifiant de station'));
 assert(pair('[data-duration], [data-remaining]').every(n=>n.textContent==='00:05'));
 audio.currentTime=45.7;emit('timeupdate');await tick();
 assert(titles().every(t=>t==='Morceau B'));
 assert(pair('[data-next-title]').every(n=>n.textContent==='Morceau B'));
 validDate=false;await refresh();
 assert(titles().every(t=>t==='Synchronisation du direct…'));
 assert(pair('[data-next-title]').every(n=>n.textContent==='Morceau B'));
 assert(pair('[data-elapsed]').every(n=>n.textContent==='--:--'));
 assert(window.diggerRadioDiagnostics().clockProbe.nativeOrigin===null);
 click();await tick();await refresh();
 assert(titles().every(t=>t==='Synchronisation du direct…'),'pause must not disguise an unavailable audible clock by showing the published edge title');
 assert(pair('[data-elapsed]').every(n=>n.textContent==='--:--'));
 validDate=true;click();await tick();await refresh();assert(titles().every(t=>t==='Morceau B'));
 assert.equal(loads,0);assert.equal(window.diggerRadioDiagnostics().measurement.attachments,1);
 // Native DBL_MAX must neither leak into API requests nor trigger source resets.
 const watchdog=intervals.find(i=>i.ms===1000).fn;
 audio.currentTime=Number.MAX_VALUE;
 audio.seekable={length:1,start:()=>0,end:()=>Number.MAX_VALUE};
 audio.buffered={length:1,start:()=>25,end:()=>70};
 await refresh();
 assert.equal(window.diggerRadioDiagnostics().clockProbe.computedAt,null);
 assert.equal(window.diggerRadioDiagnostics().clockProbe.position.reason,'invalid-media-time');
 assert(!requests.filter(url=>url.startsWith('/preview-radio/now-playing')).at(-1).includes('?at='));
 assert(titles().every(t=>t==='Synchronisation du direct…'));
 assert(pair('[data-elapsed]').every(n=>n.textContent==='--:--'));
 assert(pair('[data-duration], [data-remaining]').every(n=>n.textContent==='--:--'));
 assert(pair('[data-progress]').every(n=>n.style.width==='0%'));
 assert(pair('[data-cover]').every(n=>n.src==='/static/editorial/digger-logo-transparent.png'));
 assert(pair('[data-next-title]').every(n=>n.textContent==='Morceau B'));
 for(let i=0;i<23;i++){now+=4000;emit('stalled');watchdog();}
 assert.equal(window.diggerRadioDiagnostics().measurement.progressObservable,false);
 assert.equal(window.diggerRadioDiagnostics().measurement.lastProgressAgeSeconds,null);
 assert.equal(timers.size,0,'an unreadable clock must not schedule reconnection');
 assert.equal(loads,0);assert.equal(sourceWrites,1);assert.equal(audio.paused,false);
 // Pause/resume must reject the invalid range endpoint, not seek to MAX_VALUE.
 click();click();await tick();
 assert.equal(audio.currentTime,Number.MAX_VALUE);
 // A plausible but out-of-range position is also rejected, unless retained in buffered.
 audio.seekable={length:1,start:()=>25,end:()=>70};audio.currentTime=100;await refresh();
 assert.equal(window.diggerRadioDiagnostics().clockProbe.position.reason,'outside-media-ranges');
 audio.currentTime=30;await refresh();assert(titles().every(t=>t==='Morceau A'));
 assert(pair('[data-elapsed]').every(n=>n.textContent==='00:30'));
 assert(pair('[data-cover]').every(n=>n.src==='/preview-radio/tracks/1/cover'));
 audio.currentTime=40.7;await refresh();assert(titles().every(t=>t==='Jingle Digger Radio'));
 audio.currentTime=45.7;await refresh();assert(titles().every(t=>t==='Morceau B'));
 assert(window.diggerRadioDiagnostics().measurement.recoveredClocks>=1);
 assert.equal(loads,0);assert.equal(sourceWrites,1);
 // Old buffered positions remain valid after they leave the seekable download window.
 audio.seekable={length:1,start:()=>60,end:()=>90};audio.currentTime=46;await refresh();
 assert(titles().every(t=>t==='Morceau B'));
 console.log('PASS: native getStartDate+currentTime, two synchronized surfaces, track/jingle/track, missing/restored clock, DBL_MAX rejection/recovery, 23 stalled events, zero audio resets');
})().catch(e=>{console.error(e);process.exitCode=1});
