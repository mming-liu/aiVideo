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
    """重试版截断字段 ≥2 且比上一版多 → 判退化（保留上一版）."""
    prev = {"shots": [{"prompt": "上一版完整句子以句号收尾。", "ff_desc": "也是完整的描述句子。"}]}
    new = {"shots": [
        {"prompt": "残句一没有终结标点直接切断", "ff_desc": "残句二也是半句话切断", "lf_desc": "残句三依旧如此收"},
        {"prompt": "完整句子以句号收尾。", "ff_desc": "完整描述。"},
    ]}
    assert _is_retry_degraded(prev, new, "") is True
