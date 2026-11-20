# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.runtime —— 导入期副作用、路径与日志、控制台重定向、锁、超时、指标、计时、预算；项目内导入的汇集点.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
import json

#: 原 `main.py` 的路径：迁出代码里的 `__file__` 一律改用它（行为不变）
_MAIN_FILE = str(Path(__file__).resolve().parent.parent / "main.py")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
# 统一配置（从 shared.config 读取 .env）
from shared.config import (DURATION_MIN_SEC, DURATION_MAX_SEC,
                           DURATION_TARGET_MIN_SEC, DURATION_TARGET_MAX_SEC,
                           duration_band, duration_band_matches)
from shared.config import (
    NOVEL_CHAPTER, BOOK_INDEX, resolve_novel_file,
    CHARACTER_EXTRACTION_ENABLED,
    SCENE_OBJECT_EXTRACTION_ENABLED,
    SCENE_ASSET_EXTRACTION_ENABLED,
    PROP_ASSET_EXTRACTION_ENABLED,
    COMPLIANCE_STRICT, COMPLIANCE_AUTO_FIX,
    HOOK_ENABLED,
    HOOK_MAX_SENDS,
    GAP_ANALYSIS_ENABLED, GAP_MAX_REGEN_ATTEMPTS,
    SHOT_COUNT_REGEN_ENABLED,
    REPAIR_ROUNDS_ENABLED,
    SCRIPT_CANDIDATE_TOPK,
)

# import 期 HOOK_ENABLED 快照：调用方/测试显式改写模块常量时视为强制覆盖，
# 运行期判定让位于它；否则按调用时 env 重算（S16-1 根因 B）。
_HOOK_ENABLED_DEFAULT = HOOK_ENABLED
#: 钩子分类预算的 import 期快照（运行期以 env 为准，见 `_hook_max_sends`）。
_HOOK_MAX_SENDS_DEFAULT = HOOK_MAX_SENDS

# v6.23：大纲两阶段开关（ShortVideoDirector director-outline 吸收，默认开启）
OUTLINE_ENABLED = os.getenv("OUTLINE_ENABLED", "1").strip() == "1"

# 输出目录基于 book/chapter 计算（统一路径来源: shared/paths.py）
from shared.paths import (
    chapter_dir,
    script_file,
    step01_log_file,
    audio_dir,
    vimax_working_dir,
    vimax_shots_dir,
    stretched_dir,
    final_file,
    subtitles_file,
)
from shared.constants import SCRIPT_PARSE_RETRIES, SCRIPT_PARSE_RETRY_SLEEP_SEC
from shared.utils.text import text_integrity_errors
from shared.quality_gate import ensure_script_revision, attach_gate_metadata
from step01_script_gen.quality_issues import (
    Issue, collect_structured_issues, is_blocking, RULE_VERSION, RULE_SOURCE_FINGERPRINT,
    PROMPT_SOURCE_FINGERPRINT,
)
from step01_script_gen.commit_gate import REUSE_MISS_TEXT as _REUSE_MISS_TEXT
# M1（2026-11-18）：入口安装不可变 RunContext；STEP01_READ_ONLY=1 时
# step01.log 落点改 devnull、章节目录 JSON 写直接拒绝（探针零写入）。
from shared.run_context import RunContext as _RunContext, install as _install_run_context
_RUN_CONTEXT = _install_run_context(_RunContext.from_env())

CHAPTER_DIR = chapter_dir()
OUTPUT_FILE = script_file()
SCRIPT_FILE = OUTPUT_FILE

# 模块级日志
from shared.tee_logger import get_logger
_logger = get_logger(step01_log_file(), console_level="INFO")

# v6.59 (2026-09-04)：检测 shell 环境变量是否覆盖 .env.content 的 PROVIDER 设置。
# 媒体/语音通道不阻断，仅 WARN 提示。详细设计见 shared/env_guard.py 顶部。
from shared.env_guard import warn_shell_overrides as _warn_shell_overrides
_warn_shell_overrides(
    ["IMAGE_PROVIDER", "VIDEO_PROVIDER", "TTS_PROVIDER"],
    logger=_logger,
)
# 2026-11-12 用户裁决：Step01 文本通道绑定 OpenCode Go 订阅制套餐，禁止被 shell
# 覆盖成 deepseek / api.deepseek.com 官方按次计费 API。复用 env_guard 已有的
# raise_if_override，对**决定套餐身份**的三个 key 做硬阻断（不新增开关、不区分模式）。
# AUDIT_TEXT_PROVIDER / REPAIR_TEXT_PROVIDER 只 WARN：tools/audit_frozen_candidate
# 会按冻结包把这俩设成 deepseek 走桩回放（零真实发送），硬阻断会破坏历史审计回放；
# 非订阅通道下 budget.unlimited 本就不生效、退回一次性授权，规则层也禁止真实运行改它。
_warn_shell_overrides(
    ["AUDIT_TEXT_PROVIDER", "REPAIR_TEXT_PROVIDER"],
    logger=_logger,
)
_warn_shell_overrides(
    ["TEXT_PROVIDER", "AGENT_PLAN_TEXT_BASE_URL", "AGENT_PLAN_TEXT_MODEL"],
    logger=_logger,
    raise_if_override=True,
)


