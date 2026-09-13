# -*- coding: utf-8 -*-
"""09-13 kf 参考图逐帧重配（_compose_kf_refs）测试.

覆盖双根因修复：
(a) ff 整图锚条件化——kf desc 与 ff_desc 高相似（≥0.8）时摘掉 ff 整图、
    改补场景资产图（hook kf0 悬空匕首：i2i 从 ff 图抄刀根因）；
(b) 道具参图 per-frame 化——按本帧 desc+spec 匹配替换合并文本匹配结果
    （hook kf1「神秘人匕首单独摆放」脏锚定根因）。
"""
import pytest

from shared.vimax_bridge import (_KF_FF_SIM_THRESHOLD, _compose_kf_refs,
                                 _text_sim_ratio)


class TestTextSimRatio:
    def test_identical(self):
        assert _text_sim_ratio("张十三挥掌掴听香", "张十三挥掌掴听香") == 1.0

    def test_punctuation_insensitive(self):
        a = "深夜照月湾，冷蓝月光。画右船梢站着张十三"
        b = "深夜照月湾冷蓝月光画右船梢站着张十三"
        assert _text_sim_ratio(a, b) == 1.0

    def test_unrelated_low(self):
        r = _text_sim_ratio("张十三挥掌掴听香", "皇帝在金銮殿上批阅奏折")
        assert r < _KF_FF_SIM_THRESHOLD

    def test_empty(self):
        assert _text_sim_ratio("", "abc") == 0.0
        assert _text_sim_ratio("abc", "") == 0.0

    def test_near_duplicate_high(self):
        # hook 实测形态：4s 微动作镜 kf0 与 ff 只差动作短语 → 高相似
        ff = "深夜照月湾，冷蓝月光，一叶窄身无篷舴艋舟浮于水面，画右船梢的船板上站着青衣小帽的张十三，手臂后引即将挥出"
        kf = "深夜冷蓝月光下，无篷舴艋舟停在照月湾水面，画右船梢站着青衣小帽的张十三，右手停在半空面部紧绷"
        assert _text_sim_ratio(kf, ff) >= 0.5  # 场景骨架重叠即拉高（阈值判定见下）


@pytest.fixture()
def scene_png(tmp_path):
    p = tmp_path / "near.png"
    p.write_bytes(b"\x89PNG")  # 只需 exists()，不校验内容
    return str(p)


def _fake_collector(result):
    calls = []

    def _c(shot, limit, frame_desc, characters):
        calls.append({"limit": limit, "frame_desc": frame_desc})
        return result[:limit]

    return _c, calls


class TestComposeKfRefsFfAnchor:
    def test_high_sim_swaps_ff_to_scene(self, scene_png):
        refs = ["portrait.png", "ff.png", "prop_x.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="张十三右手停在半空", ff_desc="张十三右手停在半空",
            ff_image_path="ff.png", scene_alt_refs=[scene_png], ref_limit=3)
        assert "ff.png" not in out
        assert scene_png in out
        assert "portrait.png" in out  # 肖像锚不动

    def test_low_sim_keeps_ff(self, scene_png):
        refs = ["portrait.png", "ff.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="刺客破水而出举匕首下刺", ff_desc="杨旭独坐船头垂钓",
            ff_image_path="ff.png", scene_alt_refs=[scene_png], ref_limit=3)
        assert out == refs  # 不相似 → 保留 ff 环境锚（防翻船修复不回归）

    def test_swap_never_empty(self):
        # 防御：refs 只有 ff 且无场景资产可补 → 保留原 refs（不得为空）
        out = _compose_kf_refs(
            ["ff.png"], kf_desc_raw="同左", ff_desc="同左",
            ff_image_path="ff.png", scene_alt_refs=[], ref_limit=3)
        assert out == ["ff.png"]

    def test_scene_missing_file_skipped(self, tmp_path):
        refs = ["portrait.png", "ff.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="同左", ff_desc="同左",
            ff_image_path="ff.png",
            scene_alt_refs=[str(tmp_path / "not_exist.png")], ref_limit=3)
        # ff 摘掉、场景图不存在补不进 → 剩肖像（非空，合法）
        assert out == ["portrait.png"]

    def test_ref_limit_respected(self, tmp_path):
        scenes = []
        for i in range(3):
            p = tmp_path / f"s{i}.png"
            p.write_bytes(b"x")
            scenes.append(str(p))
        refs = ["p1.png", "ff.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="同左", ff_desc="同左",
            ff_image_path="ff.png", scene_alt_refs=scenes, ref_limit=2)
        assert len(out) <= 2


