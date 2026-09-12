# -*- coding: utf-8 -*-
"""拆镜视觉差异化（v6.50，08-29）单元测试.

拆镜差异化（v6.50 起 timecode 拆镜器口径；旧 _hard_fix_split_long_dialogue_shot 已删）——此前把 prompt/ff/lf 原样
复制给每个子镜（13:46 run 实测 shot9↔10 prompt 一字不差、shot9/10/12 ff==lf）。
"""
from __future__ import annotations

import difflib
import itertools
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

def _make_long_dialogue_shot(index: int = 8, sentences: int = 5) -> dict:
    """构造会触发拆镜的长对话镜（sentences 个句子 → 拆 2-3 镜）."""
    body = "。".join([
        "住嘴",
        "公子是何等人物岂容你搬弄是非",
        "老爷们急着给府学送银子",
        "谁会在乎你一个小女子冤是不冤",
        "你最好想清楚再开口",
        "否则休怪我不讲情面",
    ][:sentences]) + "。"
    narr = "张十三冷冷道：" + body
    return {
        "index": index,
        "narration_segment": narr,
        "subtitle": narr,
        "scene": "照月湾",
        "location": "照月湾小舟",
        "main_subject": "张十三",
        "prompt": "中景，张十三青衣小帽立在船头，冷冷盯着听香，听香跪坐船板低头啜泣。冷蓝夜色，固定镜头",
        "ff_desc": "中景，张十三冷冷盯着听香，听香跪坐船板低头啜泣，泪流满面",
        "lf_desc": "中景，张十三冷冷盯着听香，听香跪坐船板低头啜泣，泪流满面",
        "motion_desc": "张十三压低声音威胁，听香抽噎点头",
        "char_layout": [
            {"name": "张十三", "position": "画右船头", "facing": "面向听香", "role": "主"},
            {"name": "听香", "position": "画左船板", "facing": "面向张十三", "role": "次"},
        ],
        "duration_sec": 22.0,
    }


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()



def test_timecode_splitter_differentiates_half_index_shot():
    """timecode pre-pass 拆分器（半整数索引）同样差异化——ff==lf 必现路径."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shot = {
        "index": 11,
        "narration_segment": "张十三：公子离奇遇刺，船上却只有你一人，你又说不出个所以然来。"
                             "只说少爷是被人所刺，谁会信你？这场官司打到官府里，你便休想脱身了。",
        "subtitle": "",
        "scene": "照月湾", "location": "照月湾小舟", "main_subject": "张十三",
        "prompt": "中景，张十三蹲在听香面前，手指指着她，语气阴沉；听香抱膝眼泪滑落。冷蓝夜色，固定镜头",
        "ff_desc": "张十三蹲在画右，身体前倾，听香跪坐画左双手捧脸泪痕斑斑",
        "lf_desc": "张十三蹲在画右，身体前倾，听香跪坐画左双手捧脸泪痕斑斑",
        "motion_desc": "张十三压低声音威胁",
        "char_layout": [
            {"name": "张十三", "position": "画右", "facing": "面向听香", "role": "主"},
            {"name": "听香", "position": "画左", "facing": "面向张十三", "role": "次"},
        ],
        "duration_sec": 14.0,
        "keyframes": [],
    }
    shots = [shot]
    n = _do_pre_split_oversized_shots(shots, 12.0)
    assert n == 1 and len(shots) == 2
    b = shots[1]
    assert b["index"] == 11.5
    assert b["ff_desc"] != b["lf_desc"], "后半镜 ff==lf 未破除"
    assert "过肩" in b["ff_desc"]
    assert "聚焦张十三" in b["ff_desc"], "说话人应从原镜 narration 回退抽取"
    assert _sim(shots[0]["prompt"], b["prompt"]) < 0.85


# ════════════════════ v6.51（08-29）：接续拆分自抄修复 ════════════════════
# 16:34 run 实测：_compute_timecodes 多次落盘重跑 pre-pass，已拆子镜被再拆
# （7.5→8.0 撞真实 shot 8）；子镜 ff=上一镜 lf、lf=同文本深拷贝，装饰只加
# 尾部碎片 → ff/lf 相似度 0.85-0.94，image 模式下视频没有动作可插值。

def _make_chain_base_shot(index=7, dur=17.0):
    """接续拆分场景：lf 含机器拼装站位行（「X位于画…」）."""
    narr = ("张十三冷冷道：你可知罪。公子是何等人物岂容你搬弄是非。"
            "老爷们急着给府学送银子，谁会在乎你一个小女子。"
            "你最好想清楚再开口，否则休怪我不讲情面。")
    base = ("张十三听完，目光凝望听香，双眼微眯，听香仍掩面啜泣，杨旭（衣宽袍）不动。"
            "张十三位于画左前景、面向画右听香。听香位于画右向画左张十三。"
            "杨旭位于画中背景、仰面不动")
    return {
        "index": index,
        "narration_segment": narr,
        "subtitle": narr,
        "scene": "照月湾", "location": "照月湾小舟", "main_subject": "张十三",
        "prompt": base,
        "ff_desc": base,
        "lf_desc": base,
        "motion_desc": "张十三压低声音威胁",
        "char_layout": [
            {"name": "张十三", "position": "画左前景", "facing": "面向听香", "role": "主"},
            {"name": "听香", "position": "画右", "facing": "面向张十三", "role": "次"},
            {"name": "杨旭", "position": "画中背景", "facing": "仰面", "role": "次"},
        ],
        "duration_sec": dur,
        "keyframes": [],
    }


def test_timecode_split_intra_shot_fflf_below_085():
    """拆出子镜自身 ff/lf 相似度 < 0.85（16:34 run shot7.5/8.0/9.5 自抄根因）."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shots = [_make_chain_base_shot()]
    _do_pre_split_oversized_shots(shots, 12.0)
    assert len(shots) == 2
    b = shots[1]
    r = _sim(b["ff_desc"], b["lf_desc"])
    assert r < 0.85, f"子镜 ff/lf 相似度 {r:.2f} ≥ 0.85"


