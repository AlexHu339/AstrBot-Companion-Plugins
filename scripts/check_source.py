"""Offline structural checks; does not start AstrBot or call a model."""
from pathlib import Path
import ast,json,sys
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'PLUGIN_MANIFEST.json').read_text(encoding='utf-8'))
count=0
for item in manifest['plugins']:
 p=root/'plugins'/item['directory']
 for name in ('main.py','metadata.yaml','LICENSE','_conf_schema.json'):
  if not (p/name).is_file():raise SystemExit('Missing '+str(p/name))
for p in root.rglob('*'):
 if not p.is_file() or '.git' in p.parts:continue
 if p.suffix=='.py':ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p));count+=1
 elif p.suffix=='.json':json.loads(p.read_text(encoding='utf-8-sig'))
print(f'PASS: {len(manifest["plugins"])} plugins, {count} Python files; required files and JSON valid. Runtime/API compatibility requires your own environment.')
