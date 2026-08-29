# -*- coding: utf-8 -*-
"""pytest 全局隔离（v6.50，08-28）.

修两类真实事故：
1. **测试日志污染产线日志**：step01_script_gen/main.py 模块级
   ``get_logger(CHAPTER_DIR / "step01.log")``——任何直接/间接 import main
   的测试都会创建绑定产线日志路径的 TeeLogger，此后 engine 层测试的
   ``[CONSTRAINT] broken 异常`` ``duplicate_append: a ↔ b`` 等告警全部
   写进生产 step01.log（08-28 实测，误导排障：产线日志里出现测试
   专用 toy 约束名，被误判为「旧路径在产线运行」）。
   修法：conftest 顶部（先于 shared.config / dotenv）把 OUTPUT_BASE
   指到临时目录——CHAPTER_DIR 随之落在临时目录，测试日志与产线隔离。
2. **shell 残留开关劫持测试行为**：HARD_FIX_USE_OLD 这类行为开关一旦
   在某次调试里 export 过，后续所有进程（含 pytest）静默继承。
   conftest 统一 pop 掉，测试需要时自行 monkeypatch.setenv。
"""
import os
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 1) 先于 shared.config 导入改写 OUTPUT_BASE（load_dotenv 不覆盖已存在的 env，
#    此处显式设置即可压过 .env.content 里的值）
_TMP_OUT = Path(tempfile.mkdtemp(prefix="aivideo_test_out_"))
os.environ["OUTPUT_BASE"] = str(_TMP_OUT)

# 2) 清理可能从 shell 泄漏进来的行为开关（测试各自按需 setenv）
for _k in ("HARD_FIX_USE_OLD", "REGEN_AUTO_TRIGGER", "SELF_REVIEW_ENABLED",
           "IMAGE_QC_WRITE_CRITICAL", "QC_SEMANTICS_CRITICAL"):
    os.environ.pop(_k, None)
