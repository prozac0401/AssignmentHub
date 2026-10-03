'use strict';
// Focused real-browser UI regression. API responses are synthetic; no live course is contacted.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');

async function main(){
  const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  const checks=[],errors=[];let mutations=[];
  const root=path.resolve(__dirname,'../assignmenthub/static');
  const assignment={id:'assignment-1',title:'Synthetic assignment',is_open:true,allowed_file_categories:['other']};
  const unfinished={id:'upload-1',assignment_id:assignment.id,assignment_title:assignment.title,status:'paused',total_bytes:10,files:[{id:'file-1',name:'missing-original.txt',size:10,offset:4,sha256:'0'.repeat(64)}]};
  let uploads=[unfinished];
  const page=await browser.newPage({viewport:{width:1000,height:900}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    const pathname=url.pathname;
    if(request.method()!=='GET')mutations.push({method:request.method(),path:pathname});
    if(pathname==='/upload')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'upload.html'),'utf8').replace('<!-- authenticated-session -->','<script id="session-data" type="application/json">'+JSON.stringify({token:'synthetic-only',user:{name:'Synthetic',user_id:'test-user'},must_change_password:false})+'</script>')});
    if(pathname.startsWith('/upload-static/')){
      const filename=pathname.slice('/upload-static/'.length);
      if(!['upload.js','upload.css','sha256.js'].includes(filename))return route.abort();
      return route.fulfill({contentType:filename.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(path.join(root,filename),'utf8')});
    }
    const responses={
      '/api/info':{course_name:'Synthetic recovery test',timezone:'Asia/Seoul',file_categories:[{id:'other',label:'기타',extensions:[]}]},
      '/api/assignments':[assignment],
      '/api/quota':{used_bytes:0,reserved_bytes:uploads.length?10:0,quota_bytes:100,max_file_bytes:100,max_files:10,chunk_bytes:8},
      '/api/uploads':uploads,
      '/api/submissions':[]
    };
    if(pathname==='/api/uploads/upload-1/cancel'&&request.method()==='POST'){uploads=[];return route.fulfill({json:{...unfinished,status:'cancelled'}});}
    if(pathname in responses)return route.fulfill({json:responses[pathname]});
    errors.push('Unexpected request: '+request.method()+' '+pathname);return route.abort();
  });
  const checked=name=>checks.push(name);
  try{
    await page.goto('http://127.0.0.1:9876/upload');
    await page.locator('#workspace').waitFor({state:'visible'});
    assert.equal(await page.locator('#cancel').isVisible(),false);checked('cancel hidden before choosing a task');
    await page.locator('#unfinished button').click();
    assert.equal(await page.locator('#cancel').isVisible(),true,'Recovered task must offer cancellation before original files are reselected');checked('cancel visible immediately after recovery selection');
    assert.equal(await page.locator('#start').isEnabled(),false);checked('resuming still requires original files');
    assert.equal(await page.locator('#pause').isVisible(),false);checked('pause hidden while not transferring');
    assert.equal(await page.locator('#assignment').isEnabled(),false);checked('recovered assignment stays locked');
    assert.deepEqual(mutations,[]);checked('choosing recovery performs no mutation or cancellation');
    await page.locator('#fresh').click();
    assert.equal(await page.locator('#progress-panel').isVisible(),false);assert.equal(await page.locator('#assignment').isEnabled(),true);assert.deepEqual(mutations,[]);checked('new submission leaves old task intact');
    await page.locator('#unfinished button').click();
    assert.equal(await page.locator('#cancel').isVisible(),true);checked('repeated recovery selection restores cancel');
    await page.locator('#cancel').click();
    await page.waitForFunction(()=>!state.cancelling);
    assert.deepEqual(mutations,[{method:'POST',path:'/api/uploads/upload-1/cancel'}]);checked('explicit cancel uses only existing endpoint once');
    assert.match(await page.locator('#notice').innerText(),/취소했습니다/);assert.equal(await page.locator('#progress-panel').isVisible(),false);assert.equal(await page.locator('#cancel').isVisible(),false);checked('cancel resets recovery UI');
    assert.match(await page.locator('#unfinished').innerText(),/이어 올릴 작업이 없습니다/);checked('unfinished list refreshed after cancellation');
    for(const status of ['completed','failed','cancelled','expired']){
      await page.evaluate(status=>{state.upload={status};controls(false);},status);
      assert.equal(await page.locator('#cancel').isVisible(),false,status+' must not expose cancellation');
    }checked('existing terminal-state guard preserved');
    assert.deepEqual(errors,[]);checked('no browser errors or unexpected requests');
    console.log(JSON.stringify({passed:true,browser:browser.version(),checks,scope:'Real Chromium DOM with mocked API; not live backend or full end-to-end'},null,2));
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
