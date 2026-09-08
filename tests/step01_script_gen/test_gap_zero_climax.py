"""GAP「零高潮覆盖」检测 —— v6.62.4（09-08）.

修的两个真实缺陷（09-08 chapter_001 实测）：

1. **零边界漏洞**：`analyze_shot_distribution` 旧代码写
   `if has_climax_coverage and climax_shots_count < 3`——`count == 0` 时
   `has_climax_coverage` 为 False，整个蒙太奇检查被**跳过**。于是
   「1-2 个高潮镜」FAIL 而「0 个高潮镜」PASS，还打印「✅ 质量优秀」。
   实测：正片 11 镜零高潮（刺客/刺杀/匕首/心口/破水/尖叫/死 全 0 次），
   GAP 报 status=pass、反馈为空，LLM 永远收不到「补高潮」指令。

2. **检测面近乎全盲**：只扫 narration_segment + subtitle + core_keywords，
   而实测 7/11 镜 narration_segment 为空——高潮若只写在 motion_desc /
   main_subject 就检测不到。

3. **反馈二次遮蔽**：`prompts._load_gap_analysis_context` 把高潮分支嵌在
   `if not dist["valid"]` 内，零高潮时 valid=True → 分支正好在该报的场景失效。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.gap_analysis import analyze_shot_distribution
from step01_script_gen.prompts import _load_gap_analysis_context
from tests.step01_script_gen.conftest import make_script, make_shot


def _dist(shots):
    """跑分布分析（novel_text 不参与高潮检测，传空串即可）."""
    return analyze_shot_distribution(make_script(shots), "")


# ---------------------------------------------------------------------------
# 1. 零边界：0 个高潮镜必须 fail（比 1-2 个更严重，不能静默通过）
# ---------------------------------------------------------------------------

def test_zero_climax_shots_fails():
    """零高潮覆盖 → status != pass 且报「零高潮」."""
    shots = [
        make_shot(index=i, narration_segment="", main_subject="空镜·环境",
                  motion_desc="镜头缓慢平移，荷叶轻晃")
        for i in range(1, 6)
    ]
    d = _dist(shots)
    assert d["climax_shots"] == 0
    assert d["status"] != "pass", "零高潮不能判 pass（v6.62.4 修的零边界漏洞）"
    assert any("零高潮" in m for m in d["messages"])


def test_zero_climax_does_not_claim_excellent():
    """零高潮时不得输出「质量优秀」（旧行为的直接回归防护）."""
    shots = [make_shot(index=i, main_subject="群演") for i in range(1, 5)]
    d = _dist(shots)
    assert not any("质量优秀" in m for m in d["messages"])


# ---------------------------------------------------------------------------
# 2. 不回归：1-2 个高潮镜仍走原有蒙太奇告警；>=3 个仍 pass
# ---------------------------------------------------------------------------

def test_single_climax_shot_still_warns_montage():
    """1 个高潮镜 → 保留原「建议拆分为至少 3 个」告警（口径不变）.

    注意用词：GAP 词表（climax_words._CLIMAX_WORDS_GAP_NO_META 23 词）收的是
    「刺杀/寒光/鲜血/破水而出」等，**不含「刺客」**（角色名不是事件词）。
    """
    shots = [make_shot(index=1, narration_segment="寒光一闪，公子遭刺杀")]
    shots += [make_shot(index=i, main_subject="空镜") for i in range(2, 5)]
    d = _dist(shots)
    assert d["climax_shots"] == 1
    assert d["status"] != "pass"
    assert any("至少 3 个" in m for m in d["messages"])
    # 不应误报成「零高潮」
    assert not any("零高潮" in m for m in d["messages"])


def test_three_climax_shots_pass():
    """>=3 个高潮镜 → 蒙太奇检查通过."""
    shots = [
        make_shot(index=1, narration_segment="刺客破水而出", main_subject="刺客"),
        make_shot(index=2, narration_segment="寒光一闪，刺入心口", main_subject="刺客"),
        make_shot(index=3, narration_segment="鲜血涌出，反转揭露", main_subject="那男子"),
    ]
    d = _dist(shots)
    assert d["climax_shots"] >= 3
    assert not any("零高潮" in m or "至少 3 个" in m for m in d["messages"])


# ---------------------------------------------------------------------------
# 3. 扩检测面：高潮只写在 motion_desc / main_subject 也要能检出
# ---------------------------------------------------------------------------

def test_climax_detected_in_motion_desc_only():
    """narration_segment 全空、高潮只在 motion_desc → 仍应检出（治全盲）."""
    shots = [
        make_shot(index=1, narration_segment="", subtitle="",
                  motion_desc="前2秒：刺客破水而出；2-4秒：挥刀刺入"),
        make_shot(index=2, narration_segment="", motion_desc="追逐，快马疾驰"),
        make_shot(index=3, narration_segment="", main_subject="刺杀现场特写"),
    ]
    d = _dist(shots)
    assert d["climax_shots"] == 3, f"应 3 镜命中，实际 {d['climax_shots']}"


def test_single_char_keyword_not_matched_in_new_fields():
    """单字词（跑/杀/死）在新增字段不算命中——防误报.

    实测 shot1 的 prompt 曾因单字「跑」假命中。新字段只认 >=2 字关键词。
    """
    shots = [
        make_shot(index=i, narration_segment="", subtitle="",
                  motion_desc="老汉挑担快跑过田埂", main_subject="群演")
        for i in range(1, 5)
    ]
    d = _dist(shots)
    assert d["climax_shots"] == 0, "单字「跑」不应在 motion_desc 触发高潮命中"


def test_single_char_keyword_still_matched_in_narration():
    """单字词在 narration_segment/subtitle 仍生效（老口径不动）."""
    shots = [make_shot(index=1, narration_segment="他跑了")]
    d = _dist(shots)
    assert d["climax_shots"] == 1


# ---------------------------------------------------------------------------
# 4. 反馈解遮蔽：valid=True 时零高潮反馈也必须到达 LLM
# ---------------------------------------------------------------------------

def test_feedback_emitted_even_when_valid_true():
    """dist.valid=True + climax_shots=0 → 反馈非空（旧实现此处恒空）."""
    validation = {
        "low_coverage_shots": [],
        "distribution": {
            "status": "pass",
            "valid": True,             # 关键：旧代码在这里被挡住
            "has_climax_coverage": False,
            "climax_shots": 0,
            "messages": [],
        },
    }
    fb = _load_gap_analysis_context(validation)
    assert fb.strip(), "零高潮反馈不能为空"
    assert "零高潮" in fb
    assert "至少 3 镜" in fb


def test_feedback_for_one_or_two_climax_shots():
    """0 < count < 3 → 报「少于 3 个」而非「零高潮」."""
    validation = {
        "low_coverage_shots": [],
        "distribution": {
            "status": "fail", "valid": False,
            "has_climax_coverage": True, "climax_shots": 2, "messages": [],
        },
    }
    fb = _load_gap_analysis_context(validation)
    assert "少于 3 个" in fb
    assert "零高潮" not in fb


def test_feedback_silent_when_climax_ok():
    """>=3 个高潮镜 → 不产生分布反馈."""
    validation = {
        "low_coverage_shots": [],
        "distribution": {
            "status": "pass", "valid": True,
            "has_climax_coverage": True, "climax_shots": 4, "messages": [],
        },
    }
    fb = _load_gap_analysis_context(validation)
    assert "镜头分布问题" not in fb


def test_feedback_no_distribution_key_is_silent():
    """distribution 缺失（非 GAP 路径调用）→ 不误报零高潮."""
    fb = _load_gap_analysis_context({"low_coverage_shots": []})
    assert "零高潮" not in fb
