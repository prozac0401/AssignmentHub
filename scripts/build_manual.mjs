// Copy to a private build directory with node_modules linked to the Codex
// bundled runtime. Run there with <workspace> <presentation-skill> <python>.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {Presentation,PresentationFile} from '@oai/artifact-tool';

const [workspaceArg,skillArg,pythonArg,filename='AssignmentHub_사용자_매뉴얼.pptx']=process.argv.slice(2);
if(!workspaceArg||!skillArg||!pythonArg)throw new Error('Provide workspace, presentation skill directory, and bundled Python paths');
const workspaceDir=path.resolve(workspaceArg),SKILL_DIR=path.resolve(skillArg),RUNTIME_PYTHON=path.resolve(pythonArg);
const tmp=path.join(workspaceDir,'.local-tests','manual-build');
const out=path.join(workspaceDir,'docs','manual');
await fs.mkdir(tmp,{recursive:true});await fs.mkdir(out,{recursive:true});
process.env.RUNTIME_NODE_MODULES ||= await fs.realpath(path.join(tmp,'node_modules'));
process.env.RUNTIME_NODE ||= process.execPath;
process.env.RUNTIME_PYTHON ||= RUNTIME_PYTHON;
process.env.RUNTIME_BIN_DIR ||= path.resolve(RUNTIME_PYTHON,'..','..','bin','override');
const {finalizePresentation,resolvePresentationFont}=await import(pathToFileURL(path.join(SKILL_DIR,'container_tools','artifact_tool_utils.mjs')).href);
const font=resolvePresentationFont({fontFamily:'Malgun Gothic'});
const presentation=Presentation.create({slideSize:{width:1600,height:900}});
const content=JSON.parse(await fs.readFile(path.join(out,'slides.json'),'utf8'));
const routes=JSON.parse(await fs.readFile(path.join(out,'routes.json'),'utf8'));
const dark='#202539',green='#4F46E5',muted='#647087';
function wrapWords(value,width,size){
 const measure=s=>Array.from(s).reduce((n,c)=>n+(/\s/.test(c)?0.35:c.codePointAt(0)>255?1:0.57),0)*size;
 const lines=[];let line='';
 const words=value.replace(/(\d+(?:\.\d+)?)\s+(GiB|MiB|B)\b/g,'$1\u00a0$2').split(/ +/);
 for(const word of words){const trial=line?line+' '+word:word;if(line&&measure(trial)>width){lines.push(line);line=word;}else line=trial;}
 if(line)lines.push(line);return lines.join('\n');
}
function text(slide,value,x,y,w,h,size=28,bold=false,color=dark){
 const shape=slide.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 shape.text=value;shape.text.style={typeface:font,fontSize:size,bold,color,autoFit:'none'};
 return shape;
}
function base(title,page){const slide=presentation.slides.add();slide.background.fill='#FFFFFF';text(slide,title,64,40,1435,92,56,true);text(slide,String(page).padStart(2,'0'),1500,830,55,35,23,false,muted);return slide;}
const cover=presentation.slides.add();cover.background.fill='#F0F2FF';
text(cover,'AssignmentHub',92,208,1390,120,94,true);
text(cover,'시나리오별 사용자 매뉴얼',98,343,1370,85,60,true);
text(cover,'무설치 실행과 TSV 명단 입력',100,470,1350,55,34,false,green);
text(cover,'v1.3.0\n2026년 9월 8일',100,690,1200,100,27,false,muted);
cover.speakerNotes.textFrame.setText('운영자는 시나리오 01~04를 먼저 확인합니다. 수강생은 05~08을 참고합니다. 배포용 퀵가이드에는 운영자와 수강생 절차를 각각 한 쪽에 정리했습니다.');
for(let i=0;i<content.length;i++){
 const c=content[i],slide=base(c.title,i+2);
 if(i===0){
  routes.forEach(([number,title,role,pages],n)=>{const x=n<6?64:834,y=178+(n%6)*100;
   text(slide,String(number).padStart(2,'0')+'. '+title,x,y,680,46,32,true,green);
   text(slide,role+'  /  '+pages+'쪽',x,y+46,680,42,25,false,muted);
  });
  text(slide,c.note,64,827,1395,62,23,false,muted);
 }else if(c.image){
  let y=177;
  for(const [label,body] of c.steps){text(slide,label,64,y,410,48,31,true,green);text(slide,wrapWords(body,366,27),64,y+58,410,246,27);y+=310;}
  const blob=await fs.readFile(path.join(out,'screenshots',c.image));
  const iw=blob.readUInt32BE(16),ih=blob.readUInt32BE(20),scale=Math.min(1030/iw,655/ih);
  const w=iw*scale,h=ih*scale;
  slide.images.add({blob,contentType:'image/png',alt:c.title+' 실제 화면',fit:'contain',position:{left:500+(1030-w)/2,top:155+(655-h)/2,width:w,height:h}});
  text(slide,c.note,64,827,1395,62,23,false,muted);
 }else{
  c.steps.forEach(([label,body],n)=>{const x=n%2?834:64,y=n<2?192:485;text(slide,label,x,y,690,60,36,true,green);text(slide,wrapWords(body,608,29),x,y+77,660,200,29);});
  text(slide,c.note,64,823,1390,65,25,false,muted);
 }
 slide.speakerNotes.textFrame.setText(c.steps.map(([label,body])=>label+'\n'+body).join('\n\n')+'\n\n'+c.note);
}
const candidatePath=path.join(tmp,'candidate-'+Date.now()+'.pptx');
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);
console.log('Draft exported '+candidatePath);
const finalPath=path.join(out,filename);
const result=await finalizePresentation({workspaceDir,candidatePath,finalPath,pythonExecutable:RUNTIME_PYTHON,
 integrityValidatorPath:path.join(SKILL_DIR,'container_tools','inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(SKILL_DIR,'container_tools','inspect_presentation_layout_geometry.py'),
 layoutArgs:['--expected-slide-size-emu','15240000,8572500','--validate-bullet-geometry','--validate-heading-fit'],
 requiredNativeTableOwnerSlides:[],requiredNativeChartOwnerSlides:[],
 fontPolicy:{basis:'design',families:[font]},verifyArtifactToolImport:true,
 receiptPath:path.join(tmp,filename+'.validation.json')});
console.log(JSON.stringify({finalPath,slideCount:content.length+1,result}));
