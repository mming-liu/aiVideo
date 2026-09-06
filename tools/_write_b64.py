#!/usr/bin/env python
# tools/_write_b64.py - 写大文件 (b64 解码 stdin->目标)
import sys, base64, os
target = sys.argv[1]
data = base64.b64decode(sys.stdin.read())
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, 'wb') as f:
    f.write(data)
print('wrote', target, len(data), 'bytes')
