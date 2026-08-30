# -*- coding: utf-8 -*-
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')
s = json.load(open(r'C:\myFloders\git\aiVideo\output\video\锦衣夜行\part_01_杀青州\chapter_001\script_result.json','r',encoding='utf-8'))
hook = s['hook']
print('=== hook.ff_desc (前 1200) ===')
print(hook.get('ff_desc','')[:1200])
print()
print('=== hook.char_layout ===')
for c in hook.get('char_layout', []):
    print(f'  {c.get("name")}: pos={c.get("position")} surface={c.get("surface","")} facing={c.get("facing","")}')
print()
print('=== hook keys ===')
for k in sorted(hook.keys()):
    v = hook.get(k)
    if isinstance(v, str):
        print(f'  {k}: {v[:80]!r}')
    else:
        print(f'  {k}: {type(v).__name__}={v}')
