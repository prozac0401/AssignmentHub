"""Build an offline scenario manual and a two-page quick guide from local copy."""
from pathlib import Path
import html
import json

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/manual'

def main():
    slides=json.loads((OUT/'slides.json').read_text(encoding='utf-8'))
    routes=json.loads((OUT/'routes.json').read_text(encoding='utf-8'))
    scenarios=json.loads((OUT/'scenarios.json').read_text(encoding='utf-8'))
    esc=html.escape
    md=['# AssignmentHub v1.2.0 시나리오별 사용 예시','',
        'Python 내장 무설치 배포와 TSV 명단 입력을 기준으로 작성했습니다. 예시 인물과 수업은 모두 가상입니다.',
        '', '[상세 PDF](AssignmentHub_사용자_매뉴얼.pdf) · [편집용 PPTX](AssignmentHub_사용자_매뉴얼.pptx) · [2쪽 퀵가이드](AssignmentHub_퀵가이드.pdf)', '',
        '캡처의 `127.0.0.1`과 임시 포트·경로는 재현용입니다. 교육장에서는 실제 서버의 네트워크 IP와 운영용 저장 폴더를 지정합니다.', '']
    nav=[];sections=[]
    for scenario,route in zip(scenarios,routes):
        n,title,role,pages=route
        nav.append(f'<a href="#scenario-{n:02}">{n:02}. {esc(title)}</a>')
        md.extend([f'## {n:02}. {title}', '',f'**담당:** {role} / 상세 매뉴얼 {pages}쪽','',
                   '**상황:** '+scenario['situation'],''])
        part=[f'<section id="scenario-{n:02}"><h2>{n:02}. {esc(title)}</h2>',
              f'<p class="meta">{esc(role)} / PDF·PPTX {pages}쪽</p>',
              f'<p><strong>상황</strong> {esc(scenario["situation"])}</p>']
        for example in scenario.get('examples',[]):
            path=OUT/'examples'/example
            body=path.read_text(encoding='utf-8-sig').strip()
            md.extend([f'[예시 파일 다운로드: {example}](examples/{example})','','```tsv',body,'```',''])
            part.append(f'<p><a download href="examples/{example}">예시 다운로드: {example}</a></p><pre>{esc(body)}</pre>')
        for slide in [s for s in slides if s.get('scenario')==n]:
            md.extend(['### '+slide['title'],''])
            part.append('<h3>'+esc(slide['title'])+'</h3><ol>')
            for i,(label,body) in enumerate(slide['steps'],1):
                md.append(f'{i}. **{label}**: {body}')
                part.append(f'<li><strong>{esc(label)}</strong> {esc(body)}</li>')
            md.extend(['','> '+slide['note'],''])
            part.append('</ol><p class="note">'+esc(slide['note'])+'</p>')
            if slide.get('image'):
                file='screenshots/'+slide['image']
                md.extend([f'![{slide["title"]} 실제 예시]({file})',''])
                part.append(f'<figure><a href="{file}"><img loading="lazy" src="{file}" alt="{esc(slide["title"])} 실제 화면"></a><figcaption>그림을 열면 원본 크기로 볼 수 있습니다.</figcaption></figure>')
        md.extend(['**완료 확인:** '+scenario['success'],''])
        part.append('<p class="done"><strong>완료 확인</strong> '+esc(scenario['success'])+'</p></section>')
        sections.append('\n'.join(part))
    (OUT/'scenarios.md').write_text('\n'.join(md).rstrip()+'\n',encoding='utf-8')
    css='''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:#202539;background:#f7f8fc;font:17px/1.8 "Malgun Gothic",sans-serif}a{color:#4338ca}header{padding:48px max(24px,calc((100% - 1300px)/2));background:#eeedff}h1{font-size:38px;margin:0}header p{margin:12px 0}main{max-width:1340px;margin:auto;padding:32px 24px;display:grid;grid-template-columns:250px 1fr;gap:44px}nav{position:sticky;top:24px;align-self:start}nav a{display:block;margin:0 0 12px;text-decoration:none;font-size:15px}section{background:white;padding:32px 38px;margin-bottom:32px;scroll-margin-top:20px}h2{font-size:28px;line-height:1.5;margin:0 0 8px}h3{font-size:22px;margin:36px 0 16px}li{padding:5px 0}.meta,figcaption{color:#647087;font-size:14px}.note{border-left:3px solid #a5a0fa;padding:8px 18px;color:#4b5563}.done{border-top:1px solid #dce0ec;padding-top:20px}figure{margin:24px 0}img{display:block;width:100%;height:auto}pre{overflow:auto;background:#f1f3f9;padding:20px;font:16px/1.8 Consolas,"Malgun Gothic",monospace;tab-size:14}footer{padding:24px;text-align:center;color:#647087;font-size:14px}@media(max-width:800px){main{display:block;padding:18px}nav{position:static;margin-bottom:24px}section{padding:24px 18px}header{padding:32px 24px}h1{font-size:30px}}@media print{body{background:white;font-size:11pt}nav,header .downloads,figcaption{display:none}main{display:block;padding:0}section{padding:0;break-before:page}h2,h3{break-after:avoid}figure{break-inside:avoid}img{max-height:18cm;object-fit:contain}a{color:inherit;text-decoration:none}header{padding:0;background:white}}'''
    page=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AssignmentHub v1.2.0 시나리오별 사용자 매뉴얼</title><style>{css}</style></head><body><header><h1>AssignmentHub 사용자 매뉴얼</h1><p>v1.2.0 · 무설치 실행 · TSV 명단 입력 · 12개 사용 시나리오</p><p class="downloads"><a href="AssignmentHub_퀵가이드.pdf">2쪽 퀵가이드</a> · <a href="AssignmentHub_사용자_매뉴얼.pdf">상세 PDF</a> · <a href="AssignmentHub_사용자_매뉴얼.pptx">편집용 PPTX</a></p><p>예시 인물과 수업은 가상입니다. 캡처의 로컬 주소와 임시 경로는 실제 교육장 IP와 운영용 저장 폴더로 바꿔 사용하세요.</p></header><main><nav aria-label="시나리오 목차">{''.join(nav)}</nav><article>{''.join(sections)}</article></main><footer>2026-09-08 개정 · 모든 화면과 예시는 이 폴더에서 오프라인으로 볼 수 있습니다.</footer></body></html>'''
    (OUT/'index.html').write_text(page,encoding='utf-8')
    print('Wrote scenario Markdown and offline HTML.')

if __name__=='__main__':
    main()
