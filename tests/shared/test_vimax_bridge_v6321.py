# -*- coding: utf-8 -*-
"""2026-08-29 三 bug 修复单测：vimax_bridge 重试膨胀 + lf 软跳过 + style_prefix 措辞.

v6.32.1 修复：
- Bug 1: prompt 组装移出重试循环（避免 attempt=2 时 prompt 膨胀 25-40%）
- Bug 2: lf 矛盾门包 try-except → 软跳过（返回 None），不再让 lf 矛盾 fail-fast
  整条 shot；ff 仍 fail-fast 保留主防线
- Bug 3 (style_prefix): 黑影尾句改措辞，不触发矛盾自检器
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest


# ── Bug 1: vimax_bridge prompt 组装移出循环 ─────────────────────────


def test_ff_prompt_assembly_outside_retry_loop(monkeypatch, tmp_path):
    """ff prompt 在 3 次 attempt 长度必须相等（无叠加注入）.

    用 mock adapter 抓取每次重试实际发送的 prompt 字符串，比较长度。
    旧实现 attempt=2 会比 attempt=1 长 25-40%（实测）；新实现恒等。

    设计：让前 2 次 attempt 的图片文件不达标（_validate_frame 返 False），
    强制走 3 次循环；第 3 次成功，捕获所有 prompt 比较。
    v6.x C 项：per-attempt 微扰动只追加到 desc_full 尾部，**剥离扰动后
    基础 prompt 应恒等**（08-29 重试膨胀修复的不变量保持）。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "3")  # 总尝试 3 次

    from shared.vimax_bridge import _gen_first_frame_async, _ATTEMPT_VARIATIONS

    captured_prompts: list[str] = []
    validate_call_count = [0]

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            captured_prompts.append(kwargs["prompt"])
            from PIL import Image
            class _Out:
                def __init__(self):
                    self.data = Image.new("RGB", (1024, 1792), color="black")
            return _Out()

    shot = {
        "index": 99,
        "ff_desc": "杨旭与听香对坐舱中，月色清凉",
        "lf_desc": "",
        "motion_desc": "",
        "scene": "照月湾小舟上",
        "location": "照月湾",
        "narration_segment": "",
        "main_subject": "杨旭",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    from shared import vimax_bridge as vb

    async def _no_qc(*a, **k): return False
    monkeypatch.setattr(vb, "_maybe_qc_frame", _no_qc)

    # 前 2 次 validate 返 False（不达标）→ 强制重试；第 3 次返 True
    def _validate_v2(path):
        validate_call_count[0] += 1
        return validate_call_count[0] >= 3
    monkeypatch.setattr(vb, "_validate_frame", _validate_v2)
    monkeypatch.setattr(vb.Path, "write_text", lambda *a, **k: None)

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)

    async def run():
        return await _gen_first_frame_async(
            MockAdapter(), work_dir, shot,
            force=True,
            script_mtime=None,
            reference_paths=None,
            max_retries=2,  # 总 3 次
            empty_scene=False,
            characters=characters,
        )

    result = asyncio.run(run())
    assert result is not None
    # 期望：3 次 prompt 字符串都被捕获
    assert len(captured_prompts) == 3, f"期望 3 次 attempt，实测 {len(captured_prompts)}"
    p1, p2, p3 = captured_prompts
    # v6.x C 项：剥离 per-attempt variation 后基础 prompt 应恒等
    # attempt 0 = variation[0] = ""（无追加）
    v1 = _ATTEMPT_VARIATIONS[1]
    v2 = _ATTEMPT_VARIATIONS[2]
    assert p2.endswith(v1), f"attempt 1 尾部应为 variation[1]={v1!r}，实测末尾 {p2[-30:]!r}"
    assert p3.endswith(v2), f"attempt 2 尾部应为 variation[2]={v2!r}，实测末尾 {p3[-30:]!r}"
    base1 = p2[: -len(v1)] if v1 else p2
    base2 = p3[: -len(v2)] if v2 else p3
    assert base1 == base2, "剥离 variation 后基础 prompt 应恒等（08-29 修复不变量保持）"
    # attempt 0 的 prompt 等于 base（variation[0]=""）
    assert p1 == base1, "attempt 0 (variation[0]='') 应等于剥离后的基础 prompt"


# ── Bug 2: lf 矛盾软跳过（不抛） ─────────────────────────────────────


