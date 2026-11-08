# -*- coding: utf-8 -*-
"""WP3：把参考图组装 / QC 尝试日志 / doodle 先修后抽钉到已采纳稿件 58c1b6bd 的真入口.

外部数据集（专项回放，不属于干净检出即可运行的基础测试）：
  路径：<repo>/output/*/part_*/chapter_*/script_result.json
  版本：script_revision 以 58c1b6bd 开头（2026-11-08 现网采纳稿，13 镜）
  准备：step01 已提交该稿；本文件只读 output，不写任何正式产物。
  缺失：pytest.skip 并报出版本前缀，不自动跑模型重建现场。

被测链（生产代码，未经替换）：
  compose_frame_prompt / assert_no_contradictions / _select_all_character_refs
  _gen_first_frame_async -> _maybe_qc_frame -> _try_qc_repair -> build_repair_plan
    -> _doodle_refine_first_frame -> _accept_doodle_refinement
  只有两个外部出口打桩：生图 provider（generate_single_image）与 VLM 质检
  （image_qc.maybe_qc_sync）。QC 分类、红框绘制、复检准入、替换/回滚全部走生产实现。

按当前源码校正计划稿的两处过期接口名：
  * 计划里的 `_select_multi_character_references(shot, characters, bank, limit=2)`
    现网不存在；真实接口是 `_select_all_character_refs(shot, bank, characters_data, limit=2)`。
  * QC 侧车是 `<stem>_hallucination.json`（`image_qc.sidecar_path`），不是 `.qc.json`；
    `<stem>_qc_log.json` 才是尝试日志。二修候选的复检写 `<stem>_repair_qc_log.json`。
"""
from __future__ import annotations

import asyncio
import copy
import json
import random
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

REVISION_PREFIX = "58c1b6bd"
EXPECTED_REVISION = (
    "58c1b6bdd9b3d4589af3a24807beb9b776ab83867a855b37bbbc232bda85d9e4"
)
EXPECTED_SHOTS = 13

#: 本轮外部请求计数：任何一次真实 HTTP 发送都会在 fixture teardown 处失败。
_HTTP = {"sends": 0, "detail": []}


# ---------------------------------------------------------------------------
# 离线护栏 / 外部数据集
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """开场焊死外传：requests 的四个入口全部抛错并计数，收尾断言 0 次."""
    import requests

    _HTTP["sends"] = 0
    _HTTP["detail"] = []

    def _boom(*args, **kwargs):
        _HTTP["sends"] += 1
        _HTTP["detail"].append(repr(args[:2])[:160])
        raise RuntimeError("offline test: network forbidden")

    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(requests, "request", _boom)
    monkeypatch.setattr(requests.Session, "request", _boom)
    yield
    assert _HTTP["sends"] == 0, "离线用例出现外传请求: %s" % (_HTTP["detail"],)


