/* Real Edge + disposable API/Streamlit course; no production data is used. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
let playwright,expect;
try{playwright=require('playwright');({expect}=require('playwright/test'));}
catch{const installed=path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');playwright=require(installed);({expect}=require(path.join(installed,'test')));}

async function main(){
  const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const browser=await playwright.chromium.launch({headless:true,...(os.platform()==='win32'?{channel:'msedge'}:{})});
  const context=await browser.newContext({viewport:{width:1280,height:1000}}),errors=[],checks=[];
  context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
  const checked=name=>{checks.push(name);console.log('Passed: '+name);};
  const toggle=async(scope,label,value)=>{const input=scope.getByRole('checkbox',{name:label,exact:true});if(await input.isChecked()!==value)await scope.getByText(label,{exact:true}).click();await expect.poll(()=>input.isChecked(),{message:label+' toggle'}).toBe(value);};
  const admin=await context.newPage();
  try{
    const logged=await context.request.post(fixture.url+'/api/auth/login',{data:{user_id:'admin',password:fixture.admin_password}});
    assert(logged.ok());const headers={Authorization:'Bearer '+(await logged.json()).token};
    const getAssignments=async()=>await(await context.request.get(fixture.url+'/api/assignments',{headers})).json();
    const patch=async(id,data)=>{const r=await context.request.patch(fixture.url+'/api/admin/assignments/'+id,{headers,data});assert(r.ok(),await r.text());};
    await admin.goto(fixture.url);
    await admin.getByRole('textbox',{name:'로그인 ID',exact:true}).fill('admin');
    await admin.getByRole('textbox',{name:'비밀번호',exact:true}).fill(fixture.admin_password);
    await admin.getByRole('button',{name:'로그인',exact:true}).click();
    await admin.getByRole('button',{name:'과제 관리로 이동',exact:true}).click();
    await admin.getByText('새 과제 추가',{exact:true}).click();
    const form=admin.locator('[data-testid="stForm"]').filter({has:admin.getByRole('textbox',{name:'새 과제명',exact:true})});
    await form.getByRole('textbox',{name:'새 과제명',exact:true}).fill('문서 제출 검사');
    for(const label of ['스프레드시트','발표자료','이미지','영상','음성·음악','압축파일','코드·데이터','기타'])await toggle(form,label,false);
    await form.getByRole('button',{name:'과제 추가',exact:true}).click();
    await expect.poll(async()=>(await getAssignments()).some(a=>a.title==='문서 제출 검사')).toBe(true);
    await expect(admin.locator('.stApp')).toHaveAttribute('data-test-script-state','notRunning');
    const docs=(await getAssignments()).find(a=>a.title==='문서 제출 검사');
    assert.deepEqual(docs.allowed_file_categories,['documents']);
    checked('admin_creates_document_only_assignment');

    const choose=admin.locator('[data-testid="stSelectbox"]').filter({hasText:'수정할 과제'});
    await choose.getByRole('combobox').click();
    await admin.getByRole('option',{name:'문서 제출 검사',exact:true}).click();
    const edit=admin.locator('[data-testid="stForm"]').filter({has:admin.getByRole('textbox',{name:'과제명',exact:true})});
    await expect(edit.getByRole('textbox',{name:'과제명',exact:true})).toHaveValue('문서 제출 검사');
    await expect(edit.getByRole('checkbox',{name:'압축파일',exact:true})).not.toBeChecked();
    await expect(admin.locator('.stApp')).toHaveAttribute('data-test-script-state','notRunning');
    await toggle(edit,'압축파일',true);
    await edit.getByRole('button',{name:'변경 사항 저장',exact:true}).click();
    await expect.poll(async()=>(await getAssignments()).find(a=>a.id===docs.id).allowed_file_categories.includes('archives')).toBe(true);
    await expect(admin.locator('.stApp')).toHaveAttribute('data-test-script-state','notRunning');
    await expect(edit.getByRole('textbox',{name:'과제명',exact:true})).toHaveValue('문서 제출 검사');
    await toggle(edit,'압축파일',false);
    await edit.getByRole('button',{name:'변경 사항 저장',exact:true}).click();
    await expect.poll(async()=>(await getAssignments()).find(a=>a.id===docs.id).allowed_file_categories).toEqual(['documents']);
    await expect(admin.locator('.stApp')).toHaveAttribute('data-test-script-state','notRunning');
    await admin.screenshot({path:path.join(fixture.output,'admin-policy.png'),fullPage:true});
    assert.equal(await admin.locator('[data-testid="stException"]').count(),0);
    checked('admin_toggles_archive_permission_and_persists');

    const videoResponse=await context.request.post(fixture.url+'/api/admin/assignments',{headers,data:{title:'영상 제출 검사',allowed_file_categories:['video']}});
    assert(videoResponse.ok());const video=await videoResponse.json();
    const page=await context.newPage();await page.goto(fixture.url+'/upload');
    await page.locator('#user-id').fill('001');await page.locator('#password').fill(fixture.student_password);
    await page.locator('#login-form button').click();await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('#assignment').selectOption(docs.id);
    assert.match(await page.locator('#files').getAttribute('accept'),/\.pdf/);
    assert(!/\.zip/.test(await page.locator('#files').getAttribute('accept')));
    await page.locator('#files').setInputFiles({name:'archive.ZIP',mimeType:'application/zip',buffer:Buffer.from('zip')});
    assert(await page.locator('#start').isDisabled());assert(await page.locator('#selection-error').isVisible());
    await page.evaluate(()=>{const data=new DataTransfer();data.items.add(new File(['zip'],'dropped.7z'));document.querySelector('.drop-zone').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:data}));});
    assert(await page.locator('#start').isDisabled());assert.match(await page.locator('#selection-error').innerText(),/dropped.7z/);
    await page.locator('#files').setInputFiles({name:'report.PDF',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\nsmall policy fixture')});
    assert(await page.locator('#start').isEnabled());assert.equal(await page.locator('#selection-error').isVisible(),false);
    await page.locator('#start').click();await page.locator('#receipt').waitFor({state:'visible',timeout:60000});
    checked('picker_and_drop_reject_archives_and_accept_document');

    await page.locator('#receipt button').click();await page.locator('#assignment').selectOption(video.id);
    await page.locator('#files').setInputFiles({name:'fake.mp4',mimeType:'video/mp4',buffer:Buffer.from('not a video')});
    await page.locator('#start').click();
    await expect(page.locator('#notice')).toContainText('재생할 수 없습니다',{timeout:60000});
    assert.equal(await page.locator('#receipt').isVisible(),false);
    checked('renamed_invalid_video_has_no_submission_receipt');

    await page.locator('#fresh').click();await page.locator('#files').setInputFiles(path.join(fixture.output,'normal.mp4'));
    await page.locator('#start').click();await page.locator('#receipt').waitFor({state:'visible',timeout:60000});
    assert.match(await page.locator('#receipt').innerText(),/첫 5초 검증 완료/);
    assert.match(await page.locator('#receipt').innerText(),/소리 확인/);
    await page.screenshot({path:path.join(fixture.output,'video-receipt.png'),fullPage:true});
    checked('real_video_is_validated_before_receipt');

    await page.locator('#receipt button').click();await page.locator('#files').setInputFiles(path.join(fixture.output,'silent.mp4'));
    await page.locator('#start').click();await expect(page.locator('#notice')).toContainText('소리가 확인되지',{timeout:60000});
    assert.equal(await page.locator('#receipt').isVisible(),false);
    await patch(video.id,{video_audio_required:false});
    await page.locator('#fresh').click();await page.locator('#refresh').click();
    await expect(page.locator('#file-policy')).toContainText('무음 영상도 허용합니다');
    await page.locator('#files').setInputFiles(path.join(fixture.output,'silent.mp4'));
    await page.locator('#start').click();await page.locator('#receipt').waitFor({state:'visible',timeout:60000});
    assert.match(await page.locator('#receipt').innerText(),/무음 허용/);
    checked('silent_video_requires_explicit_permission');
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(fixture.output,'mobile-policy.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
    checked('mobile_has_no_horizontal_overflow');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(fixture.output,'result.json'),JSON.stringify({passed:true,browser:browser.version(),checks,errors},null,2));
  }catch(error){await admin.screenshot({path:path.join(fixture.output,'failure.png'),fullPage:true}).catch(()=>{});throw error;}
  finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
