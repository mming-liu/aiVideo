# -*- coding: utf-8 -*-
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')
s = json.load(open(r'C:\myFloders\git\aiVideo\output\video\锦衣夜行\part_01_杀青州\chapter_001\script_result.json','r',encoding='utf-8'))
hook = s['hook']
print('=== hook.prompt (前 2000) ===')
print(hook.get('prompt',''))
print()
print('=== hook.ff_desc_final (前 2000) ===')
print(hook.get('ff_desc_final','')[:2000])
