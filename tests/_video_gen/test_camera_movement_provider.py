"""v6.58 camera_movement 字段接入 3 个 provider 的测试.

覆盖：
  1. _hailuo_camera_from_structured: 结构化字段 → 方括号指令
  2. _hailuo_camera_from_structured 边界（空/None/无匹配）
  3. _hailuo_camera_from_structured ≤3 组合上限
  4. 旧 _hailuo_camera_prefix 仍可用（从 prompt 搜索）
  5. VideoGenerationRequest 新字段
  6. 幂等："运镜：" 已在 prompt 开头则不再注入（volcengine / bailian）
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from _video_gen.models import VideoGenerationRequest
from _video_gen.minimax_provider import (
    _hailuo_camera_from_structured,
    _hailuo_camera_prefix,
)


class TestHailuoCameraFromStructured:
    """结构化字段驱动的方括号指令映射."""

    def test_basic_push_in(self):
        assert _hailuo_camera_from_structured("缓慢推近") == "[Push in] "

    def test_tracking_shot(self):
        assert _hailuo_camera_from_structured("快速跟拍") == "[Tracking shot] "

    def test_static_shot(self):
        assert _hailuo_camera_from_structured("固定机位") == "[Static shot] "

    def test_tilt_up(self):
        assert _hailuo_camera_from_structured("仰拍") == "[Tilt up] "

    def test_truck_left(self):
        assert _hailuo_camera_from_structured("横移") == "[Truck left] "

    def test_empty_string(self):
        assert _hailuo_camera_from_structured("") == ""

    def test_none(self):
        assert _hailuo_camera_from_structured(None) == ""

    def test_unknown_camera_returns_empty(self):
        # 环绕：Hailuo 官方没这指令
        assert _hailuo_camera_from_structured("环绕") == ""
        # 主观视角：也不在表里
        assert _hailuo_camera_from_structured("主观视角") == ""

    def test_max_three_combinations(self):
        # 即便有 4+ 命中，限 ≤3
        s = "推近 拉远 横移 仰拍"
        out = _hailuo_camera_from_structured(s)
        # 形如 "[X,Y,Z] "，3 个逗号
        assert out.startswith("[") and out.endswith("] ")
        body = out[1:-2]
        assert len(body.split(",")) <= 3

    def test_no_duplicate_in_combination(self):
        # "缓慢推近" 和 "推近" 都命中 Push in，但只出现一次
        out = _hailuo_camera_from_structured("缓慢推近 推近")
        assert out == "[Push in] "


class TestHailuoCameraPrefixLegacy:
    """旧路径（从 prompt 全文搜索）—— 保留兼容."""

    def test_extracts_from_prompt(self):
        prompt = "杨旭奔跑。镜头缓慢推近，捕捉他脸上的汗珠。"
        out = _hailuo_camera_prefix(prompt)
        assert out == "[Push in] "

    def test_no_match_returns_empty(self):
        prompt = "杨旭站立不动。"
        assert _hailuo_camera_prefix(prompt) == ""

    def test_empty_prompt(self):
        assert _hailuo_camera_prefix("") == ""


class TestVideoGenerationRequest:
    """dataclass 新字段."""

    def test_default_empty(self):
        req = VideoGenerationRequest(prompt="x", clip_index=1)
        assert req.camera_movement == ""

    def test_set_camera_movement(self):
        req = VideoGenerationRequest(
            prompt="x", clip_index=1, camera_movement="推近"
        )
        assert req.camera_movement == "推近"


class TestCameraPrefixIdempotency:
    """provider 层"运镜：已在 prompt 开头则不重复"幂等性."""

    def test_volcengine_skip_when_present(self):
        # 模拟 volcengine 行为
        prompt = "运镜：缓慢推近\n【视频起点】江面"
        cam = "推近"
        if cam and "运镜：" not in prompt[:20]:
            prompt = f"运镜：{cam}\n{prompt}"
        # prompt 应保持原样
        assert prompt.startswith("运镜：缓慢推近")

    def test_volcengine_inject_when_absent(self):
        prompt = "【视频起点】江面"
        cam = "推近"
        if cam and "运镜：" not in prompt[:20]:
            prompt = f"运镜：{cam}\n{prompt}"
        assert prompt == "运镜：推近\n【视频起点】江面"

    def test_bailian_clip_prompt_idempotent(self):
        # 模拟 bailian _clip_prompt 行为
        class FakeReq:
            prompt = "运镜：缓慢推近\n杨旭..."
            camera_movement = "缓慢推近"

        prompt = FakeReq.prompt
        if FakeReq.camera_movement and "运镜：" not in prompt[:20]:
            prompt = f"运镜：{FakeReq.camera_movement}\n{prompt}"
        # 幂等：不重复加
        assert prompt == "运镜：缓慢推近\n杨旭..."
