'use strict';
// Dependency-free focused DOM-mock regression; not a real browser/backend test.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
class Node {
  constructor(tag='div'){this.tag=tag;this.children=[];this.hidden=false;this.disabled=false;this.value='';this.textContent='';this.classes=new Set();this.classList={toggle:(c,on)=>on?this.classes.add(c):this.classes.delete(c),add:c=>this.classes.add(c),remove:c=>this.classes.delete(c)};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(){}
  removeAttribute(){}
}
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
const flatten=n=>n.children.flatMap(c=>[c,...flatten(c)]);
const sourcePath=process.argv[2]||path.resolve(__dirname,'../assignmenthub/static/upload.js');
let source=fs.readFileSync(sourcePath,'utf8');
assert.equal((source.match(/^initialize\(\);$/gm)||[]).length,1);
source=source.replace(/^initialize\(\);$/m,'// Initialization omitted: synthetic fixture below.');
const context=vm.createContext({console,Headers,setTimeout,clearTimeout,Intl,
  document:{getElementById:node,createElement:tag=>new Node(tag),querySelector:()=>node('drop-zone'),querySelectorAll:()=>flatten(node('unfinished')).filter(n=>n.tag==='button')},
  window:{addEventListener(){}},
});
vm.runInContext(source,context,{filename:sourcePath});
const evaluate=code=>vm.runInContext(code,context);
evaluate(`
  state.info={file_categories:[{id:'other',label:'기타',extensions:[]}]};
  state.assignments=[{id:'assignment-1',title:'Synthetic',is_open:true,allowed_file_categories:['other']}];
  state.quota={used_bytes:0,reserved_bytes:10,quota_bytes:100,max_file_bytes:100,max_files:10,chunk_bytes:8};
  $('assignment').value='assignment-1';
  var unfinishedFixture={id:'upload-1',assignment_id:'assignment-1',status:'paused',total_bytes:10,files:[{id:'file-1',name:'missing-original.txt',size:10,offset:4,sha256:'0'.repeat(64)}]};
  var mutationCalls=[];
  api=async (path,options={})=>{mutationCalls.push({path,method:options.method});return {...unfinishedFixture,status:'cancelled'};};
  refresh=async()=>{renderUnfinished([]);selection();};
  hidden('cancel');hidden('pause');renderUnfinished([unfinishedFixture]);
`);
const checks=[];const checked=name=>checks.push(name);
const hidden=id=>node(id).hidden||node(id).classes.has('hidden');
const choose=()=>flatten(node('unfinished')).find(n=>n.tag==='button').onclick();
async function main(){
  assert(hidden('cancel'));checked('cancel hidden before recovery selection');
  choose();
  assert.equal(hidden('cancel'),false,'Recovered task must offer cancellation before original files are reselected');checked('cancel exposed immediately for selected unfinished task');
  assert(node('start').disabled);checked('resume still requires file reselection');
  assert(hidden('pause'));checked('pause hidden while not transferring');
  assert(node('assignment').disabled);checked('original assignment remains locked');
  assert.equal(evaluate('mutationCalls.length'),0);checked('recovery selection performs no mutation');
  evaluate('fresh()');assert(hidden('progress-panel'));assert.equal(node('assignment').disabled,false);assert.equal(evaluate('mutationCalls.length'),0);checked('fresh keeps existing server task untouched');
  choose();assert.equal(hidden('cancel'),false);checked('repeated recovery selection restores cancellation');
  await node('cancel').onclick();
  assert.equal(evaluate('JSON.stringify(mutationCalls)'),JSON.stringify([{path:'/uploads/upload-1/cancel',method:'POST'}]));checked('explicit cancel uses original endpoint exactly once');
  assert.match(node('notice').textContent,/취소했습니다/);assert(hidden('progress-panel'));assert(hidden('cancel'));assert.equal(evaluate('state.resume'),null);checked('successful cancel resets recovery UI');
  assert.equal(flatten(node('unfinished')).filter(n=>n.tag==='button').length,0);checked('unfinished list refreshed');
  for(const status of ['completed','failed','cancelled','expired']){evaluate(`state.upload={status:${JSON.stringify(status)}};controls(false)`);assert(hidden('cancel'));}checked('terminal-state cancellation guard preserved');
  assert.equal(evaluate('state.cancelling'),false);assert.equal(node('cancel').disabled,false);checked('cancelling lock released');
  console.log(JSON.stringify({passed:true,checks,scope:'DOM mocks and synthetic API only; no browser, backend, storage, or Windows claims'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
