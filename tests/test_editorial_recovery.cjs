/* Deterministic recovery regressions only; supplements the real native-HLS soak. */
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../static/editorial/player.js'),'utf8');
(async()=>{
 let now=0,loads=0,plays=0,nextTimer=1;
 const timers=new Map(),intervals=[],listeners={};
 const api={code:200,data:{available:true,track_id:1,title:'Private fixture'}};
 const ranges={length:0};
 const audio={dataset:{enabled:'true'},paused:true,currentTime:0,readyState:4,networkState:2,buffered:ranges,seekable:ranges,seeking:false,
  addEventListener(name,fn){(listeners[name]||=[]).push(fn);},
  canPlayType:()=> 'probably',getStartDate:()=>new Date(NaN),
  play:async()=>{plays++;audio.paused=false;emit('playing');},
  pause:()=>{audio.paused=true;emit('pause');},load:()=>loads++,removeAttribute:name=>delete audio[name]};
 const emit=name=>{for(const fn of listeners[name]||[])fn();};
 const window={addEventListener(){}};
 vm.runInNewContext(source,{window,navigator:{userAgent:'deterministic recovery test'},performance:{now:()=>now},AbortController,
  document:{querySelector:s=>s==='#editorial-audio'?audio:s==='.live-card'?{dataset:{}}:null,querySelectorAll:()=>[],addEventListener(){}},
  fetch:async()=>({ok:api.code===200,status:api.code,json:async()=>api.data}),
  setInterval:(fn,delay)=>{intervals.push({fn,delay});},
  setTimeout:(fn,delay)=>{const id=nextTimer++;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id)});
 await new Promise(setImmediate);
 const watchdog=intervals.find(x=>x.delay===1000).fn;
 const refresh=intervals.find(x=>x.delay===2000).fn;
 assert.equal(plays,1);
 for(let i=1;i<=8;i++){
  now=i*5000;audio.currentTime+=5;emit('stalled');watchdog();
 }
 assert.equal(loads,0,'stalled and throttled timeupdate must preserve a moving media clock');
 assert.equal(plays,1);
 now+=16000;watchdog();
 let pending=[...timers.entries()].find(([,t])=>t.delay===3000);
 assert(pending,'a genuinely frozen media clock must schedule recovery');
 audio.currentTime+=2;now+=3000;timers.delete(pending[0]);pending[1].fn();
 assert.equal(loads,0,'a stale timeout must re-sample currentTime before destroying a recovered buffer');
 now+=16000;watchdog();pending=[...timers.entries()].find(([,t])=>t.delay===3000);
 assert(pending);now+=3000;timers.delete(pending[0]);pending[1].fn();await new Promise(setImmediate);
 assert.equal(loads,1,'a genuinely frozen source must recover once');
 assert.equal(plays,2);
 api.code=503;await refresh();assert.equal(loads,1);assert.equal(audio.paused,false,'metadata failure must not stop real media');
 api.code=401;await refresh();assert.equal(audio.paused,true,'auth expiry must stop immediately');assert.equal(audio.src,undefined);
 console.log('PASS: native stalled, background event throttling, stale timer recovery, genuine freeze, metadata outage and immediate auth shutdown');
})().catch(e=>{console.error(e);process.exitCode=1;});
