# -*- coding: utf-8 -*-
# 用法: python tools\_gen_file.py <dst> <utf8_content_as_b64>
import sys, base64, os
dst = sys.argv[1]
data = base64.b64decode(sys.argv[2])
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(dst, 'wb') as f:
    f.write(data)
print('wrote', dst, len(data), 'bytes')