# ============================================================================
# 控制台静默：把运行期间的 print() 全部重定向到 step01.log
# 控制台只保留关键进度提示（_logger.console）和最终摘要
# ============================================================================
class _ConsoleToLog:
    """把 print() 输出重定向到 log 文件的对象（配合 contextlib.redirect_stdout）."""

    def write(self, s: str):
        if s and s.strip():
            _logger.verbose(s.rstrip("\n"))

    def flush(self):
        pass


def _redirect_to_log() -> _ConsoleToLog:
    """返回一个 file-like 对象，print 写进去会落到 step01.log 而非控制台."""
    return _ConsoleToLog()


def _console(msg: str) -> None:
    """直接写原始 stdout（绕过重定向），用于控制台进度提示与最终摘要.

    重定向期间 _logger.info() 也会被吞进 log，故关键控制台输出走这里.
    """
    try:
        print(msg, file=_REAL_STDOUT)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"), file=_REAL_STDOUT)


# 保存真实 stdout 引用（redirect 前赋值）
_REAL_STDOUT = sys.stdout

# ============================================================================
# 导入子模块
# ============================================================================
from shared.chapter_reader import extract_section, get_chapter_meta
from _text_gen import create_service, TextGenerationRequest
from shared.character_bank import CharacterBank
from shared.scene_objects_bank import SceneObjectsBank
from shared.assets_bank import SceneAssetsBank, PropAssetsBank
from step01_script_gen.prompts import (
    build_prompt, build_hook_prompt, parse_hook_response,
    _load_gap_analysis_context, _build_regen_iron_rules,
    _build_character_anchors,
    _load_knowledge_skills, _load_local_skill_context,
    _load_knowledge_skills_for,
    auto_select_prompt_builder, auto_select_system_prompt,
    resolve_model_prompt_profile, render_execution_contract,
    append_execution_contract,
)

# v2026-09-03: 按 TEXT_PROVIDER 自动选择 prompt builder。
# agent_plan（doubao-seed-2-1-turbo）→ doubao 专属（XML schema + few-shot + workflow）
# 其他 provider → 沿用 deepseek 调优的 build_prompt / build_hook_prompt
from _text_gen.config import TEXT_PROVIDER_DEFAULT
_TEXT_PROVIDER = (os.getenv("TEXT_PROVIDER") or TEXT_PROVIDER_DEFAULT).strip().lower()
build_prompt, build_hook_prompt = auto_select_prompt_builder(_TEXT_PROVIDER)
_SYSTEM_PROMPT = auto_select_system_prompt(_TEXT_PROVIDER)
if _TEXT_PROVIDER == "agent_plan":
    # v6.143：这行原写「使用 doubao 专属 prompt」——**错**。v6.68 起 prompt 剖面跟
    # **模型**走（`resolve_model_prompt_profile`，见 `MODEL_PROMPT_PROFILES`）：
    # agent_plan 通道跑 deepseek-v4-flash 时用的是 `build_prompt` + `system.md`，
    # 与 doubao 毫无关系。硬编码这句话会主动误导「step01 在用 doubao」——
    # 2026-09-26 用户据此提出「改用 deepseek-flash」，而实测 764 次提交**全是**
    # deepseek-v4-flash。改为按**实际候选模型**逐个解析后打印。
    from shared.config import SCRIPT_CANDIDATE_MODELS as _CAND_MODELS
    _profiles = ", ".join(
        "%s→%s" % (_m, resolve_model_prompt_profile(_m)[0].__name__)
        for _m in (_CAND_MODELS or [None])) or "(provider 默认)"
    _logger.info("[INFO] agent_plan 通道：prompt 剖面按模型解析 = %s" % _profiles)
from step01_script_gen.character_extractor import extract_characters
from step01_script_gen.object_extractor import extract_scene_objects
from step01_script_gen.asset_extractor import extract_scene_assets, extract_props
from step01_script_gen.validation import parse_response, build_regen_feedback, _set_novel_text, _run_prompt_checklist
from step01_script_gen.timecode import (
    _compute_timecodes,
    write_timeline_from_durations,
    timeline_consistency_errors,
)
from step01_script_gen.hash_cache import _prompt_changed, _hash_source
from step01_script_gen.gap_analysis import (
    gap_needs_full_regen,
    generate_gap_report,
    print_gap_report,
    validate_script_gap,
)


from shared import style_prefix
import scene_compliance
import shared.content_safety as content_safety


# ===========================================================================
# v6.103（2026-09-23 总计划阶段 4）：**唯一最终提交点**
# ===========================================================================
# 正常生成 / 缓存命中 / BEST-GUARD 恢复三条入口此前各写一遍落盘序列，时序互不
# 相同：缓存路径不写 quality/audit；BEST-GUARD 在 finalize 之后还能重生 hook；
# final semantic audit 只由离线工具 `tools/run_step01_final_audit.py` 补跑，
# 生产链路里根本没有「审计 → 提交」这一步。结果是 `step01_quality.json` PASS、
# 同 revision 的审计却有 4 条 blocking，`require_semantic_gate()` 必然拒绝。
#
# 现在只有一条提交路径，顺序固定（总计划 §3）：
#
#   候选（BEST-GUARD 只**选**）→ hook QC/regen → finalize → revision
#     → 确定性 quality → final semantic audit
#         ├─ 无 blocking → staged 三件套 → require_semantic_gate 自验
#         │                → audit → quality → script 依次替换 → commit manifest
#         │                → 正式路径再自验 + manifest 校验
#         └─ 有 blocking → 受限修复（≤2 轮，每轮重新 finalize+revision）
#                          → 仍有 blocking 就 BLOCK（不写三份产物）
#
# 「final audit 之后禁止任何语义写入」由两件事保证：① 提交点之后不再调用任何
# 修复/约束/post_fix；② audit 前后各算一次 revision，不等则本轮审计作废。
STEP01_AUDIT_SCOPE = "final_after_deterministic_fixes"


