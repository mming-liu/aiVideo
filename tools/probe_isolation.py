# -*- coding: utf-8 -*-
"""探针/诊断脚本的**隔离启动器**（2026-10-07 产线日志事故的直接产物）.

## 事故

一次 `.tmp/` 探针借测试助手跑桩运行：它只隔离了**章节目录**（`monkeypatch` 的
`OUTPUT_BASE`/tmp 路径），没有设 `STEP01_LOG_PATH`——于是 `step01_script_gen.main`
导入时那条模块级 `get_logger(step01_log_file())` 绑到了**产线**日志，
`create_service` 的 `[INFO] agent_plan 通道：prompt 剖面按模型解析…` 直接落进
`output/video/锦衣夜行/part_01_杀青州/chapter_001/step01.log`。靠 §77 参照快照的
`artifacts.sha256` 逐字节比对才发现——**事后截断 + 补记忆都不是隔离**。

## 这个模块做什么

把隔离收进**一个启动器**，探针只要 `with isolated_probe(...)`：

1. **导入产品包之前**设好隔离的 输出根 / step01 日志 / 调用台账（含 `set_log_context()`）；
2. **断言**解析出来的路径真的落在隔离根里（env 名写错时不许静默回到产线）；
3. **阻断外部传输**（探针只许离线；真模型调用走授权流程，不走探针）；
4. 退出时（**正常或抛异常两条路径**）核**正式产物摘要**未变，并**恢复进程上下文**
   （env / 日志上下文 / 台账）。

保护与恢复从**第一次改上下文之前**就开始：设置、断言、正文、退出四段在同一个
`try/finally` 里——启动准备阶段（建目录 / 设 env / 入口断言）就失败时，正文一行没跑，
上下文也必须原样还原（复核 2026-10-07 四轮 P2）。

保护集合覆盖**正式提交产物**（三份正式产物 + **提交清单** + 日志 + 镜头与音频目录），
路径一律从 `shared/paths.py` 取（复核 2026-10-07 四轮 P1）。摘要采集区分
**文件不存在**与**无法读取**：读不动时既不启动探针、也不宣布"未变化"。

用法：

    from tools.probe_isolation import isolated_probe

    with isolated_probe(label="card-probe") as probe:
        import step01_script_gen.main as m          # ← 在 with 里才导入产品包
        m._logger.verbose("probe line")
        probe.assert_isolated()                     # 设定 scope 之后再核一次
    # 退出时自动核正式产物摘要；被改动 / 读不动 ⇒ `ProbeIsolationError`
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import socket
import sys
import tempfile
from pathlib import Path

#: 隔离要覆盖的进程级配置：产物根 / step01 日志 / 台账 / 章节与部的范围 / 呈现模式
ENV_KEYS = ("OUTPUT_BASE", "STEP01_LOG_PATH", "TEXT_CALL_LEDGER", "NOVEL_NAME",
            "NOVEL_CHAPTER", "BOOK_INDEX", "BOOK_NAME", "STEP01_PRESENTATION_MODE")

#: 回环地址：探针允许连本机（本地服务/桩），其余一律阻断
_LOOPBACK = ("127.0.0.1", "::1", "localhost", "0.0.0.0")


class ProbeIsolationError(RuntimeError):
    """隔离被破坏：路径跑到产线里、探针期间正式产物被改动/读不动、或试图做外部传输."""


def _within(path, root) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def protected_paths() -> list:
    """要保护的**正式产物单文件**：三份正式产物 + **提交清单** + 日志.

    路径一律从 `shared/paths.py` 取（复核 2026-10-07 四轮 P1：提交清单此前不在保护
    集合里，把它从 COMMITTED 改成 BLOCK 也照样报 `production_unchanged=True`）。
    """
    from shared.paths import (script_file, step01_audit_file, step01_commit_file,
                              step01_log_file, step01_quality_file,
                              step02b_quality_file)
    return [script_file(), step01_audit_file(), step01_quality_file(),
            step01_commit_file(), step02b_quality_file(), step01_log_file()]


def protected_dirs() -> list:
    """要保护的**章节级产物目录**（逐个文件比摘要）."""
    from shared.paths import audio_dir, clips_dir
    return [clips_dir(), audio_dir()]


def _read_digest(path, *, kind: str = "file") -> tuple:
    """`(sha256 或 None, 读不动的**结构化记录** 或 None)`——**文件不存在** ⇒ `(None, None)`.

    读不动返回 `{"path", "kind", "error"}`（复核 2026-10-07 五轮）：调用方要能分开
    "这个**文件**读不动"与"这个**目录**读不动（成员无法枚举）"——两者都让摘要**核不出来**，
    但都**不等于**内容变了。
    """
    def _bad(_exc):
        return {"path": str(path), "kind": kind,
                "error": "%s: %s" % (type(_exc).__name__, _exc)}
    try:
        if not path.is_file():
            return None, None
    except OSError as _exc:
        return None, _bad(_exc)
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest(), None
    except OSError as _exc:
        return None, _bad(_exc)


def _render_unreadable(entries) -> str:
    """结构化读取错误 → 一行说明（`路径｜文件/目录｜原因`）."""
    return "；".join("%s｜%s｜%s" % (x.get("path"), x.get("kind"), x.get("error"))
                     for x in entries)


def _under_any(path, dirs) -> bool:
    """`path` 是否落在某个**不可读目录**之下（那些成员同样核不出来）."""
    if not dirs:
        return False
    _p = Path(path)
    for _parent in (_p, *_p.parents):
        if str(_parent) in dirs:
            return True
    return False


def production_digest() -> tuple:
    """正式产物的只读摘要 → `(路径 → sha256, **读不动**的结构化记录列表)`.

    ⚠️ 两条**分开**的口径（复核 2026-10-07 四轮 / 五轮）：
    1. **`文件不存在` 与 `无法读取` 不是一回事**——旧写法 `except OSError: continue`
       把"读不动"折成"没有这个文件"，被保护的文件被改成读不动时前后两份摘要都缺这个键，
       差集为空 ⇒ 误报"未变化"。现在读失败单独返回，调用方据此**拒绝启动**或报告
       **核验未完成**。
    2. **读不动也不等于"已改动"**——它只让那一条（或那个目录下的成员）**核不出来**，
       调用方把它列进**未知**，不进"确认改动"（复核第五轮实测：只注入读取失败、字节
       未变，却被同时写成 `changed_paths=[…]`，与分账口径不符）。

    与 `tools/verify_enhancement_transaction.snapshot_production` 同源思路（计划 /
    质量门 / step01 日志 / 镜头与音频目录），另加三份正式产物与**提交清单**；这里
    **认当前进程环境**——两次都在同一个 env 下算，比对才有意义（退出时会先恢复 env
    再算第二次）。
    """
    _digest: dict = {}
    _bad: list = []
    for _p in protected_paths():
        _h, _err = _read_digest(_p, kind="file")
        if _err:
            _bad.append(_err)
        elif _h is not None:
            _digest[str(_p)] = _h
    for _d in protected_dirs():
        try:
            if not _d.is_dir():
                continue
            _files = sorted(_f for _f in _d.iterdir() if _f.is_file())
        except OSError as _exc:
            _bad.append({"path": str(_d), "kind": "dir",
                         "error": "%s: %s" % (type(_exc).__name__, _exc)})
            continue
        for _f in _files:
            _h, _err = _read_digest(_f, kind="file")
            if _err:
                _bad.append(_err)
            elif _h is not None:
                _digest[str(_f)] = _h
    return _digest, _bad


def _note(exc: BaseException, msg: str) -> None:
    """把第二条证据挂到**已经在向上抛**的异常上（不拿它顶掉原异常）."""
    _add = getattr(exc, "add_note", None)
    if _add is not None:
        _add("[probe] %s" % msg)
    else:                                              # Python < 3.11 兜底
        print("[PROBE][VIOLATION] %s（原异常 %s 仍向上抛）"
              % (msg, type(exc).__name__))


@contextlib.contextmanager
def block_external_transport():
    """挡住**一切进程外传输**：非回环地址的 `connect` 直接抛.

    同时检查 HTTP 原始目标与 socket 地址，避免本地代理转发绕过回环许可。
    真模型调用要走授权流程，不走离线探针。
    """
    from urllib.parse import urlsplit
    from unittest.mock import patch
    import httpx
    import requests
    import urllib.request

    def local(host):
        return str(host) in _LOOPBACK or str(host).startswith("127.")

    def target(url):
        host = urlsplit(str(url)).hostname
        if not local(host):
            raise ProbeIsolationError("探针禁止外部 HTTP 目标（含本地代理转发）：%r" % host)

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    def guard_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, (tuple, list)) and address else address
        if local(host):
            return real_connect(self, address, *args, **kwargs)
        raise ProbeIsolationError("探针禁止外部传输：connect(%r)" % (address,))
    def guard_connect_ex(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, (tuple, list)) and address else address
        if local(host):
            return real_connect_ex(self, address, *args, **kwargs)
        raise ProbeIsolationError("探针禁止外部传输：connect_ex(%r)" % (address,))

    real_httpx = httpx.HTTPTransport.handle_request
    real_async = httpx.AsyncHTTPTransport.handle_async_request
    real_requests = requests.adapters.HTTPAdapter.send
    real_urllib = urllib.request.AbstractHTTPHandler.do_open
    def sync_send(self, request):
        target(request.url)
        return real_httpx(self, request)
    async def async_send(self, request):
        target(request.url)
        return await real_async(self, request)
    def requests_send(self, request, *args, **kwargs):
        target(request.url)
        return real_requests(self, request, *args, **kwargs)
    def urllib_open(self, connection, request, *args, **kwargs):
        target(request.full_url)
        return real_urllib(self, connection, request, *args, **kwargs)

    # 检查网络传输的原始目标，不能因代理连接到回环地址就把远端请求放行。
    # MockTransport 不执行真实网络传输，保持离线协议测试可用。
    with contextlib.ExitStack() as stack:
        for cls, attr, replacement in (
            (socket.socket, "connect", guard_connect),
            (socket.socket, "connect_ex", guard_connect_ex),
            (httpx.HTTPTransport, "handle_request", sync_send),
            (httpx.AsyncHTTPTransport, "handle_async_request", async_send),
            (requests.adapters.HTTPAdapter, "send", requests_send),
            (urllib.request.AbstractHTTPHandler, "do_open", urllib_open),
        ):
            stack.enter_context(patch.object(cls, attr, replacement))
        yield


class ProbeHandle:
    """隔离句柄：`root` / `changed_paths` / `production_unchanged` / `assert_isolated()`.

    - `production_unchanged`：**只有核验真的做完且没变化**时才是 `True`；
    - `changed_paths`：**确认改动**（前后都读得出来且摘要不同）；
    - `unknown_paths`：**核不出来**的产物（文件读不动，或落在不可读目录下）——
      它们**不进** `changed_paths`（读不动 ≠ 已改动），但会让核验不完整；
    - `verification_incomplete` / `unreadable_paths` / `unreadable`：读不动的正式产物
      （路径串 / 结构化记录 `{path, kind, error}`；读不动 ⇒ 不许宣布"未变化"）；
    - `errors`：保护核验自己的错误证据（正文也抛异常时，它与原异常**两份都留**）。
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.changed_paths: list = []
        self.production_unchanged: bool = True
        self.verification_incomplete: bool = False
        self.unknown_paths: list = []
        self.unreadable_paths: list = []
        self.unreadable: list = []
        self.errors: list = []
        #: 本次保护集合（进入探针时已确认可读的那些正式产物路径）
        self.protected: list = []

    def isolated_paths(self) -> dict:
        """当前**解析出来**的运行路径（导入产品包之后调用）."""
        from shared import call_ledger as _cl
        from shared.paths import chapter_dir, step01_log_file
        _out = {"章节目录": Path(chapter_dir()), "step01 日志": Path(step01_log_file())}
        _led = _cl.ledger_path()
        if _led is not None:
            _out["调用台账"] = Path(_led)
        return _out

    def assert_isolated(self) -> dict:
        """断言解析出来的输出/日志/台账路径**都落在隔离根里**.

        env 名写错、或某处自行拼了产线路径时，这里立刻失败——而不是"跑完才发现
        产线日志多了几行"。
        """
        _got = self.isolated_paths()
        _bad = {k: str(v) for k, v in _got.items() if not _within(v, self.root)}
        if _bad:
            raise ProbeIsolationError(
                "这些路径没落在隔离根里（env 写错就会静默回到产线）：%s（根 %s）"
                % (_bad, self.root))
        return _got


