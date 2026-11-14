# -*- coding: utf-8 -*-
"""`tools/script_preflight.py` 的判据回归（夹具驱动）.

数据全部来自 ``fixtures/script_preflight/packs/sample_packs.json``——匿名人物与通用道具，
不绑定任何具体作品；本文件只做「样例 → 运行 → 与预期逐条比对」，不含故事内容。
新增一类缺陷或换一章语料时**只加 pack**，本文件不改。

覆盖的口径（U6 S03 之前全靠审计模型才发现的 6 类 + 1 类原有判据）：

====================== ============================================ ======
code                   形态                                          严重度
====================== ============================================ ======
layout_presence_gap    关键帧「在—不在—在」且无离场证据                  hard
prop_chain_break       道具从有到无却无释放证据（镜内 / 跨镜断链又回来）    advisory
prop_chain_break       跨镜断链但此后不再出现（正常取舍）                  info
entity_class_conflict  音效与画面的乐器/载具不一致                        advisory
location_drift         镜内硬切到 location 之外的地点                     advisory
stage_progress_mismatch stage=pre 但描述已是完成态                     advisory
duplicate_injection    跨字段 24 字逐字重复（step02a 硬阻断）              hard
image_prompt_leak      图片提示词里混进运镜/时长/转场词                     advisory
====================== ============================================ ======
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import script_preflight as pf                       # noqa: E402
import script_preflight_builder as builder                     # noqa: E402

#: 本工具当前产出的全部 code；pack 的 scope 只能取这里的子集。
SCOPE_CODES = {
    "layout_presence_gap", "prop_chain_break", "entity_class_conflict",
    "location_drift", "stage_progress_mismatch", "duplicate_injection",
    "image_prompt_leak",
}

#: 台账口径：只有 hard / advisory 进告警，info 只作解释、不参与「有没有问题」。
_ALERT = {"hard", "advisory"}

#: {pack 文件: JSON 规范化文本 sha256[:16]}——受控夹具只读，改语料必须同步改摘要。
FROZEN = {
    "packs/sample_packs.json": "0d804ed69a02517c",
}


def _canonical_digest(path: Path) -> str:
    raw = json.dumps(json.loads(path.read_text(encoding="utf-8")),
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cases() -> list[tuple[str, str]]:
    return builder.iter_cases()


def _ids() -> list[str]:
    return ["%s::%s" % (pack_id, case_id) for pack_id, case_id in _cases()]


def _scoped(script: dict, scope: set[str]) -> list[tuple]:
    """只保留该 pack 所属判据族的问题，按 (code, shot, frame, severity) 归一."""
    out = []
    for issue in pf.run(script):
        if issue.get("code") not in scope:
            continue
        out.append((issue["code"], str(issue.get("shot")), str(issue.get("frame") or ""),
                    str(issue.get("severity"))))
    return sorted(out)


def _expected(issues: list[dict]) -> list[tuple]:
    return sorted((i["code"], str(i["shot"]), str(i.get("frame") or ""), i["severity"])
                  for i in issues)


# ---------------------------------------------------------------------------
# 1. 受控夹具只读
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel,digest", sorted(FROZEN.items()))
def test_fixture_is_frozen(rel, digest):
    assert _canonical_digest(builder.FIXTURES / rel) == digest, (
        "样本包已被改写；确需更新请由构造器重建并同步本文件的 FROZEN 摘要")


# ---------------------------------------------------------------------------
# 2. 样本包自身合法（结构 / 内容无关 / 变异因果）
# ---------------------------------------------------------------------------

def test_every_pack_scope_is_a_known_code():
    for pack_id, pack in builder.load_packs().items():
        unknown = set(pack["scope"]) - SCOPE_CODES
        assert not unknown, "pack %s 声明了工具不会产出的 code：%s" % (pack_id, unknown)


def test_every_case_is_content_agnostic():
    for pack_id, case_id in _cases():
        pack = builder.load_packs()[pack_id]
        builder.build_case(pack, case_id)  # 构造器内部逐例复核黑名单


def test_every_case_mutates_only_declared_fields():
    for pack_id, case_id in _cases():
        pack = builder.load_packs()[pack_id]
        case = next(c for c in pack["cases"] if c["id"] == case_id)
        script = builder.apply_patches(builder.build_baseline(pack), case.get("patches"))
        builder.assert_case_is_causal(pack, case, script)


# ---------------------------------------------------------------------------
# 3. 判据：每个 pack 的 legal 变体零 finding，defect 变体命中预期
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pack_id,case_id", _cases(), ids=_ids())
def test_pack_case_matches_expectation(pack_id, case_id):
    pack = builder.load_packs()[pack_id]
    case = builder.build_case(pack, case_id)
    got = _scoped(case["script"], set(pack["scope"]))
    assert got == _expected(case["expected"]["issues"]), (
        "pack %s case %s（%s）：%s\n预期 %s\n实际 %s"
        % (pack_id, case_id, case["kind"], case["why"],
           _expected(case["expected"]["issues"]), got))


def test_every_family_has_both_legal_and_defect_variant():
    """每族都要有「不报」与「报」两侧，只有一侧的族等于没校准阈值。"""
    packs = builder.load_packs()
    families: dict[str, set[str]] = {}
    for pack in packs.values():
        kinds = families.setdefault(pack["code"], set())
        kinds.update(c["kind"] for c in pack["cases"])
    assert set(families) == SCOPE_CODES
    missing = {code: sorted({"legal", "defect"} - kinds)
               for code, kinds in families.items() if kinds != {"legal", "defect"}}
    assert not missing, "以下判据缺对照侧：%s" % missing


def test_defect_variant_is_not_silently_clean():
    """defect 变体若在 scope 内一条都不出，说明判据退化；逐族钉住."""
    for pack_id, case_id in _cases():
        pack = builder.load_packs()[pack_id]
        case = builder.build_case(pack, case_id)
        if case["kind"] != "defect":
            continue
        got = _scoped(case["script"], set(pack["scope"]))
        assert got, "pack %s case %s 标了 defect 却零 finding" % (pack_id, case_id)


def test_info_is_not_counted_as_alert():
    """「本镜没再用某道具」记 info：要能看见，但不能进告警台账。"""
    pack = builder.load_packs()["prop_cross_shot_resume"]
    case = builder.build_case(pack, "noise_not_reused")
    issues = pf.run(case["script"])
    info = [i for i in issues if i.get("severity") == "info"]
    assert info and all(i["code"] == "prop_chain_break" for i in info)
    assert not [i for i in issues if i.get("severity") in _ALERT
                and i["code"] in pack["scope"]]


# ---------------------------------------------------------------------------
# 4. 判据是纯函数：不写回输入、不依赖调用顺序
# ---------------------------------------------------------------------------

def test_run_does_not_mutate_script():
    pack = builder.load_packs()["presence_peak_frame_drop"]
    script = builder.build_case(pack, "defect_exit_evidence_removed")["script"]
    before = json.dumps(script, ensure_ascii=False, sort_keys=True)
    pf.run(script)
    assert json.dumps(script, ensure_ascii=False, sort_keys=True) == before


def test_run_is_order_independent():
    """同一 script 连跑两次结果一致（判据不得记忆上一次的中间状态）."""
    pack = builder.load_packs()["prop_cross_shot_resume"]
    script = builder.build_case(pack, "defect_gap_then_resume")["script"]
    first = _scoped(script, set(pack["scope"]))
    second = _scoped(script, set(pack["scope"]))
    assert first == second
