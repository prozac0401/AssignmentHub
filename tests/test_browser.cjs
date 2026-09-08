/* Real Chrome/Edge + Caddy + Streamlit/API smoke test, no server mocks.
 * Usage: node tests/test_browser.cjs PATH_TO_PRIVATE_TEST_JSON
 * JSON: [{"url":"http://127.0.0.1:18501","instance_id":"test_one",
 *         "user_id":"student","password":"..."}, ...]
 * Use dedicated test instances: this intentionally leaves completed submissions.
 * npm install --no-save playwright (or set NODE_PATH to an existing installation).
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');
const privateValues=[];
function safeError(error){let text=String(error.stack||error.message||error);for(const value of privateValues)if(value)text=text.split(value).join('[REDACTED]');return text;}
let playwright;
try{playwright=require('playwright');}catch{playwright=require(path.join(process.env.USERPROFILE||os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
async function hashFile(file){const hash=crypto.createHash('sha256');for await(const chunk of fs.createReadStream(file))hash.update(chunk);return hash.digest('hex');}
async function waitText(page,selector,text,timeout=120000){await page.locator(selector).filter({hasText:text}).waitFor({state:'visible',timeout});}
async function timeout(promise,milliseconds,message){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(message)),milliseconds);})]);}finally{clearTimeout(timer);}}
function nextDownload(context){
  const promise=new Promise((resolve,reject)=>{const pages=new Set();let timer;
    const cleanup=()=>{clearTimeout(timer);context.off('page',attach);for(const page of pages)page.off('download',done);};
    const done=download=>{cleanup();resolve(download);};
    const attach=page=>{pages.add(page);page.on('download',done);};
    context.on('page',attach);for(const page of context.pages())attach(page);
    timer=setTimeout(()=>{cleanup();reject(new Error('Native download event timed out'));},30000);
  });
  promise.catch(()=>{});return promise;
}
async function main(){
  const instances=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));assert(instances.length>0);
  for(const fixture of instances)privateValues.push(fixture.password,fixture.new_password);
  const sampleSize=Number(process.env.AH_BROWSER_FILE_BYTES||instances[0].file_bytes||(9*1024*1024+29));
  assert(Number.isSafeInteger(sampleSize)&&sampleSize>=0);
  const operationTimeout=Number(process.env.AH_BROWSER_TIMEOUT_MS||(sampleSize>=1073741824?1800000:120000));
  const started=Date.now();
  const disk=fs.statfsSync(os.tmpdir());
  const freeBytes=disk.bavail*disk.bsize;
  const requiredBytes=sampleSize*(1+instances.length)+5*1073741824;
  assert(freeBytes>=requiredBytes,`Test volume needs ${requiredBytes} free bytes including 5 GiB reserve; available=${freeBytes}`);
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'assignmenthub-browser-'));
  const sample=path.join(directory,'한글 공백 과제.txt');
  const fd=fs.openSync(sample,'w');const block=Buffer.alloc(1024*1024);
  for(let i=0;i<block.length;i++)block[i]=(i*23+71)%256;
  for(let offset=0;offset<sampleSize;offset+=block.length)fs.writeSync(fd,block,0,Math.min(block.length,sampleSize-offset));fs.closeSync(fd);
  const expected=await hashFile(sample);const errors=[];const results=[];const loggedInUploaders=[];const pendingGates=[];
  let browser;
  const launchOptions={headless:true,timeout:90000,...(os.platform()==='win32'?{channel:'msedge'}:{})};
  for(let attempt=0;attempt<2;attempt++){
    try{browser=await playwright.chromium.launch(launchOptions);break;}
    catch(error){console.error('Browser launch attempt '+(attempt+1)+': '+safeError(error).slice(0,300));if(attempt===1)throw error;await new Promise(resolve=>setTimeout(resolve,1000));}
  }
  const context=await browser.newContext({acceptDownloads:true,viewport:{width:1440,height:1080}});
  const obsoleteAuthRequests=[];
  context.on('request',request=>{if(['/api/auth/bridge','/api/auth/exchange'].includes(new URL(request.url()).pathname))obsoleteAuthRequests.push(request.url());});
  try{
    for(const instance of instances){
      console.log('Browser test: '+instance.instance_id+' Streamlit login');
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
      let websocket=false;page.on('websocket',()=>{websocket=true;});
      await page.goto(instance.url,{waitUntil:'domcontentloaded'});
      await page.getByRole('textbox',{name:'로그인 ID',exact:true}).fill(instance.user_id);
      await page.getByRole('textbox',{name:'비밀번호',exact:true}).fill(instance.password);
      await page.getByRole('button',{name:'로그인',exact:true}).click();
      await page.getByRole('button',{name:'로그아웃',exact:true}).waitFor({timeout:30000});
      if(instance.new_password){
        await page.getByRole('textbox',{name:'현재 비밀번호 또는 임시비밀번호',exact:true}).fill(instance.password);
        assert.equal(await page.getByRole('button',{name:'일회용 연결 코드 발급',exact:true}).count(),0,'No assignment access before password change');
        await page.getByRole('textbox',{name:'새 비밀번호',exact:true}).fill(instance.new_password);
        await page.getByRole('textbox',{name:'새 비밀번호 확인',exact:true}).fill(instance.new_password);
        await page.getByRole('button',{name:'비밀번호 변경',exact:true}).click();
        await page.getByRole('textbox',{name:'로그인 ID',exact:true}).fill(instance.user_id);
        await page.getByRole('textbox',{name:'비밀번호',exact:true}).fill(instance.new_password);
        await page.getByRole('button',{name:'로그인',exact:true}).click();
        await page.getByRole('button',{name:'로그아웃',exact:true}).waitFor({timeout:30000});
      }
      assert(websocket,'Streamlit websocket connected');
      console.log('Browser test: '+instance.instance_id+' direct submission');
      if(await page.getByText('과제 제출·나의 이력',{exact:true}).count())await page.getByText('과제 제출·나의 이력',{exact:true}).click();
      if(instance.assignment_title){
        await page.getByRole('combobox').first().click();
        await page.getByRole('option',{name:instance.assignment_title+' · 접수 중',exact:true}).click();
      }
      assert.equal(await page.getByRole('button',{name:'일회용 연결 코드 발급',exact:true}).count(),0);
      assert.equal(await page.locator('[data-testid="stException"]').count(),0);
      const opened=context.waitForEvent('page');
      await page.frameLocator('iframe').first().getByRole('button',{name:'파일 제출·이어 올리기 ↗',exact:true}).click();
      const upload=await opened;upload.on('pageerror',e=>errors.push(e.message));
      await upload.waitForLoadState('domcontentloaded');
      upload.on('request',request=>{if(new URL(request.url()).pathname==='/api/downloads')console.log('Native download POST Origin: '+(request.headers().origin||'(absent)'));});
      await upload.locator('#workspace').waitFor({state:'visible'});
      assert.equal(await upload.locator('#auth').isVisible(),false);
      assert.equal(await upload.locator('#bridge').count(),0);
      assert.equal(new URL(upload.url()).search,'');
      assert.equal(await upload.locator('#session-data').count(),0);
      if(instance.assignment_title)assert.equal(await upload.locator('#assignment option:checked').innerText(),instance.assignment_title);
      loggedInUploaders.push({page:upload,instance,home:page});
      console.log('Browser test: '+instance.instance_id+' real direct upload');
      await upload.locator('#files').setInputFiles(sample);
      assert.equal(await upload.locator('#start').isEnabled(),true);
      // Gate forwarding until the visible state is checked; API results stay real.
      let releaseVerification,releaseCompletion;
      const verificationGate=new Promise(resolve=>{releaseVerification=resolve;});
      const completionGate=new Promise(resolve=>{releaseCompletion=resolve;});
      pendingGates.push(()=>{releaseVerification();releaseCompletion();});
      await upload.route('**/api/uploads/*/verify',async route=>{await verificationGate;await route.continue();});
      await upload.route('**/api/uploads/*/complete',async route=>{await completionGate;await route.continue();});
      let interrupted=false;
      let signalInterrupted;
      const interruptedPromise=new Promise(resolve=>{signalInterrupted=resolve;});
      if(instance.resume_test){
        await upload.route('**/api/uploads/*/files/*?offset=*',async route=>{
          if(!interrupted&&Number(new URL(route.request().url()).searchParams.get('offset'))>0){interrupted=true;await route.abort('connectionreset');signalInterrupted();}
          else await route.continue();
        });
      }
      await upload.locator('#start').click();
      if(instance.resume_test){
        await timeout(interruptedPromise,operationTimeout,'Resume test did not reach second chunk');
        await upload.locator('#pause').click();await waitText(upload,'#status','일시 중지');
        await upload.reload();
        await upload.waitForLoadState('networkidle');
        if(!await upload.locator('#workspace').isVisible()){
          await upload.locator('#user-id').fill(instance.user_id);await upload.locator('#password').fill(instance.new_password||instance.password);
          await upload.locator('#login-form button').click();await upload.locator('#workspace').waitFor({state:'visible'});
        }
        await upload.locator('#unfinished button').first().click();
        await upload.locator('#files').setInputFiles(sample);await upload.locator('#start').click();
      }
      await waitText(upload,'#status','검증 중',operationTimeout);
      assert.equal(await upload.locator('#receipt').isVisible(),false,'No receipt before commit');
      releaseVerification();
      await waitText(upload,'#status','저장 중',operationTimeout);
      assert.equal(await upload.locator('#receipt').isVisible(),false,'No receipt while saving');
      releaseCompletion();
      await upload.locator('#receipt').waitFor({state:'visible',timeout:operationTimeout});
      console.log('Browser test: '+instance.instance_id+' native download');
      assert.match(await upload.locator('#receipt').innerText(),/제출번호/);
      const downloadEvent=nextDownload(context);
      await upload.locator('#history-list button').first().click();
      const download=await downloadEvent;const downloaded=await download.path();assert.equal(await hashFile(downloaded),expected,'Downloaded original SHA-256');
      assert.equal(fs.statSync(downloaded).size,fs.statSync(sample).size);
      if(instance.streamlit_download&&sampleSize<1073741824){
        console.log('Browser test: '+instance.instance_id+' Streamlit native download');
        await page.getByText('비밀번호 변경',{exact:true}).click();
        await page.getByRole('textbox',{name:'현재 비밀번호 또는 임시비밀번호',exact:true}).waitFor({timeout:60000});
        await page.getByText('과제 제출·나의 이력',{exact:true}).click();
        if(instance.assignment_title){
          await page.getByRole('combobox').first().click();
          await page.getByRole('option',{name:instance.assignment_title+' · 접수 중',exact:true}).click();
        }
        await page.getByRole('button',{name:'다운로드 준비',exact:true}).first().click();
        const streamlitDownloadEvent=nextDownload(context);
        await page.frameLocator('iframe').last().getByRole('button',{name:'파일 다운로드',exact:true}).click();
        const streamlitDownload=await streamlitDownloadEvent;
        assert.equal(await hashFile(await streamlitDownload.path()),expected,'Streamlit native download SHA-256');
      }
      const storage=await upload.evaluate(()=>({
        local:Object.keys(localStorage).filter(key=>!key.startsWith('stActiveTheme-')),
        session:Object.keys(sessionStorage),
        credentialPresent:[...Object.values(localStorage),...Object.values(sessionStorage)].some(value=>value.includes(state.token))}));
      assert.deepEqual(storage.local,[],'Only Streamlit display preferences may persist');assert.deepEqual(storage.session,[],'No token persisted in sessionStorage');
      assert.equal(storage.credentialPresent,false,'No login credential persisted');
      const cookies=await context.cookies();const scoped=cookies.filter(c=>c.name==='_streamlit_xsrf'&&c.path==='/ui/'+instance.instance_id+'/');
      assert.equal(scoped.length,1,'Streamlit XSRF cookie scoped to instance base path');
      await upload.screenshot({path:path.join(directory,'uploader-'+instance.instance_id+'.png'),fullPage:true,timeout:10000});
      const completed=await upload.evaluate(()=>({upload_id:state.upload.id,submission_number:state.upload.submission_number}));
      results.push({instance_id:instance.instance_id,bytes:fs.statSync(sample).size,sha256:expected,sha256_match:true,websocket:true,direct_submission:true,password_change:!!instance.new_password,browser_reload_resume:!!instance.resume_test,cookie_path:scoped[0].path,upload_url:new URL(upload.url()).pathname,...completed});
    }
    for(const {page} of loggedInUploaders){
      const status=await page.evaluate(async()=>{const response=await fetch('/api/auth/me',{headers:{Authorization:'Bearer '+state.token},credentials:'omit'});return response.status;});
      assert.equal(status,200,'Prior instance login survives other port login');
    }
    if(loggedInUploaders.length>1){
      const foreignToken=await loggedInUploaders[1].page.evaluate(()=>state.token);
      const denied=await context.request.get(instances[0].url+'/api/auth/me',{headers:{Authorization:'Bearer '+foreignToken}});
      assert.equal(denied.status(),401,'Other instance bearer token rejected');
    }
    const attached=loggedInUploaders.find(entry=>!entry.instance.resume_test);
    if(attached){
      await attached.home.getByRole('button',{name:'로그아웃',exact:true}).click();
      await attached.home.getByRole('button',{name:'로그인',exact:true}).waitFor();
      const revoked=await attached.page.evaluate(async()=>{const response=await fetch('/api/quota',{headers:{Authorization:'Bearer '+state.token}});return response.status;});
      assert.equal(revoked,401,'Logging out of the class revokes the submission window');
    }
    assert.deepEqual(obsoleteAuthRequests,[],'No connection code requests');
    assert.deepEqual(errors,[],'No browser script errors');
    const report={passed:true,browser:browser.version(),platform:os.platform()+' '+os.release(),elapsed_seconds:(Date.now()-started)/1000,results,artifacts:directory};
    fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
  }catch(error){
    console.error('Browser failure: '+safeError(error));
    console.error('Failure artifacts: '+directory);
    let n=0;for(const page of context.pages()){
      await page.screenshot({path:path.join(directory,'failure-'+n+'.png'),fullPage:true,timeout:5000,mask:[page.locator('[data-testid="stCode"]'),page.locator('input[type="password"]')]}).catch(()=>{});
      const safe=await page.locator('body').evaluate(body=>{const clone=body.cloneNode(true);clone.querySelectorAll('[data-testid="stCode"],input[type="password"],script,style').forEach(node=>node.remove());return clone.textContent;},undefined,{timeout:5000}).catch(()=>'');
      fs.writeFileSync(path.join(directory,'failure-'+n+'.txt'),safe);n++;
    }
    throw error;
  }finally{for(const release of pendingGates)release();await context.close();await browser.close();}
}
main().catch(error=>{console.error(safeError(error));process.exitCode=1;});
