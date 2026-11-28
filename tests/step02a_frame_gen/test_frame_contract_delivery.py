"""Frame contract regressions: self-contained inputs, offline real entry points."""
from __future__ import annotations

import copy
import json

import pytest
from PIL import Image


def sample(index=41, names=("甲", "乙"), place="维修舱"):
    chars = [{"identifier": n, "static_features": "短发青年，灰色外衣"} for n in names]
    entries = [{"name": n, "on_screen": True, "position": "画中", "pose": "坐着",
                "props": [], "states": ["左袖湿透"] if i == 0 else [],
                "support": {"id": "seat", "kind": "长椅", "surface": "坐面"}}
               for i, n in enumerate(names)]
    shot = {"index": index, "main_subject": names[-1], "scene": place, "location": place,
            "ff_desc": "维修员坐着，助手扶着扶手。", "lf_desc": "维修员起身，助手扶着扶手。",
            "motion_desc": "维修员起身。", "prompt": "两人在维修舱交谈。",
            "frame_layouts": {"ff": entries, "lf": copy.deepcopy(entries),
                              "kfs": [copy.deepcopy(entries)]},
            "keyframes": [{"desc": "维修员起身。"}]}
    return shot, chars


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    monkeypatch.setenv("POST_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "auto")


@pytest.mark.parametrize("names,index", [(("甲", "乙"), 41), (("丙", "丁", "戊"), 108)])
def test_prompt_roster_and_state_follow_contract(names, index):
    from shared.vimax_bridge import compose_frame_prompt
    shot, chars = sample(index, names)
    prompt = compose_frame_prompt(shot, chars, "first_frame")
    roster = prompt.split("【人物数量限制】")[-1]
    assert all(n in roster for n in names)
    assert f"总共{len(names)}人" in roster
    assert "左袖湿透" in prompt


def test_portraits_do_not_require_names_in_prose(tmp_path, monkeypatch):
    from shared import vimax_bridge as bridge
    from shared import paths
    shot, chars = sample()
    registry = {}
    for c in chars:
        p = tmp_path / (c['identifier'] + '.png')
        Image.new("RGB", (24, 24), "blue").save(p)
        registry[c['identifier']] = {"front": {"path": str(p)}}
    (tmp_path / 'character_portraits_registry.json').write_text(
        json.dumps(registry), encoding='utf-8')
    monkeypatch.setattr(paths, "vimax_working_dir", lambda: tmp_path)
    monkeypatch.setattr(bridge, "_head_portrait_path", lambda p: p)
    refs = bridge._collect_portrait_refs_for_shot(
        shot, chars, frame_desc=shot['ff_desc'], roster={"甲", "乙"})
    assert set(refs) == {v['front']['path'] for v in registry.values()}


def test_product_report_cannot_pass_unchecked_frames(tmp_path):
    from step02a_frame_gen.image_prompt_quality import build_image_product_quality
    shot, chars = sample()
    d = tmp_path / 'shots' / str(shot['index'])
    d.mkdir(parents=True)
    for role in ('first_frame', 'last_frame'):
        Image.new('RGB', (24, 24), 'blue').save(d / (role + '.png'))
        (d / (role + '_qc_log.json')).write_text(
            json.dumps([{'has_hallucination': False}]), encoding='utf-8')
    result = build_image_product_quality({'shots': [shot], 'characters': chars}, tmp_path,
                                        require_keyframes=False, media_policy='auto')
    assert result['status'] == 'BLOCK'
    assert result['issues_by_shot'][str(shot['index'])]
