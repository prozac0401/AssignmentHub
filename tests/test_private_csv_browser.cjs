/* Real browser check for the private CSV helper: no public media route or script injection. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict'),cp=require('node:child_process');
let playwright;try{playwright=require('playwright');}catch{playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}
async function main(){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'assignmenthub-private-csv-'));
  const html=path.join(dir,'helper.html'),expected=path.join(dir,'expected.csv');
  const python=process.env.AH_TEST_PYTHON||path.join(process.cwd(),'.venv',os.platform()==='win32'?'Scripts/python.exe':'bin/python');
  const source=[
    'from pathlib import Path','import sys','from assignmenthub import ui',
    'data = ui.csv_bytes([{ "user_id":"0001", "name":"</script><img src=x onerror=alert(1)>", "temporary_password":"=DANGEROUS()" }], ["user_id", "name", "temporary_password"])',
    'Path(sys.argv[2]).write_bytes(data)',
    'ui.components.html = lambda content, **kwargs: Path(sys.argv[1]).write_text(content, encoding="utf-8")',
    'ui.private_csv_download("CSV download", data, "private.csv")'
  ].join('\n');
  const rendered=cp.spawnSync(python,['-c',source,html,expected],{encoding:'utf8'});assert.equal(rendered.status,0,rendered.stderr);
  const browser=await playwright.chromium.launch({headless:true,...(os.platform()==='win32'?{channel:'msedge'}:{})});
  const context=await browser.newContext({acceptDownloads:true});const page=await context.newPage();const requests=[];let dialogs=0;
  page.on('dialog',async d=>{dialogs++;await d.dismiss();});page.on('request',request=>{if(/^https?:/.test(request.url()))requests.push(request.url());});
  try{
    await page.setContent(fs.readFileSync(html,'utf8'));
    const waiting=page.waitForEvent('download');await page.getByRole('button',{name:'CSV download'}).click();
    const download=await waiting;assert.equal(download.suggestedFilename(),'private.csv');
    assert.deepEqual(fs.readFileSync(await download.path()),fs.readFileSync(expected));assert.equal(dialogs,0);assert.deepEqual(requests,[]);
    console.log(JSON.stringify({passed:true,private_csv_blob:true,hostile_script_cell_escaped:true,formula_cell_escaped:true,external_requests:0,browser:browser.version()}));
  }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
