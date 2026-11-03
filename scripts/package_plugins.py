"""Create per-plugin ZIPs and a source bundle without runtime data."""
from pathlib import Path
import argparse,hashlib,json,zipfile
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=root/'dist');args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
manifest=json.loads((root/'PLUGIN_MANIFEST.json').read_text(encoding='utf-8'))
def add(z,p,name):
 info=zipfile.ZipInfo(name,(2026,11,3,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=(0o100755 if p.suffix=='.sh' else 0o100644)<<16;z.writestr(info,p.read_bytes())
def allowed(p):return p.is_file() and not any(x in {'.git','__pycache__','.pytest_cache','.ruff_cache','node_modules','dist'} for x in p.relative_to(root).parts) and p.suffix not in {'.pyc','.pyo','.db','.sqlite','.sqlite3','.jsonl','.log','.wav','.mp3','.ogg','.silk','.zip'}
created=[]
for item in manifest['plugins']:
 name=item['directory'];base=root/'plugins'/name;target=out/(name+'.zip')
 with zipfile.ZipFile(target,'w') as z:
  for p in sorted(base.rglob('*')):
   if allowed(p):add(z,p,name+'/'+p.relative_to(base).as_posix())
 created.append(target)
target=out/'AstrBot-Companion-Plugins-bundle.zip'
with zipfile.ZipFile(target,'w') as z:
 for p in sorted(root.rglob('*')):
  if allowed(p) and not p.resolve().is_relative_to(out):add(z,p,'AstrBot-Companion-Plugins/'+p.relative_to(root).as_posix())
created.append(target)
(out/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in created),encoding='utf-8')
print(json.dumps({'archives':len(created),'bytes':sum(p.stat().st_size for p in created)},ensure_ascii=False))
