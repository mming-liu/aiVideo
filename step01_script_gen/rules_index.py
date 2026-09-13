"""规则版本索引 — 分镜规则的单一事实源 (Single Source of Truth).

每个版本号对应一组规则：
  - prompt_sections:   prompts/*.md 中的段落（行号锚点，方便跳转）
  - constraints:       constraints/ 目录下的 Constraint 子类名
  - validation_checks: validation.py 中的 _check_* 函数
  - timecode_changes:  timecode.py 中的拆分/拉伸逻辑
  - style_prefix:      style_prefix.py 中的配套改写函数
  - tests:             测试文件路径

原则：
  1. 改任何一版规则前，先查此文件 → 知道要改哪些文件
  2. 新增规则版本时，在此文件追加一条记录
  3. 版本号遵循语义化：v<大版本>.<小版本>（v6.51 = v6 系列第 51 次小迭代）

用法：
  from step01_script_gen.rules_index import RULES_INDEX
  print(RULES_INDEX["v6.51"]["constraints"])
"""
from __future__ import annotations

import re
from typing import TypedDict


class RuleEntry(TypedDict, total=False):
    """单版本规则条目."""
    name: str                    # 简短描述
    date: str                    # 引入日期 YYYY-MM-DD
    prompt_sections: list[str]   # prompts/*.md 段落位置（文件:行号 或 锚点文字）
    constraints: list[str]       # Constraint.name（与 ALL_CONSTRAINTS 中 name 一致）
    validation_checks: list[str] # validation.py 中 _check_* 函数名
    timecode_changes: list[str]  # timecode.py 相关改动描述
    style_prefix: list[str]      # style_prefix.py 相关函数名
    tests: list[str]             # 测试文件路径（相对项目根）


# ---------------------------------------------------------------------------
# 规则索引（按版本号降序，最新版在前）
# ---------------------------------------------------------------------------

