"""F12. SurfaceBackfillConstraint 单元测试（v6.62.1，09-06）.

contract:
  - char_layout[i].surface 空 → 从 position/ff_desc/lf_desc/location 派生回填
  - 已有非空 surface（≠"未指定"）→ 跳过（幂等）
  - 全部无法派生 → 不改（留 warning 给修复轮）
  - "未指定" 占位符（v6.62 前历史值）同样回填
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make_constraint():
    from step01_script_gen.constraints.surface_backfill import SurfaceBackfillConstraint
    return SurfaceBackfillConstraint()


class TestSurfaceBackfill:

    def test_backfill_from_ff_desc(self):
        """ff_desc 写「杨旭盘膝坐船头」→ surface=船头."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1,
                      ff_desc="杨旭盘膝坐船头，双手持箫吹奏",
                      lf_desc="杨旭放下竹箫，仰头望夜空",
                      char_layout=[{"name": "杨旭", "position": "画中央船头",
                                    "facing": "盘膝坐", "role": "主", "surface": ""}]),
        ])
        ctx = make_ctx(script)
        repairs = _make_constraint().apply(ctx)
        assert len(repairs) == 1
        assert script["shots"][0]["char_layout"][0]["surface"] == "船头"

    def test_backfill_from_position(self):
        """position=画右中舱 → surface=中舱（船板 fallback 前 position 优先）."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1,
                      ff_desc="听香立于中舱撑伞",
                      char_layout=[{"name": "听香", "position": "画右中舱",
                                    "facing": "站立", "role": "主", "surface": ""}]),
        ])
        ctx = make_ctx(script)
        _make_constraint().apply(ctx)
        assert script["shots"][0]["char_layout"][0]["surface"] == "中舱"

    def test_backfill_from_scene_fallback(self):
        """position/文本全无 surface 词 → scene=杨府别庄 → 院内地面."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1, scene="杨府别庄", location="别庄前厅",
                      ff_desc="肖管事拱手而立",
                      char_layout=[{"name": "肖管事", "position": "画中央",
                                    "facing": "拱手", "role": "主", "surface": ""}]),
        ])
        ctx = make_ctx(script)
        _make_constraint().apply(ctx)
        # location 含「厅」→ 厅内地面（scene 词表内「厅」在「庄」前面同串都命中，
        # 场景扫描顺序 庄→厅？表顺序：船/舟/湾/车/轿/厅…庄在后 → 厅内地面）
        assert script["shots"][0]["char_layout"][0]["surface"] == "厅内地面"

    def test_existing_surface_untouched(self):
        """已有 surface=船尾 → 不覆盖."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1,
                      ff_desc="杨旭仰卧船头",
                      char_layout=[{"name": "杨旭", "position": "画中船头",
                                    "facing": "仰卧", "role": "主", "surface": "船尾"}]),
        ])
        ctx = make_ctx(script)
        repairs = _make_constraint().apply(ctx)
        assert repairs == []
        assert script["shots"][0]["char_layout"][0]["surface"] == "船尾"

    def test_unspecified_placeholder_backfilled(self):
        """历史占位符「未指定」也回填."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1,
                      ff_desc="张十三站在岸边提灯",
                      char_layout=[{"name": "张十三", "position": "画左",
                                    "facing": "站立", "role": "主", "surface": "未指定"}]),
        ])
        ctx = make_ctx(script)
        _make_constraint().apply(ctx)
        assert script["shots"][0]["char_layout"][0]["surface"] == "岸边"

    def test_no_layout_no_op(self):
        """无 char_layout 的镜零操作."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([make_shot(index=1)])
        ctx = make_ctx(script)
        assert _make_constraint().apply(ctx) == []

    def test_idempotent(self):
        """连跑 2 次第 2 次零新增."""
        from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

        script = make_script([
            make_shot(index=1,
                      ff_desc="杨旭盘膝坐船头",
                      char_layout=[{"name": "杨旭", "position": "画中央船头",
                                    "facing": "盘膝坐", "role": "主", "surface": ""}]),
        ])
        ctx = make_ctx(script)
        c = _make_constraint()
        r1 = c.apply(ctx)
        r2 = c.apply(ctx)
        assert len(r1) == 1
        assert r2 == []

    def test_constraint_attributes(self):
        from step01_script_gen.constraints.types import Severity
        c = _make_constraint()
        assert c.name == "surface_backfill"
        assert c.severity == Severity.WARN
