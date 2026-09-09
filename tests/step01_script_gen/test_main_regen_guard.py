"""GAP 重试回退保护（v6.47，08-27）单元测试.

覆盖：
  1. _retry_raw_duration：原始总时长（不含钩子，拉伸前的值）
  2. _count_ungrounded_narrations：台词未在原文找到的镜头数
  3. _is_retry_degraded：时长塌缩 / 台词失锚变多 → True；健康重试 → False
  4. _build_regen_iron_rules：铁律块含关键非协商规则
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.main import (
    _is_retry_degraded, _retry_raw_duration, _count_ungrounded_narrations,
)
from step01_script_gen.prompts import _build_regen_iron_rules

_NOVEL = "杨旭在船上钓鱼。听香在舱中做饭。刺客从水下跃出。"


def test_retry_raw_duration_excludes_hook():
    """原始总时长只算 shots，不含 hook（钩子时长另算）."""
    script = {
        "shots": [{"duration_sec": 5.0}, {"duration_sec": 6.0}],
        "hook": {"duration_sec": 20.0},
    }
    assert _retry_raw_duration(script) == 11.0


def test_retry_raw_duration_handles_missing_field():
    """duration_sec 缺失时回退 duration / 0."""
    script = {"shots": [{"duration_sec": 5.0}, {"duration": 3.0}, {"prompt": "x"}]}
    assert _retry_raw_duration(script) == 8.0


def test_duration_collapse_detected():
    """上一版健康(60s) + 重试版塌缩(11s) → 退化，应回退."""
    prev = {"shots": [{"duration_sec": 7.5} for _ in range(8)]}  # 60s
    new = {"shots": [{"duration_sec": 1.0} for _ in range(11)]}  # 11s
    assert _is_retry_degraded(prev, new, "") is True


def test_healthy_retry_not_degraded():
    """重试版时长健康(63s) → 不退化."""
    prev = {"shots": [{"duration_sec": 7.5} for _ in range(8)]}  # 60s
    new = {"shots": [{"duration_sec": 7.0} for _ in range(9)]}  # 63s
    assert _is_retry_degraded(prev, new, "") is False


def test_both_short_not_degraded():
    """上一版也短(<40s)时不算退化（本来就短，不是重试写塌）."""
    prev = {"shots": [{"duration_sec": 5.0} for _ in range(6)]}  # 30s
    new = {"shots": [{"duration_sec": 3.0} for _ in range(8)]}  # 24s
    assert _is_retry_degraded(prev, new, "") is False


def test_ungrounded_dialogue_regression_detected():
    """重试版台词失锚数 > 上一版且 ≥2 条 → 退化（原文幻觉）."""
    prev = {"shots": [{"narration_segment": "杨旭在船上钓鱼"}]}  # 锚定 0 失锚
    new = {"shots": [
        {"narration_segment": "凶手是谁？如何击杀了公子"},
        {"narration_segment": "那人一下子从水里跳出来，就击杀了公子"},
    ]}
    assert _count_ungrounded_narrations(prev, _NOVEL) == 0
    assert _count_ungrounded_narrations(new, _NOVEL) == 2
    assert _is_retry_degraded(prev, new, _NOVEL) is True


def test_no_grounding_regression_when_equal():
    """重试版失锚数与上一版相同 → 不判退化（无新增幻觉）."""
    prev = {"shots": [{"narration_segment": "原文没有的台词"}]}  # 1 失锚
    new = {"shots": [{"narration_segment": "另一句原文没有的台词"}]}  # 1 失锚
    assert _is_retry_degraded(prev, new, _NOVEL) is False


def test_iron_rules_contains_key_constraints():
    """铁律块必须重申总时长/台词锚定/合规/kf 四类非协商规则."""
    iron = _build_regen_iron_rules()
    assert "SUM(duration_sec)" in iron        # 总时长
    assert "逐字来自原文" in iron              # 台词锚定
    assert "击杀" in iron                     # 合规
    assert "kf.t_sec" in iron                 # kf 铁律
    assert "拒收" in iron                     # 措辞 = 拒收标准


# ============================================================================
# v6.50（08-28）：文本中途截断 → 回退保护第三信号
# ============================================================================

from step01_script_gen.main import _count_mid_clause_tails  # noqa: E402


def test_mid_clause_tails_counts_truncated_fields():
    """「镜头推近至」「惊觉对」这类残句 → 计数（实测 08-28 落盘样本）."""
    script = {"shots": [
        {"prompt": "近景，水花四溅，月光下刀光冷冽，镜头推近至", "ff_desc": "完整的句子。"},
        {"prompt": "危机一触即发，暗流涌动，惊觉对", "ff_desc": "正常描述，结尾句号。"},
    ]}
    assert _count_mid_clause_tails(script) == 2


def test_mid_clause_tails_ignores_healthy_fields():
    """终结标点收尾 → 0；短字段（<10 字）不计."""
    script = {"shots": [
        {"prompt": "完整的镜头描述。", "ff_desc": "短句"},
        {"prompt": "台词允许问号结尾？", "ff_desc": "感叹也合法！"},
    ]}
    assert _count_mid_clause_tails(script) == 0


def test_retry_degraded_on_truncation_regression():
    """重试版截断字段 ≥2 且比上一版多 → 判退化（保留上一版）.

    v6.62.6：判据从「缺句末标点」换成「悬挂虚词收尾」，本用例的残句样例同步改成
    真正停在虚词上的形态（原样例「…直接切断」「…半句话切断」只是没加句号，
    是**完整**句子，新判据正确地放过——旧判据把它们当截断才是误报）。
    """
    prev = {"shots": [{"prompt": "上一版完整句子以句号收尾。", "ff_desc": "也是完整的描述句子。"}]}
    new = {"shots": [
        {"prompt": "中景，船头对峙，镜头推近至", "ff_desc": "听香端着陶碗立于舱口，惊觉对",
         "lf_desc": "杨旭抬手，掌心翻转朝着舱内而"},
        {"prompt": "完整句子以句号收尾。", "ff_desc": "完整描述。"},
    ]}
    assert _is_retry_degraded(prev, new, "") is True



# ======================================================================
# v6.62.6（09-09）_retry_degrade_reason：日志文案必须点名**真正命中**的那条判据
#
# 09-08 实测日志打出「重试版质量退化（原始总时长 77s vs 上一版 66s，台词失锚
# 0 条）」——两个被打印的指标都不是触发项，真正命中的是「文本中途截断」，
# 而它从不出现在文案里 → 运维会误判护栏把更好的版本判退化了。
# ======================================================================

def _shots_with_tails(n_shots: int, dur: float, tails: list[str]) -> dict:
    """构造 shots：前 len(tails) 镜的 prompt 用给定结尾（残句 or 完整句）."""
    shots = []
    for i in range(n_shots):
        prompt = tails[i] if i < len(tails) else "杨旭立于船头远望水面。"
        shots.append({"duration_sec": dur, "prompt": prompt})
    return {"shots": shots}


def test_degrade_reason_names_mid_clause_truncation_not_duration():
    """截断触发时，文案必须说「截断」，且**不能**把未塌缩的时长说成原因."""
    from step01_script_gen.main import _retry_degrade_reason
    # 两版时长都健康（不触发判据 1）；novel_text 传空（不触发判据 2）
    prev = _shots_with_tails(8, 7.5, [])                      # 60s，0 残句
    new = _shots_with_tails(8, 8.75, ["中景，船头对峙，镜头推近至",   # 70s，2 残句
                                      "听香端着陶碗立于舱口，惊觉对"])
    reason = _retry_degrade_reason(prev, new, "")
    assert reason is not None, "≥2 个残句字段且比上一版多 → 应判退化"
    assert "截断" in reason, f"必须点名真正命中的判据，实际: {reason}"
    assert "时长塌缩" not in reason, f"未塌缩就不能说时长，实际: {reason}"


def test_degrade_reason_none_when_healthy():
    """健康重试版 → None（与 _is_retry_degraded 返回 False 一致）."""
    from step01_script_gen.main import _retry_degrade_reason
    prev = _shots_with_tails(8, 7.5, [])
    new = _shots_with_tails(8, 8.75, [])
    assert _retry_degrade_reason(prev, new, "") is None
    assert _is_retry_degraded(prev, new, "") is False


def test_is_retry_degraded_delegates_to_reason():
    """两个函数必须同源：有原因 ⇔ 判退化（防日后判据改一处漏一处）."""
    from step01_script_gen.main import _retry_degrade_reason
    prev = _shots_with_tails(8, 7.5, [])      # 60s
    new = _shots_with_tails(11, 1.0, [])      # 11s 时长塌缩
    assert _is_retry_degraded(prev, new, "") is True
    reason = _retry_degrade_reason(prev, new, "")
    assert reason is not None and "时长塌缩" in reason


# ======================================================================
# v6.62.6（09-09）_count_mid_clause_tails 判据换血：
#   「缺句末标点」→「悬挂虚词收尾」
#
# 旧判据在 09-08 真实数据上命中 40/44 = 91%，命中的全是完整描述（doubao 不给
# 描述性字段加句号）。后果：判据 3 被行文风格稳定触发，**把几乎每次 GAP 重试都
# 回退掉**，连 v6.62.4 的零高潮专项重试一起废掉（授予重试 → 以「48>39 残句」
# 回退 → 终稿仍零高潮）。
# ======================================================================

def _one_shot(**fields) -> dict:
    return {"shots": [dict(duration_sec=5.0, **fields)]}


def test_complete_descriptions_without_period_are_not_truncation():
    """doubao 风格完整描述（不加句号）必须全部放过——旧判据在此 100% 误报."""
    from step01_script_gen.main import _count_mid_clause_tails
    script = _one_shot(
        prompt="中景，船头对峙，危机一触即发，暗流涌动，惊觉对峙",
        ff_desc="烈日直晒，热浪微微蒸腾，孩童嬉玩可见",
        lf_desc="脸上满是兴致。画面完整，所有角色定锚",
        motion_desc="前2秒：孩童蹲身揭土皮，摞成小堆嬉玩",
    )
    assert _count_mid_clause_tails(script) == 0


def test_real_truncation_residue_still_detected():
    """日志实录的真实残句（停在「至」「对」）必须仍然命中."""
    from step01_script_gen.main import _count_mid_clause_tails
    script = _one_shot(
        prompt="中景，船头对峙，镜头推近至",
        ff_desc="听香端着陶碗立于舱口，惊觉对",
    )
    assert _count_mid_clause_tails(script) == 2


def test_guqing_nouns_ending_in_particles_not_flagged():
    """古风名词尾（大将/刀把/棉被/土地）不得误报——故意不收这些字入表."""
    from step01_script_gen.main import _count_mid_clause_tails
    script = _one_shot(
        prompt="殿前列阵，甲士拱卫着那位大将",
        ff_desc="刀鞘斜挂腰间，右手稳稳握住刀把",
        lf_desc="竹榻上胡乱叠着一床旧棉被",
        motion_desc="前2秒：脚步碾过雨后湿软的土地",
    )
    assert _count_mid_clause_tails(script) == 0


def test_fixed_collocations_excluded():
    """由于/至于/面对 等固定搭配不算悬挂（_DANGLING_EXCEPT）."""
    from step01_script_gen.main import _count_mid_clause_tails
    script = _one_shot(
        prompt="两人于舱中隔案而坐，彼此沉默地面对",
        ff_desc="船身微微一晃，皆因水下暗涌由于",
    )
    assert _count_mid_clause_tails(script) == 0


# ---------------------------------------------------------------------------
# v6.62.7（09-09）零高潮救援：prev 零高潮而 new 补上高潮镜时，软判据（台词失锚
# /截断残句）降级为告警，只剩「时长塌缩」还能触发回退。
# 背景：09-08 实测正片零高潮 → v6.62.4 特批 1 次重试 → 重试版被「台词失锚 2 条」
# 判退化回退 → 终稿仍零高潮（刺杀整段没拍）。两个修复互相抵消。
# ---------------------------------------------------------------------------

def test_climax_rescue_waives_ungrounded_dialogue():
    """救援态：台词失锚变多不再触发回退（措辞瑕疵 < 整章高潮缺失）."""
    from step01_script_gen.main import _retry_degrade_reason
    prev = {"shots": [{"narration_segment": "杨旭在船上钓鱼", "duration_sec": 7.5}
                      for _ in range(8)]}
    new = {"shots": [
        {"narration_segment": "凶手是谁？如何击杀了公子", "duration_sec": 7.5},
        {"narration_segment": "那人一下子从水里跳出来，就击杀了公子", "duration_sec": 7.5},
    ] + [{"duration_sec": 7.5} for _ in range(6)]}
    # 非救援态仍判退化（原行为不变）
    assert _is_retry_degraded(prev, new, _NOVEL) is True
    # 救援态放行
    assert _is_retry_degraded(prev, new, _NOVEL, climax_rescue=True) is False
    assert _retry_degrade_reason(prev, new, _NOVEL, climax_rescue=True) is None


def test_climax_rescue_waives_truncation_residue():
    """救援态：截断残句变多也不再触发回退."""
    prev = {"shots": [{"duration_sec": 7.5} for _ in range(8)]}
    new = {"shots": [
        {"prompt": "中景，船头对峙，镜头推近至", "duration_sec": 7.5},
        {"ff_desc": "听香端着陶碗立于舱口，惊觉对", "duration_sec": 7.5},
    ] + [{"duration_sec": 7.5} for _ in range(6)]}
    assert _is_retry_degraded(prev, new, "") is True
    assert _is_retry_degraded(prev, new, "", climax_rescue=True) is False


def test_climax_rescue_still_blocks_duration_collapse():
    """救援态**不豁免**时长塌缩——拉伸成慢动作是不可挽回的物理损坏."""
    from step01_script_gen.main import _retry_degrade_reason
    prev = {"shots": [{"duration_sec": 7.5} for _ in range(8)]}   # 60s
    new = {"shots": [{"duration_sec": 1.0} for _ in range(11)]}   # 11s
    assert _is_retry_degraded(prev, new, "", climax_rescue=True) is True
    assert "时长塌缩" in _retry_degrade_reason(prev, new, "", climax_rescue=True)


# ---------------------------------------------------------------------------
# v6.62.6（09-09）判据表结构不变式 + 日志文案一致性
#
# 换判据是高误报风险动作（旧判据 91% 误报把每次 GAP 重试都回退掉），两张手写字符
# 表因此需要结构性守卫：一条永不生效的例外条目只会让人误以为某个词被排除了。
# ---------------------------------------------------------------------------

def test_dangling_except_entries_all_end_with_a_tail_char():
    """_DANGLING_EXCEPT 每一项都必须以 _DANGLING_TAIL 中的字结尾.

    不满足则该条目**永远不会被 endswith(_DANGLING_TAIL) 命中**，是死条目——
    读表的人会以为它排除了某个搭配，实际什么也没做。
    """
    from step01_script_gen.main import _DANGLING_TAIL, _DANGLING_EXCEPT

    dead = [w for w in _DANGLING_EXCEPT if not w.endswith(_DANGLING_TAIL)]
    assert dead == [], f"死条目（永不生效）: {dead}"


def test_dangling_tail_excludes_known_noun_endings():
    """故意不收的名词尾字不得被加回表里（加回=大面积误报回归）."""
    from step01_script_gen.main import _DANGLING_TAIL

    for ch in "地把被由跟朝沿将向和得":
        assert ch not in _DANGLING_TAIL, f"『{ch}』常作名词尾，收入必然误报"


def test_truncation_reason_wording_matches_actual_criterion():
    """日志文案必须说「悬挂虚词」而不是「非终结标点」.

    本次改动的动机之一就是「打印的指标不是真正命中的判据」误导运维；文案若仍
    描述已被替换掉的旧判据，等于换了个方式重犯同一个错。
    """
    from step01_script_gen.main import _retry_degrade_reason

    prev = {"shots": [{"duration_sec": 7.5} for _ in range(8)]}
    new = {"shots": [
        {"prompt": "中景，船头对峙，镜头推近至", "duration_sec": 7.5},
        {"ff_desc": "听香端着陶碗立于舱口，惊觉对", "duration_sec": 7.5},
    ] + [{"duration_sec": 7.5} for _ in range(6)]}

    reason = _retry_degrade_reason(prev, new, "")
    assert "悬挂虚词" in reason
    assert "非终结标点" not in reason, "文案仍在描述已被替换的旧判据"


def test_terminal_tail_constant_removed():
    """旧判据常量 _TERMINAL_TAIL 必须随判据一起删除（不留孤儿）."""
    import step01_script_gen.main as m

    assert not hasattr(m, "_TERMINAL_TAIL")
