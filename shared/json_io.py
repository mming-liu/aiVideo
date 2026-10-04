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


def _swap_tmp_path(path) -> Path:
    """两阶段写盘的临时文件路径（`<path>.tmp`，与原实现同一命名）."""
    return Path(str(Path(path)) + ".tmp")


def _unlink_quiet(path) -> None:
    try:
        if Path(path).exists():
            Path(path).unlink()
    except OSError:
        pass


def prepare_json_swap(path, obj, **kwargs) -> Path:
    """**两阶段写盘的第一阶段**：把 `obj` 写满 `<path>.tmp` 并 fsync，**不动原文件**.

    为什么拆开（复核 2026-10-02 之八 P1）：`atomic_dump_json` 把"准备"与"替换"合成
    一步，调用方**没有地方**在两者之间再核一次截止——于是准备阶段的耗时漏出去，旧文件
    被换成了新内容，而这一轮又按"没时间了"收口（**既发布了、又没下发**）。拆开之后调用
    方可以在 `commit_json_swap` 之前再判一次：过点就 `discard_json_swap` 删掉临时文件，
    盘上那份一个字节不改。

    失败（不可序列化 / 磁盘满 / 中断）时清掉残留 .tmp 并**抛出**，原文件始终完整。

    Returns: 临时文件路径（交给 `commit_json_swap` 或 `discard_json_swap`）。
    """
    path = Path(path)
    tmp_path = _swap_tmp_path(path)
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(obj, f, **kwargs)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        _unlink_quiet(tmp_path)
        raise
    return tmp_path


def commit_json_swap(path, tmp_path) -> None:
    """**两阶段写盘的第二阶段**：`os.replace(tmp, path)`（同卷内原子替换）.

    只有走到这里，盘上那份才被换成新内容；失败时清掉临时文件并抛出。
    """
    try:
        os.replace(Path(tmp_path), Path(path))
    except Exception:
        _unlink_quiet(tmp_path)
        raise


def discard_json_swap(tmp_path) -> None:
    """放弃本次准备：删掉临时文件，**原文件保持原样**（幂等，不存在也算成功）."""
    _unlink_quiet(tmp_path)


def atomic_dump_json(path, obj, **kwargs) -> None:
    """原子写 JSON：先写 .tmp，os.replace 替换原文件.

    即使 json.dump 中途抛错（不可序列化对象/磁盘满/中断），原文件仍完好
    ——避免"半截文件污染原 JSON"事故（v6.x 实抽）。
    kwargs 原样透传 json.dump（如 ensure_ascii=False, indent=2, default=...）。

    需要"准备完成后、替换之前还能再判一次"的调用方，用
    `prepare_json_swap` / `commit_json_swap` / `discard_json_swap`（本函数就是
    这两步的组合，行为与拆分前逐字一致）。
    """
    commit_json_swap(path, prepare_json_swap(path, obj, **kwargs))
