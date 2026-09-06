# -*- coding: utf-8 -*-
"""v6.61 hook shot0 六根因修复回归测试.

09-05 锦衣夜行 chapter_001 hook（shot0）实抽 6 类缺陷 → 根因定位：
  根因1  _extract_char_layout 合并 scan 威胁门控 → lf 误判威胁源可见，
         锚定块逼模型画站立刺客 → 迁移 spec 驱动（frame_kind 参数）
  根因2  prompt 约束块「威胁源黑影不计入实体人数」vs QC 名单含威胁源
         → 规格互斥，任何输出必 critical → QC 期望人数同源剔除
  根因3  hook 无 char_layout → kf 锚定退化 scan 提取三重矛盾；
         kf 恒定「环境构图与首帧保持一致」尾巴与 kfs[i] 站位冲突
  根因4  InputTextSensitiveContentDetected 输入侧 400 → 跳帧留陈旧图
         → provider 清洗重试
  根因5  doodle_refine provider 落盘 fallback "kf" → shot_N_kf.png
         冒充关键帧槽位 → 显式 save_path + provider fallback 改 "misc"
  根因6  NO_ANIMAL_FEATURES_GUARD 负向列举（无兽耳/无角）priming
         → kf1 刺客猫耳 → 改正向人类形态锚
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.vimax_bridge import (
    _extract_char_layout,
    _inject_frame_spec,
    _doodle_refine_first_frame,
)
from shared.image_qc import _threat_shadow_not_counted, _expected_persons
from step01_script_gen.prompt_semantic import NO_ANIMAL_FEATURES_GUARD

# ── 共用夹具：09-05 hook 真实结构（frame_layouts，无 char_layout）──────

_CHARS = [{"identifier": "杨旭"}, {"identifier": "听香"}, {"identifier": "刺客"}]

_FL = {
    "ff": [
        {"name": "杨旭", "on_screen": True, "position": "画中偏左，船头",
         "pose": "仰卧", "gaze_target": "画上星空", "props": ["酒壶", "钓杆"]},
        {"name": "听香", "on_screen": True, "position": "画左，杨旭左侧跪坐",
         "pose": "跪坐", "gaze_target": "画右水面", "props": []},
        {"name": "刺客", "on_screen": True, "position": "画右水下",
         "pose": "潜伏水下", "gaze_target": "画中杨旭", "props": ["匕首"]},
    ],
    "lf": [
        {"name": "杨旭", "on_screen": True, "position": "画中偏左，船头",
         "pose": "仰倒船头", "gaze_target": "画上星天", "props": []},
        {"name": "听香", "on_screen": True, "position": "画左，向后仰坐",
         "pose": "仰坐", "gaze_target": "画中杨旭", "props": []},
        {"name": "刺客", "on_screen": False, "position": "无",
         "pose": "已没入水中", "gaze_target": "无", "props": []},
    ],
    "kfs": [
        [
            {"name": "刺客", "on_screen": True, "position": "画右半身破水",
             "pose": "破水跃出，右手持匕", "gaze_target": "画中杨旭", "props": ["匕首"]},
            {"name": "杨旭", "on_screen": True, "position": "画中偏左，船头",
             "pose": "仰卧", "gaze_target": "画右水花", "props": ["酒壶"]},
            {"name": "听香", "on_screen": True, "position": "画左，跪坐",
             "pose": "仰卧", "gaze_target": "画右水花", "props": []},
        ],
    ],
}


def _hook_shot() -> dict:
    return {
        "index": 0,
        "ff_desc": "冷蓝月光下舴艋舟浮于荷花丛，杨旭白袍仰卧船头，"
                   "听香跪坐其侧，船右水面下只有一片模糊的暗色阴影缓慢漂移",
        "lf_desc": "水面余波扩展，神秘人已彻底没入消失；杨旭仰倒船头；听香向后仰坐",
        "motion_desc": "神秘人猛然破开水面，右手匕首从背后刺向杨旭后心；一击即退没入水中",
        "frame_layouts": dict(_FL),
        # 故意不写 char_layout——hook 真实状态
    }


# ── 根因1：lf spec 驱动，威胁源 on_screen=false 不进锚定块 ──────────

def test_lf_anchor_excludes_submerged_threat():
    """lf：刺客 on_screen=false（已没入）→ 锚定块无刺客、无朝向句."""
    shot = _hook_shot()
    block = _extract_char_layout(shot, _CHARS,
                                 role_scan_text=shot["lf_desc"],
                                 frame_kind="last_frame")
    assert block, "lf 应产出锚定块（杨旭+听香在场）"
    assert "神秘人" not in block
    assert "刺客" not in block
    # 旧根因：scan 含 motion_desc「刺向」→ 威胁朝向兜底句被误注入
    assert "攻击目标" not in block


def test_lf_anchor_contains_onboard_chars_with_spec_positions():
    """lf：杨旭/听香站位来自 spec（画中偏左，船头 / 画左，向后仰坐）."""
    shot = _hook_shot()
    block = _extract_char_layout(shot, _CHARS,
                                 role_scan_text=shot["lf_desc"],
                                 frame_kind="last_frame")
    assert "画中偏左" in block or "船头" in block
    assert "向后仰坐" in block


def test_ff_anchor_spec_threat_visible_uses_spec_pose():
    """ff：刺客 on_screen=true 潜伏水下 → 进锚定块但用 spec pose（潜伏水下）."""
    shot = _hook_shot()
    block = _extract_char_layout(shot, _CHARS,
                                 role_scan_text=shot["ff_desc"],
                                 water_state=False,
                                 frame_kind="first_frame")
    assert block
    # spec pose 直进锚定（不再从 ff_desc scan 猜「探出」类错误姿态）
    assert "潜伏" in block


def test_kf_anchor_uses_per_frame_spec():
    """kf0：spec pose「破水跃出」进锚定块，ff_desc 的「潜伏水下」不再污染."""
    shot = _hook_shot()
    kf_scan = " ".join([shot["ff_desc"], shot["lf_desc"], shot["motion_desc"],
                        "刺客半身破水而出，水花飞溅"])
    block = _extract_char_layout(shot, _CHARS, role_scan_text=kf_scan,
                                 frame_kind="keyframe", kf_index=0)
    assert block
    assert "破水跃出" in block or "破水" in block


def test_legacy_path_without_frame_kind_unchanged():
    """不传 frame_kind → 走旧合并 scan 启发式（老章兼容不回归）."""
    shot = _hook_shot()
    del shot["frame_layouts"]  # 老章结构
    shot["char_layout"] = [
        {"name": "杨旭", "position": "画右", "facing": "仰卧"},
        {"name": "听香", "position": "画右中景", "facing": "跪坐"},
    ]
    block = _extract_char_layout(shot, _CHARS, role_scan_text=shot["ff_desc"])
    assert "杨旭" in block
    assert "听香" in block


# ── 根因2：QC 期望人数与 prompt 约束块同源 ─────────────────────────

def test_qc_expected_persons_lf_excludes_offscreen_threat():
    """lf spec：刺客 on_screen=false → 期望人数 2（spec 直读，本就正确）."""
    shot = _hook_shot()
    got = _expected_persons(shot, "last_frame", _CHARS)
    assert got == 2


def test_threat_shadow_not_counted_ff():
    """ff：威胁源以「潜伏水下/暗影」非实体呈现（_threat_visible_in=False）
    → prompt 约束块写「黑影不计入实体人数」，QC 应同步剔除."""
    shot = _hook_shot()
    # 真实 hook ff_desc 首句含「刺客潜伏水下」（合规替换前别名刺客仍在
    # ff_desc 里）；夹具补齐使 _threat_visible_in 命中非实体语境
    shot["ff_desc"] = "云河镇照月湾小舟，刺客潜伏水下，" + shot["ff_desc"]
    assert _threat_shadow_not_counted(shot, "first_frame", _CHARS) is True


def test_threat_shadow_not_counted_negative_kf():
    """kf0：威胁源破水可见 → 不剔除（_threat_visible_in=True）。"""
    shot = _hook_shot()
    # frame_kind=keyframe 不适用（该函数只消费 ff/lf 叙事 desc），
    # 用 first_frame 可见样本验证 False 分支
    vis_shot = _hook_shot()
    vis_shot["ff_desc"] = "神秘人半身探出水面，右手举匕首直指杨旭"
    assert _threat_shadow_not_counted(vis_shot, "first_frame", _CHARS) is False


# ── 根因3 尾巴：kf「环境构图与首帧保持一致」恒定尾巴已改 ───────────

def test_kf_desc_full_no_layout_lock_tail():
    """kf prompt 尾巴改为环境延续层（人物布局以本帧描述为准）.

    旧尾巴「环境构图与首帧保持一致…镜头机位…不变」与 kfs[i] 差异化站位冲突；
    09-05 hook kf 全部站位错的一环。此测试锁住新文案存在 + 旧文案消失。
    """
    import inspect
    from shared import vimax_bridge
    src = inspect.getsource(vimax_bridge._gen_keyframes_async)
    assert "环境延续首帧" in src
    assert "环境构图与首帧保持一致" not in src
    assert "人物站位/动作以本帧描述为准" in src


# ── 根因5：doodle_refine 显式 save_path（代码级锁定）────────────────

def test_doodle_refine_passes_save_path():
    """_doodle_refine_first_frame 必须传 save_path（provider fallback
    "kf" 曾把 doodle 中间产物存成 shot_N_kf.png 冒充关键帧）."""
    import inspect
    src = inspect.getsource(_doodle_refine_first_frame)
    assert "save_path=str(output_path)" in src


def test_provider_fallback_name_is_misc():
    """5 个 provider 未知帧类 fallback 命名 = misc（不再落 kf 槽位）."""
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent.parent
    for name in ("agent_plan", "volcengine", "agnes", "bailian", "minimax"):
        src = (root / "_image_gen" / f"{name}_provider.py").read_text(encoding="utf-8")
        assert '.get(frame_label, "misc")' in src, name
        assert '.get(frame_label, "kf")' not in src, name


# ── 根因4：agent_plan InputTextSensitiveContentDetected 清洗重试 ────

def test_agent_plan_input_sensitive_retry_exists():
    """agent_plan provider 400 InputTextSensitiveContentDetected → 两级清洗重试."""
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent.parent
    src = (root / "_image_gen" / "agent_plan_provider.py").read_text(encoding="utf-8")
    assert "InputTextSensitiveContentDetected" in src
    assert "from shared.content_safety import sanitize" in src
    assert "soften_input_text" in src
    # 重试提交：payload["prompt"] = sanitized 后二次 post
    assert 'payload["prompt"] = sanitized' in src


def test_soften_input_text_two_level():
    """输入侧降级表：全表 sanitize 无效的持械逼临组合，soften 必须有变化."""
    from shared.content_safety import sanitize, soften_input_text
    probe = "神秘人右臂下压，匕首柄抵近杨旭，杨旭身躯猛地后仰"
    assert sanitize(probe) == probe          # L1 无效（已清洗过）
    softened = soften_input_text(probe)      # L2 必须变化
    assert softened != probe
    assert "抵近" not in softened
    # 链式安全：值不含其它 key
    from shared.content_safety import INPUT_SENSITIVE_SOFTEN
    keys = set(INPUT_SENSITIVE_SOFTEN.keys())
    for k, v in INPUT_SENSITIVE_SOFTEN.items():
        for k2 in keys:
            if k2 != k:
                assert k2 not in v, (k, v, k2)


# ── 根因6：兽耳护栏负向列举 → 正向人类形态锚 ────────────────────────

def test_no_animal_guard_positive_wording():
    """护栏不再罗列「无兽耳/无角/无犄角」负向词（Seedream 提到即渲染）."""
    for w in ("无兽耳", "无角", "无犄角", "无鳞", "无羽毛"):
        assert w not in NO_ANIMAL_FEATURES_GUARD
    # 正向锚保留人类形态语义
    assert "人类" in NO_ANIMAL_FEATURES_GUARD


def test_build_char_anchor_appends_positive_guard():
    """_build_char_anchor 触发动物关键词时追加的护栏为新正向文案."""
    from shared.vimax_bridge import _build_char_anchor
    anchor = _build_char_anchor("刺客", "细长丹凤眼，眼神清冷，虎目", "蒙面，身着夜行衣")
    assert "无兽耳" not in anchor
    assert "人类圆形耳廓" in anchor


def test_build_char_anchor_strips_llm_beast_negation():
    """LLM static_features 自带「无兽化特征」负向声明 → 入口剥掉（priming 根因）."""
    from shared.vimax_bridge import _build_char_anchor
    anchor = _build_char_anchor(
        "刺客", "细长丹凤眼，蒙面，仅露双眼，无兽化特征", "湿透的黑色夜行衣")
    assert "无兽化特征" not in anchor
    # 「无任何兽化特征」变体同样剥掉
    anchor2 = _build_char_anchor(
        "听香", "鹅蛋脸，无任何兽化特征，仅人族发簪步摇", "身着春衫")
    assert "无任何兽化特征" not in anchor2
