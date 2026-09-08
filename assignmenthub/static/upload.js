'use strict';
// Credentials and one-time tickets exist only in memory, never in URLs or storage.
const $=id=>document.getElementById(id);
const state={token:null,user:null,info:{},quota:null,assignments:[],selected:[],upload:null,
  resume:null,requestId:null,manifest:null,running:false,cancelling:false,stopped:false,xhr:null,worker:null,workerReject:null,initialAssignment:null};
const hidden=(id,hide=true)=>{const node=$(id);node.hidden=hide;node.classList.toggle('hidden',hide);};
const bytes=n=>{n=Number(n)||0;if(n>=1073741824)return (n/1073741824).toFixed(2)+' GiB';if(n>=1048576)return (n/1048576).toFixed(2)+' MiB';if(n>=1024)return (n/1024).toFixed(1)+' KiB';return n+' B';};
const exact=n=>bytes(n)+' ('+Number(n).toLocaleString('ko-KR')+' B)';
const labels={uploading:'전송 중',paused:'일시 중지',verifying:'검증 중',finalizing:'저장 중',completed:'제출 완료',failed:'실패',cancelled:'취소됨',expired:'보관 기간 만료'};
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);hidden('notice',!message);}
function element(tag,text,className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function date(value){if(!value)return '—';try{return new Intl.DateTimeFormat('ko-KR',{dateStyle:'medium',timeStyle:'medium',timeZone:state.info.timezone||'Asia/Seoul'}).format(new Date(value))+' ('+(state.info.timezone||'Asia/Seoul')+')';}catch{return String(value);}}
async function api(path,options={}) {
  const headers=new Headers(options.headers||{});
  if(state.token)headers.set('Authorization','Bearer '+state.token);
  if(options.json!==undefined){headers.set('Content-Type','application/json');options.body=JSON.stringify(options.json);}
  let response;
  try{response=await fetch('/api'+path,{...options,headers,credentials:'omit',cache:'no-store'});}
  catch{const e=new Error('연결이 끊겼습니다. 서버와 네트워크를 확인한 뒤 재시도하세요.');e.status=0;throw e;}
  const result=await response.json().catch(()=>({detail:'서버 응답을 읽지 못했습니다.'}));
  if(!response.ok){const e=new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));e.status=response.status;throw e;}
  return result;
}
async function loadData(path) {
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),15000);
  const timeoutMessage='화면 정보를 불러오는 시간이 초과되었습니다. 서버 상태를 확인한 뒤 다시 로그인하세요.';
  try{
    const result=await api(path,{signal:controller.signal});
    if(controller.signal.aborted)throw new Error(timeoutMessage);
    return result;
  }catch(error){
    if(controller.signal.aborted)throw new Error(timeoutMessage);
    throw error;
  }finally{clearTimeout(timer);}
}
function controls(running) {
  const busy=running||state.cancelling;
  state.running=running;$('files').disabled=busy;$('assignment').disabled=busy||!!state.resume;
  $('start').disabled=busy||!state.selected.length;$('fresh').disabled=busy;$('refresh').disabled=busy;
  $('logout').disabled=busy;hidden('pause',!running||state.cancelling);hidden('cancel',!state.upload||state.upload.status==='completed');
  for(const b of document.querySelectorAll('#unfinished button'))b.disabled=busy;
}
function checkStop(){if(state.stopped)throw new Error('일시 중지했습니다. 파일과 작업을 보관하고 있습니다. 다시 시도로 이어 올릴 수 있습니다.');}
function stop(){state.stopped=true;if(state.xhr)state.xhr.abort();if(state.worker){state.worker.terminate();state.worker=null;if(state.workerReject)state.workerReject(new Error('해시 확인을 중지했습니다. 다시 시도할 수 있습니다.'));state.workerReject=null;}}
async function authenticate(result) {
  state.token=result.token;state.user=result.user;$('password').value='';
  hidden('auth');hidden('password-panel',!result.must_change_password);hidden('workspace');
  if(result.must_change_password){notice('최초 로그인 또는 초기화 상태입니다. 관리 화면에서 비밀번호 변경을 먼저 완료하세요.');state.token=null;return;}
  notice('과제와 저장 용량을 확인하고 있습니다.');$('identity').textContent=state.user.name+' · '+state.user.user_id;
  try{await refresh();hidden('workspace',false);notice('');}
  catch(error){state.token=null;state.user=null;hidden('workspace');hidden('auth',false);throw error;}
}
async function refresh() {
  const [assignments,quota,unfinished,history]=await Promise.all([loadData('/assignments'),loadData('/quota'),loadData('/uploads'),loadData('/submissions?all_versions=true')]);
  state.assignments=assignments;state.quota=quota;
  const previous=$('assignment').value||state.initialAssignment;state.initialAssignment=null;$('assignment').replaceChildren();
  for(const a of assignments){const option=element('option',a.title+(a.is_open?'':' · 접수 종료'));option.value=a.id;option.disabled=!a.is_open;$('assignment').append(option);}
  const chosen=assignments.find(a=>a.id===previous)||assignments.find(a=>a.is_open);
  if(chosen)$('assignment').value=chosen.id;
  $('assignment-description').textContent=chosen?(chosen.is_open?(chosen.description||'선택한 과제에 제출할 파일을 아래에서 선택하세요.'):'신규 제출 접수가 종료되었습니다. 기존 미완료 작업은 이어 올리기에서 선택하세요.'):'접수 중인 과제가 없습니다. 신규 제출을 시작할 수 없습니다.';
  if(state.resume){$('assignment').value=state.resume.assignment_id;$('assignment').disabled=true;}
  $('quota-used').textContent=bytes(quota.used_bytes)+' / '+bytes(quota.quota_bytes);
  $('quota-detail').textContent='진행 중 예약 '+bytes(quota.reserved_bytes)+' · 이번 선택 '+bytes(state.selected.reduce((n,f)=>n+f.size,0));
  $('quota-progress').value=Math.min(100,(quota.used_bytes+quota.reserved_bytes)/quota.quota_bytes*100);
  $('limits').replaceChildren();
  for(const [label,value] of [['파일당 최대',bytes(quota.max_file_bytes)],['한 묶음 파일 수',quota.max_files+'개'],['전송 청크',bytes(quota.chunk_bytes)]]){$('limits').append(element('dt',label),element('dd',value));}
  renderUnfinished(unfinished);renderHistory(history);selection();
}
function renderUnfinished(items) {
  $('unfinished').replaceChildren();if(!items.length)$('unfinished').append(element('p','이어 올릴 작업이 없습니다.','muted'));
  for(const u of items){const box=element('div',undefined,'unfinished-item');box.append(element('strong',u.assignment_title||u.assignment_id),element('div',(labels[u.status]||u.status)+' · '+bytes(u.total_bytes)));
    if(!['cancelled','expired','failed'].includes(u.status)){
      const button=element('button','이 작업 이어 올리기','secondary');button.disabled=state.running;button.onclick=()=>{state.resume=u;state.upload=u;state.manifest=null;state.requestId=null;state.selected=[];$('files').value='';$('assignment').value=u.assignment_id;$('assignment').disabled=true;hidden('resume-indicator',false);$('resume-indicator').textContent='이어 올릴 파일 '+u.files.length+'개를 모두 다시 선택하세요: '+u.files.map(f=>f.name).join(', ');hidden('fresh',false);hidden('receipt');selection();renderProgress();};box.append(button);
    }else{box.append(element('div','재개할 수 없습니다. 새 제출을 시작하세요.','muted'));}
    $('unfinished').append(box);}
}
function selection() {
  $('selection').replaceChildren();for(const f of state.selected){const row=element('div',undefined,'file-row');const head=element('div',undefined,'file-head');head.append(element('span',f.name),element('small',exact(f.size)));row.append(head);$('selection').append(row);}
  const total=state.selected.reduce((n,f)=>n+f.size,0);$('selection-total').textContent=state.selected.length+'개 파일 · 합계 '+exact(total);
  let issue='';const q=state.quota;
  if(q){if(state.selected.length>q.max_files)issue='한 번에 최대 '+q.max_files+'개까지 선택할 수 있습니다.';else if(state.selected.some(f=>f.size>q.max_file_bytes))issue='파일당 최대 '+exact(q.max_file_bytes)+'를 초과한 파일이 있습니다.';else if(!state.resume&&q.used_bytes+q.reserved_bytes+total>q.quota_bytes)issue='완료 사용량과 예약량, 이번 선택을 합하면 누적 한도를 초과합니다. 파일 선택을 줄이거나 관리자에게 문의하세요.';}
  if(issue)notice(issue,true);
  $('start').disabled=state.running||!state.selected.length||!!issue||(!state.resume&&!state.assignments.some(a=>a.id===$('assignment').value&&a.is_open));
  $('start').textContent=state.upload?.status==='completed'?'제출 완료':(state.resume?'확인 후 이어 올리기':'제출 시작');
  if(q)$('quota-detail').textContent='진행 중 예약 '+bytes(q.reserved_bytes)+' · 이번 선택 '+bytes(total);
}
function renderProgress(liveFile=null,liveBytes=0) {
  const u=state.upload;if(!u)return;hidden('progress-panel',false);
  const confirmed=u.files.reduce((n,f)=>n+f.offset,0);const sent=Math.min(u.total_bytes,confirmed+liveBytes);
  const percent=u.total_bytes?sent/u.total_bytes*100:(u.status==='completed'?100:0);
  $('total-progress').value=percent;$('percent').textContent=percent.toFixed(1)+'%';
  $('total-progress-label').textContent='서버 확정 수신량 '+bytes(confirmed)+' / '+bytes(u.total_bytes)+(liveBytes?' · 현재 청크 전송 '+bytes(liveBytes):'');
  $('file-progress-list').replaceChildren();
  u.files.forEach((f,i)=>{const row=element('div',undefined,'file-row');const head=element('div',undefined,'file-head');head.append(element('span',(i+1)+' / '+u.files.length+' · '+f.name),element('small',bytes(f.offset+(f.id===liveFile?liveBytes:0))+' / '+bytes(f.size)));const p=element('progress');p.max=100;p.value=f.size?(f.offset+(f.id===liveFile?liveBytes:0))/f.size*100:100;row.append(head,p);$('file-progress-list').append(row);});
}
function hashFile(file,index,total) {
  checkStop();return new Promise((resolve,reject)=>{
    const worker=new Worker('/upload-static/hash-worker.js');state.worker=worker;state.workerReject=reject;
    const cleanup=()=>{worker.terminate();if(state.worker===worker){state.worker=null;state.workerReject=null;}};
    worker.onmessage=e=>{if(e.data.error){cleanup();reject(new Error(e.data.error));return;}
      $('current-file').textContent=(index+1)+' / '+total+' · '+file.name+' · SHA-256 확인 '+bytes(e.data.bytes)+' / '+bytes(file.size);
      if(e.data.sha256){cleanup();resolve(e.data.sha256);}};
    worker.onerror=()=>{cleanup();reject(new Error('파일 해시 작업을 시작할 수 없습니다. 브라우저를 확인하세요.'));};
    worker.postMessage({file,id:index});
  });
}
function requestId(){const b=new Uint8Array(16);crypto.getRandomValues(b);b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const h=Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);}
function sendChunk(file,blob,offset,sha256) {
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();state.xhr=xhr;
    xhr.open('PATCH','/api/uploads/'+encodeURIComponent(state.upload.id)+'/files/'+encodeURIComponent(file.id)+'?offset='+offset);
    xhr.setRequestHeader('Authorization','Bearer '+state.token);xhr.setRequestHeader('X-Chunk-SHA256',sha256);xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.timeout=120000;
    xhr.upload.onprogress=e=>{if(e.lengthComputable)renderProgress(file.id,e.loaded);};
    const error=(message,status)=>{state.xhr=null;const e=new Error(message);e.status=status;reject(e);};
    xhr.onload=()=>{state.xhr=null;let data;try{data=JSON.parse(xhr.responseText);}catch{error('서버 응답을 읽지 못했습니다.',xhr.status);return;}
      if(xhr.status>=200&&xhr.status<300)resolve(data);else error(typeof data.detail==='string'?data.detail:'전송 요청을 처리하지 못했습니다.',xhr.status);};
    xhr.onerror=()=>error('연결 끊김: 서버에 확정된 위치를 확인하고 재시도합니다.',0);
    xhr.ontimeout=()=>error('전송 응답 시간이 초과되었습니다.',0);xhr.onabort=()=>error('전송을 일시 중지했습니다.',0);xhr.send(blob);
  });
}
const retryable=e=>!e.status||[408,409,423,429,500,502,503,504].includes(e.status);
async function bounded(task,description) {
  for(let attempt=0;;attempt++){
    checkStop();try{return await task();}catch(error){checkStop();if(attempt>=3||!retryable(error))throw error;
      $('status').textContent=(error.status===429?'대기 중':'연결 끊김 · 재시도 중')+' ('+(attempt+1)+'/3)';$('progress-help').textContent=description+' · '+error.message;
      await new Promise(resolve=>setTimeout(resolve,Math.min(800*2**attempt,5000)));}
  }
}
async function run() {
  if(state.running)return;state.stopped=false;controls(true);hidden('retry');hidden('receipt');hidden('progress-panel',false);notice('');
  if(!state.upload){$('total-progress').value=0;$('percent').textContent='0%';$('total-progress-label').textContent='전송 시작 전 · 파일 확인 중';$('file-progress-list').replaceChildren();}
  try {
    $('status').textContent='파일 무결성 확인 중';$('progress-help').textContent='파일을 작은 조각으로 읽어 전체 SHA-256을 계산합니다. 이 단계에는 파일이 전송되지 않습니다.';
    if(!state.manifest){const manifest=[];for(let i=0;i<state.selected.length;i++){const f=state.selected[i];manifest.push({name:f.name,size:f.size,sha256:await hashFile(f,i,state.selected.length)});}state.manifest=manifest;}
    checkStop();
    if(state.resume){state.upload=await bounded(()=>api('/uploads/'+state.resume.id),'이어 올릴 작업 확인');
      const unused=state.manifest.map((m,i)=>({...m,index:i}));const ordered=[];
      for(const file of state.upload.files){const found=unused.findIndex(m=>m.name===file.name&&m.size===file.size&&m.sha256===file.sha256);if(found<0)throw new Error('기존 작업과 파일 내용이 다릅니다. 원래 선택했던 모든 파일을 다시 선택하세요.');ordered.push(state.selected[unused[found].index]);unused.splice(found,1);}
      if(unused.length)throw new Error('선택한 파일 수가 원래 제출 묶음과 다릅니다.');state.selected=ordered;
      state.manifest=state.upload.files.map(f=>({name:f.name,size:f.size,sha256:f.sha256}));
    } else if(!state.upload){state.requestId=state.requestId||requestId();state.upload=await bounded(()=>api('/uploads',{method:'POST',json:{assignment_id:$('assignment').value,request_id:state.requestId,files:state.manifest}}),'제출 시작 요청 확인');}
    if(state.upload.status==='completed'){receipt(state.upload);return;}
    if(['cancelled','expired','failed'].includes(state.upload.status))throw new Error('이 작업은 '+(labels[state.upload.status]||state.upload.status)+' 상태입니다. 새 제출로 시작하세요.');
    renderProgress();hidden('cancel',false);let fileIndex=0;
    for(const descriptor of state.upload.files){
      checkStop();const local=state.selected[fileIndex];$('current-file').textContent=(fileIndex+1)+' / '+state.upload.files.length+' · '+descriptor.name;
      while(true){
        checkStop();const remote=state.upload.files.find(f=>f.id===descriptor.id);if(remote.offset>=remote.size)break;
        await bounded(async()=>{
          // Query server on every attempt; an earlier response may have been lost.
          state.upload=await api('/uploads/'+state.upload.id);const f=state.upload.files.find(v=>v.id===descriptor.id);
          if(f.offset>=f.size)return;const offset=f.offset;const blob=local.slice(offset,Math.min(offset+state.quota.chunk_bytes,f.size));
          const hash=new SHA256().update(await blob.arrayBuffer()).digest();checkStop();
          $('status').textContent='전송 중';$('progress-help').textContent='전송량 100% 후에도 서버 검증과 저장이 완료될 때까지 기다려 주세요.';
          const result=await sendChunk(f,blob,offset,hash);f.offset=result.offset;renderProgress();
        },'서버 수신 위치에서 청크 재전송');
      }
      fileIndex++;
    }
    checkStop();$('status').textContent='검증 중';$('progress-help').textContent='모든 파일의 실제 크기와 SHA-256을 서버에서 확인하고 있습니다.';renderProgress();
    if(state.upload.status!=='finalizing')state.upload=await bounded(()=>api('/uploads/'+state.upload.id+'/verify',{method:'POST'}),'파일 검증');
    checkStop();$('status').textContent='저장 중';$('progress-help').textContent='파일 저장과 제출 기록 확정이 진행 중입니다. 아직 제출 완료가 아닙니다.';
    state.upload=await bounded(()=>api('/uploads/'+state.upload.id+'/complete',{method:'POST'}),'제출 확정');
    if(state.upload.status!=='completed')throw new Error('서버의 완료 확정을 받지 못했습니다. 상태를 확인하고 다시 시도하세요.');
    receipt(state.upload);await refresh();
  } catch(error) {
    $('status').textContent=state.stopped?'일시 중지':(!error.status?'연결 끊김 / 작업 중단':'업로드 중단');
    const canRetry=state.stopped||retryable(error)||error.status===507;
    $('progress-help').textContent=error.message+(canRetry?' · 다시 시도할 수 있습니다.':' · 입력 또는 계정 상태를 수정한 뒤 다시 시작하세요.');
    notice(error.message,true);hidden('retry',!canRetry);hidden('fresh',!state.upload);
    if(error.status===401){state.token=null;hidden('auth',false);$('progress-help').textContent+=' 다시 로그인한 뒤 같은 파일로 이어 올리세요.';}
  } finally {controls(false);if(state.upload?.status==='completed'){$('start').disabled=true;hidden('retry');hidden('cancel');}}
}
function receipt(u) {
  state.resume=null;hidden('resume-indicator');hidden('fresh');$('start').textContent='제출 완료';
  $('status').textContent='제출 완료';$('progress-help').textContent='모든 파일과 제출 기록의 저장이 완료되었습니다.';renderProgress();
  $('receipt').replaceChildren(element('h2','제출이 완료되었습니다.'),element('p','제출번호 '+u.submission_number+' · 버전 '+u.version),element('p',(u.assignment_title||u.assignment_id)+' · '+(state.user?.user_id||u.user_id)),element('p',date(u.completed_at)));
  for(const f of u.files)$('receipt').append(element('div',f.name+' · '+exact(f.size),'file-row'));
  const next=element('button','다른 파일 새로 제출','secondary');next.onclick=fresh;$('receipt').append(next);hidden('receipt',false);
}
function fresh(){state.upload=null;state.resume=null;state.requestId=null;state.manifest=null;state.selected=[];$('files').value='';$('assignment').disabled=false;hidden('fresh');hidden('resume-indicator');hidden('progress-panel');hidden('receipt');notice('');selection();}
async function download(file) {
  try{const {ticket}=await api('/files/'+encodeURIComponent(file.id)+'/ticket',{method:'POST'});
    // Native browser download streams to disk; neither fetch nor Streamlit buffers it.
    const form=element('form');form.method='POST';form.action='/api/downloads';form.target='_self';form.style.display='none';
    const input=element('input');input.type='hidden';input.name='ticket';input.value=ticket;form.append(input);document.body.append(form);form.submit();form.remove();
  }catch(error){notice(error.message,true);}
}
function renderHistory(items) {
  $('history-list').replaceChildren();if(!items.length)$('history-list').append(element('p','아직 완료된 제출이 없습니다.','muted'));
  for(const u of items){const entry=element('article',undefined,'history-entry');entry.append(element('h3',(u.assignment_title||u.assignment_id)+' · 제출번호 '+u.submission_number),element('p','버전 '+u.version+' · '+date(u.completed_at)+' · 합계 '+bytes(u.total_bytes),'meta'));
    for(const f of u.files){const row=element('div',undefined,'history-file');row.append(element('span',f.name+' · '+bytes(f.size)));const button=element('button','다운로드','secondary');button.onclick=()=>download(f);row.append(button);entry.append(row);}$('history-list').append(entry);}
}
$('login-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{await authenticate(await api('/auth/login',{method:'POST',json:{user_id:$('user-id').value,password:$('password').value}}));}catch(error){notice(error.message,true);}finally{button.disabled=false;}};
$('logout').onclick=async()=>{try{await api('/auth/logout',{method:'POST'});}catch(error){notice(error.message,true);return;}state.token=null;state.user=null;fresh();hidden('workspace');hidden('auth',false);notice('로그아웃했습니다.');};
$('files').onchange=()=>{state.selected=Array.from($('files').files);state.manifest=null;state.requestId=null;if(!state.resume)state.upload=null;notice('');selection();};
const dropZone=document.querySelector('.drop-zone');
for(const event of ['dragenter','dragover'])dropZone.addEventListener(event,e=>{e.preventDefault();if(!$('files').disabled)dropZone.classList.add('dragging');});
for(const event of ['dragleave','drop'])dropZone.addEventListener(event,e=>{e.preventDefault();dropZone.classList.remove('dragging');});
dropZone.addEventListener('drop',e=>{if($('files').disabled||!e.dataTransfer.files.length)return;$('files').files=e.dataTransfer.files;$('files').dispatchEvent(new Event('change',{bubbles:true}));});
$('assignment').onchange=()=>{const a=state.assignments.find(v=>v.id===$('assignment').value);$('assignment-description').textContent=a?.description||'';state.requestId=null;state.upload=null;selection();};
$('start').onclick=run;$('retry').onclick=run;$('pause').onclick=stop;$('fresh').onclick=fresh;
$('refresh').onclick=()=>refresh().catch(error=>notice(error.message,true));
$('cancel').onclick=async()=>{
  if(!state.upload)return;const uploadId=state.upload.id;state.cancelling=true;stop();controls(state.running);$('cancel').disabled=true;
  try{
    // Abort can reach the server before its current chunk lock is released.
    for(let attempt=0;;attempt++){
      try{await api('/uploads/'+uploadId+'/cancel',{method:'POST'});break;}
      catch(error){if(attempt>=3||![409,423].includes(error.status))throw error;await new Promise(resolve=>setTimeout(resolve,400*2**attempt));}
    }
    fresh();notice('업로드를 취소했습니다. 예약 용량과 임시 파일이 정리됩니다.');await refresh();
  }catch(error){notice(error.message,true);}finally{state.cancelling=false;$('cancel').disabled=false;controls(state.running);}
};
async function initialize() {
  try{
    const bootstrap=$('session-data');let session=null;
    if(bootstrap){
      try{session=JSON.parse(bootstrap.textContent);}
      catch{throw new Error('제출 화면의 로그인 정보를 읽지 못했습니다. 수업 화면에서 다시 열어 주세요.');}
      finally{bootstrap.remove();}
      hidden('auth');
    }
    const info=await loadData('/info');state.info=info;$('course').textContent=info.course_name||'과제 제출';document.title=(info.course_name||'AssignmentHub')+' · 과제 제출';
    if(session){state.initialAssignment=session.assignment_id;await authenticate(session);}
    else{hidden('auth',false);}
  }catch(error){state.token=null;hidden('workspace');hidden('auth',false);notice(error.message,true);}
  finally{hidden('loading-panel');}
}
initialize();
window.addEventListener('beforeunload',event=>{if(state.running){event.preventDefault();event.returnValue='';}});
