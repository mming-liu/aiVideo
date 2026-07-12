# -*- coding: utf-8 -*-
import json
from pathlib import Path
import os
import sys

# Force UTF-8
sys.stdout.reconfigure(encoding='utf-8')

book_idx = int(os.getenv('BOOK_INDEX', '1'))
chapter_idx = int(os.getenv('NOVEL_CHAPTER', '1'))
output_base = Path(os.getenv('OUTPUT_BASE', './output/video'))
script_file = output_base / f'book_{book_idx:02d}' / f'chapter_{chapter_idx:02d}' / 'script_result.json'

with open(script_file, 'r', encoding='utf-8') as f:
    script = json.load(f)

output = []
for shot in script.get('shots', []):
    if shot['index'] in [21, 22]:
        output.append(f"=== Shot {shot['index']} ===")
        output.append(shot.get('prompt', 'N/A'))
        output.append("")

# Write to file with UTF-8
with open('debug_prompt_output.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))
print("Written to debug_prompt_output.txt")