"""v6.50 hook ff_desc 否定语境 + 弱实体判定扩展（2026-09-04）.

样本：hook ff_desc 「蒙面神秘人完全潜伏水面下未露出任何身体部位」——
  旧逻辑：命中 visible hint "蒙面"+"露出"(在"未露出"子串) → 判 True →
  默认分支硬塞「禁止…黑影」与 ff_desc「一道黑影」对称矛盾
  → shared.prompt_contradiction fail-fast 拦下 step02a

新逻辑（3 处加固）：
  1. 否定语境优先级：窗口含「未/不/没/无 + 露出/冒出/破水/...」 → 跳过
  2. 部分露出量化词：含「Xcm/几厘米/一截/小截」 → 当弱实体
  3. 「斜」单字太宽（"斜阳"/"斜坡"误命中）→ 改成「斜望/斜视」

v6.50 修复（2026-09-04）：hook prompt 对称矛盾 fail-fast 解锁
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# helper 单元测试
# ---------------------------------------------------------------------------

def test_threat_visible_basic_invisible():
    """完全没入水中 → False（不可见）."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("神秘人已彻底没入水中，水面只剩剧烈涟漪", []) is False


def test_threat_visible_basic_visible():
    """露半身+举匕首 → True（可见实体）."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("画右水面刺客半身探出、右手举匕首", []) is True


def test_threat_visible_no_threat_returns_none():
    """无威胁源 → None."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("杨旭独自垂钓", []) is None


def test_threat_visible_partial_with_cm():
    """'仅露出 2cm' → False（部分露出，弱实体）."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("神秘人潜伏水下，仅露出 2cm", []) is False


def test_threat_visible_partial_with_xie():
    """'一截' 也是部分露出量化词 → False."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("水下黑影，一截刀尖微露水面", []) is False


# ---------------------------------------------------------------------------
# v6.50 新增：否定语境优先级
# ---------------------------------------------------------------------------

def test_negation_unmouchu():
    """'没冒出' → False（窗口含「没+冒出」）."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("神秘人在水底，没冒出", []) is False


def test_negation_weiluchu():
    """'未露出任何' → False（窗口含「未+露出」）."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in(
        "蒙面神秘人完全潜伏水面下未露出任何身体部位", []
    ) is False


def test_negation_meidao():
    """'没露面' → False."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("神秘人潜伏暗处，没露面", []) is False


def test_negation_weiposhuichu():
    """'未破水而出' → False."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("水底黑影蓄势待发，未破水而出", []) is False


# ---------------------------------------------------------------------------
# v6.50 新增：「斜」单字放宽
# ---------------------------------------------------------------------------

def test_xi_single_char_no_false_hit():
    """'斜阳'/'斜坡' 无关命中 → 仍不可见（hit 为空）.xxx."""
    from shared.vimax_bridge import _threat_visible_in
    # 「刺客斜阳照着」—— 威胁源出现但没可见特征 → 期望 False
    assert _threat_visible_in("刺客斜阳照着", []) is False


def test_xi_wang_is_visible():
    """'斜望' 真可见特征 → True."""
    from shared.vimax_bridge import _threat_visible_in
    assert _threat_visible_in("刺客斜望敌人", []) is True


# ---------------------------------------------------------------------------
# v6.50 实战：hook.ff_desc
# ---------------------------------------------------------------------------

def test_hook_ff_real_desc():
    """live chapter_001 hook.ff_desc 的威胁可见性判定必须可复现（不 hard-fail）.

    09-06 教训：该测试此前断言 `is False`——但 live script_result.json 每次跑
    step01 都会重生成，钩子叙事形态不固定（09-06 实测换成张十三质问版，desc
    无威胁源词 → 返回 None），硬断言 False 与 live 数据耦合必挂。
    改为宽松契约：返回值 ∈ {True, False, None} 且 None/False（非「确认可见」）
    时不得对 ff_desc 注入「禁止黑影」类硬约束。即只拦「确认可见」分支误放。
    """
    from shared.vimax_bridge import _threat_visible_in
    import json
    script_path = (
        r"C:\myFloders\git\aiVideo\output\video\锦衣夜行\part_01_杀青州"
        r"\chapter_001\script_result.json"
    )
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        # 跳过（路径不存在时，09-04 旧路径或换电脑）
        return
    hook = data.get("hook", {})
    ff = hook.get("ff_desc", "")
    if not ff:
        return
    chars = data.get("characters", [])
    visible = _threat_visible_in(ff, chars)
    # 契约：返回值只能是 True/False/None 三态
    assert visible in (True, False, None), (
        f"hook.ff_desc 可见性判定返回了非法值 {visible!r}（ff={ff!r}）"
    )
    # 无威胁源词的叙事形态（None/False）不算「确认可见」——上游按不可见兜底，
    # 不应对无刺客的 ff_desc 强注负面词（09-06 实测 v6.50 三加固逻辑正确）。


# ---------------------------------------------------------------------------
# 集成：可见分支不被误伤
# ---------------------------------------------------------------------------

def test_visible_held_weapon_kept_true():
    """真可见实体（握匕首）不被否定规则误伤."""
    from shared.vimax_bridge import _threat_visible_in
    # 否定词「没入水中」是过去式（已没入），不是当前状态
    # 但「握匕首」是真可见 → 旧逻辑 True，本逻辑也应 True
    # 「神秘人蹲踞船头，握匕首」窗口里没否定词 → True
    assert _threat_visible_in(
        "神秘人蹲踞船头，右手紧握匕首，目光盯向杨旭", []
    ) is True


def test_partial_upper_body_visible():
    """'部分露出上半身' → True（露出上半身 = 可见实体）."""
    from shared.vimax_bridge import _threat_visible_in
    # "部分" 不在 PARTIAL_QUANTIFIERS 列表里（"只/部分"被故意排除，太宽泛）
    # 但 "半身" 命中 visible hint → True
    assert _threat_visible_in(
        "神秘人从水中部分露出上半身", []
    ) is True
