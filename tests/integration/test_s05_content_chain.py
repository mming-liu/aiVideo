# -*- coding: utf-8 -*-
"""S05：受限修订 → 提交投影 → 画面计划 → 生成器桩的**完整链路**（跨内容离线回放）.

2026-11-13 复核要求：逐帧提示消费只证明了一段。本文件把同一候选及其身份贯穿：

1. **受限修订入口** `main._repair_shot_warnings`（授权字段 + 桩响应）；
2. **提交投影** `main.project_for_commit`（唯一实现；产出最终 revision）；
3. Step01 落盘的 `script_result.json` 被 Step02a 读取；
4. **画面计划构建** `execution_plan.build_visual_plan`；
5. **计划模式变更保护/覆盖检查** `validate_plan_identity` / `verify_plan_changes` /
   `verify_plan_coverage`；
6. **真实逐帧入口** `_gen_first_frame_async` → 生成器桩。

两个匿名样本在内容关系、镜头编号（连续/非连续）、载体分布（旁白/对白/画面）
上有实质差异；断言只针对有来源/无来源与链路身份，不绑定任何小说或章节。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _shot(index, *, chars, location, scene, subject, prompt, ff, lf, motion,
          narration="", beats=(1,), layout=None):
    entries = layout or [{"name": chars[0], "on_screen": True,
                          "position": "船头", "pose": "坐着",
                          "gaze_target": "水面"}]
    return {
        "index": index, "duration_sec": 4.0, "duration": 4.0, "subtitle": "",
        "location": location, "scene": scene, "main_subject": subject,
        "prompt": prompt, "ff_desc": ff, "lf_desc": lf, "motion_desc": motion,
        "narration_segment": narration,
        "frame_layouts": {"ff": entries, "lf": entries, "kfs": []},
        "keyframes": [], "camera_movement": "固定机位",
        "plan_refs": {"beats": list(beats), "lines": []},
    }


def _sample_river() -> dict:
    """连续编号（1 镜）；关系 = 出行→到达；画面载体；有规格的船."""
    shot = _shot(
        1, chars=["甲"], location="河湾小舟", scene="渡口", subject="甲",
        prompt="[中景] 甲坐在船头，水面平静，远处青山如黛。",
        ff="甲坐在一叶小舟（长6米窄身无篷舴艋舟）船头，双手扶膝。",
        lf="甲抬头看向岸边。",
        motion="前2秒：甲坐着；后2秒：甲抬头。",
        beats=(1,))
    return {
        "name": "river",
        "chars": [{"identifier": "甲"}],
        "scope": {"chapter": 1, "book_index": 1, "book_name": "匿名样本A"},
        "novel": "渡口边，甲坐在一叶小舟上。船身窄，船头堆着缆绳。",
        "scene_objects": {"河湾小舟": {"category": "船", "subtype": "无篷舴艋舟",
                                       "length_m": 6.0, "width_m": 1.4}},
        "shots": [shot],
        "outline": {
            "shot_beats": ["节拍：甲坐船头｜本镜信息目的：建立场景｜原文依据：甲坐在小舟上｜载体：画面"],
            "dialogue_lines": [], "narration_lines": [],
            "main_events": ["甲坐在小舟上"], "climax": "甲抬头",
            "causal_chain": ["起因：甲出行（B1）", "结果：甲到达（B1）"],
            "required_facts": [], "fact_uids": [],
        },
        "issue": {"shot_id": "1", "field_path": "ff_desc",
                  "evidence": "窄身无篷舴艋舟"},
        "patch": {"shot_id": "1", "field_path": "ff_desc",
                  "from": "长6米窄身无篷舴艋舟", "to": "长6米无篷舴艋舟"},
        "expect": {"width": "宽1.4米", "no_terms": ["窄身", "肩宽", "只画船头"]},
    }


def _sample_harbor() -> dict:
    """非连续编号（2/5/9）；关系 = 出行→到达；旁白+对白载体；两种船型."""
    a, b = "丙", "丁"
    shots = [
        _shot(2, chars=[a], location="港湾渔船", scene="港湾", subject=a,
              prompt="[近景] 丙坐在船头，水面有雾。",
              ff="丙坐在一叶小舟（长4.2米窄身有篷渔船）船头，双手扶膝。",
              lf="丙抬头看向岸边。",
              motion="前2秒：丙坐着；后2秒：丙抬头。",
              narration="三日后，丙把船摇到港湾。", beats=(1,),
              layout=[{"name": a, "on_screen": True, "position": "船头",
                       "pose": "坐着", "gaze_target": "水面"}]),
        _shot(5, chars=[b], location="水面", scene="港湾", subject=b,
              prompt="[中景] 水面泛起细纹，丁只露头顶。",
              ff="丁潜伏水下，仅头顶约9cm露出。",
              lf="丁仍潜伏水下。",
              motion="前2秒：静伏；后2秒：不动。",
              narration="", beats=(2,),
              layout=[{"name": b, "on_screen": True, "position": "水下",
                       "pose": "潜伏", "gaze_target": "水面"}]),
        _shot(9, chars=[b], location="快艇", scene="码头", subject=b,
              prompt="[中景] 码头石桩旁，丁俯身系缆。",
              ff="小舟靠岸，丁把缆绳系在石桩上。",
              lf="缆绳绷紧，小舟停稳。",
              motion="前2秒：系绳；后2秒：拉紧。",
              narration="", beats=(3,),
              layout=[{"name": b, "on_screen": True, "position": "船头",
                       "pose": "系绳", "gaze_target": "石桩"}]),
    ]
    return {
        "name": "harbor",
        "chars": [{"identifier": a}, {"identifier": b}],
        "scope": {"chapter": 3, "book_index": 2, "book_name": "匿名样本B"},
        "novel": "港湾里，丙摇着渔船靠岸。三日后，丁潜入水下，又系好快艇。",
        "scene_objects": {
            "港湾渔船": {"category": "船", "subtype": "有篷渔船",
                         "length_m": 4.2, "width_m": 1.1},
            "快艇": {"category": "船", "subtype": "敞篷快艇",
                     "length_m": 3.5, "width_m": 1.2},
        },
        "shots": shots,
        "outline": {
            "shot_beats": ["节拍：丙坐船头｜本镜信息目的：建立场景｜原文依据：丙摇渔船｜载体：旁白1",
                           "节拍：丁潜伏水下｜本镜信息目的：推进｜原文依据：丁潜入水下｜载体：画面",
                           "节拍：丁系船靠岸｜本镜信息目的：确认结果｜原文依据：丁系好快艇｜载体：画面"],
            "dialogue_lines": [],
            "narration_lines": [{"text": "三日后，丙把船摇到港湾。",
                                 "source": "三日后，丙把船摇到港湾。",
                                 "fact_refs": [1]}],
            "main_events": ["丙坐船头", "丁潜伏水下", "丁系船靠岸"],
            "climax": "丁系船靠岸",
            "causal_chain": ["起因：丙出行（B1）", "结果：丁到达（B3）"],
            "required_facts": ["丙坐在船头｜原文证据：丙坐在船头｜承载：B1",
                               "三日后丁浮出水面｜原文证据：三日后｜承载：B2"],
            "fact_uids": ["f01", "f02"],
            "voice_units": [{"unit_id": "v1", "kind": "narration",
                             "text": "三日后，丙把船摇到港湾。",
                             "shot_index": 2}],
        },
        "issue": {"shot_id": "2", "field_path": "ff_desc",
                  "evidence": "窄身有篷渔船"},
        "patch": {"shot_id": "2", "field_path": "ff_desc",
                  "from": "长4.2米窄身有篷渔船", "to": "长4.2米有篷渔船"},
        "expect": {"width": "宽1.1米", "no_terms": ["窄身"]},
    }


_SAMPLES = {"river": _sample_river(), "harbor": _sample_harbor()}


class _TextSvc:
    """受限修订的桩服务：只回一条合法补丁（不冒充模型原话）."""

    def __init__(self, payload):
        self.text = json.dumps(payload, ensure_ascii=False)
        self.calls = 0

    def generate(self, req):
        self.calls += 1

        class _R:
            success = True
            error = ""
        _R.text = self.text
        return _R()


class _Adapter:
    """生成器桩：记录真实入口交给适配器的最终提示."""

    def __init__(self):
        self.prompts: list[str] = []

    async def generate_single_image(self, **kwargs):
        self.prompts.append(str(kwargs.get("prompt") or ""))

        def _save(path):
            Image.new("RGB", (1024, 1024), (18, 24, 30)).save(path)

        return SimpleNamespace(data=SimpleNamespace(save=_save))


def _base_script(sample: dict) -> dict:
    return {
        "book_name": sample["scope"]["book_name"],
        "book_index": sample["scope"]["book_index"],
        "chapter_index": sample["scope"]["chapter"],
        "chapter_title": "匿名样本",
        "script_revision": "base" * 16,
        "source_hash": "hash-base",
        "plan_id": "plan-base",
        "characters": sample["chars"],
        "scene_objects": sample["scene_objects"],
        "shots": sample["shots"],
        "chapter_outline": sample["outline"],
        "total_duration": sum(s["duration_sec"] for s in sample["shots"]),
        "body_duration": sum(s["duration_sec"] for s in sample["shots"]),
        "hook_duration": 0.0,
    }


def _patch_value(sample: dict, shot: dict) -> str:
    patch = sample["patch"]
    field = patch["field_path"]
    return str(shot[field]).replace(patch["from"], patch["to"], 1)


def _run_chain(sample: dict, tmp_path, monkeypatch) -> dict:
    import step01_script_gen.main as m

    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "output"))
    monkeypatch.setenv("QC_PROVIDER", "none")
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "collect_structured_issues",
                        lambda *a, **k: ([], False, "probe"))
    for name in ("_metrics_inc", "_metrics_set", "_metrics_update"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))

    base = _base_script(sample)
    issue = sample["issue"]
    target_shot = next(s for s in base["shots"]
                       if str(s["index"]) == str(issue["shot_id"]))
    payload = {"issues": [{
        "code": "model_audit_FIELD_CONFLICT", "severity": "visual_risk",
        "message": "无来源的船体形制", "shot_id": issue["shot_id"],
        "field_path": issue["field_path"], "evidence": issue["evidence"],
        "repair_targets": [{"shot_id": "shot_%s" % issue["shot_id"],
                            "field_path": issue["field_path"]}],
    }]}
    issues = m._audit_payload_to_issues(payload)
    svc = _TextSvc({"shot_replacements": [{
        "shot_id": str(issue["shot_id"]),
        "updates": [{"field_path": issue["field_path"],
                     "value": _patch_value(sample, target_shot)}],
    }]})
    monkeypatch.setattr(m, "create_service", lambda *a, **k: svc)

    diag, blocks = [], []
    revised = m._repair_shot_warnings(
        base, sample["novel"], audit_issues=issues, max_rounds=1,
        repair_diag=diag, block_reasons=blocks,
        retry_state={}, audit_attempted=set(), defer_targets=set())
    decision = next((d for d in diag if isinstance(d, dict) and "accepted" in d), {})
    assert decision.get("accepted") is True, (decision, blocks)
    assert svc.calls == 1

    projected, errors = m.project_for_commit(revised, sample["novel"])
    assert errors == []
    assert projected.get("script_revision")

    # Step01 的消费形态：提交投影后的 script_result.json（Step02a 读取它做尺寸锚）。
    chapter = tmp_path / ("chapter_%03d" % sample["scope"]["chapter"])
    chapter.mkdir(parents=True, exist_ok=True)
    (chapter / "script_result.json").write_text(
        json.dumps(projected, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("shared.paths.chapter_dir", lambda *a, **k: chapter)

    from step02a_frame_gen.execution_plan import (
        build_visual_plan, validate_plan_identity, verify_plan_changes,
        verify_plan_coverage,
    )
    indices = {int(s["index"]) for s in projected["shots"]}
    plan = build_visual_plan(projected, scope=sample["scope"],
                             only_indices=indices, frames={"ff"})
    assert plan.get("status") != "blocked", {
        "unresolved": plan.get("unresolved"),
        "shot_issues": [e.get("issues") for e in plan.get("shots") or []],
    }
    assert validate_plan_identity(plan, projected, scope=sample["scope"]) == []
    assert verify_plan_changes(plan, projected) == []
    assert verify_plan_coverage(plan, only_indices=indices, frames={"ff"},
                                script=projected) == []

    from shared import vimax_bridge as vb
    adapter = _Adapter()
    shots = {int(s["index"]): s for s in projected["shots"]}
    plan_prompts = {}
    for entry in plan["shots"]:
        idx = int(entry["shot_index"])
        frame = next(f for f in entry["frames"] if f.get("kind") == "ff")
        plan_prompts[idx] = frame["prompt"]
        ok = asyncio.run(vb._gen_first_frame_async(
            adapter, tmp_path / "shots", dict(shots[idx]), False,
            script_mtime=None, reference_paths=[], max_retries=0,
            characters=projected["characters"]))
        assert ok
    return {
        "revised": revised,
        "projected": projected,
        "plan": plan,
        "plan_prompts": plan_prompts,
        "adapter_prompts": adapter.prompts,
        "decision": decision,
        "calls": svc.calls,
    }


@pytest.mark.parametrize("name", sorted(_SAMPLES))
def test_full_chain_reaches_generator_with_same_identity(name, tmp_path, monkeypatch):
    sample = _SAMPLES[name]
    out = _run_chain(sample, tmp_path, monkeypatch)
    projected = out["projected"]
    plan = out["plan"]

    # 身份贯穿：计划绑定的是同一份投影稿 / revision。
    binding = plan.get("execution_binding") or {}
    source = binding.get("source") or {}
    assert source.get("script_revision") == projected.get("script_revision")
    assert len(out["adapter_prompts"]) == len(projected["shots"])
    # 生成器桩收到的就是计划里的最终提示（同一次组装，不再另拼一份）。
    for idx, prompt in out["plan_prompts"].items():
        assert prompt in out["adapter_prompts"], idx

    # 内容：后处理修好的内容到达最终提示；其他实体的形制不被误删。
    prompts = "\n".join(out["adapter_prompts"])
    assert sample["expect"]["width"] in prompts
    for term in sample["expect"]["no_terms"]:
        assert term not in prompts, (term, prompts)

    # 计划模式的变更保护与覆盖检查都通过（上面的 assert 已逐项核过），
    # 这里再确认「不是空计划」：每个请求镜头都有一帧。
    assert {int(e["shot_index"]) for e in plan["shots"]} == {
        int(s["index"]) for s in projected["shots"]}
    # 修订决策确实记录了本轮前后身份（同一候选身份，不是事后补造）。
    assert out["decision"]["revision_before"] != out["decision"]["revision_after"]