@contextlib.contextmanager
def isolated_probe(*, label: str = "probe", root=None, block_transport: bool = True):
    """探针启动器：隔离设置 → 断言 → 阻断外部传输 → 退出时核摘要并恢复上下文.

    `with` 体内才导入产品包（模块级 logger / 台账都在导入时绑定路径）。
    """
    import shared.log_utils as _log
    from shared import call_ledger as _cl

    _root = Path(root) if root else Path(tempfile.mkdtemp(prefix="probe_%s_" % label))
    # ⚠️ **先让路径模块加载 `.env.*`，再快照环境**（2026-10-08 自查发现）：`shared.paths`
    # 在导入时 `load_dotenv`（`.env.content` / `.env.keys`）把 `NOVEL_NAME` / `BOOK_*` /
    # `NOVEL_CHAPTER` 写进 `os.environ`——若快照发生在它之前，这些键快照到的是 `None`，
    # 退出还原就会把它们**弹掉**：`chapter_dir()` 随后少一层小说/部目录，第二次摘要
    # 解析出的是**另一批路径** ⇒ 保护核验把"没法比"报成"14 处改动"（实测复现：
    # 只要在被隔离的进程里导入任何会拉起 `shared.paths` 的模块就会发生）。
    import shared.paths  # noqa: F401  —— 只为让 `.env.*` 先落地（导入本身无副作用）
    _saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
    _saved_ctx = _log.current_log_dir()
    _saved_ledger = _cl.ledger_path()
    # ① **前置采集**（改任何上下文之前）：读不动 ⇒ **不进探针**。先有一份可比的基线，
    #    才谈得上"探针期间没动过正式产物"；采集不到就不许开始。
    _before, _before_bad = production_digest()
    if _before_bad:
        raise ProbeIsolationError(
            "探针**未启动**：正式产物摘要无法采集（读了但读不动）：%s——"
            "保护核验的前提是先有一份可比的基线"
            % _render_unreadable(_before_bad[:3]))
    _h = ProbeHandle(_root)
    _h.protected = sorted(_before)
    try:
        # ② **导入产品包之前**设好隔离路径（三件套 + 日志上下文）
        for _sub_dir in ("output", "logs", "ledger"):
            (_root / _sub_dir).mkdir(parents=True, exist_ok=True)
        os.environ["OUTPUT_BASE"] = str(_root / "output")
        os.environ["STEP01_LOG_PATH"] = str(_root / "logs" / "step01.log")
        os.environ["TEXT_CALL_LEDGER"] = str(_root / "ledger" / "step01_calls.jsonl")
        _log.set_log_context(_root / "logs")
        _cl.set_ledger_path(_root / "ledger" / "step01_calls.jsonl")
        # ③ 导入前先核一次（此时只解析得到"由 env 决定的"路径；导入后再核台账）
        _h.assert_isolated()
        with (block_external_transport() if block_transport
              else contextlib.nullcontext()):
            yield _h
    finally:
        # ④ **先恢复进程上下文**（这样第二次摘要与第一次在同一 env 下算），再核摘要。
        #    设置 / 断言 / 正文 / 退出四段都在这个 finally 的覆盖里：启动准备阶段失败时
        #    正文一行没跑，上下文也必须还原（复核 2026-10-07 四轮 P2）。
        _pending = sys.exc_info()[1]
        for _k, _v in _saved_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
        _log.set_log_context(_saved_ctx)
        _cl.set_ledger_path(_saved_ledger)
        # **还原必须逐键等于快照**（同一条自查）：不等 ⇒ 第二次摘要与第一次**不是同一批
        # 路径**，"核验"这件事本身失效——这时只能报"核验未完成"，不许拿它当"没变化"或
        # "被改动"（保护核验的两个答案都必须建立在**可比**之上）。
        _env_now = {k: os.environ.get(k) for k in ENV_KEYS}
        _env_drift = sorted(k for k in ENV_KEYS if _env_now.get(k) != _saved_env.get(k))
        _after, _after_bad = production_digest()
        _bad_all = list(_before_bad) + list(_after_bad)
        _bad_dirs = {x["path"] for x in _bad_all if x.get("kind") == "dir"}
        _bad_paths = {x["path"] for x in _bad_all}
        # **读不动 ⇒ 未知，不记"已改动"**（复核 2026-10-07 五轮）：只注入读取失败、字节
        # 没变时，旧写法把"核不出来"同时写成"确认改动"。目录不可读时，它**下面的成员**
        # 同样核不出来 ⇒ 一并列未知。
        _unknown = sorted(_k for _k in set(_before) | set(_after)
                          if _k in _bad_paths or _under_any(_k, _bad_dirs))
        # ⚠️ 比的是**路径 → 摘要**，不是路径集合：只比 key 的话，"同一份文件被改写"
        # 会被判成"没变"（本模块的第一版就踩了这条，被用例逮住）。
        _h.changed_paths = sorted(
            _k for _k in set(_before) | set(_after)
            if _before.get(_k) != _after.get(_k) and _k not in _unknown)
        _h.unknown_paths = _unknown
        _h.unreadable = _bad_all
        _h.unreadable_paths = sorted(_bad_paths)
        if _env_drift:
            _bad_all = list(_bad_all) + [
                {"path": "（进程环境）", "kind": "env",
                 "error": "还原后这些键与快照不符：%s——两次摘要不是同一批路径，"
                          "保护核验**未完成**" % _env_drift}]
            _h.unreadable = _bad_all
        _h.verification_incomplete = bool(_bad_all)
        # **读不动 ≠ 没变化**：核验没做完就不许宣布"未变化"（复核 2026-10-07 四轮 P2）。
        _h.production_unchanged = not _h.changed_paths and not _h.verification_incomplete
        _why = []
        if _h.changed_paths:
            _why.append("正式产物被改动（%d 处）：%s"
                        % (len(_h.changed_paths), _h.changed_paths[:5]))
        if _bad_all:
            _why.append("保护核验**未完成**：%d 条读不动 / 无法枚举（%s）——这些列为"
                        "**未知**，不计入 changed_paths（读不动不等于已改动），"
                        "但也不许据此宣布未变化"
                        % (len(_bad_all), _render_unreadable(_bad_all[:3])))
        if _why:
            _msg = "；".join(_why)
            print("[PROBE][VIOLATION] %s" % _msg)
            _h.errors.append(_msg)
            if _pending is None:
                raise ProbeIsolationError(_msg)
            _note(_pending, _msg)


__all__ = ["ENV_KEYS", "ProbeHandle", "ProbeIsolationError", "block_external_transport",
           "isolated_probe", "production_digest", "protected_dirs", "protected_paths"]
