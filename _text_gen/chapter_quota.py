"""Chapter-level POST quota reservation shared by guarded Step01 runners.

A batch ledger alone cannot stop "chapter already at 19, new batch sends 4": the chapter
total must be reserved **before** the first HTTP call. This module keeps the chapter ledger
authoritative and uses an OS file lock so concurrent wrappers cannot both pass the check.

Settlement is conservative:
- readable batch ledger: refund reserved-but-unspent quota;
- missing/unreadable batch ledger: **do not refund**; mark the reservation unknown and keep
  the full reservation counted. A lost record must never look like a free send.
"""
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path


ENV_FILE = "STEP01_CHAPTER_QUOTA_FILE"


class QuotaError(Exception):
    pass


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, data):
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, p)


class _Lock:
    def __init__(self, path, timeout=10.0):
        self.path = str(path) + ".lock"
        self.timeout = timeout
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "a+b")
        deadline = time.time() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() > deadline:
                    raise QuotaError("chapter ledger lock timeout: %s" % self.path)
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()


def _int_ok(value, *, minimum=0):
    return type(value) is int and value >= minimum


def reserve(chapter_path, n, *, tag=""):
    """Reserve `n` sends against the chapter cap; returns reservation id."""
    n = int(n)
    if n <= 0:
        raise QuotaError("reservation must be positive")
    with _Lock(chapter_path):
        data = _read(chapter_path)
        if not isinstance(data, dict) or not _int_ok(data.get("used")) \
                or not _int_ok(data.get("max"), minimum=1):
            raise QuotaError("chapter ledger structure invalid: %r" % (data,))
        used, cap = data["used"], data["max"]
        if used + n > cap:
            raise QuotaError("chapter quota would exceed cap: used=%d reserve=%d max=%d"
                             % (used, n, cap))
        data["used"] = used + n
        rid = uuid.uuid4().hex[:12]
        data.setdefault("reservations", []).append({
            "id": rid, "n": n, "tag": str(tag), "state": "reserved",
            "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")})
        _write(chapter_path, data)
    return rid


def _find(data, rid):
    row = next((x for x in (data.get("reservations") or []) if x.get("id") == rid), None)
    if row is None:
        raise QuotaError("reservation not found: %s" % rid)
    return row


def settle(chapter_path, reservation_id, spent, attempts=()):
    """Settle a readable batch: refund unspent quota, keep actual attempts."""
    if not _int_ok(spent):
        raise QuotaError("spent must be a non-negative int: %r" % (spent,))
    with _Lock(chapter_path):
        data = _read(chapter_path)
        if not isinstance(data, dict) or not _int_ok(data.get("used")):
            raise QuotaError("chapter ledger structure invalid")
        row = _find(data, reservation_id)
        n = row.get("n")
        if not _int_ok(n, minimum=1) or spent > n:
            raise QuotaError("spent %r outside reservation %r" % (spent, n))
        data["used"] = int(data.get("used") or 0) - (n - spent)
        data["reservations"] = [x for x in data["reservations"]
                                if x.get("id") != reservation_id]
        data.setdefault("attempts", []).extend(list(attempts or []))
        _write(chapter_path, data)