def test_lf_contradiction_soft_skip_returns_none(monkeypatch, tmp_path):
    """lf prompt 含确定矛盾时 → _gen_last_frame_async 返回 None 不抛.

    ff fail-fast 保留主防线；lf 软跳过避免单帧矛盾 kill 整条 shot。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "1")

    from shared.vimax_bridge import _gen_last_frame_async

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            raise AssertionError("矛盾时不应调到图片生成")

    # 矛盾 prompt：匕首在杨旭手中 + 杨旭手中没有武器
    shot = {
        "index": 7,
        "ff_desc": "杨旭与听香对坐舱中",
        "lf_desc": "杨旭手中匕首寒光如月",
        "motion_desc": "",
        "scene": "照月湾小舟上",
        "location": "照月湾",
        "narration_segment": "",
        "main_subject": "杨旭",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)

    from shared import vimax_bridge as vb
    # 注入矛盾的 _build_final_frame_prompt（绕过真实组装，确保进入矛盾门）
    monkeypatch.setattr(
        vb, "_build_final_frame_prompt",
        lambda *a, **k: "【道具锚定】神秘人匕首在杨旭手中。"
                       "杨旭手中没有武器，没有刀，没有剑，没有短刃。"
    )

    async def run():
        return await _gen_last_frame_async(
            MockAdapter(), work_dir, shot,
            force=True, script_mtime=None,
            reference_paths=None, max_retries=1,
            empty_scene=False, characters=characters,
        )

    result = asyncio.run(run())
    assert result is None, f"lf 矛盾应软跳过返回 None，实测 {result!r}"


# ── Bug 3: style_prefix 黑影尾句不再触发矛盾自检器 ───────────────────


def test_invisible_threat_wording_passes_contradiction_check():
    """新措辞代入最终 prompt 跑矛盾自检器，必须 0 报矛盾.

    旧措辞「禁止…第二个黑影」+ 剧情「水下黑影轮廓」会触发对称矛盾。
    沿用原 test_count_anchor_invisible_threat_shows_entity_count 的 shot
    设定（保证走 _threat_invisible_only 分支，应用新措辞）。
    """
    from shared.prompt_contradiction import find_contradictions
    from shared.style_prefix import add_image_strict_constraints

    shot = {
        "location": "小舟上",
        "ff_desc": "神秘人化作水下黑影一闪而过，杨旭仰卧未觉，听香在旁未察",
        "lf_desc": "", "motion_desc": "",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}, {"identifier": "刺客"}]
    base = "杨旭仰卧，水下黑影，"
    tail = add_image_strict_constraints(base, "首帧", shot=shot, characters=characters)
    full = base + tail
    issues = find_contradictions(full)
    assert not any("对称矛盾" in i for i in issues), \
        f"新措辞不应触发黑影自触矛盾，实测 issues={issues}"


# ── Bug 4: 末次 attempt QC critical → 保留末版（attempts_exhausted）──
# v6.32.1 后续兜底：兑现 `保留最不差一版 / 无图比坏图更伤链路` 注释意图。
# 旧实现 unlink 跑在 attempt 检查前，末次仍删帧；新实现 unlink 只发生在
# 还有下一次 attempt 时，末次保留并打 attempts_exhausted 留痕。


def test_ff_attempts_exhausted_keeps_frame(monkeypatch, tmp_path):
    """ff 3 次 attempt QC 都 critical → 末版保留 + 返回路径存在.

    设计：mock _maybe_qc_frame 恒返 True；mock _try_qc_repair 恒返 False
    （走删帧/重抽分支）；mock validate 恒 True（让文件保留在磁盘）。
    末次 attempt 后断言：ff_path 仍存在；返回路径 == ff_path。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "3")

    from shared.vimax_bridge import _gen_first_frame_async

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            from PIL import Image
            class _Out:
                def __init__(self):
                    self.data = Image.new("RGB", (1024, 1792), color="black")
            return _Out()

    shot = {
        "index": 50,
        "ff_desc": "杨旭与听香对坐舱中",
        "lf_desc": "",
        "motion_desc": "",
        "scene": "小舟上",
        "location": "小舟上",
        "narration_segment": "",
        "main_subject": "杨旭",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    from shared import vimax_bridge as vb

    async def _always_critical(*a, **k): return True
    async def _no_repair(*a, **k): return False
    monkeypatch.setattr(vb, "_maybe_qc_frame", _always_critical)
    monkeypatch.setattr(vb, "_try_qc_repair", _no_repair)
    monkeypatch.setattr(vb, "_validate_frame", lambda p: True)
    monkeypatch.setattr(vb.Path, "write_text", lambda *a, **k: None)

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)
    ff_path = work_dir / "50" / "first_frame.png"

    async def run():
        return await _gen_first_frame_async(
            MockAdapter(), work_dir, shot,
            force=True, script_mtime=None,
            reference_paths=None, max_retries=2,
            empty_scene=False, characters=characters,
        )

    result = asyncio.run(run())
    assert result is not None, "末次保留应返回非 None 路径"
    assert result == str(ff_path), f"应返回 ff_path={ff_path}，实测 {result}"
    assert Path(result).exists(), f"末次保留帧应存在磁盘，实测 {result} 不存在"


