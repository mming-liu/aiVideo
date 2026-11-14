# -*- coding: utf-8 -*-
"""图片/视频 POST 的跨进程预算、额度熔断与请求结果账本（2026-11-08 WP7-Prep）.

与 Step01 的 shared/send_budget.py 同构，但面向 POST 次数：

- 启用条件（U6-FIN WP1a 起：账本存在即启用，未设环境变量不再等于裸奔）：
  1. 显式设置 *_BUDGET_TOTAL > 0 → 按该上限启用（显式覆盖一切）；
  2. 显式设置 *_BUDGET_TOTAL <= 0 → 显式关闭（唯一合法的"我不想要门禁"写法）；
  3. 未设置 *_BUDGET_TOTAL 但章节目录下已有账本（默认名见 _KINDS）→
     **自动绑定该账本并读取其 limit 作为上限**（`source=ledger_autodiscovered`）；
  4. 都没有 → 不启用，行为与历史版本一致。
  2026-11-14 R6 S07 实测事故：人手漏传 `STEP02A_IMAGE_BUDGET_TOTAL` 时，
  章目录里明明躺着 `used=31/limit=172` 的账本，护栏却被静默旁路。自动发现
  就是堵这个洞——"账本在、门禁就在"，只有显式写 0 才能关。
- 账本落在章节目录（或 *_BUDGET_FILE），每次发送前原子检查并 +1；
  进程内 threading 锁 + 跨进程文件锁（Windows msvcrt / POSIX fcntl）。
- 文件损坏/不可读 ⇒ 拒绝发送（fail-closed）。自动发现到的账本即使上限读不出
  也照样启用（limit=0 交由 :func:`_read` 判死），不会因为"读不懂"而放行。
- 上游明确返回额度耗尽（403 SetLimitExceeded / QuotaExceeded）时写入熔断标记，
  重启后仍拒绝发送；需显式 reset 或人工删账本。

M0.3（2026-11-09）：`used` 仍是**已发送 POST 次数**（口径不变），另加**互斥**
结果分类：

    generated_ok          请求成功并拿到可用图片
    account_quota_refused 上游明确因账户额度/配额拒绝（403/429 限额类）
    transport_failed      网络、5xx、解析或下载失败等请求级失败
    qc_rejected           已生成但被本地视觉质检拒收（从 generated_ok 改判）

恒等式：`sum(outcomes) + pending == used`。`pending` 是已扣预算、结果尚未回写的
请求数（发送与下载/解析之间）。旧账本（只有 kind/used/limit）在读取时按
`generated_ok = used - pending` 派生迁移视图，**不改写文件**；显式
`migrate(kind)` 才把 schema_version=2 与 outcomes 落盘。真实账本在只读核对中
保持原样。
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path

_KINDS = {
    "image": ("STEP02A_IMAGE_BUDGET_FILE", "STEP02A_IMAGE_BUDGET_TOTAL",
              "step02a_image_budget.json"),
    "video": ("STEP02B_VIDEO_BUDGET_FILE", "STEP02B_VIDEO_BUDGET_TOTAL",
              "step02b_video_budget.json"),
}

#: 结果分类（互斥，顺序即报告顺序）。
OUTCOMES = ("generated_ok", "account_quota_refused",
            "transport_failed", "qc_rejected")
SCHEMA_VERSION = 2

_LOCK = threading.Lock()
_QUOTA: dict[str, str] = {}
_QUOTA_ERROR_MARKERS = (
    "setlimitexceeded", "quotaexceeded", "quota exceeded", "quota_exceeded",
    "额度耗尽", "额度不足", "配额不足", "配额耗尽",
)


class PostBudgetExhausted(RuntimeError):
    """预算/额度已耗尽：调用方必须在发送前停止."""


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _limit_from_ledger(path: Path) -> int:
    """从已有账本读上限；读不出返回 0（由 :func:`_read` 兜底 fail-closed）.

    只认正整数。`limit` 是当前口径，`budget_total` / `total` 兼容早期手写账本。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    for key in ("limit", "budget_total", "total"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            continue
        return value
    return 0


def _default_ledger(default_name: str) -> Path:
    from shared.paths import chapter_dir
    return Path(chapter_dir()) / default_name


def _config_detail(kind: str, *, discover: bool = True) -> dict:
    """解析账本路径与上限；返回 {path, limit, source}.

    source 取值：`env`（显式上限）/ `explicit_off`（显式 0 关闭）/
    `ledger_env_file`（只给了 FILE，上限取自该账本）/
    `ledger_autodiscovered`（环境变量全空，章目录里已有账本）/
    `disabled`（无账本、也没给配置）.

    `discover=False` 只认显式启用：破坏性操作（如 :func:`reset`）绝不能顺手
    删掉一个"只是恰好存在"的真实账本。
    """
    try:
        env_file, env_total, default_name = _KINDS[kind]
    except KeyError as exc:
        raise ValueError("未知预算类型: %r" % kind) from exc
    explicit_file = (os.getenv(env_file) or "").strip()
    raw_total = os.getenv(env_total)
    explicit_total = raw_total is not None and str(raw_total).strip() != ""
    if explicit_total:
        limit = _int(raw_total, 0)
        if limit <= 0:
            return {"path": None, "limit": 0, "source": "explicit_off"}
        path = Path(explicit_file) if explicit_file else _default_ledger(default_name)
        return {"path": path, "limit": limit, "source": "env"}
    if not discover:
        return {"path": None, "limit": 0, "source": "disabled"}
    if explicit_file:
        path = Path(explicit_file)
        if not path.exists():
            return {"path": None, "limit": 0, "source": "disabled"}
        return {"path": path, "limit": _limit_from_ledger(path),
                "source": "ledger_env_file"}
    path = _default_ledger(default_name)
    if not path.exists():
        return {"path": None, "limit": 0, "source": "disabled"}
    return {"path": path, "limit": _limit_from_ledger(path),
            "source": "ledger_autodiscovered"}


def _config(kind: str, *, discover: bool = True):
    """返回 (ledger_path | None, limit)；未启用返回 (None, 0)."""
    detail = _config_detail(kind, discover=discover)
    return detail["path"], detail["limit"]


def _empty_outcomes() -> dict:
    return {name: 0 for name in OUTCOMES}


def _normalize_outcomes(data: dict, used: int) -> dict:
    """读取/派生 outcomes 与 pending；旧账本按 generated_ok 迁移到内存视图.

    只做**读时归一**，不写文件。任何字段类型/范围/恒等式不成立一律 fail-closed。
    """
    version = data.get("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version not in (1, 2):
        raise PostBudgetExhausted("预算账本 schema_version 非法（拒绝发送）: %r"
                                  % (version,))
    pending = data.get("pending", 0)
    if isinstance(pending, bool) or not isinstance(pending, int) or pending < 0:
        raise PostBudgetExhausted("预算账本 pending 非法（拒绝发送）: %r" % (pending,))
    raw = data.get("outcomes")
    if raw is None:
        # 旧账本：已发送的 POST 没有结果回写，按「已生成」派生；不丢 used 口径。
        outcomes = _empty_outcomes()
        outcomes["generated_ok"] = max(0, used - pending)
    else:
        if not isinstance(raw, dict):
            raise PostBudgetExhausted("预算账本 outcomes 不是对象（拒绝发送）")
        unknown = set(raw) - set(OUTCOMES)
        if unknown:
            raise PostBudgetExhausted("预算账本 outcomes 含未知分类（拒绝发送）: %s"
                                      % ", ".join(sorted(str(x) for x in unknown)))
        outcomes = {}
        for name in OUTCOMES:
            value = raw.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PostBudgetExhausted(
                    "预算账本 outcomes.%s 非法（拒绝发送）: %r" % (name, value))
            outcomes[name] = value
    if sum(outcomes.values()) + pending != used:
        raise PostBudgetExhausted(
            "预算账本结果分类与 used 不一致（拒绝发送）：sum(outcomes)=%d + pending=%d != used=%d"
            % (sum(outcomes.values()), pending, used))
    data["schema_version"] = version
    data["outcomes"] = outcomes
    data["pending"] = pending
    return data


def _read(path: Path, kind: str, expected_limit: int) -> dict:
    """读取并严格校验账本；缺失返回初始状态；字段损坏/身份不符一律拒绝.

    不允许 used 缺失/负数/字符串被当成 0（那会静默恢复额度），也不允许
    账本 limit 与本次 TOTAL 不一致（那会在恢复时静默扩大/缩小预算）。
    """
    if not path.exists():
        return {"kind": kind, "used": 0, "limit": int(expected_limit),
                "quota_exhausted": "", "schema_version": SCHEMA_VERSION,
                "outcomes": _empty_outcomes(), "pending": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PostBudgetExhausted("预算账本不可读（拒绝发送）: %s" % exc) from exc
    if not isinstance(data, dict):
        raise PostBudgetExhausted("预算账本不是 JSON 对象（拒绝发送）: %s" % path)
    if data.get("kind") != kind:
        raise PostBudgetExhausted(
            "预算账本身份不符：kind=%r，期望 %r（拒绝发送）"
            % (data.get("kind"), kind))
    used = data.get("used")
    if isinstance(used, bool) or not isinstance(used, int) or used < 0:
        raise PostBudgetExhausted(
            "预算账本 used 非法：%r（拒绝发送，禁止按 0 重置）" % (used,))
    limit = data.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise PostBudgetExhausted(
            "预算账本 limit 非法：%r（拒绝发送）" % (limit,))
    if int(expected_limit) and limit != int(expected_limit):
        raise PostBudgetExhausted(
            "预算身份不一致：账本 limit=%d，本次 TOTAL=%d；"
            "不得静默扩大/重置，如需改预算请显式 reset"
            % (limit, int(expected_limit)))
    quota = data.get("quota_exhausted", "")
    if quota not in (None, "") and not isinstance(quota, str):
        raise PostBudgetExhausted("预算账本 quota_exhausted 非法（拒绝发送）")
    data["quota_exhausted"] = str(quota or "")
    return _normalize_outcomes(data, used)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["schema_version"] = SCHEMA_VERSION
    data.setdefault("outcomes", _empty_outcomes())
    data.setdefault("pending", 0)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def _file_lock(path: Path):
    """跨进程排他锁：锁文件与账本同目录."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _event(event_name: str, kind: str, request_id: str | None, **fields) -> None:
    """请求事件是证据，不是发送前提：任何异常都不得改变预算语义."""
    try:
        from shared import request_events
        request_events.emit(event_name, request_id=request_id or "", kind=kind, **fields)
    except Exception:
        pass


def acquire(kind: str, amount: int = 1, request_id: str | None = None) -> int:
    """发送前原子扣减；超限/熔断抛 PostBudgetExhausted；返回已用次数.

    request_id 用于把「这次扣减」与后续响应/图片/QC 事件关联；不传时由调用方
    上下文（request_events.begin_request）提供，没有就留空——不编造身份。
    """
    amount = max(1, int(amount))
    path, limit = _config(kind)
    if path is None:
        if _QUOTA.get(kind):
            raise PostBudgetExhausted("额度已熔断（%s）：%s" % (kind, _QUOTA[kind]))
        return 0
    with _LOCK, _file_lock(path):
        data = _read(path, kind, limit)
        quota = data["quota_exhausted"]
        if quota:
            _QUOTA[kind] = quota
            raise PostBudgetExhausted("额度已熔断（%s）：%s" % (kind, quota))
        used = data["used"]
        effective = data["limit"]
        if used + amount > effective:
            raise PostBudgetExhausted(
                "POST 预算耗尽（%s）：已用 %d/%d" % (kind, used, effective))
        used += amount
        data.update({"kind": kind, "used": used, "limit": effective,
                     "pending": int(data.get("pending") or 0) + amount,
                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        data.pop("quota_exhausted", None)
        _write(path, data)
        _event("request_sent", kind, request_id,
               amount=amount, used=used, limit=effective)
        return used


def mark_quota_exhausted(kind: str, message: str) -> None:
    """记录上游额度耗尽；有账本时落盘，重启后仍拒绝发送."""
    text = str(message or "quota exhausted")[:500]
    _QUOTA[kind] = text
    _event("account_refused", kind, None, source="quota_marker", detail=text)
    path, _limit = _config(kind)
    if path is None:
        return
    with _LOCK, _file_lock(path):
        data = _read(path, kind, _limit)
        data.update({"kind": kind, "quota_exhausted": text,
                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        _write(path, data)


def quota_exhausted(kind: str) -> str:
    """返回熔断原因（空=未熔断）；账本损坏按熔断处理（fail-closed）."""
    if _QUOTA.get(kind):
        return _QUOTA[kind]
    path, _limit = _config(kind)
    if path is None:
        return ""
    try:
        with _LOCK, _file_lock(path):
            data = _read(path, kind, _limit)
    except PostBudgetExhausted as exc:
        return str(exc)
    text = str(data.get("quota_exhausted") or "")
    if text:
        _QUOTA[kind] = text
    return text


def usage(kind: str) -> dict:
    """读取当前账本（诊断/报告用）；未启用返回 {}.

    额外回报 `budget_source`，让调用方/日志能分辨"环境变量显式启用"与
    "账本自动发现"——2026-11-14 的漏传事故正是因为这两者在输出上不可分辨。
    """
    detail = _config_detail(kind)
    path, limit = detail["path"], detail["limit"]
    if path is None:
        return {}
    try:
        with _LOCK, _file_lock(path):
            data = _read(path, kind, limit)
    except PostBudgetExhausted as exc:
        return {"error": str(exc), "path": str(path), "limit": limit,
                "budget_source": detail["source"]}
    data.setdefault("limit", limit)
    data["path"] = str(path)
    data["budget_source"] = detail["source"]
    return data


def describe(kind: str, *, discover: bool = True) -> dict:
    """只读窗口：这次会不会有门禁、绑到哪个账本、还剩多少.

    **不建锁文件、不写盘**——plan-only / 预检必须用它。:func:`usage` 会开
    `*.json.lock`，在"只读预检对章目录零写入"的场合下那是一笔多余写操作。

    不可读/字段非法时不抛异常，改为在返回值里带 `error`，由调用方决定是否阻断
    （真正的发送门禁仍由 :func:`acquire` 的 fail-closed 兜底）。
    """
    detail = _config_detail(kind, discover=discover)
    path = detail["path"]
    if path is None:
        return {"enabled": False, "source": detail["source"]}
    info = {"enabled": True, "source": detail["source"],
            "path": str(path), "limit": detail["limit"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        info["error"] = "账本不可读：%s" % exc
        return info
    if not isinstance(data, dict):
        info["error"] = "账本不是 JSON 对象"
        return info
    used = data.get("used")
    limit = data.get("limit")
    info["used"] = used
    info["pending"] = data.get("pending", 0)
    if isinstance(limit, int) and not isinstance(limit, bool):
        info["limit"] = limit
    info["quota_exhausted"] = str(data.get("quota_exhausted") or "")
    if (isinstance(used, int) and not isinstance(used, bool)
            and isinstance(info.get("limit"), int)):
        info["remaining"] = max(0, info["limit"] - used)
    return info


#: 预算结果分类 → 请求事件名（终局事件只由真实终局触发）。
_OUTCOME_EVENTS = {
    "generated_ok": "response_received",
    "account_quota_refused": "account_refused",
    "transport_failed": "transport_failed",
    "qc_rejected": "qc_rejected",
}


def record(kind: str, outcome: str, amount: int = 1,
           request_id: str | None = None) -> dict:
    """回写**已发送请求**的结果分类；返回最新账本视图.

    `used` 口径不变（acquire 已 +1）；这里把对应数量的 `pending` 结清并计入
    互斥分类。调用方必须只在请求真正有终局结果后调用（成功/额度拒绝/传输失败），
    不得用猜测的结果回写。未启用账本时返回 `{}`。
    """
    outcome = str(outcome or "")
    if outcome not in OUTCOMES:
        raise ValueError("未知图片/视频请求结果分类: %r（合法：%s）"
                         % (outcome, ", ".join(OUTCOMES)))
    amount = max(1, int(amount))
    path, limit = _config(kind)
    if path is None:
        return {}
    with _LOCK, _file_lock(path):
        data = _read(path, kind, limit)
        pending = int(data.get("pending") or 0)
        if pending < amount:
            # 兼容旧调用/迁移：没有 pending 记录时把本次结果视为一次未登记请求，
            # 同步补 used（仍保持 sum(outcomes)+pending == used）。
            data["used"] = int(data["used"]) + (amount - pending)
            pending = amount
        data["pending"] = pending - amount
        data["outcomes"][outcome] += amount
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(path, data)
        _event(_OUTCOME_EVENTS.get(outcome, "response_received"), kind, request_id,
               outcome=outcome, amount=amount,
               used=data["used"], pending=data["pending"])
        data["path"] = str(path)
        return data


def reclassify(kind: str, from_outcome: str, to_outcome: str,
               amount: int = 1) -> bool:
    """把一个已记录分类改判为另一个（如 generated_ok → qc_rejected）.

    只有源分类计数足够时才改；否则返回 False 且**不改账本**（不制造负计数）。
    """
    for name in (from_outcome, to_outcome):
        if name not in OUTCOMES:
            raise ValueError("未知图片/视频请求结果分类: %r" % (name,))
    amount = max(1, int(amount))
    path, limit = _config(kind)
    if path is None:
        return False
    with _LOCK, _file_lock(path):
        data = _read(path, kind, limit)
        outcomes = data["outcomes"]
        if outcomes.get(from_outcome, 0) < amount:
            return False
        outcomes[from_outcome] -= amount
        outcomes[to_outcome] += amount
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(path, data)
        return True


def record_qc_rejected(kind: str, amount: int = 1) -> bool:
    """把已生成图片改判为质检拒收；无 generated_ok 可扣时返回 False."""
    return reclassify(kind, "generated_ok", "qc_rejected", amount)


def migrate(kind: str) -> dict:
    """把旧账本显式迁移到 schema_version=2（保留 used/limit/quota 与更新时间）.

    只补 outcomes/pending/schema_version；**不 reset、不改 used 计数口径**。
    """
    path, limit = _config(kind)
    if path is None:
        return {}
    with _LOCK, _file_lock(path):
        data = _read(path, kind, limit)
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(path, data)
        data["path"] = str(path)
        return data


def clear_quota(kind: str) -> None:
    """清除额度熔断标记但保留已用次数（账户限额调整后恢复用）."""
    _QUOTA.pop(kind, None)
    path, limit = _config(kind)
    if path is None:
        return
    with _LOCK, _file_lock(path):
        data = _read(path, kind, limit)
        data.pop("quota_exhausted", None)
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(path, data)


def reset(kind=None) -> None:
    """测试/人工重置：清内存标记并删除账本（含锁文件）.

    **只删显式启用的账本**（环境变量给了上限的那些）：自动发现出来的真实账本
    不在删除范围内——否则一次 `reset()` 就能抹掉生产账本。
    """
    kinds = [kind] if kind else list(_KINDS)
    for item in kinds:
        _QUOTA.pop(item, None)
        path, _limit = _config(item, discover=False)
        if path is None:
            continue
        for target in (path, path.with_suffix(path.suffix + '.lock'),
                       path.with_suffix(path.suffix + '.tmp')):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass


def is_quota_error(text) -> bool:
    """判断上游错误是否明确表示账户额度/配额耗尽."""
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _QUOTA_ERROR_MARKERS)


__all__ = ["OUTCOMES", "PostBudgetExhausted", "acquire", "clear_quota",
           "describe", "is_quota_error", "mark_quota_exhausted", "migrate",
           "quota_exhausted", "record", "record_qc_rejected", "reclassify",
           "reset", "usage"]
