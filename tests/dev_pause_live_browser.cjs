/* Real private DEV HLS. Pause/resume must preserve the media source and session. */
const {chromium}=require('/tmp/digger-volet01-browser/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..'),out=root+'/instance/dev/safari-clock-pause';
process.umask(0o077);fs.mkdirSync(out,{recursive:true,mode:0o700});
(async()=>{
 const browser=await chromium.launch({headless:true});
 const report={started:new Date().toISOString(),samples:[],errors:[]};
 const sample=page=>page.evaluate(()=>({diagnostic:diggerRadioDiagnostics(),
  titles:[...document.querySelectorAll('[data-title]')].map(n=>n.textContent),
  artists:[...document.querySelectorAll('[data-artist]')].map(n=>n.textContent),
  elapsed:document.querySelector('[data-elapsed]').textContent,
  duration:document.querySelector('[data-duration]').textContent,
  next:document.querySelector('[data-next-title]')?.textContent||null,
  progress:document.querySelector('[data-progress]').style.width,
  source:document.querySelector('audio').currentSrc,
  mediaEvents:window.testEvents}));
 try{
  const context=await browser.newContext();await context.addCookies([require('./dev_browser_session.cjs')(root)]);
  await context.addInitScript(()=>{
   window.testEvents=[];
   for(const n of ['loadstart','emptied','abort'])document.addEventListener(n,e=>{if(e.target.tagName==='AUDIO')testEvents.push(n)},true);
  });
  const page=await context.newPage();page.on('pageerror',e=>report.errors.push(e.message));
  await page.goto('http://127.0.0.1:17010/?radioDiagnostics=1');
  await page.waitForFunction(()=>!document.querySelector('[data-play]').disabled);
  if(await page.locator('audio').evaluate(a=>a.paused))await page.locator('[data-play]').first().click();
  await page.waitForFunction(()=>document.querySelector('[data-elapsed]').textContent!=='--:--',{},{timeout:30000});
  await page.waitForTimeout(2500);
  const playing=await sample(page);report.samples.push(playing);
  assert.equal(playing.diagnostic.version,'safari-clock-pause-2026-10-22');
  assert(!playing.titles[0].includes('Synchronisation'));assert(playing.next);assert.notEqual(playing.duration,'--:--');
  assert.equal(new Set(playing.titles).size,1);assert.equal(new Set(playing.artists).size,1);
  await page.locator('[data-play]').first().click();
  const paused=await sample(page);report.samples.push(paused);
  assert(paused.diagnostic.paused);assert(paused.diagnostic.frozen);
  await page.waitForTimeout(5500);
  const held=await sample(page);report.samples.push(held);
  assert.deepEqual(held.titles,paused.titles);assert.deepEqual(held.artists,paused.artists);
  assert.equal(held.elapsed,paused.elapsed);assert.equal(held.progress,paused.progress);assert.equal(held.duration,paused.duration);
  assert.equal(held.source,playing.source);assert.equal(held.diagnostic.measurement.detachments,0);
  await page.locator('[data-play]').first().click();
  await page.waitForFunction(()=>!document.querySelector('audio').paused&&document.querySelector('[data-elapsed]').textContent!=='--:--');
  await page.waitForTimeout(2500);
  const resumed=await sample(page);report.samples.push(resumed);
  assert(resumed.diagnostic.frozen===null);assert(resumed.diagnostic.audibleAt>paused.diagnostic.audibleAt+3);
  await page.locator('#site-nav a[href="/a-propos"]').click();await page.waitForURL('**/a-propos');
  await page.waitForTimeout(5000);
  const final=await sample(page);report.samples.push(final);
  assert.equal(final.source,playing.source);assert.deepEqual(final.mediaEvents,playing.mediaEvents);
  assert.equal(final.diagnostic.measurement.attachments,1);assert.equal(final.diagnostic.measurement.detachments,0);
  assert(!final.diagnostic.paused);assert.deepEqual(report.errors,[]);
  const t=JSON.parse(fs.readFileSync(root+'/instance/dev/private-radio/hls/timeline.json','utf8'));
  for(const s of [playing,resumed,final]){
   const d=s.diagnostic,at=d.metadata.at;
   const expected=t.tracks.filter(x=>t.origin+x.offset<=at).at(-1);assert(expected);
   assert.equal(d.metadata.trackId,expected.id);assert.equal(d.metadata.type,expected.type);
   if(expected.type==='track')assert.equal(s.titles[0],expected.title);
   const [mm,ss]=s.elapsed.split(':').map(Number),elapsed=mm*60+ss;
   assert(Math.abs(elapsed-(d.audibleAt-d.metadata.start))<2);
   const [dm,ds]=s.duration.split(':').map(Number);assert(Math.abs(dm*60+ds-expected.duration)<1);
  }
  report.passed=true;
 }catch(e){report.failure=e.message;throw e;}
 finally{
  report.finished=new Date().toISOString();fs.writeFileSync(out+'/report.json',JSON.stringify(report,null,2));
  await browser.close();console.log(JSON.stringify({passed:report.passed,failure:report.failure,seconds:(Date.parse(report.finished)-Date.parse(report.started))/1000}));
 }
})().catch(e=>{console.error(e.message);process.exitCode=1});