def _step01_run_id() -> str:
    """本轮运行身份（提交产物/台账/诊断/bank/调用记录都用它自证是哪一轮）.

    实现在 `shared.run_identity`（**单一来源**）：`STEP01_RUN_ID` 由跑批驱动注入
    （父子同身份），否则**进程内首次调用生成一次并缓存**。

    2026-10-02 复核：旧实现每次按**当前秒**重拼 `%Y%m%dT%H%M%S-<pid>`——同一轮里
    相隔一秒调用就得到两个不同 id（实测 `…183824-18820` / `…183825-18820`），
    于是「诊断」与「manifest」各写一个 id，产物无法自证同轮。缓存是硬要求。
    """
    from shared.run_identity import current_run_id
    return current_run_id()


def _step01_now() -> str:
    import time as _time
    return _time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_json_quiet(path) -> "dict | None":
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# v6.63.1：_locate_narration_in_novel 下沉至 gap_analysis（constraints 层复用，
# 反向 import main 有重依赖 + 循环导入风险 + 触发 style_prefix 弃用 shim）。
# 此处保留同名兼容导出，行为零变化。
from step01_script_gen.gap_analysis import _locate_narration_in_novel  # noqa: F401,E402


from step01_script_gen.repair_fields import (merge_authorized as _merge_authorized,
                                          apply_scoped_field_updates as _apply_scoped_field_updates,
                                          canonical_path_diff,
                                          frame_layout_structural_violations,
                                          MODEL_AUDIT_WARNING_PREFIX)


def _pid_is_alive(pid: int) -> bool:
    """安全查询 PID 是否存活（Windows 不使用 os.kill(pid, 0)）.

    Windows 的 os.kill(pid, 0) 会调用 TerminateProcess，而不是 POSIX 的
    「只探测不发送信号」。因此 Windows 必须走 OpenProcess；无权查询时保守
    当作存活，避免误删其他进程持有的 chapter 锁。
    """
    if pid <= 0:
        return False
    import os as _os
    if _os.name != "nt":
        try:
            _os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    import ctypes
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _process_query_limited_information = 0x1000
    _synchronize = 0x00100000
    _handle = _kernel32.OpenProcess(
        _process_query_limited_information | _synchronize,
        False,
        pid,
    )
    if _handle:
        _kernel32.CloseHandle(_handle)
        return True
    # ERROR_ACCESS_DENIED：进程存在但当前用户无权打开。
    return ctypes.get_last_error() == 5


def _acquire_step01_lock() -> bool:
    """章节级 single-flight 锁：禁止两个 step01 并发写同一章.

    O_EXCL 创建 .step01.lock；创建者 PID 已退出才允许接管 stale 锁。
    """
    import os as _os
    import time as _time
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CHAPTER_DIR / ".step01.lock"
    try:
        fd = _os.open(lock_path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL)
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": _os.getpid(), "started_at": _time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        return True
    except FileExistsError:
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                info = json.load(f)
            pid = int(info.get("pid", 0))
        except Exception:
            pid = 0
        if pid <= 0:
            try:
                lock_path.unlink()
            except OSError:
                pass
            return _acquire_step01_lock()
        if _pid_is_alive(pid):
            _logger.warn(f"[LOCK] 已有 step01 运行中（PID={pid}），本次跳过")
            _console(f"[LOCK] 已有 step01 运行中（PID={pid}），本次跳过")
            return False
        try:
            lock_path.unlink()
        except OSError:
            pass
        return _acquire_step01_lock()