def test_lf_attempts_exhausted_keeps_frame(monkeypatch, tmp_path):
    """lf 3 次 attempt QC 都 critical → 末版保留 + 返回路径存在.

    与 ff 对称，验证 lf 末次也走保留分支。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "3")

    from shared.vimax_bridge import _gen_last_frame_async

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            from PIL import Image
            class _Out:
                def __init__(self):
                    self.data = Image.new("RGB", (1024, 1792), color="black")
            return _Out()

    shot = {
        "index": 51,
        "ff_desc": "杨旭与听香对坐舱中",
        "lf_desc": "杨旭手持匕首刀锋寒光",
        "motion_desc": "",
        "scene": "小舟上",
        "location": "小舟上",
        "narration_segment": "",
        "main_subject": "杨旭",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    from shared import vimax_bridge as vb

    async def _always_critical(*a, **k): return True
    async def _no_repair(*a, **k): return False
    monkeypatch.setattr(vb, "_maybe_qc_frame", _always_critical)
    monkeypatch.setattr(vb, "_try_qc_repair", _no_repair)
    monkeypatch.setattr(vb, "_validate_frame", lambda p: True)
    monkeypatch.setattr(vb.Path, "write_text", lambda *a, **k: None)

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)
    lf_path = work_dir / "51" / "last_frame.png"

    async def run():
        return await _gen_last_frame_async(
            MockAdapter(), work_dir, shot,
            force=True, script_mtime=None,
            reference_paths=None, max_retries=2,
            empty_scene=False, characters=characters,
        )

    result = asyncio.run(run())
    assert result is not None, "lf 末次保留应返回非 None 路径"
    assert result == str(lf_path), f"应返回 lf_path={lf_path}，实测 {result}"
    assert Path(result).exists(), f"lf 末次保留帧应存在磁盘，实测 {result} 不存在"


def test_kf_attempts_exhausted_keeps_path(monkeypatch, tmp_path):
    """kf 3 次 attempt QC 都 critical → 末版 append 到 kf_paths（不再丢弃）.

    旧实现末次 critical 直接 break，kf 静默丢失；新实现末次 append + break，
    坏 kf 进视频比没 kf 好（与 ff/lf 对齐）。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "3")

    from shared.vimax_bridge import _gen_keyframes_async

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            from PIL import Image
            class _Out:
                def __init__(self):
                    self.data = Image.new("RGB", (1024, 1792), color="black")
            return _Out()

    # keyframes_data 是 list of {desc, t_sec, ...}；至少 1 个 kf。
    # v6.34：必须走 t_sec + shot.duration_sec（is_middle_kf 严格判定）
    kf_desc = "听香持匕首刺向杨旭胸口，刀光凛冽"
    shot = {
        "index": 52,
        "ff_desc": "杨旭仰卧舱中",
        "lf_desc": "杨旭仰卧舱中匕首没入胸口",
        "motion_desc": "前2秒：听香破水出刺；2-4秒：匕首没入胸口杨旭闷哼",
        "duration_sec": 4.0,
        "duration": 4.0,
        "keyframes": [
            {"desc": kf_desc, "t_sec": 1.5, "stage": "approach"},
        ],
        "scene": "小舟上",
        "location": "小舟上",
        "narration_segment": "",
        "main_subject": "听香",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    from shared import vimax_bridge as vb

    async def _always_critical(*a, **k): return True
    monkeypatch.setattr(vb, "_maybe_qc_frame", _always_critical)
    monkeypatch.setattr(vb, "_validate_frame", lambda p: True)
    monkeypatch.setattr(vb.Path, "write_text", lambda *a, **k: None)

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)
    kf_path = work_dir / "52" / "keyframe_0.png"

    async def run():
        return await _gen_keyframes_async(
            MockAdapter(), work_dir, shot,
            force=True, script_mtime=None,
            reference_paths=None, max_retries=2,
            empty_scene=False, characters=characters,
        )

    kf_paths = asyncio.run(run())
    assert isinstance(kf_paths, list), f"应返回 list，实测 {type(kf_paths)}"
    assert len(kf_paths) == 1, f"应保留 1 个 kf（旧版会丢），实测 {len(kf_paths)}"
    assert kf_paths[0] == str(kf_path), f"应保留 kf_path={kf_path}，实测 {kf_paths[0]}"
    assert Path(kf_paths[0]).exists(), f"kf 末次保留帧应存在磁盘，实测 {kf_paths[0]} 不存在"


# ── C 项：per-attempt prompt 微扰动（局部变量、零污染）─────────────────


