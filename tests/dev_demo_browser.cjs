/* Actual public HTTPS / real HLS. No account, cookie injection or media mock. */
const {chromium}=require('/tmp/digger-volet01-browser/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
const root='/home/jean/digger-dev/instance/dev/mobile-demo';
process.umask(0o077);
(async()=>{
 const link=fs.readFileSync(root+'/link.txt','utf8').trim(),base=link.split('/#')[0];
 const browser=await chromium.launch({headless:true});
 const report={started:new Date().toISOString(),samples:[],errors:[],httpErrors:[]};
 try{
  const anon=await browser.newContext();
  for(const p of ['/preview-radio/hls/stream.m3u8','/preview-radio/now-playing','/preview-radio/tracks/4/cover'])
   assert.equal((await anon.request.get(base+p)).status(),401);
  assert.equal((await anon.request.get(base+'/admin')).status(),403);
  await anon.close();
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const page=await context.newPage();
  page.on('pageerror',e=>report.errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400)report.httpErrors.push({path:new URL(r.url()).pathname,status:r.status()})});
  // Live HLS keeps requesting media; networkidle is not a readiness condition.
  await page.goto(link,{waitUntil:'domcontentloaded'});
  await page.waitForSelector('audio[data-enabled="true"]',{state:'attached'});
  assert.equal(new URL(page.url()).hash,'');
  assert.equal(await page.locator('audio').count(),1);
  assert.equal(await page.locator('a[href="/admin"]').count(),0);
  for(const p of ['/admin','/admin/preview-radio','/submissions','/login','/preview-radio/tracks/4/audio'])
   assert.equal((await context.request.get(base+p)).status(),403);
  assert.equal((await context.request.post(base+'/')).status(),403);
  const metadata=await context.request.get(base+'/preview-radio/now-playing');
  assert.equal(metadata.status(),200); assert.equal((await metadata.json()).available,true);
  const manifest=await context.request.get(base+'/preview-radio/hls/stream.m3u8');
  assert.equal(manifest.status(),200);
  const text=await manifest.text(); assert(text.startsWith('#EXTM3U'));
  const segment=text.split('\n').find(l=>/^seg_\d+\.ts$/.test(l)); assert(segment);
  const media=await context.request.get(base+'/preview-radio/hls/'+segment);
  assert.equal(media.status(),200); assert((await media.body()).length>1000);
  if(await page.locator('audio').evaluate(a=>a.paused))
   await page.locator('[data-play]').first().click();
  await page.waitForFunction(()=>{const a=document.querySelector('audio');return !a.paused&&a.currentTime>0},{timeout:30000});
  const initial=await page.evaluate(()=>document.querySelector('audio').currentTime);
  await page.screenshot({path:root+'/mobile.png',fullPage:true});
  await page.locator('#site-nav a[href="/a-propos"]').evaluate(a=>a.click());
  await page.waitForURL(base+'/a-propos');
  for(let i=0;i<8;i++){
   await page.waitForTimeout(5000);
   report.samples.push(await page.evaluate(()=>{const a=document.querySelector('audio');return {time:a.currentTime,paused:a.paused,error:a.error?.code||null,audioCount:document.querySelectorAll('audio').length}}));
  }
  assert(report.samples.at(-1).time>initial+30);
  assert(report.samples.every(s=>!s.paused&&!s.error&&s.audioCount===1));
  const visible=await page.evaluate(()=>({at:diggerRadioDiagnostics().audibleAt,
   titles:[...document.querySelectorAll('[data-title]')].map(e=>e.textContent),
   artists:[...document.querySelectorAll('[data-artist]')].map(e=>e.textContent)}));
  const synchronized=await context.request.get(base+'/preview-radio/now-playing?at='+visible.at);
  assert.equal(synchronized.status(),200);
  const actual=await synchronized.json();assert.equal(actual.metadata_basis,'audible-clock');
  assert(visible.titles.every(t=>t===actual.title));
  assert(visible.artists.every(a=>a===(actual.type==='jingle'?'Identifiant de station':actual.artist)));
  report.synchronizedMetadata={title:actual.title,artist:actual.artist,type:actual.type};
  assert.deepEqual(report.errors,[]); assert.deepEqual(report.httpErrors,[]);
  report.passed=true; report.seconds=report.samples.at(-1).time-initial;
  console.log(JSON.stringify({passed:true,seconds:report.seconds,https:true,hls:true,anonymousBlocked:true,adminBlocked:true,navigation:true}));
 }finally{
  report.finished=new Date().toISOString();fs.writeFileSync(root+'/browser-report.json',JSON.stringify(report,null,2));
  await browser.close();
 }
})().catch(e=>{console.error(e.message);process.exitCode=1});
