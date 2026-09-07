/* Real Edge, live server, fictional classroom. No fabricated UI or API responses. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict');
let playwright;try{playwright=require('playwright');}catch{playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
const secrets=[];
function safe(error){let message=String(error.stack||error);for(const secret of secrets)if(secret)message=message.split(secret).join('[REDACTED]');return message;}
function parseCsv(text){const rows=[];let row=[],value='',quoted=false;for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){value+='"';i++;}else quoted=!quoted;}else if(c===','&&!quoted){row.push(value);value='';}else if(c==='\n'&&!quoted){row.push(value.replace(/\r$/,''));rows.push(row);row=[];value='';}else value+=c;}if(value||row.length){row.push(value);rows.push(row);}return rows;}
async function main(){
 const f=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));secrets.push(f.password);
 const browser=await playwright.chromium.launch({headless:true,channel:'msedge',timeout:90000});
 const context=await browser.newContext({viewport:{width:1280,height:900},acceptDownloads:true});
 const admin=await context.newPage();const errors=[];
 context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 admin.on('pageerror',e=>errors.push(e.message));
 let student,upload;
 const shot=async(p,name,extra=[])=>{await p.waitForTimeout(600);assert.equal(await p.locator('[data-testid="stException"]').count(),0);await p.screenshot({path:path.join(f.output,name+'.png'),fullPage:false,mask:[p.locator('input[type="password"]'),...extra],maskColor:'#E3E7EF'});console.log('Captured '+name);};
 const responsiveChecks=[];
 const responsive=async(p,label,widths)=>{for(const width of widths){await p.setViewportSize({width,height:900});await p.waitForTimeout(350);
  const overflow=await p.evaluate(()=>{const nodes=[document.documentElement,document.querySelector('[data-testid="stMain"]')].filter(Boolean);return nodes.some(n=>n.scrollWidth>n.clientWidth+2);});
  assert.equal(overflow,false,label+' overflows at '+width);responsiveChecks.push({screen:label,width,horizontal_overflow:false});}
  await p.setViewportSize({width:1280,height:900});};
 const nav=async(name)=>{await admin.locator('[data-testid="stSidebar"]').getByText(name,{exact:true}).click();await admin.waitForTimeout(700);};
 const login=async(p,id,password)=>{await p.goto(f.url);await p.getByRole('textbox',{name:'로그인 ID',exact:true}).fill(id);await p.getByRole('textbox',{name:'비밀번호',exact:true}).fill(password);await p.getByRole('button',{name:'로그인',exact:true}).click();await p.getByRole('button',{name:'로그아웃',exact:true}).waitFor({timeout:60000});};
 try{
  await admin.goto(f.url);await admin.getByRole('textbox',{name:'로그인 ID',exact:true}).fill('admin');
  await shot(admin,'06-admin-login');
  await responsive(admin,'login',[360,390,600,768,1024,1440]);
  await admin.getByRole('textbox',{name:'비밀번호',exact:true}).fill(f.password);await admin.getByRole('button',{name:'로그인',exact:true}).click();
  await admin.getByRole('button',{name:'명단 등록·사용자 관리로 이동',exact:true}).waitFor({timeout:60000});
  await shot(admin,'07-admin-home');
  await admin.getByRole('button',{name:'명단 등록·사용자 관리로 이동',exact:true}).click();
  await admin.locator('input[type=file]').setInputFiles(path.resolve('templates/users_template.xlsx'));
  await admin.getByRole('button',{name:'명단 검증 및 미리보기',exact:true}).click();
  await admin.getByRole('button',{name:'검증된 명단 반영',exact:true}).waitFor();
  await shot(admin,'08-roster-preview');
  await admin.getByRole('button',{name:'검증된 명단 반영',exact:true}).click();
  await admin.getByRole('button',{name:'임시비밀번호 결과 닫기',exact:true}).waitFor();
  const csvEvent=admin.waitForEvent('download');
  await admin.frameLocator('iframe').getByRole('button',{name:'임시비밀번호 결과 CSV 다운로드',exact:true}).click();
  const csvDownload=await csvEvent;
  const rows=parseCsv(fs.readFileSync(await csvDownload.path(),'utf8').replace(/^\uFEFF/,''));
  const header=rows.shift(),index=header.indexOf('temporary_password'),idIndex=header.indexOf('user_id');
  const studentRow=rows.find(r=>r[idIndex]==='001');assert(studentRow,'Text student ID 001 preserved');
  const initial=studentRow[index];secrets.push(...rows.map(r=>r[index]));
  await admin.getByRole('button',{name:'임시비밀번호 결과 닫기',exact:true}).click();
  await admin.waitForFunction(()=>[...document.querySelectorAll('button')].every(button=>button.innerText.trim()!=='임시비밀번호 결과 닫기'));
  await admin.waitForTimeout(700);
  await admin.getByRole('heading',{name:'사용자 계정 관리',exact:true}).scrollIntoViewIfNeeded();
  await shot(admin,'09-users-registered');
  await nav('과제 관리');
  // Every new course has a default assignment, so explicitly expand the form.
  const create=admin.getByText('새 과제 추가',{exact:true});await create.click();
  await admin.getByRole('textbox',{name:'새 과제명',exact:true}).fill('1주차 실습 과제');
  await admin.getByRole('textbox',{name:'과제 설명',exact:true}).fill('실습 결과 파일을 제출하세요. 수정할 때는 새 버전으로 다시 제출할 수 있습니다.');
  await shot(admin,'10-assignment-create');
  await admin.getByRole('button',{name:'과제 추가',exact:true}).click();
  await admin.waitForTimeout(700);
  student=await context.newPage();await login(student,'001',initial);
  await student.getByRole('textbox',{name:'새 비밀번호',exact:true}).waitFor();
  await shot(student,'11-first-password');
  const password=crypto.randomBytes(24).toString('base64url');secrets.push(password);
  await student.getByRole('textbox',{name:'현재 비밀번호 또는 임시비밀번호',exact:true}).fill(initial);
  await student.getByRole('textbox',{name:'새 비밀번호',exact:true}).fill(password);
  await student.getByRole('textbox',{name:'새 비밀번호 확인',exact:true}).fill(password);
  await student.getByRole('button',{name:'비밀번호 변경',exact:true}).click();
  await student.getByRole('textbox',{name:'로그인 ID',exact:true}).waitFor();
  await login(student,'001',password);
  await student.getByRole('button',{name:'일회용 연결 코드 발급',exact:true}).click();
  const codeElement=student.locator('[data-testid="stCode"] code');await codeElement.waitFor();
  const code=(await codeElement.innerText()).trim();secrets.push(code);
  await shot(student,'12-student-home',[student.locator('[data-testid="stCode"]')]);
  upload=await context.newPage();await upload.goto(f.url+'/upload');
  await shot(upload,'13-upload-connect');
  await upload.locator('#bridge').fill(code);await upload.getByRole('button',{name:'코드로 연결',exact:true}).click();
  await upload.locator('#workspace').waitFor({state:'visible'});
  await upload.locator('#assignment').selectOption({label:'1주차 실습 과제'});
  const dropped=await upload.evaluateHandle(()=>{const transfer=new DataTransfer();transfer.items.add(new File(['sample'],'아주_긴_이름의_과제_제출_파일_'.repeat(6)+'.txt',{type:'text/plain'}));return transfer;});
  await upload.locator('.drop-zone').dispatchEvent('drop',{dataTransfer:dropped});await dropped.dispose();
  assert.equal(await upload.evaluate(()=>state.selected.length),1);
  assert.equal(await upload.locator('#start').isEnabled(),true);
  await responsive(upload,'uploader-long-filename',[360,390,600,768,1024,1440]);
  const sample=path.join(f.work,'1주차_실습결과.txt');
  const data=Buffer.from('파이썬 기초 실습 과제\n학생 001의 예시 제출 파일입니다.\n'.repeat(150000));fs.writeFileSync(sample,data);
  const digest=crypto.createHash('sha256').update(data).digest('hex');
  await upload.locator('#files').setInputFiles(sample);
  await shot(upload,'14-file-selected');
  await upload.setViewportSize({width:390,height:920});await upload.evaluate(()=>window.scrollTo(0,0));await shot(upload,'23-upload-mobile');
  await upload.setViewportSize({width:1280,height:900});
  let interrupted=false,signal;const reached=new Promise(r=>signal=r);
  await upload.route('**/api/uploads/*/files/*?offset=*',async route=>{if(!interrupted&&Number(new URL(route.request().url()).searchParams.get('offset'))>0){interrupted=true;await route.abort('connectionreset');signal();}else await route.continue();});
  await upload.locator('#start').click();
  await Promise.race([reached,new Promise((_,reject)=>{const timer=setTimeout(()=>reject(new Error('Second upload chunk not reached')),120000);timer.unref();})]);
  await upload.locator('#pause').click();await upload.locator('#status').filter({hasText:'일시 중지'}).waitFor();
  await upload.locator('#progress-panel').scrollIntoViewIfNeeded();await shot(upload,'15-upload-paused');
  const confirmed=await upload.evaluate(()=>state.upload.files[0].offset);assert(confirmed>0&&confirmed<data.length);
  await upload.reload();await upload.locator('#user-id').fill('001');await upload.locator('#password').fill(password);
  await upload.locator('#login-form button').click();await upload.locator('#workspace').waitFor({state:'visible'});
  await upload.locator('#unfinished button').first().click();await upload.locator('#files').setInputFiles(sample);
  await upload.evaluate(()=>window.scrollTo(0,260));await shot(upload,'16-resume-selected');
  await upload.locator('#start').click();await upload.locator('#receipt').waitFor({state:'visible',timeout:120000});
  await upload.locator('#receipt').scrollIntoViewIfNeeded();await shot(upload,'17-submission-receipt');
  await upload.locator('#history').scrollIntoViewIfNeeded();await shot(upload,'18-student-history');
  const fileEvent=upload.waitForEvent('download');await upload.locator('#history-list button').first().click();
  const download=await fileEvent;const downloaded=fs.readFileSync(await download.path());
  assert.equal(crypto.createHash('sha256').update(downloaded).digest('hex'),digest);
  await nav('제출 현황');
  await admin.getByRole('combobox').first().click();await admin.getByRole('option',{name:'1주차 실습 과제',exact:true}).click();
  await admin.getByRole('heading',{name:'제출 현황',exact:true}).waitFor();
  await admin.waitForTimeout(500);await admin.evaluate(()=>window.scrollTo(0,0));await shot(admin,'19-admin-dashboard');
  await responsive(admin,'admin-dashboard',[360,390,600,768,1024,1440]);
  await admin.setViewportSize({width:768,height:1024});await shot(admin,'24-dashboard-tablet');
  await admin.setViewportSize({width:1280,height:900});
  await nav('저장 공간·기록');await admin.getByRole('heading',{name:'저장 공간과 운영 기록',exact:true}).waitFor();
  await shot(admin,'20-storage-audit');
  assert.deepEqual(errors,[]);
  const report={passed:true,browser:browser.version(),captured_at:new Date().toISOString(),fictional_course:'파이썬 기초 실습',student_id:'001',
   ui_roster_import:true,private_csv_download:true,assignment_created:true,first_password_changed:true,one_time_bridge:true,
   network_interruption_simulated:'Second chunk aborted by Playwright; subsequent transfer uses real server responses',
   browser_reload_resume:true,confirmed_bytes_before_reload:confirmed,file_bytes:data.length,download_sha256:digest,
   download_matches_original:true,javascript_errors:0,drag_and_drop:true,responsive_checks:responsiveChecks,screenshots:fs.readdirSync(f.output).filter(n=>n.endsWith('.png')).length};
  fs.writeFileSync(path.join(f.output,'..','walkthrough.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({passed:true,file_bytes:data.length,download_matches_original:true}));
 }catch(error){
  // Failure screenshots remain private because an unknown state may contain credentials.
  for(const [i,p] of context.pages().entries())await p.screenshot({path:path.join(f.work,'failure-'+i+'.png'),mask:[p.locator('input[type="password"]'),p.locator('[data-testid="stCode"]'),p.locator('[data-testid="stDataFrame"]')]}).catch(()=>{});
  throw error;
 }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(safe(error));process.exitCode=1;});
