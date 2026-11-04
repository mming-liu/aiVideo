from __future__ import annotations
import sys
from pathlib import Path
_ROOT=Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path: sys.path.insert(0,str(_ROOT))

def test_image_quality_reports_video_leak(monkeypatch,tmp_path):
    import step02a_frame_gen.image_prompt_quality as _q
    collect_image_prompt_quality = _q.collect_image_prompt_quality
    # 模块内是 `from shared.paths import chapter_dir`，必须钉模块属性，
    # 否则干净检出里会写到不存在的生产章目录（本机靠历史目录才“碰巧通过”）。
    monkeypatch.setattr(_q, 'chapter_dir', lambda: tmp_path)
    script={'shots':[{'index':1,'ff_desc':'中景，镜头缓慢推近，杨旭站立','lf_desc':'杨旭转身','keyframes':[]}]}
    r=collect_image_prompt_quality(script)
    assert r['warning_count']==1
    assert r['warnings'][0]['category']=='image_video_directive_leak'

def test_final_prompt_lint_flags_s25_markers():
    import step02a_frame_gen.image_prompt_quality as q
    text = ("无篷舴艋舟，young男子，披头散发，"
            + "船板中央" * 3 + "x" * 900)
    cats = {w["category"] for w in q._lint_final_prompt(
        text, {"ff_desc": "平静湖面"}, "first_frame")}
    assert "image_final_prompt_negated_object" in cats
    assert "image_final_prompt_untranslated_age" in cats
    assert "image_final_prompt_stale_state" in cats
    assert "image_final_prompt_repeated_phrase" in cats
    assert "image_final_prompt_length" in cats


def test_final_prompt_offscreen_entity_warning(monkeypatch, tmp_path):
    import step02a_frame_gen.image_prompt_quality as _q
    monkeypatch.setattr(_q, "chapter_dir", lambda: tmp_path)
    script = {
        "characters": [{"identifier": "甲"}, {"identifier": "乙"}],
        "shots": [{
            "index": 1, "location": "湖湾",
            "ff_desc": "甲站在船板上，乙在远处岸上。",
            "lf_desc": "甲站在船板上。",
            "frame_layouts": {
                "ff": [{"name": "甲", "on_screen": True, "position": "船板", "pose": "站在船板上"}],
                "lf": [{"name": "甲", "on_screen": True, "position": "船板", "pose": "站在船板上"}],
            },
        }],
    }
    r = _q.collect_image_prompt_quality(script, write=False)
    cats = [w["category"] for w in r["warnings"]]
    assert "image_final_prompt_offscreen_entity" in cats


def test_image_quality_clean(monkeypatch,tmp_path):
    import step02a_frame_gen.image_prompt_quality as _q
    collect_image_prompt_quality = _q.collect_image_prompt_quality
    monkeypatch.setattr(_q, 'chapter_dir', lambda: tmp_path)
    script={'shots':[{'index':1,'ff_desc':'中景，杨旭站立','lf_desc':'杨旭转身','keyframes':[]}]}
    assert collect_image_prompt_quality(script)['warning_count']==0

def test_image_product_acceptance_uses_root_keyframe_paths(monkeypatch, tmp_path):
    import step02a_frame_gen.image_prompt_quality as q
    work = tmp_path / "working"
    shot_dir = work / "shots" / "1"
    shot_dir.mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (8, 8), "white").save(shot_dir / "first_frame.png")
    Image.new("RGB", (8, 8), "white").save(shot_dir / "last_frame.png")
    Image.new("RGB", (8, 8), "white").save(shot_dir / "keyframe_0.png")
    monkeypatch.setattr(q, "chapter_dir", lambda: tmp_path)
    # 固定「当前视频模型消费关键帧」，避免断言随 .env 的 VIDEO_PROVIDER/型号漂移
    monkeypatch.setattr(q, "_keyframes_consumed_by_video", lambda: True)
    r = q.write_image_product_quality(
        {"shots": [{"index": 1, "keyframes": [{"desc": "k"}]}]}, work)
    assert r["status"] == "PASS"
    assert "keyframe_0.png" in r["outputs"]["1"]["keyframes"]


def test_image_product_acceptance_blocks_missing_required_keyframe(monkeypatch, tmp_path):
    import step02a_frame_gen.image_prompt_quality as q
    from PIL import Image
    work = tmp_path / "working"
    shot_dir = work / "shots" / "1"
    shot_dir.mkdir(parents=True)
    Image.new("RGB", (8, 8), "white").save(shot_dir / "first_frame.png")
    Image.new("RGB", (8, 8), "white").save(shot_dir / "last_frame.png")
    monkeypatch.setattr(q, "chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(q, "_keyframes_consumed_by_video", lambda: True)
    r = q.write_image_product_quality(
        {"shots": [{"index": 1, "keyframes": [{"desc": "k"}]}]}, work)
    assert r["status"] == "BLOCK"
    # v6.110：判据从「核心帧数量」改为「按序号逐一核对」，文案随之变化
    assert any("关键帧" in m and "[0]" in m for m in r["missing"])
