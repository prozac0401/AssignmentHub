"""Render the two-page Korean quick guide with embedded system fonts."""
from pathlib import Path
import html
import re
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]

def rich(value):
    value=html.escape(value)
    value=re.sub(r'\*\*(.+?)\*\*',r'<b>\1</b>',value)
    value=re.sub(r'`([^`]+)`',r'<font color="#4338ca">\1</font>',value)
    return value

def main():
    target=Path(sys.argv[1]).resolve()
    if target.exists():
        raise FileExistsError(target)
    fonts=Path('C:/Windows/Fonts')
    pdfmetrics.registerFont(TTFont('Malgun',str(fonts/'malgun.ttf')))
    pdfmetrics.registerFont(TTFont('Malgun-Bold',str(fonts/'malgunbd.ttf')))
    pdfmetrics.registerFontFamily('Malgun',normal='Malgun',bold='Malgun-Bold')
    body=ParagraphStyle('body',fontName='Malgun',fontSize=11.5,leading=17.5,spaceAfter=7,wordWrap='CJK',textColor=colors.HexColor('#202539'))
    title=ParagraphStyle('title',parent=body,fontName='Malgun-Bold',fontSize=24,leading=32,spaceAfter=5)
    small=ParagraphStyle('small',parent=body,fontSize=9,leading=13,textColor=colors.HexColor('#566176'),spaceAfter=10)
    note=ParagraphStyle('note',parent=body,fontSize=10.3,leading=15.5,spaceBefore=3,spaceAfter=5)
    lines=(ROOT/'docs/manual/quick-guide.md').read_text(encoding='utf-8').splitlines()
    flow=[];active=False;code=None;page=0
    for line in lines:
        if line.startswith('## '):
            if page:flow.append(PageBreak())
            page+=1;active=True
            role='운영자용' if page==1 else '수강생용'
            flow.append(Paragraph(role+' 퀵가이드',title))
            flow.append(Paragraph('AssignmentHub v1.3.0 · 2026-09-08',small))
            if page==2:
                flow.append(Paragraph('수업 주소: ____________________________<br/>과제명: _______________________________',body))
                flow.append(Spacer(1,4))
        elif not active or not line.strip():
            continue
        elif line.startswith('```'):
            if code is None:code=[]
            else:
                table=Table(code,colWidths=[155,160,196],hAlign='LEFT')
                table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Malgun'),('FONTSIZE',(0,0),(-1,-1),9.7),
                                          ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eceaff')),('TEXTCOLOR',(0,0),(-1,-1),colors.HexColor('#202539')),
                                          ('BOTTOMPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),
                                          ('LINEBELOW',(0,-1),(-1,-1),0.5,colors.HexColor('#cbd1df'))]))
                flow.extend([table,Spacer(1,6)]);code=None
        elif code is not None:
            code.append(line.split('\t'))
        else:
            is_step=bool(re.match(r'^\d+\.',line))
            flow.append(Paragraph(rich(line),body if is_step else note))
    target.parent.mkdir(parents=True,exist_ok=True)
    def footer(canvas,doc):
        canvas.setTitle('AssignmentHub v1.3.0 운영자·수강생 퀵가이드')
        canvas.setAuthor('AssignmentHub')
        canvas.setFont('Malgun',8)
        canvas.setFillColor(colors.HexColor('#647087'))
        canvas.drawString(42,23,'상세 사용 예시는 함께 제공된 시나리오별 매뉴얼을 참고하세요.')
        canvas.drawRightString(A4[0]-42,23,f'{doc.page} / 2')
    doc=SimpleDocTemplate(str(target),pagesize=A4,rightMargin=42,leftMargin=42,topMargin=34,bottomMargin=40)
    doc.build(flow,onFirstPage=footer,onLaterPages=footer)
    reader=PdfReader(target)
    assert len(reader.pages)==2,f'Expected exactly two pages, got {len(reader.pages)}'
    assert '운영자용' in reader.pages[0].extract_text() and '수강생용' in reader.pages[1].extract_text()
    print(f'Created two-page quick guide: {target}')

if __name__=='__main__':
    main()
