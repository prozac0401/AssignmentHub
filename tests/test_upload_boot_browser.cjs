/* Real browser initialization, resource failure, and recovery checks.
 * Supply a private JSON file: {url, user_id, password, output} for a disposable course.
 * This test intentionally creates a small completed submission for that student.
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
let playwright;
try{playwright=require('playwright');}catch{playwright=require(path.join(process.env.USERPROFILE||os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}

async function main(){
  const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const output=path.resolve(fixture.output);fs.mkdirSync(output,{recursive:true});
  const browser=await playwright.chromium.launch({headless:true,...(os.platform()==='win32'?{channel:'msedge'}:{})});
  const context=await browser.newContext({viewport:{width:1280,height:1000}});
  const errors=[],checks=[];let releaseInfo;
  const checked=name=>{checks.push(name);console.log('Passed: '+name);};
  context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  try{
    const home=await context.newPage();
    await home.goto(fixture.url,{waitUntil:'domcontentloaded'});
    await home.getByRole('textbox',{name:'로그인 ID',exact:true}).fill(fixture.user_id);
    await home.getByRole('textbox',{name:'비밀번호',exact:true}).fill(fixture.password);
    await home.getByRole('button',{name:'로그인',exact:true}).click();
    await home.getByRole('button',{name:'로그아웃',exact:true}).waitFor();
    async function openSubmission(){
      const opened=context.waitForEvent('page');
      await home.frameLocator('iframe').first().getByRole('button',{name:'파일 제출·이어 올리기 ↗',exact:true}).click();
      const page=await opened;await page.waitForLoadState('domcontentloaded');return page;
    }
    async function loginDirect(page){
      await page.locator('#user-id').fill(fixture.user_id);
      await page.locator('#password').fill(fixture.password);
      await page.locator('#login-form button').click();
    }
    async function assertRecoverable(page,message){
      await page.locator('#notice').filter({hasText:message}).waitFor({state:'visible',timeout:25000});
      assert(await page.locator('#auth').isVisible());
      assert.equal(await page.locator('#workspace').isVisible(),false);
      assert.equal(await page.locator('#loading-panel').isVisible(),false);
    }

    const normal=await openSubmission();
    await normal.locator('#workspace').waitFor({state:'visible'});
    for(const selector of ['#assignment','#files','#start','#quota-used','#unfinished','#history'])assert(await normal.locator(selector).isVisible(),selector);
    assert.equal(await normal.locator('#auth').isVisible(),false);
    assert.equal(await normal.locator('#loading-panel').isVisible(),false);
    assert.equal(await normal.locator('#start').isEnabled(),false);
    await normal.screenshot({path:path.join(output,'normal-empty.png'),fullPage:true});
    await normal.locator('#files').setInputFiles({name:'화면 확인 과제.txt',mimeType:'text/plain',buffer:Buffer.from('파일 제출 화면과 해시 작업 확인','utf8')});
    assert(await normal.locator('#start').isEnabled());
    await normal.locator('#start').click();
    await normal.locator('#receipt').waitFor({state:'visible',timeout:60000});
    assert.match(await normal.locator('#receipt').innerText(),/제출번호/);
    checked('normal_submission_with_hash_worker');
    await normal.close();

    const blockScript=route=>route.abort('blockedbyclient');
    await context.route('**/upload-static/upload.js',blockScript);
    const blocked=await openSubmission();
    await blocked.waitForLoadState('networkidle');
    assert(await blocked.locator('#loading-panel').isVisible());
    assert.match(await blocked.locator('#loading-panel').innerText(),/다시 누르세요/);
    assert.equal(await blocked.locator('#workspace').isVisible(),false);
    await blocked.screenshot({path:path.join(output,'script-unavailable.png'),fullPage:true});
    checked('blocked_script_has_visible_guidance');
    await blocked.close();await context.unroute('**/upload-static/upload.js',blockScript);

    let infoRequested;
    const requested=new Promise(resolve=>{infoRequested=resolve;});
    const pending=new Promise(resolve=>{releaseInfo=resolve;});
    const delayInfo=async route=>{infoRequested();await pending;try{await route.abort();}catch{}};
    await context.route('**/api/info',delayInfo);
    const stalled=await openSubmission();await requested;
    assert(await stalled.locator('#loading-panel').isVisible());
    await assertRecoverable(stalled,'시간이 초과되었습니다');
    await stalled.screenshot({path:path.join(output,'load-timeout.png'),fullPage:true});
    releaseInfo();await context.unroute('**/api/info',delayInfo);
    await loginDirect(stalled);await stalled.locator('#workspace').waitFor({state:'visible'});
    checked('info_timeout_and_login_recovery');await stalled.close();

    const rejectQuota=route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'검증용 저장 용량 조회 실패'})});
    await context.route('**/api/quota',rejectQuota);
    const rejected=await openSubmission();
    await assertRecoverable(rejected,'검증용 저장 용량 조회 실패');
    const loginRejected=rejected.waitForResponse(response=>new URL(response.url()).pathname==='/api/quota'&&response.status()===503);
    await loginDirect(rejected);
    await loginRejected;
    await rejected.waitForFunction(()=>!document.querySelector('#login-form button').disabled);
    await assertRecoverable(rejected,'검증용 저장 용량 조회 실패');
    await context.unroute('**/api/quota',rejectQuota);
    await loginDirect(rejected);await rejected.locator('#workspace').waitFor({state:'visible'});
    checked('quota_failure_and_repeated_login_recovery');await rejected.close();

    const corruptSession=async route=>{
      const response=await route.fetch();
      const html=await response.text();
      const corrupted=html.replace(/(<script id="session-data" type="application\/json">)[\s\S]*?(<\/script>)/,'$1{$2');
      assert.notEqual(corrupted,html);
      await route.fulfill({response,body:corrupted});
    };
    await context.route('**/upload',corruptSession);
    const corrupted=await openSubmission();
    await assertRecoverable(corrupted,'로그인 정보를 읽지 못했습니다');
    assert.equal(await corrupted.locator('#session-data').count(),0);
    await context.unroute('**/upload',corruptSession);
    await loginDirect(corrupted);await corrupted.locator('#workspace').waitFor({state:'visible'});
    checked('invalid_session_data_has_recovery');await corrupted.close();

    const noScripts=await browser.newContext({javaScriptEnabled:false});
    try{
      const page=await noScripts.newPage();await page.goto(fixture.url+'/upload');
      assert(await page.locator('#loading-panel noscript').isVisible());
      assert.match(await page.locator('#loading-panel noscript').innerText(),/JavaScript가 꺼져 있습니다/);
      checked('javascript_disabled_guidance');
    }finally{await noScripts.close();}
    assert.deepEqual(errors,[]);
    const report={passed:true,browser:browser.version(),checks,errors};
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{if(releaseInfo)releaseInfo();await context.close();await browser.close();}
}
main().catch(error=>{console.error(String(error.message));process.exitCode=1;});