def test_attempt_variation_no_shot_pollution(monkeypatch, tmp_path):
    """C 项：每次 attempt 追加 variation，shot 字段零污染 + 哈希键不变.

    验证：
    1. attempt 0 prompt 无 variation 尾缀（variation[0]=""）
    2. attempt 1+ prompt 尾部追加对应 variation
    3. shot 字段（如 ff_desc/keyframes）保持原值，未被回写
    4. 哈希值在 attempt 间不变（key 不含 variation 维度）
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    # QC_MAX_ATTEMPTS=4 → max_retries=3 → 4 次 attempt
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "4")

    from shared.vimax_bridge import _gen_first_frame_async, _ATTEMPT_VARIATIONS

    captured_prompts: list[str] = []
    captured_hashes: list[str] = []
    validate_call_count = [0]

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            captured_prompts.append(kwargs["prompt"])
            from PIL import Image
            class _Out:
                def __init__(self):
                    self.data = Image.new("RGB", (1024, 1792), color="black")
            return _Out()

    shot = {
        "index": 60,
        "ff_desc": "杨旭与听香对坐舱中",
        "lf_desc": "",
        "motion_desc": "",
        "scene": "小舟上",
        "location": "小舟上",
        "narration_segment": "",
        "main_subject": "杨旭",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    from shared import vimax_bridge as vb

    async def _no_qc(*a, **k): return False
    monkeypatch.setattr(vb, "_maybe_qc_frame", _no_qc)

    def _validate_v3(path):
        validate_call_count[0] += 1
        return validate_call_count[0] >= 4  # 强制 4 次 attempt
    monkeypatch.setattr(vb, "_validate_frame", _validate_v3)

    # 抓取 hash 写入
    orig_write_text = vb.Path.write_text
    def _capture_hash(self, *a, **k):
        if a and "hash" in str(self).lower():
            captured_hashes.append(a[0])
        return None
    monkeypatch.setattr(vb.Path, "write_text", _capture_hash)

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)

    async def run():
        return await _gen_first_frame_async(
            MockAdapter(), work_dir, shot,
            force=True, script_mtime=None, reference_paths=None,
            max_retries=3,  # 总 4 次
            empty_scene=False, characters=characters,
        )

    asyncio.run(run())

    assert len(captured_prompts) == 4
    # attempt 0 = variation[0]=""（无追加）
    assert not captured_prompts[0].endswith(_ATTEMPT_VARIATIONS[1])
    # attempt 1 = variation[1]
    assert captured_prompts[1].endswith(_ATTEMPT_VARIATIONS[1])
    # attempt 2 = variation[2]
    assert captured_prompts[2].endswith(_ATTEMPT_VARIATIONS[2])
    # attempt 3 = variation[3]
    assert captured_prompts[3].endswith(_ATTEMPT_VARIATIONS[3])
    # shot 字段零污染（ff_desc 不被回写）
    assert shot["ff_desc"] == "杨旭与听香对坐舱中"
    # 哈希值不变（取模后 4 次的 hash 都一样）
    assert len(set(captured_hashes)) == 1, f"哈希应恒定，实测 {captured_hashes}"


# ── A 项：扩 _LOCALIZED_FIX_PATTERNS 5→9 单元测试 ──────────────────────


def test_new_fix_patterns_match_and_select():
    """A 项：4 个新模式（costume_color/holding_wrong/multi_finger/face_expression）
    都能匹配 build_repair_plan；person_count 仍走 regen（不做局部修复）。"""
    from shared.image_qc import _LOCALIZED_FIX_PATTERNS, build_repair_plan
    from types import SimpleNamespace

    pattern_names = {p[0] for p in _LOCALIZED_FIX_PATTERNS}
    # 4 个新模式在册
    assert "costume_color" in pattern_names
    assert "holding_wrong" in pattern_names
    assert "multi_finger" in pattern_names
    assert "face_expression" in pattern_names
    # 注释说明不做的 2 个不在册
    assert "person_count" not in pattern_names
    assert "pose_wrong" not in pattern_names

    # 模拟 critical issues 验证 build_repair_plan 分类正确
    base_result = SimpleNamespace(
        issues=[
            SimpleNamespace(category="prompt_semantics", description="服装颜色不符描述", severity="critical"),
        ],
        entities=[
            {"label": "person", "desc": "wearing red robe, standing", "bbox": [100, 100, 400, 900]},
            {"label": "person", "desc": "wearing white robe, standing", "bbox": [500, 100, 800, 900]},
        ],
    )
    plan = build_repair_plan({"char_layout": []}, "first_frame", base_result)
    assert plan["action"] == "edit", f"costume_color 应触发 edit，实测 {plan['action']}"
    assert "costume_color" in plan["matched_patterns"]


# ── v6.x.x P0 修：4 模式正则收紧（属性词→问题形态词）回归测试 ──


def test_new_patterns_no_false_positive_on_attribute_words():
    """P0 修：4 新模式不再被"白袍""穿白衣"等裸属性词触发.

    旧 `r"穿|衣|袍"` 单字匹配太宽——critical 描述几乎必含衣袍类字。
    验证：纯属性词描述（"白袍角色"、"红衣人物"）不命中任何新模式。
    """
    from shared.image_qc import _LOCALIZED_FIX_PATTERNS
    import re

    # 找各模式的正则
    pat_map = {p[0]: re.compile(p[1]) for p in _LOCALIZED_FIX_PATTERNS}

    # 不应触发的纯属性词描述（"白袍角色睁眼"——睁眼是旧 eyes_closed 管的，
    # 这里的描述里没有眼睛/闭眼/睁眼词，但有白袍/角色属性词）
    false_positive_cases = [
        ("白袍角色", "costume_color"),  # 裸"袍"应不命中
        ("红衣人物", "costume_color"),
        ("穿白衣的剑客", "costume_color"),  # 裸"穿/衣"应不命中
        ("手中持刀", "holding_wrong"),  # 裸"持"应不命中（仅描述有持物，未言错）
        ("手指纤细", "multi_finger"),  # 裸"手指"应不命中
        ("表情温和", "face_expression"),  # 裸"表情"应不命中
    ]
    for desc, mode_name in false_positive_cases:
        assert not pat_map[mode_name].search(desc), \
            f"{mode_name} 不应被裸属性词 {desc!r} 触发（v6.x.x P0 修）"

    # 应触发的"问题形态词"描述
    true_positive_cases = [
        ("服装颜色错", "costume_color"),
        ("衣服颜色不符", "costume_color"),
        ("穿错衣服", "costume_color"),
        ("应持匕首", "holding_wrong"),
        ("应拿伞", "holding_wrong"),
        ("多指畸形", "multi_finger"),
        ("手部畸形", "multi_finger"),
        ("表情错", "face_expression"),
        ("惊愕", "face_expression"),
        ("痛苦", "face_expression"),
    ]
    for desc, mode_name in true_positive_cases:
        assert pat_map[mode_name].search(desc), \
            f"{mode_name} 应被 {desc!r} 触发（问题形态词）"


def test_critical_can_match_multiple_patterns():
    """P0 修：一条 critical 可同时命中多个模式，指令合并（first-match-wins 去除）.

    场景："白袍角色睁眼+血迹"——眼睛/血迹都是真问题，red_mark + eyes_closed
    都要修。旧实现 first-match-wins 会丢一个；新实现两个都命中、指令合并。
    """
    from shared.image_qc import build_repair_plan
    from types import SimpleNamespace

    mixed_result = SimpleNamespace(
        issues=[
            # 描述里既有"红色痕迹"又有"睁眼"，两个都该修
            SimpleNamespace(
                category="prompt_semantics",
                description="白袍人物有红色痕迹，睁眼",
                severity="critical",
            ),
        ],
        entities=[
            {"label": "person", "desc": "wearing white robe, lying", "bbox": [100, 100, 400, 900]},
        ],
    )
    plan = build_repair_plan({"char_layout": []}, "first_frame", mixed_result)
    assert plan["action"] == "edit", f"混合问题应触发 edit，实测 {plan['action']}"
    # 两个模式都应命中
    assert "red_mark" in plan["matched_patterns"], \
        f"red_mark 应在 matched_patterns，实测 {plan['matched_patterns']}"
    assert "eyes_closed" in plan["matched_patterns"], \
        f"eyes_closed 应在 matched_patterns，实测 {plan['matched_patterns']}"
    # 指令应包含两条
    edit_prompt = plan["edit_prompt"]
    assert "红色痕迹" in edit_prompt or "血迹" in edit_prompt, \
        f"edit_prompt 应含 red_mark 指令，实测 {edit_prompt}"
    assert "闭" in edit_prompt or "眼" in edit_prompt, \
        f"edit_prompt 应含 eyes_closed 指令，实测 {edit_prompt}"




def test_cache_hit_bad_qc_uses_variation1(monkeypatch, tmp_path):
    """P1 修：缓存命中但 QC critical 时，continue 让 attempt 走到 1 触发 variation[1].

    旧行为：缓存命中 → 删帧 → fall through 到 attempt=0 生成分支（variation[0]=""，
    即用与缓存相同的原 prompt 重画）—— 缓存的图本身就是原 prompt 画的失败品，
    用相同 prompt 画没意义。
    新行为：缓存命中 + bad QC → continue → 下一轮 attempt=1 用 variation[1]
    ("镜头构图稍紧，人物间距略近。") 重画。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "3")

    from shared.vimax_bridge import _gen_first_frame_async, _ATTEMPT_VARIATIONS

    captured_prompts: list[str] = []
    call_count = [0]

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            captured_prompts.append(kwargs["prompt"])
            from PIL import Image
            class _Out:
                def __init__(self):
                    self.data = Image.new("RGB", (1024, 1792), color="black")
            return _Out()

    # 写一个假帧 + 假哈希（模拟上次跑留下的"已失败"缓存）
    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)
    shot_dir = work_dir / "80"
    shot_dir.mkdir(parents=True, exist_ok=True)
    from PIL import Image as PILImage
    PILImage.new("RGB", (1024, 1792), color="black").save(str(shot_dir / "first_frame.png"))
    # 哈希需与 current_hash 匹配——直接调一次 _get_frame_content_hash 拿真实值
    from shared.vimax_bridge import _get_frame_content_hash
    shot = {
        "index": 80,
        "ff_desc": "杨旭与听香对坐舱中",
        "lf_desc": "", "motion_desc": "",
        "scene": "小舟上", "location": "小舟上",
        "narration_segment": "", "main_subject": "杨旭",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]
    current_hash = _get_frame_content_hash(shot, False, None, lock_kind="layout")
    (shot_dir / "frame_hash.txt").write_text(current_hash, encoding="utf-8")

    from shared import vimax_bridge as vb

    # QC 永远 critical（模拟缓存的图是"上次失败的坏图"）
    async def _always_critical(*a, **k): return True
    monkeypatch.setattr(vb, "_maybe_qc_frame", _always_critical)
    # 关闭 _try_qc_repair 让 continue 路径生效（不走修复）
    async def _no_repair(*a, **k): return False
    monkeypatch.setattr(vb, "_try_qc_repair", _no_repair)
    monkeypatch.setattr(vb, "_validate_frame", lambda p: True)
    monkeypatch.setattr(vb.Path, "write_text", lambda *a, **k: None)

    async def run():
        return await _gen_first_frame_async(
            MockAdapter(), work_dir, shot,
            force=False,  # ← 关键：不 force，触发缓存命中路径
            script_mtime=None, reference_paths=None,
            max_retries=2, empty_scene=False, characters=characters,
        )

    asyncio.run(run())
    # 缓存命中 + bad QC → continue → attempt=1 触发 variation[1]
    # 第一次 generate_single_image 应该是 attempt=1 的 prompt
    assert len(captured_prompts) >= 1
    first_prompt = captured_prompts[0]
    assert first_prompt.endswith(_ATTEMPT_VARIATIONS[1]), \
        f"P1 修：缓存命中 bad QC 后第一次生成应带 variation[1]={_ATTEMPT_VARIATIONS[1]!r}，实测末尾 {first_prompt[-30:]!r}"


