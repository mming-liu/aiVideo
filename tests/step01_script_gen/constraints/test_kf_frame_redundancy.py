# -*- coding: utf-8 -*-
"""K9. kf 帧级冗余检测（kf_frame_redundancy）单元测试.

09-13 hook 实测盲区：kf0（climax）与 ff 只差几个字（图片近乎复刻）、
frame_layouts.kfs[1] 原样复制 kfs[0]。全部 WARN-only、只读幂等。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make():
    from step01_script_gen.constraints.kf_frame_redundancy import (
        KfFrameRedundancyConstraint)
    return KfFrameRedundancyConstraint()


def _shot_with_kfs(kfs, ff, spec=None, index=1):
    from tests.step01_script_gen.conftest import make_shot
    return make_shot(index=index, ff_desc=ff, keyframes=kfs,
                     frame_layouts={"ff": [], "lf": [], "kfs": spec or []})


_FF = ("深夜照月湾，冷蓝月光，一叶窄身无篷舴艋舟浮于水面，"
       "画右船梢站着青衣小帽的张十三，手臂后引即将挥出，"
       "画左船头站着披头散发的听香，身体蜷缩僵住")


class TestMidKfVsFf:
    def test_hook_climax_kf_warns(self):
        """09-13 hook 实测对（difflib≈0.50）→ WARN."""
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kf = {"desc": "深夜冷蓝月光下，无篷舴艋舟停在照月湾水面，画右船梢站着青衣小帽的张十三，"
                      "右手停在半空面部紧绷，画左船头听香头偏向一侧", "stage": "climax", "t_sec": 1.2}
        script = make_script([_shot_with_kfs([kf], _FF)])
        repairs = _make().apply(make_ctx(script))
        assert len(repairs) == 1
        assert repairs[0].target == "kfs[0]_vs_ff"
        assert "复刻" in repairs[0].detail

    def test_differentiated_mid_kf_passes(self):
        """正常拉开差异的 mid kf（实测 ≤0.21）→ 0 WARN."""
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kf = {"desc": "刺客破水而出，水花四溅，右手举匕首对准船头", "stage": "approach", "t_sec": 1.0}
        script = make_script([_shot_with_kfs([kf], _FF)])
        assert _make().apply(make_ctx(script)) == []

    def test_pre_stage_exempt(self):
        """pre≈ff 是阶段语义设计 → 豁免."""
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kf = {"desc": _FF, "stage": "pre", "t_sec": 0.5}
        script = make_script([_shot_with_kfs([kf], _FF)])
        assert _make().apply(make_ctx(script)) == []

    def test_missing_stage_treated_as_mid(self):
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kf = {"desc": _FF, "t_sec": 1.0}
        script = make_script([_shot_with_kfs([kf], _FF)])
        repairs = _make().apply(make_ctx(script))
        assert len(repairs) == 1

    def test_hook_included(self):
        """hook 也在检查范围（shot_index=None）."""
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kf = {"desc": _FF, "stage": "climax", "t_sec": 1.0}
        hook = _shot_with_kfs([kf], _FF, index=0)
        script = make_script([], hook=hook)
        repairs = _make().apply(make_ctx(script))
        assert len(repairs) == 1 and repairs[0].shot_index is None

    def test_static_shot_exempt(self):
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kf = {"desc": _FF + "，画面定格", "stage": "climax", "t_sec": 1.0}
        s = _shot_with_kfs([kf], _FF + "，画面定格")
        script = make_script([s])
        assert _make().apply(make_ctx(script)) == []


class TestKfPairDuplicate:
    def test_near_identical_kf_pair_warns(self):
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kfs = [{"desc": "刺客半身探出水面，右手举匕首", "stage": "approach", "t_sec": 1.0},
               {"desc": "刺客半身探出水面，右手举匕首。", "stage": "climax", "t_sec": 2.0}]
        script = make_script([_shot_with_kfs(kfs, _FF)])
        repairs = _make().apply(make_ctx(script))
        assert any(r.target == "kfs[0]_vs_kfs[1]" for r in repairs)

    def test_distinct_kf_pair_passes(self):
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kfs = [{"desc": "刺客破水而出", "stage": "approach", "t_sec": 1.0},
               {"desc": "杨旭仰面倒向船尾，胸口洇开暗红", "stage": "react", "t_sec": 3.0}]
        script = make_script([_shot_with_kfs(kfs, _FF)])
        assert _make().apply(make_ctx(script)) == []


class TestSpecDuplicate:
    def test_spec_copy_warns(self):
        """hook 实测：kfs[1] 原样复制 kfs[0]（pose/position 全同）→ WARN."""
        from tests.step01_script_gen.conftest import make_ctx, make_script
        spec = [[{"name": "张十三", "position": "画右船梢", "pose": "右手停在半空", "props": []}],
                [{"name": "张十三", "position": "画右船梢", "pose": "右手停在半空", "props": []}]]
        kfs = [{"desc": "刺客破水而出", "stage": "approach", "t_sec": 1.0},
               {"desc": "杨旭仰面倒下", "stage": "react", "t_sec": 3.0}]
        script = make_script([_shot_with_kfs(kfs, _FF, spec=spec)])
        repairs = _make().apply(make_ctx(script))
        assert any(r.target == "spec_kfs[0]_vs_kfs[1]" for r in repairs)

    def test_spec_differentiated_passes(self):
        from tests.step01_script_gen.conftest import make_ctx, make_script
        spec = [[{"name": "张十三", "position": "画右船梢", "pose": "手臂后引", "props": []}],
                [{"name": "张十三", "position": "画右船梢", "pose": "右手收回站直", "props": []}]]
        kfs = [{"desc": "刺客破水而出", "stage": "approach", "t_sec": 1.0},
               {"desc": "杨旭仰面倒下", "stage": "react", "t_sec": 3.0}]
        script = make_script([_shot_with_kfs(kfs, _FF, spec=spec)])
        assert _make().apply(make_ctx(script)) == []


class TestIdempotence:
    def test_rerun_same_result(self):
        """只读约束：连跑两次结果一致（幂等硬要求）."""
        from tests.step01_script_gen.conftest import make_ctx, make_script
        kf = {"desc": _FF, "stage": "climax", "t_sec": 1.0}
        script = make_script([_shot_with_kfs([kf], _FF)])
        c = _make()
        r1 = c.apply(make_ctx(script))
        r2 = c.apply(make_ctx(script))
        assert len(r1) == len(r2) == 1
