# -*- coding: utf-8 -*-
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')
s = json.load(open(r'C:\myFloders\git\aiVideo\output\video\锦衣夜行\part_01_杀青州\chapter_001\script_result.json','r',encoding='utf-8'))
hook = s['hook']
prompt = hook.get('prompt','')
print(f'=== prompt 长度: {len(prompt)} ===')
print(prompt)
print()
print('=== 关键片段检测 ===')
print('  含"已倾倒":', '已倾倒' in prompt)
print('  含"伤处白袍":', '伤处白袍' in prompt, '← lf 内容混入')
print('  含"血迹洇开":', '血迹洇开' in prompt, '← lf 内容')
print('  含"双目圆睁":', '双目圆睁' in prompt, '← lf 内容')
print('  含"舟侧水下":', '舟侧水下' in prompt)
print('  含"船外":', '船外' in prompt)
print('  含"船舷外":', '船舷外' in prompt)
print()
print('=== hook.lf_desc 前 300 ===')
print(hook.get('lf_desc','')[:300])