# ── v6.x.x P5 修：kf 接 _try_qc_repair（v6.54.2 落地） ──


def test_kf_qc_repair_keeps_path(monkeypatch, tmp_path):
    """P5 修：kf QC critical 时先 _try_qc_repair，修复成功保留 kf 路径（不删帧重抽）.

    旧行为：kf 跳 _try_qc_repair，直接删帧重抽——局部可修的 critical 也走完整
    重抽循环，浪费 1 次模型调用 + 1 次 i2i 失败回退路径。
    新行为：kf 也接 _try_qc_repair（v6.54.2 落地）——修复成功时 kf_paths 含路径
    且哈希未删（下次缓存命中即修好的图）。
    """
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "3")

    from shared.vimax_bridge import _gen_keyframes_async

    class MockAdapter:
        async def generate_single_image(self, **kwargs):
            from PIL import Image
            class _Out:
                def __init__(self):
                    self.data = Image.new("RGB", (1024, 1792), color="black")
            return _Out()

    kf_desc = "听香持匕首刺向杨旭胸口，刀光凛冽"
    shot = {
        "index": 90,
        "ff_desc": "杨旭仰卧舱中",
        "lf_desc": "杨旭仰卧舱中匕首没入胸口",
        "motion_desc": "前2秒：听香破水出刺",
        "duration_sec": 4.0, "duration": 4.0,
        "keyframes": [{"desc": kf_desc, "t_sec": 1.5, "stage": "approach"}],
        "scene": "小舟上", "location": "小舟上",
        "narration_segment": "", "main_subject": "听香",
    }
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]

    from shared import vimax_bridge as vb

    async def _always_critical(*a, **k): return True
    async def _repair_success(*a, **k): return True
    monkeypatch.setattr(vb, "_maybe_qc_frame", _always_critical)
    monkeypatch.setattr(vb, "_try_qc_repair", _repair_success)
    monkeypatch.setattr(vb, "_validate_frame", lambda p: True)
    monkeypatch.setattr(vb.Path, "write_text", lambda *a, **k: None)

    work_dir = tmp_path / "shots"
    work_dir.mkdir(parents=True, exist_ok=True)
    kf_path = work_dir / "90" / "keyframe_0.png"

    async def run():
        return await _gen_keyframes_async(
            MockAdapter(), work_dir, shot,
            force=True, script_mtime=None, reference_paths=None,
            max_retries=2, empty_scene=False, characters=characters,
        )

    kf_paths = asyncio.run(run())
    assert len(kf_paths) == 1, f"P5 修：_try_qc_repair 成功应保留 kf，实测 {len(kf_paths)} 个"
    assert kf_paths[0] == str(kf_path), f"应保留 kf 路径，实测 {kf_paths[0]}"