def test_timecode_split_ff_no_camera_push_and_lf_single_push():
    """ff（静态首帧）不带运镜短语；lf 恰好一条推镜指令."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shot = _make_chain_base_shot()
    shot["ff_desc"] = shot["lf_desc"] = (
        shot["lf_desc"] + "，镜头较首帧缓推至全景，过肩视角，越张十三的肩望向听香")
    shots = [shot]
    _do_pre_split_oversized_shots(shots, 12.0)
    b = shots[1]
    assert "推至" not in b["ff_desc"], "ff 不应携带运镜短语"
    assert b["lf_desc"].count("推至") == 1, f"lf 应恰好一条推镜: {b['lf_desc']}"
    assert "杨旭位于画中背景、仰面不动" in b["lf_desc"], "受害者站位行应保留"


def test_timecode_split_lf_focus_rotates_to_listener():
    """lf 焦点换到另一侧（说话神情 ↔ 聆听反应），成图主体不同."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shots = [_make_chain_base_shot()]
    _do_pre_split_oversized_shots(shots, 12.0)
    b = shots[1]
    assert "画面聚焦张十三（说话神情）" in b["ff_desc"]
    assert "画面聚焦听香（聆听反应）" in b["lf_desc"]


def test_timecode_split_never_chains_on_recompute():
    """_compute_timecodes 重跑 pre-pass 不再拆已拆镜（7.5→8.0 链式禁令）."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shots = [_make_chain_base_shot()]
    _do_pre_split_oversized_shots(shots, 12.0)
    after_first = [(s["index"], s["narration_segment"]) for s in shots]
    n2 = _do_pre_split_oversized_shots(shots, 12.0)
    assert n2 == 0, "第二轮 pre-pass 不应再拆任何镜"
    after_second = [(s["index"], s["narration_segment"]) for s in shots]
    assert after_first == after_second


def test_timecode_split_index_never_collides_with_existing():
    """索引防碰撞：orig=7.5 + 0.5=8.0 撞真实 shot 8 → 细化粒度避开."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    big = _make_chain_base_shot(index=7.5)
    big["narration_segment"] = "张十三冷冷道：" + "你可知罪。" * 10
    existing8 = {"index": 8, "narration_segment": "短", "ff_desc": "",
                 "lf_desc": "", "prompt": "", "motion_desc": ""}
    shots = [big, existing8]
    _do_pre_split_oversized_shots(shots, 12.0)
    new_idx = shots[1]["index"]
    all_idx = [s["index"] for s in shots]
    assert len(set(all_idx)) == len(all_idx), f"索引数值重复: {all_idx}"
    assert new_idx != 8.0


