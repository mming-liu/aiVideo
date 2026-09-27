# -*- coding: utf-8 -*-
"""step01 日志路径单一来源 + 可覆盖（09-26 取证障碍回归）.

## 为什么需要它

`step01_script_gen/main.py` **模块级**执行
`get_logger(chapter_dir() / "step01.log")`——任何导入 main 的进程都会把
logger 绑到**生产章目录**的 step01.log。pytest 侧已由 `tests/conftest.py`
（v6.50）改写 `OUTPUT_BASE` 隔离；但**诊断脚本/探针**（`.tmp/*.py`）不在
pytest 里，导入 main 即写生产日志。09-26 实测：`.tmp/probe_contract_anchoring.py`
单次运行往生产 step01.log 写了 142 行 CONSTRAINT-WARN，事后**无法从日志区分
哪几行属于正式 run**——正是排查候选塌缩 P0 时的取证障碍。

修法：路径收敛到 `shared.paths.step01_log_file()`（唯一来源），
`STEP01_LOG_PATH` 可整体覆盖，main / timecode / validation 三处一起跟随。

本文件锁两件事：① 覆盖开关生效、默认路径不变；② 三处初始化不得再自行拼
`chapter_dir() / "step01.log"`（拼回去 = 覆盖开关对那处失效）。
"""
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.paths import chapter_dir, step01_log_file  # noqa: E402

_LOGGER_SITES = [
    "step01_script_gen/main.py",
    "step01_script_gen/timecode.py",
    "step01_script_gen/validation.py",
]


def test_step01_log_path_defaults_to_chapter_dir(monkeypatch):
    monkeypatch.delenv("STEP01_LOG_PATH", raising=False)
    assert step01_log_file() == chapter_dir() / "step01.log"
    assert step01_log_file(chapter=2) == chapter_dir(2) / "step01.log"


def test_step01_log_path_env_override(monkeypatch, tmp_path):
    """探针/并行进程用 STEP01_LOG_PATH 把日志写到自己那份，不碰生产日志."""
    target = tmp_path / "probe.log"
    monkeypatch.setenv("STEP01_LOG_PATH", str(target))
    assert step01_log_file() == target
    # 覆盖优先于 chapter 参数：任何章都写同一份诊断日志（探针只跑一章）
    assert step01_log_file(chapter=9) == target


def test_step01_log_path_blank_env_is_ignored(monkeypatch):
    """空串/空白 = 未设置（env_guard 那套「空值即默认」口径），不得变成 CWD 相对路径."""
    for blank in ("", "   "):
        monkeypatch.setenv("STEP01_LOG_PATH", blank)
        assert step01_log_file() == chapter_dir() / "step01.log"


def test_no_logger_site_builds_chapter_log_path_by_hand():
    """三处 logger 初始化必须走 step01_log_file()，不得自行拼 chapter_dir()/step01.log."""
    offenders = []
    for rel in _LOGGER_SITES:
        src = (_PROJECT_ROOT / rel).read_text(encoding="utf-8")
        if "step01_log_file()" not in src:
            offenders.append("%s: 未使用 step01_log_file()" % rel)
        if re.search(r'get_logger\(\s*chapter_dir\([^)]*\)\s*/\s*"step01\.log"', src):
            offenders.append("%s: 仍在自行拼 chapter_dir()/step01.log" % rel)
    assert not offenders, "；".join(offenders)
