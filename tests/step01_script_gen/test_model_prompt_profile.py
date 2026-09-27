"""v6.68 per-model prompt 剖面单测：解析规则 + gen_spy_script 跟随模型.

背景：TEXT_PROVIDER=agent_plan 下此前所有模型共用 build_doubao_prompt +
DOUBAO_SYSTEM_PROMPT + novel≤1600 截断，曾造成候选结构性零高潮（高潮被
切在 prompt 外）。现在 prompt/system 跟模型走，且两个剖面都保留完整 novel。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# resolve_model_prompt_profile：解析规则
# ---------------------------------------------------------------------------

def test_profile_substring_match():
    from step01_script_gen.prompts import (
        DOUBAO_SYSTEM_PROMPT, SYSTEM_PROMPT,
        build_doubao_prompt, build_prompt,
        resolve_model_prompt_profile,
    )

    b, s = resolve_model_prompt_profile("doubao-seed-2-1-turbo")
    assert b is build_doubao_prompt and s is DOUBAO_SYSTEM_PROMPT
    b, s = resolve_model_prompt_profile("deepseek-v4-flash")
    assert b is build_prompt and s is SYSTEM_PROMPT
    # 大小写不敏感
    b, _ = resolve_model_prompt_profile("DeepSeek-V4-Flash")
    assert b is build_prompt


def test_profile_none_uses_provider_default_model(monkeypatch):
    """model=None → 按 provider 默认模型解析（GAP 重生成等调用点的一致性）."""
    from _text_gen.config import TextGenCfg
    from step01_script_gen.prompts import (
        DOUBAO_SYSTEM_PROMPT, SYSTEM_PROMPT,
        build_doubao_prompt, build_prompt,
        resolve_model_prompt_profile,
    )

    monkeypatch.setattr(TextGenCfg, "PROVIDER", "agent_plan")
    monkeypatch.setattr(TextGenCfg, "AGENT_PLAN_TEXT_MODEL", "deepseek-v4-flash")
    b, s = resolve_model_prompt_profile(None)
    assert b is build_prompt and s is SYSTEM_PROMPT

    monkeypatch.setattr(TextGenCfg, "AGENT_PLAN_TEXT_MODEL", "doubao-seed-2-1-turbo")
    b, s = resolve_model_prompt_profile(None)
    assert b is build_doubao_prompt and s is DOUBAO_SYSTEM_PROMPT


def test_profile_unknown_model_falls_back_to_provider_selection(monkeypatch):
    """未注册模型 → 回退 provider 级选择（旧行为兜底，不猜剖面）."""
    from _text_gen.config import TextGenCfg
    from step01_script_gen.prompts import (
        DOUBAO_SYSTEM_PROMPT, build_doubao_prompt,
        resolve_model_prompt_profile,
    )

    monkeypatch.setattr(TextGenCfg, "PROVIDER", "agent_plan")
    b, s = resolve_model_prompt_profile("qwen-plus")
    assert b is build_doubao_prompt and s is DOUBAO_SYSTEM_PROMPT


def test_default_text_model_accessor(monkeypatch):
    from _text_gen.config import TextGenCfg

    monkeypatch.setattr(TextGenCfg, "PROVIDER", "agent_plan")
    monkeypatch.setattr(TextGenCfg, "AGENT_PLAN_TEXT_MODEL", "m-x")
    assert TextGenCfg.default_text_model() == "m-x"
    monkeypatch.setattr(TextGenCfg, "PROVIDER", "unknown_prov")
    assert TextGenCfg.default_text_model() == ""


# ---------------------------------------------------------------------------
# gen_spy_script：prompt 跟随模型（截断差异实证）
# ---------------------------------------------------------------------------

# >1600 字：结尾高潮标记必须在两个模型剖面中都可见
_LONG_NOVEL = "开头铺垫。" + "中段过渡。" * 400 + "高潮标记词腥甜刺杀在此。"


class _OkResult:
    def __init__(self):
        self.success = True
        self.error = ""
        self.text = '{"ok": 1}'


class _CaptureService:
    def __init__(self, captured):
        self._captured = captured

    def generate(self, req):
        self._captured.append(req)
        return _OkResult()


def test_gen_spy_script_deepseek_profile_keeps_full_novel(monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.prompts import SYSTEM_PROMPT

    captured = []
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _CaptureService(captured))
    m.gen_spy_script(_LONG_NOVEL, model="deepseek-v4-flash")
    assert captured[0].system_prompt is SYSTEM_PROMPT
    assert "腥甜刺杀" in captured[0].user_prompt  # 不截断：结尾高潮可见


def test_gen_spy_script_doubao_profile_keeps_full_novel(monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.prompts import DOUBAO_SYSTEM_PROMPT

    captured = []
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _CaptureService(captured))
    m.gen_spy_script(_LONG_NOVEL, model="doubao-seed-2-1-turbo")
    assert captured[0].system_prompt is DOUBAO_SYSTEM_PROMPT
    assert "腥甜刺杀" in captured[0].user_prompt  # 不再私有截断：结尾高潮可见


def test_doubao_system_prompt_is_clean_and_consistent():
    from pathlib import Path

    from step01_script_gen.prompts import DOUBAO_SYSTEM_PROMPT

    path = (Path(__file__).resolve().parents[2]
            / "step01_script_gen" / "prompts" / "doubao_system.md")
    assert "\x00" not in path.read_text(encoding="utf-8")
    assert "0.05 ≤ t_sec" in DOUBAO_SYSTEM_PROMPT
    assert "威胁源统一写「神秘人」" in DOUBAO_SYSTEM_PROMPT
    assert "刺客：受死吧" not in DOUBAO_SYSTEM_PROMPT


def test_system_prompt_split_examples_match_shot_cap():
    """system.md 的切分示例必须与**当前**单镜上限自洽（09-26 实缺陷）.

    旧文案「37-64 字（8.8-14.7s）**不必切**，一镜可容纳」是按 **15s** 上限标定的
    残留；当前 `SHOT_CAP=12s`，64 字 ≈14.7s 已超上限，而同段上文刚写完 12s 上限
    ——自相矛盾，等于教模型把超容台词留在一镜（章 2 诊断稿实测：256 字对白塞进
    合计 27s 的 5 镜，按 4 字/秒需 64s）。

    本用例不看死某个数字，而是**从示例自带的秒数反查 cap**：cap 以后再变，
    只要有人把旧标定抄回来就会判红。
    """
    import re

    from step01_script_gen.prompts import SYSTEM_PROMPT

    cap_m = re.search(r"单镜 narration_segment ≤ ([0-9]+) 字（≈([0-9.]+)s 以内）", SYSTEM_PROMPT)
    assert cap_m, "system.md 未声明单镜安全字数 / 单镜上限"
    safe_chars, cap_sec = int(cap_m.group(1)), float(cap_m.group(2))
    assert safe_chars == int(cap_sec * 4) - 4, (
        "安全字数 %d 与上限 %.0fs 不自洽（口径：cap × 4 字/秒 − 4 = %d）"
        % (safe_chars, cap_sec, int(cap_sec * 4) - 4))
    _ok = re.findall(r"([0-9]+) 字 ≈([0-9.]+)s 一镜可容纳", SYSTEM_PROMPT)
    assert _ok, "system.md 没有「一镜可容纳」示例——本判据随之失效，需同步更新"
    for n, sec in _ok:
        assert float(sec) <= cap_sec + 0.01, (
            "示例「%s 字 ≈%ss 一镜可容纳」超过单镜上限 %.0fs" % (n, sec, cap_sec))
    for n, sec in re.findall(r"([0-9]+) 字 ≈([0-9.]+)s 已超", SYSTEM_PROMPT):
        assert float(sec) > cap_sec, (
            "示例「%s 字 ≈%ss 已超」其实没超上限 %.0fs" % (n, sec, cap_sec))


def test_split_guidance_cut_points_match_the_hard_gate():
    """生成提示的**切点口径**必须与统一切分判据同源（逗号切点一律非法）.

    `plan_feasibility._SENT_END_CHARS`（。！？…；）之外一律非法，但 system.md /
    main.md 一直写着「切点选在「，」「…」「。」等停顿处」——**提示教模型按逗号拆，
    硬门按句末拒收**。实测章 2 第 1 句（80 字，通篇只有逗号/顿号）正是这条冲突的靶子：
    模型被诱导出逗号拆分 → 终稿台词门判 `cross_shot_split` → 整稿拒收。
    本用例锁三件事：① 不再列逗号为切点；② 列出的句末标点集是判据集的子集；
    ③ 不把「安全字数」写成「超过就一定切得开」的合法边界（能不能切还要看有无句末切点）。
    """
    import re

    from step01_script_gen.plan_feasibility import _SENT_END_CHARS
    from step01_script_gen.prompts import PROMPT_TEMPLATE, SYSTEM_PROMPT

    for name, text in (("system.md", SYSTEM_PROMPT), ("main.md", PROMPT_TEMPLATE)):
        assert not re.search(r"切点.{0,30}「，」", text), (
            "%s 仍把逗号写成合法切点（与 _SENT_END_CHARS 冲突）" % name)
        assert "句末" in text, "%s 未声明句末切点口径" % name
        listed = re.findall(r"句末标点（([^）]+)）", text)
        assert listed, "%s 未列出句末标点集——提示与判据无法对上" % name
        for chars in listed:
            extra = set(chars) - set(_SENT_END_CHARS)
            assert not extra, (
                "%s 列出的句末标点 %s 不在判据集 %r 里" % (name, sorted(extra), _SENT_END_CHARS))
        assert "单镜安全字数" in text and "不可表示" in text, (
            "%s 未声明「安全字数 ≠ 存在合法切点」（超字数但没有句末切点时不可表示）" % name)


def test_agent_plan_log_line_names_the_actual_model_profile():
    """启动日志不得再硬编码「doubao 专属 prompt」——剖面跟**模型**走.

    实损（2026-09-26）：`main.py` 在 agent_plan 通道下无条件打印「使用 doubao 专属
    prompt」，而 `SCRIPT_CANDIDATE_MODELS=deepseek-v4-flash` 实际走 `build_prompt` +
    `system.md`。用户据此判断「step01 在用 doubao、能力太弱」并提出改模型配置——
    实测 764 次提交**全是** deepseek-v4-flash。**日志主动误导比日志缺失更糟**：
    它会把排障引到错误的模型上。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "step01_script_gen" / "main.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        if "doubao 专属 prompt" in line:
            assert line.lstrip().startswith("#"), (
                "启动日志不得再硬编码「doubao 专属 prompt」（只许留在说明注释里）：%s" % line)
    assert "prompt 剖面按模型解析" in src, "启动日志必须按实际候选模型解析并打印剖面"


def test_doubao_prompt_compact_outline_keeps_climax(monkeypatch):
    """超长大纲压缩时必须保留高潮锚点，不能被前置事件行挤掉."""
    from step01_script_gen.prompts import build_doubao_prompt

    monkeypatch.setattr("step01_script_gen.prompts.PROMPT_TEMPLATE", "{novel_text}")
    events = "；".join(f"事件{i:02d}" for i in range(30))
    outline_anchor = (
        f"【本章主要事件】{events}\n"
        "【情绪弧线】压抑试探，逐步升温，最后爆发。\n"
        "【高潮事件】本章刺杀反转完成。\n"
        "【结尾钩子】留下更大的阴谋伏笔。"
    )
    prompt = build_doubao_prompt("novel", outline_anchor=outline_anchor)
    outline_part = prompt.split(
        "### 本章大纲（分镜必须覆盖以下事件与高潮）\n", 1
    )[1]
    assert "【高潮事件】本章刺杀反转完成。" in outline_part
    assert "【结尾钩子】" not in outline_part
    assert len(outline_part.split("\n###", 1)[0]) <= 200
