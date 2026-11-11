/* Unmocked native HLS stability run on the actual private DEV stream. */
const pw=require(process.env.DIGGER_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const mode=process.env.DIGGER_BROWSER||'chromium';
const seconds=Number(process.env.DIGGER_SOAK_SECONDS||900);
const before=process.env.DIGGER_PLAYER_BEFORE;
const output=process.env.DIGGER_SOAK_OUTPUT||path.join(root,'instance/dev/safari-stability');
process.umask(0o077);fs.mkdirSync(output,{recursive:true,mode:0o700});
const cookie=require('./dev_browser_session.cjs')(root);
(async()=>{
 const launch={headless:true};
 if(mode==='webkit'){
  const dir=process.env.DIGGER_WEBKIT_DIR || '/home/jean/.cache/ms-playwright/webkit-2370/minibrowser-wpe';
  const deps=process.env.DIGGER_WEBKIT_DEPS || '/tmp/digger-webkit-deps/root/usr/lib/x86_64-linux-gnu';
  launch.executablePath=dir+'/bin/MiniBrowser';
  launch.env={...process.env,WEBKIT_EXEC_PATH:dir+'/bin',WEBKIT_INJECTED_BUNDLE_PATH:dir+'/lib',WEBKIT_INSPECTOR_RESOURCES_PATH:dir+'/share',LD_LIBRARY_PATH:dir+'/lib:'+dir+'/sys/lib:'+deps,GST_PLUGIN_PATH:deps+'/gstreamer-1.0',GIO_EXTRA_MODULES:deps+'/gio/modules',WEBKIT_GST_ENABLE_HLS_SUPPORT:'1'};
 }
 const browser=await pw[mode].launch(launch);
 const report={engine:mode,native:true,seconds_requested:seconds,started_utc:new Date().toISOString(),samples:[],http_errors:[],items:[],page_errors:[]};
 try{
  const ctx=await browser.newContext();await ctx.addCookies([cookie]);
  const original=fs.readFileSync(before||path.join(root,'static/editorial/player.js'),'utf8');
  // Select only the existing native adapter, leaving all media methods and clocks unmocked.
  await ctx.route('**/static/editorial/player.js',r=>r.fulfill({contentType:'text/javascript',body:original.replace("if (nativeSupported && typeof audio.getStartDate === 'function')",'if (nativeSupported)')}));
  const page=await ctx.newPage();
  await page.addInitScript(()=>{
   window.mediaEvents=[];window.loadCalls=0;
   const original=HTMLMediaElement.prototype.load;
   HTMLMediaElement.prototype.load=function(...args){window.loadCalls++;return original.apply(this,args);};
   document.addEventListener('DOMContentLoaded',()=>{
    const a=document.querySelector('audio');if(!a)return;
    for(const name of ['loadstart','loadedmetadata','stalled','waiting','playing','pause','error','emptied','seeking','seeked','abort']) a.addEventListener(name,()=>window.mediaEvents.push({event:name,at:performance.now(),time:a.currentTime,ready:a.readyState,network:a.networkState,error:a.error?.code||null}));
   });
  });
  page.on('pageerror',e=>report.page_errors.push(e.message));
  page.on('response',async r=>{
   const url=new URL(r.url());
   if(r.status()>=400)report.http_errors.push({path:url.pathname,status:r.status()});
   if(url.pathname==='/preview-radio/now-playing'&&r.ok())try{
    const d=await r.json();const key=d.type+':'+d.track_id+':'+d.playback_started_at;
    if(d.track_id||d.type==='jingle')if(report.items.at(-1)?.key!==key)report.items.push({key,title:d.title,basis:d.metadata_basis});
   }catch(_){}
  });
  await page.goto('http://127.0.0.1:17010/?radioDiagnostics=1');
  const capabilities=await page.locator('audio').evaluate(a=>({native:a.canPlayType('application/vnd.apple.mpegurl'),clock:typeof a.getStartDate,userAgent:navigator.userAgent}));report.capabilities=capabilities;
  assert(capabilities.native,'Native HLS not supported');
  await page.waitForFunction(()=>!document.querySelector('.live-card [data-play]').disabled);
  if(await page.locator('audio').evaluate(a=>a.paused))await page.locator('.live-card [data-play]').click();
  await page.waitForFunction(()=>!document.querySelector('audio').paused&&document.querySelector('audio').readyState>=3,{},{timeout:45000});
  const begin=Date.now();let navigated=false;
  await page.evaluate(()=>{window.testAudio=document.querySelector('audio');window.testStartEvents=window.mediaEvents.length;});
  while(Date.now()-begin<seconds*1000){
   const sample=await page.locator('audio').evaluate(a=>({time:a.currentTime,paused:a.paused,ready:a.readyState,network:a.networkState,buffered:a.buffered.length?Array.from({length:a.buffered.length},(_,i)=>[a.buffered.start(i),a.buffered.end(i)]):[],loads:window.loadCalls,loadstarts:window.mediaEvents.filter(e=>e.event==='loadstart').length,source:a.currentSrc}));
   report.samples.push({elapsed:(Date.now()-begin)/1000,...sample});
   if(!navigated&&Date.now()-begin>Math.min(60000,seconds*500)){
    await page.locator('#site-nav a[href="/a-propos"]').click();await page.waitForURL('**/a-propos');
    assert(await page.evaluate(()=>testAudio===document.querySelector('audio')));
    await page.locator('#site-nav a[href="/"]').click();await page.waitForURL('http://127.0.0.1:17010/');navigated=true;
   }
   if(report.samples.length%15===0){fs.writeFileSync(path.join(output,'progress.json'),JSON.stringify({elapsed:Math.round((Date.now()-begin)/1000),last:sample,items:report.items.length}));console.log('NATIVE',mode,Math.round((Date.now()-begin)/1000),'s','loads',sample.loads,'loadstarts',sample.loadstarts);}
   await page.waitForTimeout(2000);
  }
  report.real_seconds=(Date.now()-begin)/1000;
  report.events=await page.evaluate(()=>window.mediaEvents);
  report.diagnostics=await page.evaluate(()=>window.diggerRadioDiagnostics?.()||null);
  const final=report.samples.at(-1),initial=report.samples[0];
  report.source_resets=final.loadstarts-initial.loadstarts;
  report.load_calls=final.loads-initial.loads;
  report.max_no_progress_seconds=0;let flat=0;
  for(let i=1;i<report.samples.length;i++){
   flat=report.samples[i].time>report.samples[i-1].time+.1?0:flat+report.samples[i].elapsed-report.samples[i-1].elapsed;
   report.max_no_progress_seconds=Math.max(flat,report.max_no_progress_seconds);
  }
  if(!before){assert.equal(report.source_resets,0);assert.equal(report.load_calls,0);assert(report.max_no_progress_seconds<8);assert.equal(report.page_errors.length,0);}
  report.status='passed';
 }catch(e){report.status='failed';report.failure=e.message;throw e;}
 finally{report.finished_utc=new Date().toISOString();fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({status:report.status,engine:mode,seconds:report.real_seconds,resets:report.source_resets,loads:report.load_calls,failure:report.failure}));}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
