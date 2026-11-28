"""v6.62.6（09-09）场景物体提取的截断处置回归测试.

两条被 `allow_truncated=True` 打开的新路径需要守住：
  1. 截断响应**放行**后仍要打 WARN——否则「提取到 N 个」静默少报，和 09-08
     那串问题（沉默的数据丢失）是同一个病。
  2. json_repair 抢救截断输出时，尾部没写完的物体可能被削成 `{}`；这种空壳
     不得入库（bank 以 identifier 为主键，打印成 '?'）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class _FakeBank:
    """object_extractor 只用 bank 拼「已知物体」提示段，空表即可."""

    def get_all_identifiers(self):
        return []

    def get_object(self, ident):
        return None


@pytest.fixture
def _patched(monkeypatch):
    """把 create_service 换成返回固定结果的桩，并记下收到的 request."""
    from step01_script_gen import object_extractor as oe
    from _text_gen.models import TextGenerationResult

    seen: dict = {}

    def _install(text: str, truncated: bool):
        class _Svc:
            def generate(self, req):
                seen["req"] = req
                return TextGenerationResult(success=True, text=text, truncated=truncated)

        monkeypatch.setattr(oe, "create_service", lambda *a, **k: _Svc())
        return oe

    _install.seen = seen
    return _install


def test_truncated_extraction_warns_and_keeps_complete_objects(_patched, capsys):
    """截断放行后：完整物体入库 + 显式 WARN 告知可能少报."""
    oe = _patched(
        '{"scene_objects": [{"identifier": "照月湾小舟", "category": "船", "length_m": 5.5}, '
        '{"identifier": "青石马车", "category": "车", "size_reasoning": "原文说马车窄小',
        truncated=True,
    )
    objs = oe.extract_scene_objects("原文……", _FakeBank())

    assert [o["identifier"] for o in objs] == ["照月湾小舟", "青石马车"]
    out = capsys.readouterr().out
    assert "截断" in out and "可能少于实际" in out, "静默少报正是要修的病"


def test_truncation_residue_shell_is_dropped(_patched, capsys):
    """截断落在下一个 key 上 → 尾对象被削成 {}，必须过滤掉而不是入库."""
    oe = _patched(
        '{"scene_objects": [{"identifier": "照月湾小舟", "category": "船"}, '
        '{"identifier": "青石马车", "ca',
        truncated=True,
    )
    objs = oe.extract_scene_objects("原文……", _FakeBank())

    assert [o["identifier"] for o in objs] == ["照月湾小舟"]
    out = capsys.readouterr().out
    assert "残缺条目" in out, "丢弃动作要可见，否则数量对不上没人查得到原因"


def test_normal_extraction_has_no_truncation_noise(_patched, capsys):
    """未截断时不得打截断 WARN（告警要保持信噪比）."""
    oe = _patched(
        '{"scene_objects": [{"identifier": "照月湾小舟", "category": "船"}]}',
        truncated=False,
    )
    objs = oe.extract_scene_objects("原文……", _FakeBank())

    assert len(objs) == 1
    out = capsys.readouterr().out
    assert "截断" not in out
    assert "残缺条目" not in out


def test_list_extraction_opts_into_truncation_tolerance(_patched):
    """本调用点必须显式 allow_truncated=True，且上限走配置而非字面量."""
    from _text_gen import config as text_config

    oe = _patched('{"scene_objects": []}', truncated=False)
    oe.extract_scene_objects("原文……", _FakeBank())

    req = _patched.seen["req"]
    assert req.allow_truncated is True
    assert req.max_tokens == text_config.TEXT_EXTRACT_MAX_TOKENS
