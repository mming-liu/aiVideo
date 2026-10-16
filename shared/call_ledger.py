# -*- coding: utf-8 -*-
"""**模型调用台账**：每次文本生成调用记一条带 `run_id` 的记录（2026-10-02 复核）.

为什么需要它（今天日志复核的确认结论）：

  · `step01_metrics.json` 里 `calls: {}` —— 今天 6 次真实运行、每轮 4 次模型调用，
    **一条调用记录都没有**，事后无法对账成本、无法区分「重试耗尽」与「模型违约」；
  · provider 的 DEBUG 行只有 `model=…/response_format=…`，**没有 run_id**，
    同一份 append-only 的章节日志里混着多轮运行，谁调的不清不楚。

落点选在 `_text_gen.service.create_service()`（**工厂代理**）而不是 4 个 provider
里各写一遍：四家 provider 共用同一出口，新接一家自动带上，不会漏。

**完整正文单独留档**（2026-10-02 复核之七）：JSONL 只放索引与摘要，模型的**完整正文**
按 `call_id` 存成独立文件（`step01_call_bodies/<call_id>.txt`），行里记 `response_path` /
`response_sha1` / `response_bytes` ——摘要可回读核验，非法与截断的响应也**原样保留**
（日志对响应有 ~1000 字符截断，靠日志前缀猜"模型到底回了什么"已经吃过一次亏）。
留档失败**明确告警并标记证据缺失**（`archived=False` + `archive_error`），**绝不**声称
"已留档"。

写入策略：**尽力而为**（台账写失败绝不影响生成）。路径由调用方通过
`set_ledger_path()` 指定（step01 传章节目录下的 `step01_calls.jsonl`）；没设路径就
退化成"只打一行带 run_id 的日志"，不写盘。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

_LOCK = threading.Lock()
_LEDGER_PATH: Path | None = None

#: 完整正文的存放子目录（与台账同目录）：`<章节目录>/step01_call_bodies/<call_id>.txt`
BODIES_DIRNAME = "step01_call_bodies"


def set_ledger_path(path) -> None:
    """指定本轮台账文件（`None` = 关闭落盘，只保留日志行）."""
    global _LEDGER_PATH
    _LEDGER_PATH = Path(path) if path else None


def ledger_path() -> Path | None:
    return _LEDGER_PATH


def bodies_dir(ledger=None) -> Path | None:
    """完整正文的目录（台账文件旁边）；没设台账路径 ⇒ `None`（不落盘）."""
    _p = Path(ledger) if ledger else _LEDGER_PATH
    return None if _p is None else Path(_p).parent / BODIES_DIRNAME


def new_call_id() -> str:
    """**调用前**生成的唯一 `call_id`（并发安全；同一进程/同一 run 内不重复）."""
    return "c" + uuid.uuid4().hex[:16]


def _describe(req) -> dict:
    """从请求里取**可对账**的少量字段（不落整份 prompt：体积与敏感度都不合适）.

    ⚠️ **但要有摘要**（2026-10-11 逐笔诊断的缺口）：只留 `schema/参数` 时，"这次请求的提示
    到底写没写清输出形状"**无法用本次运行的产物自证**——只能读当时的源码。实测一条修订拒收
    （`delta_delete_and_upsert_same_uid`）用归档响应**复现不出**，正是因为请求正文没留、无从
    判断当时发的是什么。这里补 `prompt_sha256`（**摘要，不是正文**）：不增体积、不含内容，
    但可以用"按当前源码 + 同样输入重建的提示"算出同一个值来**证明提示逐字未变**。
    """
    _schema = ""
    try:
        _schema = str((getattr(req, "json_schema", None) or {}).get("name") or "")
    except Exception:
        _schema = ""
    _sys = str(getattr(req, "system_prompt", "") or "")
    _user = str(getattr(req, "user_prompt", "") or "")
    _payload = ("system:" + _sys + "\nuser:" + _user).encode("utf-8")
    return {
        "max_tokens": getattr(req, "max_tokens", None),
        "temperature": getattr(req, "temperature", None),
        "schema": _schema,
        "purpose": str(getattr(req, "purpose", "") or ""),
        #: 提示的**逐字摘要**（`system` + `user`）——用于事后核对"发出去的提示没变"。
        "prompt_sha256": hashlib.sha256(_payload).hexdigest(),
        "prompt_chars": len(_sys) + len(_user),
    }


def _archive_body(call_id: str, text: str) -> dict:
    """把**完整正文**写成独立文件 → `{archived, path, sha1, bytes, error}`.

    空正文也写文件（0 字节 + 空串摘要）：**"这次调用回来了什么"是最基本的事实**，
    空与"没留档"必须能分辨（`archived=False` 才是留档失败）。
    """
    _dir = bodies_dir()
    if _dir is None:
        return {"archived": False, "response_path": "", "response_sha1": "",
                "response_bytes": 0, "archive_error": "未设台账路径（不落盘）"}
    _raw = (text or "").encode("utf-8")
    _path = Path(_dir) / ("%s.txt" % (call_id or "unknown"))
    try:
        Path(_dir).mkdir(parents=True, exist_ok=True)
        with open(_path, "wb") as f:
            f.write(_raw)
        return {"archived": True, "response_path": str(_path),
                "response_sha1": hashlib.sha1(_raw).hexdigest(),
                "response_bytes": len(_raw), "archive_error": ""}
    except OSError as exc:
        # **留档失败要看得见**（复核之七）：告警 + 行里标证据缺失，不假装留过档
        print("[WARN] 调用留档失败（**证据缺失**，正文未落盘）：%s（call_id=%s）"
              % (exc, call_id))
        return {"archived": False, "response_path": str(_path), "response_sha1": "",
                "response_bytes": len(_raw), "archive_error": str(exc)}


def _append_row(line: str, call_id: str) -> bool:
    """把一行追加进台账；失败 ⇒ **明确告警**并返回 False（绝不影响生成）.

    复核 2026-10-02 之八 P2：旧写法把整段追加包在 `record_call` 的大 try 里，
    追加失败**一声不响**地返回——台账少了一行，而调用方拿到的仍是 `call_id`，
    事后分不清"这次没调用"与"调用了但没落档"（证据链最不该有的歧义）。
    """
    _p = _LEDGER_PATH
    if _p is None:
        return False          # 没设台账路径 = 有意不落盘，不是故障，不打告警
    try:
        with _LOCK:
            _p.parent.mkdir(parents=True, exist_ok=True)
            with open(_p, "a", encoding="utf-8") as f:
                f.write(line + chr(10))
        return True
    except Exception as exc:                                        # noqa: BLE001
        print("[WARN] 调用台账**追加失败**（**证据缺失**，本次调用不在台账里）："
              "%s: %s（call_id=%s）" % (type(exc).__name__, exc, call_id))
        return False


def record_call(provider: str, req, result, *, elapsed_ms: float,
                model_default: str = "", call_id: str = "") -> str:
    """记一条调用（成功/失败都记）；**任何异常都吞掉**，绝不影响生成 → 返回 `call_id`.

    `model_default`：调用点没显式指定模型时**实际生效**的那个（provider 默认），
    否则台账里 `model` 留空、事后查不出用的哪个模型（2026-10-02 实跑所见）。
    """
    try:
        from shared.run_identity import current_run_id
        # 直接调用方没给 id 也要能定位（工厂代理总会给；这里兜底而不是留空）
        call_id = str(call_id or "").strip() or new_call_id()
        _usage = getattr(result, "usage", None) or {}
        _text = str(getattr(result, "text", "") or "")
        try:
            _arch = _archive_body(call_id, _text)
        except Exception as exc:      # 归档自己抛错也要**留行**并标证据缺失
            print("[WARN] 调用留档失败（**证据缺失**，正文未落盘）：%s: %s（call_id=%s）"
                  % (type(exc).__name__, exc, call_id))
            _arch = {"archived": False, "response_path": "", "response_sha1": "",
                     "response_bytes": len(_text.encode("utf-8")),
                     "archive_error": "%s: %s" % (type(exc).__name__, exc)}
        _rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_id": current_run_id(),
            # **每一次调用**的唯一标识：同一轮里的多次调用、并发调用都能各自定位
            "call_id": str(call_id or ""),
            "provider": str(provider or ""),
            "model": str(getattr(req, "model", "") or model_default or ""),
            "ok": bool(getattr(result, "success", False)),
            "truncated": bool(getattr(result, "truncated", False)),
            # 失败与截断的结果也带它（判"模型自己停了"还是"写到上限被切"）
            "finish_reason": str(getattr(result, "finish_reason", "") or ""),
            "error": str(getattr(result, "error", "") or "")[:300],
            "completion_tokens": _usage.get("output_tokens"),
            "prompt_tokens": _usage.get("input_tokens"),
            "elapsed_ms": int(elapsed_ms),
            "response_chars": len(_text),
        }
        _rec.update(_arch)
        _rec.update(_describe(req))
        _line = json.dumps(_rec, ensure_ascii=False)
        _append_row(_line, str(call_id or ""))
        return str(call_id or "")
    except Exception as exc:                                        # noqa: BLE001
        # 兜底（序列化 / 描述字段本身出错）：同样**告警**，不静默
        print("[WARN] 调用台账记录失败（**证据缺失**）：%s: %s（call_id=%s）"
              % (type(exc).__name__, exc, call_id))
        return str(call_id or "")


def read_calls(path=None) -> list:
    """读台账（诊断/测试用）：坏行跳过，不抛."""
    _p = Path(path) if path else _LEDGER_PATH
    if not _p or not Path(_p).exists():
        return []
    out = []
    try:
        for line in Path(_p).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out


def call_ids_for(schema: str = "", run_id: str = "", path=None) -> list:
    """本轮**确实发生**的调用 id（按 schema/run 过滤，保持台账顺序）.

    给"条目更新关联实际来源 call_id"用（复核之七）：bank 不去猜是哪次调用产出的它，
    而是从**台账**里取——那里记的是真的发生过的调用。同一次更新涉及多次调用（重试）时
    会拿到多个 id，全留着。
    """
    _rid = run_id
    if not _rid:
        try:
            from shared.run_identity import current_run_id
            _rid = current_run_id()
        except Exception:
            _rid = ""
    out = []
    for _c in read_calls(path):
        if _rid and str(_c.get("run_id") or "") != _rid:
            continue
        if schema and str(_c.get("schema") or "") != schema:
            continue
        _cid = str(_c.get("call_id") or "")
        if _cid and _cid not in out:
            out.append(_cid)
    return out


def call_id_of(exc) -> str:
    """异常对象上挂的 `call_id`（provider 抛异常时由 `_LedgeredService` 附上）；没有则空串.

    复核之十一 P2：调用**失败**的出口同样要能报出"这是哪一次调用"——台账里那一行
    是按同一个 id 落的。没有这个属性（异常不是从调用通道出来的）时如实返回空串，
    不编造身份。
    """
    return str(getattr(exc, "ai_video_call_id", "") or "")


def call_body_path(call_id: str, path=None):
    """正文文件的路径（`None` = 没设台账路径 / 没给 id）；**不判存在性**."""
    _dir = bodies_dir(path)
    if _dir is None or not call_id:
        return None
    return Path(_dir) / ("%s.txt" % (call_id,))


def read_call_body(call_id: str, path=None) -> str | None:
    """按 `call_id` 回读**完整正文**（重放/核验用）；找不到返回 `None`."""
    _p = call_body_path(call_id, path)
    if _p is None:
        return None
    try:
        if not _p.is_file():
            return None
        return _p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def verify_body(rec) -> bool:
    """台账行的摘要与**盘上那份文件**是否一致（"摘要可回读核验"）.

    复核 2026-10-02 之八 P2：旧写法把 `read_call_body(...) or ""` 当作正文——文件
    **不存在**与"正文本来就是空串"在它这里都变成 `b""`，于是「空响应」那一行在正文
    文件被删掉之后**仍然核验通过**（空串的摘要恰好对得上）。核验必须先确认文件
    **存在且可读**，再比原始字节；两者缺一即 `False`（证据缺失就不算核验通过）。
    """
    if not rec or not str(rec.get("response_sha1") or ""):
        return False
    _p = call_body_path(str(rec.get("call_id") or ""))
    if _p is None:
        return False
    try:
        if not _p.is_file():
            return False
        _raw = _p.read_bytes()
    except OSError:
        return False
    return (hashlib.sha1(_raw).hexdigest() == rec.get("response_sha1")
            and len(_raw) == int(rec.get("response_bytes") or 0))


def default_ledger_path():
    """默认台账路径（step0X 通用）：`<chapter_dir>/<stage>_calls.jsonl`。

    `step01` 走它；其它阶段不设就只是不写盘（避免替它们决定产物布局）。
    """
    _env = str(os.environ.get("TEXT_CALL_LEDGER") or "").strip()
    if _env:
        return Path(_env)
    return None