class TestComposeKfRefsPropPerFrame:
    def test_prop_refs_replaced_by_per_frame(self):
        collector, calls = _fake_collector(["prop_slap.png"])
        refs = ["portrait.png", "ff.png", "prop_dagger.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="张十三挥掌", ff_desc="杨旭垂钓",
            prop_ref_paths=["prop_dagger.png"], prop_scan="张十三挥掌 画右船梢",
            shot={"index": 0}, characters=[], ref_limit=3,
            prop_collector=collector)
        assert "prop_dagger.png" not in out  # 合并文本匹配的脏道具被摘
        assert "prop_slap.png" in out        # 本帧匹配结果注入
        # 收集器收到的是本帧 scan 文本（per-frame），不是合并全文
        assert calls and calls[0]["frame_desc"] == "张十三挥掌 画右船梢"

    def test_prop_per_frame_empty_result_drops_prop(self):
        collector, _ = _fake_collector([])
        refs = ["portrait.png", "prop_dagger.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="张十三挥掌（无道具）", ff_desc="杨旭持竿垂钓",
            prop_ref_paths=["prop_dagger.png"], prop_scan="张十三挥掌 无道具",
            shot={}, characters=[], ref_limit=3, prop_collector=collector)
        assert out == ["portrait.png"]  # 本帧无道具 → 脏道具参图被摘且不补

    def test_unchanged_when_no_signals(self):
        refs = ["portrait.png", "ff.png"]
        out = _compose_kf_refs(refs, kf_desc_raw="x", ff_desc="y")
        assert out == refs

    def test_both_fixes_compose(self, scene_png):
        # 高相似 + 脏道具：ff 换场景图、脏道具换本帧道具，互不踩名额
        collector, _ = _fake_collector(["prop_new.png"])
        refs = ["portrait.png", "ff.png", "prop_dagger.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="同左", ff_desc="同左",
            ff_image_path="ff.png", scene_alt_refs=[scene_png],
            prop_ref_paths=["prop_dagger.png"], prop_scan="同左 无道具",
            shot={}, characters=[], ref_limit=3, prop_collector=collector)
        assert "ff.png" not in out and "prop_dagger.png" not in out
        assert len(out) <= 3


class TestStageGating:
    """09-13 校准：pre≈ff、after/exit≈lf 是阶段语义设计，不触发替换."""

    def test_pre_stage_high_sim_keeps_ff(self, scene_png):
        refs = ["portrait.png", "ff.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="同左", ff_desc="同左", kf_stage="pre",
            ff_image_path="ff.png", scene_alt_refs=[scene_png], ref_limit=3)
        assert out == refs  # pre 帧高相似不替换

    def test_after_stage_high_sim_keeps_ff(self, scene_png):
        refs = ["portrait.png", "ff.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="同左", ff_desc="同左", kf_stage="after",
            ff_image_path="ff.png", scene_alt_refs=[scene_png], ref_limit=3)
        assert out == refs

    def test_climax_stage_high_sim_swaps(self, scene_png):
        refs = ["portrait.png", "ff.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="同左", ff_desc="同左", kf_stage="climax",
            ff_image_path="ff.png", scene_alt_refs=[scene_png], ref_limit=3)
        assert "ff.png" not in out and scene_png in out

    def test_missing_stage_treated_as_mid(self, scene_png):
        refs = ["portrait.png", "ff.png"]
        out = _compose_kf_refs(
            refs, kf_desc_raw="同左", ff_desc="同左", kf_stage=None,
            ff_image_path="ff.png", scene_alt_refs=[scene_png], ref_limit=3)
        assert "ff.png" not in out

    def test_calibrated_threshold_hook_pair(self, scene_png):
        """hook 实测问题对（difflib≈0.50，climax）→ 触发替换；阈值 0.40 居中."""
        ff = ("深夜照月湾，冷蓝月光，一叶窄身无篷舴艋舟浮于水面，"
              "画右船梢站着青衣小帽的张十三，手臂后引即将挥出，"
              "画左船头站着披头散发的听香，身体蜷缩僵住")
        kf = ("深夜冷蓝月光下，无篷舴艋舟停在照月湾水面，画右船梢站着青衣小帽的张十三，"
              "右手停在半空面部紧绷，画左船头听香头偏向一侧")
        assert 0.40 <= _text_sim_ratio(kf, ff) < 0.75  # 校准锚点，防阈值漂移
        out = _compose_kf_refs(
            ["portrait.png", "ff.png"], kf_desc_raw=kf, ff_desc=ff, kf_stage="climax",
            ff_image_path="ff.png", scene_alt_refs=[scene_png], ref_limit=3)
        assert "ff.png" not in out
