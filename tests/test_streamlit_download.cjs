/* Optional fresh-login Streamlit native-download test; use dedicated test credentials JSON. */
'use strict';
const fs=require('node:fs');const path=require('node:path');const os=require('node:os');const assert=require('node:assert/strict');const crypto=require('node:crypto');
let playwright;try{playwright=require('playwright');}catch{playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
async function hashFile(file){const h=crypto.createHash('sha256');for await(const b of fs.createReadStream(file))h.update(b);return h.digest('hex');}
async function main(){
  const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'))[0];
  const login=await fetch(fixture.url+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:fixture.user_id,password:fixture.password})});const auth=await login.json();assert(auth.token);
  const response=await fetch(fixture.url+'/api/submissions?all_versions=false',{headers:{Authorization:'Bearer '+auth.token}});const submissions=await response.json();assert(submissions.length);const expected=submissions[0].files[0];
  let browser;try{browser=await playwright.chromium.launch({channel:'msedge',headless:true});}catch{browser=await playwright.chromium.launch({headless:true});}
  const context=await browser.newContext({acceptDownloads:true});const page=await context.newPage();
  try{
    console.log('Streamlit download: login');await page.goto(fixture.url);
    page.on('request',request=>{if(new URL(request.url()).pathname==='/api/downloads')console.log('Streamlit POST Origin: '+(request.headers().origin||'(absent)'));});
    await page.getByRole('textbox',{name:'로그인 ID',exact:true}).fill(fixture.user_id);await page.getByRole('textbox',{name:'비밀번호',exact:true}).fill(fixture.password);await page.getByRole('button',{name:'로그인',exact:true}).click();
    await page.getByRole('button',{name:'다운로드 준비',exact:true}).first().waitFor({timeout:60000});console.log('Streamlit download: ticket');
    await page.getByRole('button',{name:'다운로드 준비',exact:true}).first().click();
    let resolveDownload;const downloadPromise=new Promise(resolve=>{resolveDownload=resolve;});context.on('page',p=>p.on('download',resolveDownload));for(const p of context.pages())p.on('download',resolveDownload);
    await page.frameLocator('iframe').last().getByRole('button',{name:'파일 다운로드',exact:true}).click();
    let timer;const download=await Promise.race([downloadPromise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('No native download')),30000);})]).finally(()=>clearTimeout(timer));
    const downloaded=await download.path();assert.equal(await hashFile(downloaded),expected.sha256);assert.equal(fs.statSync(downloaded).size,expected.size);
    console.log(JSON.stringify({passed:true,streamlit_native_download:true,bytes:expected.size,sha256_match:true,browser:browser.version()}));
  }catch(error){console.error('Streamlit download failed: '+error.message);const out=path.join(os.tmpdir(),'assignmenthub-streamlit-download-failure.txt');fs.writeFileSync(out,await page.locator('body').innerText({timeout:5000}).catch(()=>''));console.error('Failure text: '+out);throw error;
  }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
