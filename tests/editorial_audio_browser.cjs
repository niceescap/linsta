/* Real hls.js decoding, synthetic offline HLS fixture. No radio engine/server. */
const {chromium}=require(process.env.DIGGER_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=process.env.DIGGER_PREVIEW_DIR||'/tmp/digger-volet01-preview';
const pages=JSON.parse(fs.readFileSync(path.join(out,'pages.json')));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--autoplay-policy=no-user-gesture-required']});
 try{
  const context=await browser.newContext();
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.hostname!=='digger.test') return route.abort();
   if(pages[url.pathname]) return route.fulfill({body:pages[url.pathname],contentType:'text/html'});
   if(url.pathname==='/preview-radio/now-playing') return route.fulfill({json:{available:true,type:'jingle',title:'Signal synthétique de test',artist:'Fixture isolée',cover_url:'/static/digger-icon.png'}});
   if(url.pathname.startsWith('/preview-radio/hls/')) return route.fulfill({path:path.join(out,'audio',path.basename(url.pathname)),contentType:url.pathname.endsWith('.m3u8')?'application/vnd.apple.mpegurl':'video/mp2t'});
   if(url.pathname.startsWith('/static/')) return route.fulfill({path:path.join(root,url.pathname)});
   return route.fulfill({status:404,body:''});
  });
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://digger.test/');
  await page.waitForFunction(()=>{const a=document.querySelector('audio');return !a.paused&&a.currentTime>1&&a.readyState>=3;},{},{timeout:20000});
  const first=await page.evaluate(()=>{window.savedAudio=document.querySelector('audio');return savedAudio.currentTime;});
  await page.locator('#site-nav a[href="/a-propos"]').click();await page.waitForURL('**/a-propos');
  await page.waitForFunction(t=>document.querySelector('audio').currentTime>t+.5,first);
  assert(await page.evaluate(()=>savedAudio===document.querySelector('audio')));
  await page.locator('.mini-player [data-play]').click();
  assert(await page.evaluate(()=>document.querySelector('audio').paused));
  await page.locator('.mini-player [data-play]').click();
  await page.waitForFunction(()=>!document.querySelector('audio').paused&&document.querySelector('audio').readyState>=3);
  assert.equal(await page.locator('audio').count(),1);
  assert.deepEqual(errors,[]);
  console.log('PASS: real hls.js '+await page.evaluate(()=>Hls.version)+', AAC synthetic decoding, permitted autoplay, continuous audio across navigation, pause/resume, one audio.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
