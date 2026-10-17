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
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 1) 先于 shared.config 导入改写 OUTPUT_BASE（load_dotenv 不覆盖已存在的 env，
#    此处显式设置即可压过 .env.content 里的值）
_TMP_OUT = Path(__file__).resolve().parent.parent / ".pytest_tmp"
_TMP_OUT.mkdir(parents=True, exist_ok=True)
os.environ["OUTPUT_BASE"] = str(_TMP_OUT)

# 2) 清理可能从 shell 泄漏进来的行为开关（测试各自按需 setenv）
#    注意：这里只对**不在 .env.content 里**的键有效——见下面 autouse fixture 的说明。
for _k in ("HARD_FIX_USE_OLD", "REGEN_AUTO_TRIGGER", "SELF_REVIEW_ENABLED",
           "IMAGE_QC_WRITE_CRITICAL", "QC_SEMANTICS_CRITICAL"):
    os.environ.pop(_k, None)

# 3) 把顶层带 load_dotenv 的配置模块在 **collection 期**导入掉。
#    v6.117 autouse fixture 的逐用例 `delenv("AUDIT_TEXT_PROVIDER")` 依赖
#    「导入都发生在 fixture 之前」这一假设（本文件下方 docstring 已写明：
#    pop 之后任何一次 load_dotenv 都会把 .env.content 里的值写回 os.environ）。
#    单文件运行时该假设会破：首个用例体内 `import step01_script_gen.audit`
#    触发 `_text_gen.config` 首次导入 → load_dotenv 晚于 fixture 的 delenv 执行
#    → 审计专用通道被意外激活，用例行为随运行方式漂移（2026-09-26 实测）。
#    在这里先导入完，之后测试体内的 import 全部命中缓存，不再触发 load_dotenv。
import shared.config  # noqa: F401,E402
import _text_gen.config  # noqa: F401,E402


