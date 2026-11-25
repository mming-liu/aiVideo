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


def reserve(chapter_path, n, *, tag=""):
    """Reserve `n` sends against the chapter cap; returns reservation id."""
    n = int(n)
    if n <= 0:
        raise QuotaError("reservation must be positive")
    with _Lock(chapter_path):
        data = _read(chapter_path)
        used, cap = int(data.get("used") or 0), int(data.get("max") or 20)
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
    spent = int(spent)
    with _Lock(chapter_path):
        data = _read(chapter_path)
        row = _find(data, reservation_id)
        n = int(row.get("n") or 0)
        if spent > n:
            raise QuotaError("spent %d exceeds reservation %d" % (spent, n))
        data["used"] = int(data.get("used") or 0) - (n - spent)
        data["reservations"] = [x for x in data["reservations"]
                                if x.get("id") != reservation_id]
        data.setdefault("attempts", []).extend(list(attempts or []))
        _write(chapter_path, data)


def settle_unknown(chapter_path, reservation_id, *, reason=""):
    """Batch missing/unreadable: keep the whole reservation, mark it unknown."""
    with _Lock(chapter_path):
        data = _read(chapter_path)
        row = _find(data, reservation_id)
        row["state"] = "unknown"
        row["unknown_reason"] = str(reason or "batch ledger missing/unreadable")[:200]
        row["unknown_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _write(chapter_path, data)


def pending(chapter_path):
    return _read(chapter_path).get("reservations") or []
