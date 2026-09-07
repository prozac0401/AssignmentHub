/* Real Edge + live Caddy/Streamlit: admin onboarding navigation. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
let playwright;try{playwright=require('playwright');}catch{playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
async function main(){
  const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const browser=await playwright.chromium.launch({headless:true,timeout:90000,...(os.platform()==='win32'?{channel:'msedge'}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
  try{
    await page.goto(fixture.url);
    await page.getByRole('textbox',{name:'로그인 ID',exact:true}).fill(fixture.user_id);
    await page.getByRole('textbox',{name:'비밀번호',exact:true}).fill(fixture.password);
    await page.getByRole('button',{name:'로그인',exact:true}).click();
    await page.getByRole('button',{name:'명단 등록·사용자 관리로 이동',exact:true}).waitFor({timeout:60000});
    assert.equal(await page.getByRole('link',{name:'브라우저 파일 제출 ↗'}).count(),0);
    assert.equal((await page.locator('[data-testid="stCode"] code').innerText()).trim(),fixture.url);
    if(fixture.screenshot)await page.screenshot({path:fixture.screenshot,fullPage:true});
    await page.getByRole('button',{name:'명단 등록·사용자 관리로 이동',exact:true}).click();
    await page.getByRole('heading',{name:'사용자 명단 등록',exact:true}).waitFor();
    await page.locator('[data-testid="stSidebar"]').getByText('운영 안내',{exact:true}).click();
    await page.getByRole('button',{name:'과제 관리로 이동',exact:true}).click();
    await page.getByRole('heading',{name:'과제 관리',exact:true}).waitFor();
    await page.locator('[data-testid="stSidebar"]').getByText('운영 안내',{exact:true}).click();
    await page.getByRole('button',{name:'제출 현황 확인',exact:true}).click();
    await page.getByRole('heading',{name:'제출 현황',exact:true}).waitFor();
    assert.equal(await page.locator('[data-testid="stException"]').count(),0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,browser:browser.version(),onboarding_navigation:true,public_url_matches:true,javascript_errors:0}));
  }catch(error){
    if(fixture.screenshot)await page.screenshot({path:fixture.screenshot.replace('.png','-failure.png'),fullPage:true,mask:[page.locator('input[type="password"]')]}).catch(()=>{});
    throw error;
  }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