def test_differentiate_idempotent_byte_level():
    """差异化二次应用字节级收敛（对话拆分器拆到已差异化子镜的场景）."""
    from step01_script_gen.split_visuals import differentiate_split_visuals

    orig = _make_chain_base_shot()
    sub = {"prompt": orig["prompt"], "ff_desc": orig["lf_desc"],
           "lf_desc": orig["lf_desc"],
           "narration_segment": "你可知罪。公子是何等人物岂容你搬弄是非。",
           "char_layout": orig["char_layout"]}
    differentiate_split_visuals(orig, sub, part_i=1)
    snap = (sub["prompt"], sub["ff_desc"], sub["lf_desc"])
    differentiate_split_visuals(orig, sub, part_i=1)
    assert (sub["prompt"], sub["ff_desc"], sub["lf_desc"]) == snap


def test_anchor_variant_not_double_appended():
    """变体锚（暗红痕迹洇开）已存在时不再追加完整 INJURY_ANCHOR_TAIL.

    16:34 run shot8.0 实测：STRONG 判已锚漏过变体 → 双锚追加 → 复读去重
    抠出「。暗红血迹洇开，血痕」截断碎片。
    """
    from step01_script_gen.timecode import _do_pre_split_oversized_shots
    from step01_script_gen.prompt_semantic.registry import INJURY_ANCHOR_TAIL

    shot = _make_chain_base_shot()
    variant = shot["lf_desc"] + "。伤处白袍心口一片暗红痕迹洇开，清晰可见"
    shot["ff_desc"] = shot["lf_desc"] = variant
    shot["prompt"] = shot["prompt"] + "。心口中刀"
    shots = [shot]
    _do_pre_split_oversized_shots(shots, 12.0)
    b = shots[1]
    for fld in ("ff_desc", "lf_desc"):
        assert b[fld].count("洇开") <= 2 and "血痕清晰可见" not in b[fld], \
            f"{fld} 疑似重复追加完整锚尾: {b[fld][-50:]}"
        assert INJURY_ANCHOR_TAIL not in b[fld]


def test_repair_echo_stripped_from_motion_desc():
    """regen 修复指令回显剥离（「，，，每拍1个面部细节按时间先后写全」）."""
    from step01_script_gen.constraints.prompt_repetition import _strip_repair_echo

    assert _strip_repair_echo("嘴角下撇，，，每拍1个面部细节按时间先后写全") == "嘴角下撇"
    assert _strip_repair_echo("低头隐忍。每拍一个面部细节，按时间先后写全") == "低头隐忍"
    assert _strip_repair_echo("正常描述不剥离") == "正常描述不剥离"
    assert _strip_repair_echo("中间提到每拍面部细节的句子继续") == "中间提到每拍面部细节的句子继续"


def test_consecutive_punct_collapsed():
    """连续同类标点折叠（「瞪大，，，扭曲」→「瞪大，扭曲」）."""
    from shared.content_safety import collapse_doubled_tokens

    assert collapse_doubled_tokens("瞪大，，，扭曲") == "瞪大，扭曲"
    assert collapse_doubled_tokens("完。。。。结") == "完。结"
    assert collapse_doubled_tokens("慢慢走来") == "慢慢走来"
    assert collapse_doubled_tokens("等等……他说——") == "等等……他说——"


def test_repair_echo_preserves_trailing_period_when_no_echo():
    """无回显的字段原样返回——不误删尾部句号."""
    from step01_script_gen.constraints.prompt_repetition import _strip_repair_echo

    assert _strip_repair_echo("杨旭倒在地上。") == "杨旭倒在地上。"
    assert _strip_repair_echo("听香掩面啜泣，") == "听香掩面啜泣，"
