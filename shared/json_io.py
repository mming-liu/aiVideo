"""shared.json_io - 原子 JSON 落盘工具（2026-09-13 审计 #7 收敛）.

背景：step02a 早有正确的 `_atomic_dump_json`，但 step01/step02b/audio_pregen/
continuity/vimax_bridge 等仍直接 `json.dump` 覆盖写——磁盘满、序列化异常或
进程中断会留下半截 JSON，导致跨阶段恢复困难。现抽成共享工具，全链路统一。

用法：
    from shared.json_io import atomic_dump_json
    atomic_dump_json(path, obj, ensure_ascii=False, indent=2)
"""
import json
import os
from pathlib import Path


def atomic_dump_json(path, obj, **kwargs) -> None:
    """原子写 JSON：先写 .tmp，os.replace 替换原文件.

    即使 json.dump 中途抛错（不可序列化对象/磁盘满/中断），原文件仍完好
    ——避免"半截文件污染原 JSON"事故（v6.x 实抽）。
    kwargs 原样透传 json.dump（如 ensure_ascii=False, indent=2, default=...）。
    """
    path = Path(path)
    tmp_path = Path(str(path) + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(obj, f, **kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # 清理残留 .tmp，原文件保持完整
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
