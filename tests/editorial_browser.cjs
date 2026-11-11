/* Run after export_editorial_preview.py. All HTTP is intercepted: no server/radio. */
const {chromium} = require(process.env.DIGGER_PLAYWRIGHT || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname,'..');
const out = process.env.DIGGER_PREVIEW_DIR || '/tmp/digger-volet01-preview';
const pages = JSON.parse(fs.readFileSync(path.join(out,'pages.json')));
const base='http://digger.test';
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  for (const native of [true,false]) {
   const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce'});
   let metadata={available:false}, code=200;
   const errors=[];
   const layout=[];
   await context.addInitScript(({native})=>{
    window.audioCalls={play:0,pause:0,instances:0,destroyed:0,fail:false};
    const p=HTMLMediaElement.prototype;
    Object.defineProperty(p,'paused',{get(){return !this._playing;}});
    Object.defineProperty(p,'seekable',{get(){return {length:1,start:()=>0,end:()=>104};}});
    p.canPlayType=()=>native?'probably':'';
    p.play=function(){window.audioCalls.play++; if(window.audioCalls.fail) return Promise.reject(new DOMException('Blocked','NotAllowedError'));this._playing=true;this.dispatchEvent(new Event('playing'));return Promise.resolve();};
    p.pause=function(){window.audioCalls.pause++;this._playing=false;this.dispatchEvent(new Event('pause'));};
    p.load=function(){};
    p.getStartDate=()=>new Date(NaN);
    window.Hls=class {
     static isSupported(){return true;} static Events={ERROR:'error'};
     constructor(){window.audioCalls.instances++;this.liveSyncPosition=100;}
     loadSource(){} attachMedia(){} on(_,fn){window.hlsError=fn;} destroy(){window.audioCalls.destroyed++;}
    };
   },{native});
   await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.hostname==='cdn.jsdelivr.net' || url.pathname==='/static/editorial/vendor/hls.min.js') return route.fulfill({body:'',contentType:'text/javascript'});
    if(url.hostname!=='digger.test') return route.abort();
    if(url.pathname==='/preview-radio/now-playing') return route.fulfill({status:code,json:metadata});
    if(pages[url.pathname]) return route.fulfill({body:pages[url.pathname],contentType:'text/html'});
    let file=path.join(root,url.pathname);
    if(url.pathname.startsWith('/preview-radio/tracks/')) file=path.join(root,'static/digger-icon.png');
    if(url.pathname.startsWith('/static/') || url.pathname.startsWith('/preview-radio/tracks/')) {
     if(fs.existsSync(file)) return route.fulfill({path:file});
    }
    return route.fulfill({status:404,body:'Not found'});
   });
   const page=await context.newPage();
   page.on('pageerror',error=>errors.push(error.message));
   await page.goto(base);await page.evaluate(()=>document.fonts.ready);
   assert.equal(await page.locator('audio').count(),1);
   assert.equal(await page.locator('[data-live]').first().textContent(),'DIRECT INDISPONIBLE');
   // Render screenshots without fabricated music or a simulated live badge.
   if(native){
    for(const width of [1440,1920,2560,3440,1024,768,390,320]){
     await page.setViewportSize({width,height:width<500?844:1100});
     await page.evaluate(()=>document.fonts.ready);
     if(width===390){
      await page.locator('.menu-toggle').click();
      assert.equal(await page.locator('.menu-toggle').getAttribute('aria-expanded'),'true');
      await page.locator('#site-nav a').first().focus();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.menu-toggle').getAttribute('aria-expanded'),'false');
      assert(await page.locator('.menu-toggle').evaluate(el=>el===document.activeElement));
     }
     assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
     assert(await page.locator('.session-link').isVisible(),`session entry hidden at ${width}`);
     const emptyHeight=await page.locator('.upcoming').evaluate(el=>el.getBoundingClientRect().height);
     assert(emptyHeight<=180,`empty upcoming too tall at ${width}: ${emptyHeight}`);
     const miniHeight=await page.locator('.mini-player').evaluate(el=>el.getBoundingClientRect().height);
     assert(miniHeight<=70,`mini-player too tall at ${width}: ${miniHeight}`);
     const openingHeight=await page.locator('.opening-grid').evaluate(el=>el.getBoundingClientRect().height);
     if(width===1440) assert(openingHeight<=320,`empty opening row too tall: ${openingHeight}`);
     const contentWidth=await page.locator('#content').evaluate(el=>el.getBoundingClientRect().width);
     if(width===1440){
      const sizes=await page.evaluate(()=>{
       const size=s=>parseFloat(getComputedStyle(document.querySelector(s)).fontSize);
       return {nav:size('#site-nav a'),signature:size('.wordmark small'),description:size('.editorial-card p:not(.eyebrow)'),button:size('.contribute')};
      });
      assert(sizes.nav>=15&&sizes.signature>=11&&sizes.description>=15&&sizes.button>=14);
     }
     if(width>=1024 && width<=2560) assert(contentWidth/width>=.90&&contentWidth/width<=.94,`narrow content at ${width}: ${contentWidth}`);
     if(width===3440) assert.equal(contentWidth,2560);
     layout.push({width,contentWidth,emptyHeight,miniHeight,openingHeight});
     assert.equal(await page.locator('.editorial-photo').count(),3);
     for(const selector of ['.editorial-card:last-child .pill','.genres a:last-child']){
      const link=page.locator(selector);
      await link.evaluate(el=>el.scrollIntoView({block:'end'}));
      const bottom=await link.evaluate(el=>el.getBoundingClientRect().bottom);
      const playerTop=await page.locator('.mini-player').evaluate(el=>el.getBoundingClientRect().top);
      assert(bottom<=playerTop,`fixed player masks ${selector} at ${width}`);
     }
     await page.evaluate(()=>scrollTo(0,0));
     await page.evaluate(()=>document.activeElement.blur());
     await page.screenshot({path:path.join(out,`home-${width}.png`),fullPage:true});
     if(width===1440 || width===390) await page.screenshot({path:path.join(out,`viewport-${width}.png`)});
    }
    await page.setViewportSize({width:1440,height:1100});
    fs.writeFileSync(path.join(out,'layout.json'),JSON.stringify(layout,null,2));
   }
   await page.evaluate(()=>{window.audioCalls.fail=true;window.originalAudio=document.querySelector('audio');});
   metadata={available:true,track_id:1,title:'Real fixture',artist:'Fixture artist',cover_url:'/preview-radio/tracks/1/cover',next_track_id:2,next_title:'Reserved fixture',next_artist:'Fixture artist'};
   await page.waitForFunction(()=>document.querySelector('[data-play]').textContent==='Activer le son');
   assert.equal(await page.evaluate(()=>window.audioCalls.play),1);
   await page.evaluate(()=>{window.audioCalls.fail=false;});
   await page.locator('.live-card [data-play]').click();
   await page.waitForFunction(()=>!document.querySelector('audio').paused);
   await page.locator('audio').evaluate(a=>a.dispatchEvent(new Event('loadedmetadata')));
   assert.equal(await page.evaluate(()=>document.querySelector('audio').currentTime),native?92:100);
   // A healthy native `stalled` notification must not destroy a progressing buffer.
   const beforeStall=await page.evaluate(()=>({...window.audioCalls}));
   await page.locator('audio').evaluate(a=>{a.currentTime+=1;a.dispatchEvent(new Event('timeupdate'));a.dispatchEvent(new Event('stalled'));});
   await page.waitForTimeout(3300);
   assert.equal(await page.evaluate(()=>window.audioCalls.play),beforeStall.play);
   assert.equal(await page.evaluate(()=>window.audioCalls.destroyed),beforeStall.destroyed);
   const position=await page.locator('audio').evaluate(a=>a.currentTime);
   await page.locator('audio').evaluate(a=>a.dispatchEvent(new Event('loadedmetadata')));
   assert.equal(await page.locator('audio').evaluate(a=>a.currentTime),position,'metadata notification must not seek again');
   code=503;
   await page.waitForTimeout(2200);
   assert(await page.evaluate(()=>!document.querySelector('audio').paused),'metadata failure must preserve audio');
   assert.equal(await page.evaluate(()=>window.audioCalls.destroyed),beforeStall.destroyed);
   code=200;
   await page.locator('audio').evaluate(a=>{a.currentTime+=1;a.dispatchEvent(new Event('timeupdate'));});
   await page.waitForTimeout(2200);
   await page.locator('[data-volume]').fill('35');
   assert.equal(await page.evaluate(()=>document.querySelector('audio').volume),.35);
   assert.equal(await page.locator('[data-next-title]').textContent(),'Reserved fixture');
   assert(await page.locator('[data-next-link]').isVisible());
   assert.equal(await page.locator('[data-next-link]').getAttribute('href'),'/morceaux/2');
   assert(await page.locator('[data-next-cover]').evaluate(el=>el.complete&&el.naturalWidth>0));
   await page.locator('.session-link').click();
   await page.waitForURL('**/mon-espace');
   assert(await page.evaluate(()=>window.originalAudio===document.querySelector('audio')&&!document.querySelector('audio').paused));
   assert(await page.locator('form[action="/logout-form"] input[name="csrf_token"]').count());
   await page.locator('#content a[href="/"]').click();
   await page.waitForURL(base+'/');
   await page.locator('#site-nav a[href="/decouvrir"]').click();
   await page.waitForURL('**/decouvrir');
   assert(await page.evaluate(()=>window.originalAudio===document.querySelector('audio')&&!document.querySelector('audio').paused));
   assert.match(await page.title(),/Découvrir/);
   assert.equal(await page.locator('#content').evaluate(el=>el===document.activeElement),true);
   await page.goBack();await page.waitForURL(base+'/');
   await page.waitForFunction(()=>!document.querySelector('#hero').hidden);
   await page.locator('.now-grid [data-track-link]').click();await page.waitForURL('**/morceaux/1');
   assert(await page.evaluate(()=>window.originalAudio===document.querySelector('audio')&&!document.querySelector('audio').paused));
   await page.locator('.mini-player [data-play]').click();
   assert(await page.evaluate(()=>document.querySelector('audio').paused));
   await page.locator('.mini-player [data-play]').click();
   await page.waitForFunction(()=>!document.querySelector('audio').paused);
   assert.equal(await page.evaluate(()=>window.audioCalls.instances-window.audioCalls.destroyed),native?0:1);
   await page.locator('.mini-player [data-mute]').click();
   assert.equal(await page.locator('.mini-player [data-mini-status]').textContent(),'Son coupé');
   metadata={available:false,status:'inactive'};
   await page.waitForFunction(()=>document.querySelector('audio').paused);
   assert.equal(await page.locator('[data-live]').first().textContent(),'DIRECT INDISPONIBLE');
   metadata={available:true,track_id:1,title:'Recovered'};
   await page.waitForFunction(()=>!document.querySelector('audio').paused);
   if(!native){await page.evaluate(()=>window.hlsError(null,{fatal:true}));await page.waitForTimeout(3300);assert.equal(await page.evaluate(()=>window.audioCalls.instances-window.audioCalls.destroyed),1);}
   code=401;
   await page.waitForFunction(()=>document.querySelector('[data-mini-status]').textContent.includes('Session expirée'));
   assert(await page.evaluate(()=>document.querySelector('audio').paused));
   await page.reload(); // A direct URL must render independently too.
   assert.match(await page.locator('#content h1').textContent(),/Track 1/);
   assert.deepEqual(errors,[]);
   await context.close();
  }
  console.log('PASS: responsive 320–3440, 93% content width and 2560px cap, readable typography, compact empty upcoming, mini-player <=70px, CTA clearance, photographic slots, autoplay blocked, native/HLS paths (media mocked), live seek, persistent navigation/back/direct link, focus, volume mute, recovery, auth expiry, one audio/HLS.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
