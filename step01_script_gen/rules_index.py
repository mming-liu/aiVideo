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
            "prompts/main.md:v6.51 时序三铁律（第22-25行）",
            "prompts/hook.md:（同步适用）",
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
            "prompts/main.md:v6.49 红果场景/人物硬约束（第16-20行）",
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
            # Phase 1 核心（A-D）
            "injury_continuity",        # A: 致命伤持续
            "gaze_direction",           # B: 视线方向
            "prop_position",            # C: 道具空间方位
            "kf_pre_post_conflict",     # D: kf 时序矛盾
            # Phase 2（E-K）
            "kf_time_window_alignment", # E: kf 时段缺段
            "kf_t_sec_bounds",          # F: kf t_sec 越界
            "kf_monotonic",             # F1: kf 单调性
            "kf_time_window_alignment", # E
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
            "boat_crop",                # V: 船体裁切
            "prompt_repetition",        # P: 复读机
            "platform_compliance",      # O: 首镜 AI 生成标注
            "conflict_detector",        # 矛盾检测器
            "platform_compliance",
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

def get_rule_version(version: str) -> RuleEntry | None:
    """获取指定版本的规则条目（支持不精确匹配：v6.5 匹配 v6.5x 最新版）."""
    if version in RULES_INDEX:
        return RULES_INDEX[version]
    # 模糊匹配：v6.5 匹配 v6.5x 中最新的
    candidates = [k for k in RULES_INDEX if k.startswith(version)]
    if not candidates:
        return None
    return RULES_INDEX[sorted(candidates)[-1]]


def latest_version() -> str:
    """返回最新版本号."""
    return sorted(RULES_INDEX.keys())[-1]


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
