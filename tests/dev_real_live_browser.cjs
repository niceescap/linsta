/* Real private DEV HTTP/HLS, existing verified account via an ephemeral server-signed test session.
   No password, account, role or secret is changed. Never print the cookie. */
const {chromium}=require(process.env.DIGGER_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),dataRoot=path.join(root,'instance/dev'),hlsRoot=path.join(dataRoot,'private-radio/hls');
const out=process.env.DIGGER_REAL_OUTPUT || path.join(dataRoot,'volet02-validation');
process.umask(0o077);fs.mkdirSync(out,{recursive:true,mode:0o700});
const cookie=JSON.parse(execFileSync(path.join(root,'.venv/bin/python'),['-B','-c',`
import json,sqlite3
import dev_web
app=dev_web.application
with sqlite3.connect('file:/home/jean/digger-dev/instance/dev/digger.sqlite?mode=ro',uri=True) as c:
 row=c.execute("SELECT id FROM users WHERE is_verified=1 AND role='admin' ORDER BY id LIMIT 1").fetchone()
 if not row: raise RuntimeError('Verified existing DEV account required')
value=app.session_interface.get_signing_serializer(app).dumps({'user_id':row[0],'_permanent':True})
print(json.dumps({'name':app.config['SESSION_COOKIE_NAME'],'value':value,'url':'http://127.0.0.1:17010','httpOnly':True,'sameSite':'Lax'}))
`],{cwd:root,encoding:'utf8'}));
(async()=>{
 const browser=await chromium.launch({headless:true});
 const report={started_utc:new Date().toISOString(),mode:'real private DEV HLS; actual AAC decoding, not mocked',items:[],segments:[],samples:[],metadata_checks:0,history_checks:0,errors:[]};
 let interrupted=false,last=null;
 try{
  const anonymous=await browser.newContext();
  for(const route of ['/preview-radio/now-playing','/preview-radio/hls/stream.m3u8','/morceaux/4']){
   const r=await anonymous.request.get('http://127.0.0.1:17010'+route);assert.equal(r.status(),401);
  }
  await anonymous.close();report.anonymous_access='401';
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.addCookies([cookie]);
  await context.route('**/*',route=>{
   if(new URL(route.request().url()).origin!=='http://127.0.0.1:17010') return route.abort();
   return interrupted?route.abort():route.continue();
  });
  const page=await context.newPage();
  page.on('pageerror',e=>report.errors.push(e.message));
  page.on('response',async response=>{
   if(new URL(response.url()).pathname==='/preview-radio/now-playing' && response.ok()){
    try{last={...await response.json(),requested_at:Number(new URL(response.url()).searchParams.get('at'))||null};}catch(_){}
   }
  });
  await page.goto('http://127.0.0.1:17010/');
  await page.waitForFunction(()=>!document.querySelector('.live-card [data-play]').disabled);
  if(await page.locator('audio').evaluate(a=>a.paused)) await page.locator('.live-card [data-play]').click();
  await page.waitForFunction(()=>{const a=document.querySelector('audio');return !a.paused&&a.currentTime>0&&a.readyState>=3;},{},{timeout:30000});
  await page.evaluate(()=>{window.realAudio=document.querySelector('audio');});
  await page.locator('#site-nav a[href="/a-propos"]').click();await page.waitForURL('**/a-propos');
  assert(await page.evaluate(()=>realAudio===document.querySelector('audio')&&!realAudio.paused));
  await page.locator('#site-nav a[href="/"]').click();await page.waitForURL('http://127.0.0.1:17010/');
  report.navigation='same audio, playback continuous';
  // A brief real network interruption, followed by genuine metadata/HLS requests.
  interrupted=true;await page.waitForTimeout(5000);interrupted=false;
  await page.waitForFunction(()=>!document.querySelector('audio').paused&&document.querySelector('audio').readyState>=3,{},{timeout:25000});
  report.network_recovery='5s interruption recovered';
  const started=Date.now(),seen=new Set(),copied=new Set();
  while(Date.now()-started<720000){
   fs.writeFileSync(path.join(out,'progress.json'),JSON.stringify({elapsed:Date.now()-started,last,items:report.items.length}));
   const playlist=fs.readFileSync(path.join(hlsRoot,'stream.m3u8'),'utf8');
   for(const name of playlist.split(/\r?\n/).filter(x=>/^seg_\d+\.ts$/.test(x))){
    if(copied.has(name))continue;
    try{fs.copyFileSync(path.join(hlsRoot,name),path.join(out,name),fs.constants.COPYFILE_EXCL);copied.add(name);report.segments.push(name);}catch(e){if(e.code!=='ENOENT')throw e;}
   }
   const timeline=JSON.parse(fs.readFileSync(path.join(hlsRoot,'timeline.json'),'utf8'));
   if(last?.metadata_basis==='audible-clock' && last.requested_at){
    const expected=timeline.tracks.filter(x=>timeline.origin+x.offset<=last.requested_at).at(-1);
    if(expected){assert.equal(last.track_id,expected.id);assert.equal(last.type,expected.type);report.metadata_checks++;}
    for(const entry of last.history||[]){assert(entry.ended_at<=last.requested_at+.01);report.history_checks++;}
    const key=`${last.type}:${last.track_id}:${last.playback_started_at}`;
    if(!seen.has(key)){
     seen.add(key);report.items.push({type:last.type,id:last.track_id,title:last.title,at:last.playback_started_at,observed_utc:new Date().toISOString()});
     console.log('REAL ITEM',JSON.stringify(report.items.at(-1)));
    }
   }
   const sample=await page.locator('audio').evaluate(a=>({paused:a.paused,readyState:a.readyState,currentTime:a.currentTime}));
   report.samples.push({at:new Date().toISOString(),...sample});
   assert.equal(await page.locator('audio').count(),1);
   if(report.items.filter(x=>x.type==='track').length>=2 && report.items.filter(x=>x.type==='jingle').length>=1)break;
   await page.waitForTimeout(2000);
  }
  const tracks=report.items.filter(x=>x.type==='track'),jingles=report.items.filter(x=>x.type==='jingle');
  assert(tracks.length>=2&&jingles.length>=1,'Not enough real transitions observed');
  await page.screenshot({path:path.join(out,'desktop-real.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(out,'mobile-real.png'),fullPage:true});
  report.history_visible=await page.locator('[data-history] .broadcast-item').count();
  report.cover_loaded=await page.locator('.now-grid [data-cover]').evaluate(el=>el.complete&&el.naturalWidth>0);
  assert(report.history_visible>0);assert(report.cover_loaded);assert.deepEqual(report.errors,[]);
  report.status='passed';
 }catch(e){report.status='failed';report.failure=e.message;throw e;}
 finally{
  report.finished_utc=new Date().toISOString();
  fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));
  await browser.close();
  console.log(JSON.stringify({status:report.status,items:report.items.length,segments:report.segments.length,metadata_checks:report.metadata_checks,history_checks:report.history_checks}));
 }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