# ── v6.x.x 实抽 bug 修：_try_qc_repair 复检路径 AttributeError ──


def test_try_qc_repair_post_edit_qc_no_attribute_error():
    """v6.x.x bug 修：_try_qc_repair 复检时用 _maybe_qc_frame 的 bool 返回值，
    而不是错误地访问 bool.has_hallucination（旧代码触发 AttributeError）。

    触发条件：_try_qc_repair 实际被调用 + 走到 i2i 编辑后的 QC 复检分支。
    之前所有单测都 monkeypatch _try_qc_repair 返 True/False，从未走到内部 i2i+复检
    路径——bug 一直藏到真实跑批才暴露。

    本测试用源码静态检查：找旧 bug 的特征行 `bool(qc2 and qc2.has_hallucination`，
    不在源码中即证明修复生效。
    """
    from pathlib import Path
    src = Path(_PROJECT_ROOT / "shared" / "vimax_bridge.py").read_text(encoding="utf-8")
    # 旧 bug 特征行：访问 .has_hallucination 必须在 bool(qc2 ...) 上下文
    buggy_pattern = "bool(qc2 and qc2.has_hallucination"
    assert buggy_pattern not in src, \
        f"v6.x.x bug 修：_try_qc_repair 仍包含旧 bug 模式 `{buggy_pattern}`\n" \
        f"（_maybe_qc_frame 返 bool，访问 .has_hallucination 必崩 AttributeError）"
    # 同时确认修复后的正确模式存在
    fixed_pattern = "qc2_critical = await _maybe_qc_frame"
    assert fixed_pattern in src, \
        f"v6.x.x bug 修：应包含 `{fixed_pattern}`\n（直接用 bool 返值）"


