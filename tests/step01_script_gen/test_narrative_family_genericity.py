# -*- coding: utf-8 -*-
"""三族审计夹具的内容无关性验收（AGENTS.md「内容无关性与夹具」）.

审核问题不是「当前小说能否过」，而是「相同契约换一部小说后是否仍成立」。
本文件验证：

* 冻结的三份 legal 夹具是**全轴干净**基线，且与样本包逐字一致（防手工漂移）；
* 同一套构造器 / 不变量 / 提示渲染 / 确定性检查器，在内容、长度、实体数量、
  镜头编号、道具与载体分布都不同的多组样本包上结论一致；
* 反例由同一基线的副本按样本包声明变异而来，且只改目标字段；
* 换小说时只新增一份 pack，构造器、消费测试与生产代码都不用改。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from narrative_family_builder import (  # noqa: E402
    FAMILY_DISCRIMINATING, FIXTURES, PACKS_PATH,
    assert_case_matches_pack, assert_clean_baseline, build_baseline,
    build_defect, load_packs,
)
from step01_script_gen import audit  # noqa: E402
from step01_script_gen.audit_adjudication import path_exists  # noqa: E402
from step01_script_gen.script_findings import scan_script  # noqa: E402

PACKS = load_packs()
FROZEN = {
    "time_ellipsis_legal.json": "pack_courtyard_time",
    "state_legal.json": "pack_cabin_state",
    "voice_legal.json": "pack_hall_voice",
}

#: 模拟「下次换一部小说」：一份全新的、与现有六组都不同的样本包。
#: 换内容时只需要新增这样一份数据，下面的机制与断言一处都不改。
NEW_NOVEL_PACK = {
    "id": "pack_adhoc_new_novel",
    "family": "time_ellipsis",
    "case_id": "legal_time_jump_observatory",
    "novel": "岚在观测台合上记录板。一周后，岚带着记录板离开观测台。",
    "actors": {"main": "岚", "listener": "岑", "offscreen": None},
    "on_screen_actors": ["岚", "岑"],
    "place": "观测台",
    "location": "观测台",
    "prop": "记录板",
    "events": [
        {"text": "合上记录板", "start": "右手拿着记录板站在台边",
         "end": "把记录板合上，转身面向出口"},
        {"text": "带板离开", "start": "拿着记录板立于台边",
         "end": "已离开观测台，记录板仍在手中"},
    ],
    "scenes": ["观测台·黄昏", "观测台·一周后"],
    "transitions": ["硬切", "淡出黑场，1s"],
    "bridges": ["", "一周后，光线已变"],
    "voice_lines": ["", ""],
    "dialogue_lines": [],
    "shot_indices": [21, 22],
    "durations": [9.0, 9.0],
    "props_ff": [["记录板"], ["记录板"]],
    "props_lf": [["记录板"], ["记录板"]],
    "framing": "[全景]",
    "phases": ["前半", "后半"],
    "relation_label": "时间省略：隔了一周",
    "must_preserve": ["时间关系：一周后", "记录板全程在手", "末镜已完成离开"],
    "defect": {
        "target_shot": 1,
        "patches": {"context_bridge": "", "clip_transition": "硬切"},
        "defect_class": "unclear",
        "label": "抹掉时间省略依据",
        "expected_findings": [],
    },
    "defect_case_id": "defect_time_jump_observatory",
}


# ---------------------------------------------------------------------------
# 冻结夹具：全轴干净 + 与样本包逐字一致
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,pid", list(FROZEN.items()), ids=list(FROZEN))
def test_frozen_legal_fixtures_are_clean_and_match_pack(name, pid):
    case = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    pack = PACKS[pid]
    assert_clean_baseline(case, pack)
    assert_case_matches_pack(case, pack)


def test_every_legal_fixture_is_clean():
    """新增 legal 夹具若不干净，这里会直接失败，不会等到真实调用才发现。"""
    legal = [p for p in sorted(FIXTURES.glob("*.json"))
             if json.loads(p.read_text(encoding="utf-8"))["expected"]["verdict"] == "legal"]
    assert len(legal) == 3
    for path in legal:
        case = json.loads(path.read_text(encoding="utf-8"))
        assert_clean_baseline(case, PACKS[case["spec"]["pack_id"]])


# ---------------------------------------------------------------------------
# 跨内容：样本包真的不同，机制仍然相同
# ---------------------------------------------------------------------------

def test_packs_span_real_content_differences():
    by_family: dict[str, list] = {}
    for pack in PACKS.values():
        by_family.setdefault(pack["family"], []).append(pack)
    assert set(by_family) == set(FAMILY_DISCRIMINATING)
    for family, group in by_family.items():
        assert len(group) >= 2, family
        assert len({p["novel"] for p in group}) == len(group), family
        assert len({p["place"] for p in group}) == len(group), family
        assert len({tuple(p["shot_indices"]) for p in group}) == len(group), family
    all_packs = list(PACKS.values())
    assert len({len(p["events"]) for p in all_packs}) >= 2, "需覆盖不同镜头数"
    assert len({len(p["on_screen_actors"]) for p in all_packs}) >= 2, "需覆盖不同实体数"
    assert len({tuple(p["durations"]) for p in all_packs}) >= 3, "需覆盖不同时长"
    assert len({p.get("prop") for p in all_packs}) >= 3, "需覆盖不同道具与无道具"


@pytest.mark.parametrize("pid", list(PACKS), ids=list(PACKS))
def test_same_mechanism_survives_content_change(pid):
    pack = PACKS[pid]
    case = build_baseline(pack)          # 内部已跑 I1–I9
    assert_clean_baseline(case, pack)
    assert not scan_script(case["script"])
    prompt = audit.build_audit_prompt(case["novel"], case["plan"], case["script"])
    for field in FAMILY_DISCRIMINATING[pack["family"]]:
        top = field.split(".")[0]
        assert any(path_exists(s, field) for s in case["script"]["shots"]), field
        assert '"%s"' % top in prompt, field
    assert pack["actors"]["main"] in prompt


@pytest.mark.parametrize("pid", list(PACKS), ids=list(PACKS))
def test_defect_is_a_causal_mutation_of_the_same_baseline(pid):
    pack = PACKS[pid]
    defect = build_defect(pack)          # 内部已断言「只改声明字段」
    assert defect["expected"]["verdict"] == "defect"
    codes = sorted({f["code"] for f in scan_script(defect["script"])})
    assert codes == sorted(pack["defect"]["expected_findings"]), (pid, codes)


def test_builder_contains_no_story_content():
    """机制层不得内置任何样本内容；否则换小说就等于改机制。"""
    src = (HERE / "narrative_family_builder.py").read_text(encoding="utf-8")
    story_terms = (
        "院中", "酒壶", "茶盏", "公文包", "堂内", "舱内", "站台",
        "值班室", "茶室", "次日清晨", "淡出黑场", "观测台", "记录板",
    )
    for term in story_terms:
        assert term not in src, "构造器内置了故事内容：%r" % term


def test_packs_file_is_self_contained():
    doc = json.loads(PACKS_PATH.read_text(encoding="utf-8"))
    assert doc["schema"] == "narrative_family_pack.v1"
    assert len(doc["packs"]) == len(PACKS)


# ---------------------------------------------------------------------------
# 换小说的实际动作：只新增一份 pack
# ---------------------------------------------------------------------------

def test_new_novel_needs_only_a_new_pack():
    case = build_baseline(NEW_NOVEL_PACK)
    assert_clean_baseline(case, NEW_NOVEL_PACK)
    assert not scan_script(case["script"])
    prompt = audit.build_audit_prompt(case["novel"], case["plan"], case["script"])
    assert "岚" in prompt and "观测台" in prompt
    assert "院中" not in prompt and "酒壶" not in prompt
    # 镜头编号不假设从 1 开始，也不假设固定时长。
    assert [s["index"] for s in case["script"]["shots"]] == [21, 22]
    assert [s["duration_sec"] for s in case["script"]["shots"]] == [9.0, 9.0]
    # 同一份新 pack 也能生成成对反例，且只改声明字段。
    defect = build_defect(NEW_NOVEL_PACK)
    assert defect["expected"]["verdict"] == "defect"
