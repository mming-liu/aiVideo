# -*- coding: utf-8 -*-
"""09-13 武器 holder 校验（_check_weapon_holder）测试.

根因：hook ff_desc「一支短刃自画外斜入画面，刀尖抵近张十三身侧」——
frame_layouts 两人 props 全空、同句无 holder 动词，图片模型只能画悬浮刀。
校验逐帧（ff/lf/kf）武器词必须有持握主体或落点锚，warning-only。
"""
from step01_script_gen.validation import _check_weapon_holder


def _shot(text, field="ff_desc", props_map=None, index=1):
    """构造单镜；props_map: {"ff": [[entry...]], "lf": [...], "kfs": [[...]]}"""
    fl = {"ff": [], "lf": [], "kfs": []}
    if props_map:
        fl.update(props_map)
    return {"index": index, "ff_desc": "", "lf_desc": "", "keyframes": [],
            field: text, "frame_layouts": fl}


class TestOrphanWeaponWarns:
    def test_hook_dangling_blade(self):
        # 09-13 hook 实测原句 → 必须 WARN
        hook = _shot(
            "深夜照月湾，画右船梢站着张十三，手臂后引即将挥出，"
            "一支短刃自画外斜入画面，刀尖抵近张十三身侧，即将触及",
            props_map={"ff": [{"name": "张十三", "props": []},
                              {"name": "听香", "props": []}]})
        r = _check_weapon_holder([], hook=hook)
        assert len(r["warnings"]) == 1
        assert "钩子" in r["warnings"][0] and "悬浮" in r["warnings"][0]

    def test_lf_orphan_sword(self):
        s = _shot("一柄长剑斜指夜空", field="lf_desc")
        r = _check_weapon_holder([s])
        assert len(r["warnings"]) == 1
        assert "lf" in r["warnings"][0]

    def test_kf_desc_orphan(self):
        s = _shot("")
        s["keyframes"] = [{"desc": "一把匕首抵住咽喉", "stage": "climax", "t_sec": 2.0}]
        s["frame_layouts"]["kfs"] = [[{"name": "刺客", "props": []}]]
        r = _check_weapon_holder([s])
        assert len(r["warnings"]) == 1
        assert "kf0" in r["warnings"][0]


class TestHolderPresentPasses:
    def test_holder_verb_same_clause(self):
        s = _shot("张十三右手紧握匕首，刀尖垂向水面")
        assert _check_weapon_holder([s])["warnings"] == []

    def test_draw_verb(self):
        s = _shot("刺客从腰间拔出短刃")
        assert _check_weapon_holder([s])["warnings"] == []

    def test_spec_props_holding(self):
        s = _shot("匕首寒光一闪", props_map={
            "ff": [{"name": "神秘人", "props": ["寒光匕首"]}]})
        assert _check_weapon_holder([s])["warnings"] == []

    def test_drop_anchor_body(self):
        s = _shot("匕首插在杨旭胸口，血洇开")
        assert _check_weapon_holder([s])["warnings"] == []

    def test_drop_anchor_ground(self):
        s = _shot("长刀落地，无人去捡")
        assert _check_weapon_holder([s])["warnings"] == []

    def test_hidden_weapon_exempt(self):
        s = _shot("袖中藏着一把未出鞘的匕首")
        assert _check_weapon_holder([s])["warnings"] == []


class TestNonWeaponPasses:
    def test_eyebrow_not_weapon(self):
        s = _shot("剑眉星目的杨旭独坐船头")
        assert _check_weapon_holder([s])["warnings"] == []

    def test_no_weapon(self):
        s = _shot("张十三挥掌掴向听香，听香头偏向一侧")
        assert _check_weapon_holder([s])["warnings"] == []

    def test_empty_text(self):
        assert _check_weapon_holder([_shot("")])["warnings"] == []