def _release_step01_lock() -> None:
    """仅删除本进程创建的锁，避免释放他人锁."""
    import os as _os
    lock_path = CHAPTER_DIR / ".step01.lock"
    try:
        if not lock_path.exists():
            return
        with open(lock_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        if int(info.get("pid", 0)) == _os.getpid():
            lock_path.unlink()
    except Exception as _e:
        _logger.warn(f"[LOCK] 释放 step01 锁失败: {_e}")


def _candidate_early_stop_enabled() -> bool:
    """候选早停开关，默认开启；=0 时保持 v6.65「凑满 TOPK」旧行为."""
    import os as _os
    return _os.getenv("CANDIDATE_EARLY_STOP", "1").strip() != "0"


def _candidate_gen_timeout_sec() -> int | None:
    """候选生成请求级超时（v6.70.3，默认 300s；env 设 0 = 回退 provider 默认 600s）.

    09-14 实测：候选 3 耗 12 分钟把「100 分候选 64s 到手」的 run 拖成 17m38s；
    超时判 success=False → 走既有「失败重试/凑不够就用手头候选」路径，不再盲等。
    """
    import os as _os
    try:
        _v = int(_os.getenv("CANDIDATE_GEN_TIMEOUT_SEC", "300"))
    except ValueError:
        _v = 300
    return _v if _v > 0 else None


def _full_script_timeout_sec() -> int | None:
    """全脚本级 LLM 调用超时（GAP 重生 / 结构收敛，v6.70.3 默认 300s）.

    v6.101：结构收敛轮从 `_repair_timeout_sec()`（180s）改到本档——它也是
    「整章输入 + 整脚本输出（~26k token）」，与 GAP 重生同量级；
    第十四次重跑实测 180s 不够（客户端超时 → 候选拿不到）。
    """
    import os as _os
    try:
        _v = int(_os.getenv("FULL_SCRIPT_TIMEOUT_SEC", "300"))
    except ValueError:
        _v = 300
    return _v if _v > 0 else None


def _repair_timeout_sec() -> int | None:
    """定向修复/超限收敛/hook 超时（v6.70.3 默认 180s）.

    这些站点失败语义都是「保留原版继续」，600s 盲等只会把整轮拖死（09-13/14
    两晚修复轮 + hook 重生各白等一次 600s）。
    """
    import os as _os
    try:
        _v = int(_os.getenv("REPAIR_TIMEOUT_SEC", "180"))
    except ValueError:
        _v = 180
    return _v if _v > 0 else None


# ============================================================================
# v6.70.3（09-14）：阶段计时 + 总预算
# 09-14 监控审计发现 17m38s 的 run 无任何结构化耗时数据，事后只能靠日志时间戳
# 人工拼时间线。timing 落盘 step01_timing.json；预算超时跳过「失败即保留原版」
# 语义的可选修复轮（regen_over_limit / repair rounds），核心链路（候选/GAP/hook）不受影响。
# ============================================================================
_STEP_TIMING: list = []
_STEP_TIMING_T0 = None
#: run 级计时起点的 monotonic 孪生（`_timing_start()` 一起设；墙上时钟会被系统时间调整
#: 污染，**截止时刻只认 monotonic**——工作包 §4 B）
_STEP_TIMING_T0_MONO = None
_STEP_LAST_MARK_T = None
_RUN_METRICS: dict = {}
_METRICS_LOCK = None
#: **本轮终局**（落进 metrics/timing）：`{"status": "ok"|"failed", "code": "", "detail": ""}`。
#: 由 `_run_finish()` 设置；`_timing_dump` / `_metrics_dump` 只读它，
#: 不再各自硬编码 "ok"（2026-10-02 复核：失败运行被报成 ok）。
_RUN_OUTCOME: dict = {"status": "ok", "code": "", "detail": ""}


def _run_outcome_reset() -> None:
    _RUN_OUTCOME.update({"status": "ok", "code": "", "detail": ""})


def _run_finish(status: str, code: str = "", detail: str = "") -> None:
    """记录本轮终局（幂等：**失败优先**，失败后再写 ok 不覆盖）."""
    _s = str(status or "").strip() or "ok"
    if _RUN_OUTCOME["status"] == "failed" and _s == "ok":
        return
    _RUN_OUTCOME.update({"status": _s, "code": str(code or ""), "detail": str(detail or "")[:300]})


def _metrics_lock():
    global _METRICS_LOCK
    if _METRICS_LOCK is None:
        import threading as _threading
        _METRICS_LOCK = _threading.Lock()
    return _METRICS_LOCK


def _metrics_bucket(path: str) -> dict:
    """Create/return a nested metrics bucket for dotted path."""
    cur = _RUN_METRICS
    for part in path.split("."):
        if not part:
            continue
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    return cur


def _metrics_inc(path: str, n: int = 1) -> None:
    try:
        with _metrics_lock():
            parts = path.split(".")
            cur = _RUN_METRICS
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            key = parts[-1]
            cur[key] = int(cur.get(key, 0) or 0) + int(n)
    except Exception:
        pass


def _metrics_set(path: str, value) -> None:
    try:
        with _metrics_lock():
            parts = path.split(".")
            cur = _RUN_METRICS
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            cur[parts[-1]] = value
    except Exception:
        pass


def _metrics_update(path: str, values: dict) -> None:
    try:
        with _metrics_lock():
            bucket = _metrics_bucket(path)
            bucket.update(values or {})
    except Exception:
        pass


def _metrics_dump(path) -> None:
    """Persist structured call/time/repair metrics without affecting the run.

    2026-10-02 复核：载荷里**必须**有 `run_id` 与**真实终局**——旧版两章都以
    `PlanInfeasibleError` 收场，metrics 却写 `_total status=ok` 且 `calls={}`，
    等于把失败运行报成成功、且无任何调用读数可对账。
    """
    try:
        import time as _t
        from shared.json_io import atomic_dump_json as _adj

        payload = dict(_RUN_METRICS)
        payload["run_id"] = _step01_run_id()
        payload["status"] = _RUN_OUTCOME["status"]
        payload["outcome_code"] = _RUN_OUTCOME["code"]
        payload["phases"] = list(_STEP_TIMING)
        if _STEP_TIMING_T0 is not None:
            payload["total_elapsed_sec"] = round(_t.time() - _STEP_TIMING_T0, 1)
        _adj(path, payload, ensure_ascii=False, indent=2)
    except Exception as _e:
        try:
            _logger.warn(f"[METRICS] metrics 落盘失败（不阻断）: {_e}")
        except Exception:
            pass


def _timing_start() -> None:
    """run 级计时起点（main() 拿锁后调用）."""
    global _STEP_TIMING_T0, _STEP_LAST_MARK_T, _STEP_TIMING_T0_MONO
    import time as _t
    _STEP_TIMING_T0 = _t.time()
    _STEP_TIMING_T0_MONO = _t.monotonic()          # 截止时刻只用 monotonic（工作包 §4 B）
    _STEP_LAST_MARK_T = _STEP_TIMING_T0
    _STEP_TIMING.clear()
    _run_outcome_reset()
    _RUN_METRICS.clear()
    _RUN_METRICS.update({
        "run_started": _STEP_TIMING_T0,
        "calls": {},
        "repair": {},
        "audit": {},
        "hook": {},
        "duration": {},
        "quality": {},
    })


def _project_commit_outcome(commit_summary: dict | None) -> None:
    """提交结果 → **运行终局**（两处提交返回共用的唯一投影）.

    复核 2026-10-04 C1：章 2 那次 `step01_commit.json` = BLOCK（5 条 blocking、修复轮零改善），
    而 `step01_metrics.json` / `step01_timing.json` 记的是 `status=ok`、`outcome_code=""`
    ——提交点是**唯一的正式产物出口**，它 BLOCK 就是这一轮失败，运行终局不能报 ok。
    审计自己的 `audit_status=ok` 留在提交清单里（那是"审计调用成功"的事实，不冲突）。

    失败优先：`_run_finish` 本身幂等，早期已记的失败不会被这里的 `ok` 覆盖。
    """
    _s = (commit_summary or {}).get("status")
    if _s == "COMMITTED":
        _run_finish("ok")
        return
    _detail = str((commit_summary or {}).get("detail") or "")
    _code = str((commit_summary or {}).get("blocking_code") or "") or "commit_blocked"
    _run_finish("failed", _code, _detail or "提交点未产出 COMMITTED 清单")


def _timing_mark(phase: str, status: str = "ok", **fields) -> None:
    """记录一个阶段完成点（毫秒级开销，失败静默）.

    ``elapsed_sec`` is cumulative since run start and is retained for backward
    compatibility.  ``phase_duration_sec`` is the independent duration since
    the previous mark, which is the field optimization work should consume.
    """
    global _STEP_LAST_MARK_T
    import time as _t
    try:
        if _STEP_TIMING_T0 is None:
            return
        now = _t.time()
        last = _STEP_LAST_MARK_T if _STEP_LAST_MARK_T is not None else _STEP_TIMING_T0
        _rec = {
            "phase": phase,
            "status": status,
            "phase_duration_sec": round(max(0.0, now - last), 1),
            "elapsed_since_start_sec": round(now - _STEP_TIMING_T0, 1),
            # Backward-compatible alias; treat as cumulative, not phase cost.
            "elapsed_sec": round(now - _STEP_TIMING_T0, 1),
        }
        _STEP_LAST_MARK_T = now
        _rec.update(fields)
        _STEP_TIMING.append(_rec)
    except Exception:
        pass


def _timing_dump(path) -> None:
    """落盘 timing JSON（失败只告警，绝不影响主流程）."""
    try:
        import time as _t
        if _STEP_TIMING_T0 is not None:
            now = _t.time()
            last = _STEP_LAST_MARK_T if _STEP_LAST_MARK_T is not None else _STEP_TIMING_T0
            _STEP_TIMING.append({
                "phase": "_total", "status": _RUN_OUTCOME["status"],
                "phase_duration_sec": round(max(0.0, now - last), 1),
                "elapsed_since_start_sec": round(now - _STEP_TIMING_T0, 1),
                # Backward-compatible alias; cumulative, not phase cost.
                "elapsed_sec": round(now - _STEP_TIMING_T0, 1),
            })
        _payload = {"run_started": _STEP_TIMING_T0, "run_id": _step01_run_id(),
                    "status": _RUN_OUTCOME["status"],
                    "outcome_code": _RUN_OUTCOME["code"],
                    "phases": _STEP_TIMING}
        import json as _json
        from shared.json_io import atomic_dump_json as _adj
        _adj(path, _payload, ensure_ascii=False, indent=2)
    except Exception as _e:
        try:
            _logger.warn(f"[TIMING] timing 落盘失败（不阻断）: {_e}")
        except Exception:
            pass


def _total_budget_sec() -> float:
    """run 总预算（v6.70.3 起默认 900s；2026-11-15 提到 1200s；0 = 不限时）.

    2026-11-15（run 20261115T140440g 实测）：900s 下 plan/修复阶段方差一大，
    最终语义审计只剩 186s 墙钟（审计实际耗时 274–420s）→ SSE 未收完按调用失败
    BLOCK——内容全对、纯粹时间不够。1200s 给审计留足 480s 余量。
    """
    import os as _os
    try:
        _v = float(_os.getenv("STEP01_TOTAL_BUDGET_SEC", "1200"))
    except ValueError:
        _v = 1200.0
    return _v


def _budget_exceeded() -> bool:
    """超预算判定（未 start 或预算 0 = False）."""
    import time as _t
    if _STEP_TIMING_T0 is None or _total_budget_sec() <= 0:
        return False
    return (_t.time() - _STEP_TIMING_T0) > _total_budget_sec()


def _paused_checkpoint_enabled() -> bool:
    """PAUSED 落盘开关（T1，默认关闭 = 入口行为与改造前逐字一致）.

    只控制「超时是否把最新 CP 记成 PAUSED」这一保护性副作用；不控制
    `STEP01_TOTAL_BUDGET_SEC` 的判定与默认值（仍默认 1200s，0=不限时）。
    """
    return str(os.getenv("STEP01_PAUSED_CHECKPOINT", "")).strip().lower() in (
        "1", "true", "yes", "on")


def _record_budget_pause(reason: str, *, run_id: str = "") -> dict:
    """超时/预算不足时把**最近完成的 CP** 记为 PAUSED；失败只告警，不抛.

    记录字段：`stage`（暂停态）、`checkpoint_stage`（四态）、`resume_from`
    （CP1–CP4）、`run_id`、`revision`、`written_at`、`reason`。写入落在
    `snapshots/_paused.json`，不碰四态索引——`latest()` 的正常语义保持不变。
    """
    rec: dict = {"written": False, "skipped": False, "reason": reason, "error": ""}
    if not _paused_checkpoint_enabled():
        rec["skipped"] = True
        rec["detail"] = "STEP01_PAUSED_CHECKPOINT 未启用（默认行为不变）"
        return rec
    try:
        from shared import step01_checkpoint as _ckpt
        _latest = _ckpt.latest(CHAPTER_DIR)
        if not _latest:
            rec["detail"] = "没有已完成阶段检查点，无法记录 PAUSED"
            return rec
        _key = str(_latest.get("key") or "")
        _cp = _key
        if _key.startswith(_ckpt.CP4_REPAIR_ROUND_K_DONE + "_r"):
            _cp = _ckpt.CP4_REPAIR_ROUND_K_DONE
        _elapsed = None
        if _STEP_TIMING_T0 is not None:
            import time as _t
            _elapsed = round(max(0.0, _t.time() - _STEP_TIMING_T0), 1)
        rec.update(_ckpt.write_paused(
            CHAPTER_DIR, _cp,
            run_id=str(run_id or _step01_run_id()),
            revision=str(_latest.get("revision") or ""),
            reason=reason,
            extra={"checkpoint_key": _key,
                   "budget_sec": _total_budget_sec(),
                   "elapsed_sec": _elapsed},
            log=_logger))
        rec["reason"] = reason
        if rec.get("written"):
            _logger.warn("[BUDGET] 已记录 PAUSED 检查点（%s，续跑起点 %s）"
                         % (rec.get("stage"), rec.get("resume_from")))
    except Exception as _e:                       # noqa: BLE001
        rec["error"] = "%s: %s" % (type(_e).__name__, _e)
        try:
            _logger.warn("[BUDGET] PAUSED 检查点写入失败（不改变超时结论）: %s" % _e)
        except Exception:
            pass
    return rec


def _budget_exceeded_with_pause(reason: str) -> bool:
    """`_budget_exceeded()` 的接线壳：超时先落 PAUSED，再返回同一结论.

    未超时零副作用；`STEP01_PAUSED_CHECKPOINT` 未启用时与 `_budget_exceeded()`
    逐字等价（只多一次不写盘的开关判断）。
    """
    if not _budget_exceeded():
        return False
    _record_budget_pause(reason)
    return True


def _planning_deadline_at(cap_sec=None):
    """整次 Step 01 的**规划截止时刻**（monotonic 绝对时刻；工作包 §4 B）.

    `deadline = min(整次剩余预算换算出的截止点, 现在 + cap_sec)`；两者都没有 ⇒ `None`
    （不设截止 ⇒ 通道行为同改造前）。换算只做**一次**，此后所有环节按它重算余量。
    """
    from _text_gen.deadline import planning_deadline_at
    return planning_deadline_at(_budget_left_sec(), cap_sec)


def _budget_left_sec():
    """run 总预算**剩余秒数**（未 start 或预算 0 → None）."""
    import time as _t
    if _STEP_TIMING_T0 is None:
        return None
    _total = _total_budget_sec()
    if _total <= 0:
        return None
    return round(_total - (_t.time() - _STEP_TIMING_T0), 1)


#: 审计请求超时上限（v6.152 / A1）：对齐通道自身的 `AGENT_PLAN_TEXT_TIMEOUT_SEC` = 300
AUDIT_TIMEOUT_CAP_SEC = 300
#: 给提交收尾（写盘 / 自检 / BLOCK 记账）留的余量秒数
AUDIT_COMMIT_RESERVE_SEC = 60


def _audit_budget_allowance() -> tuple:
    """**整次审计**的可用时长与截止时刻：`(allowance_sec, deadline_ts | None, info)`.

    v6.152（A1，2026-09-29 复核两轮）：上限 180 → 300（对齐通道自身），且**必须受本跑
    剩余总预算约束**——实测单跑已到 950–995s，只抬上限等于把「审计超时 BLOCK」换成
    「总预算耗尽 BLOCK」。

    这里给的是**整次审计**（含截断/解析重采样）的额度，不是单次请求的超时：调用方把它
    换成 `deadline_ts` 传进审计，由审计在**每次实际发起前**重算单次超时；剩余不足以
    发起一次有意义的调用时，审计**不发请求**、直接记 `budget_exhausted`。
    旧实现给单次请求截一个 5s 下限，既注定超时、又照样挤占收尾余量。

    无总预算约束（未 start / 预算 0）→ `deadline_ts=None`，行为与旧版一致。
    """
    import os as _os
    try:
        cap = int(float(_os.getenv("AUDIT_TIMEOUT_SEC",
                                   str(AUDIT_TIMEOUT_CAP_SEC)) or AUDIT_TIMEOUT_CAP_SEC))
    except (TypeError, ValueError):
        cap = AUDIT_TIMEOUT_CAP_SEC
    left = _budget_left_sec()
    if left is None:
        return cap, None, {"cap": cap, "left": None, "reserve": None,
                           "why": "无总预算约束（未 start 或预算 0），用上限 %ss" % cap}
    allowance = int(max(0, min(cap, left - AUDIT_COMMIT_RESERVE_SEC)))
    import time as _t
    if allowance >= cap:
        return cap, _t.time() + cap, {
            "cap": cap, "left": left, "reserve": AUDIT_COMMIT_RESERVE_SEC,
            "why": "余量充足，用上限 %ss" % cap}
    return allowance, _t.time() + allowance, {
        "cap": cap, "left": left, "reserve": AUDIT_COMMIT_RESERVE_SEC,
        "why": ("上限 %ss 受剩余总预算约束（剩余 %.1fs - 收尾预留 %ss）"
                % (cap, left, AUDIT_COMMIT_RESERVE_SEC)),
    }


# ---------------------------------------------------------------------------
# M04（2026-11-15）：审计预算准入 / 冻结候选 / 四项计时
#
# 缺口：`_audit_budget_allowance()` 只在审计**即将开始时**截短额度，前序的
# 额外生成 / 再生成 / 修复可以把时间烧到只剩 126s（run i），再抬总预算也补不回
# 完整审计。这里在**每个额外动作启动之前**先按「本次动作 + 最终生效审计上限 +
# 提交收尾」判定；不足即保存冻结候选与 `budget_exhausted` 明确终局。
# ---------------------------------------------------------------------------
def _final_audit_reserve_config(*, include_runtime: bool = False) -> dict:
    """最终生效的审计配置身份。

    默认**只含稳定项**，供冻结候选与续验比对；`include_runtime=True` 另带
    随时刻变化的读数（剩余预算 / 本次审计允许时长），**不得**进配置指纹。
    """
    _allow, _deadline, _to_info = _audit_budget_allowance()
    cfg = {
        "audit_timeout_cap_sec": int(_to_info.get("cap") or AUDIT_TIMEOUT_CAP_SEC),
        "commit_reserve_sec": AUDIT_COMMIT_RESERVE_SEC,
        "total_budget_sec": _total_budget_sec(),
        "audit_scope": STEP01_AUDIT_SCOPE,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
    }
    if include_runtime:
        cfg["audit_allowance_sec"] = int(_allow)
        cfg["left_sec"] = _budget_left_sec()
    return cfg


def _action_cost_sec(kind: str) -> float:
    """「本次动作」的保守耗时 = 该动作**最终生效的超时上限**（最坏情况）。

    取的是配置上限，不是猜出来的预估：regen 走通道超时，修复轮走
    `_repair_timeout_sec()`。任何取不到的情形回退到通道默认 300s。
    """
    if str(kind) == "repair":
        try:
            return float(_repair_timeout_sec() or 300)
        except Exception:
            return 300.0
    try:
        return float(os.getenv("AGENT_PLAN_TEXT_TIMEOUT_SEC", "300") or 300)
    except (TypeError, ValueError):
        return 300.0


def _action_budget_admission(action: str, action_cost_sec: float) -> dict:
    """用**既有预算函数**的读数跑纯函数准入，不另造一套剩余预算计算."""
    from step01_script_gen.repair_budget import admit_action
    cfg = _final_audit_reserve_config()
    adm = admit_action(
        remaining_budget_sec=_budget_left_sec(),
        action_cost_sec=action_cost_sec,
        audit_cap_sec=cfg["audit_timeout_cap_sec"],
        commit_reserve_sec=cfg["commit_reserve_sec"])
    rec = adm.as_record()
    rec["action"] = str(action or "")
    rec["audit_config"] = cfg
    return rec


def _audit_budget_timing(result, *, allowance_sec, left_sec,
                         phase: str = "final_audit") -> dict:
    """把四项读数**分别**记成结构化台账项；不可得为 None 且标来源."""
    from step01_script_gen.repair_budget import budget_timing_record
    _t = dict(getattr(result, "timing", None) or {})
    rec = budget_timing_record(
        remaining_budget_sec=left_sec,
        audit_allowance_sec=allowance_sec,
        server_wait_sec=_t.get("server_wait_sec"),
        stream_elapsed_sec=_t.get("stream_elapsed_sec"),
        stream_timing_source=_t.get("stream_timing_source")
        or "provider_not_instrumented",
        phase=phase)
    rec["provider_timing"] = _t.get("provider_timing")
    rec["attempts"] = int(getattr(result, "attempts", 1) or 1)
    return rec


def _early_stop_score() -> float:
    """早停阈值；默认 95，可用 env 调整（非法值回退 95）."""
    import os as _os
    try:
        return float(_os.getenv("CANDIDATE_EARLY_STOP_SCORE", "95"))
    except ValueError:
        return 95.0


def _candidate_early_stop_max_shots() -> int:
    """早停镜头上限；默认 20，0 = 不限制.

    09-15 实测：100 分、20 镜、90s、高潮完整的候选已经拿到，却因旧的
    `len(shots) > 18` 不放行，继续等同一模型两次 300s 超时，候选阶段
    861.7s。20 镜仍是下游可生成规模，且分数已包含高潮完整性，因此把
    上限做成配置，默认 20。
    """
    import os as _os
    try:
        _v = int(_os.getenv("CANDIDATE_EARLY_STOP_MAX_SHOTS", "20"))
    except ValueError:
        _v = 20
    return _v if _v > 0 else 0


def _candidate_is_early_stop(score: dict, script: dict) -> bool:
    """高分候选 + 基础结构健康 → 允许放弃等待慢候选.

    只做确定性结构护栏（shot 数 / 时长 / 钩子），避免把「剧情高分但不可生成」
    的候选误判为终稿；下游 hard gate 仍照常执行。
    """
    if not _candidate_early_stop_enabled():
        return False
    if score.get("gate_status") == "BLOCK":
        return False
    if float(score.get("final_score", score.get("total", 0)) or 0) < _early_stop_score():
        return False
    if score.get("climax_complete") is not True:
        return False
    # Quality-aware gate: a raw 100 with blocking visual risks or corrupted
    # text must not win early-stop and lock the run into an unsafe candidate.
    if score.get("quality_checked") is False:
        return False
    if int(score.get("blocking_risk_count", 0) or 0) > 0:
        return False
    if score.get("quality_checked") and score.get("integrity_ok") is not True:
        return False
    shots = script.get("shots", [])
    if not shots:
        return False
    _max_shots = _candidate_early_stop_max_shots()
    if _max_shots and len(shots) > _max_shots:
        return False
    duration = sum(float(shot.get("duration_sec", 0) or 0) for shot in shots)
    if not DURATION_MIN_SEC <= duration <= DURATION_MAX_SEC:
        return False
    if not script.get("hook"):
        return False
    return True

# <split:generated>
# 项目内导入的名字由其他编排模块经 `_rt.X` 使用（门面打桩的落点）。
__all__ = [
    "CHARACTER_EXTRACTION_ENABLED",
    "COMPLIANCE_AUTO_FIX",
    "CharacterBank",
    "DURATION_MAX_SEC",
    "DURATION_MIN_SEC",
    "DURATION_TARGET_MAX_SEC",
    "DURATION_TARGET_MIN_SEC",
    "GAP_ANALYSIS_ENABLED",
    "GAP_MAX_REGEN_ATTEMPTS",
    "HOOK_ENABLED",
    "HOOK_MAX_SENDS",
    "Issue",
    "MODEL_AUDIT_WARNING_PREFIX",
    "NOVEL_CHAPTER",
    "PROMPT_SOURCE_FINGERPRINT",
    "PROP_ASSET_EXTRACTION_ENABLED",
    "PropAssetsBank",
    "REPAIR_ROUNDS_ENABLED",
    "RULE_SOURCE_FINGERPRINT",
    "RULE_VERSION",
    "SCENE_ASSET_EXTRACTION_ENABLED",
    "SCENE_OBJECT_EXTRACTION_ENABLED",
    "SCRIPT_CANDIDATE_TOPK",
    "SCRIPT_PARSE_RETRIES",
    "SCRIPT_PARSE_RETRY_SLEEP_SEC",
    "SHOT_COUNT_REGEN_ENABLED",
    "SceneAssetsBank",
    "SceneObjectsBank",
    "TextGenerationRequest",
    "_REUSE_MISS_TEXT",
    "_apply_scoped_field_updates",
    "_build_character_anchors",
    "_build_regen_iron_rules",
    "_compute_timecodes",
    "_hash_source",
    "_load_gap_analysis_context",
    "_load_knowledge_skills",
    "_load_knowledge_skills_for",
    "_locate_narration_in_novel",
    "_merge_authorized",
    "_prompt_changed",
    "_run_prompt_checklist",
    "_set_novel_text",
    "append_execution_contract",
    "attach_gate_metadata",
    "audio_dir",
    "build_hook_prompt",
    "build_regen_feedback",
    "canonical_path_diff",
    "collect_structured_issues",
    "content_safety",
    "create_service",
    "duration_band",
    "duration_band_matches",
    "ensure_script_revision",
    "extract_characters",
    "extract_section",
    "final_file",
    "frame_layout_structural_violations",
    "gap_needs_full_regen",
    "generate_gap_report",
    "get_chapter_meta",
    "is_blocking",
    "parse_hook_response",
    "parse_response",
    "print_gap_report",
    "render_execution_contract",
    "resolve_model_prompt_profile",
    "resolve_novel_file",
    "scene_compliance",
    "stretched_dir",
    "style_prefix",
    "subtitles_file",
    "text_integrity_errors",
    "timeline_consistency_errors",
    "validate_script_gap",
    "vimax_shots_dir",
    "vimax_working_dir",
    "write_timeline_from_durations",
]
# </split:generated>
