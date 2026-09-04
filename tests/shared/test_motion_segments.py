"""v6.58 motion_segments 解析与注入的测试.

覆盖：
  1. build_motion_segments: 显式分多段（"2-4秒"）
  2. build_motion_segments: 单 "前N秒" + implicit 补段
  3. build_motion_segments: 全角破折号 "2－4秒"
  4. build_motion_segments: 无时段 / 空 motion
  5. count_effective_segments: 排除 implicit 空段
  6. format_motion_segments: zh / en_s 两种风格
  7. format_motion_segments: 跳过空段
  8. _build_prompt 集成: 3 段应注入
  9. _build_prompt 集成: 单段不注入
  10. _build_seedance_concise_prompt 集成: 短 3 段应注入
  11. _build_seedance_concise_prompt 集成: 长段不注入
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.motion_segments import (
    build_motion_segments,
    format_motion_segments,
    count_effective_segments,
)


class TestBuildMotionSegments:
    """基础解析."""

    def test_three_explicit_segments(self):
        segs = build_motion_segments({
            "duration_sec": 6,
            "motion_desc": "前2秒：拔剑；2-4秒：挥剑；4-6秒：收剑",
        })
        assert len(segs) == 3
        assert segs[0]["start"] == 0.0 and segs[0]["end"] == 2.0
        assert segs[1]["start"] == 2.0 and segs[1]["end"] == 4.0
        assert segs[2]["start"] == 4.0 and segs[2]["end"] == 6.0
        assert segs[0]["text"] == "拔剑"
        assert segs[1]["text"] == "挥剑"
        assert segs[2]["text"] == "收剑"

    def test_single_前N_sec_implicit(self):
        """单 '前3秒' 显式 + implicit (3, 6) 空段."""
        segs = build_motion_segments({
            "duration_sec": 6,
            "motion_desc": "前3秒：杨旭抬头",
        })
        # len = 2（一段显式 + 一段 implicit 空）
        assert len(segs) == 2
        assert segs[0]["text"] == "杨旭抬头"
        assert segs[1]["text"] == ""  # implicit

    def test_fullwidth_dash(self):
        """全角破折号 2－4秒 也支持."""
        segs = build_motion_segments({
            "duration_sec": 5,
            "motion_desc": "0－2秒：抽刀；3－5秒：出刀",
        })
        assert len(segs) == 2
        assert segs[0]["text"] == "抽刀"
        assert segs[1]["text"] == "出刀"

    def test_no_time_segment(self):
        segs = build_motion_segments({
            "duration_sec": 6,
            "motion_desc": "杨旭沿街奔跑",
        })
        assert segs == []

    def test_empty_motion(self):
        segs = build_motion_segments({"duration_sec": 6, "motion_desc": ""})
        assert segs == []

    def test_no_duration(self):
        segs = build_motion_segments({"motion_desc": "前2秒：拔剑"})
        assert segs == []


class TestCountEffectiveSegments:
    """有效段计数（排除 implicit 空段）."""

    def test_excludes_implicit_empty(self):
        segs = build_motion_segments({
            "duration_sec": 6,
            "motion_desc": "前3秒：抬头",
        })
        # 2 段总，但只有 1 段有效
        assert len(segs) == 2
        assert count_effective_segments(segs) == 1

    def test_all_effective(self):
        segs = build_motion_segments({
            "duration_sec": 6,
            "motion_desc": "前2秒：A；2-4秒：B；4-6秒：C",
        })
        assert count_effective_segments(segs) == 3

    def test_empty(self):
        assert count_effective_segments([]) == 0


class TestFormatMotionSegments:
    """格式化输出."""

    def test_zh_style(self):
        segs = [
            {"start": 0.0, "end": 2.0, "text": "拔剑"},
            {"start": 2.0, "end": 4.0, "text": "挥剑"},
        ]
        out = format_motion_segments(segs, style="zh")
        assert out == "0-2秒：拔剑；2-4秒：挥剑"

    def test_en_s_style(self):
        segs = [
            {"start": 0.0, "end": 2.0, "text": "拔剑"},
            {"start": 2.0, "end": 4.0, "text": "挥剑"},
        ]
        out = format_motion_segments(segs, style="en_s")
        # 段间用全角分号「；」拼接（与 zh 一致）
        assert out == "0-2s: 拔剑；2-4s: 挥剑"

    def test_skip_empty_segments(self):
        segs = [
            {"start": 0.0, "end": 2.0, "text": "拔剑"},
            {"start": 2.0, "end": 4.0, "text": ""},  # implicit
        ]
        out = format_motion_segments(segs, style="zh")
        assert out == "0-2秒：拔剑"

    def test_empty_input(self):
        assert format_motion_segments([]) == ""


class TestBuildPromptIntegration:
    """_build_prompt 注入【时间分段】段."""

    def test_three_segments_injected(self):
        from step02_video_gen import prompt_builder as pb
        shot = {
            "index": 1,
            "prompt": "杨旭", "ff_desc": "远景", "lf_desc": "近景",
            "motion_desc": "前2秒：拔剑；2-4秒：挥剑；4-6秒：收剑",
            "duration_sec": 6,
        }
        out = pb._build_prompt(shot, script={}, mode="A")
        assert "【时间分段】" in out
        assert "0-2秒" in out and "2-4秒" in out and "4-6秒" in out

    def test_single_前N_sec_not_injected(self):
        """单 '前3秒' 实际只产 1 段有效，不注入."""
        from step02_video_gen import prompt_builder as pb
        shot = {
            "index": 1,
            "prompt": "杨旭", "ff_desc": "杨旭", "lf_desc": "杨旭",
            "motion_desc": "前3秒：杨旭抬头",
            "duration_sec": 6,
        }
        out = pb._build_prompt(shot, script={}, mode="A")
        assert "【时间分段】" not in out

    def test_no_time_segment_not_injected(self):
        from step02_video_gen import prompt_builder as pb
        shot = {
            "index": 1,
            "prompt": "杨旭", "ff_desc": "杨旭", "lf_desc": "杨旭",
            "motion_desc": "杨旭沿街奔跑",
            "duration_sec": 6,
        }
        out = pb._build_prompt(shot, script={}, mode="A")
        assert "【时间分段】" not in out


class TestBuildSeedanceConcisePromptIntegration:
    """精简 prompt 路径（500 字硬约束）."""

    def test_short_three_segments_injected_en_s(self):
        from step02_video_gen import prompt_builder as pb
        shot = {
            "index": 1,
            "prompt": "杨旭", "ff_desc": "远景", "lf_desc": "近景",
            "motion_desc": "前2秒：拔剑；2-4秒：挥剑；4-6秒：收剑",
            "duration_sec": 6,
        }
        out = pb._build_seedance_concise_prompt(shot)
        # 精简路径用 en_s（Seedance 2.0 官方推荐）
        assert "【时间分段】" in out
        assert "0-2s:" in out  # 注意英文冒号 + s
        # 总长应 < 500
        assert len(out) < 500

    def test_long_segments_not_injected(self):
        """段文本超 80 字时不注入，避免超 500 字硬约束."""
        from step02_video_gen import prompt_builder as pb
        # 3 段各 50 字 → 段文本总 150+ 字（含时间标签和分号），> 80 字
        long_text = (
            "前2秒：杨旭从暗处小心翼翼地探出半个身子，握刀的手微微颤抖，眼神警觉地扫视四周；"
            "2-4秒：杨旭突然跃起，手中钢刀在月光下划出一道寒光，朝对方头部猛劈；"
            "4-6秒：杨旭借力后撤半步重新调整姿态准备下一轮攻击"
        )
        shot = {
            "index": 1,
            "prompt": "杨旭", "ff_desc": "远景", "lf_desc": "近景",
            "motion_desc": long_text,
            "duration_sec": 6,
        }
        out = pb._build_seedance_concise_prompt(shot)
        # 段文本 > 80 字时跳过
        assert "【时间分段】" not in out

    def test_too_many_segments_not_injected(self):
        """>4 段不注入精简路径（控成本）."""
        from step02_video_gen import prompt_builder as pb
        shot = {
            "index": 1,
            "prompt": "杨旭", "ff_desc": "远景", "lf_desc": "近景",
            "motion_desc": "0-1秒：A；1-2秒：B；2-3秒：C；3-4秒：D；4-5秒：E",
            "duration_sec": 5,
        }
        out = pb._build_seedance_concise_prompt(shot)
        assert "【时间分段】" not in out
