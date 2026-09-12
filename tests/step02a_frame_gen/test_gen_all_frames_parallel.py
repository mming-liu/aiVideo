"""gen_all_frames 镜头级并发的语义锁定（2026-09-12 并发化）.

不变量：
  - 并发数 N：同时 in-flight 的 gen_shot_frames ≤ N（信号量限流）
  - 失败隔离：单镜抛异常不影响其他镜头的结果（失败镜无 results 条目）
  - 结果键：results 以 shot index 为键完整回收
  - concurrency=1：串行回退（同时 in-flight = 1）
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make_script(indices: list[int], tmp_path: Path) -> Path:
    """构造临时 script（写入 pytest tmp_path，不污染包目录）."""
    import json
    p = tmp_path / "script_parallel.json"
    shots = [{"index": i, "prompt": f"镜头{i}", "ff_desc": f"ff{i}",
              "lf_desc": f"lf{i}", "duration_sec": 4.0, "location": "小舟"}
             for i in indices]
    p.write_text(json.dumps({"shots": shots, "hook": None, "characters": []},
                            ensure_ascii=False), encoding="utf-8")
    return p


def _install_fake_gen(monkeypatch, *, fail_idx: set[int] | None = None,
                      sleep: float = 0.15, in_flight: list | None = None):
    """替换 shared.vimax_bridge.gen_shot_frames 为可控假实现."""
    import shared.vimax_bridge as bridge

    async def fake_gen(shot, shots_dir, image_adapter, script_mtime,
                       force=False, characters=None, skip_first_frame=False,
                       frames=None):
        idx = shot["index"]
        if in_flight is not None:
            in_flight.append(idx)
            await asyncio.sleep(sleep)
            in_flight.remove(idx)
        else:
            await asyncio.sleep(sleep)
        if fail_idx and idx in fail_idx:
            raise RuntimeError(f"shot {idx} 注入失败")
        return {"ff_path": None, "lf_path": f"lf_{idx}.png", "keyframes_paths": []}

    monkeypatch.setattr(bridge, "gen_shot_frames", fake_gen)


def test_parallel_results_and_failure_isolation(tmp_path, monkeypatch):
    """单镜失败不中断整轮：失败镜缺席、其余镜结果完整."""
    from step02a_frame_gen.main import gen_all_frames

    idxs = [1, 2, 3, 4]
    script_path = _make_script(idxs, tmp_path)
    _install_fake_gen(monkeypatch, fail_idx={3}, sleep=0.01)

    results = asyncio.run(gen_all_frames(script_path, tmp_path, concurrency=2))
    assert set(results) == {1, 2, 4}, f"失败镜 3 应缺席: {set(results)}"
    assert results[2]["lf_path"] == "lf_2.png"


def test_concurrency_bounds_in_flight(tmp_path, monkeypatch):
    """并发数 N：同时 in-flight ≤ N（信号量真实限流）."""
    from step02a_frame_gen.main import gen_all_frames

    idxs = [1, 2, 3, 4, 5, 6]
    script_path = _make_script(idxs, tmp_path)
    in_flight: list[int] = []
    peak = {"v": 0}

    def _track():
        # 记录峰值 in-flight（fake gen 内部已维护 in_flight 列表）
        peak["v"] = max(peak["v"], len(in_flight))

    import shared.vimax_bridge as bridge

    async def fake_gen(shot, *a, **k):
        in_flight.append(shot["index"])
        _track()
        await asyncio.sleep(0.15)
        in_flight.remove(shot["index"])
        return {"ff_path": None, "lf_path": "lf.png", "keyframes_paths": []}

    monkeypatch.setattr(bridge, "gen_shot_frames", fake_gen)

    asyncio.run(gen_all_frames(script_path, tmp_path, concurrency=2))
    assert peak["v"] <= 2, f"峰值 in-flight {peak['v']} 超过并发数 2"
    assert peak["v"] == 2, f"并发未生效（峰值 {peak['v']}）"


def test_serial_fallback_concurrency_one(tmp_path, monkeypatch):
    """concurrency=1：串行回退（同时 in-flight 恒为 1）."""
    from step02a_frame_gen.main import gen_all_frames

    idxs = [1, 2, 3]
    script_path = _make_script(idxs, tmp_path)
    in_flight: list[int] = []
    peak = {"v": 0}

    import shared.vimax_bridge as bridge

    async def fake_gen(shot, *a, **k):
        in_flight.append(shot["index"])
        peak["v"] = max(peak["v"], len(in_flight))
        await asyncio.sleep(0.05)
        in_flight.remove(shot["index"])
        return {"ff_path": None, "lf_path": "lf.png", "keyframes_paths": []}

    monkeypatch.setattr(bridge, "gen_shot_frames", fake_gen)

    results = asyncio.run(gen_all_frames(script_path, tmp_path, concurrency=1))
    assert set(results) == {1, 2, 3}
    assert peak["v"] == 1, f"串行回退峰值 in-flight 应为 1，实际 {peak['v']}"


def test_parallel_faster_than_serial_timing(tmp_path, monkeypatch):
    """吞吐对照：4 镜 × 0.2s 假生成，并发 4 应显著快于串行总时长."""
    from step02a_frame_gen.main import gen_all_frames

    idxs = [1, 2, 3, 4]
    script_path = _make_script(idxs, tmp_path)

    import shared.vimax_bridge as bridge

    async def fake_gen(shot, *a, **k):
        await asyncio.sleep(0.2)
        return {"ff_path": None, "lf_path": "lf.png", "keyframes_paths": []}

    monkeypatch.setattr(bridge, "gen_shot_frames", fake_gen)

    t0 = time.monotonic()
    asyncio.run(gen_all_frames(script_path, tmp_path / "a", concurrency=4))
    parallel_dt = time.monotonic() - t0

    t0 = time.monotonic()
    asyncio.run(gen_all_frames(script_path, tmp_path / "b", concurrency=1))
    serial_dt = time.monotonic() - t0

    assert parallel_dt < serial_dt * 0.75, (
        f"并发 {parallel_dt:.2f}s 未显著快于串行 {serial_dt:.2f}s")