def settle_unknown(chapter_path, reservation_id, *, reason=""):
    """Batch missing/unreadable: keep the whole reservation, mark it unknown."""
    with _Lock(chapter_path):
        data = _read(chapter_path)
        if not isinstance(data, dict) or not _int_ok(data.get("used")):
            raise QuotaError("chapter ledger structure invalid")
        row = _find(data, reservation_id)
        row["state"] = "unknown"
        row["unknown_reason"] = str(reason or "batch ledger missing/unreadable")[:200]
        row["unknown_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _write(chapter_path, data)


def settle_batch(chapter_path, reservation_id, batch_path):
    """Settle from a batch file; any structural doubt is unknown (never refund)."""
    try:
        b = _read(batch_path)
    except BaseException as exc:  # noqa: BLE001
        settle_unknown(chapter_path, reservation_id, reason="batch missing/unreadable: %s" % exc)
        return {"status": "unknown", "chapter_after": _read(chapter_path).get("used")}
    if (not isinstance(b, dict) or not _int_ok(b.get("used"))
            or not isinstance(b.get("attempts", []), list)):
        settle_unknown(chapter_path, reservation_id,
                       reason="batch structure invalid: %r" % (b,))
        return {"status": "unknown", "chapter_after": _read(chapter_path).get("used")}
    if b.get("reservation_id") is not None and str(b.get("reservation_id")) != str(reservation_id):
        settle_unknown(chapter_path, reservation_id,
                       reason="batch reservation_id mismatch: %r != %r"
                       % (b.get("reservation_id"), reservation_id))
        return {"status": "unknown", "chapter_after": _read(chapter_path).get("used")}
    try:
        settle(chapter_path, reservation_id, b["used"], b.get("attempts") or [])
    except QuotaError as exc:
        settle_unknown(chapter_path, reservation_id, reason="batch settle invalid: %s" % exc)
        return {"status": "unknown", "chapter_after": _read(chapter_path).get("used")}
    return {"status": "settled", "spent": b["used"],
            "chapter_after": _read(chapter_path).get("used")}


def reserve_batch(chapter_path, batch_path, n, *, tag="") -> str:
    """在章节账本预占 `n` 次，并写出带 `reservation_id` 的批次账本."""
    bp = Path(batch_path)
    if bp.exists():
        raise QuotaError("batch ledger already exists: %s" % bp)
    rid = reserve(chapter_path, n, tag=tag or bp.name)
    _write(bp, {"max": int(n), "used": 0, "attempts": [],
                "chapter_ledger": str(chapter_path), "reservation_id": rid})
    return rid


def has_reservation(chapter_path, reservation_id) -> bool:
    """检查章节账本中是否存在处于 `reserved` 状态的预占 ID."""
    rid = str(reservation_id or "").strip()
    if not rid:
        return False
    with _Lock(chapter_path):
        try:
            data = _read(chapter_path)
        except BaseException:  # noqa: BLE001
            return False
        if not isinstance(data, dict):
            return False
        return any(
            isinstance(r, dict) and str(r.get("id") or "") == rid
            and r.get("state") == "reserved"
            for r in (data.get("reservations") or [])
        )


def consume_one(chapter_path, *, provider="", endpoint="", call_id="") -> int:
    """逐 POST 原子扣减 1 次章节额度（直接调用未走批次预占时使用）."""
    with _Lock(chapter_path):
        try:
            data = _read(chapter_path)
        except BaseException as exc:  # noqa: BLE001
            raise QuotaError("chapter ledger unreadable: %s" % exc) from exc
        if not isinstance(data, dict) or not _int_ok(data.get("used")) \
                or not _int_ok(data.get("max"), minimum=1):
            raise QuotaError("chapter ledger structure invalid: %r" % (data,))
        if data["used"] >= data["max"]:
            raise QuotaError("chapter quota exhausted: used=%d max=%d"
                             % (data["used"], data["max"]))
        data["used"] += 1
        data.setdefault("attempts", []).append({
            "n": data["used"],
            "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": str(provider or ""),
            "endpoint": str(endpoint or ""),
            "call_id": str(call_id or ""),
            "direct": True,
        })
        _write(chapter_path, data)
        return data["used"]


def pending(chapter_path):
    return _read(chapter_path).get("reservations") or []


def check_available(chapter_path) -> dict:
    """发送边界预检：账本必须可读、结构合法且 `used < max`（只读不扣额）."""
    with _Lock(chapter_path):
        try:
            data = _read(chapter_path)
        except BaseException as exc:  # noqa: BLE001
            raise QuotaError("chapter ledger unreadable: %s" % exc) from exc
        if not isinstance(data, dict) or not _int_ok(data.get("used")) \
                or not _int_ok(data.get("max"), minimum=1):
            raise QuotaError("chapter ledger structure invalid: %r" % (data,))
        if data["used"] >= data["max"]:
            raise QuotaError("chapter quota exhausted: used=%d max=%d"
                             % (data["used"], data["max"]))
        return {"used": data["used"], "max": data["max"],
                "remaining": data["max"] - data["used"]}
