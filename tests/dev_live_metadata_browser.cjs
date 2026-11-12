/* Real HLS and browser media clock. No substituted clock, media method or API. */
const pw=require('/tmp/digger-volet01-browser/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..'),mode=process.env.DIGGER_METADATA_ENGINE||'chromium';
const short=Number(process.env.DIGGER_METADATA_SHORT_SECONDS||0);
const out=path.join(root,'instance/dev/metadata-'+mode+(short?'-short':''));
process.umask(0o077);fs.mkdirSync(out,{recursive:true,mode:0o700});
(async()=>{
 const launch={headless:true};
 if(mode==='webkit'){
  const dir='/home/jean/.cache/ms-playwright/webkit-2370/minibrowser-wpe',deps='/tmp/digger-webkit-deps/root/usr/lib/x86_64-linux-gnu';
  launch.executablePath=dir+'/bin/MiniBrowser';
  launch.env={...process.env,WEBKIT_EXEC_PATH:dir+'/bin',WEBKIT_INJECTED_BUNDLE_PATH:dir+'/lib',WEBKIT_INSPECTOR_RESOURCES_PATH:dir+'/share',LD_LIBRARY_PATH:dir+'/lib:'+dir+'/sys/lib:'+deps,GST_PLUGIN_PATH:deps+'/gstreamer-1.0',GIO_EXTRA_MODULES:deps+'/gio/modules',WEBKIT_GST_ENABLE_HLS_SUPPORT:'1'};
 }
 const browser=await pw[mode].launch(launch);
 const report={mode,started:new Date().toISOString(),items:[],samples:[],checks:0,httpErrors:[],errors:[]};
 let latest,closing=false;
 try{
  const ctx=await browser.newContext();await ctx.addCookies([require('./dev_browser_session.cjs')(root)]);
  const page=await ctx.newPage();
  page.on('pageerror',e=>report.errors.push(e.message));
  page.on('response',async r=>{
   const u=new URL(r.url());if(r.status()>=400)report.httpErrors.push({path:u.pathname,status:r.status()});
   if(u.pathname==='/preview-radio/now-playing'&&r.ok()){
    let d;try{d=await r.json();}catch(error){if(closing)return;throw error;}
    if(d.metadata_basis!=='audible-clock')return;
    const at=Number(u.searchParams.get('at'));
    const t=JSON.parse(fs.readFileSync(root+'/instance/dev/private-radio/hls/timeline.json','utf8'));
    const item=t.tracks.filter(x=>t.origin+x.offset<=at).at(-1);
    assert(item);assert.equal(d.track_id,item.id);assert.equal(d.type,item.type);
    if(item.type==='track'){assert.equal(d.title,item.title);assert.equal(d.artist,item.artist);}
    else {assert.equal(d.artist,null);assert.equal(d.cover_url,null);}
    assert.equal(d.next_track_id,item.next_track_id);
    report.checks++;latest=d;
    const key=d.playback_started_at;
    if(report.items.at(-1)?.key!==key){report.items.push({key,type:d.type,title:d.title,next:d.next_title});console.log('REAL',mode,JSON.stringify(report.items.at(-1)));}
   }
  });
  await page.goto('http://127.0.0.1:17010/?radioDiagnostics=1');
  await page.waitForFunction(()=>!document.querySelector('[data-play]').disabled);
  if(await page.locator('audio').evaluate(a=>a.paused))await page.locator('[data-play]').first().click();
  await page.waitForFunction(()=>document.querySelector('audio').currentTime>0,{},{timeout:45000});
  const began=Date.now(),limit=short?short*1000:mode==='webkit'?10000:600000;
  const initial=await page.evaluate(()=>{window.testAudio=document.querySelector('audio');return diggerRadioDiagnostics()});
  report.initial=initial;
  let navigated=false;
  while(Date.now()-began<limit){
   const sample=await page.evaluate(()=>({diagnostic:diggerRadioDiagnostics(),titles:[...document.querySelectorAll('[data-title]')].map(e=>e.textContent),artists:[...document.querySelectorAll('[data-artist]')].map(e=>e.textContent),covers:[...document.querySelectorAll('[data-cover]')].map(e=>e.getAttribute('src')),nativeDate:document.querySelector('audio').getStartDate?.().getTime()/1000}));
   assert.equal(new Set(sample.titles).size,1);assert.equal(new Set(sample.artists).size,1);assert.equal(new Set(sample.covers).size,1);
   if(latest && sample.diagnostic.metadata.at===latest.metadata_at &&
      sample.diagnostic.audibleAt>latest.playback_started_at+1 &&
      (!Number.isFinite(latest.playback_ended_at)||sample.diagnostic.audibleAt<latest.playback_ended_at-1)){
    assert.equal(sample.titles[0],latest.title);
    assert.equal(sample.artists[0],latest.type==='jingle'?'Identifiant de station':latest.artist);
    assert.equal(sample.covers[0],latest.cover_url||'/static/digger-icon.png');
   }
   report.samples.push(sample);
   if(!navigated&&Date.now()-began>4000){
    await page.locator('#site-nav a[href="/a-propos"]').click();await page.waitForURL('**/a-propos');
    assert(await page.evaluate(()=>window.testAudio===document.querySelector('audio')));
    await page.locator('#site-nav a[href="/"]').click();await page.waitForURL('http://127.0.0.1:17010/');navigated=true;
   }
   fs.writeFileSync(out+'/progress.json',JSON.stringify({seconds:(Date.now()-began)/1000,checks:report.checks,items:report.items,sample}));
   if(report.items.length>=3 && report.items.some(i=>i.type==='jingle') && report.items.filter(i=>i.type==='track').length>=2)break;
   await page.waitForTimeout(1000);
  }
  report.seconds=(Date.now()-began)/1000;
  report.final=await page.evaluate(()=>diggerRadioDiagnostics());
  assert(report.checks>=2,'Native clock did not yield verified audible metadata');
  assert.equal(report.final.measurement.attachments,1);assert.equal(report.final.measurement.detachments,0);
  if(mode!=='webkit'){
   if(!short)assert(report.items.some(i=>i.type==='jingle') && report.items.filter(i=>i.type==='track').length>=2,'Missing actual track/jingle/track transition');
   assert(await page.locator('.now-grid [data-cover]').evaluate(i=>i.complete&&i.naturalWidth>0));
   assert.equal((await ctx.request.get('http://127.0.0.1:17010/preview-radio/now-playing')).status(),200);
  }
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.httpErrors,[]);
  report.passed=true;
 }catch(e){report.failure=e.message;throw e;}
 finally{
  report.finished=new Date().toISOString();fs.writeFileSync(out+'/report.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify({mode,passed:report.passed,seconds:report.seconds,checks:report.checks,items:report.items.length,failure:report.failure}));
  closing=true;await browser.close();
 }
})().catch(e=>{console.error(e.message);process.exitCode=1});
