/* Real browser: paste/upload TSV, invalidate stale previews, and apply a roster. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
let playwright;try{playwright=require('playwright');}catch{playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}

async function main(){
  const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const browser=await playwright.chromium.launch({headless:true,channel:'msedge'});
  const context=await browser.newContext({viewport:{width:1400,height:1000},acceptDownloads:true});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const apply=page.getByRole('button',{name:'검증된 명단 반영',exact:true});
  const validate=page.getByRole('button',{name:'명단 검증 및 미리보기',exact:true});
  const ready=enabled=>page.waitForFunction(enabled=>{
    const button=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='검증된 명단 반영');
    return button && button.disabled===!enabled;
  },enabled);
  const noPreview=()=>page.waitForFunction(()=>![...document.querySelectorAll('button')].some(x=>x.textContent.trim()==='검증된 명단 반영'));
  try{
    await page.goto(fixture.url);
    await page.getByRole('textbox',{name:'로그인 ID',exact:true}).fill(fixture.user_id);
    await page.getByRole('textbox',{name:'비밀번호',exact:true}).fill(fixture.password);
    await page.getByRole('button',{name:'로그인',exact:true}).click();
    await page.getByRole('button',{name:'명단 등록·사용자 관리로 이동',exact:true}).click();
    const textarea=page.getByRole('textbox',{name:'TSV 명단 붙여넣기',exact:true});
    await textarea.waitFor();
    assert.equal(await page.locator('input[type=file]').count(),0);
    const download=page.waitForEvent('download');
    await page.getByRole('button',{name:'명단 TSV 템플릿 다운로드',exact:true}).click();
    const saved=await download;
    assert.equal(saved.suggestedFilename(),'users_template.tsv');
    assert(fs.readFileSync(await saved.path(),'utf8').startsWith('\uFEFFuser_id\tname\tgroup\r\n001\t'));
    const valid='user_id\tname\tgroup\n001\t홍길동\tA반\n1\t김민수\tB반';
    await textarea.fill(valid);
    await validate.click();await ready(true);
    if(fixture.screenshot)await page.screenshot({path:fixture.screenshot,fullPage:true});
    await textarea.fill('user_id\tname\n001\t첫 번째\n001\t중복');
    await page.getByRole('heading',{name:'사용자 명단 등록',exact:true}).click();await noPreview();
    await validate.click();await ready(false);
    await textarea.fill(valid);
    await validate.click();await ready(true);
    if(fixture.common_password){
      await page.getByText('신규 수강생에게 공통 임시비밀번호 사용',{exact:true}).click();
      await page.getByRole('textbox',{name:'이 차수의 공통 임시비밀번호',exact:true}).fill(fixture.common_password);
      if(fixture.screenshot)await page.screenshot({path:fixture.screenshot,fullPage:true,mask:[page.locator('input[type=password]')]});
    }
    await apply.click();
    await page.getByRole('button',{name:'임시비밀번호 결과 닫기',exact:true}).waitFor();
    if(fixture.common_password){
      const csvEvent=page.waitForEvent('download');
      await page.frameLocator('iframe').first().getByRole('button',{name:'임시비밀번호 결과 CSV 다운로드',exact:true}).click();
      const csv=fs.readFileSync(await (await csvEvent).path(),'utf8');
      assert.equal(csv.split(fixture.common_password).length-1,2,'Both new accounts receive the common value');
      for(const uid of ['001','1']){
        const response=await context.request.post(fixture.url+'/api/auth/login',{data:{user_id:uid,password:fixture.common_password}});
        assert.equal(response.status(),200);assert.equal((await response.json()).must_change_password,true);
      }
    }
    await page.getByRole('button',{name:'임시비밀번호 결과 닫기',exact:true}).click();
    await page.getByText('TSV 파일 업로드',{exact:true}).click();
    await noPreview();
    const file=page.locator('input[type=file]');
    const content='user_id\tname\tgroup\r\n003\t파일 등록\tC반\r\n';
    await file.setInputFiles({name:'명단.tsv',mimeType:'text/tab-separated-values',buffer:Buffer.concat([Buffer.from([255,254]),Buffer.from(content,'utf16le')])});
    await validate.click();await ready(true);
    await page.getByText('직접 붙여넣기',{exact:true}).click();await noPreview();
    await page.getByText('TSV 파일 업로드',{exact:true}).click();
    await file.setInputFiles({name:'명단.tsv',mimeType:'text/tab-separated-values',buffer:Buffer.from('\uFEFF'+content,'utf8')});
    await validate.click();await ready(true);await apply.click();
    await page.getByRole('button',{name:'임시비밀번호 결과 닫기',exact:true}).waitFor();
    const login=await fetch(fixture.url+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:fixture.user_id,password:fixture.password})});
    assert.equal(login.status,200);const auth=await login.json();
    const users=await fetch(fixture.url+'/api/admin/users',{headers:{Authorization:'Bearer '+auth.token}}).then(r=>r.json());
    assert.deepEqual(users.filter(u=>u.role==='student').map(u=>u.user_id).sort(),['001','003','1']);
    assert.equal(await page.locator('[data-testid="stException"]').count(),0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,browser:browser.version(),tsv_paste:true,utf8_file:true,utf16_preview:true,
      stale_preview_cleared:true,duplicate_id_blocked:true,leading_zeroes_preserved:true,template_download:true,common_password:!!fixture.common_password}));
  }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
