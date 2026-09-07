"""Read-only native PowerPoint rendering. Does not alter or close user decks.

Usage: bundled-python scripts/render_manual_powerpoint.py PPTX OUTPUT_DIR
Requires Windows, PowerPoint and pywin32. Output is QA, not an authored deck.
"""
import json
from pathlib import Path
import sys
import win32com.client
import psutil


def main():
    source=Path(sys.argv[1]).resolve()
    output=Path(sys.argv[2]).resolve()
    output.mkdir(parents=True,exist_ok=True)
    existing={p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='powerpnt.exe'}
    app=win32com.client.DispatchEx('PowerPoint.Application')
    deck=None
    try:
        deck=app.Presentations.Open(str(source),True,False,False)
        slides=[]
        for index in range(1,deck.Slides.Count+1):
            slide=deck.Slides(index)
            slide.Export(str(output/f'slide-{index:02}.png'),'PNG',1600,900)
            text=[]
            for shape in slide.Shapes:
                if shape.HasTextFrame and shape.TextFrame.HasText:
                    frame=shape.TextFrame2
                    text.append({'text':frame.TextRange.Text,'box_height':shape.Height,
                                 'text_height':frame.TextRange.BoundHeight,
                                 'left':shape.Left,'top':shape.Top,'width':shape.Width})
            slides.append({'slide':index,'text':text})
            print(f'Rendered slide {index}',flush=True)
        report={'engine':'Microsoft PowerPoint','version':app.Version,'slides':len(slides),'source':source.name,
                'read_only':True,'slide_width_pt':deck.PageSetup.SlideWidth,
                'slide_height_pt':deck.PageSetup.SlideHeight,'text_geometry':slides}
        (output/'native-render.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        if deck is not None:
            deck.Close()
        # PowerPoint may reuse a pre-existing application even for DispatchEx.
        if not existing:
            app.Quit()


if __name__=='__main__':
    main()