# ── v6.x.x area 护栏按模式分阈值 ──


def test_area_threshold_per_pattern():
    """v6.x.x 修：area 护栏从一刀切 70% 改为按模式分阈值。

    实抽 entry 6/7 验证：glowing/eyes_closed 命中（person_count=3），
    union area 80% 被旧 70% 护栏拒绝，修复机制实际命中率 0%。

    验证：
    - knife_contact 仍严守 70%（接触带小区域，70% 合理）
    - 其他模式（glowing/eyes_closed 等）放宽到 95%
    """
    from types import SimpleNamespace
    from shared.image_qc import build_repair_plan

    # 3 个人的全图 bboxes（union 80%）
    persons_3 = [
        {"label": "person", "desc": "wearing white robe, lying", "bbox": [50, 100, 600, 700]},
        {"label": "person", "desc": "wearing green dress, kneeling", "bbox": [400, 600, 850, 1100]},
        {"label": "person", "desc": "wearing black, head above water", "bbox": [100, 800, 500, 1100]},
    ]

    # 1. glowing 命中 + 80% union → 旧 70% 拒，新 95% 应 edit
    glow_result = SimpleNamespace(
        issues=[SimpleNamespace(category="anchor_mismatch",
                                description="图中是否有发光的物体或光刃？答 yes，期望 no",
                                severity="critical")],
        entities=persons_3,
    )
    plan = build_repair_plan({}, "first_frame", glow_result)
    assert plan["action"] == "edit", \
        f"glowing 命中 + 80% union 应放行（95% 阈值），实测 {plan['action']}，reason={plan.get('reason')}"
    assert "glowing" in plan["matched_patterns"]

    # 2. eyes_closed 命中 + 80% union → 应 edit
    eyes_result = SimpleNamespace(
        issues=[SimpleNamespace(category="anchor_mismatch",
                                description="仰面朝上双目紧闭？答 no，期望 yes",
                                severity="critical")],
        entities=persons_3,
    )
    plan = build_repair_plan({}, "first_frame", eyes_result)
    assert plan["action"] == "edit", \
        f"eyes_closed 命中 + 80% union 应放行（95% 阈值），实测 {plan['action']}"
    assert "eyes_closed" in plan["matched_patterns"]

    # 3. knife_contact 命中 + 80% union → knife_contact 严守 70%，应 regenerate
    #    （knife 接触带小区域理论 ≤30%，80% 一定非局部）
    knife_result = SimpleNamespace(
        issues=[SimpleNamespace(category="knife_contact",
                                description="刀具是否已接触或切入任何人的身体？",
                                severity="critical")],
        entities=persons_3,
    )
    plan = build_repair_plan({}, "first_frame", knife_result)
    assert plan["action"] == "regenerate", \
        f"knife_contact 80% union 应被 70% 阈值拒绝，实测 {plan['action']}，reason={plan.get('reason')}"

    # 4. 多模式命中（glowing + knife_contact）→ 取最宽松阈值（95%），应 edit
    mixed_result = SimpleNamespace(
        issues=[
            SimpleNamespace(category="anchor_mismatch",
                            description="图中是否有发光的物体或光刃？答 yes，期望 no",
                            severity="critical"),
            SimpleNamespace(category="knife_contact",
                            description="刀具是否已接触或切入任何人的身体？",
                            severity="critical"),
        ],
        entities=persons_3,
    )
    plan = build_repair_plan({}, "first_frame", mixed_result)
    assert plan["action"] == "edit", \
        f"混合命中（glowing + knife_contact）应取最宽松 95%，实测 {plan['action']}"