def _find_adopted_script() -> tuple[Path, dict]:
    """定位唯一一份 58c1b6bd 已采纳稿；缺失即跳过（专项回放，不重建现场）."""
    hits: list[tuple[Path, dict]] = []
    for path in sorted((_ROOT / "output").glob("*/part_*/chapter_*/script_result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(data.get("script_revision") or "").startswith(REVISION_PREFIX):
            hits.append((path, data))
    if not hits:
        pytest.skip(
            "外部数据集缺失：output/*/part_*/chapter_*/script_result.json 中"
            "没有 script_revision 以 %s 开头的已采纳稿" % REVISION_PREFIX
        )
    if len(hits) > 1:
        pytest.fail("同一 revision 命中多份脚本，身份不唯一：%s" % [str(p) for p, _ in hits])
    path, data = hits[0]
    assert data.get("script_revision") == EXPECTED_REVISION, "脚本身份漂移: %s" % path
    shots = data.get("shots") or []
    assert len(shots) == EXPECTED_SHOTS, "镜数不是 %d: %d (%s)" % (
        EXPECTED_SHOTS, len(shots), path)
    return path, data


@pytest.fixture
def script() -> dict:
    _path, data = _find_adopted_script()
    return copy.deepcopy(data)


@pytest.fixture
def portrait_bank(tmp_path, script):
    """为 58c1b6bd 的 5 个角色造合成肖像（>6KB PNG）+ characters_bank.json.

    `_select_all_character_refs` 消费的是 CharacterBank 的 `portraits[view].path`；
    ViMax 工作目录里的 character_portraits_registry.json 由另一条入口
    （`_collect_portrait_refs_for_shot`）消费，不在本用例被测范围。
    """
    from shared.character_bank import CharacterBank

    bank_dir = tmp_path / "characters_bank"
    bank_dir.mkdir(parents=True)
    registry: dict = {}
    ident_by_path: dict[str, str] = {}
    for i, char in enumerate(script["characters"]):
        ident = char["identifier"]
        portraits = {}
        for view in ("front", "side", "back"):
            fp = bank_dir / ("%s_%s.png" % (ident, view))
            fp.write_bytes(_noise_png(seed=1000 + i, size=256))
            portraits[view] = {"path": str(fp), "description": "合成%s肖像" % view}
            ident_by_path[str(fp)] = ident
        registry[ident] = {"portraits": portraits}
    (bank_dir / "characters_bank.json").write_text(
        json.dumps(
            {"characters": registry,
             "meta": {"total_characters": len(registry)}},
            ensure_ascii=False),
        encoding="utf-8",
    )
    bank = CharacterBank(str(bank_dir))
    bank.load()
    return bank, ident_by_path


# ---------------------------------------------------------------------------
# 桩：生图 provider / VLM 质检
# ---------------------------------------------------------------------------

def _noise_png(seed: int, size: int = 512) -> bytes:
    """确定性噪声 PNG（随机像素不可压缩，512² 约 780KB，稳过 5KB 门槛）."""
    from io import BytesIO
    from PIL import Image

    data = random.Random(seed).randbytes(size * size * 3)
    buf = BytesIO()
    Image.frombytes("RGB", (size, size), data).save(buf, format="PNG")
    return buf.getvalue()


def _red_pixels(path: Path) -> int:
    """数纯红 (255,0,0) 像素：doodle 红框是 8px 实线，应当远多于噪点误命中."""
    from PIL import Image

    with Image.open(path) as im:
        raw = im.convert("RGB").tobytes()
    return sum(1 for i in range(0, len(raw), 3) if raw[i:i + 3] == b"\xff\x00\x00")


class _RenderedImage:
    def __init__(self, png: bytes):
        self._png = png

    def save(self, path):
        Path(path).write_bytes(self._png)


class _StubImageAdapter:
    """只替换 provider：按调用序号产出确定性图片并记录全部入参."""

    def __init__(self):
        self.calls: list[dict] = []
        self.pngs: list[bytes] = []

    async def generate_single_image(self, **kwargs):
        self.calls.append(dict(kwargs))
        png = _noise_png(seed=len(self.calls))
        self.pngs.append(png)
        save_path = kwargs.get("save_path")
        if save_path:
            Path(save_path).write_bytes(png)
        return _RenderedImage(png)


class _QcPlan:
    """按顺序返回预设 QcResult，并记录每次质检的真实入参."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    @property
    def remaining(self) -> int:
        return len(self._responses)

    def __call__(self, frame_path, shot, frame_kind, characters=None,
                 empty_scene=False, prompt=None, kf_index=None):
        self.calls.append({
            "path": Path(frame_path),
            "frame_kind": frame_kind,
            "prompt": prompt or "",
        })
        if not self._responses:
            raise AssertionError("QC 调用次数超过预设序列: %s" % frame_path)
        return self._responses.pop(0)


def _qc_ok(person_count: int | None = None):
    from shared.image_qc import QcResult

    return QcResult(has_hallucination=False, issues=[], ok=True,
                    person_count=person_count)


def _qc_critical(description: str, bbox, *, entity_desc: str):
    from shared.image_qc import QcIssue, QcResult

    return QcResult(
        has_hallucination=True,
        ok=True,
        person_count=1,
        issues=[QcIssue(category="prompt_semantics", description=description,
                        severity="critical")],
        entities=[{"label": "person", "bbox": list(bbox), "desc": entity_desc}],
    )


# ---------------------------------------------------------------------------
# 公共执行器
# ---------------------------------------------------------------------------

def _run_first_frame(monkeypatch, tmp_path, script, shot, qc_plan,
                     repair_env: bool = True):
    """以生产入口跑一次首帧生成，返回 (adapter, 返回值)."""
    import shared.image_qc as image_qc
    import shared.paths as paths
    import shared.vimax_bridge as bridge

    # 章节目录/工作目录全部落到 tmp：日志、侧车、哈希不得写进正式产物
    monkeypatch.setattr(paths, "chapter_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(paths, "vimax_working_dir",
                        lambda *a, **k: tmp_path / "vimax_working")
    if repair_env:
        monkeypatch.setenv("IMAGE_QC_REPAIR_ENABLED", "1")
        monkeypatch.setenv("STEP02A_NO_REPAIR", "0")
        monkeypatch.setenv("QC_MAX_ATTEMPTS", "2")
    monkeypatch.delenv("DOODLE_REFINE_ENABLED", raising=False)
    # 桩必须打在 image_qc 模块上：_maybe_qc_frame 在调用时才 import 这个名字
    monkeypatch.setattr(image_qc, "maybe_qc_sync", qc_plan)

    adapter = _StubImageAdapter()
    result = asyncio.run(bridge._gen_first_frame_async(
        adapter,
        tmp_path / "shots",
        copy.deepcopy(shot),
        False,
        max_retries=1,
        characters=script["characters"],
    ))
    return adapter, result


def _shot(script: dict, idx: int) -> dict:
    return next(s for s in script["shots"] if s["index"] == idx)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 场景 1：13 镜 × 首尾帧 = 26 条 prompt；多角色参考图组装
# ---------------------------------------------------------------------------

def test_scenario1_prompts_and_multi_character_refs(script, portrait_bank):
    from shared.frame_spec import get_frame_spec, is_on_screen
    from shared.prompt_contradiction import assert_no_contradictions
    from shared.vimax_bridge import _select_all_character_refs, compose_frame_prompt

    bank, ident_by_path = portrait_bank
    characters = script["characters"]
    idents = [c["identifier"] for c in characters]

    checked = 0
    for shot in script["shots"]:
        for frame_kind in ("first_frame", "last_frame"):
            prompt = compose_frame_prompt(shot, characters, frame_kind)
            assert prompt.strip(), "shot %s %s prompt 为空" % (shot["index"], frame_kind)
            assert_no_contradictions(
                prompt, "shot%s %s" % (shot["index"], frame_kind))
            checked += 1

            spec = [item for item in get_frame_spec(shot, frame_kind)
                    if isinstance(item, dict) and is_on_screen(item)]
            groups: dict[tuple[str, str], list[dict]] = {}
            for item in spec:
                support = item.get("support") if isinstance(item.get("support"), dict) else {}
                sid = str(support.get("id") or item.get("support_id") or "").strip()
                kind = str(support.get("kind") or item.get("support_kind") or "").strip()
                if sid and kind:
                    groups.setdefault((sid, kind), []).append(item)

            assert "微俯视斜侧视角" not in prompt
            assert "船体横向占画面宽度80%以上" not in prompt
            for (_sid, kind), group in groups.items():
                if len(group) < 2:
                    continue
                assert "承载关系" in prompt
                assert "不落入承载面外侧" in prompt
                for item in group:
                    assert f"{item['name']}位于同一{kind}" in prompt

    assert checked == EXPECTED_SHOTS * 2, "首尾帧 prompt 数不是 26: %d" % checked

    # 参考图选择：只由 ff_desc/prompt 中出现的角色决定，最多 limit 张，且路径真实存在
    for shot in script["shots"]:
        refs = _select_all_character_refs(shot, bank, characters, limit=2)
        assert len(refs) <= 2
        text = "%s %s" % (shot.get("ff_desc") or "", shot.get("prompt") or "")
        expected = [i for i in idents if i in text][:2]
        assert [ident_by_path[r] for r in refs] == expected, (
            "shot %s 参考图与文本提及不一致: %r" % (shot["index"], refs))
        for ref in refs:
            assert Path(ref).exists()

    # 空镜（shot 1）：没有同载多人，也不该给参考图
    empty = _shot(script, 1)
    assert "【画面构图】" not in compose_frame_prompt(empty, characters, "first_frame")
    assert _select_all_character_refs(empty, bank, characters, limit=2) == []

    # 同船两人（shot 2）：承载关系 + 两张在场角色肖像（不再注入固定构图）
    two_person = _shot(script, 2)
    prompt2 = compose_frame_prompt(two_person, characters, "first_frame")
    assert "承载关系" in prompt2 and "不落入承载面外侧" in prompt2
    assert "【画面构图】" not in prompt2 and "【船内站位】" not in prompt2
    refs2 = _select_all_character_refs(two_person, bank, characters, limit=2)
    assert len(refs2) == 2
    names2 = {str(item.get("name")) for item in get_frame_spec(two_person, "first_frame")
              if isinstance(item, dict) and is_on_screen(item)}
    assert {ident_by_path[r] for r in refs2} <= names2


# ---------------------------------------------------------------------------
# 场景 2：真入口路径 A —— 首抽直接通过 QC
# ---------------------------------------------------------------------------

def test_scenario2_first_draw_passes_qc(monkeypatch, tmp_path, script):
    shot = _shot(script, 2)
    qc = _QcPlan([_qc_ok(person_count=2)])
    adapter, result = _run_first_frame(monkeypatch, tmp_path, script, shot, qc)

    shot_dir = tmp_path / "shots" / "2"
    frame = shot_dir / "first_frame.png"
    assert len(adapter.calls) == 1, "通过 QC 后仍重抽: %d 次" % len(adapter.calls)
    assert adapter.calls[0]["frame_desc"] == "首帧"
    assert result == str(frame)

    assert frame.exists() and frame.stat().st_size > 5 * 1024
    assert (shot_dir / "frame_hash.txt").exists()
    assert (shot_dir / "first_frame_hallucination.json").exists()
    assert len(qc.calls) == 1 and qc.calls[0]["path"].name == "first_frame.png"
    assert qc.remaining == 0

    log = _read_json(shot_dir / "first_frame_qc_log.json")
    assert len(log) == 1
    assert log[0]["has_hallucination"] is False
    assert log[0]["variation"] == ""

    # 未触发任何修图/重抽产物
    assert not (shot_dir / "first_frame_doodle.png").exists()
    assert not (shot_dir / "first_frame_repair.png").exists()


# ---------------------------------------------------------------------------
# 场景 3：真入口路径 B —— 局部 critical → doodle 二修 → 复检通过，不重抽
# ---------------------------------------------------------------------------

def test_scenario3_doodle_repair_keeps_accepted_frame(monkeypatch, tmp_path, script):
    shot = _shot(script, 5)
    qc = _QcPlan([
        _qc_critical("人物服装颜色不符", [200, 200, 600, 800],
                     entity_desc="站立人物"),
        _qc_ok(person_count=3),
    ])
    adapter, result = _run_first_frame(monkeypatch, tmp_path, script, shot, qc)

    shot_dir = tmp_path / "shots" / "5"
    frame = shot_dir / "first_frame.png"
    assert len(adapter.calls) == 2, (
        "预期 首帧 + doodle 两次生图，实际 %d 次" % len(adapter.calls))
    assert adapter.calls[0]["frame_desc"] == "首帧"
    assert adapter.calls[1]["frame_desc"] == "doodle_refine"
    assert adapter.calls[1]["force_multi_ref"] is False
    doodle_refs = adapter.calls[1]["reference_image_paths"]
    assert len(doodle_refs) == 1
    assert Path(doodle_refs[0]).name == "first_frame_doodle.png"

    # 红框二修图确实画了 8px 红框
    doodle = shot_dir / "first_frame_doodle.png"
    assert doodle.exists()
    assert _red_pixels(doodle) > 100, "doodle 图没有红框"

    # 复检走真实 _accept_doodle_refinement 分支：候选图 + 「不得保留红框」断言
    assert len(qc.calls) == 2
    assert qc.calls[1]["path"].name == "first_frame_repair.png"
    assert "不得保留红框" in qc.calls[1]["prompt"]
    assert qc.remaining == 0

    # 最终首帧 = 二修候选（原子替换），不是首抽原图
    assert result == str(frame)
    assert frame.read_bytes() == adapter.pngs[1]
    assert frame.read_bytes() != adapter.pngs[0]
    assert not list(shot_dir.glob("*.bak")), "备份 .bak 未清理"

    # 哈希保留（修复成功不删，缓存命中修好的图）
    assert (shot_dir / "frame_hash.txt").exists()

    # 侧车已更新为无 critical；主图尝试日志保留首抽 critical，二修复检单独记日志
    sidecar = _read_json(shot_dir / "first_frame_hallucination.json")
    assert sidecar["has_hallucination"] is False and sidecar["issues"] == []
    main_log = _read_json(shot_dir / "first_frame_qc_log.json")
    assert len(main_log) == 1 and main_log[0]["has_hallucination"] is True
    repair_log = _read_json(shot_dir / "first_frame_repair_qc_log.json")
    assert len(repair_log) == 1 and repair_log[0]["has_hallucination"] is False


# ---------------------------------------------------------------------------
# 场景 4：真入口路径 C —— 二修复检仍 critical → 删帧删哈希 → attempt=1 重抽
# ---------------------------------------------------------------------------

def test_scenario4_failed_doodle_falls_back_to_retry(monkeypatch, tmp_path, script):
    from shared.vimax_bridge import _ATTEMPT_VARIATIONS

    shot = _shot(script, 8)
    qc = _QcPlan([
        _qc_critical("人物手中物品不对", [250, 200, 650, 850],
                     entity_desc="手中提灯的人物"),
        _qc_critical("画面残留红色标注线", [250, 200, 650, 850],
                     entity_desc="手中提灯的人物"),
        _qc_ok(person_count=1),
    ])
    adapter, result = _run_first_frame(monkeypatch, tmp_path, script, shot, qc)

    shot_dir = tmp_path / "shots" / "8"
    frame = shot_dir / "first_frame.png"
    assert [c["frame_desc"] for c in adapter.calls] == ["首帧", "doodle_refine", "首帧"]
    assert qc.remaining == 0

    # 最终图只能是 attempt=1 的重抽图：不是首抽坏图，也不是失败的 doodle 候选
    assert result == str(frame)
    assert frame.read_bytes() == adapter.pngs[2]
    assert frame.read_bytes() != adapter.pngs[0]
    assert frame.read_bytes() != adapter.pngs[1]

    main_log = _read_json(shot_dir / "first_frame_qc_log.json")
    assert len(main_log) == 2, "主图尝试日志应为 2 条，实际 %d" % len(main_log)
    assert main_log[0]["has_hallucination"] is True
    assert main_log[0]["variation"] == ""
    assert main_log[1]["has_hallucination"] is False
    assert main_log[1]["variation"] == _ATTEMPT_VARIATIONS[1]

    sidecar = _read_json(shot_dir / "first_frame_hallucination.json")
    assert sidecar["has_hallucination"] is False
    assert (shot_dir / "frame_hash.txt").exists()
    assert not list(shot_dir.glob("*.bak"))

    # 未被采纳的二修候选不得留在镜目录：图/侧车/红框标注都清掉，只留复检日志
    assert not (shot_dir / "first_frame_repair.png").exists()
    assert not (shot_dir / "first_frame_repair_hallucination.json").exists()
    assert not (shot_dir / "first_frame_doodle.png").exists()
    assert (shot_dir / "first_frame_repair_qc_log.json").exists()
