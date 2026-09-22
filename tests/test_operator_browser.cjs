'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
let playwright,expect;
try{playwright=require('playwright');({expect}=require('playwright/test'));}
catch{const installed=path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');playwright=require(installed);({expect}=require(path.join(installed,'test')));}

(async()=>{
  const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const browser=await playwright.chromium.launch({headless:true,...(os.platform()==='win32'?{executablePath:process.env.AH_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const idle=async()=>await expect(page.locator('.stApp')).toHaveAttribute('data-test-script-state','notRunning');
  try {
    await page.goto(fixture.url);
    await expect(page.getByRole('heading',{name:'과제 제출 및 확인',exact:true})).toBeVisible();
    assert(!/간편하게|YOUR CLASS WORKSPACE/.test(await page.locator('body').innerText()));
    await page.screenshot({path:path.join(fixture.output,'login.png'),fullPage:true});
    await page.getByRole('textbox',{name:'로그인 ID',exact:true}).fill('admin');
    await page.getByRole('textbox',{name:'비밀번호',exact:true}).fill(fixture.admin_password);
    await page.getByRole('button',{name:'로그인',exact:true}).click();
    await page.getByRole('button',{name:'제출 현황 확인',exact:true}).click();
    await expect(page.getByRole('heading',{name:'제출 파일 확인',exact:true})).toBeVisible();
    await idle();
    const choose=page.locator('[data-testid="stSelectbox"]').filter({hasText:'확인할 제출'});
    await expect(choose).toContainText('홍길동 (001)');
    await expect(choose).toContainText('실습 결과.txt');
    await expect(page.getByText('표시 중: 제출 1건 · 파일 1개.',{exact:false})).toBeVisible();
    await page.getByRole('heading',{name:'제출 파일 확인',exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(fixture.output,'operator.png'),fullPage:true});
    await page.getByText('이전 완료 버전도 표시',{exact:true}).click();
    await expect(page.getByText('표시 중: 제출 2건 · 파일 2개.',{exact:false})).toBeVisible();
    await idle();
    const search=page.getByRole('textbox',{name:'제출 파일명 검색',exact:true});
    await search.fill('초안'); await search.press('Enter');
    await expect(choose).toContainText('초안.txt');
    await idle();
    await page.getByRole('button',{name:'다운로드 준비',exact:true}).click();
    const downloadButton=page.frameLocator('iframe').last().getByRole('button',{name:'파일 다운로드',exact:true});
    let receiveDownload;
    const downloaded=new Promise(resolve=>{receiveDownload=resolve;});
    context.on('page',p=>p.on('download',receiveDownload));
    for(const p of context.pages())p.on('download',receiveDownload);
    await downloadButton.click();
    let timer;
    const file=await Promise.race([downloaded,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('download timeout')),15000);})]).finally(()=>clearTimeout(timer));
    assert.match(file.suggestedFilename(),/홍길동_001_제출-000001_v1_초안.txt/);
    assert.equal(fs.readFileSync(await file.path(),'utf8'),'draft');
    const csvPromise=page.waitForEvent('download');
    await page.frameLocator('iframe').first().getByRole('button',{name:'현재 파일 목록 CSV 다운로드',exact:true}).click();
    const csv=await csvPromise;
    const content=fs.readFileSync(await csv.path(),'utf8');
    assert(content.includes('초안.txt') && content.includes('홍길동') && content.includes('서버 저장 경로'));
    assert(!content.includes('실습 결과.txt'));
    await page.getByText('미제출자',{exact:true}).last().click();
    await expect(page.getByText('조회 조건에 해당하는 완료 제출물이 없습니다.',{exact:true})).toBeVisible();
    await page.getByText('전체',{exact:true}).click();
    await search.fill(''); await search.press('Enter');
    await idle();
    await page.setViewportSize({width:390,height:844});
    await expect(page.locator('[data-testid="stSidebar"]')).toHaveAttribute('aria-expanded','false');
    await page.getByRole('heading',{name:'제출 파일 확인',exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(fixture.output,'operator-mobile.png'),fullPage:true});
    assert.equal(await page.locator('[data-testid="stException"]').count(),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1),false);
    assert.deepEqual(errors,[]);
    console.log('PASS: plain login, latest/history filters, student names and file search, original bytes, named download, filtered CSV, missing filter, mobile layout');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
