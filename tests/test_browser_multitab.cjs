/* Four real same-account tabs, small actual files. Large four-tab testing is separate. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict');
let playwright;try{playwright=require('playwright');}catch{playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
async function main(){
  const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'))[0];const started=Date.now();
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'assignmenthub-multitab-'));
  const sample=path.join(dir,'동시 탭 제출.bin');const data=Buffer.alloc(262157);for(let i=0;i<data.length;i++)data[i]=(i*13+37)%256;fs.writeFileSync(sample,data);const digest=crypto.createHash('sha256').update(data).digest('hex');
  const browser=await playwright.chromium.launch({headless:true,timeout:90000,...(os.platform()==='win32'?{channel:'msedge'}:{})});const context=await browser.newContext();const errors=[];
  try{
    const pages=[];for(let i=0;i<4;i++){const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));pages.push(page);}
    await Promise.all(pages.map(async page=>{await page.goto(fixture.url+'/upload');await page.locator('#user-id').fill(fixture.user_id);await page.locator('#password').fill(fixture.password);await page.locator('#login-form button').click();await page.locator('#workspace').waitFor({state:'visible',timeout:60000});await page.locator('#files').setInputFiles(sample);}));
    await Promise.all(pages.map(page=>page.locator('#start').click()));
    await Promise.all(pages.map(page=>page.locator('#receipt').waitFor({state:'visible',timeout:120000})));
    const uploads=await Promise.all(pages.map(page=>page.evaluate(()=>({id:state.upload.id,submission_number:state.upload.submission_number,status:state.upload.status,bytes:state.upload.total_bytes,sha256:state.upload.files[0].stored_sha256}))));
    assert.equal(new Set(uploads.map(u=>u.id)).size,4);assert.equal(new Set(uploads.map(u=>u.submission_number)).size,4);
    for(const u of uploads){assert.equal(u.status,'completed');assert.equal(u.bytes,data.length);assert.equal(u.sha256,digest);}assert.deepEqual(errors,[]);
    const report={passed:true,scope:'four real same-account browser tabs, small files',browser:browser.version(),bytes_per_file:data.length,concurrent_tabs:4,sha256_match:true,elapsed_seconds:(Date.now()-started)/1000,uploads};
    fs.writeFileSync(path.join(dir,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({...report,artifacts:dir},null,2));
  }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