RULES_INDEX: dict[str, RuleEntry] = {
    # ========================================================================
    # v6.70 — 总时长规划目标 70-80s + 武器 holder 校验 + kf 帧级冗余（2026-09-13）
    # ========================================================================
    "v6.70": {
        "name": "总时长规划目标 70-80s（合法带 60-90s 不动）+ weapon_holder 校验 + kf 帧级冗余 + kf 参考图 per-frame 化",
        "date": "2026-09-13",
        "prompt_sections": [
            "prompts/system.md:K3. mid 阶段 kf 必须与 ff 拉开动作差异",
            "prompts/system.md:规划目标 70-80s（按 ~75s 分配）",
            "prompts/doubao_system.md:RULE-13b",
            "prompts/doubao_system.md:RULE-8",
            "prompts.py:_build_total_duration_constraint",
            "prompts.py:_build_regen_iron_rules",
        ],
        "constraints": [
            "kf_frame_redundancy",       # K9: mid-kf≈ff / kf≈kf / spec 复制检测（WARN）
        ],
        "validation_checks": [
            "_check_weapon_holder",      # 武器词必须有持握主体/落点锚（WARN，hook 悬空匕首根因）
        ],
        "tests": [
            "tests/step01_script_gen/constraints/test_kf_frame_redundancy.py",
            "tests/step01_script_gen/test_weapon_holder.py",
            "tests/shared/test_kf_refs_per_frame.py",  # vimax_bridge._compose_kf_refs（step02a 侧）
        ],
    },

    # ========================================================================
    # v6.69 — 旧 _hard_fix_* 家族删除 + 结构归一约束三件套（2026-09-12）
    # ========================================================================
    "v6.69": {
        "name": "旧 hard_fix 家族删除（-2973 行）+ kf 归一/重复合并/侧翻转约束 + 拆分验证回滚",
        "date": "2026-09-12",
        "prompt_sections": [],
        "constraints": [
            # 新增三件套（95 分稿复盘根治）
            "kf_timeline_normalize",     # kf 时间线归一：同刻重排/越界钳制/标签错位/hold帧回归
            "duplicate_shot_merge",      # 相邻镜重复视觉 HARD 合并（12/12.5 凑镜根治）
            "char_side_continuity",      # 同地点连续镜角色侧翻转交换（10→11 越轴根治）
            # 随家族删除一并变更：ship-integrity 条款加「本镜在船上」门控（vimax_bridge）
            # timecode 超长拆镜器补接 differentiate + 拆分后验证回滚（b.ff/b.lf、a.lf/b.lf ≥0.85 不拆）
            # post_fix：motion「N-M三」归一 / prompt 句读终端不变量 / 打斗反馈水体选词
        ],
        "validation_checks": [],
        "tests": [
            "tests/step01_script_gen/constraints/test_kf_timeline_normalize.py",
            "tests/step01_script_gen/constraints/test_structure_fixes_0912.py",
            "tests/step01_script_gen/test_postfix_roots_0912.py",
        ],
        # 删除记录：legacy-only 行为 kneel_beside_victim / frame_layout_consistency /
        # hook_env_anchor / hook_witness_not_killer 随家族移除（v6.42 起未在默认管线
        # 运行；git 历史可找回）。约束共享常量 16 个迁至 validation 顶部保留。
    },

    # ========================================================================
    # v6.68 — per-model prompt 剖面（2026-09-12）
    # ========================================================================
    "v6.68": {
        "name": "prompt/system 跟实际模型走（MODEL_PROMPT_PROFILES）：根治候选结构性零高潮",
        "date": "2026-09-12",
        "prompt_sections": [
            "prompts.py:MODEL_PROMPT_PROFILES",
        ],
        "constraints": [],
        "validation_checks": [],
        "tests": [
            "tests/step01_script_gen/test_model_prompt_profile.py",
        ],
    },

    # ========================================================================
    # v6.61 — provider-neutral step01 framework
    # ========================================================================
    "v6.61": {
        "name": "共享生成/修复/GAP/硬修复框架：provider 只影响 prompt/system",
        "date": "2026-09-06",
        "prompt_sections": [],
        "constraints": [
            "narration_subtitle_consistency",
            "core_keyword_coverage",
            "role_anchor",
            "opening_empty_shot_insert",
            "climax_distribution",
            "overshoulder_consistency",
            "frame_spec_repair",
            "mandatory_biz_fields",
        ],
        "validation_checks": [],
        "tests": [
            "tests/step01_script_gen/constraints",
            "tests/step01_script_gen",
            "tests/tools/test_bench_text_provider_ab.py",
        ],
    },

    # ========================================================================
    # v6.60 — historical provider-specific chain, superseded by v6.61
    # ========================================================================
    "v6.60": {
        "name": "doubao 专用链：DOUBAO_SYSTEM_PROMPT + 修复轮/GAP 重生 prompt + 7 个 hard_fix Constraint",
        "date": "2026-09-05",
        "prompt_sections": [
            "prompts/doubao_system.md",
        ],
        "constraints": [
            # 历史专用约束已由 v6.61 的全局共享约束承接
            "narration_subtitle_consistency",
            "core_keyword_coverage",
            "role_anchor",
            "opening_empty_shot_insert",
            "climax_distribution",
            "overshoulder_consistency",
            "frame_spec_repair",
        ],
        "validation_checks": [],
        "tests": [
            "tests/step01_script_gen/constraints",
        ],
        # 非 Constraint 配套模块（构造器/规则行常量）：
        # 该历史链已删除，功能由 v6.61 全局约束承接。
    },

    # ========================================================================
    # v6.59 — shell 环境变量覆盖 .env.content 的 PROVIDER 检测（仅 WARN）
    # ========================================================================
    "v6.59": {
        "name": "PROVIDER shell 覆盖检测（shared/env_guard，不阻断）",
        "date": "2026-09-04",
        "constraints": [],   # 非 Constraint：main.py 模块级调 env_guard.warn_shell_overrides
        "validation_checks": [],
        "tests": [],
    },

    # ========================================================================
    # v6.58 — camera_movement 字段接入 3 个视频 provider（Phase C-1）
    #       + motion_desc 时间分段解析与 prompt 注入（Phase C-2）
    #       + A7 拆分镜内容重复检测 + B9 拆分镜 frame_layouts.kfs 修复
    # ========================================================================
    "v6.58": {
        "name": "camera_movement provider 接入 + motion 分段 + 拆分镜重复/kfs 修复",
        "date": "2026-09-03",
        "prompt_sections": [
            # 不改 prompt，只改消费侧
        ],
        "constraints": [
            # 复用 v6.47 camera_movement_fill 写入的结构化字段
            # 复用 v6.42 kf_time_window_alignment 的 _parse_motion_time_segments
            "split_content_similarity",  # A7: 拆分镜内容重复检测（WARN）
        ],
        "validation_checks": [],
        "style_prefix": [],
        "tests": [
            "tests/_video_gen/test_camera_movement_provider.py",     # 18 测
            "tests/shared/test_motion_segments.py",                 # 19 测
            "tests/step01_script_gen/constraints/test_split_content_similarity.py",  # 17 测
            "tests/step01_script_gen/test_split_frame_layouts_kfs.py",  # 6 测
        ],
    },

    # ========================================================================
    # v6.57 — frame_spec / per-frame 出镜表事实源
    # ========================================================================
    "v6.57": {
        "name": "per-frame spec 事实源（frame_layouts.kfs 与 keyframes 一一对应）",
        "date": "2026-08-30",
        "prompt_sections": [
            "prompts/main.md:frame_layouts 字段",
        ],
        "constraints": [
            "frame_spec_consistency",  # S: per-frame spec 必填+完整性+desc 一致性
        ],
        "validation_checks": [
            # validation.py 侧的旧检查已逐步迁移到 constraint
        ],
        "tests": [
            "tests/shared/test_frame_spec.py",
        ],
    },

    # ========================================================================
    # v6.51 — 时序三铁律 + 跨镜衔接 + 三态冗余 + 结构化字段
    # ========================================================================
    "v6.51": {
        "name": "时序三铁律（FF前一刻 / LF留接口 / motion分段写）+ 跨镜衔接 + 拆分镜kf派生",
        "date": "2026-09-02",
        "prompt_sections": [
            "prompts/main.md:v6.51 时序三铁律",
            "prompts/main.md:v6.51.1 单镜内字段一致性",
            "prompts/hook.md",  # 整文件同步适用
        ],
        "constraints": [
            # FF 前一刻
            "ff_motion_overlap",        # F3 增强：water_break 变体 + WB姿态→水下姿态
            "ff_lf_temporal",           # F10 ff/lf 时序协议
            "lf_midstate",              # Q: lf 动作进行态→终态
            # 跨镜衔接
            "cross_shot_continuity",    # K2: 场景转场 + 状态跳变 + narr重复 + LF同态
            "transition_keywords",      # W4: 转场关键词（旧约束，与 K2 协同）
            # 三态冗余
            "ff_lf_redundancy",         # K3: FF/LF/Motion 内容重复检测
            # 结构化字段
            "camera_movement_fill",     # J3 增强：同步填 camera_movement 字段
            "prompt_sanitize",          # J4: （画中X）/（AI生成）清理
            "prop_prompt_consistency",  # L2: 道具别名→标准名
        ],
        "style_prefix": [
            "normalize_ff_desc",            # 主入口
            "detect_ff_multi_action",       # 检测
            "_WB_POSE_TO_UNDERWATER",       # v6.51 新增：出水后姿态→水下姿态
            "_strip_ff_time_advance",
            "_split_ff_clauses",
            "_preactionize_ff_motion",
            "strip_spatial_anchors",        # shared/utils/text.py
            "strip_debug_markers",
        ],
        "timecode_changes": [
            "拆分镜 keyframes 派生（_split_narration_shot：从原镜截取后半段kf）",
        ],
        "validation_checks": [
            "_check_ff_lf_desc_completeness",
            "_check_first_frame_timing",
            "_check_transition_keywords",
            "_check_keyframe_quality",
        ],
    },

    # ========================================================================
    # v6.50 — ff/lf 时序协议（本镜有攻击时 ff 禁结束态）
    # ========================================================================
    "v6.50": {
        "name": "ff/lf 时序协议：本镜有攻击时 ff 禁结束态",
        "date": "2026-08-28",
        "constraints": [
            "ff_lf_temporal",           # F10
        ],
    },

    # ========================================================================
    # v6.49 — 红果 60-90s 标准 + 8 项硬修复（F3-F10）
    # ========================================================================
    "v6.49": {
        "name": "红果标准（60-90s/3-8镜/≤3场景/≤4人物）+ F3-F10 硬修复",
        "date": "2026-08-28",
        "prompt_sections": [
            "prompts/main.md:v6.49 红果场景/人物硬约束",
        ],
        "constraints": [
            "redguo_scene_char_limit",   # F5: 场景/人物硬上限（HARD）
            "runtime_extend",            # W6: 时长拉伸（HARD）
            "short_shot_merge",          # F9: 短镜合并（HARD）
            "climax_priority",           # F8: 高潮优先（0镜日常）
            "ff_motion_overlap",         # F3: ff 动作重叠
            "kf_blood_monotonic",        # F2: kf 血状态单调
            "kf_pose_anchor",            # F4: kf 姿态+位置锚定
            "post_combat_blank",         # F6: 打斗战后空镜收尾
            "restrained_emotion_chain",  # F7: 克制情绪时间链
        ],
        "timecode_changes": [
            "W6 时长拉伸（runtime_extend：每镜均匀+blank_hold）",
            "短镜合并（short_shot_merge：<2s 合并到相邻镜）",
        ],
        "tests": [
            "tests/step01_script_gen/test_split_shot_differentiation.py",
        ],
    },

    # ========================================================================
    # v6.47 — 运镜缺失补填
    # ========================================================================
    "v6.47": {
        "name": "运镜缺失补填（camera_movement_fill）",
        "date": "2026-08-27",
        "constraints": [
            "camera_movement_fill",      # J3: 无运镜语言→按角色补/兜底固定机位
        ],
    },

    # ========================================================================
    # v6.45 — kf 弱模板占位
    # ========================================================================
    "v6.45": {
        "name": "kf 弱模板占位修复（W16）",
        "date": "2026-08-26",
        "constraints": [
            "kf_weak_template",          # W16: 弱模板承袭 ff/lf + step02a 双兜底
        ],
    },

    # ========================================================================
    # v6.43 — prompt 强化（4+1 规则）+ kf 单调性
    # ========================================================================
    "v6.43": {
        "name": "v6.43 prompt 强化（4+1 规则：小船/抬轿/重叠/抽象/威胁源朝向）",
        "date": "2026-08-23",
        "constraints": [
            "v6_43_prompt_enforcement",  # 5 条规则集中实现
            "kf_monotonic",              # F1: kf t_sec 单调性
        ],
        "validation_checks": [
            # v6.43 5 条规则的检测函数散在 validation.py 各节
        ],
        "tests": [],
    },

    # ========================================================================
    # v6.42 — 基础约束体系（从旧 _hard_fix_* 迁移到 Constraint 框架）
    # ========================================================================
    "v6.42": {
        "name": "基础约束体系（42+ 个约束从 _hard_fix_* 迁移到 Constraint 框架）",
        "date": "2026-08-22",
        "constraints": [
            # Phase 1 核心（A-D）——名字与 ALL_CONSTRAINTS 实际 name 对齐（2026-09-06 核对）
            "fatal_wound_continuity",   # A: 致命伤持续（旧称 injury_continuity）
            "gaze_direction",           # B: 视线方向
            "prop_spatial_anchor",      # C: 道具空间方位（旧称 prop_position）
            "kf_temporal_consistency",  # D: kf 时序矛盾（旧称 kf_pre_post_conflict）
            # Phase 2（E-K）
            "kf_time_window_alignment", # E: kf 时段缺段
            "kf_t_sec_bounds",          # F: kf t_sec 越界
            "kf_monotonic",             # F1: kf 单调性
            "hook_duration",            # R: hook 时长下限
            "opening_hook",             # K: 开篇冲突
            # Phase 3 更多（W开头+其他）
            "climax_distribution",      # W7: 高潮分布
            "framing_jump",             # W10: 景别跳跃
            "axis_jump",                # W11: 轴线越轴
            "punch_density",            # L: 爆点密度
            "bridge_words",             # W13: 衔接词
            "camera_variety",           # J2: 运镜多样性
            "framing_diversity_v2",     # I2: 景别多样性
            "lf_desc_length",           # M: lf 字数下限
            "lf_midstate",              # Q: lf 进行态
            "multi_char_layout_depth",  # W5: 多人站位纵深
            "narration_subtitle_consistency",  # H: narration/subtitle 一致
            "threat_gaze_coupling",     # G: 威胁源-视线耦合
            "combat_impact",            # W3: 打击反馈
            "char_layout_sync",         # N: char_layout 同步
            "facing_to_gaze",           # T2: 面向→凝望
            "boat_width",               # T1: 船体宽度
            "boat_crop_strip",          # V: 船体裁切（旧称 boat_crop）
            "prompt_repetition",        # P: 复读机
            "platform_compliance",      # O: 首镜 AI 生成标注
            # conflict_detector：独立检测器（engine.detect_and_log 直调，非 Constraint 子类）
        ],
        "validation_checks": [
            # 旧 validation.py 中 50+ 个 _check_* 函数（逐步迁移到 Constraint）
            "_check_injury_continuity",
            "_check_gaze_direction",
            "_check_prop_position",
            "_check_kf_pre_post_conflict",
            "_check_kf_time_window_alignment",
            "_check_strong_emotion_chain",
            "_check_threat_gaze_coupling",
            "_check_combat_actions",
            "_check_multi_char_layout",
            # ... 更多见 validation.py
        ],
    },
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _version_key(v: str) -> tuple[int, int]:
    """版本号数值排序键（"v6.100" > "v6.99"）——字符串字典序在 v6.100 边界
    翻车（"v6.100" < "v6.70"），latest/get_rule_version 必须走数值序。"""
    m = re.fullmatch(r"v(\d+)\.(\d+)", v)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def get_rule_version(version: str) -> RuleEntry | None:
    """获取指定版本的规则条目（支持不精确匹配：v6.5 匹配 v6.5x 最新版）."""
    if version in RULES_INDEX:
        return RULES_INDEX[version]
    # 模糊匹配：v6.5 匹配 v6.5x 中最新的
    candidates = [k for k in RULES_INDEX if k.startswith(version)]
    if not candidates:
        return None
    return RULES_INDEX[max(candidates, key=_version_key)]


def latest_version() -> str:
    """返回最新版本号（数值序——09-13 修：字符串序在 v6.100 处翻车）."""
    return max(RULES_INDEX.keys(), key=_version_key)


def all_constraint_names() -> set[str]:
    """返回所有版本涉及的 constraint name 集合（去重）."""
    names: set[str] = set()
    for entry in RULES_INDEX.values():
        names.update(entry.get("constraints", []))
    return names


__all__ = [
    "RULES_INDEX",
    "RuleEntry",
    "get_rule_version",
    "latest_version",
    "all_constraint_names",
]
