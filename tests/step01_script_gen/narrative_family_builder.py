# -*- coding: utf-8 -*-
"""三族审计夹具的共用构造器（内容无关）.

按 AGENTS.md「内容无关性与夹具」：**样本数据与机制分离**。

* 本模块只提供句式骨架、**内容无关的不变量校验**与字段级变异；
* 人名、地点、道具、动作、台词、时间标记、镜头编号、时长、关系与预期全部由
  显式样本包 ``fixtures/audit_narrative_families/packs/sample_packs.json`` 提供；
* **换小说 / 换章节 = 新增或替换样本包**，不改本模块、不改消费测试、不改生产代码；
* 构造器与校验器不认识任何具体作品词，也不内置章节答案。

「全轴干净」不是靠人工挑句子，而由下列内容无关不变量定义：

====== ================================================================
不变量  含义
====== ================================================================
I1      结局完成：原文最后一个动作的结果态出现在最后一镜 lf_desc
I2      尾帧结果态：每镜 lf_desc 取该镜事件的 end，且不含过程态标记
I3      跨镜继承：后镜 ff 的道具集合 == 前镜 lf 的道具集合
I4      台词忠实：narration 台词逐字来自样本包 dialogue_lines，不新增
I5      声音归属：画外说话人不出现在任何 frame_layouts；前缀一致
I6      事件可追：每镜 motion/prompt 都描述本镜事件
I7      确定性检查器零 finding：scan_script(script) 不出任何 code
I8      编号/时长不假设默认值：与样本包逐项一致
I9      不含具体作品词
====== ================================================================

I2/I6 用到的词表是**通用视觉/句式词表**，不含任何故事内容；与
``step01_script_gen/script_findings.py`` 的「内容无关性」口径一致。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "audit_narrative_families"
PACKS_PATH = FIXTURES / "packs" / "sample_packs.json"

SCHEMA = "narrative_family_pack.v1"

#: 通用过程态标记（只用于排除尾帧写成「正在做」；不是故事内容）。
PROCESS_MARKERS = (
    "走向", "迈步", "起步", "正在", "将要", "即将", "准备", "开始",
    "欲", "将要去", "正要", "尚未完成", "还没",
)

#: 判别字段是**字段路径**，与内容无关。
FAMILY_DISCRIMINATING = {
    "time_ellipsis": ("scene", "clip_transition", "context_bridge"),
    "state_contradiction": ("ff_desc", "frame_layouts.ff"),
    "offscreen_voice": ("narration_segment", "frame_layouts.ff"),
}

#: 已知作品的专有词黑名单（只用于「不得写死」检查，不是答案或分支依据）。
FORBIDDEN_WORK_TERMS = (
    "锦衣夜行", "杨旭", "听香", "张十三", "杀青州",
)

WHY_TEMPLATE = (
    "样本包显式声明「{relation}」；基线在其它轴上同样与原文一致："
    "不新增台词或事件、每镜尾帧为该镜动作的结果态、末镜完成原文最后一个动作。"
    "因此它是全轴干净的正例，而不只是单轴合法。"
)


class FixtureInvariantError(AssertionError):
    """夹具违反内容无关不变量；消息给出具体的不变量编号与位置。"""


# ---------------------------------------------------------------------------
# 样本包
# ---------------------------------------------------------------------------

def load_packs() -> dict[str, dict]:
    doc = json.loads(PACKS_PATH.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise FixtureInvariantError("样本包 schema 不符：%r" % doc.get("schema"))
    packs = {p["id"]: p for p in doc["packs"]}
    if len(packs) != len(doc["packs"]):
        raise FixtureInvariantError("样本包 id 重复")
    return packs


def _check(cond: bool, invariant: str, msg: str) -> None:
    if not cond:
        raise FixtureInvariantError("I%s 违例：%s" % (invariant, msg))


def assert_pack_is_content_agnostic(pack: dict) -> None:
    blob = json.dumps(pack, ensure_ascii=False)
    for term in FORBIDDEN_WORK_TERMS:
        _check(term not in blob, "9", "样本包含具体作品词 %r" % term)
    _check(pack.get("family") in FAMILY_DISCRIMINATING, "9",
           "family 不在三族内：%r" % pack.get("family"))


# ---------------------------------------------------------------------------
# 构造：显式样本包 -> novel / plan / script
# ---------------------------------------------------------------------------

def _layout(pack: dict, idx: int, side: str) -> list[dict]:
    """画内实体数量由样本包决定；道具只挂在首个（主体）实体上。"""
    key = "props_ff" if side == "ff" else "props_lf"
    props = pack[key][idx]
    names = pack.get("on_screen_actors") or [pack["actors"]["main"]]
    return [{"name": name, "on_screen": True,
             "props": list(props) if i == 0 else []}
            for i, name in enumerate(names)]


def _shot(pack: dict, idx: int) -> dict:
    ev = pack["events"][idx]
    actor = pack["actors"]["main"]
    phases = pack.get("phases") or ["前段", "后段"]
    return {
        "index": pack["shot_indices"][idx],
        "duration_sec": pack["durations"][idx],
        "main_subject": actor,
        "scene": pack["scenes"][idx],
        "location": pack.get("location") or pack["place"],
        "clip_transition": pack["transitions"][idx],
        "context_bridge": pack["bridges"][idx],
        "ff_desc": "%s%s" % (actor, ev["start"]),
        "lf_desc": "%s%s" % (actor, ev["end"]),
        "motion_desc": "%s：%s%s；%s：%s%s"
                       % (phases[0], actor, ev["text"], phases[1], actor, ev["end"]),
        "prompt": "%s %s，%s%s"
                  % (pack.get("framing", "[中景]"), pack["scenes"][idx], actor, ev["text"]),
        "narration_segment": (pack["voice_lines"][idx]
                              if idx < len(pack["voice_lines"]) else ""),
        "frame_layouts": {
            "ff": _layout(pack, idx, "ff"),
            "lf": _layout(pack, idx, "lf"),
            "kfs": [],
        },
        "keyframes": [],
    }


def build_baseline(pack: dict) -> dict:
    """从一份样本包构造全轴干净的正例 case（顶层结构兼容既有 runner）。"""
    assert_pack_is_content_agnostic(pack)
    family = pack["family"]
    shots = [_shot(pack, i) for i in range(len(pack["events"]))]
    case = {
        "family": family,
        "case_id": pack["case_id"],
        "expected": {
            "verdict": "legal",
            "defect_class": "none",
            "discriminating": list(FAMILY_DISCRIMINATING[family]),
            "why": WHY_TEMPLATE.format(relation=pack["relation_label"]),
            "must_preserve": list(pack["must_preserve"]),
        },
        "novel": pack["novel"],
        "plan": {},
        "script": {"shots": shots, "hook": None,
                   "total_duration": round(sum(pack["durations"]), 2)},
        # 生成身份：换内容时只换 pack，机制与消费方不变。
        "spec": {"schema": SCHEMA, "pack_id": pack["id"], "spec_version": 1},
    }
    assert_clean_baseline(case, pack)
    return case


def build_defect(pack: dict) -> dict:
    """从同一基线的副本按样本包声明做**字段级**变异；不内置任何族特定缺陷逻辑。"""
    base = build_baseline(pack)
    d = copy.deepcopy(base)
    ov = pack["defect"]
    shot = d["script"]["shots"][ov["target_shot"]]
    for key, value in ov["patches"].items():
        shot[key] = value
    d["case_id"] = pack.get("defect_case_id", pack["case_id"] + "_defect")
    d["expected"] = {
        "verdict": "defect",
        "defect_class": ov["defect_class"],
        "discriminating": list(FAMILY_DISCRIMINATING[pack["family"]]),
        "why": "从同一干净基线只改样本包声明的目标字段：%s" % ov["label"],
        "must_preserve": list(pack["must_preserve"]),
    }
    assert_defect_is_causal(d, pack)
    return d


def assert_defect_is_causal(defect: dict, pack: dict) -> None:
    """变异只应改变声明的字段，原文与其它镜头逐字不变（AGENTS.md 基线先合法）。"""
    base = build_baseline(pack)
    ov = pack["defect"]
    _check(defect["novel"] == base["novel"], "6", "变异不应改原文")
    _check(len(defect["script"]["shots"]) == len(base["script"]["shots"]), "6",
           "变异不应增删镜头")
    for i, (a, b) in enumerate(zip(base["script"]["shots"],
                                   defect["script"]["shots"])):
        changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
        want = set(ov["patches"]) if i == ov["target_shot"] else set()
        _check(changed == want, "6",
               "shot%d 变更字段 %r，期望 %r" % (i, sorted(changed), sorted(want)))


# ---------------------------------------------------------------------------
# 校验：内容无关的「全轴干净」不变量
# ---------------------------------------------------------------------------

def _narration_parts(text: str) -> tuple[str, str]:
    """把「说话人：台词」拆开；无前缀时说话人为空。"""
    raw = str(text or "").strip()
    if not raw:
        return "", ""
    for sep in ("：", ":"):
        if sep in raw:
            speaker, line = raw.split(sep, 1)
            return speaker.strip(), line.strip()
    return "", raw


def assert_clean_baseline(case: dict, pack: dict | None = None) -> None:
    """对任意样本包生成的 legal 夹具跑同一套不变量；不认识具体故事内容。"""
    if pack is None:
        pack = load_packs()[case["spec"]["pack_id"]]
    shots = case["script"]["shots"]
    events = pack["events"]
    actor = pack["actors"]["main"]

    _check(case["expected"]["verdict"] == "legal", "0", "不是 legal 基线")
    _check(len(shots) == len(events), "8", "镜头数与事件数不一致")
    _check([s["index"] for s in shots] == list(pack["shot_indices"]), "8",
           "镜头编号与样本包不一致（不得假设从 1 开始）")
    _check([s["duration_sec"] for s in shots] == list(pack["durations"]), "8",
           "镜头时长与样本包不一致")

    # I1 结局完成
    _check(events[-1]["end"] in shots[-1]["lf_desc"], "1",
           "最后一镜 lf_desc 未呈现末事件结果态 %r" % events[-1]["end"])

    # I2 尾帧结果态
    for i, (shot, ev) in enumerate(zip(shots, events)):
        _check(ev["end"] in shot["lf_desc"], "2",
               "shot%d lf_desc 未取本镜结果态" % i)
        for marker in PROCESS_MARKERS:
            _check(marker not in shot["lf_desc"], "2",
                   "shot%d lf_desc 含过程态标记 %r" % (i, marker))

    # I3 跨镜道具继承
    for i in range(len(shots) - 1):
        before = {p for item in shots[i]["frame_layouts"]["lf"]
                  for p in item.get("props") or []}
        after = {p for item in shots[i + 1]["frame_layouts"]["ff"]
                 for p in item.get("props") or []}
        _check(before == after, "3",
               "shot%d→%d 道具状态未继承：%r vs %r" % (i, i + 1, before, after))

    # I4 台词忠实 / I5 声音归属
    spoken: list[tuple[int, str, str]] = []
    for i, shot in enumerate(shots):
        speaker, line = _narration_parts(shot["narration_segment"])
        if line:
            spoken.append((i, speaker, line))
    for i, speaker, line in spoken:
        _check(line in pack["dialogue_lines"], "4",
               "shot%d 出现原文没有的台词 %r" % (i, line))
        offscreen = pack["actors"].get("offscreen")
        if offscreen:
            _check(speaker == offscreen, "5",
                   "shot%d 说话人 %r 与画外角色 %r 不符" % (i, speaker, offscreen))
    offscreen = pack["actors"].get("offscreen")
    if offscreen:
        for i, shot in enumerate(shots):
            names = {str(item.get("name") or "")
                     for side in ("ff", "lf")
                     for item in shot["frame_layouts"].get(side) or []}
            _check(offscreen not in names, "5",
                   "shot%d 画外角色 %r 出现在画面内" % (i, offscreen))

    # I6 事件可追
    for i, (shot, ev) in enumerate(zip(shots, events)):
        _check(ev["text"] in shot["motion_desc"], "6",
               "shot%d motion_desc 未描述本镜事件" % i)
        _check(ev["text"] in shot["prompt"], "6",
               "shot%d prompt 未描述本镜事件" % i)

    # I7 确定性检查器零 finding
    from step01_script_gen.script_findings import scan_script
    findings = scan_script(case["script"])
    _check(not findings, "7", "确定性检查器出 finding：%r"
           % [(f.get("code"), f.get("shot_id")) for f in findings])

    # I9 不含具体作品词
    blob = json.dumps(case, ensure_ascii=False)
    for term in FORBIDDEN_WORK_TERMS:
        _check(term not in blob, "9", "夹具含具体作品词 %r" % term)


def assert_case_matches_pack(case: dict, pack: dict) -> None:
    """冻结在磁盘上的夹具必须与样本包重新构造的结果逐字一致（防漂移）。"""
    rebuilt = build_baseline(pack)
    _check(case == rebuilt, "0",
           "冻结夹具与样本包重建结果不一致；请由构造器重新生成，不要手工改")
