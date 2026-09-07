// Development-only reproducible workbook generator. The exported template is shipped.
// Run with Node.js and @oai/artifact-tool installed or linked into node_modules.
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const outputDir = path.resolve(process.argv[2] || 'templates');
const previewDir = path.resolve('outputs/template-review');
await fs.mkdir(outputDir, {recursive: true});
await fs.mkdir(previewDir, {recursive: true});
const wb = Workbook.create();
const users = wb.worksheets.add('users');
users.showGridLines = false;
users.getRange('A1:C3').values = [
  ['user_id', 'name', 'group'],
  ['001', '홍길동 (예시)', 'A반'],
  ['002', '김하늘 (예시)', 'A반'],
];
users.getRange('A1:C501').format.font = {name:'Arial', size:11, color:'#17243A'};
users.getRange('A1:C1').format = {fill:'#243C5A',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},rowHeight:28,horizontalAlignment:'center',verticalAlignment:'center'};
users.getRange('A2:C501').format.fill = '#FFF8E5';
users.getRange('A2:C501').format.rowHeight = 23;
users.getRange('A1:A501').format.columnWidth = 24;
users.getRange('B1:B501').format.columnWidth = 30;
users.getRange('C1:C501').format.columnWidth = 24;
users.getRange('A2:C501').setNumberFormat('@');
users.freezePanes.freezeRows(1);
users.tabColor = '#243C5A';
const guide=wb.worksheets.add('작성안내');
guide.showGridLines=false;
guide.getRange('A1:B15').format.font={name:'Arial',size:11,color:'#17243A'};
guide.getRange('A1:A15').format.columnWidth=25;
guide.getRange('B1:B15').format.columnWidth=87;
guide.getRange('A2').values=[['사용자 명단 작성 안내']];
guide.getRange('A2').format.font={name:'Arial',size:15,bold:true,color:'#17243A'};
guide.getRange('A4:B14').values=[
 ['입력 위치','첫 번째 users 시트의 2행부터 입력합니다. 첫 행의 컬럼명은 바꾸지 마세요.'],
 ['예시 교체','001, 002는 예시 계정입니다. 실제 명단으로 교체하거나 예시 행을 삭제하세요.'],
 ['user_id (필수)','로그인 ID. 텍스트로 입력합니다. ID 열에는 텍스트 서식이 지정되어 있습니다.'],
 ['앞자리 0','001과 1은 서로 다른 ID입니다. 숫자로 입력해 잃어버린 0은 복원하지 않습니다.'],
 ['ID 비교 정책','앞뒤 공백을 제거하고 대소문자를 구분합니다. 빈 ID와 중복 ID는 등록할 수 없습니다.'],
 ['name (필수)','화면에 표시할 이름입니다. 이름이 비어 있는 행은 수정한 뒤 다시 등록하세요.'],
 ['group (선택)','반, 부서, 분반을 입력합니다. 해당 사항이 없으면 비워 두세요.'],
 ['재등록','새 계정만 추가합니다. 기존 이름·그룹 수정은 관리 화면에서 명시적으로 선택합니다.'],
 ['기존 계정','재등록으로 기존 비밀번호·제출 이력·활성 상태가 바뀌지 않습니다.'],
 ['등록 절차','관리자 로그인 → 명단 등록 → 검증·미리보기 → 등록 적용 → 임시비밀번호 배포.'],
 ['보관','실사용 명단과 임시비밀번호 파일은 Git에 넣지 말고 접근 가능한 사람을 제한하세요.'],
];
guide.getRange('A4:A14').format.font={name:'Arial',size:11,bold:true,color:'#243C5A'};
guide.getRange('A4:B14').format.rowHeight=34;
guide.getRange('B4:B14').format.wrapText=true;
guide.getRange('A4:B14').format.verticalAlignment='center';
guide.tabColor='#8091A5';
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:'users!A1:C3',include:'values,formulas',tableMaxRows:3,tableMaxCols:3})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',options:{useRegex:true,maxResults:20}})).ndjson);
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(outputDir,'users_template.xlsx'));
const pythonArg=process.argv.find(arg=>arg.startsWith('--python='));
const pythonExe=pythonArg ? pythonArg.slice('--python='.length) : process.env.AH_PYTHON || 'python';
const finalized=spawnSync(pythonExe,[fileURLToPath(new URL('./finalize_template.py',import.meta.url)),path.join(outputDir,'users_template.xlsx')],{stdio:'inherit'});
if(finalized.status!==0)throw new Error('Native ID-column text format verification failed. Supply --python=<Python with openpyxl>.');
await fs.unlink(path.join(outputDir,'users_template.xlsx.inspect.ndjson')).catch(()=>{});
console.log('Exported '+path.join(outputDir,'users_template.xlsx'));
for (const [sheetName,range,file] of process.argv.includes('--no-render') ? [] : [['users','A1:C8','users.png'],['작성안내','A1:B15','guide.png']]) {
  const preview=await wb.render({sheetName,range,scale:1.5,format:'png'});
 await fs.writeFile(path.join(previewDir,file),new Uint8Array(await preview.arrayBuffer()));
}
