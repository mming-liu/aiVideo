"""S02：审计输入补齐衔接上下文 + 时间省略判据（2026-11-11）.

离线只证明三件事：
  1. 四个衔接字段**真的进了**实际构建出的审计提示；
  2. 字段缺失时渲染为 `null`（未知），而不是被补成某种时间关系；
  3. 判据文本明确区分「合法时间省略」与「信息缺失/状态矛盾」，且合法硬切不再被要求改写。

模型是否照此判断，属于 S06 的真实调用对照，不在本文件声称。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from step01_script_gen import audit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "audit_narrative_families"
CONTEXT_FIELDS = ("scene", "location", "clip_transition", "context_bridge")


def _cases() -> list[dict]:
    out = []
    for path in sorted(FIXTURES.glob("*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def test_compact_fields_carry_narrative_context():
    for field in CONTEXT_FIELDS:
        assert field in audit._COMPACT_SHOT_FIELDS, field


def test_prompt_contains_context_fields_and_unknown_renders_null():
    case = json.loads((FIXTURES / "time_ellipsis_legal.json").read_text(encoding="utf-8"))
    prompt = audit.build_audit_prompt(case["novel"], case["plan"], case["script"])
    # 断言从夹具字段派生，不写死任何具体故事文本（AGENTS.md「内容无关性与夹具」）。
    # 换小说/章节时只需换样本包，本用例不需要跟着改字符串。
    shot = next(s for s in case["script"]["shots"] if s["context_bridge"])
    for field in ("scene", "clip_transition", "context_bridge"):
        rendered = json.dumps(shot[field], ensure_ascii=False)
        assert '"%s": %s' % (field, rendered) in prompt, field
    # 缺字段 → null（未知），不得补造时间关系
    bare = {"shots": [{"index": 1, "duration_sec": 5.0, "ff_desc": "甲站立"}],
            "total_duration": 5.0}
    bare_prompt = audit.build_audit_prompt("匿名原文", {}, bare)
    assert '"scene": null' in bare_prompt
    assert '"context_bridge": null' in bare_prompt


def test_rules_state_time_ellipsis_and_evidence_burden():
    prompt = audit.build_audit_prompt("匿名原文", {}, {"shots": [], "total_duration": 0})
    # 缺失即未知：不得据此推断「紧接着」
    assert "缺失即未知" in prompt
    assert "不得据此推断「紧接着」" in prompt
    # 合法省略 + 举证责任
    assert "合法省略" in prompt
    assert "观众缺少的是哪一项**理解剧情所必需**的信息" in prompt
    # 状态冲突先核时间关系
    assert "报跨镜状态矛盾前，先核对时间关系与状态变化依据" in prompt
    # 转场是剪辑表达：硬切合法，淡入淡出不能证明因果
    assert "硬切是合法表达" in prompt
    assert "不能证明" in prompt and "淡入淡出" in prompt


def test_prompt_version_covers_compact_field_scope(monkeypatch):
    """送审字段范围一变，审计版本身份必须失效（否则旧结论给新请求盖章）."""
    before = audit.audit_prompt_version()
    monkeypatch.setattr(audit, "_COMPACT_SHOT_FIELDS",
                        tuple(audit._COMPACT_SHOT_FIELDS) + ("new_field",))
    assert audit.audit_prompt_version() != before


def test_prompt_version_covers_time_ellipsis_rules(monkeypatch):
    before = audit.audit_prompt_version()
    monkeypatch.setattr(audit, "_AUDIT_RULES_TEMPLATE",
                        audit._AUDIT_RULES_TEMPLATE + "\n- 追加一条判据")
    assert audit.audit_prompt_version() != before


def test_each_family_pairs_legal_with_a_real_counterexample():
    cases = _cases()
    families: dict = {}
    for case in cases:
        families.setdefault(case["family"], []).append(case)
    assert set(families) == {"time_ellipsis", "state_contradiction", "offscreen_voice"}
    for family, group in families.items():
        verdicts = sorted(c["expected"]["verdict"] for c in group)
        assert verdicts == ["defect", "legal"], (family, verdicts)
        # 反例必须是**真正改变关键关系**的那一类，不是纯审美
        broken = [c for c in group if c["expected"]["verdict"] == "defect"][0]
        assert broken["expected"]["defect_class"] in ("contradiction", "unclear"), family
        assert broken["expected"]["must_preserve"], family


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["case_id"])
def test_sample_really_carries_its_discriminating_fields(case):
    """样本自证：声明的判别字段必须是分镜里真实存在的路径（不是空口声明）."""
    from step01_script_gen.audit_adjudication import path_exists

    shots = case["script"]["shots"]
    for field in case["expected"]["discriminating"]:
        assert any(path_exists(shot, field) for shot in shots), (case["case_id"], field)
    # 预期裁决与依据都必须写清，S02/S03 才能消费同一份冻结预期
    assert case["expected"]["why"].strip()
    assert case["expected"]["must_preserve"]


def test_family_samples_are_content_agnostic():
    """样本只用人称代词与通用场所，不绑定任何具体作品."""
    blob = "".join(json.dumps(c, ensure_ascii=False) for c in _cases())
    for forbidden in ("锦衣夜行", "杨旭", "听香", "张十三", "杀青州"):
        assert forbidden not in blob