@pytest.fixture(autouse=True)
def _audit_channel_off_by_default(monkeypatch):
    """审计专用通道默认关闭，用例各自 `setenv` 打开（v6.117，2026-09-25）.

    为什么不能用上面那种「导入时 pop」：`AUDIT_TEXT_PROVIDER` 写在 `.env.content` 里，
    pop 掉之后任何一次 `load_dotenv`（`shared.paths` / `shared.config` / `_text_gen.config`
    顶层都有）都会把它**写回来**——实测 pop 后 `import shared.paths` 即恢复。逐用例
    `monkeypatch.delenv` 在所有导入之后执行、结束自动还原，才真正隔离。

    不隔离的实测后果：`_reusable_final_audit` 的通道判据会拒掉那些没写
    `channel_provider` 的旧 fixture，4 个「复用判据」用例变红——而它们测的是
    revision / 指纹 / prompt 版本，与通道无关。
    """
    monkeypatch.delenv("AUDIT_TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("AUDIT_TEXT_MODEL", raising=False)
    # 定向修复专用通道同理（2026-09-28 新增，也写在 .env.content 里）：
    # 不隔离的话每个用例都会去建原生 deepseek 服务，修复路径的行为随 .env 漂移。
    monkeypatch.delenv("REPAIR_TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("REPAIR_TEXT_MODEL", raising=False)


@pytest.fixture(autouse=True)
def _no_external_transport(request):
    """**离线护栏**：任何用例都不许发起进程外传输（2026-10-08 事故后补的机制）.

    事故形态：新写的提交链用例漏调 `_chapter` / `_fake_audit`，`_commit_step01_outputs`
    于是**真的**去调了审计模型（3 次真实调用、2 次成功），并把日志追加进了正式章节目录。
    「忘了打桩」不能靠人记住——非回环地址的 `connect` 直接抛（真模型调用要走授权流程，
    不走用例）。判据**单源**在 `tools.probe_isolation.block_external_transport`。

    ⚠️ **`tests/test_probe_isolation.py` 例外**：那个文件**就是**在验证这套机制，
    它的对照组必须在撤掉注入之后**真的发得出去**一次外部 `connect`——否则"对照组恢复"
    这条断言永远为假（实测：加护栏后该用例转红，正是这个原因）。
    """
    if str(getattr(request.node, "fspath", "")).endswith("test_probe_isolation.py"):
        yield
        return
    from tools.probe_isolation import block_external_transport
    with block_external_transport():
        yield


# ---------------------------------------------------------------------------
# 3) 会话级磁盘治理（2026-10 复核 P0）：防止 `.pytest_tmp` 无界膨胀
# ---------------------------------------------------------------------------
# 实测现场：`.pytest_tmp` 涨到 **189 GB / 33.8 万文件**（C 盘仅剩 137 GB）。
# 两个成因**都在测试侧**，且都发生在 `OUTPUT_BASE` 之下：
#
#   ① 按 PID 命名的隔离根从不回收。
#      `tests/enhancements/test_motion_{cli,revise,render,overlay,validate,schema}.py`
#      各自算 `BASE = Path(os.environ["OUTPUT_BASE"]) / f"motion_revise_{os.getpid()}"`
#      ——每个 pytest 进程留一个，实测 **每个 ~1,376 MB / 1,599 文件**，往后再没人删。
#      pytest 的 `tmp_path_retention_*`（见 pytest.ini）**管不到**它们：它们不是 tmp_path。
#
#   ② 测试日志追加进同一个文件。
#      `shared/tee_logger` 以追加模式写 `step01.log`，而本节把 `OUTPUT_BASE` 指到
#      `.pytest_tmp`，于是上万次测试的日志累到同一个文件（实测单个 **417 MB**，
#      另有快照日志 371 MB）。
#
# 下面在**会话开始**时做两件事：回收「非本进程且进程已退出」的 PID 目录；截断超限日志。
#
# ⚠️ 为什么**不**把 attempt 的 `frozen/assets/font_serif` 改成硬链接（曾评估）：
# 每个 attempt 一份 24 MB 字体副本确实是单目录体量的主要来源，但**冻结副本必须与来源
# 相互独立**——`shared/enhancement_io.py:763` 记录其 sha256、`:894` 再读回复核，
# `attempts/<case>/frozen/` 的「来源变了它不变」正是任务包不可变语义的前提。改成硬链接
# 等于让冻结副本随来源一起变，静默破坏该语义。**故不做**（登记为待办，需独立裁决）。
_STALE_TMP_PREFIXES = ("motion_revise_", "motion_cli_", "motion_fixtures_")

#: 单个日志文件的会话级上限；超过就在会话开始时截断（保留一行说明，不删文件本身）
_TEST_LOG_CAP_BYTES = 10 * 1024 * 1024


def _pid_is_alive(pid: int) -> bool:
    """尽力判断 PID 是否仍存活（判不了就当存活，宁可留着也不误删）."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True
    return True


def _reap_stale_pid_dirs(root, current_pid: int) -> list:
    """删掉**非本进程且进程已退出**的 PID 隔离根；返回保留的目录名."""
    import shutil

    survivors: list = []
    try:
        children = list(root.iterdir())
    except OSError:
        return survivors
    for child in children:
        if not child.is_dir():
            continue
        prefix = next((p for p in _STALE_TMP_PREFIXES if child.name.startswith(p)), None)
        if prefix is None:
            continue
        suffix = child.name[len(prefix):]
        if not suffix.isdigit():
            continue
        pid = int(suffix)
        if pid == current_pid or _pid_is_alive(pid):
            survivors.append(child.name)
            continue
        shutil.rmtree(child, ignore_errors=True)
    return survivors


def _truncate_oversized_test_logs(root) -> int:
    """把超过上限的测试日志截断（保留一行说明），返回截断个数."""
    truncated = 0
    try:
        candidates = list(root.rglob("*.log"))
    except OSError:
        return truncated
    for log_path in candidates:
        try:
            if log_path.stat().st_size > _TEST_LOG_CAP_BYTES:
                log_path.write_text(
                    "# truncated by tests/conftest.py（会话级日志上限 %d MB）\n"
                    % (_TEST_LOG_CAP_BYTES // (1024 * 1024)),
                    encoding="utf-8")
                truncated += 1
        except OSError:
            # 单个文件不可读/不可写（被占用等）不影响其余回收，也不阻断会话
            continue
    return truncated


@pytest.fixture(scope="session", autouse=True)
def _reap_stale_test_scratch():
    """会话开始回收上一轮遗留的 PID 隔离根 + 截断超限测试日志（见本节说明）."""
    root = _TMP_OUT
    if not root.exists():
        yield
        return
    current_pid = os.getpid()
    survivors = _reap_stale_pid_dirs(root, current_pid)
    truncated = _truncate_oversized_test_logs(root)
    if survivors or truncated:
        print("[conftest] 磁盘治理：保留活动 PID 目录 %d 个，截断超限日志 %d 个"
              % (len(survivors), truncated))
    yield
