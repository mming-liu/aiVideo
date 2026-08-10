# -*- coding: utf-8 -*-
import sys, io, os
sys.path.insert(0, r'C:\myFloders\git\aiVideo')
os.chdir(r'C:\myFloders\git\aiVideo')

d = __import__('json').load(
    open(r'output\video\锦衣夜行\part_01_杀青州\chapter_001\script_result.json', encoding='utf-8'))
out = io.open(r'_tmp_real_val.txt', 'w', encoding='utf-8')

from step01_script_gen.validation import _check_prop_spatial_anchor

# 假并列清单（无方位）应被 validation 拦截
fake_shot = {
    'index': 99,
    'ff_desc': '杨旭手持酒壶、钓杆、匕首，站在船头',
    'motion_desc': '杨旭举起酒壶',
    'lf_desc': '杨旭放下酒壶',
}
res = _check_prop_spatial_anchor([fake_shot], None, d.get('characters'))
out.write('=== 假并列清单 validation ===\n')
out.write(f'warnings: {len(res["warnings"])}\n')
for w in res['warnings']:
    out.write(f'  {w}\n')

# 真实 hook（有方位）不应误报
res2 = _check_prop_spatial_anchor(d['shots'], d.get('hook'), d.get('characters'))
out.write('\n=== 真实 script validation ===\n')
out.write(f'warnings: {len(res2["warnings"])}\n')
for w in res2['warnings']:
    out.write(f'  {w}\n')

out.close()
print('done')
