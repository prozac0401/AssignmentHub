"""Package only the reader-facing v1.2 manual and fictional TSV examples."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'docs/manual'
FILES=('README.md','index.html','scenarios.md','quick-guide.md',
       'AssignmentHub_사용자_매뉴얼.pdf','AssignmentHub_사용자_매뉴얼.pptx','AssignmentHub_퀵가이드.pdf')

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    destination=ROOT/'dist/manual-v1.2.0'
    destination.mkdir(parents=True,exist_ok=True)
    name='AssignmentHub-1.2.0-Manual'
    archive=destination/(name+'.zip')
    if archive.exists():
        raise FileExistsError(archive)
    files=[SOURCE/p for p in FILES]+sorted((SOURCE/'examples').glob('*.tsv'))+sorted((SOURCE/'screenshots').glob('*.png'))
    assert all(p.is_file() and p.resolve().is_relative_to(SOURCE.resolve()) for p in files)
    manifest={p.relative_to(SOURCE).as_posix():digest(p) for p in files}
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for p in files:
            bundle.write(p,name+'/'+p.relative_to(SOURCE).as_posix())
        bundle.writestr(name+'/SHA256SUMS.txt',''.join(f'{sha}  {p}\n' for p,sha in manifest.items()))
    for source,target in [('AssignmentHub_사용자_매뉴얼.pdf',name+'.pdf'),
                          ('AssignmentHub_사용자_매뉴얼.pptx',name+'.pptx'),
                          ('AssignmentHub_퀵가이드.pdf','AssignmentHub-1.2.0-Quick-Guide.pdf')]:
        target=destination/target
        if target.exists():
            raise FileExistsError(target)
        shutil.copy2(SOURCE/source,target)
    artifacts=[archive,*sorted(destination.glob('*.pdf')),*sorted(destination.glob('*.pptx'))]
    checksums=destination/'AssignmentHub-1.2.0-Manual-SHA256SUMS.txt'
    checksums.write_text(''.join(f'{digest(p)}  {p.name}\n' for p in artifacts),encoding='ascii')
    print(json.dumps({'folder':str(destination),'manual_files':len(files),
                      'assets':[{ 'name':p.name,'bytes':p.stat().st_size,'sha256':digest(p)} for p in [*artifacts,checksums]]},ensure_ascii=False))

if __name__=='__main__':
    main()
