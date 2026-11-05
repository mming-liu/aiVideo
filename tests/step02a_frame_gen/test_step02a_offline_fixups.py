"""S11–S13 离线回归：风格动态注入 / ff-lf 参考同源 / 字节哈希 / 二修守卫 / 关键帧策略.

这些用例只碰纯计算与 tmp_path 内的假图，不读生产 output、不发外部请求。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _png(path: Path, color=(0, 0, 0)) -> str:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color).save(path)
    return str(path)


# ---------------------------------------------------------------------------
# S11：风格随 VIDEO_STYLE 动态注入，画幅由调用方决定
# ---------------------------------------------------------------------------

def test_style_prefix_follows_video_style_and_canvas(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "ink_painting")
    from shared.style_prefix import style_prefix_for_canvas, style_lock_prompt

    frame = style_prefix_for_canvas("9:16")
    assert "16:9" not in frame
    assert "9:16画幅" in frame
    assert "水墨" in frame

    lock = style_lock_prompt()
    assert "水墨" in lock
    assert "写实古风真人" not in lock


def test_style_lock_keeps_gufeng_ban_list(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    from shared.style_prefix import style_lock_prompt

    lock = style_lock_prompt()
    assert "古风真人" in lock
    assert "动漫" in lock


def test_asset_prefix_keeps_landscape_ratio(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "ink_painting")
    from shared.style_prefix import asset_style_prefix

    asset = asset_style_prefix("16:9")
    assert "16:9画幅" in asset
    assert "电影宽幅" not in asset


def test_negated_boat_rewrites_remove_canopy_words():
    from shared.style_prefix import preprocess_prompt_for_image, image_negative_prompt

    out = preprocess_prompt_for_image("无篷舴艋舟，无顶棚院落，无顶棚船")
    assert "无篷" not in out
    assert "敞口平甲板木质舴艋小舟" in out
    assert "无顶棚院落" in out  # 非船物体不得被改成“甲板”
    assert "敞口无遮挡船" in out
    assert "乌篷" not in image_negative_prompt("anime")  # 不再全局压制有篷船


def test_fixed_camera_longitudinal_shift_anchor():
    from shared.vimax_bridge import _fixed_camera_shift_anchor

    # 旧启发式会凭“驶入/深处/行至”猜 Y 轴；S25 只认显式屏幕方向。
    assert _fixed_camera_shift_anchor("无篷小舟缓缓驶入荷塘深处") == ""
    assert _fixed_camera_shift_anchor("无篷小舟已行至开阔水面") == ""
    assert "纵向" in _fixed_camera_shift_anchor("小舟沿画面上方驶去")
    assert "横向" in _fixed_camera_shift_anchor("小舟横向驶过画面")
    assert "靠近" in _fixed_camera_shift_anchor("小舟迎面驶近")
    assert _fixed_camera_shift_anchor("人物向画面上方走去") == ""


def test_canvas_ratio_reduces_square_and_landscape():
    from shared.vimax_adapters import _canvas_ratio

    assert _canvas_ratio("1440x2560") == "9:16"
    assert _canvas_ratio("2560x1440") == "16:9"
    assert _canvas_ratio("1024x1024") == "1:1"
    assert _canvas_ratio("1600x1200") == "4:3"
    assert _canvas_ratio("1080*1920") == "9:16"
    with pytest.raises(ValueError):
        _canvas_ratio("2K")
    with pytest.raises(ValueError):
        _canvas_ratio("0x2560")


def test_style_profile_uses_requested_canvas():
    from shared.style_prefix import style_profile

    anime = style_profile("anime", ratio="1:1")
    assert anime.prefix.endswith("1:1画幅")
    assert "真人写实照片" in anime.negative

    ink = style_profile("ink_painting", ratio="16:9")
    assert ink.prefix.endswith("16:9画幅")
    assert ink.negative != anime.negative


def test_video_request_prefix_and_negative_follow_style(monkeypatch):
    from step02_video_gen.prompt_builder import _make_request

    monkeypatch.setenv("VIDEO_PROVIDER", "bailian")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "text")
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "auto")
    shot = {"index": 1, "duration_sec": 5, "prompt": "甲坐船",
            "ff_desc": "甲坐船", "lf_desc": "甲坐船", "keyframes": []}

    monkeypatch.setenv("VIDEO_STYLE", "anime")
    anime = _make_request(None, "甲坐船", [], shot, characters=[])
    monkeypatch.setenv("VIDEO_STYLE", "ink_painting")
    ink = _make_request(None, "甲坐船", [], shot, characters=[])

    assert "高清国漫二次元漫剧画风" in anime.prompt
    assert "东方写意水墨动画" in ink.prompt
    assert anime.negative_prompt != ink.negative_prompt


def test_frame_hash_changes_when_style_changes(monkeypatch):
    import shared.vimax_bridge as bridge

    shot = {"index": 1, "main_subject": "甲", "location": "船",
            "core_keywords": ["舟"], "ff_desc": "甲坐船"}
    first = bridge._get_frame_content_hash(shot, False, [], lock_kind="layout", characters=[])
    monkeypatch.setattr(bridge, "style_prefix_for_canvas",
                        lambda *a, **k: "另一种画风；9:16画幅")
    second = bridge._get_frame_content_hash(shot, False, [], lock_kind="layout", characters=[])
    assert first != second


def _boat_shot(positions, poses):
    ff = [{"name": name, "on_screen": True, "position": pos, "pose": pose}
          for name, pos, pose in zip(("甲", "乙", "丙"), positions, poses)]
    return {
        "index": 1, "location": "湖湾", "scene": "湖湾",
        "ff_desc": "甲坐在船板上，乙站在船板上。",
        "lf_desc": "甲坐在船板上，乙站在船板上。",
        "frame_layouts": {"ff": ff, "lf": ff},
    }


def _boat_chars():
    return [{"identifier": "甲"}, {"identifier": "乙"}, {"identifier": "丙"}]


def test_boat_capacity_anchor_uses_spec_not_location():
    import shared.vimax_bridge as bridge

    shot = _boat_shot(
        ("船板中央", "船板右侧", "远处岸上"),
        ("盘膝坐在船板中央", "站在船板上撑伞", "牵牛缓行"),
    )
    block = bridge._extract_char_layout(shot, _boat_chars(), frame_kind="first_frame")

    # location 是「湖湾」，不含「船」字；锚必须由 spec 站位触发。
    assert "船上角色甲、乙各自所在船" in block
    assert "不同船分别计算" in block
    assert "不要求并排" in block
    assert "不强制同一甲板平面" in block
    assert "岸上/远景角色丙" in block
    assert "所有角色" not in block


def test_boat_capacity_anchor_counts_three_boat_bound():
    import shared.vimax_bridge as bridge

    shot = _boat_shot(
        ("船板中央", "船板右侧", "船尾船板"),
        ("盘膝坐在船板中央", "站在船板上撑伞", "跪坐在船尾船板上"),
    )
    block = bridge._extract_char_layout(shot, _boat_chars(), frame_kind="first_frame")
    assert "船上角色甲、乙、丙各自所在船" in block
    assert "不同船分别计算" in block
    assert "不要求并排" in block
    assert "不强制同一甲板平面" in block


def test_boat_capacity_counts_unregistered_extra():
    import shared.vimax_bridge as bridge

    ff = [
        {"name": "甲", "on_screen": True, "position": "船板中央", "pose": "盘膝坐在船板中央"},
        {"name": "乙", "on_screen": True, "position": "船板右侧", "pose": "站在船板上"},
        {"name": "丁", "on_screen": True, "position": "船尾船板", "pose": "站在船尾船板上"},
    ]
    shot = {
        "index": 1, "location": "湖湾", "scene": "湖湾",
        "ff_desc": "甲坐在船板上，乙和丁站在船板上。",
        "lf_desc": "甲坐在船板上，乙和丁站在船板上。",
        "frame_layouts": {"ff": ff, "lf": ff},
    }
    block = bridge._extract_char_layout(
        shot, [{"identifier": "甲"}, {"identifier": "乙"}], frame_kind="first_frame")
    assert "船上角色甲、乙、丁各自所在船" in block
    assert "不同船分别计算" in block


def test_support_binding_is_not_boat_only():
    import shared.vimax_bridge as bridge

    ff = [
        {"name": "甲", "on_screen": True, "position": "车板中央",
         "pose": "坐在车板上", "support": {"id": "cart_1", "kind": "马车", "surface": "车板"}},
        {"name": "乙", "on_screen": True, "position": "台面右侧",
         "pose": "站在平台上", "support_kind": "平台", "support_id": "platform_2",
         "support_surface": "台面"},
        {"name": "丙", "on_screen": True, "position": "远处岸边", "pose": "牵牛缓行"},
    ]
    shot = {"index": 1, "location": "湖湾", "scene": "湖湾",
            "ff_desc": "甲坐在车板上，乙站在平台上，丙在岸边。",
            "lf_desc": "甲坐在车板上，乙站在平台上，丙在岸边。",
            "frame_layouts": {"ff": ff, "lf": ff}}
    block = bridge._extract_char_layout(
        shot, [{"identifier": "甲"}, {"identifier": "乙"}, {"identifier": "丙"}],
        frame_kind="first_frame")
    assert "甲位于马车的车板" in block
    assert "cart_1" not in block
    assert "乙位于平台的台面" in block
    assert "platform_2" not in block
    assert "并排" not in block
    assert "同一甲板平面" not in block


def test_shared_support_uses_same_label_without_ascii_id():
    import shared.vimax_bridge as bridge

    ff = [
        {"name": "甲", "on_screen": True, "position": "前舱", "pose": "坐着",
         "support": {"id": "boat_zhaoyue_1", "kind": "敞口木舟", "surface": "前舱木甲板内侧"}},
        {"name": "乙", "on_screen": True, "position": "中舱", "pose": "站着",
         "support": {"id": "boat_zhaoyue_1", "kind": "敞口木舟", "surface": "中舱木甲板内侧"}},
    ]
    shot = {"index": 1, "location": "湖湾", "ff_desc": "甲和乙在船上。",
            "lf_desc": "甲和乙在船上。", "frame_layouts": {"ff": ff, "lf": ff}}
    block = bridge._extract_char_layout(
        shot, [{"identifier": "甲"}, {"identifier": "乙"}], frame_kind="first_frame")
    assert "甲位于同一敞口木舟的前舱木甲板内侧" in block
    assert "乙位于同一敞口木舟的中舱木甲板内侧" in block
    assert "boat_zhaoyue_1" not in block
    assert "各自双脚/坐姿与衣摆底端均处于对应承载面边界内侧" in block


def test_boat_capacity_anchor_excludes_water_and_non_boat():
    import shared.vimax_bridge as bridge

    water = _boat_shot(
        ("船板中央", "船侧水面", "远处岸上"),
        ("盘膝坐在船板中央", "半身浸在水中", "牵牛缓行"),
    )
    block = bridge._extract_char_layout(water, _boat_chars(), frame_kind="first_frame")
    assert "同船角色" not in block
    assert "水中角色乙" in block

    shore = _boat_shot(
        ("岸上", "田埂", "远处岸上"),
        ("坐在岸上", "站在田埂", "牵牛缓行"),
    )
    block = bridge._extract_char_layout(shore, _boat_chars(), frame_kind="first_frame")
    assert "船板宽度至少" not in block


def test_later_timeline_clause_stripped_for_calm_frame():
    from shared.vimax_bridge import _strip_later_timeline_clauses

    dynamic = "春衫，头戴步摇，后步摇落水，披头散发"
    assert _strip_later_timeline_clauses(dynamic, "听香立于船板抚箫") == "春衫，头戴步摇"
    assert "披头散发" in _strip_later_timeline_clauses(dynamic, "听香披头散发立于船板")
    # 否定不算状态成立；只丢状态分句，不砍静态服装
    neg = _strip_later_timeline_clauses("春衫，受伤，披头散发", "甲未受伤，衣着春衫")
    assert neg == "春衫"
    # 另一角色的受伤不能给本角色放行
    other = _strip_later_timeline_clauses("受伤，披头散发", "甲站在船板")
    assert other == ""


def test_explicit_states_contract_keeps_only_allowed_states():
    from shared.vimax_bridge import _select_states_by_contract

    assert _select_states_by_contract("春衫，受伤，披头散发", ["受伤"]) == "春衫，受伤"
    assert _select_states_by_contract("春衫，受伤，披头散发", []) == "春衫"
    assert _select_states_by_contract("春衫，披头散发", ["披头散发"]) == "春衫，披头散发"


def test_prop_layout_prefers_spec_and_does_not_cross_comma(monkeypatch, tmp_path):
    import json
    import shared.paths as paths
    import shared.vimax_bridge as bridge

    script = tmp_path / "script.json"
    script.write_text(json.dumps({"prop_assets": {
        "钓杆": {"consistent_terms": [], "category": "道具"},
        "酒壶": {"consistent_terms": [], "category": "道具"},
    }}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(paths, "script_file", lambda: script)

    shot = {
        "index": 1, "location": "湖湾", "scene": "湖湾", "main_subject": "甲",
        "ff_desc": "甲左手持钓杆，右手提酒壶。", "lf_desc": "甲左手持钓杆，右手提酒壶。",
    }
    block = bridge._extract_prop_layout(shot, [{"identifier": "甲"}])
    assert "钓杆在左手" in block
    assert "酒壶在右手" in block
    assert "钓杆在右手" not in block

    shot["frame_layouts"] = {"ff": [{"name": "甲", "on_screen": True,
                                     "position": "船板", "pose": "坐在船板上",
                                     "props": ["钓杆", "酒壶"]}]}
    spec_block = bridge._extract_prop_layout(shot, [{"identifier": "甲"}], frame_kind="first_frame")
    assert "钓杆在甲左手" in spec_block
    assert "酒壶在甲右手" in spec_block
    assert "钓杆在甲右手" not in spec_block


def test_char_layout_does_not_copy_pose_into_facing():
    import shared.vimax_bridge as bridge

    shot = _boat_shot(
        ("船板中央", "船板右侧", "远处岸上"),
        ("盘坐持箫", "站立撑伞", "牵牛缓行"),
    )
    for item in shot["frame_layouts"]["ff"]:
        item.pop("gaze_target", None)
    block = bridge._extract_char_layout(shot, _boat_chars(), frame_kind="first_frame")
    assert block.count("盘坐持箫") == 1
    assert "（盘坐持箫）" not in block
    assert "，位于船板中央" in block


def test_age_label_and_negative_enum_removed():
    from shared.style_prefix import add_image_strict_constraints

    shot = {"location": "湖湾", "scene": "湖湾", "ff_desc": "甲站在湖边",
            "lf_desc": "甲站在湖边", "main_subject": "甲"}
    out = add_image_strict_constraints(
        "甲站在湖边", "首帧", shot,
        [{"identifier": "甲", "gender": "male", "age_range": "young"}])
    assert "年轻男子" in out
    assert "young男子" not in out
    assert "河流" not in out
    assert "没有刀" not in out
    assert "请在心中" not in out


def test_first_frame_threat_onscreen_does_not_say_not_emerged():
    from shared.style_prefix import add_image_strict_constraints

    shot = {"location": "湖湾", "scene": "湖湾",
            "ff_desc": "神秘人半身探出水面，右手高举匕首。",
            "lf_desc": "水面恢复平静。", "main_subject": "神秘人"}
    out = add_image_strict_constraints(
        "神秘人半身探出水面，右手高举匕首。", "首帧", shot,
        [{"identifier": "神秘人", "gender": "male", "age_range": "young"}])
    assert "尚未出水" not in out
    assert "已入画" in out


def test_threat_alias_not_double_counted():
    from shared.style_prefix import add_image_strict_constraints

    shot = {"location": "湖湾", "scene": "湖湾",
            "ff_desc": "神秘人右手握持短匕潜伏。", "lf_desc": "水面恢复平静。",
            "main_subject": "神秘人"}
    out = add_image_strict_constraints(
        "神秘人右手握持短匕潜伏。", "首帧", shot,
        [{"identifier": "神秘人", "gender": "male", "age_range": "young"}])
    assert "神秘人、刺客" not in out
    assert "总共1人" in out


def test_protected_composite_preserves_outside():
    from PIL import Image
    from shared.image_edit import compose_protected_edit

    original = Image.new("RGB", (100, 100), (0, 0, 0))
    edited = Image.new("RGB", (100, 100), (255, 255, 255))
    out = compose_protected_edit(original, edited, (0.3, 0.3, 0.7, 0.7), feather=0)
    assert out.getpixel((10, 10)) == (0, 0, 0)
    assert out.getpixel((50, 50)) == (255, 255, 255)
    with pytest.raises(ValueError):
        compose_protected_edit(original, edited, (0.7, 0.3, 0.3, 0.7), feather=0)


def test_image_adapter_prefix_uses_exact_canvas(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image
    import _image_gen
    import shared.vimax_adapters as va

    posts = []

    class FakeSvc:
        supports_multi_ref = True

        def generate(self, req):
            posts.append(req)
            out = tmp_path / "out.png"
            Image.new("RGB", (8, 8), "red").save(out)
            return SimpleNamespace(success=True, save_path=str(out), error="", soft_skip=False)

    monkeypatch.setattr(_image_gen, "create_service", lambda *a, **k: FakeSvc())
    monkeypatch.setenv("VIDEO_STYLE", "anime")
    adapter = va.ImageGeneratorAiVideoAdapter(size="1024x1024")
    result = asyncio.run(adapter.generate_single_image("甲坐船", shot_index=1))

    assert len(posts) == 1
    assert "1:1画幅" in posts[0].prompt
    assert "高清国漫" in posts[0].prompt
    assert result.data.size == (8, 8)


def test_image_adapter_provider_failure_no_retry(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import _image_gen
    import shared.vimax_adapters as va

    posts = []

    class FailSvc:
        supports_multi_ref = True

        def generate(self, req):
            posts.append(req)
            return SimpleNamespace(success=False, save_path=None, error="boom", soft_skip=False)

    monkeypatch.setattr(_image_gen, "create_service", lambda *a, **k: FailSvc())
    adapter = va.ImageGeneratorAiVideoAdapter(size="1440x2560")
    with pytest.raises(RuntimeError):
        asyncio.run(adapter.generate_single_image("甲坐船", shot_index=1))
    assert len(posts) == 1


# ---------------------------------------------------------------------------
# S12：参考图按文件字节入哈希；ff/lf 场景参考同源
# ---------------------------------------------------------------------------

def test_frame_hash_changes_when_reference_bytes_change(tmp_path):
    from shared.vimax_bridge import _get_frame_content_hash

    ref = Path(_png(tmp_path / "base.png"))
    shot = {"index": 1, "main_subject": "甲", "ff_desc": "甲站立", "lf_desc": "甲转身"}
    chars = [{"identifier": "甲", "static_features": "瘦高", "dynamic_features": "白衣"}]

    first = _get_frame_content_hash(shot, reference_paths=[str(ref)], characters=chars)
    _png(ref, (255, 255, 255))          # 路径不变、图像变了
    second = _get_frame_content_hash(shot, reference_paths=[str(ref)], characters=chars)
    assert first != second


def test_frame_hash_changes_when_character_appearance_changes(tmp_path):
    from shared.vimax_bridge import _get_frame_content_hash

    ref = Path(_png(tmp_path / "base.png"))
    shot = {"index": 1, "main_subject": "甲", "ff_desc": "甲站立"}
    a = _get_frame_content_hash(
        shot, reference_paths=[str(ref)],
        characters=[{"identifier": "甲", "static_features": "瘦高", "dynamic_features": "白衣"}])
    b = _get_frame_content_hash(
        shot, reference_paths=[str(ref)],
        characters=[{"identifier": "甲", "static_features": "矮胖", "dynamic_features": "白衣"}])
    assert a != b


def test_last_frame_hash_changes_when_first_frame_bytes_change(tmp_path):
    from shared.vimax_bridge import _get_frame_content_hash

    ff = Path(_png(tmp_path / "ff.png"))
    shot = {"index": 1, "main_subject": "甲", "ff_desc": "甲坐船", "lf_desc": "甲抬头"}
    first = _get_frame_content_hash(shot, False, [str(ff)], lock_kind="no_layout",
                                    characters=[], frame_kind="last_frame")
    _png(ff, (255, 255, 255))
    second = _get_frame_content_hash(shot, False, [str(ff)], lock_kind="no_layout",
                                     characters=[], frame_kind="last_frame")
    assert first != second


def test_first_and_last_frame_share_scene_reference(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    scene = _png(tmp_path / "wide.png")
    monkeypatch.setattr(bridge, "_collect_scene_asset_refs", lambda shot: [scene])
    monkeypatch.setattr(bridge, "_collect_space_object_refs", lambda shot: [])
    monkeypatch.setattr(bridge, "_collect_portrait_refs_for_shot", lambda *a, **k: [])
    monkeypatch.setattr(bridge, "_collect_prop_asset_refs", lambda *a, **k: [])
    monkeypatch.setattr(bridge, "_frame_spec_closeup", lambda *a, **k: False)
    monkeypatch.setattr(bridge, "is_empty_scene", lambda shot, chars: False)
    monkeypatch.setattr(bridge, "shot_work_dir", lambda idx: tmp_path)

    shot = {"index": 1, "location": "湖面", "ff_desc": "甲站立", "lf_desc": "甲转身",
            "motion_desc": "甲转身"}
    ff = bridge._collect_frame_refs_for_shot(shot, [], for_frame="首帧")
    lf = bridge._collect_frame_refs_for_shot(shot, [], for_frame="尾帧")
    assert scene in ff and scene in lf


# ---------------------------------------------------------------------------
# S12：二修候选必须过严格 QC 才允许覆盖原图
# ---------------------------------------------------------------------------

class _Qc:
    def __init__(self, ok=True, error="", critical=False):
        self.ok = ok
        self.error = error
        self.has_hallucination = critical
        self.issues = []


def test_accept_doodle_rejects_skipped_qc(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    target = Path(_png(tmp_path / "first_frame.png"))
    candidate = Path(_png(tmp_path / "first_frame_doodle_tmp.png", (255, 0, 0)))
    before = target.read_bytes()

    async def fake_qc(shot, path, kind, chars, empty, prompt=None, variation="", kf_index=None):
        shot["_qc_last"] = _Qc(ok=True, error="skipped")
        return False

    monkeypatch.setattr(bridge, "_maybe_qc_frame", fake_qc)
    accepted = asyncio.run(bridge._accept_doodle_refinement(
        str(candidate), str(target), {"index": 1}, "first_frame", [], False, "p"))
    assert accepted is False
    assert target.read_bytes() == before


def test_accept_doodle_rejects_failed_qc(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    target = Path(_png(tmp_path / "first_frame.png"))
    candidate = Path(_png(tmp_path / "first_frame_doodle_tmp.png", (255, 0, 0)))
    before = target.read_bytes()

    async def fake_qc(shot, path, kind, chars, empty, prompt=None, variation="", kf_index=None):
        shot["_qc_last"] = _Qc(ok=False, error="timeout")
        return False

    monkeypatch.setattr(bridge, "_maybe_qc_frame", fake_qc)
    accepted = asyncio.run(bridge._accept_doodle_refinement(
        str(candidate), str(target), {"index": 1}, "first_frame", [], False, "p"))
    assert accepted is False
    assert target.read_bytes() == before


def test_qc_repair_routes_through_strict_acceptance(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge
    import shared.image_qc as image_qc

    target = Path(_png(tmp_path / "first_frame.png"))
    candidate = Path(_png(tmp_path / "first_frame_repair.png", (255, 0, 0)))
    before = target.read_bytes()
    calls = {"accept": 0}

    async def fake_doodle(*a, **k):
        return str(candidate)

    async def fake_accept(*a, **k):
        calls["accept"] += 1
        return False

    # S22-3：修图默认关闭；本用例测的是"修图路由到严格验收"，需显式开启。
    monkeypatch.setenv("IMAGE_QC_REPAIR_ENABLED", "1")
    monkeypatch.setenv("QC_MAX_ATTEMPTS", "2")
    monkeypatch.setattr(bridge, "_doodle_refine_first_frame", fake_doodle)
    monkeypatch.setattr(bridge, "_accept_doodle_refinement", fake_accept)
    # 16x16 假图 < _validate_frame 的 5KB 下限，这里只验证路由
    monkeypatch.setattr(bridge, "_validate_frame", lambda *a, **k: True)
    monkeypatch.setattr(image_qc, "build_repair_plan", lambda *a, **k: {
        "action": "edit", "bbox_ratio": (0.1, 0.1, 0.9, 0.9),
        "edit_prompt": "fix", "matched_patterns": [], "area_pct": 10})

    ok = asyncio.run(bridge._try_qc_repair(
        None, {"index": 1, "_qc_last": object()}, str(target), "first_frame", [], False, 1))
    assert ok is False
    assert calls["accept"] == 1
    assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# S02：OpenCode QC 失败不得被报告为“已通过”
# ---------------------------------------------------------------------------

def _empty_verdict() -> str:
    return '{"has_hallucination": false, "issues": []}'


def test_opencode_qc_success_without_questions(monkeypatch, tmp_path):
    import shared.image_qc as image_qc

    monkeypatch.setenv("QC_PROVIDER", "opencode")
    monkeypatch.setattr(image_qc, "_opencode_vision_chat",
                        lambda path, prompt: _empty_verdict())
    monkeypatch.setattr(image_qc, "_detect_entities", lambda path: [])
    monkeypatch.setattr(image_qc, "build_targeted_questions", lambda *a, **k: [])

    res = image_qc.maybe_qc_sync(
        _png(tmp_path / "ff.png"), {"index": 1, "ff_desc": "甲坐船"},
        "first_frame", [], False, None, None)
    assert res.ok is True
    assert res.error == ""


@pytest.mark.parametrize("message", ["timeout", "empty response"])
def test_opencode_qc_general_failure_is_not_ok(monkeypatch, tmp_path, message):
    import shared.image_qc as image_qc

    monkeypatch.setenv("QC_PROVIDER", "opencode")

    def boom(path, prompt):
        raise RuntimeError(f"opencode {message}")

    monkeypatch.setattr(image_qc, "_opencode_vision_chat", boom)
    res = image_qc.maybe_qc_sync(
        _png(tmp_path / "ff.png"), {"index": 1, "ff_desc": "甲坐船"},
        "first_frame", [], False, None, None)
    assert res.ok is False
    assert res.has_hallucination is False


def test_opencode_qc_detection_failure_is_not_ok(monkeypatch, tmp_path):
    import shared.image_qc as image_qc

    monkeypatch.setenv("QC_PROVIDER", "opencode")
    monkeypatch.setattr(image_qc, "_opencode_vision_chat",
                        lambda path, prompt: _empty_verdict())

    def boom(path):
        raise RuntimeError("grounding failed")

    monkeypatch.setattr(image_qc, "_detect_entities", boom)
    monkeypatch.setattr(image_qc, "build_targeted_questions", lambda *a, **k: [])
    res = image_qc.maybe_qc_sync(
        _png(tmp_path / "ff.png"), {"index": 1, "ff_desc": "甲坐船"},
        "first_frame", [], False, None, None)
    assert res.ok is False


# ---------------------------------------------------------------------------
# S12：拼图落盘按内容寻址，同 tag 不同内容不会互相覆盖
# ---------------------------------------------------------------------------

def test_reference_sheet_names_are_content_addressed(tmp_path):
    from shared.ref_compose import fit_flat

    a = _png(tmp_path / "a.png", (0, 0, 0))
    b = _png(tmp_path / "b.png", (10, 10, 10))
    c = Path(_png(tmp_path / "c.png", (20, 20, 20)))
    sheets = tmp_path / "sheets"

    first = fit_flat([a, b, str(c)], 2, sheets, "shot1_ff")
    _png(c, (200, 200, 200))
    second = fit_flat([a, b, str(c)], 2, sheets, "shot1_ff")

    assert len(first) == len(second) == 2
    assert first[-1] != second[-1]
    assert Path(first[-1]).exists() and Path(second[-1]).exists()


# ---------------------------------------------------------------------------
# S13：关键帧按模型能力决定是否要求
# ---------------------------------------------------------------------------

def test_unknown_seedance_model_keeps_keyframes(monkeypatch):
    from shared.video_media_plan import video_consumes_intermediate_frames

    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-9-9-unknown")
    assert video_consumes_intermediate_frames() is True

    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    assert video_consumes_intermediate_frames() is False


def test_non_seedance_provider_keeps_keyframes(monkeypatch):
    from shared.video_media_plan import video_consumes_intermediate_frames

    monkeypatch.setenv("VIDEO_PROVIDER", "bailian")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    assert video_consumes_intermediate_frames() is True


# ---------------------------------------------------------------------------
# S11：输入敏感词 400 必须两级降级（sanitize + soften）
# ---------------------------------------------------------------------------

def test_input_sensitive_retry_applies_both_levels():
    from shared.content_safety import input_sensitive_retry_prompt

    revised = input_sensitive_retry_prompt("他握刀刺向听香，鲜血溅出")
    assert "指向" in revised          # soften_input_text
    assert "暗红" in revised          # sanitize
    assert input_sensitive_retry_prompt("干净的画面") == ""


# ---------------------------------------------------------------------------
# S10R：Step 01 规则指纹只覆盖 Step 01 消费的符号
# ---------------------------------------------------------------------------

def test_rule_fingerprint_excludes_step02b_only_symbols():
    from pathlib import Path
    import step01_script_gen.quality_issues as q

    style_prefix = Path(q.__file__).resolve().parent.parent / "shared" / "style_prefix.py"
    base = q._module_symbol_digest(style_prefix, q._STEP01_STYLE_PREFIX_SYMBOLS)
    # 稳定
    assert base == q._module_symbol_digest(style_prefix, q._STEP01_STYLE_PREFIX_SYMBOLS)
    # style_lock_prompt 是 Step02b 专用；把它加进名单必须改变摘要，
    # 说明默认名单确实没有把整份文件都算进去。
    with_extra = q._module_symbol_digest(
        style_prefix, q._STEP01_STYLE_PREFIX_SYMBOLS + ("style_lock_prompt",))
    assert base != with_extra
    # Step01 真正消费的符号必须在名单里
    assert "normalize_ff_desc" in q._STEP01_STYLE_PREFIX_SYMBOLS
    assert "is_empty_scene" in q._STEP01_STYLE_PREFIX_SYMBOLS


# ---------------------------------------------------------------------------
# S13B：--shots 只收敛到目标镜头实际出场的角色
# ---------------------------------------------------------------------------

def test_units_for_indices_includes_hook_and_selected_shots():
    from shared.vimax_bridge import _units_for_indices

    script = {"hook": {"index": 0}, "shots": [{"index": 1}, {"index": 2}]}
    units = _units_for_indices(script, {0, 2})
    assert [u["index"] for u in units] == [0, 2]


def test_characters_in_units_filters_out_absent_characters():
    from shared.vimax_bridge import _characters_in_units

    characters = [{"identifier": "甲"}, {"identifier": "乙"}]
    units = [{"index": 1, "ff_desc": "甲站在船头", "lf_desc": "甲转身"}]
    assert [c["identifier"] for c in _characters_in_units(characters, units)] == ["甲"]
    assert _characters_in_units(characters, []) == []