# ── v6.x.x.x review fixes：extra_person 正则收窄 + smaller_person 主体护栏 ──


def test_extra_person_regex_tightened():
    """review 修：去掉「多余人物」三字——router warning「画面中多出多余人物」
    误中删人模式风险。代码生成的确定性描述只剩「图中实体人数 vs 名单」
    和「角色被复制」。
    """
    from types import SimpleNamespace
    from shared.image_qc import build_repair_plan
    import re
    from shared.image_qc import _LOCALIZED_FIX_PATTERNS

    # 找 extra_person 模式
    extra_person_pat = next(p[1] for p in _LOCALIZED_FIX_PATTERNS if p[0] == "extra_person")

    # 应匹配的：代码生成的确定性文案
    assert re.search(extra_person_pat, "图中实体人数 4 vs 名单 3（容差 0）"), \
        "extra_person 应匹配「图中实体人数 vs 名单」（代码生成）"
    assert re.search(extra_person_pat, "黑色人物出现 2 次（角色被复制）"), \
        "extra_person 应匹配「角色被复制」"

    # 不应匹配的：router warning 文案（v6.x.x.x 收窄后）
    assert not re.search(extra_person_pat, "画面中多出多余人物"), \
        "v6.x.x.x 收窄后「多余人物」不应再匹配 extra_person（router warning 误中风险）"
    assert not re.search(extra_person_pat, "画面有多余人物"), \
        "v6.x.x.x 收窄后「多余人物」三字不应再匹配"


def test_smaller_person_subject_guard():
    """review 修：smaller_person 加主体护栏——最大框必须满足 _is_knife_holder
    或 _is_lying 才放行 edit，否则 regen 兜底（防把主体当多余者删掉）。

    hook 场景：神秘人是小框 + 持刀者（_is_knife_holder）→ 应 edit
    shot5 场景：神秘人落水是小框 + 不持刀不躺卧 → 应 regen（主体识别不明确）
    """
    from types import SimpleNamespace
    from shared.image_qc import build_repair_plan

    # hook 场景：3 人，大框=杨旭（躺卧），神秘人持刀，2 个小框（听香跪坐 + 神秘人头）
    # 最大框是杨旭（desc "lying" 触发 _is_lying）→ 放行
    hook_persons = [
        {"label": "person", "desc": "wearing white robe, lying", "bbox": [50, 100, 600, 800]},  # 大
        {"label": "person", "desc": "wearing green dress, kneeling", "bbox": [400, 700, 700, 950]},  # 小
        {"label": "person", "desc": "wearing black, holding knife", "bbox": [200, 800, 450, 950]},  # 小
    ]
    hook_result = SimpleNamespace(
        issues=[SimpleNamespace(category="extra_person",
                                description="图中实体人数 4 vs 名单 3（容差 0）",
                                severity="critical")],
        entities=hook_persons,
    )
    plan = build_repair_plan({}, "first_frame", hook_result)
    assert plan["action"] == "edit", \
        f"hook 场景（大框=躺卧杨旭）应放行 edit，实测 {plan['action']}, reason={plan.get('reason')}"

    # shot5 场景：3 人，最大框是个站立者（不躺不持刀），主体识别不明确
    # → smaller_person 返空 boxes → build_repair_plan 走 regen
    shot5_persons = [
        {"label": "person", "desc": "wearing white robe, standing", "bbox": [50, 100, 600, 800]},  # 大
        {"label": "person", "desc": "wearing black, head above water", "bbox": [200, 800, 450, 950]},  # 小
        {"label": "person", "desc": "wearing green dress, kneeling", "bbox": [400, 700, 700, 950]},  # 小
    ]
    shot5_result = SimpleNamespace(
        issues=[SimpleNamespace(category="extra_person",
                                description="图中实体人数 4 vs 名单 3（容差 0）",
                                severity="critical")],
        entities=shot5_persons,
    )
    plan = build_repair_plan({}, "first_frame", shot5_result)
    # 主体不明确 → all_boxes 空 → 走 regen（而不是删错人）
    assert plan["action"] == "regenerate", \
        f"shot5 场景（大框=站立者非躺非持刀）主体不明确应 regen，实测 {plan['action']}"
